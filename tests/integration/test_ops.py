from uuid import uuid4

import psycopg
import pytest

from bikeshare.db.ops import OpsStore
from bikeshare.registry.models import AliasChange
from tests.integration.conftest import url_for

pytestmark = pytest.mark.integration


@pytest.fixture
def ops(admin: psycopg.Connection) -> OpsStore:
    return OpsStore(url_for("pipeline_writer"))


def test_run_lifecycle(ops: OpsStore, admin: psycopg.Connection) -> None:
    run_id = str(uuid4())

    ops.run_started(run_id, "bikeshare-training", "abc1234")
    ops.run_finished(
        run_id,
        status="completed",
        outcome="promoted",
        batch_ids=["2011-07"],
        mlflow_run_id="run-1",
        model_version="2",
        metrics={"mae": 18.5},
        step_durations={"entrainement": 1.2},
    )

    row = admin.execute(
        "SELECT status, outcome, batch_ids, model_version, metrics->>'mae', ended_at >= started_at"
        " FROM ops.pipeline_runs"
    ).fetchone()
    assert row == ("completed", "promoted", ["2011-07"], 2, "18.5", True)


def test_failed_run_keeps_the_error(ops: OpsStore, admin: psycopg.Connection) -> None:
    run_id = str(uuid4())
    ops.run_started(run_id, "bikeshare-training", "abc1234")

    ops.run_finished(run_id, status="failed", outcome="invalid_data", error="lot 2011-07 rejeté")

    row = admin.execute("SELECT status, outcome, error FROM ops.pipeline_runs").fetchone()
    assert row == ("failed", "invalid_data", "lot 2011-07 rejeté")


def test_validated_batch(ops: OpsStore, admin: psycopg.Connection) -> None:
    failures = [{"check": "volume"}, {"check": "volume"}, {"check": "bornes"}]

    ops.batch_validated(
        {"batch_id": "2011-07", "success": False, "rows": 372, "failures": failures}, None
    )

    row = admin.execute(
        "SELECT batch_id, accepted, row_count, failed_checks FROM ops.data_batches"
    ).fetchone()
    assert row == ("2011-07", False, 372, ["bornes", "volume"])


def test_alias_change_and_alert(ops: OpsStore, admin: psycopg.Connection) -> None:
    change = AliasChange(
        "rollback",
        "bikeshare-demand",
        "champion",
        "3",
        "2",
        "hugo",
        "régression",
        "2026-11-12T21:00:00+00:00",
    )

    ops.alias_changed(change)
    ops.alert_received("Pipeline en échec", "détail", forwarded=True)

    promotion = admin.execute(
        "SELECT action, from_version, to_version, actor FROM ops.model_promotions"
    ).fetchone()
    assert promotion == ("rollback", 3, 2, "hugo")
    assert admin.execute("SELECT subject, forwarded FROM ops.alerts").fetchone() == (
        "Pipeline en échec",
        True,
    )


def test_unreachable_database_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    ops = OpsStore("postgresql://pipeline_writer:x@127.0.0.1:9/absent")

    ops.run_started(str(uuid4()), "bikeshare-training", "abc1234")

    assert "suivi ops non enregistré" in caplog.text


def test_disabled_without_url(caplog: pytest.LogCaptureFixture) -> None:
    OpsStore(None).alert_received("sujet", "corps", forwarded=False)

    assert "suivi ops non enregistré" not in caplog.text
