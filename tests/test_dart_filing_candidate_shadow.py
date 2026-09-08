"""Daily News Filing-Event Shadow Adapter, Batch 2b — DART pipeline/
service focused tests. Same two-layer approach as
test_edgar_filing_candidate_shadow.py: direct unit tests of
`_build_dart_filing_candidate_shadow_report` (no join needed for DART —
just one call per filing) plus `run_pipeline`-level tests with
`scan_service.scan` patched. Zero network calls, zero translation calls,
zero persistence beyond radar_pipeline's own pre-existing JSON candidate
store under tmp_path."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from src.config.tracked_companies import TrackedCompany
from src.data_access.dart import radar_pipeline, scan_service

_SAMSUNG = TrackedCompany(
    name="Samsung Electronics", exchange="KRX", krx_code="005930", source="OpenDART / DART",
    themes=("memory", "ai-buildout"), subthemes=("dram", "hbm"), corp_code="00126380",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _filing_event(rcept_no: str, report_nm: str = "분기보고서 (2026.06)", stock_code: str = "005930", corp_code: str = "00126380"):
    return scan_service.FilingEvent(
        rcept_no=rcept_no,
        corp_code=corp_code,
        corp_name="삼성전자",
        stock_code=stock_code,
        report_nm=report_nm,
        rcept_dt="2026-08-01",
        flr_nm="삼성전자",
        source_name="OpenDART / DART",
        retrieved_at=_now(),
    )


def _scan_result(new_filing_events=(), errors=()):
    scope = scan_service.ScanScope(
        bgn_de="20260801", end_de="20260831", lookback_days=30,
        companies=("Samsung Electronics",), source="OpenDART / DART", scanned_at=_now(),
    )
    return scan_service.ScanResult(
        scope=scope, new_filing_events=tuple(new_filing_events), new_candidate_signals=(),
        already_seen_count=0, errors=tuple(errors), no_data_companies=(),
    )


# ============================================================
# 1. Pure unit tests of _build_dart_filing_candidate_shadow_report
# ============================================================


def test_no_join_needed_maps_each_filing_independently():
    earnings_filing = _filing_event("20260801000001", report_nm="분기보고서 (2026.06)")
    scan_result = _scan_result(new_filing_events=(earnings_filing,))

    candidates, diagnostics = radar_pipeline._build_dart_filing_candidate_shadow_report(scan_result)

    assert diagnostics == ()
    assert [c.doc_id for c in candidates] == ["20260801000001"]
    assert candidates[0].company_name == "Samsung Electronics"


def test_per_filing_mapping_exception_is_isolated_sanitized_and_does_not_stop_remaining_filings():
    healthy_filing = _filing_event("20260801000001")
    failing_filing = _filing_event("20260801000002")
    scan_result = _scan_result(new_filing_events=(healthy_filing, failing_filing))

    real_mapper = radar_pipeline.map_dart_filing_to_candidate

    def _flaky_mapper(filing):
        if filing.rcept_no == "20260801000002":
            raise ValueError("raw internal detail: api_key=sk-secret-value")
        return real_mapper(filing)

    with patch.object(radar_pipeline, "map_dart_filing_to_candidate", side_effect=_flaky_mapper):
        candidates, diagnostics = radar_pipeline._build_dart_filing_candidate_shadow_report(scan_result)

    assert [c.doc_id for c in candidates] == ["20260801000001"]
    assert diagnostics == ("20260801000002:ValueError",)
    assert "api_key" not in diagnostics[0]
    assert "sk-secret-value" not in diagnostics[0]


def test_diagnostics_are_capped_at_twenty_entries_per_run():
    filings = [_filing_event(f"2026080100{i:04d}") for i in range(25)]
    scan_result = _scan_result(new_filing_events=tuple(filings))

    with patch.object(radar_pipeline, "map_dart_filing_to_candidate", side_effect=RuntimeError("boom")):
        candidates, diagnostics = radar_pipeline._build_dart_filing_candidate_shadow_report(scan_result)

    assert candidates == ()
    assert len(diagnostics) == 20


def test_empty_scan_result_yields_empty_matches_and_diagnostics():
    candidates, diagnostics = radar_pipeline._build_dart_filing_candidate_shadow_report(_scan_result())
    assert candidates == ()
    assert diagnostics == ()


# ============================================================
# 2. run_pipeline-level tests: flag gating + no-behavior-change proof
# ============================================================


def test_flag_omitted_means_adapter_never_called_and_fields_are_empty(tmp_path):
    filing = _filing_event("20260801000001")
    scan_result = _scan_result(new_filing_events=(filing,))
    translation_provider = MagicMock()

    with patch.object(scan_service, "scan", return_value=scan_result):
        with patch.object(radar_pipeline, "map_dart_filing_to_candidate") as mapper:
            report = radar_pipeline.run_pipeline(MagicMock(), translation_provider, [_SAMSUNG], tmp_path)

    mapper.assert_not_called()
    assert report.filing_candidate_shadow_matches == ()
    assert report.filing_candidate_shadow_diagnostics == ()
    translation_provider.translate.assert_not_called()


def test_flag_false_means_adapter_never_called_and_fields_are_empty(tmp_path):
    filing = _filing_event("20260801000001")
    scan_result = _scan_result(new_filing_events=(filing,))
    translation_provider = MagicMock()

    with patch.object(scan_service, "scan", return_value=scan_result):
        with patch.object(radar_pipeline, "map_dart_filing_to_candidate") as mapper:
            report = radar_pipeline.run_pipeline(
                MagicMock(), translation_provider, [_SAMSUNG], tmp_path, filing_candidate_shadow_enabled=False,
            )

    mapper.assert_not_called()
    assert report.filing_candidate_shadow_matches == ()
    assert report.filing_candidate_shadow_diagnostics == ()


def test_flag_true_yields_expected_in_memory_candidates_with_nothing_persisted(tmp_path):
    filing = _filing_event("20260801000001")
    scan_result = _scan_result(new_filing_events=(filing,))
    translation_provider = MagicMock()

    with patch.object(scan_service, "scan", return_value=scan_result):
        report = radar_pipeline.run_pipeline(
            MagicMock(), translation_provider, [_SAMSUNG], tmp_path, filing_candidate_shadow_enabled=True,
        )

    assert [c.doc_id for c in report.filing_candidate_shadow_matches] == ["20260801000001"]
    assert report.filing_candidate_shadow_diagnostics == ()
    translation_provider.translate.assert_not_called()
    from src.data_access.dart import candidate_store
    assert candidate_store.load_candidates(tmp_path) == {}


def test_one_mapper_failure_is_isolated_and_does_not_change_prior_pipeline_output(tmp_path):
    healthy = _filing_event("20260801000001")
    failing = _filing_event("20260801000002")
    scan_result = _scan_result(new_filing_events=(healthy, failing))
    translation_provider = MagicMock()

    real_mapper = radar_pipeline.map_dart_filing_to_candidate

    def _flaky_mapper(filing):
        if filing.rcept_no == "20260801000002":
            raise RuntimeError("boom")
        return real_mapper(filing)

    with patch.object(scan_service, "scan", return_value=scan_result):
        with patch.object(radar_pipeline, "map_dart_filing_to_candidate", side_effect=_flaky_mapper):
            report_on = radar_pipeline.run_pipeline(
                MagicMock(), translation_provider, [_SAMSUNG], tmp_path, filing_candidate_shadow_enabled=True,
            )
        with patch.object(radar_pipeline, "map_dart_filing_to_candidate", side_effect=_flaky_mapper):
            report_off = radar_pipeline.run_pipeline(MagicMock(), translation_provider, [_SAMSUNG], tmp_path)

    assert [c.doc_id for c in report_on.filing_candidate_shadow_matches] == ["20260801000001"]
    assert report_on.filing_candidate_shadow_diagnostics == ("20260801000002:RuntimeError",)
    # Every non-shadow field is identical between the failing-shadow-on
    # run and the shadow-off run — the mapping exception never touched
    # scan/candidate/processing behavior.
    for field_name in (
        "new_filing_events", "candidates_detected", "candidates_processed", "candidates_deferred",
        "documents_retrieved", "documents_extracted", "translations_completed", "already_seen_count",
        "no_data_count", "errors_by_category", "cache_hits", "warnings",
    ):
        assert getattr(report_on, field_name) == getattr(report_off, field_name), field_name
    translation_provider.translate.assert_not_called()
