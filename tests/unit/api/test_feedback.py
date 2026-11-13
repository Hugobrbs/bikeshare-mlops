from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests.unit.api.conftest import FakeRepository


@pytest.fixture
def prediction_id(client: TestClient, observation: dict) -> str:
    response = client.post("/predict", json={"observation": observation})
    return response.json()["prediction"]["prediction_id"]


def observed(prediction_id: str, **overrides: object) -> dict:
    payload = {
        "feedback_id": str(uuid4()),
        "prediction_id": prediction_id,
        "observed_rentals": 412,
        "observed_at": "2012-06-15T09:05:00Z",
        "label_source": "comptage-stations",
    }
    return {**payload, **overrides}


def test_feedback_with_observed_value(
    client: TestClient, repository: FakeRepository, prediction_id: str
) -> None:
    payload = observed(prediction_id)

    response = client.post("/feedback", json=payload)

    assert response.status_code == 201
    assert response.json()["status"] == "created"
    assert response.json()["feedback_id"] == payload["feedback_id"]
    [stored] = repository.feedbacks.values()
    assert stored.observed_rentals == 412
    assert repository.requests[-1].endpoint == "/feedback"


def test_subjective_feedback(client: TestClient, prediction_id: str) -> None:
    payload = {"feedback_id": str(uuid4()), "prediction_id": prediction_id, "useful": False}

    assert client.post("/feedback", json=payload).status_code == 201


def test_replay_is_idempotent(
    client: TestClient, repository: FakeRepository, prediction_id: str
) -> None:
    payload = observed(prediction_id)
    client.post("/feedback", json=payload)

    replay = client.post("/feedback", json=payload)

    assert replay.status_code == 200
    assert replay.json()["status"] == "already_recorded"
    assert len(repository.feedbacks) == 1
    assert [r.http_status for r in repository.requests if r.endpoint == "/feedback"] == [201, 200]


def test_same_id_with_other_content_is_a_conflict(
    client: TestClient, repository: FakeRepository, prediction_id: str
) -> None:
    payload = observed(prediction_id)
    client.post("/feedback", json=payload)

    response = client.post("/feedback", json={**payload, "observed_rentals": 5})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "feedback_conflict"
    assert next(iter(repository.feedbacks.values())).observed_rentals == 412
    assert repository.requests[-1].error_code == "feedback_conflict"


def test_second_feedback_on_same_prediction_is_a_conflict(
    client: TestClient, prediction_id: str
) -> None:
    client.post("/feedback", json=observed(prediction_id))

    assert client.post("/feedback", json=observed(prediction_id)).status_code == 409


def test_unknown_prediction(client: TestClient, repository: FakeRepository) -> None:
    response = client.post("/feedback", json=observed(str(uuid4())))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "prediction_not_found"
    assert repository.requests[-1].http_status == 404


@pytest.mark.parametrize(
    "overrides",
    [
        {"observed_rentals": None, "observed_at": None, "label_source": None},
        {"label_source": None},
        {"observed_at": None},
        {"observed_rentals": -3},
        {"observed_rentals": 3_000_000_000},
        {"label_source": "a\x00b"},
        {"observed_at": "2012-06-15T09:05:00"},
        {"feedback_id": "pas-un-uuid"},
    ],
    ids=[
        "vide",
        "sans-source",
        "sans-date",
        "negatif",
        "trop-grand-pour-la-base",
        "caractere-nul",
        "date-sans-fuseau",
        "id-invalide",
    ],
)
def test_invalid_feedback(client: TestClient, prediction_id: str, overrides: dict) -> None:
    response = client.post("/feedback", json=observed(prediction_id, **overrides))

    assert response.status_code == 422


def test_database_down(client: TestClient, repository: FakeRepository, prediction_id: str) -> None:
    repository.available = False

    response = client.post("/feedback", json=observed(prediction_id))

    assert response.status_code == 503
    assert repository.feedbacks == {}
