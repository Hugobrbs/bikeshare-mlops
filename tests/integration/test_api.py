from collections.abc import Iterator
from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from bikeshare.api.app import create_app
from bikeshare.api.repository import TraceRepository
from bikeshare.settings import Settings
from tests.unit.api.conftest import FakeModelStore

pytestmark = pytest.mark.integration

OBSERVATION = {
    "timestamp": "2012-06-15T08:00:00",
    "weather_code": 1,
    "temperature_c": 22.5,
    "feels_like_c": 25.0,
    "humidity_pct": 60,
    "windspeed_kmh": 12.0,
}


@pytest.fixture
def client(repository: TraceRepository) -> Iterator[TestClient]:
    """La vraie API et la vraie base ; seul le modèle est simulé."""
    models = FakeModelStore()
    model_id = repository.register_model("http://mlflow", "bikeshare-demand", 3, "run-abc")
    models.current = replace(models.current, model_id=model_id)
    app = create_app(Settings(app_revision="test"), repository, models)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def scalar(admin: psycopg.Connection, query: str, *params: object) -> object:
    return admin.execute(query, params).fetchone()[0]


def test_batch_writes_one_request_and_n_predictions(
    client: TestClient, admin: psycopg.Connection
) -> None:
    response = client.post("/predict/batch", json={"observations": [OBSERVATION] * 4})

    request_id = response.json()["request_id"]
    assert response.status_code == 200
    assert scalar(admin, "SELECT count(*) FROM tracing.api_requests") == 1
    assert scalar(admin, "SELECT item_count FROM tracing.api_requests") == 4
    assert (
        scalar(admin, "SELECT count(*) FROM tracing.predictions WHERE request_id = %s", request_id)
        == 4
    )


def test_response_can_be_rebuilt_from_the_database(
    client: TestClient, admin: psycopg.Connection
) -> None:
    body = client.post("/predict", json={"observation": OBSERVATION}).json()

    row = admin.execute(
        """
        SELECT p.predicted_rentals, m.registered_name, m.version, m.mlflow_run_id,
               p.input_features->>'timestamp', r.endpoint, r.api_revision
        FROM tracing.predictions p
        JOIN tracing.model_versions m USING (model_id)
        JOIN tracing.api_requests r USING (request_id)
        WHERE p.prediction_id = %s
        """,
        (body["prediction"]["prediction_id"],),
    ).fetchone()

    assert row == (
        80.0,
        "bikeshare-demand",
        3,
        "run-abc",
        "2012-06-15T08:00:00",
        "/predict",
        "test",
    )


def test_invalid_request_is_traced_without_prediction(
    client: TestClient, admin: psycopg.Connection
) -> None:
    response = client.post("/predict", json={"observation": {**OBSERVATION, "weather_code": 9}})

    assert response.status_code == 422
    row = admin.execute(
        "SELECT request_id::text, http_status, error_code FROM tracing.api_requests"
    ).fetchone()
    assert row == (response.json()["request_id"], 422, "invalid_input")
    assert scalar(admin, "SELECT count(*) FROM tracing.predictions") == 0


def test_feedback_lifecycle(client: TestClient, admin: psycopg.Connection) -> None:
    prediction_id = client.post("/predict", json={"observation": OBSERVATION}).json()["prediction"][
        "prediction_id"
    ]
    feedback = {
        "feedback_id": str(uuid4()),
        "prediction_id": prediction_id,
        "observed_rentals": 95,
        "observed_at": "2012-06-15T09:05:00Z",
        "label_source": "comptage-stations",
    }

    statuses = [
        client.post("/feedback", json=feedback).status_code,
        client.post("/feedback", json=feedback).status_code,
        client.post("/feedback", json={**feedback, "observed_rentals": 1}).status_code,
        client.post("/feedback", json={**feedback, "prediction_id": str(uuid4())}).status_code,
    ]

    assert statuses == [201, 200, 409, 404]
    assert scalar(admin, "SELECT count(*) FROM tracing.feedbacks") == 1
    traced = admin.execute(
        "SELECT http_status FROM tracing.api_requests"
        " WHERE endpoint = '/feedback' ORDER BY started_at"
    ).fetchall()
    assert [status for (status,) in traced] == [201, 200, 409, 404]
    # écart entre prévu (80) et observé (95), comme le calcule le dashboard
    mae = scalar(
        admin,
        "SELECT avg(abs(f.observed_rentals - p.predicted_rentals)) FROM tracing.feedbacks f"
        " JOIN tracing.predictions p USING (prediction_id)",
    )
    assert mae == 15.0
