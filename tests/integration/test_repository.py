import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
import pytest

from bikeshare.api.errors import ApiError
from bikeshare.api.repository import FeedbackRow, PredictionRow, RequestTrace, TraceRepository

pytestmark = pytest.mark.integration

INSTANCE = uuid4()


def make_trace(endpoint: str = "/predict", status: int = 200, **overrides: object) -> RequestTrace:
    defaults = {
        "request_id": uuid4(),
        "started_at": datetime.now(UTC),
        "endpoint": endpoint,
        "http_status": status,
        "processing_duration_ms": 12.5,
        "worker_pid": os.getpid(),
        "instance_id": INSTANCE,
        "api_revision": "test",
        "item_count": 1,
    }
    return RequestTrace(**{**defaults, **overrides})


def make_rows(count: int = 1) -> list[PredictionRow]:
    return [PredictionRow(uuid4(), i, {"weather_code": 1}, 100.0 + i) for i in range(count)]


def count(admin: psycopg.Connection, table: str) -> int:
    return admin.execute(f"SELECT count(*) FROM tracing.{table}").fetchone()[0]


@pytest.fixture
def model_id(repository: TraceRepository) -> UUID:
    return repository.register_model("http://mlflow", "bikeshare-demand", 1, "run-1")


@pytest.fixture
def prediction(repository: TraceRepository, model_id: UUID) -> PredictionRow:
    [row] = make_rows()
    repository.save_predictions(make_trace(), model_id, [row])
    return row


def observed(prediction: PredictionRow, **overrides: object) -> FeedbackRow:
    defaults = {
        "feedback_id": uuid4(),
        "prediction_id": prediction.prediction_id,
        "useful": None,
        "observed_rentals": 120,
        "observed_at": datetime(2012, 6, 15, 9, tzinfo=UTC),
        "label_source": "comptage",
    }
    return FeedbackRow(**{**defaults, **overrides})


def test_ping(repository: TraceRepository) -> None:
    assert repository.ping()


def test_same_model_version_is_registered_once(
    repository: TraceRepository, admin: psycopg.Connection
) -> None:
    first = repository.register_model("http://mlflow", "bikeshare-demand", 1, "run-1")
    second = repository.register_model("http://mlflow", "bikeshare-demand", 1, "run-1")
    other = repository.register_model("http://mlflow", "bikeshare-demand", 2, "run-2")

    assert first == second != other
    assert count(admin, "model_versions") == 2


def test_request_and_predictions_are_written_together(
    repository: TraceRepository, admin: psycopg.Connection, model_id: UUID
) -> None:
    trace = make_trace("/predict/batch", item_count=3, inference_duration_ms=4.2)

    repository.save_predictions(trace, model_id, make_rows(3))

    assert count(admin, "api_requests") == 1
    assert count(admin, "predictions") == 3


def test_failed_write_leaves_nothing_behind(
    repository: TraceRepository, admin: psycopg.Connection, model_id: UUID
) -> None:
    rows = make_rows(3)
    rows[2] = PredictionRow(rows[0].prediction_id, 2, {}, 1.0)

    with pytest.raises(psycopg.errors.UniqueViolation):
        repository.save_predictions(make_trace(item_count=3), model_id, rows)

    assert count(admin, "api_requests") == 0
    assert count(admin, "predictions") == 0


def test_error_request_must_have_an_error_code(repository: TraceRepository) -> None:
    repository.save_request(make_trace(status=422, error_code="invalid_input", item_count=None))

    with pytest.raises(psycopg.errors.CheckViolation):
        repository.save_request(make_trace(status=422, error_code=None))


def test_feedback_created_then_replayed(
    repository: TraceRepository, admin: psycopg.Connection, prediction: PredictionRow
) -> None:
    feedback = observed(prediction)
    first, replay = make_trace("/feedback", 201), make_trace("/feedback", 201)

    assert repository.save_feedback(first, feedback) == "created"
    assert repository.save_feedback(replay, feedback) == "already_recorded"

    assert (first.http_status, replay.http_status) == (201, 200)
    assert count(admin, "feedbacks") == 1
    # la requête d'origine reste celle rattachée au feedback
    stored = admin.execute("SELECT request_id FROM tracing.feedbacks").fetchone()[0]
    assert stored == first.request_id
    assert count(admin, "api_requests") == 3


@pytest.mark.parametrize("same_id", [True, False], ids=["meme-id", "autre-id"])
def test_different_feedback_is_a_conflict(
    repository: TraceRepository, admin: psycopg.Connection, prediction: PredictionRow, same_id: bool
) -> None:
    feedback = observed(prediction)
    repository.save_feedback(make_trace("/feedback", 201), feedback)
    other = observed(
        prediction,
        feedback_id=feedback.feedback_id if same_id else uuid4(),
        observed_rentals=999,
    )

    with pytest.raises(ApiError) as error:
        repository.save_feedback(make_trace("/feedback", 201), other)

    assert error.value.status == 409
    assert count(admin, "feedbacks") == 1
    # la transaction refusée n'a rien laissé, même pas sa ligne de requête
    assert count(admin, "api_requests") == 2


def test_feedback_on_unknown_prediction(repository: TraceRepository) -> None:
    ghost = PredictionRow(uuid4(), 0, {}, 1.0)

    with pytest.raises(ApiError) as error:
        repository.save_feedback(make_trace("/feedback", 201), observed(ghost))

    assert error.value.status == 404


def test_concurrent_identical_feedbacks_create_one_row(
    repository: TraceRepository, admin: psycopg.Connection, prediction: PredictionRow
) -> None:
    feedback = observed(prediction)

    def send(_: int) -> str:
        return repository.save_feedback(make_trace("/feedback", 201), feedback)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(send, range(8)))

    assert results.count("created") == 1
    assert results.count("already_recorded") == 7
    assert count(admin, "feedbacks") == 1


def test_heartbeat(repository: TraceRepository, admin: psycopg.Connection) -> None:
    repository.save_heartbeat(INSTANCE, 42, 12.5, 310.0, 3)

    row = admin.execute(
        "SELECT worker_pid, model_version FROM tracing.worker_heartbeats"
    ).fetchone()
    assert row == (42, 3)
