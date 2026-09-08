"""EevaResearch — Phase A1 (design/DECISIONS.md). Focused tests for
backend_factory.py's ThemeMatchingRepositoryProtocol seam
(get_theme_matching_repository) — the single, wholly internal
theme-matching persistence seam, with no public counterpart. Every
fixture is synthetic; Postgres tests use the shared, fail-soft
local-only fixtures from tests/_postgres_test_support.py."""
from __future__ import annotations

import hashlib

import pytest

from src.config.settings import Settings
from src.data_access import backend_factory, theme_store
from src.models.theme_matching import (
    MatchConfidence,
    MatchReviewStatus,
    ResearchCaseThemeMatch,
    ThemeMatchingScope,
    ThemeMatchReviewDecision,
)
from src.models.theme_research import EvidenceDirection, ResearchTheme, ThemeCategory, ThemeStatus, ThemeVisibility

from tests._postgres_test_support import pg_conn, pg_isolated_connection  # noqa: F401


def _theme(theme_id="theme-x", title="Test theme", created_at="2026-08-20T00:00:00+00:00", **overrides):
    defaults = dict(
        id=theme_id,
        category=ThemeCategory.BOTTLENECK, status=ThemeStatus.NEW, visibility=ThemeVisibility.INTERNAL,
        title=title, key_question="q", hypothesis="h", working_thesis="w", why_it_matters="y",
        what_could_change_the_view="c", what_to_watch_next="n", created_at=created_at, updated_at=created_at,
    )
    defaults.update(overrides)
    return ResearchTheme(**defaults)


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


def _sqlite_settings(tmp_path) -> Settings:
    return Settings(db_backend="sqlite", state_db_path=tmp_path / "state.db")


# ============================================================
# Factory backend selection
# ============================================================


def test_json_backend_default(tmp_path):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    repo = backend_factory.get_theme_matching_repository(settings)
    assert isinstance(repo, backend_factory.JsonThemeMatchingRepository)
    scope = _scope()
    assert repo.insert_scope(scope) is True
    assert repo.get_scope(scope.theme_id) == scope


def test_sqlite_backend(tmp_path):
    settings = _sqlite_settings(tmp_path)
    repo = backend_factory.get_theme_matching_repository(settings)
    assert isinstance(repo, backend_factory.SqliteThemeMatchingRepository)

    theme_curator = backend_factory.get_theme_curator_repository(settings)
    theme_curator.insert_theme(_theme())
    scope = _scope()
    assert repo.insert_scope(scope) is True
    assert repo.get_scope(scope.theme_id) == scope


def test_postgres_backend(pg_conn):
    repo = backend_factory.PostgresThemeMatchingRepository(conn=pg_conn)
    from src.data_access.postgres_state_db import theme_repository as postgres_themes

    postgres_themes.insert_theme(pg_conn, _theme(theme_id="pg-bf-theme"))
    scope = _scope(theme_id="pg-bf-theme")
    assert repo.insert_scope(scope) is True
    assert repo.get_scope(scope.theme_id) == scope


# ============================================================
# All eight required methods present on all three adapters
# ============================================================


def test_all_three_adapters_expose_the_same_eight_methods():
    expected = {
        "insert_scope", "get_scope", "list_active_scopes", "insert_match",
        "existing_match_ids_for_case_ids", "list_pending_matches",
        "insert_review_decision", "list_review_decisions_for_match",
    }
    for cls in (
        backend_factory.JsonThemeMatchingRepository,
        backend_factory.SqliteThemeMatchingRepository,
        backend_factory.PostgresThemeMatchingRepository,
    ):
        exported = {name for name in dir(cls) if not name.startswith("_")}
        assert expected <= exported


def test_no_forbidden_methods_beyond_the_nine_approved_ones():
    # Citrini-style Theme research workspace vertical slice
    # (design/DECISIONS.md) added get_match — a narrow, read-only
    # single-match lookup needed by scripts/promote_match_to_evidence.py
    # to safely read a specific match's stored content before promoting
    # it, rather than trusting a blindly recomputed id.
    approved = {
        "insert_scope", "get_scope", "list_active_scopes", "insert_match", "get_match",
        "existing_match_ids_for_case_ids", "list_pending_matches",
        "insert_review_decision", "list_review_decisions_for_match",
    }
    exported = {name for name in dir(backend_factory.ThemeMatchingRepositoryProtocol) if not name.startswith("_")}
    assert exported == approved


# ============================================================
# Empty bulk lookup — all three backends, zero I/O
# ============================================================


def test_json_existing_match_ids_for_case_ids_empty_input(tmp_path):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    repo = backend_factory.get_theme_matching_repository(settings)
    assert repo.existing_match_ids_for_case_ids([]) == frozenset()


def test_sqlite_existing_match_ids_for_case_ids_empty_input(tmp_path):
    settings = _sqlite_settings(tmp_path)
    repo = backend_factory.get_theme_matching_repository(settings)
    assert repo.existing_match_ids_for_case_ids([]) == frozenset()


def test_postgres_existing_match_ids_for_case_ids_empty_input(pg_conn):
    repo = backend_factory.PostgresThemeMatchingRepository(conn=pg_conn)
    assert repo.existing_match_ids_for_case_ids([]) == frozenset()


# ============================================================
# Public/private boundary
# ============================================================


def test_theme_matching_repository_never_referenced_by_public_theme_repository_protocol():
    public_exported = {name for name in dir(backend_factory.ThemeRepositoryProtocol) if not name.startswith("_")}
    matching_exported = {name for name in dir(backend_factory.ThemeMatchingRepositoryProtocol) if not name.startswith("_")}
    assert public_exported.isdisjoint(matching_exported)


def test_theme_curator_repository_protocol_unaffected_by_new_seam():
    # ThemeCuratorRepositoryProtocol (private Themes seam) predates
    # Phase A1 and must keep its own exact method set, unrelated to
    # theme-matching persistence.
    expected = {
        "insert_theme", "get_theme", "set_visibility",
        "insert_evidence_item", "insert_company_map_entry",
    }
    exported = {name for name in dir(backend_factory.ThemeCuratorRepositoryProtocol) if not name.startswith("_")}
    assert expected <= exported
