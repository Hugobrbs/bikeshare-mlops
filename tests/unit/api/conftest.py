from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import numpy as np
import pandas as pd
import psycopg
import pytest
from fastapi.testclient import TestClient

from bikeshare.api.app import create_app
from bikeshare.api.errors import feedback_conflict, prediction_not_found
from bikeshare.api.model_store import LoadedModel, UnapprovedModelError
from bikeshare.api.repository import FeedbackRow, PredictionRow, RequestTrace
from bikeshare.settings import Settings


class FakeRepository:
    def __init__(self) -> None:
        self.available = True
        self.rejects_writes_with: Exception | None = None
        self.requests: list[RequestTrace] = []
        self.predictions: dict[UUID, PredictionRow] = {}
        self.feedbacks: dict[UUID, FeedbackRow] = {}
        self.heartbeats: list[tuple] = []

    def _check(self) -> None:
        if not self.available:
            raise psycopg.OperationalError("connexion refusée")

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def ping(self) -> bool:
        return self.available

    def register_model(self, tracking_uri: str, name: str, version: int, run_id: str) -> UUID:
        return uuid4()

    def save_predictions(self, trace: RequestTrace, model_id: UUID, rows: list) -> None:
        self._check()
        if self.rejects_writes_with:
            raise self.rejects_writes_with
        self.requests.append(trace)
        self.predictions.update({row.prediction_id: row for row in rows})

    def save_request(self, trace: RequestTrace) -> None:
        self._check()
        self.requests.append(trace)

    def save_feedback(self, trace: RequestTrace, feedback: FeedbackRow) -> str:
        self._check()
        if feedback.prediction_id not in self.predictions:
            raise prediction_not_found()
        existing = self.feedbacks.get(feedback.feedback_id)
        if existing is None:
            if any(f.prediction_id == feedback.prediction_id for f in self.feedbacks.values()):
                raise feedback_conflict("Cette prédiction a déjà reçu un autre feedback.")
            self.feedbacks[feedback.feedback_id] = feedback
        elif existing != feedback:
            raise feedback_conflict("Ce feedback_id existe déjà avec un autre contenu.")
        trace.http_status = 200 if existing else 201
        self.requests.append(trace)
        return "already_recorded" if existing else "created"

    def save_heartbeat(self, *row: object) -> None:
        self._check()
        self.heartbeats.append(row)


class HourModel:
    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return pd.to_datetime(frame["timestamp"]).dt.hour.to_numpy() * 10.0


class FakeModelStore:
    def __init__(self) -> None:
        self.last_error: str | None = None
        self.refused = False
        self.refreshes = 0
        self.current: LoadedModel | None = LoadedModel(
            model_id=uuid4(),
            name="bikeshare-demand",
            version=3,
            run_id="run-abc",
            alias="champion",
            pipeline=HourModel(),
            loaded_at=datetime(2026, 11, 12, tzinfo=UTC),
        )

    @property
    def registry_reachable(self) -> bool:
        return self.last_error is None or self.refused

    def refresh(self) -> bool:
        self.refreshes += 1
        if self.refused:
            raise UnapprovedModelError(self.last_error)
        if self.last_error:
            raise RuntimeError(self.last_error)
        return False

    def switch_to(self, version: int) -> None:
        self.current = replace(self.current, version=version)


@pytest.fixture
def repository() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def models() -> FakeModelStore:
    return FakeModelStore()


@pytest.fixture
def client(repository: FakeRepository, models: FakeModelStore) -> Iterator[TestClient]:
    settings = Settings(admin_token="jeton-de-test", app_revision="abc1234")
    app = create_app(settings, repository, models)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def observation() -> dict:
    return {
        "timestamp": "2012-06-15T08:00:00",
        "holiday": False,
        "weather_code": 1,
        "temperature_c": 22.5,
        "feels_like_c": 25.0,
        "humidity_pct": 60,
        "windspeed_kmh": 12.0,
    }
