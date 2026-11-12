from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from bikeshare.api.errors import feedback_conflict, prediction_not_found


@dataclass
class RequestTrace:
    request_id: UUID
    started_at: datetime
    endpoint: str
    http_status: int
    processing_duration_ms: float
    worker_pid: int
    instance_id: UUID
    api_revision: str
    campaign_id: str = "manual"
    item_count: int | None = None
    inference_duration_ms: float | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class PredictionRow:
    prediction_id: UUID
    item_index: int
    input_features: dict
    predicted_rentals: float


@dataclass(frozen=True)
class FeedbackRow:
    feedback_id: UUID
    prediction_id: UUID
    useful: bool | None
    observed_rentals: int | None
    observed_at: datetime | None
    label_source: str | None


INSERT_REQUEST = """
    INSERT INTO tracing.api_requests (
        request_id, started_at, endpoint, http_status, item_count, processing_duration_ms,
        inference_duration_ms, worker_pid, instance_id, api_revision, campaign_id, error_code
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def request_params(trace: RequestTrace) -> tuple:
    return (
        trace.request_id,
        trace.started_at,
        trace.endpoint,
        trace.http_status,
        trace.item_count,
        trace.processing_duration_ms,
        trace.inference_duration_ms,
        trace.worker_pid,
        trace.instance_id,
        trace.api_revision,
        trace.campaign_id,
        trace.error_code,
    )


class TraceRepository:
    """Écritures PostgreSQL de l'API. Chaque méthode est une transaction :
    soit tout est écrit, soit rien."""

    def __init__(self, url: str, min_size: int = 1, max_size: int = 5) -> None:
        self.pool = ConnectionPool(
            url, min_size=min_size, max_size=max_size, open=False, timeout=1, name="api"
        )

    def open(self) -> None:
        # wait=False : l'API démarre même si la base est arrêtée, /health/ready le signalera
        self.pool.open(wait=False)

    def close(self) -> None:
        self.pool.close()

    def ping(self) -> bool:
        try:
            with self.pool.connection(timeout=1) as conn:
                conn.execute("SELECT 1")
            return True
        except (psycopg.Error, TimeoutError):
            return False

    def register_model(self, tracking_uri: str, name: str, version: int, run_id: str) -> UUID:
        """Plusieurs workers chargent la même version : le premier insère, les autres relisent."""
        with self.pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO tracing.model_versions
                    (model_id, tracking_uri, registered_name, version, mlflow_run_id)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (tracking_uri, registered_name, version) DO NOTHING
                """,
                (uuid4(), tracking_uri, name, version, run_id),
            )
            row = conn.execute(
                """
                SELECT model_id FROM tracing.model_versions
                WHERE tracking_uri = %s AND registered_name = %s AND version = %s
                """,
                (tracking_uri, name, version),
            ).fetchone()
        return row[0]

    def save_predictions(
        self, trace: RequestTrace, model_id: UUID, predictions: list[PredictionRow]
    ) -> None:
        with self.pool.connection() as conn, conn.cursor() as cursor:
            cursor.execute(INSERT_REQUEST, request_params(trace))
            cursor.executemany(
                """
                INSERT INTO tracing.predictions (
                    prediction_id, request_id, item_index, model_id,
                    input_features, predicted_rentals
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        p.prediction_id,
                        trace.request_id,
                        p.item_index,
                        model_id,
                        Jsonb(p.input_features),
                        p.predicted_rentals,
                    )
                    for p in predictions
                ],
            )

    def save_request(self, trace: RequestTrace) -> None:
        with self.pool.connection() as conn:
            conn.execute(INSERT_REQUEST, request_params(trace))

    def save_feedback(
        self, trace: RequestTrace, feedback: FeedbackRow
    ) -> Literal["created", "already_recorded"]:
        """Un feedback par prédiction. Rejouer le même envoi ne crée rien de plus.

        Les doublons sont arbitrés par les contraintes d'unicité, pas par un
        "je vérifie puis j'insère" qui laisserait passer deux envois simultanés.
        Met à jour trace.http_status (201 ou 200) avant d'écrire la ligne de requête.
        """
        with self.pool.connection() as conn:
            known = conn.execute(
                "SELECT 1 FROM tracing.predictions WHERE prediction_id = %s",
                (feedback.prediction_id,),
            ).fetchone()
            if known is None:
                raise prediction_not_found()

            inserted = conn.execute(
                """
                INSERT INTO tracing.feedbacks (
                    feedback_id, request_id, prediction_id, useful,
                    observed_rentals, observed_at, label_source
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING feedback_id
                """,
                (
                    feedback.feedback_id,
                    trace.request_id,
                    feedback.prediction_id,
                    feedback.useful,
                    feedback.observed_rentals,
                    feedback.observed_at,
                    feedback.label_source,
                ),
            ).fetchone()

            if not inserted:
                existing = conn.execute(
                    """
                    SELECT prediction_id, useful, observed_rentals, observed_at, label_source
                    FROM tracing.feedbacks WHERE feedback_id = %s
                    """,
                    (feedback.feedback_id,),
                ).fetchone()
                # lever ici annule la transaction ; le 409 est tracé par le gestionnaire d'erreurs
                if existing is None:
                    raise feedback_conflict("Cette prédiction a déjà reçu un autre feedback.")
                if existing != (
                    feedback.prediction_id,
                    feedback.useful,
                    feedback.observed_rentals,
                    feedback.observed_at,
                    feedback.label_source,
                ):
                    raise feedback_conflict("Ce feedback_id existe déjà avec un autre contenu.")

            trace.http_status = 201 if inserted else 200
            conn.execute(INSERT_REQUEST, request_params(trace))
            return "created" if inserted else "already_recorded"

    def save_heartbeat(
        self,
        instance_id: UUID,
        worker_pid: int,
        cpu_percent: float,
        rss_mb: float,
        model_version: int | None,
    ) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO tracing.worker_heartbeats
                    (instance_id, worker_pid, cpu_percent, rss_mb, model_version)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (instance_id, worker_pid, cpu_percent, rss_mb, model_version),
            )
