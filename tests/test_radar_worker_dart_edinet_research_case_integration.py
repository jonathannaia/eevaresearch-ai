"""Autonomous Theme candidate detection, Phase 2 (design/DECISIONS.md)
— dedicated positive-path DART/EDINET Research Case creation tests via
`scripts/radar_worker.py::_run_source_research_case_step()`/
`_run_provider_tick()`. `tests/test_radar_worker_research_case_integration.py`
already proves the shared-pipeline wiring itself (proof2); this file
adds the DART/EDINET-specific coverage that phase's own spec calls for:
successful creation for each of DART and EDINET individually, the
correct per-source lookback/allowed_source_names config, and per-source
failure isolation in both directions (a DART failure never blocks
EDGAR/EDINET, and vice versa). Follows the existing file's own
conventions exactly: a real in-file SQLite backend, fake `run_scan`
service modules, no real worker process or live network call."""
from __future__ import annotations

import types

import pytest

from scripts import radar_worker
from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.dart import radar_service as dart_radar_service
from src.data_access.edinet import edinet_service
from src.logic import research_lead_orchestration
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, StateTransition

_SOURCE_CONFIG = {
    "dart": {"display_source": "OpenDART / DART", "lookback_module": dart_radar_service, "original_language": "Korean"},
    "edinet": {"display_source": "EDINET", "lookback_module": edinet_service, "original_language": "Japanese"},
}


def _ambient_settings(**overrides) -> Settings:
    fields = dict(
        radar_live_scan_enabled=True,
        radar_worker_db_backend="sqlite",
        radar_worker_state_db_path=None,
        radar_worker_state_db_url=None,
        edgar_auto_publish_enabled=False,
    )
    fields.update(overrides)
    return Settings(**fields)


def _worker_settings(tmp_path) -> Settings:
    ambient = _ambient_settings(radar_worker_db_backend="sqlite", radar_worker_state_db_path=tmp_path / "state.db")
    return radar_worker._build_worker_settings(ambient)


def _scan_status_repo(worker_settings):
    return backend_factory.get_scan_status_repository(worker_settings)


def _fake_report(candidates_detected=1, candidates_processed=1, end_date="2026-08-20"):
    return types.SimpleNamespace(
        candidates_detected=candidates_detected, candidates_processed=candidates_processed,
        warnings=(), end_date=end_date,
    )


def _source_candidate(provider_key, rcept_no, company="TSMC", rcept_dt="2026-08-15"):
    cfg = _SOURCE_CONFIG[provider_key]
    filing = FilingEvent(
        rcept_no=rcept_no, corp_code=f"code-{rcept_no}", corp_name=company, stock_code="X",
        report_nm="Material Disclosure", rcept_dt=rcept_dt, flr_nm=company, source_name=cfg["display_source"],
        source_url="https://example.com/filing", retrieved_at=rcept_dt + "T01:00:00+00:00",
        original_language=cfg["original_language"],
    )
    return CandidateSignal(
        id=f"{provider_key}-cand-{rcept_no}", filing=filing,
        matched_rules=["financing_or_debt:2.03", "material_agreement:1.01"],
        confidence="High", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="The company entered into a financing agreement.",
        state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at=rcept_dt + "T00:00:00+00:00")],
    )


def _seed_candidates(worker_settings, provider_key, *candidates):
    display_source = _SOURCE_CONFIG[provider_key]["display_source"]
    repo = backend_factory.get_candidate_repository(worker_settings, display_source)
    repo.upsert_new_candidates(list(candidates))


def _set_run_scan(monkeypatch, provider_key, report=None):
    report = report or _fake_report()
    monkeypatch.setitem(
        radar_worker._SERVICE_MODULES, provider_key,
        types.SimpleNamespace(run_scan=lambda settings, candidate_repository=None: report),
    )


def _set_fixed_as_of_date(monkeypatch, value="2026-08-20"):
    monkeypatch.setattr(radar_worker, "_current_utc_date", lambda: value)


# ============================================================
# Positive path — successful creation for DART and EDINET individually
# ============================================================


@pytest.mark.parametrize("provider_key", ["dart", "edinet"])
def test_successful_research_case_creation_via_provider_tick(tmp_path, monkeypatch, capsys, provider_key):
    worker_settings = _worker_settings(tmp_path)
    candidate = _source_candidate(provider_key, "acc-1")
    _seed_candidates(worker_settings, provider_key, candidate)
    _set_run_scan(monkeypatch, provider_key)
    _set_fixed_as_of_date(monkeypatch)
    scan_status_repo = _scan_status_repo(worker_settings)

    candidates, newly_created_cases = radar_worker._run_provider_tick(provider_key, worker_settings, scan_status_repo)

    out = capsys.readouterr().out
    assert f"{provider_key.upper()}: research cases — evaluated=1 created=1" in out
    assert candidate.id in candidates
    assert len(newly_created_cases) == 1

    research_case_repo = backend_factory.get_research_case_repository(worker_settings)
    cases = research_case_repo.list_recent_cases(10)
    assert len(cases) == 1
    assert cases[0].trigger_source_id == candidate.id


@pytest.mark.parametrize("provider_key", ["dart", "edinet"])
def test_idempotent_across_repeated_ticks(tmp_path, monkeypatch, capsys, provider_key):
    worker_settings = _worker_settings(tmp_path)
    candidate = _source_candidate(provider_key, "acc-1")
    _seed_candidates(worker_settings, provider_key, candidate)
    _set_run_scan(monkeypatch, provider_key)
    _set_fixed_as_of_date(monkeypatch)
    scan_status_repo = _scan_status_repo(worker_settings)

    radar_worker._run_provider_tick(provider_key, worker_settings, scan_status_repo)
    capsys.readouterr()
    radar_worker._run_provider_tick(provider_key, worker_settings, scan_status_repo)
    out = capsys.readouterr().out
    assert "created=0" in out
    assert "existing=1" in out

    research_case_repo = backend_factory.get_research_case_repository(worker_settings)
    assert len(research_case_repo.list_recent_cases(10)) == 1


# ============================================================
# Per-source config — correct lookback/allowed_source_names
# ============================================================


@pytest.mark.parametrize("provider_key", ["dart", "edinet"])
def test_uses_expected_per_source_config(tmp_path, monkeypatch, provider_key):
    worker_settings = _worker_settings(tmp_path)
    candidate = _source_candidate(provider_key, "acc-1")
    _seed_candidates(worker_settings, provider_key, candidate)
    _set_run_scan(monkeypatch, provider_key)
    _set_fixed_as_of_date(monkeypatch, "2026-08-20")
    scan_status_repo = _scan_status_repo(worker_settings)

    captured_config = {}
    real_prepare = research_lead_orchestration.prepare_research_case_bundles

    def _tracking_prepare(candidates, existing_case_ids, config):
        captured_config["config"] = config
        return real_prepare(candidates, existing_case_ids, config)

    monkeypatch.setattr(radar_worker, "prepare_research_case_bundles", _tracking_prepare)

    radar_worker._run_provider_tick(provider_key, worker_settings, scan_status_repo)

    config = captured_config["config"]
    display_source = _SOURCE_CONFIG[provider_key]["display_source"]
    lookback_module = _SOURCE_CONFIG[provider_key]["lookback_module"]
    assert config.allowed_source_names == (display_source,)
    assert config.max_candidates == 5
    assert config.lookback_days == lookback_module.scan_service.DEFAULT_LOOKBACK_DAYS
    assert config.as_of_date == "2026-08-20"


# ============================================================
# Per-source failure isolation, both directions
# ============================================================


_ORIGINAL_RUN_SOURCE_RESEARCH_CASE_STEP = radar_worker._run_source_research_case_step


def _raise_only_for(target_provider_key):
    def _fn(provider_key, *args, **kwargs):
        if provider_key == target_provider_key:
            raise RuntimeError("synthetic research-case step failure")
        return _ORIGINAL_RUN_SOURCE_RESEARCH_CASE_STEP(provider_key, *args, **kwargs)
    return _fn


def test_dart_research_step_failure_does_not_prevent_edgar_or_edinet(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    _set_run_scan(monkeypatch, "edgar")
    _set_run_scan(monkeypatch, "edinet")
    _set_run_scan(monkeypatch, "dart")
    monkeypatch.setattr(radar_worker, "_run_source_research_case_step", _raise_only_for("dart"))
    scan_status_repo = _scan_status_repo(worker_settings)

    radar_worker.run_one_tick(worker_settings, scan_status_repo)

    assert scan_status_repo.get_scan_status("SEC EDGAR").failure_code is None
    assert scan_status_repo.get_scan_status("EDINET").failure_code is None
    assert scan_status_repo.get_scan_status("OpenDART / DART").failure_code is None  # the scan itself still succeeded


def test_edinet_research_step_failure_does_not_prevent_edgar_or_dart(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    _set_run_scan(monkeypatch, "edgar")
    _set_run_scan(monkeypatch, "dart")
    _set_run_scan(monkeypatch, "edinet")
    monkeypatch.setattr(radar_worker, "_run_source_research_case_step", _raise_only_for("edinet"))
    scan_status_repo = _scan_status_repo(worker_settings)

    radar_worker.run_one_tick(worker_settings, scan_status_repo)

    assert scan_status_repo.get_scan_status("SEC EDGAR").failure_code is None
    assert scan_status_repo.get_scan_status("OpenDART / DART").failure_code is None
    assert scan_status_repo.get_scan_status("EDINET").failure_code is None  # the scan itself still succeeded
