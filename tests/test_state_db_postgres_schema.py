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
    """Compares against postgres_schema.CURRENT_SCHEMA_VERSION
    dynamically (never a hardcoded 12) — see state_db/test_state_db_
    schema.py's own test_v11_database_upgrades_to_v12 for why."""
    conn = pg_isolated_connection
    _migrate_up_to(conn, 11)
    assert postgres_schema.get_schema_version(conn) == 11

    result = postgres_schema.migrate(conn)

    assert result == postgres_schema.CURRENT_SCHEMA_VERSION
    assert postgres_schema.get_schema_version(conn) == postgres_schema.CURRENT_SCHEMA_VERSION
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


def test_v13_schema_upgrades_to_v14(pg_isolated_connection):
    """Simulates a schema that was already at v13 before the Daily News
    worker observability workstream — applies exactly migrations 1..13,
    confirms it really is recorded at v13, then calls migrate() and
    confirms v14's own three new daily_news_scan_status columns are
    present, NOT NULL, default 0, and every pre-existing row was
    backfilled to 0 by the ALTER TABLE itself. Compares against
    postgres_schema.CURRENT_SCHEMA_VERSION dynamically, same discipline
    as test_v11_schema_upgrades_to_v12 above."""
    conn = pg_isolated_connection
    _migrate_up_to(conn, 13)
    assert postgres_schema.get_schema_version(conn) == 13
    conn.execute("INSERT INTO daily_news_scan_status (company_name, updated_at) VALUES ('NVIDIA', 'now')")
    conn.commit()

    result = postgres_schema.migrate(conn)

    assert result == postgres_schema.CURRENT_SCHEMA_VERSION
    assert postgres_schema.get_schema_version(conn) == postgres_schema.CURRENT_SCHEMA_VERSION
    rows = conn.execute(
        "SELECT column_name, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'daily_news_scan_status'"
    ).fetchall()
    by_name = {row["column_name"]: row for row in rows}
    for column in ("items_already_seen_last_run", "items_deduplicated_last_run", "items_suppressed_no_url_last_run"):
        assert column in by_name
        assert by_name[column]["is_nullable"] == "NO"
        assert by_name[column]["column_default"] == "0"
    row = conn.execute("SELECT * FROM daily_news_scan_status WHERE company_name = 'NVIDIA'").fetchone()
    assert row["items_already_seen_last_run"] == 0
    assert row["items_deduplicated_last_run"] == 0
    assert row["items_suppressed_no_url_last_run"] == 0


def test_v16_schema_upgrades_to_v17(pg_isolated_connection):
    """Simulates a schema that was already at v16 before the Daily News
    worker observability Part A workstream — applies exactly migrations
    1..16, confirms it really is recorded at v16, then calls migrate()
    and confirms v17's own new column and new table are present and
    usable. Compares against postgres_schema.CURRENT_SCHEMA_VERSION
    dynamically, same discipline as the other version-upgrade tests
    above."""
    conn = pg_isolated_connection
    _migrate_up_to(conn, 16)
    assert postgres_schema.get_schema_version(conn) == 16
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

    result = postgres_schema.migrate(conn)

    assert result == postgres_schema.CURRENT_SCHEMA_VERSION
    assert postgres_schema.get_schema_version(conn) == postgres_schema.CURRENT_SCHEMA_VERSION
    tables = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
    ).fetchall()
    assert "daily_news_source_status" in {row["table_name"] for row in tables}
    columns = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'daily_news_sources'"
    ).fetchall()
    assert "first_discovered_at" in {row["column_name"] for row in columns}
    row = conn.execute("SELECT first_discovered_at FROM daily_news_sources WHERE story_id = 's1'").fetchone()
    assert row["first_discovered_at"] is None


def test_v17_daily_news_source_status_round_trips_and_upserts(pg_isolated_connection):
    conn = pg_isolated_connection
    postgres_schema.migrate(conn)
    conn.execute(
        "INSERT INTO daily_news_source_status (source_id, company_name, last_attempt_at, updated_at) "
        "VALUES ('meta-ir-rss', 'Meta Platforms, Inc.', 'now', 'now')"
    )
    conn.execute(
        "INSERT INTO daily_news_source_status (source_id, company_name, last_attempt_at, updated_at) "
        "VALUES ('meta-newsroom-rss', 'Meta Platforms, Inc.', 'now', 'now')"
    )
    conn.commit()
    rows = conn.execute(
        "SELECT * FROM daily_news_source_status WHERE company_name = 'Meta Platforms, Inc.'"
    ).fetchall()
    assert {r["source_id"] for r in rows} == {"meta-ir-rss", "meta-newsroom-rss"}

    conn.execute(
        "INSERT INTO daily_news_source_status (source_id, company_name, last_attempt_at, updated_at) "
        "VALUES ('meta-ir-rss', 'Meta Platforms, Inc.', 'later', 'later') "
        "ON CONFLICT (source_id) DO UPDATE SET last_attempt_at = excluded.last_attempt_at, "
        "updated_at = excluded.updated_at"
    )
    conn.commit()
    row = conn.execute("SELECT * FROM daily_news_source_status WHERE source_id = 'meta-ir-rss'").fetchone()
    assert row["last_attempt_at"] == "later"


def test_v17_daily_news_source_status_accepts_http_status_and_duration(pg_isolated_connection):
    conn = pg_isolated_connection
    postgres_schema.migrate(conn)
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


def test_v17_schema_upgrades_to_v18(pg_isolated_connection):
    """Simulates a schema that was already at v17 before the open-beta
    feedback workstream — applies exactly migrations 1..17, confirms a
    pre-existing user_accounts row survives untouched, then calls
    migrate() and confirms v18's new table is present and usable."""
    conn = pg_isolated_connection
    _migrate_up_to(conn, 17)
    assert postgres_schema.get_schema_version(conn) == 17
    conn.execute(
        "INSERT INTO user_accounts (email, display_name, first_seen_at, last_seen_at, sign_in_count) "
        "VALUES ('founder@example.test', 'Ada', 'now', 'now', 1)"
    )
    conn.commit()

    result = postgres_schema.migrate(conn)

    assert result == postgres_schema.CURRENT_SCHEMA_VERSION
    assert postgres_schema.get_schema_version(conn) == postgres_schema.CURRENT_SCHEMA_VERSION
    tables = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
    ).fetchall()
    assert "feedback_submissions" in {row["table_name"] for row in tables}
    row = conn.execute("SELECT * FROM user_accounts WHERE email = 'founder@example.test'").fetchone()
    assert row["sign_in_count"] == 1
    assert row["display_name"] == "Ada"


def test_v18_feedback_submissions_round_trips_and_upserts(pg_isolated_connection):
    conn = pg_isolated_connection
    postgres_schema.migrate(conn)
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
    count = conn.execute("SELECT COUNT(*) AS n FROM feedback_submissions").fetchone()
    assert count["n"] == 1


def test_migration_leaves_no_open_transaction_between_steps(pg_isolated_connection):
    """A no-hidden-state proof mirroring the SQLite suite's own
    discipline: after migrate() returns, ordinary reads on the same
    connection work without the caller needing to commit/rollback
    first — every step already committed cleanly inside migrate()
    itself."""
    postgres_schema.migrate(pg_isolated_connection)
    row = pg_isolated_connection.execute("SELECT COUNT(*) AS n FROM candidates").fetchone()
    assert row["n"] == 0


# --- Signals materiality classification (design/DECISIONS.md): static
# migration assertions, no database connection required. ---


def test_v20_is_registered_immediately_after_v19():
    # Was pinned to "== 20 and is current" until V21 landed without
    # updating it (a pre-existing failure at baseline); now an adjacency-
    # only check, with the "is current" pin moved to the V22 test below.
    versions = [v for v, _ in postgres_schema._MIGRATIONS]
    assert versions == sorted(versions)  # strictly ordered, no gaps/duplicates
    assert versions.index(20) == versions.index(19) + 1
    assert dict(postgres_schema._MIGRATIONS)[20] is postgres_schema._V20_STATEMENTS


# --- V22: Autonomous Research Agent — CandidateSignal.published_by ---------
# (design/AUTONOMOUS_EVIDENCE_FIRST_RESEARCH_AGENT_DESIGN_2026_09_17.md, §5.1)
# Mirrors the SQLite backend's V20 exactly; the two backends version
# independently (Postgres carries the editorial_stories-only V20/V21).

def test_v22_is_registered_immediately_after_v21_and_is_current():
    assert postgres_schema.CURRENT_SCHEMA_VERSION == 22
    versions = [v for v, _ in postgres_schema._MIGRATIONS]
    assert versions == sorted(versions)  # strictly ordered, no gaps/duplicates
    assert versions[-2:] == [21, 22]
    assert dict(postgres_schema._MIGRATIONS)[22] is postgres_schema._V22_STATEMENTS


def test_v22_statements_are_additive_only_one_provenance_column_on_candidates():
    assert postgres_schema._V22_STATEMENTS == (
        "ALTER TABLE candidates ADD COLUMN published_by TEXT",
    )
    for statement in postgres_schema._V22_STATEMENTS:
        assert statement.strip().upper().startswith("ALTER TABLE CANDIDATES ADD COLUMN")


def test_v22_published_by_is_nullable_with_no_default(pg_isolated_connection):
    postgres_schema.migrate(pg_isolated_connection)
    rows = pg_isolated_connection.execute(
        "SELECT is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'candidates' "
        "AND column_name = 'published_by'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["is_nullable"] == "YES"
    assert rows[0]["column_default"] is None


def test_v22_upgrade_leaves_pre_existing_published_candidate_null(pg_isolated_connection):
    """A PUBLISHED candidate that predates V22 must read back NULL after
    the upgrade — never backfilled as human-reviewed."""
    conn = pg_isolated_connection
    _migrate_up_to(conn, 21)
    conn.execute(
        "INSERT INTO filing_events (corp_code, rcept_no, corp_name, stock_code, report_nm, rcept_dt, flr_nm) "
        "VALUES ('00126380', 'r-pre-v22', 'Samsung', '005930', 'R', '20260101', 'Samsung')"
    )
    conn.execute(
        "INSERT INTO candidates (id, source, filing_corp_code, filing_rcept_no, confidence, status, "
        "extraction_state, translation_state, excerpt_quality, created_at, updated_at) "
        "VALUES ('cand-pre-v22', 'OpenDART / DART', '00126380', 'r-pre-v22', 'High', 'Published', "
        "'Not fetched', 'Not requested', 'Unknown', 'now', 'now')"
    )
    conn.commit()
    assert postgres_schema.migrate(conn) == 22
    row = conn.execute("SELECT published_by FROM candidates WHERE id = 'cand-pre-v22'").fetchone()
    assert row["published_by"] is None


def test_v20_statements_are_additive_only_four_nullable_columns():
    assert postgres_schema._V20_STATEMENTS == (
        "ALTER TABLE daily_news_stories ADD COLUMN materiality_tier TEXT",
        "ALTER TABLE daily_news_stories ADD COLUMN materiality_reasons TEXT",
        "ALTER TABLE editorial_stories ADD COLUMN materiality_tier TEXT",
        "ALTER TABLE editorial_stories ADD COLUMN materiality_reasons TEXT",
    )
    for statement in postgres_schema._V20_STATEMENTS:
        assert statement.strip().upper().startswith("ALTER TABLE")
        assert " ADD COLUMN " in statement.upper()
        assert "DROP" not in statement.upper()
        assert "NOT NULL" not in statement.upper()  # nullable — no default value forced onto existing rows
        assert statement.split()[2] in ("daily_news_stories", "editorial_stories")
