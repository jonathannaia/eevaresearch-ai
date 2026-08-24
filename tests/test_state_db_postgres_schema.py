"""Durable-State Phase 4B — schema creation and idempotent migration for
the isolated Postgres backend (src/data_access/postgres_state_db/schema.py),
against the real local disposable Postgres test container. Every test
uses pg_isolated_connection (a uniquely-named, auto-dropped schema on
the shared local test database, never the default public schema, never
a hosted target) and skips cleanly if that container/password isn't
available this run — see tests/_postgres_test_support.py."""
from __future__ import annotations

from src.data_access.postgres_state_db import schema as postgres_schema

from tests._postgres_test_support import pg_isolated_connection  # noqa: F401 (fixture import)


def test_get_schema_version_is_zero_before_any_migration(pg_isolated_connection):
    assert postgres_schema.get_schema_version(pg_isolated_connection) == 0


def test_fresh_schema_migrates_to_current_version(pg_isolated_connection):
    result = postgres_schema.migrate(pg_isolated_connection)
    assert result == postgres_schema.CURRENT_SCHEMA_VERSION


def test_migration_is_idempotent_on_an_already_migrated_schema(pg_isolated_connection):
    first = postgres_schema.migrate(pg_isolated_connection)
    second = postgres_schema.migrate(pg_isolated_connection)
    assert first == second == postgres_schema.CURRENT_SCHEMA_VERSION
    assert postgres_schema.get_schema_version(pg_isolated_connection) == postgres_schema.CURRENT_SCHEMA_VERSION


def test_migration_is_repeatable_many_times(pg_isolated_connection):
    results = [postgres_schema.migrate(pg_isolated_connection) for _ in range(5)]
    assert results == [postgres_schema.CURRENT_SCHEMA_VERSION] * 5


def test_all_expected_tables_exist_after_migration(pg_isolated_connection):
    postgres_schema.migrate(pg_isolated_connection)
    rows = pg_isolated_connection.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
    ).fetchall()
    names = {row["table_name"] for row in rows}
    assert {"filing_events", "candidates", "state_transitions", "resolved_identifiers", "schema_version"}.issubset(names)


def test_no_signals_table_exists(pg_isolated_connection):
    postgres_schema.migrate(pg_isolated_connection)
    rows = pg_isolated_connection.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
    ).fetchall()
    names = {row["table_name"] for row in rows}
    assert "signals" not in names


def test_migration_leaves_no_open_transaction_between_steps(pg_isolated_connection):
    """A no-hidden-state proof mirroring the SQLite suite's own
    discipline: after migrate() returns, ordinary reads on the same
    connection work without the caller needing to commit/rollback
    first — every step already committed cleanly inside migrate()
    itself."""
    postgres_schema.migrate(pg_isolated_connection)
    row = pg_isolated_connection.execute("SELECT COUNT(*) AS n FROM candidates").fetchone()
    assert row["n"] == 0
