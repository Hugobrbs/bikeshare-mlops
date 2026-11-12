import os
from collections.abc import Iterator

import psycopg
import pytest

from bikeshare.db.migrate import init_database

ADMIN_URL = os.environ.get("TEST_ADMIN_DATABASE_URL")
PASSWORDS = {
    "api_writer": "api-test",
    "pipeline_writer": "ops-test",
    "grafana_reader": "grafana-test",
}

TABLES = (
    "tracing.feedbacks",
    "tracing.predictions",
    "tracing.api_requests",
    "tracing.model_versions",
    "tracing.worker_heartbeats",
    "ops.pipeline_runs",
    "ops.data_batches",
    "ops.model_promotions",
    "ops.alerts",
)


def url_for(role: str) -> str:
    host_and_database = ADMIN_URL.split("@", 1)[1]
    return f"postgresql://{role}:{PASSWORDS[role]}@{host_and_database}"


@pytest.fixture(scope="session")
def database() -> str:
    """Base PostgreSQL jetable, initialisée comme en vrai. Voir `make test-integration`."""
    if not ADMIN_URL:
        pytest.skip("TEST_ADMIN_DATABASE_URL non défini")
    init_database(ADMIN_URL, PASSWORDS)
    return ADMIN_URL


@pytest.fixture
def admin(database: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(database, autocommit=True) as conn:
        conn.execute(f"TRUNCATE {', '.join(TABLES)} CASCADE")
        yield conn
