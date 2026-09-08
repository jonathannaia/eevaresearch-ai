"""Autonomous Theme candidate detection, Phase 2 (design/DECISIONS.md)
— cross-market clustering tests for
`scripts/radar_worker.py::_run_theme_candidate_detection_step()`.
Proves the mixed-source qualifying case this phase's spec calls for (1
EDGAR + 1 DART + 1 EDINET company under the same theme_slug/
subtheme_slug firing one candidate Theme), plus the required duplicate/
issuer-identity safety properties: a company's own repeat filing is
never double-counted, and two different companies across sources are
never accidentally merged into one. `_run_theme_candidate_detection_step`/
`detect_theme_candidates` are entirely unchanged by this phase — company
identity here is exactly what it has always been, the literal
`candidate.filing.corp_name` string, deduplicated as a set with no
cross-source identity resolution of any kind (per the explicit "no
speculative cross-market issuer matching" instruction — this phase
deliberately does not build or use any issuer-registry merge). Follows
tests/test_radar_worker_theme_candidate_detection_integration.py's own
conventions exactly: real in-memory SQLite, no live scan, no worker
process."""
from __future__ import annotations

from scripts import radar_worker
from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.state_db import research_repository as sqlite_research_repository
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, StateTransition
from src.models.research_case import ResearchCase, ResearchCaseStatus
from src.models.theme_research import ThemeVisibility

_SOURCE_NAMES = {"edgar": "SEC EDGAR", "dart": "OpenDART / DART", "edinet": "EDINET"}


def _worker_settings(tmp_path) -> Settings:
    ambient = Settings(
        radar_live_scan_enabled=True, radar_worker_db_backend="sqlite",
        radar_worker_state_db_path=tmp_path / "state.db", radar_worker_state_db_url=None,
        edgar_auto_publish_enabled=False,
    )
    return radar_worker._build_worker_settings(ambient)


def _candidate(candidate_id, company, rcept_dt, provider_key, theme_slug="ai-buildout", subtheme_slug="compute-accelerators"):
    filing = FilingEvent(
        rcept_no=f"acc-{candidate_id}", corp_code=f"code-{candidate_id}", corp_name=company, stock_code="X",
        report_nm="Material Disclosure", rcept_dt=rcept_dt, flr_nm=company, source_name=_SOURCE_NAMES[provider_key],
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
        trigger_summary="material disclosure", title="t", research_question="q", status=ResearchCaseStatus.OPEN,
        created_at=rcept_dt + "T00:00:00+00:00", version=1,
    )


def _seed(worker_settings, candidate, case):
    conn = backend_factory._require_sqlite_connection(worker_settings)
    sqlite_research_repository.insert_research_case(conn, case)


def _seed_cross_market_cluster(worker_settings, entries, theme_slug="ai-buildout", subtheme_slug="compute-accelerators"):
    """`entries` is a sequence of (provider_key, company, rcept_dt,
    candidate_id) tuples — deliberately explicit ids rather than an
    auto-incrementing index, so a test can seed two candidates that
    intentionally share the same company name."""
    candidates = {}
    for i, (provider_key, company, rcept_dt, candidate_id) in enumerate(entries):
        case_id = f"case-{i}"
        candidate = _candidate(candidate_id, company, rcept_dt, provider_key, theme_slug, subtheme_slug)
        case = _case(case_id, candidate_id, company, rcept_dt)
        _seed(worker_settings, candidate, case)
        candidates[candidate_id] = candidate
    return candidates


def _set_fixed_as_of_date(monkeypatch, value="2026-09-01"):
    monkeypatch.setattr(radar_worker, "_current_utc_date", lambda: value)


# ============================================================
# Mixed-source qualifying cluster
# ============================================================


def test_one_edgar_one_dart_one_edinet_company_fires_one_theme(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    candidates = _seed_cross_market_cluster(worker_settings, [
        ("edgar", "Applied Materials", "2026-08-01", "edgar-cand-1"),
        ("dart", "Samsung Electronics", "2026-08-10", "dart-cand-1"),
        ("edinet", "Tokyo Electron", "2026-08-20", "edinet-cand-1"),
    ])
    _set_fixed_as_of_date(monkeypatch)

    summary = radar_worker._run_theme_candidate_detection_step(worker_settings, candidates, ())
    assert "clusters_detected=1" in summary
    assert "themes_created=1" in summary
    assert "matches_created=3" in summary
    assert "company_roles_created=3" in summary

    curator = backend_factory.get_theme_curator_repository(worker_settings)
    themes = curator.list_themes()
    assert len(themes) == 1
    theme = themes[0]
    assert theme.visibility is ThemeVisibility.INTERNAL

    company_map = curator.company_map_for_theme(theme.id)
    assert {entry.company_name for entry in company_map} == {
        "Applied Materials", "Samsung Electronics", "Tokyo Electron",
    }

    matching_repo = backend_factory.get_theme_matching_repository(worker_settings)
    pending = matching_repo.list_pending_matches()
    assert len(pending) == 3
    # Provenance: every member candidate's own filing.source_name is
    # preserved unchanged — this step never rewrites or merges source
    # attribution across providers.
    sources_seen = {c.filing.source_name for c in candidates.values()}
    assert sources_seen == {"SEC EDGAR", "OpenDART / DART", "EDINET"}


# ============================================================
# Duplicate/issuer-identity safety
# ============================================================


def test_same_company_repeat_filing_not_double_counted(tmp_path, monkeypatch):
    """Two DART filings from the literal same corp_name, plus one EDGAR
    and one EDINET company, must count as 3 distinct companies (not 4)
    — the repeat filing from 'Samsung Electronics' is deduplicated by
    the existing set-based company_names computation, unchanged by this
    phase."""
    worker_settings = _worker_settings(tmp_path)
    candidates = _seed_cross_market_cluster(worker_settings, [
        ("edgar", "Applied Materials", "2026-08-01", "edgar-cand-1"),
        ("dart", "Samsung Electronics", "2026-08-05", "dart-cand-1"),
        ("dart", "Samsung Electronics", "2026-08-10", "dart-cand-2"),
        ("edinet", "Tokyo Electron", "2026-08-20", "edinet-cand-1"),
    ])
    _set_fixed_as_of_date(monkeypatch)

    summary = radar_worker._run_theme_candidate_detection_step(worker_settings, candidates, ())
    assert "clusters_detected=1" in summary
    assert "themes_created=1" in summary

    curator = backend_factory.get_theme_curator_repository(worker_settings)
    theme = curator.list_themes()[0]
    company_map = curator.company_map_for_theme(theme.id)
    assert {entry.company_name for entry in company_map} == {
        "Applied Materials", "Samsung Electronics", "Tokyo Electron",
    }
    assert len(company_map) == 3


def test_two_distinct_companies_across_sources_never_merged_into_one(tmp_path, monkeypatch):
    """A Korean company and a Japanese company with genuinely distinct
    names must never be collapsed into a single company-map entry —
    this phase deliberately performs no cross-market issuer identity
    resolution at all, so two distinct literal names always remain two
    distinct companies."""
    worker_settings = _worker_settings(tmp_path)
    candidates = _seed_cross_market_cluster(worker_settings, [
        ("edgar", "Applied Materials", "2026-08-01", "edgar-cand-1"),
        ("dart", "Samsung Electronics", "2026-08-10", "dart-cand-1"),
        ("edinet", "Samsung Japan KK", "2026-08-20", "edinet-cand-1"),
    ])
    _set_fixed_as_of_date(monkeypatch)

    summary = radar_worker._run_theme_candidate_detection_step(worker_settings, candidates, ())
    assert "themes_created=1" in summary

    curator = backend_factory.get_theme_curator_repository(worker_settings)
    theme = curator.list_themes()[0]
    company_map = curator.company_map_for_theme(theme.id)
    names = {entry.company_name for entry in company_map}
    assert names == {"Applied Materials", "Samsung Electronics", "Samsung Japan KK"}
    assert len(names) == 3


def test_below_threshold_cross_market_cluster_creates_nothing(tmp_path, monkeypatch):
    """_THEME_CANDIDATE_DETECTION_MIN_COMPANIES is 2 (the cluster-
    detection threshold, unchanged by this phase and distinct from the
    auto-publish policy's own 3-company gate) — a single-company,
    single-source cluster must not qualify."""
    worker_settings = _worker_settings(tmp_path)
    candidates = _seed_cross_market_cluster(worker_settings, [
        ("edgar", "Applied Materials", "2026-08-01", "edgar-cand-1"),
    ])
    _set_fixed_as_of_date(monkeypatch)

    summary = radar_worker._run_theme_candidate_detection_step(worker_settings, candidates, ())
    assert "clusters_detected=0" in summary
    assert "themes_created=0" in summary

    curator = backend_factory.get_theme_curator_repository(worker_settings)
    assert curator.list_themes() == ()


def test_idempotent_across_repeated_ticks_for_cross_market_cluster(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    candidates = _seed_cross_market_cluster(worker_settings, [
        ("edgar", "Applied Materials", "2026-08-01", "edgar-cand-1"),
        ("dart", "Samsung Electronics", "2026-08-10", "dart-cand-1"),
        ("edinet", "Tokyo Electron", "2026-08-20", "edinet-cand-1"),
    ])
    _set_fixed_as_of_date(monkeypatch)

    first = radar_worker._run_theme_candidate_detection_step(worker_settings, candidates, ())
    second = radar_worker._run_theme_candidate_detection_step(worker_settings, candidates, ())
    assert "themes_created=1" in first
    assert "themes_created=0" in second
    assert "clusters_detected=0" in second

    curator = backend_factory.get_theme_curator_repository(worker_settings)
    assert len(curator.list_themes()) == 1
