"""Daily News Filing-Event Shadow Adapter, Batch 2b — EDINET pipeline/
service focused tests. EDINET is the one source with TWO independent
shadow features (the pre-existing Extraordinary Report
material_event_lexicon_enabled / shadow_material_event_matches, and this
batch's filing_candidate_shadow_enabled / filing_candidate_shadow_matches)
— most of this file's weight is proving they never interfere with each
other in any combination, on top of the same join-free unit/run_pipeline
coverage the DART file has."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from src.config.tracked_companies import TrackedCompany
from src.data_access.daily_news.filing_event_models import FilingCandidateStatus
from src.data_access.edinet import edinet_pipeline, scan_service

_SOFTBANK = TrackedCompany(
    name="SoftBank Group Corp.", exchange="TSE", krx_code="99840", source="EDINET",
    themes=("ai-buildout",), corp_code="E02778",
)

_SHARE_BUYBACK_TRIPLET = ("010", "170000", "220")  # edinet_rules.DEFAULT_CODE_CATEGORY_MAP's real share_buyback_status entry
_EXTRAORDINARY_REPORT_TRIPLET = ("010", "053000", "180")  # material_event_shadow.py's own real eligible triplet
_EXTRAORDINARY_REPORT_TITLE = "臨時報告書"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _filing_event(rcept_no: str, triplet, report_nm: str, stock_code: str = "99840", corp_code: str = "E02778"):
    ordinance_code, pblntf_ty, pblntf_detail_ty = triplet
    return scan_service.FilingEvent(
        rcept_no=rcept_no,
        corp_code=corp_code,
        corp_name="SoftBank Group Corp.",
        stock_code=stock_code,
        report_nm=report_nm,
        rcept_dt="2026-08-01",
        flr_nm="SoftBank Group Corp.",
        pblntf_ty=pblntf_ty,
        pblntf_detail_ty=pblntf_detail_ty,
        ordinance_code=ordinance_code,
        source_name="EDINET",
        retrieved_at=_now(),
    )


def _scan_result(new_filing_events=(), errors=()):
    scope = scan_service.ScanScope(
        bgn_date="2026-08-01", end_date="2026-08-31", lookback_days=30,
        companies=("SoftBank Group Corp.",), source="EDINET", scanned_at=_now(),
    )
    return scan_service.ScanResult(
        scope=scope, new_filing_events=tuple(new_filing_events), new_candidate_signals=(),
        already_seen_count=0, errors=tuple(errors), no_data_companies=(),
    )


# ============================================================
# 1. Pure unit tests of _build_edinet_filing_candidate_shadow_report
# ============================================================


def test_no_join_needed_maps_each_filing_independently():
    buyback_filing = _filing_event("S100AAA1", _SHARE_BUYBACK_TRIPLET, "自己株式の取得状況に関するお知らせ")
    scan_result = _scan_result(new_filing_events=(buyback_filing,))

    candidates, diagnostics = edinet_pipeline._build_edinet_filing_candidate_shadow_report(scan_result)

    assert diagnostics == ()
    assert [c.doc_id for c in candidates] == ["S100AAA1"]


def test_candidates_retain_shadow_status_and_no_official_document_url():
    buyback_filing = _filing_event("S100AAA1", _SHARE_BUYBACK_TRIPLET, "自己株式の取得状況に関するお知らせ")
    scan_result = _scan_result(new_filing_events=(buyback_filing,))

    candidates, _ = edinet_pipeline._build_edinet_filing_candidate_shadow_report(scan_result)

    assert len(candidates) == 1
    assert candidates[0].status == FilingCandidateStatus.SHADOW
    assert candidates[0].official_document_url is None


def test_per_filing_mapping_exception_is_isolated_sanitized_and_does_not_stop_remaining_filings():
    healthy = _filing_event("S100AAA1", _SHARE_BUYBACK_TRIPLET, "自己株式の取得状況に関するお知らせ")
    failing = _filing_event("S100AAA2", _SHARE_BUYBACK_TRIPLET, "自己株式の取得状況に関するお知らせ")
    scan_result = _scan_result(new_filing_events=(healthy, failing))

    real_mapper = edinet_pipeline.map_edinet_filing_to_candidate

    def _flaky_mapper(filing):
        if filing.rcept_no == "S100AAA2":
            raise ValueError("raw internal detail: subscription_key=sk-secret")
        return real_mapper(filing)

    with patch.object(edinet_pipeline, "map_edinet_filing_to_candidate", side_effect=_flaky_mapper):
        candidates, diagnostics = edinet_pipeline._build_edinet_filing_candidate_shadow_report(scan_result)

    assert [c.doc_id for c in candidates] == ["S100AAA1"]
    assert diagnostics == ("S100AAA2:ValueError",)
    assert "subscription_key" not in diagnostics[0]
    assert "sk-secret" not in diagnostics[0]


def test_diagnostics_are_capped_at_twenty_entries_per_run():
    filings = [_filing_event(f"S100AA{i:03d}", _SHARE_BUYBACK_TRIPLET, "自己株式の取得状況に関するお知らせ") for i in range(25)]
    scan_result = _scan_result(new_filing_events=tuple(filings))

    with patch.object(edinet_pipeline, "map_edinet_filing_to_candidate", side_effect=RuntimeError("boom")):
        candidates, diagnostics = edinet_pipeline._build_edinet_filing_candidate_shadow_report(scan_result)

    assert candidates == ()
    assert len(diagnostics) == 20


def test_empty_scan_result_yields_empty_matches_and_diagnostics():
    candidates, diagnostics = edinet_pipeline._build_edinet_filing_candidate_shadow_report(_scan_result())
    assert candidates == ()
    assert diagnostics == ()


# ============================================================
# 2. run_pipeline-level tests: flag gating
# ============================================================


def test_flag_omitted_means_adapter_never_called_and_fields_are_empty(tmp_path):
    filing = _filing_event("S100AAA1", _SHARE_BUYBACK_TRIPLET, "自己株式の取得状況に関するお知らせ")
    scan_result = _scan_result(new_filing_events=(filing,))

    with patch.object(scan_service, "scan", return_value=scan_result):
        with patch.object(edinet_pipeline, "map_edinet_filing_to_candidate") as mapper:
            report = edinet_pipeline.run_pipeline(MagicMock(), [_SOFTBANK], tmp_path)

    mapper.assert_not_called()
    assert report.filing_candidate_shadow_matches == ()
    assert report.filing_candidate_shadow_diagnostics == ()


def test_flag_false_means_adapter_never_called_and_fields_are_empty(tmp_path):
    filing = _filing_event("S100AAA1", _SHARE_BUYBACK_TRIPLET, "自己株式の取得状況に関するお知らせ")
    scan_result = _scan_result(new_filing_events=(filing,))

    with patch.object(scan_service, "scan", return_value=scan_result):
        with patch.object(edinet_pipeline, "map_edinet_filing_to_candidate") as mapper:
            report = edinet_pipeline.run_pipeline(
                MagicMock(), [_SOFTBANK], tmp_path, filing_candidate_shadow_enabled=False,
            )

    mapper.assert_not_called()
    assert report.filing_candidate_shadow_matches == ()
    assert report.filing_candidate_shadow_diagnostics == ()


def test_flag_true_yields_expected_in_memory_candidates_with_nothing_persisted(tmp_path):
    filing = _filing_event("S100AAA1", _SHARE_BUYBACK_TRIPLET, "自己株式の取得状況に関するお知らせ")
    scan_result = _scan_result(new_filing_events=(filing,))

    with patch.object(scan_service, "scan", return_value=scan_result):
        report = edinet_pipeline.run_pipeline(MagicMock(), [_SOFTBANK], tmp_path, filing_candidate_shadow_enabled=True)

    assert [c.doc_id for c in report.filing_candidate_shadow_matches] == ["S100AAA1"]
    assert report.filing_candidate_shadow_diagnostics == ()
    from src.data_access.dart import candidate_store
    assert candidate_store.load_candidates(tmp_path, edinet_pipeline.CANDIDATE_STORE_FILENAME) == {}


def test_no_generic_edinet_portal_fallback_url_is_ever_added():
    filing = _filing_event("S100AAA1", _SHARE_BUYBACK_TRIPLET, "自己株式の取得状況に関するお知らせ")
    scan_result = _scan_result(new_filing_events=(filing,))

    candidates, _ = edinet_pipeline._build_edinet_filing_candidate_shadow_report(scan_result)

    assert len(candidates) == 1
    assert candidates[0].official_document_url is None
    assert candidates[0].official_document_url != "https://disclosure2.edinet-fsa.go.jp/"


# ============================================================
# 3. Independence from the existing material_event_lexicon_enabled /
#    shadow_material_event_matches feature — every flag combination
# ============================================================


def _extraordinary_report_filing(rcept_no: str = "S100EXR1"):
    return _filing_event(rcept_no, _EXTRAORDINARY_REPORT_TRIPLET, _EXTRAORDINARY_REPORT_TITLE)


def test_both_flags_off_yields_both_shadow_fields_empty(tmp_path):
    filing = _extraordinary_report_filing()
    scan_result = _scan_result(new_filing_events=(filing,))

    with patch.object(scan_service, "scan", return_value=scan_result):
        report = edinet_pipeline.run_pipeline(MagicMock(), [_SOFTBANK], tmp_path)

    assert report.shadow_material_event_matches == ()
    assert report.filing_candidate_shadow_matches == ()
    assert report.filing_candidate_shadow_diagnostics == ()


def test_only_filing_candidate_shadow_enabled_leaves_material_event_shadow_untouched(tmp_path):
    filing = _extraordinary_report_filing()
    scan_result = _scan_result(new_filing_events=(filing,))

    with patch.object(scan_service, "scan", return_value=scan_result):
        report = edinet_pipeline.run_pipeline(
            MagicMock(), [_SOFTBANK], tmp_path,
            material_event_lexicon_enabled=False, filing_candidate_shadow_enabled=True,
        )

    assert report.shadow_material_event_matches == ()
    assert [c.doc_id for c in report.filing_candidate_shadow_matches] == ["S100EXR1"]


def test_only_material_event_lexicon_enabled_leaves_filing_candidate_shadow_untouched(tmp_path):
    filing = _extraordinary_report_filing()
    scan_result = _scan_result(new_filing_events=(filing,))

    with patch.object(scan_service, "scan", return_value=scan_result):
        report = edinet_pipeline.run_pipeline(
            MagicMock(), [_SOFTBANK], tmp_path,
            material_event_lexicon_enabled=True, filing_candidate_shadow_enabled=False,
        )

    assert len(report.shadow_material_event_matches) == 1
    assert report.filing_candidate_shadow_matches == ()
    assert report.filing_candidate_shadow_diagnostics == ()


def test_both_flags_on_populates_both_independently_from_the_same_filing(tmp_path):
    filing = _extraordinary_report_filing()
    scan_result = _scan_result(new_filing_events=(filing,))

    with patch.object(scan_service, "scan", return_value=scan_result):
        report = edinet_pipeline.run_pipeline(
            MagicMock(), [_SOFTBANK], tmp_path,
            material_event_lexicon_enabled=True, filing_candidate_shadow_enabled=True,
        )

    assert len(report.shadow_material_event_matches) == 1
    assert [c.doc_id for c in report.filing_candidate_shadow_matches] == ["S100EXR1"]
    assert report.filing_candidate_shadow_matches[0].status == FilingCandidateStatus.SHADOW
    assert report.filing_candidate_shadow_matches[0].official_document_url is None


def test_filing_candidate_shadow_failure_does_not_affect_material_event_shadow_results(tmp_path):
    filing = _extraordinary_report_filing()
    scan_result = _scan_result(new_filing_events=(filing,))

    with patch.object(scan_service, "scan", return_value=scan_result):
        with patch.object(edinet_pipeline, "map_edinet_filing_to_candidate", side_effect=RuntimeError("boom")):
            report = edinet_pipeline.run_pipeline(
                MagicMock(), [_SOFTBANK], tmp_path,
                material_event_lexicon_enabled=True, filing_candidate_shadow_enabled=True,
            )

    assert len(report.shadow_material_event_matches) == 1
    assert report.filing_candidate_shadow_matches == ()
    assert report.filing_candidate_shadow_diagnostics == ("S100EXR1:RuntimeError",)
