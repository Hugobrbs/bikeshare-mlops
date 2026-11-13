from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient

from tests.unit.api.conftest import FakeModelStore, FakeRepository


def test_predict(client: TestClient, repository: FakeRepository, observation: dict) -> None:
    response = client.post("/predict", json={"observation": observation})

    body = response.json()
    assert response.status_code == 200
    assert body["prediction"]["predicted_rentals"] == 80.0
    assert body["model"] == {
        "name": "bikeshare-demand",
        "version": 3,
        "run_id": "run-abc",
        "alias": "champion",
    }
    assert response.headers["x-request-id"] == body["request_id"]

    [trace] = repository.requests
    assert str(trace.request_id) == body["request_id"]
    assert (trace.endpoint, trace.http_status, trace.item_count) == ("/predict", 200, 1)
    assert trace.inference_duration_ms is not None
    assert trace.processing_duration_ms >= trace.inference_duration_ms
    assert trace.api_revision == "abc1234"
    assert trace.campaign_id == "manual"
    stored = repository.predictions[UUID(body["prediction"]["prediction_id"])]
    assert stored.predicted_rentals == 80.0
    assert stored.input_features["timestamp"] == "2012-06-15T08:00:00"


def test_batch_keeps_order_and_matches_single_predictions(
    client: TestClient, repository: FakeRepository, observation: dict
) -> None:
    hours = (6, 17, 23)
    observations = [{**observation, "timestamp": f"2012-06-15T{h:02d}:00:00"} for h in hours]

    batch = client.post("/predict/batch", json={"observations": observations}).json()
    singles = [
        client.post("/predict", json={"observation": o}).json()["prediction"]["predicted_rentals"]
        for o in observations
    ]

    predictions = batch["predictions"]
    assert [p["item_index"] for p in predictions] == [0, 1, 2]
    assert [p["predicted_rentals"] for p in predictions] == singles == [60.0, 170.0, 230.0]
    assert len({p["prediction_id"] for p in predictions}) == 3
    assert repository.requests[0].item_count == 3


def test_campaign_header_is_traced(
    client: TestClient, repository: FakeRepository, observation: dict
) -> None:
    client.post(
        "/predict", json={"observation": observation}, headers={"X-Test-Campaign": "w2-u20"}
    )

    assert repository.requests[0].campaign_id == "w2-u20"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("weather_code", 5),
        ("humidity_pct", 130),
        ("temperature_c", "chaud"),
        ("timestamp", "2012-06-15T08:00:00+02:00"),
        ("timestamp", None),
        ("rentals", 12),
    ],
    ids=["code-meteo", "humidite", "type", "fuseau", "manquant", "champ-inconnu"],
)
def test_invalid_observation(
    client: TestClient, repository: FakeRepository, observation: dict, field: str, value: object
) -> None:
    response = client.post("/predict", json={"observation": {**observation, field: value}})

    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "invalid_input"
    assert field in body["error"]["message"]
    [trace] = repository.requests
    assert (trace.http_status, trace.error_code) == (422, "invalid_input")
    assert str(trace.request_id) == body["request_id"]
    assert repository.predictions == {}


@pytest.mark.parametrize("size", [0, 101])
def test_batch_size_limits(client: TestClient, observation: dict, size: int) -> None:
    response = client.post("/predict/batch", json={"observations": [observation] * size})

    assert response.status_code == 422


def test_one_invalid_item_rejects_the_whole_batch(
    client: TestClient, repository: FakeRepository, observation: dict
) -> None:
    observations = [observation, {**observation, "weather_code": 9}, observation]

    response = client.post("/predict/batch", json={"observations": observations})

    assert response.status_code == 422
    assert "observations.1.weather_code" in response.json()["error"]["message"]
    assert repository.predictions == {}


def test_no_model_loaded(
    client: TestClient, repository: FakeRepository, models: FakeModelStore, observation: dict
) -> None:
    models.current = None

    response = client.post("/predict", json={"observation": observation})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "model_unavailable"
    assert repository.requests[0].error_code == "model_unavailable"


def test_database_down_means_no_fake_success(
    client: TestClient,
    repository: FakeRepository,
    observation: dict,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository.available = False

    response = client.post("/predict", json={"observation": observation})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "storage_unavailable"
    assert repository.predictions == {}
    assert response.json()["request_id"] in caplog.text


def test_invalid_input_while_database_is_down(
    client: TestClient,
    repository: FakeRepository,
    observation: dict,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository.available = False

    response = client.post("/predict", json={"observation": {**observation, "weather_code": 9}})

    assert response.status_code == 422
    assert response.json()["request_id"] in caplog.text


def test_sql_error_that_is_not_an_outage_is_a_traced_500(
    client: TestClient, repository: FakeRepository, observation: dict
) -> None:
    repository.rejects_writes_with = psycopg.errors.NumericValueOutOfRange("valeur refusée")

    response = client.post("/predict", json={"observation": observation})

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert repository.requests[-1].error_code == "internal_error"


def test_unexpected_error_does_not_leak_details(
    client: TestClient, repository: FakeRepository, models: FakeModelStore, observation: dict
) -> None:
    class Broken:
        def predict(self, frame: object) -> None:
            raise ZeroDivisionError("détail interne")

    models.current = models.current.__class__(**{**models.current.__dict__, "pipeline": Broken()})

    response = client.post("/predict", json={"observation": observation})

    assert response.status_code == 500
    assert response.json()["error"] == {"code": "internal_error", "message": "Erreur interne."}
    assert repository.requests[0].error_code == "internal_error"


def test_new_champion_is_served_without_restart(
    client: TestClient, models: FakeModelStore, observation: dict
) -> None:
    before = client.post("/predict", json={"observation": observation}).json()
    models.switch_to(4)
    after = client.post("/predict", json={"observation": observation}).json()

    assert (before["model"]["version"], after["model"]["version"]) == (3, 4)
