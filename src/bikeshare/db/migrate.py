from importlib.resources import files

import psycopg
from psycopg import sql

ROLES = ("api_writer", "pipeline_writer", "grafana_reader")


def init_database(admin_url: str, passwords: dict[str, str]) -> None:
    """Crée (ou met à jour) les rôles puis applique schema.sql. Rejouable."""
    with psycopg.connect(admin_url) as conn:
        for role in ROLES:
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
            conn.execute(
                sql.SQL("{} ROLE {} LOGIN PASSWORD {}").format(
                    sql.SQL("ALTER" if exists else "CREATE"),
                    sql.Identifier(role),
                    sql.Literal(passwords[role]),
                )
            )
        conn.execute("ALTER ROLE grafana_reader SET statement_timeout = '10s'")
        conn.execute("ALTER ROLE grafana_reader SET default_transaction_read_only = on")
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(conn.info.dbname),
                sql.SQL(", ").join(sql.Identifier(role) for role in ROLES),
            )
        )
        conn.execute(files("bikeshare.db").joinpath("schema.sql").read_text(encoding="utf-8"))
