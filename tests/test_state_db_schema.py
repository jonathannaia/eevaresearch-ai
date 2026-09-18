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
    """Simulates a database that was already at v11 before the Daily
    News autonomous worker workstream — applies exactly migrations
    1..11, confirms it really is recorded at v11, then calls migrate()
    and confirms v12's own two new worker-status tables are present and
    usable along the way. Compares against schema.CURRENT_SCHEMA_VERSION
    dynamically (never a hardcoded 12) since migrate() naturally
    continues on to whatever later version now exists on top (e.g.
    Company Discovery's v13) — this test's own genuinely version-
    specific claim (the v11->v12 transition itself) stays meaningful
    regardless of how many later versions exist."""
    conn = connection.connect_in_memory()
    _migrate_up_to(conn, 11)
    assert schema.get_schema_version(conn) == 11

    result = schema.migrate(conn)

    assert result == schema.CURRENT_SCHEMA_VERSION
    assert schema.get_schema_version(conn) == schema.CURRENT_SCHEMA_VERSION
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    assert {"daily_news_scan_status", "daily_news_worker_status"} <= tables


def test_v13_database_upgrades_to_v14():
    """Simulates a database that was already at v13 before the Daily
    News worker observability workstream — applies exactly migrations
    1..13, confirms it really is recorded at v13, then calls migrate()
    and confirms v14's own three new daily_news_scan_status columns are
    present, usable, and every pre-existing row was backfilled to 0 (not
    NULL) by the ALTER TABLE ... DEFAULT 0 itself. Compares against
    schema.CURRENT_SCHEMA_VERSION dynamically, same discipline as
    test_v11_database_upgrades_to_v12 above."""
    conn = connection.connect_in_memory()
    _migrate_up_to(conn, 13)
    assert schema.get_schema_version(conn) == 13
    conn.execute(
        "INSERT INTO daily_news_scan_status (company_name, updated_at) VALUES ('NVIDIA', 'now')"
    )
    conn.commit()

    result = schema.migrate(conn)

    assert result == schema.CURRENT_SCHEMA_VERSION
    assert schema.get_schema_version(conn) == schema.CURRENT_SCHEMA_VERSION
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(daily_news_scan_status)").fetchall()}
    assert {
        "items_already_seen_last_run", "items_deduplicated_last_run", "items_suppressed_no_url_last_run",
    } <= columns
    row = conn.execute("SELECT * FROM daily_news_scan_status WHERE company_name = 'NVIDIA'").fetchone()
    assert row["items_already_seen_last_run"] == 0
    assert row["items_deduplicated_last_run"] == 0
    assert row["items_suppressed_no_url_last_run"] == 0


def test_v14_daily_news_scan_status_new_columns_accept_explicit_values():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    conn.execute(
        """
        INSERT INTO daily_news_scan_status (
            company_name, updated_at, items_already_seen_last_run,
            items_deduplicated_last_run, items_suppressed_no_url_last_run
        ) VALUES ('NVIDIA', 'now', 3, 2, 1)
        """
    )
    conn.commit()
    row = conn.execute("SELECT * FROM daily_news_scan_status WHERE company_name = 'NVIDIA'").fetchone()
    assert row["items_already_seen_last_run"] == 3
    assert row["items_deduplicated_last_run"] == 2
    assert row["items_suppressed_no_url_last_run"] == 1


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


# --- Daily News worker observability, Part A: schema version 16 ---


def test_v15_database_upgrades_to_v16():
    """Simulates a database that was already at v15 before the Daily
    News worker observability Part A workstream — applies exactly
    migrations 1..15, confirms it really is recorded at v15, then calls
    migrate() and confirms v16's own new column and new table are
    present and usable. Compares against schema.CURRENT_SCHEMA_VERSION
    dynamically, same discipline as the other version-upgrade tests
    above."""
    conn = connection.connect_in_memory()
    _migrate_up_to(conn, 15)
    assert schema.get_schema_version(conn) == 15
    conn.execute(
        "INSERT INTO daily_news_stories (id, company_name, headline, status, created_at, updated_at) "
        "VALUES ('s1', 'NVIDIA', 'Headline', 'Published', 'now', 'now')"
    )
    conn.execute(
        "INSERT INTO daily_news_sources (story_id, publisher, source_class, url, title, published_at, "
        "retrieved_at, original_language) VALUES ('s1', 'NVIDIA', 'Official company source', "
        "'https://example.com/pre-migration', 'T', 'now', 'now', 'English')"
    )
    conn.commit()

    result = schema.migrate(conn)

    assert result == schema.CURRENT_SCHEMA_VERSION
    assert schema.get_schema_version(conn) == schema.CURRENT_SCHEMA_VERSION
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    assert "daily_news_source_status" in tables
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(daily_news_sources)").fetchall()}
    assert "first_discovered_at" in columns
    # Backward compatibility: a pre-migration row gets NULL, never backfilled.
    row = conn.execute("SELECT first_discovered_at FROM daily_news_sources WHERE story_id = 's1'").fetchone()
    assert row["first_discovered_at"] is None


def test_v16_daily_news_source_status_starts_empty_and_is_usable():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    assert conn.execute("SELECT COUNT(*) AS n FROM daily_news_source_status").fetchone()["n"] == 0


def test_v16_daily_news_source_status_round_trips_and_upserts():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    conn.execute(
        "INSERT INTO daily_news_source_status (source_id, company_name, last_attempt_at, updated_at) "
        "VALUES ('meta-ir-rss', 'Meta Platforms, Inc.', 'now', 'now')"
    )
    conn.execute(
        "INSERT INTO daily_news_source_status (source_id, company_name, last_attempt_at, updated_at) "
        "VALUES ('meta-newsroom-rss', 'Meta Platforms, Inc.', 'now', 'now')"
    )
    conn.commit()
    # Two sources, one company — independent rows, proving the whole
    # point of this table (daily_news_scan_status would have collapsed
    # these into one company_name-keyed row).
    rows = conn.execute(
        "SELECT * FROM daily_news_source_status WHERE company_name = 'Meta Platforms, Inc.'"
    ).fetchall()
    assert {r["source_id"] for r in rows} == {"meta-ir-rss", "meta-newsroom-rss"}
    assert rows[0]["items_discovered_last_run"] == 0

    conn.execute(
        "INSERT INTO daily_news_source_status (source_id, company_name, last_attempt_at, updated_at) "
        "VALUES ('meta-ir-rss', 'Meta Platforms, Inc.', 'later', 'later') "
        "ON CONFLICT (source_id) DO UPDATE SET last_attempt_at = excluded.last_attempt_at, "
        "updated_at = excluded.updated_at"
    )
    conn.commit()
    row = conn.execute("SELECT * FROM daily_news_source_status WHERE source_id = 'meta-ir-rss'").fetchone()
    assert row["last_attempt_at"] == "later"
    assert conn.execute("SELECT COUNT(*) AS n FROM daily_news_source_status").fetchone()["n"] == 2


def test_v16_daily_news_source_status_accepts_http_status_and_duration():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    conn.execute(
        """
        INSERT INTO daily_news_source_status (
            source_id, company_name, updated_at, last_http_status, last_request_duration_ms
        ) VALUES ('nvent-electric-ir-rss', 'nVent Electric plc', 'now', 403, 187.5)
        """
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM daily_news_source_status WHERE source_id = 'nvent-electric-ir-rss'"
    ).fetchone()
    assert row["last_http_status"] == 403
    assert row["last_request_duration_ms"] == 187.5


def test_v16_daily_news_scan_status_unchanged_after_migration():
    """The legacy/aggregate company-keyed table must be completely
    unaffected by this migration — same columns, same behavior."""
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    columns_before = {row["name"] for row in conn.execute("PRAGMA table_info(daily_news_scan_status)").fetchall()}
    assert "source_id" not in columns_before
    assert "last_result_at" not in columns_before


def test_v16_database_upgrades_to_v17():
    """Simulates a database that was already at v16 before the open-beta
    feedback workstream — applies exactly migrations 1..16, confirms a
    pre-existing user_accounts row survives untouched, then calls
    migrate() and confirms v17's new table is present and usable."""
    conn = connection.connect_in_memory()
    _migrate_up_to(conn, 16)
    assert schema.get_schema_version(conn) == 16
    conn.execute(
        "INSERT INTO user_accounts (email, display_name, first_seen_at, last_seen_at, sign_in_count) "
        "VALUES ('founder@example.test', 'Ada', 'now', 'now', 1)"
    )
    conn.commit()

    result = schema.migrate(conn)

    assert result == schema.CURRENT_SCHEMA_VERSION
    assert schema.get_schema_version(conn) == schema.CURRENT_SCHEMA_VERSION
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    assert "feedback_submissions" in tables
    # Pre-existing row in an unrelated table is completely untouched.
    row = conn.execute("SELECT * FROM user_accounts WHERE email = 'founder@example.test'").fetchone()
    assert row["sign_in_count"] == 1
    assert row["display_name"] == "Ada"


def test_v17_feedback_submissions_starts_empty_and_is_usable():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    assert conn.execute("SELECT COUNT(*) AS n FROM feedback_submissions").fetchone()["n"] == 0


def test_v17_feedback_submissions_round_trips_and_upserts():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    conn.execute(
        """
        INSERT INTO feedback_submissions (
            email, display_name, submitted_at, role, tracking_workflow,
            tracking_workflow_other, primary_interest, weekly_value_feedback
        ) VALUES (
            'founder@example.test', 'Ada', '2026-01-01T00:00:00+00:00', 'Individual investor',
            'Other', 'A spreadsheet', 'Daily News', 'Nothing yet'
        )
        """
    )
    conn.commit()
    row = conn.execute("SELECT * FROM feedback_submissions WHERE email = 'founder@example.test'").fetchone()
    assert row["role"] == "Individual investor"
    assert row["tracking_workflow_other"] == "A spreadsheet"

    conn.execute(
        """
        INSERT INTO feedback_submissions (
            email, display_name, submitted_at, role, tracking_workflow,
            tracking_workflow_other, primary_interest, weekly_value_feedback
        ) VALUES ('founder@example.test', 'Ada', 'later', 'Journalist or media', 'News alerts', NULL, 'Daily News', NULL)
        ON CONFLICT (email) DO UPDATE SET
            submitted_at = excluded.submitted_at, role = excluded.role,
            tracking_workflow = excluded.tracking_workflow,
            tracking_workflow_other = excluded.tracking_workflow_other,
            weekly_value_feedback = excluded.weekly_value_feedback
        """
    )
    conn.commit()
    row = conn.execute("SELECT * FROM feedback_submissions WHERE email = 'founder@example.test'").fetchone()
    assert row["role"] == "Journalist or media"
    assert row["tracking_workflow_other"] is None
    assert conn.execute("SELECT COUNT(*) AS n FROM feedback_submissions").fetchone()["n"] == 1


def test_v17_user_accounts_unchanged_after_migration():
    """An unrelated pre-existing table must be completely unaffected by
    this migration — same columns, same behavior."""
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(user_accounts)").fetchall()}
    assert columns == {"email", "display_name", "first_seen_at", "last_seen_at", "sign_in_count"}


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


# --- Signals materiality classification (design/DECISIONS.md): static
# migration assertions, no database connection required. ---


def test_v19_is_registered_immediately_after_v18():
    versions = [v for v, _ in schema._MIGRATIONS]
    assert versions == sorted(versions)  # strictly ordered, no gaps/duplicates
    assert versions.index(19) == versions.index(18) + 1
    assert dict(schema._MIGRATIONS)[19] is schema._V19_STATEMENTS


def test_v19_statements_are_additive_only_two_nullable_columns_on_daily_news_stories():
    assert schema._V19_STATEMENTS == (
        "ALTER TABLE daily_news_stories ADD COLUMN materiality_tier TEXT",
        "ALTER TABLE daily_news_stories ADD COLUMN materiality_reasons TEXT",
    )
    for statement in schema._V19_STATEMENTS:
        assert statement.strip().upper().startswith("ALTER TABLE DAILY_NEWS_STORIES ADD COLUMN")
        assert "DROP" not in statement.upper()
        assert "NOT NULL" not in statement.upper()  # nullable — no default value forced onto existing rows


def test_v19_migration_is_idempotent_when_applied_twice_to_the_same_connection():
    conn = connection.connect_in_memory()
    first = schema.migrate(conn)
    second = schema.migrate(conn)
    assert first == second == schema.CURRENT_SCHEMA_VERSION
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(daily_news_stories)").fetchall()]
    assert columns.count("materiality_tier") == 1
    assert columns.count("materiality_reasons") == 1


# --- V20: Autonomous Research Agent — CandidateSignal.published_by ---------
# (design/AUTONOMOUS_EVIDENCE_FIRST_RESEARCH_AGENT_DESIGN_2026_09_17.md, §5.1)

def test_v20_is_registered_immediately_after_v19_and_is_current():
    assert schema.CURRENT_SCHEMA_VERSION == 20
    versions = [v for v, _ in schema._MIGRATIONS]
    assert versions == sorted(versions)  # strictly ordered, no gaps/duplicates
    assert versions[-2:] == [19, 20]
    assert dict(schema._MIGRATIONS)[20] is schema._V20_STATEMENTS


def test_v20_statements_are_additive_only_one_provenance_column_on_candidates():
    assert schema._V20_STATEMENTS == (
        "ALTER TABLE candidates ADD COLUMN published_by TEXT NOT NULL DEFAULT 'human_reviewer'",
    )
    for statement in schema._V20_STATEMENTS:
        assert statement.strip().upper().startswith("ALTER TABLE CANDIDATES ADD COLUMN")


def test_v20_migration_is_idempotent_when_applied_twice_to_the_same_connection():
    conn = connection.connect_in_memory()
    first = schema.migrate(conn)
    second = schema.migrate(conn)
    assert first == second == 20
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(candidates)").fetchall()]
    assert columns.count("published_by") == 1


def test_v20_published_by_is_not_null_with_the_dataclass_default_so_old_rows_read_back_human_reviewer():
    """Pre-existing rows must read back exactly what CandidateSignal.
    published_by would have defaulted to in code — the column carries the
    default at the schema level (NOT NULL DEFAULT 'human_reviewer'), so
    no backfill and no application-side coalescing is ever needed."""
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    info = {row["name"]: row for row in conn.execute("PRAGMA table_info(candidates)").fetchall()}
    published_by = info["published_by"]
    assert published_by["notnull"] == 1
    assert published_by["dflt_value"] == "'human_reviewer'"


def test_v19_columns_are_nullable_and_untouched_existing_rows_read_back_as_null():
    """A row inserted with the pre-V19 column set only (materiality_tier/
    materiality_reasons never mentioned) must not error and must read
    back NULL for both new columns — the migration never forces a
    default value onto rows that predate it."""
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    conn.execute(
        "INSERT INTO daily_news_stories (id, company_name, headline, status, created_at, updated_at) "
        "VALUES ('s-static-check', 'NVIDIA', 'H', 'Published', 'now', 'now')"
    )
    conn.commit()
    row = conn.execute(
        "SELECT materiality_tier, materiality_reasons FROM daily_news_stories WHERE id = 's-static-check'"
    ).fetchone()
    assert row["materiality_tier"] is None
    assert row["materiality_reasons"] is None
