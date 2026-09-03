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
#
# test_fresh_schema_migrates_to_current_version and
# test_migration_is_idempotent_on_an_already_migrated_schema above
# already cover "a fresh/already-migrated schema reaches
# CURRENT_SCHEMA_VERSION" generically — the two v10-specific duplicates
# of that were removed. What remains are the genuinely version-specific
# historical-transition proofs.


def _migrate_up_to(conn, target: int) -> None:
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (0)")
    conn.commit()
    for version, statements in postgres_schema._MIGRATIONS:
        if version > target:
            continue
        for statement in statements:
            conn.execute(statement)
        conn.execute("UPDATE schema_version SET version = %s", (version,))
    conn.commit()


def test_v9_schema_upgrades_to_v10(pg_isolated_connection):
    """Simulates a schema that was already at v9 before the translation
    reliability workstream — applies exactly migrations 1..9, confirms it
    really is recorded at v9, then temporarily bounds migrate() to stop
    at v10 (CURRENT is now v11 or later) and confirms it reaches v10 with
    the five new columns present and usable."""
    conn = pg_isolated_connection
    _migrate_up_to(conn, 9)
    assert postgres_schema.get_schema_version(conn) == 9

    original_migrations = postgres_schema._MIGRATIONS
    postgres_schema._MIGRATIONS = tuple(m for m in original_migrations if m[0] <= 10)
    try:
        result = postgres_schema.migrate(conn)
    finally:
        postgres_schema._MIGRATIONS = original_migrations

    assert result == 10
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


# --- Daily News durability workstream: schema version 11 ---


def test_v10_schema_upgrades_to_v11(pg_isolated_connection):
    """Purely historical version-to-version transition proof — bounded to
    stop at v11 rather than asserting equality with CURRENT_SCHEMA_VERSION,
    matching test_v10_database_upgrades_to_v11's own SQLite-side fix
    (CURRENT is now v12 or later)."""
    conn = pg_isolated_connection
    _migrate_up_to(conn, 10)
    assert postgres_schema.get_schema_version(conn) == 10

    original_migrations = postgres_schema._MIGRATIONS
    postgres_schema._MIGRATIONS = tuple(m for m in original_migrations if m[0] <= 11)
    try:
        result = postgres_schema.migrate(conn)
    finally:
        postgres_schema._MIGRATIONS = original_migrations

    assert result == 11
    assert postgres_schema.get_schema_version(conn) == 11
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
    ).fetchall()
    names = {row["table_name"] for row in rows}
    assert {"daily_news_stories", "daily_news_sources", "daily_news_state_transitions"} <= names


def test_v11_daily_news_tables_start_empty_and_are_usable(pg_isolated_connection):
    postgres_schema.migrate(pg_isolated_connection)
    for table in ("daily_news_stories", "daily_news_sources", "daily_news_state_transitions"):
        row = pg_isolated_connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        assert row["n"] == 0


def test_v11_daily_news_sources_url_is_unique(pg_isolated_connection):
    import psycopg
    import pytest

    conn = pg_isolated_connection
    postgres_schema.migrate(conn)
    conn.execute(
        "INSERT INTO daily_news_stories (id, company_name, headline, status, created_at, updated_at) "
        "VALUES ('s1', 'NVIDIA', 'Headline', 'Published', 'now', 'now')"
    )
    conn.execute(
        "INSERT INTO daily_news_sources (story_id, publisher, source_class, url, title, published_at, retrieved_at, original_language) "
        "VALUES ('s1', 'NVIDIA', 'Official company source', 'https://example.com/dup', 'T', 'now', 'now', 'English')"
    )
    conn.commit()
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO daily_news_stories (id, company_name, headline, status, created_at, updated_at) "
            "VALUES ('s2', 'NVIDIA', 'Headline 2', 'Published', 'now', 'now')"
        )
        conn.execute(
            "INSERT INTO daily_news_sources (story_id, publisher, source_class, url, title, published_at, retrieved_at, original_language) "
            "VALUES ('s2', 'NVIDIA', 'Official company source', 'https://example.com/dup', 'T2', 'now', 'now', 'English')"
        )
        conn.commit()


# --- Daily News autonomous worker: schema version 12 ---


def test_v11_schema_upgrades_to_v12(pg_isolated_connection):
    conn = pg_isolated_connection
    _migrate_up_to(conn, 11)
    assert postgres_schema.get_schema_version(conn) == 11

    result = postgres_schema.migrate(conn)

    assert result == 12 == postgres_schema.CURRENT_SCHEMA_VERSION
    assert postgres_schema.get_schema_version(conn) == 12
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
    ).fetchall()
    names = {row["table_name"] for row in rows}
    assert {"daily_news_scan_status", "daily_news_worker_status"} <= names


def test_v12_daily_news_worker_status_tables_start_empty_and_are_usable(pg_isolated_connection):
    postgres_schema.migrate(pg_isolated_connection)
    for table in ("daily_news_scan_status", "daily_news_worker_status"):
        row = pg_isolated_connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        assert row["n"] == 0


def test_v12_daily_news_scan_status_round_trips_and_upserts(pg_isolated_connection):
    conn = pg_isolated_connection
    postgres_schema.migrate(conn)
    conn.execute(
        "INSERT INTO daily_news_scan_status (company_name, last_attempt_at, updated_at) "
        "VALUES ('NVIDIA', 'now', 'now')"
    )
    conn.commit()
    row = conn.execute("SELECT * FROM daily_news_scan_status WHERE company_name = 'NVIDIA'").fetchone()
    assert row["items_discovered_last_run"] == 0

    conn.execute(
        "INSERT INTO daily_news_scan_status (company_name, last_attempt_at, updated_at) "
        "VALUES ('NVIDIA', 'later', 'later') "
        "ON CONFLICT (company_name) DO UPDATE SET last_attempt_at = excluded.last_attempt_at, "
        "updated_at = excluded.updated_at"
    )
    conn.commit()
    row = conn.execute("SELECT * FROM daily_news_scan_status WHERE company_name = 'NVIDIA'").fetchone()
    assert row["last_attempt_at"] == "later"
    conn.rollback()


def test_migration_leaves_no_open_transaction_between_steps(pg_isolated_connection):
    """A no-hidden-state proof mirroring the SQLite suite's own
    discipline: after migrate() returns, ordinary reads on the same
    connection work without the caller needing to commit/rollback
    first — every step already committed cleanly inside migrate()
    itself."""
    postgres_schema.migrate(pg_isolated_connection)
    row = pg_isolated_connection.execute("SELECT COUNT(*) AS n FROM candidates").fetchone()
    assert row["n"] == 0
