import logging
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from bikeshare.registry.models import AliasChange

logger = logging.getLogger(__name__)


class OpsStore:
    def __init__(self, url: str | None) -> None:
        self.url = url

    def _execute(self, query: str, params: tuple) -> None:
        if not self.url:
            return
        try:
            with psycopg.connect(self.url, connect_timeout=5) as conn:
                conn.execute(query, params)
        except psycopg.Error as e:
            logger.warning("suivi ops non enregistré : %s", e)

    def run_started(self, flow_run_id: str, flow_name: str, app_revision: str) -> None:
        self._execute(
            """
            INSERT INTO ops.pipeline_runs (flow_run_id, flow_name, started_at, status, app_revision)
            VALUES (%s, %s, %s, 'running', %s)
            ON CONFLICT (flow_run_id) DO NOTHING
            """,
            (flow_run_id, flow_name, datetime.now(UTC), app_revision),
        )

    def run_finished(
        self,
        flow_run_id: str,
        *,
        status: str,
        outcome: str,
        batch_ids: list[str] | None = None,
        mlflow_run_id: str | None = None,
        model_version: str | None = None,
        metrics: dict[str, Any] | None = None,
        step_durations: dict[str, float] | None = None,
        error: str | None = None,
    ) -> None:
        self._execute(
            """
            UPDATE ops.pipeline_runs
            SET ended_at = %s, status = %s, outcome = %s, batch_ids = %s, mlflow_run_id = %s,
                model_version = %s, metrics = %s, step_durations = %s, error = %s
            WHERE flow_run_id = %s
            """,
            (
                datetime.now(UTC),
                status,
                outcome,
                batch_ids or [],
                mlflow_run_id,
                int(model_version) if model_version else None,
                Jsonb(metrics) if metrics else None,
                Jsonb(step_durations) if step_durations else None,
                error[:2000] if error else None,
                flow_run_id,
            ),
        )

    def batch_validated(self, report: dict, flow_run_id: str | None) -> None:
        self._execute(
            """
            INSERT INTO ops.data_batches (batch_id, flow_run_id, accepted, row_count, failed_checks)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                report["batch_id"],
                flow_run_id,
                report["success"],
                report["rows"],
                sorted({f["check"] for f in report["failures"]}),
            ),
        )

    def alias_changed(self, change: AliasChange) -> None:
        self._execute(
            """
            INSERT INTO ops.model_promotions
                (changed_at, action, model_name, alias, from_version, to_version, actor, reason)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                change.at,
                change.action,
                change.model_name,
                change.alias,
                int(change.from_version) if change.from_version else None,
                int(change.to_version),
                change.actor,
                change.reason,
            ),
        )

    def alert_received(self, subject: str, body: str, forwarded: bool) -> None:
        self._execute(
            "INSERT INTO ops.alerts (subject, body, forwarded) VALUES (%s, %s, %s)",
            (subject[:500], body[:5000], forwarded),
        )
