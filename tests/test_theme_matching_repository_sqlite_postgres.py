"""EevaResearch — Phase A1 (design/DECISIONS.md). SQLite and Postgres
repository tests for the internal theme-matching model family. Every
fixture is synthetic and locally constructed; Postgres tests use the
shared, fail-soft local-only fixtures from tests/_postgres_test_support.py
and skip cleanly when no local disposable Postgres instance is
available."""
from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

import pytest

from src.data_access import theme_store
from src.data_access.research_store import build_case_id
from src.data_access.state_db import connection as sqlite_connection
from src.data_access.state_db import research_repository as sqlite_research_repository
from src.data_access.state_db import schema as sqlite_schema
from src.data_access.state_db import theme_matching_repository as sqlite_matching
from src.data_access.state_db import theme_repository as sqlite_themes
from src.models.research_case import ResearchCase, ResearchCaseStatus
from src.models.theme_matching import (
    MatchConfidence,
    MatchReviewStatus,
    ResearchCaseThemeMatch,
    ThemeMatchingScope,
    ThemeMatchReviewDecision,
)
from src.models.theme_research import (
    EvidenceDirection,
    ResearchTheme,
    ThemeCategory,
    ThemeStatus,
    ThemeVisibility,
)

from tests._postgres_test_support import pg_conn, pg_isolated_connection  # noqa: F401

try:
    from src.data_access.postgres_state_db import theme_matching_repository as postgres_matching
except ImportError:  # pragma: no cover - psycopg always installed in this repo
    postgres_matching = None


def _theme(theme_id="theme-x", title="Test theme", created_at="2026-08-20T00:00:00+00:00", **overrides):
    defaults = dict(
        id=theme_id,
        category=ThemeCategory.BOTTLENECK, status=ThemeStatus.NEW, visibility=ThemeVisibility.INTERNAL,
        title=title, key_question="q", hypothesis="h", working_thesis="w", why_it_matters="y",
        what_could_change_the_view="c", what_to_watch_next="n", created_at=created_at, updated_at=created_at,
    )
    defaults.update(overrides)
    return ResearchTheme(**defaults)


def _case(case_id="case-x", created_at="2026-08-20T00:00:00+00:00", **overrides):
    defaults = dict(
        id=case_id,
        trigger_source_type="radar", trigger_source_id="cand-1", trigger_source_name="Example Corp",
        trigger_summary="Filed a material event.", title="Example research case",
        research_question="What is the supply-chain exposure?", status=ResearchCaseStatus.OPEN,
        created_at=created_at, version=1,
    )
    defaults.update(overrides)
    return ResearchCase(**defaults)


def _scope(theme_id="theme-x", **overrides):
    defaults = dict(
        theme_id=theme_id,
        sector_tags=("semis",), sector_subtags=(), allowed_matched_rule_categories=("keyword",),
        required_keywords=(), excluded_keywords=(),
    )
    defaults.update(overrides)
    return ThemeMatchingScope(**defaults)


def _build_match_id(case_id: str, theme_id: str) -> str:
    digest = hashlib.sha256(f"{case_id}|{theme_id}".encode("utf-8")).hexdigest()
    return f"theme-match-{digest[:24]}"


def _match(case_id="case-x", theme_id="theme-x", created_at="2026-08-20T00:00:00+00:00", **overrides):
    defaults = dict(
        id=_build_match_id(case_id, theme_id),
        case_id=case_id, theme_id=theme_id, confidence=MatchConfidence.MEDIUM,
        direction=EvidenceDirection.CONTEXT, matched_sector_tag="semis",
        matched_rule_categories=("keyword",), matched_keywords=("foundry",),
        rationale="r", created_at=created_at,
    )
    defaults.update(overrides)
    return ResearchCaseThemeMatch(**defaults)


def _decision(match_id="theme-match-x", reviewed_at="2026-08-21T00:00:00+00:00", **overrides):
    digest = hashlib.sha256(f"{match_id}|{reviewed_at}".encode("utf-8")).hexdigest()
    defaults = dict(
        id=f"theme-match-review-{digest[:24]}",
        match_id=match_id, decision=MatchReviewStatus.ACCEPTED, reviewer_note=None, reviewed_at=reviewed_at,
    )
    defaults.update(overrides)
    return ThemeMatchReviewDecision(**defaults)


def _sqlite_conn():
    conn = sqlite_connection.connect_in_memory()
    sqlite_schema.migrate(conn)
    return conn


# ============================================================
# SQLite
# ============================================================


def test_sqlite_scope_round_trip_and_duplicate_rejection():
    conn = _sqlite_conn()
    sqlite_themes.insert_theme(conn, _theme())
    scope = _scope()
    assert sqlite_matching.insert_scope(conn, scope) is True
    assert sqlite_matching.get_scope(conn, scope.theme_id) == scope
    tampered = ThemeMatchingScope(**{**scope.__dict__, "sector_tags": ("TAMPERED",)})
    assert sqlite_matching.insert_scope(conn, tampered) is False
    assert sqlite_matching.get_scope(conn, scope.theme_id) == scope


def test_sqlite_insert_scope_without_parent_theme_fails_and_leaves_connection_usable():
    conn = _sqlite_conn()
    scope = _scope(theme_id="theme-orphan")
    assert sqlite_matching.insert_scope(conn, scope) is False
    assert sqlite_matching.get_scope(conn, scope.theme_id) is None
    # Connection remains usable after the FK-rejected insert.
    sqlite_themes.insert_theme(conn, _theme())
    assert sqlite_matching.insert_scope(conn, _scope()) is True


def test_sqlite_list_active_scopes_excludes_archived():
    conn = _sqlite_conn()
    sqlite_themes.insert_theme(conn, _theme(theme_id="theme-internal"))
    sqlite_themes.insert_theme(conn, _theme(theme_id="theme-archived", title="Archived", visibility=ThemeVisibility.ARCHIVED))
    sqlite_matching.insert_scope(conn, _scope(theme_id="theme-internal"))
    sqlite_matching.insert_scope(conn, _scope(theme_id="theme-archived"))

    result = sqlite_matching.list_active_scopes(conn)
    assert [s.theme_id for s in result] == ["theme-internal"]


def test_sqlite_match_round_trip_and_duplicate_rejection():
    conn = _sqlite_conn()
    sqlite_themes.insert_theme(conn, _theme())
    sqlite_research_repository.insert_research_case(conn, _case())
    match = _match()
    assert sqlite_matching.insert_match(conn, match) is True
    tampered = ResearchCaseThemeMatch(**{**match.__dict__, "rationale": "TAMPERED"})
    assert sqlite_matching.insert_match(conn, tampered) is False


def test_sqlite_insert_match_without_parent_case_fails_and_leaves_connection_usable():
    conn = _sqlite_conn()
    sqlite_themes.insert_theme(conn, _theme())
    match = _match(case_id="case-missing")
    assert sqlite_matching.insert_match(conn, match) is False
    # Connection remains usable after the FK-rejected insert.
    sqlite_research_repository.insert_research_case(conn, _case())
    assert sqlite_matching.insert_match(conn, _match()) is True


def test_sqlite_existing_match_ids_for_case_ids_empty_input_executes_no_sql():
    conn = MagicMock()
    result = sqlite_matching.existing_match_ids_for_case_ids(conn, [])
    assert result == frozenset()
    conn.execute.assert_not_called()


def test_sqlite_existing_match_ids_for_case_ids_returns_subset():
    conn = _sqlite_conn()
    sqlite_themes.insert_theme(conn, _theme())
    for case_id in ("case-a", "case-b", "case-c"):
        sqlite_research_repository.insert_research_case(conn, _case(case_id=case_id))
    matches = {case_id: _match(case_id=case_id) for case_id in ("case-a", "case-b", "case-c")}
    for m in matches.values():
        sqlite_matching.insert_match(conn, m)

    result = sqlite_matching.existing_match_ids_for_case_ids(conn, ["case-a", "case-c", "case-missing"])
    assert result == frozenset({matches["case-a"].id, matches["case-c"].id})


def test_sqlite_list_pending_matches_derived_from_absence_of_decision():
    conn = _sqlite_conn()
    sqlite_themes.insert_theme(conn, _theme())
    sqlite_research_repository.insert_research_case(conn, _case(case_id="case-decided"))
    sqlite_research_repository.insert_research_case(conn, _case(case_id="case-pending"))
    decided = _match(case_id="case-decided", created_at="2026-08-20T00:00:00+00:00")
    pending = _match(case_id="case-pending", created_at="2026-08-21T00:00:00+00:00")
    sqlite_matching.insert_match(conn, decided)
    sqlite_matching.insert_match(conn, pending)
    sqlite_matching.insert_review_decision(conn, _decision(match_id=decided.id))

    result = sqlite_matching.list_pending_matches(conn)
    assert [m.id for m in result] == [pending.id]


def test_sqlite_review_decision_round_trip_and_immutable_history():
    conn = _sqlite_conn()
    sqlite_themes.insert_theme(conn, _theme())
    sqlite_research_repository.insert_research_case(conn, _case())
    match = _match()
    sqlite_matching.insert_match(conn, match)
    first = _decision(match_id=match.id, reviewed_at="2026-08-20T00:00:00+00:00", decision=MatchReviewStatus.PENDING_REVIEW)
    second = _decision(match_id=match.id, reviewed_at="2026-08-21T00:00:00+00:00", decision=MatchReviewStatus.ACCEPTED)
    assert sqlite_matching.insert_review_decision(conn, first) is True
    assert sqlite_matching.insert_review_decision(conn, second) is True

    result = sqlite_matching.list_review_decisions_for_match(conn, match.id)
    assert [d.id for d in result] == [first.id, second.id]


def test_sqlite_migration_reaches_version_8_with_new_tables_starting_empty():
    conn = _sqlite_conn()
    # Tracks the current latest schema version (9, after the Citrini-
    # style research-workspace vertical slice's V9 addition) — the
    # point of this test is that the V8 matching tables exist and start
    # empty, not this exact number.
    assert sqlite_schema.get_schema_version(conn) == 9
    assert sqlite_schema.CURRENT_SCHEMA_VERSION == 9
    assert sqlite_matching.get_scope(conn, "theme-does-not-exist") is None
    assert sqlite_matching.list_active_scopes(conn) == ()
    assert sqlite_matching.list_pending_matches(conn) == ()


def test_sqlite_migration_v7_to_v8_preserves_existing_theme_data():
    conn = sqlite_connection.connect_in_memory()
    # Migrate to V7 first, insert data, then migrate again to the
    # current version and confirm the pre-existing row survives
    # untouched.
    original_migrations = sqlite_schema._MIGRATIONS
    v7_only = tuple(m for m in original_migrations if m[0] <= 7)
    sqlite_schema._MIGRATIONS = v7_only
    try:
        sqlite_schema.migrate(conn)
        assert sqlite_schema.get_schema_version(conn) == 7
        sqlite_themes.insert_theme(conn, _theme())
    finally:
        sqlite_schema._MIGRATIONS = original_migrations

    version = sqlite_schema.migrate(conn)
    assert version == 9
    assert sqlite_themes.get_theme(conn, "theme-x") == _theme()
    assert sqlite_matching.insert_scope(conn, _scope()) is True


# ============================================================
# Postgres
# ============================================================


def test_postgres_scope_round_trip_and_duplicate_rejection(pg_conn):
    from src.data_access.postgres_state_db import theme_repository as postgres_themes

    postgres_themes.insert_theme(pg_conn, _theme(theme_id="pg-theme-1", title="PG scope"))
    scope = _scope(theme_id="pg-theme-1")
    assert postgres_matching.insert_scope(pg_conn, scope) is True
    assert postgres_matching.get_scope(pg_conn, scope.theme_id) == scope
    tampered = ThemeMatchingScope(**{**scope.__dict__, "sector_tags": ("TAMPERED",)})
    assert postgres_matching.insert_scope(pg_conn, tampered) is False
    # Connection remains usable after the rejected duplicate.
    postgres_themes.insert_theme(pg_conn, _theme(theme_id="pg-theme-2", title="PG scope 2"))
    assert postgres_matching.insert_scope(pg_conn, _scope(theme_id="pg-theme-2")) is True


def test_postgres_list_active_scopes_excludes_archived(pg_conn):
    from src.data_access.postgres_state_db import theme_repository as postgres_themes

    postgres_themes.insert_theme(pg_conn, _theme(theme_id="pg-internal", title="PG internal"))
    postgres_themes.insert_theme(pg_conn, _theme(theme_id="pg-archived", title="PG archived", visibility=ThemeVisibility.ARCHIVED))
    postgres_matching.insert_scope(pg_conn, _scope(theme_id="pg-internal"))
    postgres_matching.insert_scope(pg_conn, _scope(theme_id="pg-archived"))

    result = postgres_matching.list_active_scopes(pg_conn)
    assert [s.theme_id for s in result] == ["pg-internal"]


def test_postgres_match_round_trip_and_duplicate_rejection(pg_conn):
    from src.data_access.postgres_state_db import research_repository as postgres_research
    from src.data_access.postgres_state_db import theme_repository as postgres_themes

    postgres_themes.insert_theme(pg_conn, _theme(theme_id="pg-theme-m", title="PG match theme"))
    postgres_research.insert_research_case(pg_conn, _case(case_id="pg-case-m"))
    match = _match(case_id="pg-case-m", theme_id="pg-theme-m")
    assert postgres_matching.insert_match(pg_conn, match) is True
    tampered = ResearchCaseThemeMatch(**{**match.__dict__, "rationale": "TAMPERED"})
    assert postgres_matching.insert_match(pg_conn, tampered) is False


def test_postgres_list_pending_matches_derived_from_absence_of_decision(pg_conn):
    from src.data_access.postgres_state_db import research_repository as postgres_research
    from src.data_access.postgres_state_db import theme_repository as postgres_themes

    postgres_themes.insert_theme(pg_conn, _theme(theme_id="pg-theme-p", title="PG pending theme"))
    postgres_research.insert_research_case(pg_conn, _case(case_id="pg-case-decided"))
    postgres_research.insert_research_case(pg_conn, _case(case_id="pg-case-pending"))
    decided = _match(case_id="pg-case-decided", theme_id="pg-theme-p", created_at="2026-08-20T00:00:00+00:00")
    pending = _match(case_id="pg-case-pending", theme_id="pg-theme-p", created_at="2026-08-21T00:00:00+00:00")
    postgres_matching.insert_match(pg_conn, decided)
    postgres_matching.insert_match(pg_conn, pending)
    postgres_matching.insert_review_decision(pg_conn, _decision(match_id=decided.id))

    result = postgres_matching.list_pending_matches(pg_conn)
    assert [m.id for m in result] == [pending.id]


def test_postgres_review_decision_round_trip(pg_conn):
    from src.data_access.postgres_state_db import research_repository as postgres_research
    from src.data_access.postgres_state_db import theme_repository as postgres_themes

    postgres_themes.insert_theme(pg_conn, _theme(theme_id="pg-theme-d", title="PG decision theme"))
    postgres_research.insert_research_case(pg_conn, _case(case_id="pg-case-d"))
    match = _match(case_id="pg-case-d", theme_id="pg-theme-d")
    postgres_matching.insert_match(pg_conn, match)
    decision = _decision(match_id=match.id)
    assert postgres_matching.insert_review_decision(pg_conn, decision) is True

    result = postgres_matching.list_review_decisions_for_match(pg_conn, match.id)
    assert result == (decision,)


def test_postgres_bulk_empty_input_executes_no_query():
    if postgres_matching is None:
        pytest.skip("psycopg not available")
    conn = MagicMock()
    assert postgres_matching.existing_match_ids_for_case_ids(conn, []) == frozenset()
    conn.execute.assert_not_called()


def test_postgres_bulk_query_construction_uses_any():
    if postgres_matching is None:
        pytest.skip("psycopg not available")
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = []
    postgres_matching.existing_match_ids_for_case_ids(conn, ["case-a", "case-b"])
    assert conn.execute.call_count == 1
    sql_text, params = conn.execute.call_args[0]
    assert "= ANY(%s)" in sql_text
    assert params == (["case-a", "case-b"],)


def test_postgres_migration_reaches_version_8_with_new_tables(pg_isolated_connection):
    from src.data_access.postgres_state_db import schema as postgres_schema

    conn = pg_isolated_connection
    version = postgres_schema.migrate(conn)
    assert version == 9
    assert postgres_schema.CURRENT_SCHEMA_VERSION == 9
    assert postgres_matching.get_scope(conn, "theme-does-not-exist") is None
    assert postgres_matching.list_active_scopes(conn) == ()
    assert postgres_matching.list_pending_matches(conn) == ()
