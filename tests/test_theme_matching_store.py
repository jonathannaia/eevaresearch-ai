"""EevaResearch — Phase A1 (design/DECISIONS.md). JSON-backend tests
for src.data_access.theme_matching_store. Every fixture is synthetic
and locally constructed; no real evidence, network call, worker run,
or UI code involved anywhere in this file."""
from __future__ import annotations

import hashlib

from src.data_access import theme_matching_store, theme_store
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


def _decision(match_id="theme-match-x", reviewed_at="2026-08-21T00:00:00+00:00", **overrides):
    defaults = dict(
        id=theme_matching_store.build_review_decision_id(match_id, reviewed_at),
        match_id=match_id, decision=MatchReviewStatus.ACCEPTED, reviewer_note=None, reviewed_at=reviewed_at,
    )
    defaults.update(overrides)
    return ThemeMatchReviewDecision(**defaults)


# ============================================================
# Scopes
# ============================================================


def test_insert_and_get_scope_round_trip(tmp_path):
    scope = _scope()
    assert theme_matching_store.insert_scope(tmp_path, scope) is True
    assert theme_matching_store.get_scope(tmp_path, scope.theme_id) == scope


def test_duplicate_scope_insertion_for_same_theme_id_is_rejected(tmp_path):
    scope = _scope()
    tampered = ThemeMatchingScope(**{**scope.__dict__, "sector_tags": ("TAMPERED",)})
    assert theme_matching_store.insert_scope(tmp_path, scope) is True
    assert theme_matching_store.insert_scope(tmp_path, tampered) is False
    assert theme_matching_store.get_scope(tmp_path, scope.theme_id) == scope


def test_get_scope_missing_returns_none(tmp_path):
    assert theme_matching_store.get_scope(tmp_path, "theme-missing") is None


def test_list_active_scopes_excludes_archived_and_includes_internal_ready_published(tmp_path):
    theme_store.append_theme(tmp_path, _theme(theme_id="theme-internal", visibility=ThemeVisibility.INTERNAL))
    theme_store.append_theme(tmp_path, _theme(theme_id="theme-ready", title="Ready", visibility=ThemeVisibility.READY_TO_PUBLISH))
    theme_store.append_theme(tmp_path, _theme(theme_id="theme-published", title="Published", visibility=ThemeVisibility.PUBLISHED))
    theme_store.append_theme(tmp_path, _theme(theme_id="theme-archived", title="Archived", visibility=ThemeVisibility.ARCHIVED))
    for theme_id in ("theme-internal", "theme-ready", "theme-published", "theme-archived"):
        theme_matching_store.insert_scope(tmp_path, _scope(theme_id=theme_id))

    result = theme_matching_store.list_active_scopes(tmp_path)
    assert {s.theme_id for s in result} == {"theme-internal", "theme-ready", "theme-published"}


def test_list_active_scopes_excludes_scope_with_no_parent_theme(tmp_path):
    theme_matching_store.insert_scope(tmp_path, _scope(theme_id="theme-orphan"))
    assert theme_matching_store.list_active_scopes(tmp_path) == ()


def test_list_active_scopes_empty_when_no_scopes(tmp_path):
    assert theme_matching_store.list_active_scopes(tmp_path) == ()


# ============================================================
# Matches
# ============================================================


def test_insert_match_and_duplicate_rejection_by_id(tmp_path):
    match = _match()
    tampered = ResearchCaseThemeMatch(**{**match.__dict__, "rationale": "TAMPERED"})
    assert theme_matching_store.insert_match(tmp_path, match) is True
    assert theme_matching_store.insert_match(tmp_path, tampered) is False


def test_duplicate_case_theme_pair_is_rejected_because_id_is_derived_from_it(tmp_path):
    match = _match(case_id="case-1", theme_id="theme-1")
    same_pair_different_content = _match(case_id="case-1", theme_id="theme-1", rationale="different rationale")
    assert match.id == same_pair_different_content.id
    assert theme_matching_store.insert_match(tmp_path, match) is True
    assert theme_matching_store.insert_match(tmp_path, same_pair_different_content) is False


def test_existing_match_ids_for_case_ids_empty_input_returns_empty_frozenset_with_zero_io(tmp_path, monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("must not touch disk when case_ids is empty")

    monkeypatch.setattr(theme_matching_store, "load_matches", _boom)
    result = theme_matching_store.existing_match_ids_for_case_ids(tmp_path, [])
    assert result == frozenset()


def test_existing_match_ids_for_case_ids_returns_matching_subset(tmp_path):
    a = _match(case_id="case-a", theme_id="theme-1")
    b = _match(case_id="case-b", theme_id="theme-1")
    c = _match(case_id="case-c", theme_id="theme-1")
    for m in (a, b, c):
        theme_matching_store.insert_match(tmp_path, m)

    result = theme_matching_store.existing_match_ids_for_case_ids(tmp_path, ["case-a", "case-c", "case-missing"])
    assert result == frozenset({a.id, c.id})


def test_list_pending_matches_derived_from_absence_of_decision(tmp_path):
    decided = _match(case_id="case-decided", theme_id="theme-1", created_at="2026-08-20T00:00:00+00:00")
    pending = _match(case_id="case-pending", theme_id="theme-1", created_at="2026-08-21T00:00:00+00:00")
    theme_matching_store.insert_match(tmp_path, decided)
    theme_matching_store.insert_match(tmp_path, pending)
    theme_matching_store.insert_review_decision(tmp_path, _decision(match_id=decided.id))

    result = theme_matching_store.list_pending_matches(tmp_path)
    assert [m.id for m in result] == [pending.id]


def test_list_pending_matches_empty_when_no_matches(tmp_path):
    assert theme_matching_store.list_pending_matches(tmp_path) == ()


def test_list_pending_matches_deterministic_order(tmp_path):
    older = _match(case_id="case-old", theme_id="theme-1", created_at="2026-08-01T00:00:00+00:00")
    newer = _match(case_id="case-new", theme_id="theme-1", created_at="2026-08-05T00:00:00+00:00")
    theme_matching_store.insert_match(tmp_path, newer)
    theme_matching_store.insert_match(tmp_path, older)
    result = theme_matching_store.list_pending_matches(tmp_path)
    assert [m.id for m in result] == [older.id, newer.id]


# ============================================================
# Review decisions
# ============================================================


def test_build_review_decision_id_is_deterministic_and_pure():
    a = theme_matching_store.build_review_decision_id("theme-match-1", "2026-08-21T00:00:00+00:00")
    b = theme_matching_store.build_review_decision_id("theme-match-1", "2026-08-21T00:00:00+00:00")
    c = theme_matching_store.build_review_decision_id("theme-match-1", "2026-08-22T00:00:00+00:00")
    assert a == b
    assert a != c


def test_insert_review_decision_and_duplicate_rejection(tmp_path):
    decision = _decision()
    tampered = ThemeMatchReviewDecision(**{**decision.__dict__, "reviewer_note": "TAMPERED"})
    assert theme_matching_store.insert_review_decision(tmp_path, decision) is True
    assert theme_matching_store.insert_review_decision(tmp_path, tampered) is False


def test_list_review_decisions_for_match_is_immutable_audit_history_ordered(tmp_path):
    match_id = "theme-match-shared"
    first = _decision(match_id=match_id, reviewed_at="2026-08-20T00:00:00+00:00", decision=MatchReviewStatus.PENDING_REVIEW)
    second = _decision(match_id=match_id, reviewed_at="2026-08-21T00:00:00+00:00", decision=MatchReviewStatus.ACCEPTED)
    theme_matching_store.insert_review_decision(tmp_path, first)
    theme_matching_store.insert_review_decision(tmp_path, second)

    result = theme_matching_store.list_review_decisions_for_match(tmp_path, match_id)
    assert [d.id for d in result] == [first.id, second.id]


def test_list_review_decisions_for_match_empty_when_none(tmp_path):
    assert theme_matching_store.list_review_decisions_for_match(tmp_path, "theme-match-missing") == ()


# ============================================================
# No update/upsert/replace/delete path exists anywhere in this module
# ============================================================


def test_no_update_upsert_replace_or_delete_functions_exist():
    exported = {name for name in dir(theme_matching_store) if not name.startswith("_")}
    forbidden_substrings = ("update", "delete", "replace", "upsert", "overwrite", "merge")
    offenders = [name for name in exported if any(f in name.lower() for f in forbidden_substrings)]
    assert not offenders, offenders
