"""EevaResearch — Phase A2 (design/DECISIONS.md). EDGAR-only,
post-Research-Case deterministic Theme-matching worker integration
tests for scripts/radar_worker.py::_run_theme_matching_step() and its
wiring into _run_provider_tick(). Follows this repo's existing
tests/test_radar_worker_research_case_integration.py conventions
exactly: every test exercises a real in-memory SQLite backend (never a
live network call, live scan, or the authoring script), using fake
`run_scan` service modules from `types.SimpleNamespace`. No real Theme,
scope, match, or review decision is ever created outside a test
fixture; no worker process, scheduler loop, or deployment is ever
started."""
from __future__ import annotations

import types

import pytest

from scripts import radar_worker
from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.state_db import research_repository as sqlite_research_repository
from src.models.models import (
    CandidateSignal,
    CandidateStatus,
    ExtractionState,
    FilingEvent,
    StateTransition,
)
from src.models.research_case import ResearchCase, ResearchCaseStatus
from src.models.theme_matching import MatchConfidence, ThemeMatchingScope
from src.models.theme_research import (
    EvidenceDirection,
    ResearchTheme,
    ThemeCategory,
    ThemeStatus,
    ThemeVisibility,
)


def _worker_settings(tmp_path) -> Settings:
    ambient = Settings(
        radar_live_scan_enabled=True, radar_worker_db_backend="sqlite",
        radar_worker_state_db_path=tmp_path / "state.db", radar_worker_state_db_url=None,
        edgar_auto_publish_enabled=False,
    )
    return radar_worker._build_worker_settings(ambient)


def _fake_report(candidates_detected=0, candidates_processed=0, end_date="2026-08-20"):
    return types.SimpleNamespace(
        candidates_detected=candidates_detected, candidates_processed=candidates_processed,
        warnings=(), end_date=end_date,
    )


def _set_edgar_run_scan(monkeypatch, report=None):
    report = report or _fake_report()
    monkeypatch.setitem(
        radar_worker._SERVICE_MODULES, "edgar",
        types.SimpleNamespace(run_scan=lambda settings, candidate_repository=None: report),
    )


def _theme(theme_id="theme-1", **overrides):
    defaults = dict(
        id=theme_id, category=ThemeCategory.BOTTLENECK, status=ThemeStatus.NEW, visibility=ThemeVisibility.INTERNAL,
        title="T", key_question="q", hypothesis="h", working_thesis="w", why_it_matters="y",
        what_could_change_the_view="c", what_to_watch_next="n",
        created_at="2026-08-20T00:00:00+00:00", updated_at="2026-08-20T00:00:00+00:00",
    )
    defaults.update(overrides)
    return ResearchTheme(**defaults)


def _scope(theme_id="theme-1", **overrides):
    defaults = dict(
        theme_id=theme_id, sector_tags=("semis",), sector_subtags=(),
        allowed_matched_rule_categories=("financing_or_debt",), required_keywords=("financing",),
        excluded_keywords=(),
    )
    defaults.update(overrides)
    return ThemeMatchingScope(**defaults)


def _seed_active_scope(worker_settings, **overrides):
    curator = backend_factory.get_theme_curator_repository(worker_settings)
    curator.insert_theme(_theme())
    matching_repo = backend_factory.get_theme_matching_repository(worker_settings)
    matching_repo.insert_scope(_scope(**overrides))


def _candidate(candidate_id="edgar-cand-1", rcept_no="acc-1", theme_slug="semis", **overrides):
    filing = FilingEvent(
        rcept_no=rcept_no, corp_code="0000320193", corp_name="Apple Inc.", stock_code="AAPL",
        report_nm="8-K", rcept_dt="2026-08-15", flr_nm="Apple Inc.", source_name="SEC EDGAR",
        source_url="https://example.com/filing", retrieved_at="2026-08-15T01:00:00+00:00",
        original_language="English", theme_slug=theme_slug,
    )
    defaults = dict(
        id=candidate_id, filing=filing, matched_rules=["financing_or_debt:2.03"],
        confidence="High", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="The company entered into a financing agreement.",
        state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="2026-08-15T00:00:00+00:00")],
    )
    defaults.update(overrides)
    return CandidateSignal(**defaults)


def _case(case_id="case-1", trigger_source_id="edgar-cand-1", created_at="2026-08-15T00:00:00+00:00", **overrides):
    defaults = dict(
        id=case_id, trigger_source_type="radar", trigger_source_id=trigger_source_id,
        trigger_source_name="Apple Inc.", trigger_summary="8-K", title="t", research_question="q",
        status=ResearchCaseStatus.OPEN, created_at=created_at, version=1,
    )
    defaults.update(overrides)
    return ResearchCase(**defaults)


def _seed_existing_case(worker_settings, case):
    conn = backend_factory._require_sqlite_connection(worker_settings)
    sqlite_research_repository.insert_research_case(conn, case)


# ============================================================
# Zero scopes
# ============================================================


def test_zero_scopes_returns_exact_all_zero_line(tmp_path):
    worker_settings = _worker_settings(tmp_path)
    candidates = {"edgar-cand-1": _candidate()}
    cases = (_case(),)
    summary = radar_worker._run_theme_matching_step(worker_settings, candidates, cases)
    assert summary == radar_worker._ZERO_SCOPE_THEME_MATCHING_SUMMARY
    assert summary == (
        "EDGAR: theme matching — scopes_loaded=0 cases_considered=0 "
        "matches_created=0 matches_existing=0 no_match=0 matching_errors=0"
    )


def test_zero_scopes_never_calls_list_recent_cases_or_inserts(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)

    def _forbidden(*_a, **_k):
        raise AssertionError("must never be called when there are zero active scopes")

    monkeypatch.setattr(backend_factory, "get_research_case_repository", _forbidden)
    candidates = {"edgar-cand-1": _candidate()}
    cases = (_case(),)
    radar_worker._run_theme_matching_step(worker_settings, candidates, cases)


def test_zero_scopes_end_to_end_via_run_provider_tick(tmp_path, monkeypatch, capsys):
    worker_settings = _worker_settings(tmp_path)
    _set_edgar_run_scan(monkeypatch)
    scan_status_repo = backend_factory.get_scan_status_repository(worker_settings)

    radar_worker._run_provider_tick("edgar", worker_settings, scan_status_repo)
    out = capsys.readouterr().out
    assert radar_worker._ZERO_SCOPE_THEME_MATCHING_SUMMARY in out


# ============================================================
# One scope, one match / no match
# ============================================================


def test_one_scope_one_matching_case_creates_exactly_one_match(tmp_path):
    worker_settings = _worker_settings(tmp_path)
    _seed_active_scope(worker_settings)
    candidates = {"edgar-cand-1": _candidate()}
    case = _case()
    _seed_existing_case(worker_settings, case)  # FK: research_case_theme_matches.case_id -> research_cases.id
    cases = (case,)

    summary = radar_worker._run_theme_matching_step(worker_settings, candidates, cases)
    assert summary == (
        "EDGAR: theme matching — scopes_loaded=1 cases_considered=1 "
        "matches_created=1 matches_existing=0 no_match=0 matching_errors=0"
    )

    matching_repo = backend_factory.get_theme_matching_repository(worker_settings)
    pending = matching_repo.list_pending_matches()
    assert len(pending) == 1
    assert pending[0].direction == EvidenceDirection.CONTEXT
    assert pending[0].confidence in (MatchConfidence.LOW, MatchConfidence.MEDIUM, MatchConfidence.HIGH)


def test_deterministic_gate_failure_produces_no_match(tmp_path):
    worker_settings = _worker_settings(tmp_path)
    _seed_active_scope(worker_settings)
    candidates = {"edgar-cand-1": _candidate(theme_slug="unrelated-sector")}
    cases = (_case(),)

    summary = radar_worker._run_theme_matching_step(worker_settings, candidates, cases)
    assert summary == (
        "EDGAR: theme matching — scopes_loaded=1 cases_considered=1 "
        "matches_created=0 matches_existing=0 no_match=1 matching_errors=0"
    )


def test_existing_match_idempotency(tmp_path):
    worker_settings = _worker_settings(tmp_path)
    _seed_active_scope(worker_settings)
    candidates = {"edgar-cand-1": _candidate()}
    case = _case()
    _seed_existing_case(worker_settings, case)
    cases = (case,)

    first = radar_worker._run_theme_matching_step(worker_settings, candidates, cases)
    assert "matches_created=1" in first

    second = radar_worker._run_theme_matching_step(worker_settings, candidates, cases)
    assert second == (
        "EDGAR: theme matching — scopes_loaded=1 cases_considered=1 "
        "matches_created=0 matches_existing=1 no_match=0 matching_errors=0"
    )
    matching_repo = backend_factory.get_theme_matching_repository(worker_settings)
    assert len(matching_repo.list_pending_matches()) == 1


# ============================================================
# Backlog catch-up window
# ============================================================


def test_backlog_only_catch_up_when_no_cases_created_this_tick(tmp_path):
    worker_settings = _worker_settings(tmp_path)
    _seed_active_scope(worker_settings)
    candidate = _candidate()
    _seed_existing_case(worker_settings, _case())
    candidates = {"edgar-cand-1": candidate}

    summary = radar_worker._run_theme_matching_step(worker_settings, candidates, ())
    assert summary == (
        "EDGAR: theme matching — scopes_loaded=1 cases_considered=1 "
        "matches_created=1 matches_existing=0 no_match=0 matching_errors=0"
    )


def test_backlog_capped_at_25(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    _seed_active_scope(worker_settings)
    for i in range(30):
        candidate_id = f"edgar-cand-{i}"
        _seed_existing_case(
            worker_settings,
            _case(case_id=f"case-{i}", trigger_source_id=candidate_id, created_at=f"2026-08-{(i % 28) + 1:02d}T00:00:00+00:00"),
        )
    candidates = {f"edgar-cand-{i}": _candidate(candidate_id=f"edgar-cand-{i}", rcept_no=f"acc-{i}") for i in range(30)}

    captured_limit = {}
    research_case_repo = backend_factory.get_research_case_repository(worker_settings)
    original_list_recent = type(research_case_repo).list_recent_cases

    def _spy_list_recent_cases(self, limit):
        captured_limit["limit"] = limit
        return original_list_recent(self, limit)

    monkeypatch.setattr(type(research_case_repo), "list_recent_cases", _spy_list_recent_cases)

    summary = radar_worker._run_theme_matching_step(worker_settings, candidates, ())
    assert captured_limit["limit"] == 25
    assert "cases_considered=25" in summary


# ============================================================
# Selection filtering
# ============================================================


def test_non_edgar_radar_case_excluded_without_error(tmp_path):
    worker_settings = _worker_settings(tmp_path)
    _seed_active_scope(worker_settings)
    # trigger_source_id "not-an-edgar-candidate" is never in `candidates`.
    _seed_existing_case(worker_settings, _case(case_id="case-foreign", trigger_source_id="not-an-edgar-candidate"))
    candidates: dict = {}

    summary = radar_worker._run_theme_matching_step(worker_settings, candidates, ())
    assert summary == (
        "EDGAR: theme matching — scopes_loaded=1 cases_considered=0 "
        "matches_created=0 matches_existing=0 no_match=0 matching_errors=0"
    )


def test_daily_news_case_excluded_without_error(tmp_path):
    worker_settings = _worker_settings(tmp_path)
    _seed_active_scope(worker_settings)
    _seed_existing_case(
        worker_settings,
        _case(case_id="case-news", trigger_source_type="daily_news", trigger_source_id="news-1"),
    )
    candidates = {"news-1": _candidate(candidate_id="news-1")}

    summary = radar_worker._run_theme_matching_step(worker_settings, candidates, ())
    assert summary == (
        "EDGAR: theme matching — scopes_loaded=1 cases_considered=0 "
        "matches_created=0 matches_existing=0 no_match=0 matching_errors=0"
    )


def test_newly_created_case_also_in_recent_window_is_not_evaluated_twice(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    _seed_active_scope(worker_settings)
    case = _case()
    _seed_existing_case(worker_settings, case)
    candidates = {"edgar-cand-1": _candidate()}

    call_count = {"n": 0}
    original_evaluate = radar_worker.evaluate_theme_match

    def _counting_evaluate(*args, **kwargs):
        call_count["n"] += 1
        return original_evaluate(*args, **kwargs)

    monkeypatch.setattr(radar_worker, "evaluate_theme_match", _counting_evaluate)

    summary = radar_worker._run_theme_matching_step(worker_settings, candidates, (case,))
    assert call_count["n"] == 1  # one scope, one distinct case — never doubled
    assert "cases_considered=1" in summary


# ============================================================
# Defensive isolation
# ============================================================


def test_missing_candidate_is_isolated_and_does_not_raise(tmp_path):
    worker_settings = _worker_settings(tmp_path)
    _seed_active_scope(worker_settings)
    # `case.trigger_source_id` deliberately absent from `candidates`.
    candidates: dict = {}
    cases = (_case(),)

    summary = radar_worker._run_theme_matching_step(worker_settings, candidates, cases)
    assert summary == (
        "EDGAR: theme matching — scopes_loaded=1 cases_considered=0 "
        "matches_created=0 matches_existing=0 no_match=0 matching_errors=1"
    )
    assert "case-1" not in summary
    assert "edgar-cand-1" not in summary


def test_per_case_scope_matcher_exception_is_isolated(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    _seed_active_scope(worker_settings)
    candidates = {"edgar-cand-1": _candidate(), "edgar-cand-2": _candidate(candidate_id="edgar-cand-2", rcept_no="acc-2")}
    cases = (_case(case_id="case-1", trigger_source_id="edgar-cand-1"), _case(case_id="case-2", trigger_source_id="edgar-cand-2"))
    for case in cases:
        _seed_existing_case(worker_settings, case)

    real_evaluate = radar_worker.evaluate_theme_match

    def _raise_for_case_1(candidate, case_id, scope, created_at):
        if case_id == "case-1":
            raise ValueError("boom")
        return real_evaluate(candidate, case_id, scope, created_at)

    monkeypatch.setattr(radar_worker, "evaluate_theme_match", _raise_for_case_1)

    summary = radar_worker._run_theme_matching_step(worker_settings, candidates, cases)
    assert summary == (
        "EDGAR: theme matching — scopes_loaded=1 cases_considered=2 "
        "matches_created=1 matches_existing=0 no_match=0 matching_errors=1"
    )
    assert "boom" not in summary


def test_per_match_insert_exception_is_isolated(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    _seed_active_scope(worker_settings)
    candidates = {"edgar-cand-1": _candidate()}
    cases = (_case(),)

    real_get_repo = backend_factory.get_theme_matching_repository

    class _RaisingInsertRepo:
        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        def insert_match(self, match):
            raise RuntimeError("insert exploded")

    monkeypatch.setattr(backend_factory, "get_theme_matching_repository", lambda s: _RaisingInsertRepo(real_get_repo(s)))

    summary = radar_worker._run_theme_matching_step(worker_settings, candidates, cases)
    assert summary == (
        "EDGAR: theme matching — scopes_loaded=1 cases_considered=1 "
        "matches_created=0 matches_existing=0 no_match=0 matching_errors=1"
    )
    assert "insert exploded" not in summary


# ============================================================
# Whole-step abort isolation
# ============================================================


def test_scope_load_failure_isolated_via_provider_tick(tmp_path, monkeypatch, capsys):
    worker_settings = _worker_settings(tmp_path)
    _set_edgar_run_scan(monkeypatch)
    scan_status_repo = backend_factory.get_scan_status_repository(worker_settings)

    class _RaisingScopesRepo:
        def list_active_scopes(self):
            raise RuntimeError("scope load failed")

    monkeypatch.setattr(backend_factory, "get_theme_matching_repository", lambda s: _RaisingScopesRepo())

    radar_worker._run_provider_tick("edgar", worker_settings, scan_status_repo)
    out = capsys.readouterr().out
    assert "EDGAR: theme-matching step skipped (RuntimeError)." in out
    assert "research cases" in out  # research-case step's own summary is untouched
    status = scan_status_repo.get_scan_status("SEC EDGAR")
    assert status.failure_code is None  # ProviderScanStatus unaffected


def test_recent_case_load_failure_isolated(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    _seed_active_scope(worker_settings)
    candidates = {"edgar-cand-1": _candidate()}

    real_get_repo = backend_factory.get_research_case_repository

    class _RaisingRecentCasesRepo:
        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        def list_recent_cases(self, limit):
            raise RuntimeError("recent-case load failed")

    monkeypatch.setattr(backend_factory, "get_research_case_repository", lambda s: _RaisingRecentCasesRepo(real_get_repo(s)))

    with pytest.raises(RuntimeError):
        radar_worker._run_theme_matching_step(worker_settings, candidates, ())


def test_bulk_existing_match_lookup_failure_isolated(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    _seed_active_scope(worker_settings)
    candidates = {"edgar-cand-1": _candidate()}
    cases = (_case(),)

    real_get_repo = backend_factory.get_theme_matching_repository

    class _RaisingBulkLookupRepo:
        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        def existing_match_ids_for_case_ids(self, case_ids):
            raise RuntimeError("bulk lookup failed")

    monkeypatch.setattr(backend_factory, "get_theme_matching_repository", lambda s: _RaisingBulkLookupRepo(real_get_repo(s)))

    with pytest.raises(RuntimeError):
        radar_worker._run_theme_matching_step(worker_settings, candidates, cases)


# ============================================================
# EDGAR-only activation / DART/EDINET untouched
# ============================================================


@pytest.mark.parametrize("provider_key,display_source", [("dart", "OpenDART / DART"), ("edinet", "EDINET")])
def test_dart_and_edinet_never_run_theme_matching(tmp_path, monkeypatch, capsys, provider_key, display_source):
    worker_settings = _worker_settings(tmp_path)

    def _forbidden(*_a, **_k):
        raise AssertionError(f"{provider_key} tick must never call theme matching")

    monkeypatch.setattr(radar_worker, "_run_theme_matching_step", _forbidden)
    monkeypatch.setitem(
        radar_worker._SERVICE_MODULES, provider_key,
        types.SimpleNamespace(run_scan=lambda settings, candidate_repository=None: _fake_report(1, 1)),
    )
    scan_status_repo = backend_factory.get_scan_status_repository(worker_settings)

    radar_worker._run_provider_tick(provider_key, worker_settings, scan_status_repo)
    out = capsys.readouterr().out
    assert "theme matching" not in out
    assert scan_status_repo.get_scan_status(display_source) is not None


# ============================================================
# No public/UI/visibility/evidence/company-map side effects
# ============================================================


def test_matching_step_creates_no_theme_evidence_or_company_map_entries(tmp_path):
    worker_settings = _worker_settings(tmp_path)
    _seed_active_scope(worker_settings)
    candidates = {"edgar-cand-1": _candidate()}
    case = _case()
    _seed_existing_case(worker_settings, case)
    cases = (case,)

    radar_worker._run_theme_matching_step(worker_settings, candidates, cases)

    theme_repo = backend_factory.get_theme_repository(worker_settings)
    assert theme_repo.list_published_themes() == ()
    assert theme_repo.evidence_for_theme("theme-1") == ()
    assert theme_repo.company_map_for_theme("theme-1") == ()

    curator = backend_factory.get_theme_curator_repository(worker_settings)
    theme_after = curator.get_theme("theme-1")
    assert theme_after.visibility == ThemeVisibility.INTERNAL  # unchanged


def test_matching_step_never_imports_public_ui_modules():
    import ast
    from pathlib import Path

    repo_root = Path(__file__).parent.parent
    source = (repo_root / "scripts" / "radar_worker.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="radar_worker.py")
    offenders = []
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for module in modules:
            if module.startswith("src.ui."):
                offenders.append(module)
    assert not offenders, offenders
