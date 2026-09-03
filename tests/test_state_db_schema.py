"""state_db.schema — migration idempotency, schema-version recording,
foreign-key enforcement, and transaction rollback. In-memory SQLite only;
no real file, no data/cache/ access."""
from __future__ import annotations

import sqlite3

import pytest

from src.data_access.state_db import connection, schema


def test_fresh_database_migrates_to_current_version():
    conn = connection.connect_in_memory()
    assert schema.get_schema_version(conn) == 0
    result = schema.migrate(conn)
    assert result == schema.CURRENT_SCHEMA_VERSION
    assert schema.get_schema_version(conn) == schema.CURRENT_SCHEMA_VERSION


def test_migration_is_idempotent_on_an_already_migrated_database():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    tables_before = {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    result = schema.migrate(conn)
    tables_after = {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    assert result == schema.CURRENT_SCHEMA_VERSION
    assert tables_before == tables_after  # no duplicate/re-created tables


def test_migration_is_repeatable_many_times_on_a_temp_file_database(tmp_path):
    db_path = tmp_path / "state.db"
    for _ in range(3):
        conn = connection.connect(db_path)
        result = schema.migrate(conn)
        assert result == schema.CURRENT_SCHEMA_VERSION
        conn.close()


def test_all_expected_tables_exist_after_migration():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    assert {"schema_version", "filing_events", "candidates", "state_transitions", "resolved_identifiers"} <= tables


def test_no_signals_table_exists():
    # Signals must remain derived, never persisted — see
    # signal_repository.py's own module docstring.
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    assert not any("signal" in t.lower() for t in tables)


def test_foreign_keys_are_enforced_on_every_connection():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO candidates (
                id, source, filing_corp_code, filing_rcept_no, matched_rules_json, confidence, status,
                extraction_state, translation_state, excerpt_quality, version, created_at, updated_at
            ) VALUES ('orphan', 'SEC EDGAR', '9999999999', 'no-such-accession', '[]', 'Low',
                      'Candidate detected', 'Not fetched', 'Not requested', 'Unknown', 1, 'x', 'x')
            """
        )
        conn.commit()


# --- Translation reliability workstream: schema version 10 ---
#
# test_fresh_database_migrates_to_current_version and
# test_migration_is_idempotent_on_an_already_migrated_database above
# already cover "a fresh/already-migrated database reaches
# CURRENT_SCHEMA_VERSION" generically (dynamic, never hardcoding a
# version number) — the two v10-specific tests that used to duplicate
# that generically here were removed rather than kept as dead weight.
# What remains below are the genuinely version-specific historical-
# transition proofs, which stay meaningful forever regardless of how
# many later versions exist.


def _migrate_up_to(conn, target: int) -> None:
    """Manually replays every migration step up to (and including)
    `target`, bypassing migrate()'s own "run everything up to CURRENT"
    behavior — for tests that need to freeze a database at a specific
    historical version before exercising the next real upgrade step."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (0)")
    conn.commit()
    for version, statements in schema._MIGRATIONS:
        if version > target:
            continue
        for statement in statements:
            conn.execute(statement)
        conn.execute("UPDATE schema_version SET version = ?", (version,))
    conn.commit()


def test_v9_database_upgrades_to_v10():
    """Simulates a database that was already at v9 before the translation
    reliability workstream — applies exactly migrations 1..9, confirms
    it really is recorded at v9, then temporarily bounds migrate() to
    stop at v10 (bypassing its own "run everything up to CURRENT"
    behavior, since CURRENT is now v11 or later) and confirms it reaches
    v10 with the five new columns present and usable."""
    conn = connection.connect_in_memory()
    _migrate_up_to(conn, 9)
    assert schema.get_schema_version(conn) == 9

    original_migrations = schema._MIGRATIONS
    schema._MIGRATIONS = tuple(m for m in original_migrations if m[0] <= 10)
    try:
        result = schema.migrate(conn)
    finally:
        schema._MIGRATIONS = original_migrations

    assert result == 10
    assert schema.get_schema_version(conn) == 10
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(candidates)").fetchall()}
    assert {
        "translation_failure_category", "translation_failure_reason", "translation_failure_at",
        "translation_retry_count", "translation_next_retry_at",
    } <= columns


def test_v10_columns_have_correct_types_and_defaults():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    columns = {row["name"]: row for row in conn.execute("PRAGMA table_info(candidates)").fetchall()}
    assert columns["translation_failure_category"]["notnull"] == 0
    assert columns["translation_failure_reason"]["notnull"] == 0
    assert columns["translation_failure_at"]["notnull"] == 0
    assert columns["translation_next_retry_at"]["notnull"] == 0
    assert columns["translation_retry_count"]["notnull"] == 1
    assert columns["translation_retry_count"]["dflt_value"] == "0"


# --- Daily News durability workstream: schema version 11 ---


def test_v10_database_upgrades_to_v11():
    """Simulates a database that was already at v10 before the Daily News
    durability workstream — applies exactly migrations 1..10, confirms it
    really is recorded at v10, then temporarily bounds migrate() to stop
    at v11 (bypassing its own "run everything up to CURRENT" behavior,
    since CURRENT is now v12 or later) and confirms it reaches v11 with
    the three new Daily News tables present and usable. A purely
    historical version-to-version transition proof — see the module
    comment above test_v9_database_upgrades_to_v10 for why this stays a
    literal, never a dynamic CURRENT_SCHEMA_VERSION comparison."""
    conn = connection.connect_in_memory()
    _migrate_up_to(conn, 10)
    assert schema.get_schema_version(conn) == 10

    original_migrations = schema._MIGRATIONS
    schema._MIGRATIONS = tuple(m for m in original_migrations if m[0] <= 11)
    try:
        result = schema.migrate(conn)
    finally:
        schema._MIGRATIONS = original_migrations

    assert result == 11
    assert schema.get_schema_version(conn) == 11
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    assert {"daily_news_stories", "daily_news_sources", "daily_news_state_transitions"} <= tables


def test_v11_daily_news_tables_start_empty_and_are_usable():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    assert conn.execute("SELECT COUNT(*) AS n FROM daily_news_stories").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM daily_news_sources").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM daily_news_state_transitions").fetchone()["n"] == 0


def test_v11_daily_news_stories_column_shape():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    columns = {row["name"]: row for row in conn.execute("PRAGMA table_info(daily_news_stories)").fetchall()}
    assert columns["id"]["pk"] == 1
    assert columns["company_name"]["notnull"] == 1
    assert columns["ticker"]["notnull"] == 0
    assert columns["version"]["dflt_value"] == "1"


def test_v11_daily_news_sources_url_is_unique():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    conn.execute(
        "INSERT INTO daily_news_stories (id, company_name, headline, status, created_at, updated_at) "
        "VALUES ('s1', 'NVIDIA', 'Headline', 'Published', 'now', 'now')"
    )
    conn.execute(
        "INSERT INTO daily_news_sources (story_id, publisher, source_class, url, title, published_at, retrieved_at, original_language) "
        "VALUES ('s1', 'NVIDIA', 'Official company source', 'https://example.com/dup', 'T', 'now', 'now', 'English')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
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


def test_v11_database_upgrades_to_v12():
    """Simulates a database that was already at v11 before this
    workstream — applies exactly migrations 1..11, confirms it really is
    recorded at v11, then calls migrate() (CURRENT_SCHEMA_VERSION is
    exactly 12 as of this workstream) and confirms it reaches v12 with
    the two new worker-status tables present and usable."""
    conn = connection.connect_in_memory()
    _migrate_up_to(conn, 11)
    assert schema.get_schema_version(conn) == 11

    result = schema.migrate(conn)

    assert result == 12 == schema.CURRENT_SCHEMA_VERSION
    assert schema.get_schema_version(conn) == 12
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    assert {"daily_news_scan_status", "daily_news_worker_status"} <= tables


def test_v12_daily_news_worker_status_tables_start_empty_and_are_usable():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    assert conn.execute("SELECT COUNT(*) AS n FROM daily_news_scan_status").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM daily_news_worker_status").fetchone()["n"] == 0


def test_v12_daily_news_scan_status_round_trips_and_upserts():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    conn.execute(
        "INSERT INTO daily_news_scan_status (company_name, last_attempt_at, updated_at) "
        "VALUES ('NVIDIA', 'now', 'now')"
    )
    conn.commit()
    row = conn.execute("SELECT * FROM daily_news_scan_status WHERE company_name = 'NVIDIA'").fetchone()
    assert row["items_discovered_last_run"] == 0
    assert row["stories_published_last_run"] == 0

    conn.execute(
        "INSERT INTO daily_news_scan_status (company_name, last_attempt_at, updated_at) "
        "VALUES ('NVIDIA', 'later', 'later') "
        "ON CONFLICT (company_name) DO UPDATE SET last_attempt_at = excluded.last_attempt_at, "
        "updated_at = excluded.updated_at"
    )
    conn.commit()
    row = conn.execute("SELECT * FROM daily_news_scan_status WHERE company_name = 'NVIDIA'").fetchone()
    assert row["last_attempt_at"] == "later"
    assert conn.execute("SELECT COUNT(*) AS n FROM daily_news_scan_status").fetchone()["n"] == 1


def test_v12_daily_news_worker_status_single_row_by_worker_key():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    conn.execute(
        "INSERT INTO daily_news_worker_status (worker_key, updated_at) VALUES ('daily_news', 'now')"
    )
    conn.commit()
    row = conn.execute("SELECT * FROM daily_news_worker_status WHERE worker_key = 'daily_news'").fetchone()
    assert row["last_tick_started_at"] is None
    assert row["last_reconciliation_at"] is None


def test_transaction_helper_rolls_back_on_failure_leaving_no_partial_write():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    conn.execute(
        """
        INSERT INTO filing_events (
            source_name, corp_code, rcept_no, corp_name, stock_code, report_nm, rcept_dt, flr_nm
        ) VALUES ('SEC EDGAR', '0000000001', 'acc-1', 'Test Co', 'TST', '8-K', '2026-01-01', 'Test Co')
        """
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        with connection.transaction(conn):
            # A valid insert followed by an insert that violates the
            # composite primary key — the whole transaction must roll
            # back, including the first, otherwise-valid statement.
            conn.execute(
                """
                INSERT INTO filing_events (
                    source_name, corp_code, rcept_no, corp_name, stock_code, report_nm, rcept_dt, flr_nm
                ) VALUES ('SEC EDGAR', '0000000002', 'acc-2', 'Another Co', 'ANO', '8-K', '2026-01-02', 'Another Co')
                """
            )
            conn.execute(
                """
                INSERT INTO filing_events (
                    source_name, corp_code, rcept_no, corp_name, stock_code, report_nm, rcept_dt, flr_nm
                ) VALUES ('SEC EDGAR', '0000000001', 'acc-1', 'Duplicate PK', 'DUP', '8-K', '2026-01-01', 'Dup')
                """
            )

    count = conn.execute("SELECT COUNT(*) AS n FROM filing_events WHERE corp_code = '0000000002'").fetchone()["n"]
    assert count == 0  # rolled back, not partially applied
