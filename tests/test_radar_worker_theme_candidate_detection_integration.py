"""EevaResearch — autonomous Theme candidate detection (design/
DECISIONS.md). EDGAR-only worker integration tests for
scripts/radar_worker.py::_run_theme_candidate_detection_step() and its
wiring into _run_provider_tick(). Follows this repo's existing
tests/test_radar_worker_theme_matching_integration.py conventions
exactly: every test exercises a real in-memory SQLite backend (never a
live network call, live scan, or the authoring script). No real Theme,
scope, match, company-map entry, or research note is ever created
outside a test fixture; no worker process, scheduler loop, or
deployment is ever started."""
from __future__ import annotations

import types

import pytest

from scripts import radar_worker
from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.state_db import research_repository as sqlite_research_repository
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, StateTransition
from src.models.research_case import ResearchCase, ResearchCaseStatus
from src.models.theme_matching import ThemeMatchingScope
from src.models.theme_research import CompanyRole, ThemeCategory, ThemeNoteType, ThemeVisibility


def _worker_settings(tmp_path, *, detection_enabled=True) -> Settings:
    ambient = Settings(
        radar_live_scan_enabled=True, radar_worker_db_backend="sqlite",
        radar_worker_state_db_path=tmp_path / "state.db", radar_worker_state_db_url=None,
        edgar_auto_publish_enabled=False, theme_candidate_detection_enabled=detection_enabled,
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


def _set_dart_and_edinet_no_op_scan(monkeypatch):
    """Phase 2 (design/DECISIONS.md): theme-candidate-detection now
    runs once per tick via run_one_tick() (after all three providers),
    not nested inside _run_provider_tick("edgar", ...) — DART/EDINET
    are mocked to safe no-ops so a test exercising that call site
    stays network-free."""
    for provider_key in ("dart", "edinet"):
        monkeypatch.setitem(
            radar_worker._SERVICE_MODULES, provider_key,
            types.SimpleNamespace(run_scan=lambda settings, candidate_repository=None: _fake_report()),
        )


def _candidate(candidate_id, company, rcept_dt, theme_slug="ai-buildout", subtheme_slug="compute-accelerators"):
    filing = FilingEvent(
        rcept_no=f"acc-{candidate_id}", corp_code=f"code-{candidate_id}", corp_name=company, stock_code="X",
        report_nm="8-K", rcept_dt=rcept_dt, flr_nm=company, source_name="SEC EDGAR",
        source_url="https://example.com/filing", retrieved_at=rcept_dt + "T01:00:00+00:00",
        original_language="English", theme_slug=theme_slug, subtheme_slug=subtheme_slug,
    )
    return CandidateSignal(
        id=candidate_id, filing=filing, matched_rules=["material_agreement:1.01"], confidence="High",
        status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="Company disclosed a capacity expansion and wafer allocation agreement.",
        state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at=rcept_dt + "T00:00:00+00:00")],
    )


def _case(case_id, candidate_id, company, rcept_dt):
    return ResearchCase(
        id=case_id, trigger_source_type="radar", trigger_source_id=candidate_id, trigger_source_name=company,
        trigger_summary="8-K", title="t", research_question="q", status=ResearchCaseStatus.OPEN,
        created_at=rcept_dt + "T00:00:00+00:00", version=1,
    )


def _seed_case_and_candidate(worker_settings, candidate, case):
    conn = backend_factory._require_sqlite_connection(worker_settings)
    sqlite_research_repository.insert_research_case(conn, case)
    return conn


def _seed_cluster(worker_settings, companies_and_dates, theme_slug="ai-buildout", subtheme_slug="compute-accelerators"):
    candidates = {}
    for i, (company, rcept_dt) in enumerate(companies_and_dates):
        candidate_id = f"edgar-cand-{i}"
        case_id = f"case-{i}"
        candidate = _candidate(candidate_id, company, rcept_dt, theme_slug, subtheme_slug)
        case = _case(case_id, candidate_id, company, rcept_dt)
        _seed_case_and_candidate(worker_settings, candidate, case)
        candidates[candidate_id] = candidate
    return candidates


def _set_fixed_as_of_date(monkeypatch, value="2026-09-01"):
    monkeypatch.setattr(radar_worker, "_current_utc_date", lambda: value)


# ============================================================
# Disabled by default / gating
# ============================================================


def test_disabled_by_default_never_runs(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path, detection_enabled=False)
    candidates = _seed_cluster(worker_settings, [("TSMC", "2026-08-01"), ("Samsung", "2026-08-10")])

    def _forbidden(*_a, **_k):
        raise AssertionError("must never be called when theme_candidate_detection_enabled is False")

    monkeypatch.setattr(radar_worker, "_run_theme_candidate_detection_step", _forbidden)
    _set_edgar_run_scan(monkeypatch)
    _set_dart_and_edinet_no_op_scan(monkeypatch)
    scan_status_repo = backend_factory.get_scan_status_repository(worker_settings)
    radar_worker.run_one_tick(worker_settings, scan_status_repo)


def test_enabled_runs_end_to_end_via_provider_tick(tmp_path, monkeypatch, capsys):
    """Phase 2 (design/DECISIONS.md): theme-candidate-detection now
    runs once per tick via run_one_tick(), after all three providers —
    DART/EDINET are mocked to safe no-ops so this stays network-free."""
    worker_settings = _worker_settings(tmp_path, detection_enabled=True)
    candidates = _seed_cluster(worker_settings, [("TSMC", "2026-08-01"), ("Samsung", "2026-08-10")])
    # _run_edgar_research_case_step loads candidates from the real EDGAR
    # candidate repository (not the plain dict this helper also
    # returns) — seed both so the full provider-tick path sees them too.
    candidate_repo = backend_factory.get_candidate_repository(worker_settings, "SEC EDGAR")
    candidate_repo.upsert_new_candidates(list(candidates.values()))
    _set_edgar_run_scan(monkeypatch)
    _set_dart_and_edinet_no_op_scan(monkeypatch)
    _set_fixed_as_of_date(monkeypatch)
    scan_status_repo = backend_factory.get_scan_status_repository(worker_settings)

    radar_worker.run_one_tick(worker_settings, scan_status_repo)
    out = capsys.readouterr().out
    assert "EDGAR: theme candidate detection" in out
    assert "themes_created=1" in out


# ============================================================
# Direct step tests — real SQLite, threshold/dedup/content behavior
# ============================================================


def test_below_threshold_creates_nothing(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    candidates = _seed_cluster(worker_settings, [("TSMC", "2026-08-01")])
    _set_fixed_as_of_date(monkeypatch)

    summary = radar_worker._run_theme_candidate_detection_step(worker_settings, candidates, ())
    assert "clusters_detected=0" in summary
    assert "themes_created=0" in summary

    curator = backend_factory.get_theme_curator_repository(worker_settings)
    assert curator.list_themes() == ()


def test_threshold_met_creates_full_candidate_record(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    candidates = _seed_cluster(worker_settings, [("TSMC", "2026-08-01"), ("Samsung", "2026-08-10"), ("Micron", "2026-08-20")])
    _set_fixed_as_of_date(monkeypatch)

    summary = radar_worker._run_theme_candidate_detection_step(worker_settings, candidates, ())
    assert "clusters_detected=1" in summary
    assert "themes_created=1" in summary
    assert "matches_created=3" in summary
    assert "company_roles_created=3" in summary
    assert "notes_created=2" in summary
    assert "creation_errors=0" in summary

    curator = backend_factory.get_theme_curator_repository(worker_settings)
    themes = curator.list_themes()
    assert len(themes) == 1
    theme = themes[0]
    assert theme.visibility is ThemeVisibility.INTERNAL
    assert theme.category is ThemeCategory.BOTTLENECK

    company_map = curator.company_map_for_theme(theme.id)
    assert len(company_map) == 3
    assert all(entry.role is CompanyRole.EXPOSED for entry in company_map)
    assert {entry.company_name for entry in company_map} == {"TSMC", "Samsung", "Micron"}

    notes = curator.research_notes_for_theme(theme.id)
    assert len(notes) == 2
    note_types = {n.note_type for n in notes}
    assert note_types == {ThemeNoteType.HYPOTHESIS, ThemeNoteType.DECISION}

    matching_repo = backend_factory.get_theme_matching_repository(worker_settings)
    pending = matching_repo.list_pending_matches()
    assert len(pending) == 3
    assert all(m.theme_id == theme.id for m in pending)

    scope = matching_repo.get_scope(theme.id)
    assert scope.sector_tags == ("ai-buildout",)
    assert scope.sector_subtags == ("compute-accelerators",)


def test_idempotent_across_repeated_ticks(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    candidates = _seed_cluster(worker_settings, [("TSMC", "2026-08-01"), ("Samsung", "2026-08-10")])
    _set_fixed_as_of_date(monkeypatch)

    first = radar_worker._run_theme_candidate_detection_step(worker_settings, candidates, ())
    second = radar_worker._run_theme_candidate_detection_step(worker_settings, candidates, ())
    assert "themes_created=1" in first
    assert "themes_created=0" in second
    assert "clusters_detected=0" in second

    curator = backend_factory.get_theme_curator_repository(worker_settings)
    assert len(curator.list_themes()) == 1


def test_distinct_clusters_produce_distinct_themes(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    candidates = {}
    candidates.update(_seed_cluster(worker_settings, [("TSMC", "2026-08-01"), ("Samsung", "2026-08-10")], theme_slug="ai-buildout", subtheme_slug="compute-accelerators"))
    # Re-seed a second, non-overlapping cluster with distinct ids.
    for i, (company, rcept_dt) in enumerate([("SK Hynix", "2026-08-05"), ("Micron", "2026-08-15")]):
        candidate_id = f"edgar-cand-mem-{i}"
        case_id = f"case-mem-{i}"
        candidate = _candidate(candidate_id, company, rcept_dt, theme_slug="memory", subtheme_slug="hbm")
        case = _case(case_id, candidate_id, company, rcept_dt)
        _seed_case_and_candidate(worker_settings, candidate, case)
        candidates[candidate_id] = candidate
    _set_fixed_as_of_date(monkeypatch)

    summary = radar_worker._run_theme_candidate_detection_step(worker_settings, candidates, ())
    assert "clusters_detected=2" in summary
    assert "themes_created=2" in summary

    curator = backend_factory.get_theme_curator_repository(worker_settings)
    assert len(curator.list_themes()) == 2


def test_non_constraint_relevant_candidates_never_fire(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    candidates = {}
    for i, company in enumerate(["TSMC", "Samsung"]):
        candidate_id = f"edgar-cand-{i}"
        case_id = f"case-{i}"
        filing = FilingEvent(
            rcept_no=f"acc-{candidate_id}", corp_code=f"code-{i}", corp_name=company, stock_code="X",
            report_nm="10-Q", rcept_dt="2026-08-01", flr_nm=company, source_name="SEC EDGAR",
            source_url="https://example.com/filing", retrieved_at="2026-08-01T01:00:00+00:00",
            original_language="English", theme_slug="ai-buildout", subtheme_slug="compute-accelerators",
        )
        candidate = CandidateSignal(
            id=candidate_id, filing=filing, matched_rules=["earnings_or_results:10-Q"], confidence="High",
            status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
            excerpt_original="Routine quarterly earnings update.",
            state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="2026-08-01T00:00:00+00:00")],
        )
        case = _case(case_id, candidate_id, company, "2026-08-01")
        _seed_case_and_candidate(worker_settings, candidate, case)
        candidates[candidate_id] = candidate

    summary = radar_worker._run_theme_candidate_detection_step(worker_settings, candidates, ())
    assert "clusters_detected=0" in summary


def test_already_covered_cluster_not_reproposed(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    candidates = _seed_cluster(worker_settings, [("TSMC", "2026-08-01"), ("Samsung", "2026-08-10")])
    _set_fixed_as_of_date(monkeypatch)

    # Manually seed an existing theme+scope covering the same cluster,
    # simulating a theme created via the manual workspace/authoring
    # path instead of a prior detection tick.
    curator = backend_factory.get_theme_curator_repository(worker_settings)
    from src.models.theme_research import ResearchTheme, ThemeStatus

    theme = ResearchTheme(
        id="theme-manual", category=ThemeCategory.BOTTLENECK, status=ThemeStatus.NEW, visibility=ThemeVisibility.INTERNAL,
        title="Manual theme", key_question="q", hypothesis="h", working_thesis="w", why_it_matters="y",
        what_could_change_the_view="c", what_to_watch_next="n",
        created_at="2026-08-01T00:00:00+00:00", updated_at="2026-08-01T00:00:00+00:00",
    )
    curator.insert_theme(theme)
    matching_repo = backend_factory.get_theme_matching_repository(worker_settings)
    matching_repo.insert_scope(ThemeMatchingScope(
        theme_id=theme.id, sector_tags=("ai-buildout",), sector_subtags=("compute-accelerators",),
        allowed_matched_rule_categories=("material_agreement",), required_keywords=("capacity",), excluded_keywords=(),
    ))

    summary = radar_worker._run_theme_candidate_detection_step(worker_settings, candidates, ())
    assert "clusters_detected=0" in summary
    assert len(curator.list_themes()) == 1  # only the manually-seeded one


def test_archived_scope_does_not_block_redetection(tmp_path, monkeypatch):
    """An archived theme's scope is excluded from list_active_scopes(),
    so its cluster remains eligible for a fresh candidate."""
    worker_settings = _worker_settings(tmp_path)
    candidates = _seed_cluster(worker_settings, [("TSMC", "2026-08-01"), ("Samsung", "2026-08-10")])
    _set_fixed_as_of_date(monkeypatch)

    curator = backend_factory.get_theme_curator_repository(worker_settings)
    from src.models.theme_research import ResearchTheme, ThemeStatus

    theme = ResearchTheme(
        id="theme-archived", category=ThemeCategory.BOTTLENECK, status=ThemeStatus.RESOLVED, visibility=ThemeVisibility.ARCHIVED,
        title="Archived theme", key_question="q", hypothesis="h", working_thesis="w", why_it_matters="y",
        what_could_change_the_view="c", what_to_watch_next="n",
        created_at="2026-08-01T00:00:00+00:00", updated_at="2026-08-01T00:00:00+00:00",
    )
    curator.insert_theme(theme)
    matching_repo = backend_factory.get_theme_matching_repository(worker_settings)
    matching_repo.insert_scope(ThemeMatchingScope(
        theme_id=theme.id, sector_tags=("ai-buildout",), sector_subtags=("compute-accelerators",),
        allowed_matched_rule_categories=("material_agreement",), required_keywords=("capacity",), excluded_keywords=(),
    ))

    summary = radar_worker._run_theme_candidate_detection_step(worker_settings, candidates, ())
    assert "themes_created=1" in summary


# ============================================================
# Failure isolation
# ============================================================


def test_detection_step_failure_isolated_via_provider_tick(tmp_path, monkeypatch, capsys):
    worker_settings = _worker_settings(tmp_path, detection_enabled=True)
    _set_edgar_run_scan(monkeypatch)
    _set_dart_and_edinet_no_op_scan(monkeypatch)
    scan_status_repo = backend_factory.get_scan_status_repository(worker_settings)

    def _raise(*_a, **_k):
        raise RuntimeError("detection failed")

    monkeypatch.setattr(radar_worker, "_run_theme_candidate_detection_step", _raise)
    radar_worker.run_one_tick(worker_settings, scan_status_repo)
    out = capsys.readouterr().out
    assert "EDGAR: theme-candidate-detection step skipped (RuntimeError)." in out
    assert "research cases" in out  # research-case step's own summary is untouched
    status = scan_status_repo.get_scan_status("SEC EDGAR")
    assert status.failure_code is None  # ProviderScanStatus unaffected


def test_one_bad_candidate_never_blocks_the_rest_of_the_batch(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    candidates = {}
    candidates.update(_seed_cluster(worker_settings, [("TSMC", "2026-08-01"), ("Samsung", "2026-08-10")], theme_slug="ai-buildout", subtheme_slug="compute-accelerators"))
    for i, (company, rcept_dt) in enumerate([("SK Hynix", "2026-08-05"), ("Micron", "2026-08-15")]):
        candidate_id = f"edgar-cand-mem-{i}"
        case_id = f"case-mem-{i}"
        candidate = _candidate(candidate_id, company, rcept_dt, theme_slug="memory", subtheme_slug="hbm")
        case = _case(case_id, candidate_id, company, rcept_dt)
        _seed_case_and_candidate(worker_settings, candidate, case)
        candidates[candidate_id] = candidate
    _set_fixed_as_of_date(monkeypatch)

    real_insert_theme_calls = []
    real_get_theme_curator = backend_factory.get_theme_curator_repository

    class _FailFirstCuratorWrapper:
        def __init__(self, real):
            self._real = real
            self._calls = 0

        def __getattr__(self, name):
            return getattr(self._real, name)

        def insert_theme(self, theme):
            self._calls += 1
            if self._calls == 1:
                raise RuntimeError("boom")
            return self._real.insert_theme(theme)

    wrapper_holder = {}

    def _wrapped_curator(settings):
        if "wrapper" not in wrapper_holder:
            wrapper_holder["wrapper"] = _FailFirstCuratorWrapper(real_get_theme_curator(settings))
        return wrapper_holder["wrapper"]

    monkeypatch.setattr(backend_factory, "get_theme_curator_repository", _wrapped_curator)
    summary = radar_worker._run_theme_candidate_detection_step(worker_settings, candidates, ())
    assert "clusters_detected=2" in summary
    assert "themes_created=1" in summary
    assert "creation_errors=1" in summary


# ============================================================
# EDGAR-only activation / DART/EDINET untouched
# ============================================================


@pytest.mark.parametrize("provider_key,display_source", [("dart", "OpenDART / DART"), ("edinet", "EDINET")])
def test_dart_and_edinet_never_run_theme_candidate_detection(tmp_path, monkeypatch, capsys, provider_key, display_source):
    worker_settings = _worker_settings(tmp_path, detection_enabled=True)

    def _forbidden(*_a, **_k):
        raise AssertionError(f"{provider_key} tick must never call theme candidate detection")

    monkeypatch.setattr(radar_worker, "_run_theme_candidate_detection_step", _forbidden)
    monkeypatch.setitem(
        radar_worker._SERVICE_MODULES, provider_key,
        types.SimpleNamespace(run_scan=lambda settings, candidate_repository=None: _fake_report(1, 1)),
    )
    scan_status_repo = backend_factory.get_scan_status_repository(worker_settings)
    radar_worker._run_provider_tick(provider_key, worker_settings, scan_status_repo)
    out = capsys.readouterr().out
    assert "theme candidate detection" not in out
    assert scan_status_repo.get_scan_status(display_source) is not None


# ============================================================
# No evidence/publish side effects
# ============================================================


def test_never_creates_evidence_or_changes_visibility(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    candidates = _seed_cluster(worker_settings, [("TSMC", "2026-08-01"), ("Samsung", "2026-08-10")])
    _set_fixed_as_of_date(monkeypatch)

    radar_worker._run_theme_candidate_detection_step(worker_settings, candidates, ())

    curator = backend_factory.get_theme_curator_repository(worker_settings)
    theme_repo = backend_factory.get_theme_repository(worker_settings)
    themes = curator.list_themes()
    assert len(themes) == 1
    assert theme_repo.evidence_for_theme(themes[0].id) == ()
    assert theme_repo.list_published_themes() == ()
    assert themes[0].visibility is ThemeVisibility.INTERNAL
