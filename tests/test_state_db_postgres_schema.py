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


# --- Translation reliability workstream: schema version 10 ---


def test_fresh_schema_migrates_straight_to_v10(pg_isolated_connection):
    assert postgres_schema.migrate(pg_isolated_connection) == 10 == postgres_schema.CURRENT_SCHEMA_VERSION


def test_v9_schema_upgrades_to_v10(pg_isolated_connection):
    """Simulates a schema that was already at v9 before this workstream —
    applies exactly migrations 1..9 (bypassing migrate()'s own "run
    everything up to CURRENT" behavior), confirms it really is recorded
    at v9, then calls migrate() and confirms it reaches v10 with the five
    new columns present and usable."""
    conn = pg_isolated_connection
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (0)")
    conn.commit()
    for target_version, statements in postgres_schema._MIGRATIONS:
        if target_version > 9:
            continue
        for statement in statements:
            conn.execute(statement)
        conn.execute("UPDATE schema_version SET version = %s", (target_version,))
    conn.commit()
    assert postgres_schema.get_schema_version(conn) == 9

    result = postgres_schema.migrate(conn)

    assert result == 10 == postgres_schema.CURRENT_SCHEMA_VERSION
    assert postgres_schema.get_schema_version(conn) == 10
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'candidates'"
    ).fetchall()
    columns = {row["column_name"] for row in rows}
    assert {
        "translation_failure_category", "translation_failure_reason", "translation_failure_at",
        "translation_retry_count", "translation_next_retry_at",
    } <= columns


def test_migrating_an_already_v10_schema_is_idempotent(pg_isolated_connection):
    postgres_schema.migrate(pg_isolated_connection)
    assert postgres_schema.get_schema_version(pg_isolated_connection) == 10
    result = postgres_schema.migrate(pg_isolated_connection)
    assert result == 10
    assert postgres_schema.get_schema_version(pg_isolated_connection) == 10


def test_v10_translation_retry_count_defaults_to_zero(pg_isolated_connection):
    postgres_schema.migrate(pg_isolated_connection)
    rows = pg_isolated_connection.execute(
        "SELECT column_name, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'candidates' "
        "AND column_name = 'translation_retry_count'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["is_nullable"] == "NO"
    assert rows[0]["column_default"] == "0"


def test_migration_leaves_no_open_transaction_between_steps(pg_isolated_connection):
    """A no-hidden-state proof mirroring the SQLite suite's own
    discipline: after migrate() returns, ordinary reads on the same
    connection work without the caller needing to commit/rollback
    first — every step already committed cleanly inside migrate()
    itself."""
    postgres_schema.migrate(pg_isolated_connection)
    row = pg_isolated_connection.execute("SELECT COUNT(*) AS n FROM candidates").fetchone()
    assert row["n"] == 0
