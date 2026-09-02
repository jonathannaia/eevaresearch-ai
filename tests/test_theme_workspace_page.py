"""EevaResearch — Citrini-style Theme research workspace vertical slice
(design/DECISIONS.md). Tests for src/ui/pages/theme_workspace.py — the
hidden, internal-only UI. Every fixture is synthetic and directly
constructed; no real network, worker, scan, or LLM call anywhere in
this file. Business-logic functions (create_theme_with_scope,
promote_candidate, reject_candidate, add_research_note,
add_company_map_entry, publish_transition, gather_match_context) are
tested directly against real JSON/SQLite-backed repositories; page
rendering is tested via Streamlit's AppTest against fake repository
doubles."""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access import backend_factory
from src.logic.research_case_theme_matching import evaluate_theme_match
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, StateTransition
from src.models.research_case import ResearchCase, ResearchCaseStatus
from src.models.theme_matching import MatchReviewStatus, ThemeMatchingScope, ThemeMatchReviewDecision
from src.models.theme_research import (
    CompanyRole,
    EvidenceDirection,
    HypothesisConfidence,
    ResearchTheme,
    ThemeCategory,
    ThemeNoteType,
    ThemeStatus,
    ThemeVisibility,
)
from src.ui.pages import theme_workspace
from src.ui.ui import HIDDEN_FROM_NAV, PRIMARY_NAV, SYSTEM_NAV

REPO_ROOT = Path(__file__).parent.parent
APP_PATH = REPO_ROOT / "app.py"
HARNESS_PATH = REPO_ROOT / "tests" / "apptest_pages" / "theme_workspace_page.py"
_PAGE_PATH = REPO_ROOT / "src" / "ui" / "pages" / "theme_workspace.py"


def _theme(theme_id="theme-x", visibility=ThemeVisibility.INTERNAL, **overrides):
    defaults = dict(
        id=theme_id, category=ThemeCategory.BOTTLENECK, status=ThemeStatus.NEW, visibility=visibility,
        title="AI Infrastructure: Where Is the Binding Constraint?", key_question="q", hypothesis="h",
        working_thesis="w", why_it_matters="y", what_could_change_the_view="c", what_to_watch_next="n",
        created_at="2026-09-01T00:00:00+00:00", updated_at="2026-09-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    return ResearchTheme(**defaults)


def _candidate(candidate_id="edgar-cand-1", **overrides):
    filing = FilingEvent(
        rcept_no="acc-1", corp_code="0001", corp_name="TSMC", stock_code="TSM", report_nm="8-K",
        rcept_dt="2026-08-15", flr_nm="TSMC", source_name="SEC EDGAR", source_url="https://example.com/f",
        retrieved_at="2026-08-15T01:00:00+00:00", original_language="English", theme_slug="ai-buildout",
    )
    defaults = dict(
        id=candidate_id, filing=filing, matched_rules=["material_agreement:1.01"], confidence="High",
        status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="TSMC capacity expansion.",
        state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="2026-08-15T00:00:00+00:00")],
    )
    defaults.update(overrides)
    return CandidateSignal(**defaults)


def _case(case_id="case-1", trigger_source_id="edgar-cand-1", **overrides):
    defaults = dict(
        id=case_id, trigger_source_type="radar", trigger_source_id=trigger_source_id, trigger_source_name="TSMC",
        trigger_summary="8-K", title="t", research_question="q", status=ResearchCaseStatus.OPEN,
        created_at="2026-08-15T00:00:00+00:00", version=1,
    )
    defaults.update(overrides)
    return ResearchCase(**defaults)


def _full_scenario(settings: Settings):
    """Real JSON-backed theme + scope + one pending match, end to end."""
    curator = backend_factory.get_theme_curator_repository(settings)
    matching_repo = backend_factory.get_theme_matching_repository(settings)
    theme = _theme()
    curator.insert_theme(theme)

    scope = ThemeMatchingScope(
        theme_id=theme.id, sector_tags=("ai-buildout",), sector_subtags=(),
        allowed_matched_rule_categories=("material_agreement",), required_keywords=("capacity",), excluded_keywords=(),
    )
    matching_repo.insert_scope(scope)

    candidate = _candidate()
    candidate_repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    candidate_repo.upsert_new_candidates([candidate])

    from src.data_access import research_store
    case = _case()
    research_store.append_research_case(settings.cache_dir, case)

    match = evaluate_theme_match(candidate, case.id, scope, created_at="2026-08-15T00:00:00+00:00")
    matching_repo.insert_match(match)
    return theme, scope, candidate, case, match


# ============================================================
# Business logic — direct unit tests
# ============================================================


def test_create_theme_with_scope_success(tmp_path):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    curator = backend_factory.get_theme_curator_repository(settings)
    matching_repo = backend_factory.get_theme_matching_repository(settings)

    theme, errors = theme_workspace.create_theme_with_scope(
        curator, matching_repo, title="T", category=ThemeCategory.BOTTLENECK, key_question="q", hypothesis="h",
        working_thesis="w", why_it_matters="y", what_could_change_the_view="c", what_to_watch_next="n",
        sector_tags=("ai-buildout",), sector_subtags=("hbm",), allowed_rule_categories=("material_agreement",),
        required_keywords=("capacity",), excluded_keywords=(), created_at="2026-09-01T00:00:00+00:00",
    )
    assert errors == ()
    assert theme.visibility is ThemeVisibility.INTERNAL
    assert matching_repo.get_scope(theme.id).sector_subtags == ("hbm",)


def test_create_theme_with_scope_rejects_blank_fields(tmp_path):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    curator = backend_factory.get_theme_curator_repository(settings)
    matching_repo = backend_factory.get_theme_matching_repository(settings)

    theme, errors = theme_workspace.create_theme_with_scope(
        curator, matching_repo, title="", category=ThemeCategory.BOTTLENECK, key_question="q", hypothesis="h",
        working_thesis="w", why_it_matters="y", what_could_change_the_view="c", what_to_watch_next="n",
        sector_tags=("ai-buildout",), sector_subtags=(), allowed_rule_categories=("material_agreement",),
        required_keywords=("capacity",), excluded_keywords=(), created_at="2026-09-01T00:00:00+00:00",
    )
    assert theme is None
    assert any("title" in e for e in errors)


def test_create_theme_with_scope_requires_scope_fields(tmp_path):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    curator = backend_factory.get_theme_curator_repository(settings)
    matching_repo = backend_factory.get_theme_matching_repository(settings)

    theme, errors = theme_workspace.create_theme_with_scope(
        curator, matching_repo, title="T", category=ThemeCategory.BOTTLENECK, key_question="q", hypothesis="h",
        working_thesis="w", why_it_matters="y", what_could_change_the_view="c", what_to_watch_next="n",
        sector_tags=(), sector_subtags=(), allowed_rule_categories=(), required_keywords=(), excluded_keywords=(),
        created_at="2026-09-01T00:00:00+00:00",
    )
    assert theme is None
    assert len(errors) == 3


def test_create_theme_with_scope_duplicate_rejected(tmp_path):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    curator = backend_factory.get_theme_curator_repository(settings)
    matching_repo = backend_factory.get_theme_matching_repository(settings)
    kwargs = dict(
        title="T", category=ThemeCategory.BOTTLENECK, key_question="q", hypothesis="h", working_thesis="w",
        why_it_matters="y", what_could_change_the_view="c", what_to_watch_next="n",
        sector_tags=("ai-buildout",), sector_subtags=(), allowed_rule_categories=("material_agreement",),
        required_keywords=("capacity",), excluded_keywords=(), created_at="2026-09-01T00:00:00+00:00",
    )
    theme_workspace.create_theme_with_scope(curator, matching_repo, **kwargs)
    theme, errors = theme_workspace.create_theme_with_scope(curator, matching_repo, **kwargs)
    assert theme is None
    assert errors


def test_add_company_map_entry_success_and_duplicate(tmp_path):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    curator = backend_factory.get_theme_curator_repository(settings)
    curator.insert_theme(_theme())

    ok, errors = theme_workspace.add_company_map_entry(curator, "theme-x", "TSMC", CompanyRole.CONSTRAINT_OWNER, "note")
    assert ok is True
    assert errors == ()
    ok2, errors2 = theme_workspace.add_company_map_entry(curator, "theme-x", "TSMC", CompanyRole.CONSTRAINT_OWNER, "note")
    assert ok2 is False


def test_add_company_map_entry_blank_name_rejected(tmp_path):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    curator = backend_factory.get_theme_curator_repository(settings)
    ok, errors = theme_workspace.add_company_map_entry(curator, "theme-x", "   ", CompanyRole.EXPOSED, None)
    assert ok is False
    assert errors


def test_add_research_note_hypothesis_requires_confidence_and_disconfirming(tmp_path):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    curator = backend_factory.get_theme_curator_repository(settings)
    curator.insert_theme(_theme())

    ok, errors = theme_workspace.add_research_note(
        curator, "theme-x", ThemeNoteType.HYPOTHESIS, "content", None, None, "2026-09-01T00:00:00+00:00",
    )
    assert ok is False
    assert len(errors) == 2

    ok2, errors2 = theme_workspace.add_research_note(
        curator, "theme-x", ThemeNoteType.HYPOTHESIS, "content", HypothesisConfidence.HIGH, "reject if X",
        "2026-09-01T00:00:00+00:00",
    )
    assert ok2 is True
    assert errors2 == ()


def test_add_research_note_decision_ignores_confidence(tmp_path):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    curator = backend_factory.get_theme_curator_repository(settings)
    curator.insert_theme(_theme())

    ok, errors = theme_workspace.add_research_note(
        curator, "theme-x", ThemeNoteType.DECISION, "content", HypothesisConfidence.HIGH, "irrelevant",
        "2026-09-01T00:00:00+00:00",
    )
    assert ok is True
    notes = curator.research_notes_for_theme("theme-x")
    assert notes[0].confidence is None
    assert notes[0].disconfirming_condition is None


def test_promote_candidate_and_reject_candidate(tmp_path):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    theme, scope, candidate, case, match = _full_scenario(settings)
    curator = backend_factory.get_theme_curator_repository(settings)
    matching_repo = backend_factory.get_theme_matching_repository(settings)

    ok, errors = theme_workspace.promote_candidate(
        matching_repo, curator, candidate, match, EvidenceDirection.SUPPORTS,
        "TSMC disclosed capacity expansion.", "Supports the hypothesis.", "2026-08-16T00:00:00+00:00",
    )
    assert ok is True
    assert errors == ()
    evidence = curator.evidence_for_theme(theme.id)
    assert len(evidence) == 1
    assert evidence[0].direction is EvidenceDirection.SUPPORTS


def test_promote_candidate_blank_fact_rejected(tmp_path):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    theme, scope, candidate, case, match = _full_scenario(settings)
    curator = backend_factory.get_theme_curator_repository(settings)
    matching_repo = backend_factory.get_theme_matching_repository(settings)

    ok, errors = theme_workspace.promote_candidate(
        matching_repo, curator, candidate, match, EvidenceDirection.SUPPORTS, "   ", "relevance", "2026-08-16T00:00:00+00:00",
    )
    assert ok is False
    assert errors


def test_reject_candidate_records_decision_without_evidence(tmp_path):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    theme, scope, candidate, case, match = _full_scenario(settings)
    curator = backend_factory.get_theme_curator_repository(settings)
    matching_repo = backend_factory.get_theme_matching_repository(settings)

    ok, errors = theme_workspace.reject_candidate(matching_repo, match, "2026-08-16T00:00:00+00:00")
    assert ok is True
    assert curator.evidence_for_theme(theme.id) == ()
    decisions = matching_repo.list_review_decisions_for_match(match.id)
    assert decisions[0].decision is MatchReviewStatus.REJECTED


def test_duplicate_review_decision_rejected(tmp_path):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    theme, scope, candidate, case, match = _full_scenario(settings)
    curator = backend_factory.get_theme_curator_repository(settings)
    matching_repo = backend_factory.get_theme_matching_repository(settings)

    theme_workspace.reject_candidate(matching_repo, match, "2026-08-16T00:00:00+00:00")
    ok, errors = theme_workspace.promote_candidate(
        matching_repo, curator, candidate, match, EvidenceDirection.SUPPORTS, "f", "r", "2026-08-16T00:00:00+00:00",
    )
    assert ok is False


def test_gather_match_context_returns_case_and_candidate(tmp_path):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    theme, scope, candidate, case, match = _full_scenario(settings)
    research_case_repo = backend_factory.get_research_case_repository(settings)

    got_case, got_candidate = theme_workspace.gather_match_context(settings, research_case_repo, match)
    assert got_case.id == case.id
    assert got_candidate.id == candidate.id


def test_gather_match_context_missing_case_returns_none_none(tmp_path):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    research_case_repo = backend_factory.get_research_case_repository(settings)
    from src.models.theme_matching import ResearchCaseThemeMatch, MatchConfidence
    fake_match = ResearchCaseThemeMatch(
        id="m1", case_id="case-missing", theme_id="theme-x", confidence=MatchConfidence.LOW,
        direction=EvidenceDirection.CONTEXT, matched_sector_tag=None, matched_rule_categories=(),
        matched_keywords=(), rationale="r", created_at="2026-08-15T00:00:00+00:00",
    )
    got_case, got_candidate = theme_workspace.gather_match_context(settings, research_case_repo, fake_match)
    assert got_case is None
    assert got_candidate is None


def test_publish_transition_requires_evidence_before_ready_to_publish(tmp_path):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    curator = backend_factory.get_theme_curator_repository(settings)
    theme = _theme()
    curator.insert_theme(theme)

    updated, errors = theme_workspace.publish_transition(curator, theme, ThemeVisibility.READY_TO_PUBLISH, "2026-09-01T00:00:00+00:00")
    assert updated is None
    assert "evidence" in errors[0].lower()


def test_publish_transition_succeeds_with_evidence_threshold_met(tmp_path):
    """The strengthened gate requires >= _MIN_EVIDENCE_ITEMS_TO_PUBLISH
    items from >= _MIN_DISTINCT_EVIDENCE_COMPANIES_TO_PUBLISH distinct
    companies — one item from one company (below both minimums) must
    still be rejected; two items from two companies must pass."""
    from src.models.theme_research import ThemeEvidenceItem

    settings = Settings(db_backend="json", cache_dir=tmp_path)
    theme, scope, candidate, case, match = _full_scenario(settings)
    curator = backend_factory.get_theme_curator_repository(settings)
    matching_repo = backend_factory.get_theme_matching_repository(settings)
    theme_workspace.promote_candidate(matching_repo, curator, candidate, match, EvidenceDirection.SUPPORTS, "f", "r", "2026-08-16T00:00:00+00:00")

    below_threshold, errors = theme_workspace.publish_transition(curator, theme, ThemeVisibility.READY_TO_PUBLISH, "2026-09-01T00:00:00+00:00")
    assert below_threshold is None
    assert errors

    curator.insert_evidence_item(ThemeEvidenceItem(
        id="evidence-2", theme_id=theme.id, date="2026-08-17", company="Samsung", source_name="SEC EDGAR",
        source_url="https://example.com/2", fact="f2", relevance="r2", direction=EvidenceDirection.SUPPORTS,
    ))

    updated, errors = theme_workspace.publish_transition(curator, theme, ThemeVisibility.READY_TO_PUBLISH, "2026-09-01T00:00:00+00:00")
    assert errors == ()
    assert updated.visibility is ThemeVisibility.READY_TO_PUBLISH


def test_publish_transition_rejects_same_company_evidence_below_distinct_threshold(tmp_path):
    """Two evidence items from the SAME company still fail the
    distinct-companies half of the threshold."""
    from src.models.theme_research import ThemeEvidenceItem

    settings = Settings(db_backend="json", cache_dir=tmp_path)
    theme, scope, candidate, case, match = _full_scenario(settings)
    curator = backend_factory.get_theme_curator_repository(settings)
    matching_repo = backend_factory.get_theme_matching_repository(settings)
    theme_workspace.promote_candidate(matching_repo, curator, candidate, match, EvidenceDirection.SUPPORTS, "f", "r", "2026-08-16T00:00:00+00:00")
    curator.insert_evidence_item(ThemeEvidenceItem(
        id="evidence-2", theme_id=theme.id, date="2026-08-17", company="TSMC", source_name="SEC EDGAR",
        source_url="https://example.com/2", fact="f2", relevance="r2", direction=EvidenceDirection.SUPPORTS,
    ))

    updated, errors = theme_workspace.publish_transition(curator, theme, ThemeVisibility.READY_TO_PUBLISH, "2026-09-01T00:00:00+00:00")
    assert updated is None
    assert "distinct companies" in errors[0]


def test_publish_transition_rejects_disallowed_transition(tmp_path):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    curator = backend_factory.get_theme_curator_repository(settings)
    theme = _theme(visibility=ThemeVisibility.PUBLISHED)
    curator.insert_theme(theme)

    updated, errors = theme_workspace.publish_transition(curator, theme, ThemeVisibility.INTERNAL, "2026-09-01T00:00:00+00:00")
    assert updated is None
    assert "cannot transition" in errors[0].lower()


def test_publish_transition_archived_has_no_further_transitions():
    theme = _theme(visibility=ThemeVisibility.ARCHIVED)
    allowed = theme_workspace._ALLOWED_VISIBILITY_TRANSITIONS.get(theme.visibility, frozenset())
    assert allowed == frozenset()


# ============================================================
# Public/private boundary and safety guards
# ============================================================


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_page_never_imports_a_script():
    imported = _imported_module_names(_PAGE_PATH)
    offenders = [m for m in imported if m.startswith("scripts.") or m == "scripts"]
    assert not offenders, offenders


def test_page_never_imports_public_theme_repository_protocol_usage():
    """AST-based, not substring-based: the module docstring legitimately
    explains *why* the public protocol is avoided (mentioning its name
    in prose), which a naive whole-file substring scan would false-
    positive on — only real imports/calls are checked here."""
    tree = ast.parse(_PAGE_PATH.read_text(encoding="utf-8"), filename=str(_PAGE_PATH))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.names:
            offenders.extend(a.name for a in node.names if a.name in ("ThemeRepositoryProtocol", "get_theme_repository"))
        elif isinstance(node, ast.Attribute) and node.attr == "get_theme_repository":
            offenders.append("get_theme_repository")
    assert not offenders, offenders


def test_page_never_imports_network_or_llm_modules():
    imported = _imported_module_names(_PAGE_PATH)
    forbidden_substrings = ("openai", "anthropic", "requests", "httpx", "urllib")
    offenders = [m for m in imported if any(f in m.lower() for f in forbidden_substrings)]
    assert not offenders, offenders


# ============================================================
# Route registration / nav hiding
# ============================================================


def test_route_registered_and_hidden_not_in_any_nav_group():
    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    at.run()
    pages = at.session_state["_pages"]
    assert "theme_workspace" in pages
    assert pages["theme_workspace"].visibility == "hidden"

    all_nav_keys = {k for k, _ in PRIMARY_NAV + SYSTEM_NAV + HIDDEN_FROM_NAV}
    assert "theme_workspace" not in all_nav_keys


def test_global_beta_gate_still_covers_the_route(monkeypatch):
    monkeypatch.setenv("EDGE_PRIVATE_BETA_AUTH_ENABLED", "true")
    monkeypatch.setenv("EDGE_PRIVATE_BETA_ALLOWED_EMAILS", "")
    monkeypatch.setenv("EDGE_THEME_WORKSPACE_ENABLED", "true")
    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    at.query_params["theme_id"] = ""
    at.run()
    all_text = " ".join(m.value for m in at.markdown) + " ".join(t.value for t in at.title)
    assert "Private beta" in all_text
    assert "Constraint Research Workspace" not in all_text


# ============================================================
# Page rendering — disabled / empty / populated states
# ============================================================


def test_disabled_by_default_shows_not_enabled_message(monkeypatch):
    monkeypatch.delenv("EDGE_THEME_WORKSPACE_ENABLED", raising=False)
    at = AppTest.from_file(str(HARNESS_PATH), default_timeout=30)
    at.run()
    assert not at.exception
    all_text = " ".join(i.value for i in at.info)
    assert theme_workspace._NOT_ENABLED_MESSAGE in all_text


def test_enabled_empty_state_renders(monkeypatch, tmp_path):
    monkeypatch.setenv("EDGE_THEME_WORKSPACE_ENABLED", "true")
    monkeypatch.setenv("EDGE_DB_BACKEND", "json")
    monkeypatch.setenv("EDGE_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(theme_workspace, "get_settings", lambda: Settings(db_backend="json", cache_dir=tmp_path, theme_workspace_enabled=True))
    at = AppTest.from_file(str(HARNESS_PATH), default_timeout=30)
    at.run()
    assert not at.exception


def test_detail_view_renders_all_tabs_without_exception(monkeypatch, tmp_path):
    settings = Settings(db_backend="json", cache_dir=tmp_path, theme_workspace_enabled=True)
    theme, scope, candidate, case, match = _full_scenario(settings)
    curator = backend_factory.get_theme_curator_repository(settings)
    matching_repo = backend_factory.get_theme_matching_repository(settings)
    theme_workspace.promote_candidate(matching_repo, curator, candidate, match, EvidenceDirection.SUPPORTS, "f", "r", "2026-08-16T00:00:00+00:00")
    theme_workspace.add_company_map_entry(curator, theme.id, "TSMC", CompanyRole.CONSTRAINT_OWNER, "note")
    theme_workspace.add_research_note(curator, theme.id, ThemeNoteType.HYPOTHESIS, "hyp", HypothesisConfidence.MEDIUM, "cond", "2026-08-16T00:00:00+00:00")

    monkeypatch.setattr(theme_workspace, "get_settings", lambda: settings)
    at = AppTest.from_file(str(HARNESS_PATH), default_timeout=30)
    at.query_params["theme_id"] = theme.id
    at.run()
    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "TSMC" in all_text


def test_theme_not_found_renders_empty_state(monkeypatch, tmp_path):
    settings = Settings(db_backend="json", cache_dir=tmp_path, theme_workspace_enabled=True)
    monkeypatch.setattr(theme_workspace, "get_settings", lambda: settings)
    at = AppTest.from_file(str(HARNESS_PATH), default_timeout=30)
    at.query_params["theme_id"] = "theme-does-not-exist"
    at.run()
    assert not at.exception


# ============================================================
# Scope guard — no other unexpected file touched
# ============================================================


def test_no_unexpected_files_touched():
    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    allowed_prefixes_and_files = {
        "app.py", "src/config/settings.py", "src/data_access/backend_factory.py",
        "src/data_access/theme_store.py", "src/data_access/state_db/theme_repository.py",
        "src/data_access/postgres_state_db/theme_repository.py",
        # Autonomous Theme candidate detection (design/DECISIONS.md).
        "scripts/radar_worker.py",
    }
    unexpected = changed - allowed_prefixes_and_files
    unexpected = {c for c in unexpected if not c.startswith("src/ui/pages/theme_workspace.py") and not c.startswith("tests/")}
    assert not unexpected, unexpected
