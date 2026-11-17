import psycopg
import pytest
from fastapi.testclient import TestClient

from bikeshare.api.model_store import (
    IncompatibleModelError,
    NoChampionError,
    UnapprovedModelError,
)
from tests.unit.api.conftest import FakeModelStore, FakeRepository


def test_live(client: TestClient) -> None:
    assert client.get("/health/live").json()["status"] == "ok"


def test_ready(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["model_loaded"] is True
    assert response.json()["database"] is True


def test_not_ready_without_model(client: TestClient, models: FakeModelStore) -> None:
    models.current = None
    models.last_error = "alias champion introuvable"

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert response.json()["detail"] == "alias champion introuvable"


def test_not_ready_without_database(client: TestClient, repository: FakeRepository) -> None:
    repository.available = False

    assert client.get("/health/ready").status_code == 503


def test_registry_down_does_not_stop_serving(client: TestClient, models: FakeModelStore) -> None:
    models.last_error = "MLflow injoignable"

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["registry_reachable"] is False


def test_health_checks_are_not_traced(client: TestClient, repository: FakeRepository) -> None:
    client.get("/health/live")
    client.get("/health/ready")
    client.get("/model")

    assert repository.requests == []


def test_served_model(client: TestClient) -> None:
    body = client.get("/model").json()

    assert body["version"] == 3
    assert body["alias"] == "champion"
    assert body["feature_version"] == "1"


def test_refresh_needs_the_admin_token(client: TestClient, models: FakeModelStore) -> None:
    assert client.post("/admin/model/refresh").status_code == 401
    wrong = client.post("/admin/model/refresh", headers={"X-Admin-Token": "faux"})
    assert wrong.status_code == 401
    assert models.refreshes == 1


def test_non_ascii_token_is_refused_not_a_crash(client: TestClient) -> None:
    response = client.post("/admin/model/refresh", headers={"X-Admin-Token": "é".encode()})

    assert response.status_code == 401


def test_refresh(client: TestClient, models: FakeModelStore) -> None:
    response = client.post("/admin/model/refresh", headers={"X-Admin-Token": "jeton-de-test"})

    assert response.status_code == 200
    assert response.json()["version"] == 3
    assert models.refreshes == 2


def test_refresh_when_registry_is_down(client: TestClient, models: FakeModelStore) -> None:
    models.last_error = "MLflow injoignable"

    response = client.post("/admin/model/refresh", headers={"X-Admin-Token": "jeton-de-test"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "registry_unavailable"


@pytest.mark.parametrize("refusal", [UnapprovedModelError, IncompatibleModelError])
def test_refused_champion_is_not_reported_as_a_registry_outage(
    client: TestClient, models: FakeModelStore, refusal: type[Exception]
) -> None:
    models.last_error = "version 4 non servable (validation_status='rolled_back')"
    models.refusal = refusal

    ready = client.get("/health/ready")
    refresh = client.post("/admin/model/refresh", headers={"X-Admin-Token": "jeton-de-test"})

    assert ready.status_code == 200
    assert ready.json()["registry_reachable"] is True
    assert "non servable" in ready.json()["detail"]
    assert refresh.status_code == 409
    assert refresh.json()["error"]["code"] == "model_refused"
    assert "non servable" in refresh.json()["error"]["message"]


def test_refresh_without_any_champion_is_not_a_registry_outage(
    client: TestClient, models: FakeModelStore
) -> None:
    models.current = None
    models.last_error = "aucune version de bikeshare-demand ne porte l'alias champion"
    models.refusal = NoChampionError

    ready = client.get("/health/ready")
    refresh = client.post("/admin/model/refresh", headers={"X-Admin-Token": "jeton-de-test"})

    assert ready.status_code == 503
    assert ready.json()["registry_reachable"] is True
    assert refresh.status_code == 503
    assert refresh.json()["error"]["code"] == "model_unavailable"


def test_refresh_while_database_is_down_is_a_storage_error(
    client: TestClient, models: FakeModelStore
) -> None:
    models.last_error = "connexion refusée"
    models.refusal = psycopg.OperationalError

    refresh = client.post("/admin/model/refresh", headers={"X-Admin-Token": "jeton-de-test"})

    assert refresh.status_code == 503
    assert refresh.json()["error"]["code"] == "storage_unavailable"


def test_unknown_route_has_the_common_error_shape(client: TestClient) -> None:
    response = client.get("/inconnue")

    assert response.status_code == 404
    assert response.json()["error"] == {"code": "not_found", "message": "Route inconnue."}
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_wrong_method_is_a_traced_error(client: TestClient, repository: FakeRepository) -> None:
    response = client.get("/predict")

    assert response.status_code == 405
    assert response.headers["allow"] == "POST"
    assert response.json()["error"]["code"] == "method_not_allowed"
    assert [(t.endpoint, t.http_status, t.error_code) for t in repository.requests] == [
        ("/predict", 405, "method_not_allowed")
    ]


def test_openapi_documents_the_contract(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    assert {"/predict", "/predict/batch", "/feedback", "/health/ready"} <= set(schema["paths"])
    assert "example" in schema["components"]["schemas"]["Observation"]
    assert client.get("/docs").status_code == 200
