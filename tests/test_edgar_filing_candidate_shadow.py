"""Daily News Filing-Event Shadow Adapter, Batch 2b — EDGAR pipeline/
service focused tests. Two layers:

1. Direct, pure unit tests of `_build_edgar_filing_candidate_shadow_report`
   against a hand-built `scan_service.ScanResult` — the right layer for
   the join/duplicate-key/exception/cap behaviors, since those never need
   a real EdgarClient or scan.
2. `run_pipeline`-level tests with `scan_service.scan` patched to return a
   fixed `ScanResult` (same technique already used to verify EDINET's two
   independent shadow features don't interfere) — the right layer for
   "the flag actually gates the whole feature" and "no other ScanReport
   field or behavior changes."

Zero network calls; candidate_repository is always omitted so the only
disk writes are edgar_pipeline's own pre-existing JSON candidate store
under tmp_path (unrelated to this batch)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from src.config.tracked_companies import TrackedCompany
from src.data_access.edgar import edgar_pipeline, scan_service
from src.models.models import CandidateSignal, CandidateStatus

_NVDA = TrackedCompany(
    name="NVIDIA", exchange="NASDAQ", krx_code="NVDA", source="SEC EDGAR",
    themes=("ai-buildout",), corp_code="0001045810",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _filing_event(rcept_no: str, pblntf_ty: str = "10-K", stock_code: str = "NVDA", corp_code: str = "0001045810"):
    return scan_service.FilingEvent(
        rcept_no=rcept_no,
        corp_code=corp_code,
        corp_name="NVIDIA",
        stock_code=stock_code,
        report_nm="Annual report",
        rcept_dt="2026-08-01",
        flr_nm="NVIDIA",
        pblntf_ty=pblntf_ty,
        source_name="SEC EDGAR",
        retrieved_at=_now(),
        primary_document="doc.htm",
    )


def _candidate_signal(filing, matched_rules=None) -> CandidateSignal:
    return CandidateSignal(
        id=f"edgar-cand-{filing.rcept_no}",
        filing=filing,
        matched_rules=list(matched_rules or []),
        confidence="High",
        status=CandidateStatus.CANDIDATE_DETECTED,
    )


def _scan_result(new_filing_events=(), new_candidate_signals=(), errors=()):
    scope = scan_service.ScanScope(
        bgn_date="2026-08-01", end_date="2026-08-31", lookback_days=30,
        companies=("NVIDIA",), source="SEC EDGAR", scanned_at=_now(),
    )
    return scan_service.ScanResult(
        scope=scope, new_filing_events=tuple(new_filing_events),
        new_candidate_signals=tuple(new_candidate_signals),
        already_seen_count=0, errors=tuple(errors), no_data_companies=(),
    )


# ============================================================
# 1. Pure unit tests of _build_edgar_filing_candidate_shadow_report
# ============================================================


def test_join_matches_by_natural_identity_never_by_list_position_with_partial_overlap_and_reordering():
    # Three filings; only two have a matching CandidateSignal, and the
    # signals list is both a strict subset AND in a different order from
    # new_filing_events — proving the join keys off
    # (source_name, corp_code, rcept_no), never index position.
    filing_10k = _filing_event("0001045810-26-000001", pblntf_ty="10-K")
    filing_10q = _filing_event("0001045810-26-000002", pblntf_ty="10-Q")
    filing_8k_no_signal = _filing_event("0001045810-26-000003", pblntf_ty="8-K")

    signal_for_10q = _candidate_signal(filing_10q, matched_rules=["earnings_or_results:10-Q"])
    signal_for_10k = _candidate_signal(filing_10k, matched_rules=["earnings_or_results:10-K"])

    scan_result = _scan_result(
        new_filing_events=(filing_10k, filing_10q, filing_8k_no_signal),
        # Deliberately reversed relative to new_filing_events, and
        # missing an entry for filing_8k_no_signal entirely.
        new_candidate_signals=(signal_for_10q, signal_for_10k),
    )

    candidates, diagnostics = edgar_pipeline._build_edgar_filing_candidate_shadow_report(scan_result)

    assert diagnostics == ()
    doc_ids = {c.doc_id for c in candidates}
    # 10-K and 10-Q both map (form-type based, matched_rules irrelevant
    # for these two types); the 8-K with no signal and no matched_rules
    # returns None from the adapter (ambiguous/absent 8-K item) and is
    # silently omitted — not an error, not a diagnostic.
    assert doc_ids == {"0001045810-26-000001", "0001045810-26-000002"}


def test_join_passes_the_correct_matched_rules_to_each_filing_not_a_neighbors():
    filing_a = _filing_event("0001045810-26-000010", pblntf_ty="8-K")
    filing_b = _filing_event("0001045810-26-000011", pblntf_ty="8-K")
    signal_a = _candidate_signal(filing_a, matched_rules=["earnings_or_results:8-K item 2.02"])
    signal_b = _candidate_signal(filing_b, matched_rules=["material_agreement:8-K item 1.01"])

    scan_result = _scan_result(
        new_filing_events=(filing_a, filing_b),
        new_candidate_signals=(signal_b, signal_a),  # reversed order
    )

    with patch.object(edgar_pipeline, "map_edgar_filing_to_candidate") as mapper:
        mapper.return_value = None
        edgar_pipeline._build_edgar_filing_candidate_shadow_report(scan_result)

    calls_by_rcept_no = {call.args[0].rcept_no: call.kwargs["matched_rules"] for call in mapper.call_args_list}
    assert calls_by_rcept_no["0001045810-26-000010"] == ("earnings_or_results:8-K item 2.02",)
    assert calls_by_rcept_no["0001045810-26-000011"] == ("material_agreement:8-K item 1.01",)


def test_duplicate_candidate_signal_key_never_last_write_wins_and_records_one_diagnostic():
    filing = _filing_event("0001045810-26-000099", pblntf_ty="8-K")
    duplicate_a = _candidate_signal(filing, matched_rules=["earnings_or_results:8-K item 2.02"])
    duplicate_b = _candidate_signal(filing, matched_rules=["material_agreement:8-K item 1.01"])

    scan_result = _scan_result(
        new_filing_events=(filing,),
        new_candidate_signals=(duplicate_a, duplicate_b),
    )

    with patch.object(edgar_pipeline, "map_edgar_filing_to_candidate") as mapper:
        mapper.return_value = None
        candidates, diagnostics = edgar_pipeline._build_edgar_filing_candidate_shadow_report(scan_result)

    assert diagnostics == ("0001045810-26-000099:DuplicateCandidateSignalKey",)
    # No arbitrary pick of either duplicate's matched_rules — the filing
    # is mapped with matched_rules=None (unresolved), never duplicate_a's
    # or duplicate_b's rules.
    mapper.assert_called_once_with(filing, matched_rules=None)
    assert candidates == ()


def test_per_filing_mapping_exception_is_isolated_sanitized_and_does_not_stop_remaining_filings():
    healthy_filing = _filing_event("0001045810-26-000001", pblntf_ty="10-K")
    failing_filing = _filing_event("0001045810-26-000002", pblntf_ty="10-Q")

    scan_result = _scan_result(new_filing_events=(healthy_filing, failing_filing))

    real_mapper = edgar_pipeline.map_edgar_filing_to_candidate

    def _flaky_mapper(filing, matched_rules=None):
        if filing.rcept_no == "0001045810-26-000002":
            raise ValueError("some raw internal detail that must never leak: password=hunter2")
        return real_mapper(filing, matched_rules=matched_rules)

    with patch.object(edgar_pipeline, "map_edgar_filing_to_candidate", side_effect=_flaky_mapper):
        candidates, diagnostics = edgar_pipeline._build_edgar_filing_candidate_shadow_report(scan_result)

    assert [c.doc_id for c in candidates] == ["0001045810-26-000001"]
    assert diagnostics == ("0001045810-26-000002:ValueError",)
    # Sanitized: exact "{rcept_no}:{ExceptionClassName}" shape only —
    # never the raw exception message.
    assert "password" not in diagnostics[0]
    assert "hunter2" not in diagnostics[0]


def test_diagnostics_are_capped_at_twenty_entries_per_run():
    filings = [_filing_event(f"0001045810-26-{i:06d}", pblntf_ty="10-K") for i in range(25)]
    scan_result = _scan_result(new_filing_events=tuple(filings))

    with patch.object(edgar_pipeline, "map_edgar_filing_to_candidate", side_effect=RuntimeError("boom")):
        candidates, diagnostics = edgar_pipeline._build_edgar_filing_candidate_shadow_report(scan_result)

    assert candidates == ()
    assert len(diagnostics) == 20
    assert all(d.endswith(":RuntimeError") for d in diagnostics)


def test_empty_scan_result_yields_empty_matches_and_diagnostics():
    candidates, diagnostics = edgar_pipeline._build_edgar_filing_candidate_shadow_report(_scan_result())
    assert candidates == ()
    assert diagnostics == ()


# ============================================================
# 2. run_pipeline-level tests: flag gating + no-behavior-change proof
# ============================================================


def test_flag_omitted_means_adapter_never_called_and_fields_are_empty(tmp_path):
    filing = _filing_event("0001045810-26-000001", pblntf_ty="10-K")
    scan_result = _scan_result(new_filing_events=(filing,))

    with patch.object(scan_service, "scan", return_value=scan_result):
        with patch.object(edgar_pipeline, "map_edgar_filing_to_candidate") as mapper:
            report = edgar_pipeline.run_pipeline(MagicMock(), [_NVDA], tmp_path)

    mapper.assert_not_called()
    assert report.filing_candidate_shadow_matches == ()
    assert report.filing_candidate_shadow_diagnostics == ()


def test_flag_false_means_adapter_never_called_and_fields_are_empty(tmp_path):
    filing = _filing_event("0001045810-26-000001", pblntf_ty="10-K")
    scan_result = _scan_result(new_filing_events=(filing,))

    with patch.object(scan_service, "scan", return_value=scan_result):
        with patch.object(edgar_pipeline, "map_edgar_filing_to_candidate") as mapper:
            report = edgar_pipeline.run_pipeline(MagicMock(), [_NVDA], tmp_path, filing_candidate_shadow_enabled=False)

    mapper.assert_not_called()
    assert report.filing_candidate_shadow_matches == ()
    assert report.filing_candidate_shadow_diagnostics == ()


def test_flag_true_yields_expected_in_memory_candidates_with_nothing_persisted(tmp_path):
    filing = _filing_event("0001045810-26-000001", pblntf_ty="10-K")
    scan_result = _scan_result(new_filing_events=(filing,))

    with patch.object(scan_service, "scan", return_value=scan_result):
        report = edgar_pipeline.run_pipeline(MagicMock(), [_NVDA], tmp_path, filing_candidate_shadow_enabled=True)

    assert [c.doc_id for c in report.filing_candidate_shadow_matches] == ["0001045810-26-000001"]
    assert report.filing_candidate_shadow_diagnostics == ()
    # Nothing about candidate persistence changed — no candidate was
    # detected by the (empty) new_candidate_signals, so the pre-existing
    # candidate store stays empty regardless of the shadow flag.
    from src.data_access.dart import candidate_store
    assert candidate_store.load_candidates(tmp_path, edgar_pipeline.CANDIDATE_STORE_FILENAME) == {}


def test_flag_true_changes_no_other_scan_report_field_versus_flag_false(tmp_path):
    filing = _filing_event("0001045810-26-000001", pblntf_ty="10-K")
    # Deliberately no new_candidate_signals — this test isolates the
    # shadow feature's effect on every OTHER ScanReport field, not the
    # (unrelated) candidate processing loop, which needs a real
    # EdgarClient/document_service double to exercise safely.
    scan_result = _scan_result(new_filing_events=(filing,), errors=("a warning",))

    with patch.object(scan_service, "scan", return_value=scan_result):
        report_off = edgar_pipeline.run_pipeline(MagicMock(), [_NVDA], tmp_path)
    with patch.object(scan_service, "scan", return_value=scan_result):
        report_on = edgar_pipeline.run_pipeline(MagicMock(), [_NVDA], tmp_path, filing_candidate_shadow_enabled=True)

    for field_name in (
        "companies", "source", "bgn_date", "end_date", "filings_discovered", "new_filing_events",
        "candidates_detected", "candidates_processed", "candidates_deferred", "documents_retrieved",
        "documents_extracted", "already_seen_count", "no_data_count", "errors_by_category", "cache_hits",
        "warnings",
    ):
        assert getattr(report_off, field_name) == getattr(report_on, field_name), field_name

    assert report_off.filing_candidate_shadow_matches == ()
    assert len(report_on.filing_candidate_shadow_matches) == 1
