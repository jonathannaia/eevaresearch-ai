"""The autonomous EDGAR publication control against a REAL PostgreSQL
server — migration, SQLite/PostgreSQL parity, and fail-closed behaviour.

Local disposable test infrastructure only, via the same loopback
container `scripts/postgres_test_container.sh` starts for every other
Postgres test here. Never a hosted database, never production.

The parity and atomicity checks are merge gates: the two repositories
share no code by deliberate convention, and psycopg's autocommit=False
rollback semantics only exist on a real server."""
from __future__ import annotations

import pytest

from src.data_access.postgres_state_db import connection as pg_connection
from src.data_access.postgres_state_db import edgar_publication_control_repository as pg_repo
from src.data_access.postgres_state_db import schema as pg_schema
from src.data_access.state_db import connection as sqlite_connection
from src.data_access.state_db import edgar_publication_control_repository as sqlite_repo
from src.data_access.state_db import schema as sqlite_schema

from tests._postgres_test_support import pg_isolated_dsn  # noqa: F401 (fixture import)


@pytest.fixture
def pg_conn(pg_isolated_dsn):
    conn = pg_connection.connect(pg_isolated_dsn)
    pg_schema.migrate(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def sqlite_conn():
    conn = sqlite_connection.connect_in_memory()
    sqlite_schema.migrate(conn)
    return conn


def test_the_migration_creates_the_control_table_and_is_idempotent(pg_conn):
    assert pg_schema.migrate(pg_conn) == pg_schema.CURRENT_SCHEMA_VERSION
    assert pg_schema.migrate(pg_conn) == pg_schema.CURRENT_SCHEMA_VERSION
    assert pg_repo.get_control(pg_conn) is None


# --- MERGE GATE: SQLite / PostgreSQL parity ---------------------------------

def test_both_backends_default_to_disabled_when_no_row_exists(pg_conn, sqlite_conn):
    assert pg_repo.is_publication_enabled(pg_conn) is False
    assert sqlite_repo.is_publication_enabled(sqlite_conn) is False
    assert pg_repo.get_control(pg_conn) is None
    assert sqlite_repo.get_control(sqlite_conn) is None


def test_both_backends_round_trip_the_control_identically(pg_conn, sqlite_conn):
    for repo, conn in ((pg_repo, pg_conn), (sqlite_repo, sqlite_conn)):
        repo.set_control(conn, enabled=True, reason="cohort start", updated_by="ops", now="t0")

    from_pg = pg_repo.get_control(pg_conn)
    from_sqlite = sqlite_repo.get_control(sqlite_conn)
    for field in ("enabled", "reason", "updated_by", "updated_at"):
        assert getattr(from_pg, field) == getattr(from_sqlite, field), field
    assert from_pg.enabled is True and from_sqlite.enabled is True


def test_both_backends_keep_exactly_one_row_under_repeated_writes(pg_conn, sqlite_conn):
    for index in range(4):
        pg_repo.set_control(pg_conn, enabled=True, reason=f"r{index}", updated_by="ops", now=f"t{index}")
        sqlite_repo.set_control(sqlite_conn, enabled=True, reason=f"r{index}", updated_by="ops", now=f"t{index}")

    assert pg_conn.execute("SELECT COUNT(*) AS n FROM edgar_publication_control").fetchone()["n"] == 1
    assert sqlite_conn.execute("SELECT COUNT(*) AS n FROM edgar_publication_control").fetchone()["n"] == 1


def test_both_backends_disable_identically(pg_conn, sqlite_conn):
    for repo, conn in ((pg_repo, pg_conn), (sqlite_repo, sqlite_conn)):
        repo.set_control(conn, enabled=True, reason="on", updated_by="ops", now="t0")
        repo.set_control(conn, enabled=False, reason="halt", updated_by="ops", now="t1")

    assert pg_repo.is_publication_enabled(pg_conn) is False
    assert sqlite_repo.is_publication_enabled(sqlite_conn) is False


# --- MERGE GATE: fail-closed and atomicity ----------------------------------

def test_a_missing_table_fails_closed_on_postgres(pg_conn):
    pg_conn.execute("DROP TABLE edgar_publication_control")
    pg_conn.commit()

    assert pg_repo.is_publication_enabled(pg_conn) is False


def test_an_aborted_transaction_leaves_publication_disabled(pg_conn):
    """The dangerous direction: a half-applied enable must not leave the
    control reading enabled."""
    import psycopg

    with pytest.raises(psycopg.Error):
        with pg_connection.transaction(pg_conn):
            pg_repo.set_control(pg_conn, enabled=True, reason="on", updated_by="ops", now="t0")
            pg_conn.execute("SELECT 1 FROM a_table_that_does_not_exist_publication_control")

    pg_conn.rollback()
    assert pg_repo.is_publication_enabled(pg_conn) is False
    assert pg_repo.get_control(pg_conn) is None


def test_a_clean_transaction_commits_the_control(pg_conn):
    with pg_connection.transaction(pg_conn):
        pg_repo.set_control(pg_conn, enabled=True, reason="on", updated_by="ops", now="t0")

    assert pg_repo.is_publication_enabled(pg_conn) is True
