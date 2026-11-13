import psycopg
import pytest

from bikeshare.db.migrate import init_database
from tests.integration.conftest import PASSWORDS, url_for

pytestmark = pytest.mark.integration


def test_init_can_be_replayed(database: str, admin: psycopg.Connection) -> None:
    admin.execute("INSERT INTO ops.alerts (subject, body) VALUES ('test', 'conservée')")

    init_database(database, PASSWORDS)

    assert admin.execute("SELECT count(*) FROM ops.alerts").fetchone()[0] == 1


def test_api_account_can_only_append(admin: psycopg.Connection) -> None:
    with psycopg.connect(url_for("api_writer"), autocommit=True) as conn:
        conn.execute(
            "INSERT INTO tracing.worker_heartbeats (instance_id, worker_pid, cpu_percent, rss_mb)"
            " VALUES (gen_random_uuid(), 1, 0, 0)"
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE tracing.worker_heartbeats SET rss_mb = 1")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM tracing.worker_heartbeats")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM ops.pipeline_runs")


def test_grafana_account_is_read_only(admin: psycopg.Connection) -> None:
    with psycopg.connect(url_for("grafana_reader"), autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM tracing.api_requests").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM ops.pipeline_runs").fetchone()[0] == 0
        with pytest.raises(psycopg.Error):
            conn.execute("INSERT INTO ops.alerts (subject, body) VALUES ('x', 'y')")
        assert conn.execute("SHOW statement_timeout").fetchone()[0] == "10s"
