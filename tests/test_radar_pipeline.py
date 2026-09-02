"""radar_pipeline.run_pipeline — the bounded, idempotent orchestration
entry point connecting scan_service, document_service, and
translation_service. Fully mocked DartClient + TranslationProvider, zero
network, no API key required."""
from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.config.tracked_companies import TrackedCompany
from src.data_access.dart import candidate_store, document_service, radar_pipeline, retry_policy
from src.data_access.dart.client import DisclosureRecord
from src.data_access.dart.errors import DartApiError, DartTimeoutError
from src.data_access.translation.interfaces import TranslationApiError
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, StateTransition, TranslationState

_SAMSUNG = TrackedCompany(
    name="Samsung Electronics", exchange="KRX", krx_code="005930", source="OpenDART / DART",
    themes=("memory", "ai-buildout"), corp_code="00126380",
)
_SK_HYNIX = TrackedCompany(
    name="SK Hynix", exchange="KRX", krx_code="000660", source="OpenDART / DART",
    themes=("memory", "ai-buildout"), corp_code="00164779",
)


def _record(rcept_no: str, report_nm: str, corp_code: str = "00126380", corp_name: str = "삼성전자", stock_code: str = "005930") -> DisclosureRecord:
    return DisclosureRecord(
        corp_cls="Y", corp_name=corp_name, corp_code=corp_code, stock_code=stock_code,
        report_nm=report_nm, rcept_no=rcept_no, flr_nm=corp_name, rcept_dt="20260810", rm="",
    )


def _valid_document_zip(body_text: str = "신규시설투자등 결정 안내") -> bytes:
    xml = (
        f'<?xml version="1.0" encoding="utf-8"?><DOCUMENT>'
        f"<SECTION-1><P>cover</P></SECTION-1><SECTION-1><P>{body_text}</P></SECTION-1></DOCUMENT>"
    ).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("doc.xml", xml)
    return buf.getvalue()


class _FakeTranslationProvider:
    name = "DeepL"

    def __init__(self, result: str = "translated text", error: Exception | None = None):
        self._result = result
        self._error = error
        self.call_count = 0

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        self.call_count += 1
        if self._error:
            raise self._error
        return self._result


def _make_client(search_by_corp: dict[str, dict], document_by_rcept: dict | None = None):
    """search_by_corp: {corp_code: {page_no: (records, total_count)}}.
    document_by_rcept: {rcept_no: bytes | Exception}; a missing key
    returns a valid, generic document."""
    document_by_rcept = document_by_rcept or {}
    client = MagicMock()

    def _search(corp_code, bgn_de, end_de, page_no=1, page_count=100):
        pages = search_by_corp.get(corp_code)
        if pages is None:
            raise DartApiError("013", "조회된 데이터가 없습니다.")
        return pages.get(page_no, ([], 0))

    def _fetch(rcept_no):
        result = document_by_rcept.get(rcept_no)
        if isinstance(result, Exception):
            raise result
        return result if result is not None else _valid_document_zip()

    client.search_disclosures.side_effect = _search
    client.fetch_document_zip.side_effect = _fetch
    return client


def test_full_successful_pipeline_processes_a_relevant_candidate(tmp_path):
    client = _make_client({"00126380": {1: ([_record("20260810000001", "신규시설투자등")], 1)}})
    provider = _FakeTranslationProvider(result="New facility investment decision")

    report = radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)

    assert report.new_filing_events == 1
    assert report.candidates_detected == 1
    assert report.candidates_processed == 1
    assert report.candidates_deferred == 0
    assert report.documents_retrieved == 1
    assert report.documents_extracted == 1
    assert report.translations_completed == 2  # title + excerpt

    store = candidate_store.load_candidates(tmp_path)
    candidate = store["cand-20260810000001"]
    assert candidate.status == CandidateStatus.NEEDS_REVIEW
    assert candidate.extraction_state == ExtractionState.EXTRACTED
    assert candidate.translation_state == TranslationState.TRANSLATED
    assert candidate.title_translation.translated_text == "New facility investment decision"
    assert candidate.excerpt_translation is not None


def test_no_filings_returns_clean_empty_report(tmp_path):
    client = _make_client({"00126380": {1: ([], 0)}})
    provider = _FakeTranslationProvider()

    report = radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)

    assert report.new_filing_events == 0
    assert report.candidates_detected == 0
    assert report.candidates_processed == 0
    assert report.no_data_count == 1
    assert report.errors_by_category == {}
    assert provider.call_count == 0


def test_filing_with_no_rule_match_becomes_filing_event_only(tmp_path):
    client = _make_client({"00126380": {1: ([_record("20260810000001", "임원ㆍ주요주주특정증권등소유상황보고서")], 1)}})
    provider = _FakeTranslationProvider()

    report = radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)

    assert report.new_filing_events == 1
    assert report.candidates_detected == 0
    assert report.candidates_processed == 0
    assert candidate_store.load_candidates(tmp_path) == {}
    assert provider.call_count == 0  # never touched — nothing to process


def test_candidate_deferred_when_scan_budget_exceeded(tmp_path):
    client = _make_client({"00126380": {1: (
        [_record("20260810000001", "신규시설투자등"), _record("20260810000002", "유상증자결정")], 2,
    )}})
    provider = _FakeTranslationProvider()

    report = radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path, max_candidates_to_process=1)

    assert report.candidates_detected == 2
    assert report.candidates_processed == 1
    assert report.candidates_deferred == 1

    statuses = {c.status for c in candidate_store.load_candidates(tmp_path).values()}
    assert CandidateStatus.PROCESSING_DEFERRED in statuses
    assert CandidateStatus.NEEDS_REVIEW in statuses


def test_deferred_candidate_is_picked_up_by_a_later_run(tmp_path):
    client = _make_client({"00126380": {1: (
        [_record("20260810000001", "신규시설투자등"), _record("20260810000002", "유상증자결정")], 2,
    )}})
    provider = _FakeTranslationProvider()

    radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path, max_candidates_to_process=1)
    second_report = radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path, max_candidates_to_process=1)

    # Nothing new discovered (both filings already seen), but the
    # previously-deferred candidate is now processed.
    assert second_report.candidates_detected == 0
    assert second_report.candidates_processed == 1
    assert second_report.candidates_deferred == 0

    statuses = {c.status for c in candidate_store.load_candidates(tmp_path).values()}
    assert statuses == {CandidateStatus.NEEDS_REVIEW}


def test_duplicate_scan_is_fully_idempotent(tmp_path):
    client = _make_client({"00126380": {1: ([_record("20260810000001", "신규시설투자등")], 1)}})
    provider = _FakeTranslationProvider()

    radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)
    second_report = radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)

    assert second_report.new_filing_events == 0
    assert second_report.candidates_detected == 0
    assert second_report.candidates_processed == 0
    assert second_report.already_seen_count == 1
    # Already NEEDS_REVIEW — a second run must not re-fetch or re-translate.
    assert client.fetch_document_zip.call_count == 1
    assert provider.call_count == 2  # title + excerpt, once each, ever


def test_partial_failure_on_one_company_does_not_block_the_other(tmp_path):
    client = MagicMock()

    def _search(corp_code, bgn_de, end_de, page_no=1, page_count=100):
        if corp_code == _SAMSUNG.corp_code:
            raise DartApiError("900", "Undefined error")
        return [_record("20260810000099", "신규시설투자등", corp_code=_SK_HYNIX.corp_code, corp_name="SK하이닉스", stock_code="000660")], 1

    client.search_disclosures.side_effect = _search
    client.fetch_document_zip.side_effect = lambda rcept_no: _valid_document_zip()
    provider = _FakeTranslationProvider()

    report = radar_pipeline.run_pipeline(client, provider, [_SAMSUNG, _SK_HYNIX], tmp_path)

    assert report.errors_by_category.get("scan_error") == 1
    assert any("Samsung" in w for w in report.warnings)
    assert report.candidates_processed == 1  # SK Hynix's candidate still went through


def test_retry_after_partial_failure_picks_up_the_previously_failed_company(tmp_path):
    client = MagicMock()
    samsung_should_fail = {"value": True}

    def _search(corp_code, bgn_de, end_de, page_no=1, page_count=100):
        if corp_code == _SAMSUNG.corp_code:
            if samsung_should_fail["value"]:
                raise DartApiError("900", "Undefined error")
            return [_record("20260810000001", "신규시설투자등")], 1
        return [_record("20260810000099", "유상증자결정", corp_code=_SK_HYNIX.corp_code, corp_name="SK하이닉스", stock_code="000660")], 1

    client.search_disclosures.side_effect = _search
    client.fetch_document_zip.side_effect = lambda rcept_no: _valid_document_zip()
    provider = _FakeTranslationProvider()

    first = radar_pipeline.run_pipeline(client, provider, [_SAMSUNG, _SK_HYNIX], tmp_path)
    samsung_should_fail["value"] = False
    second = radar_pipeline.run_pipeline(client, provider, [_SAMSUNG, _SK_HYNIX], tmp_path)

    assert first.errors_by_category.get("scan_error") == 1
    assert first.candidates_detected == 1  # SK Hynix only
    assert second.errors_by_category == {}
    assert second.candidates_detected == 1  # Samsung's, now that it works
    # SK Hynix's candidate from run 1 isn't reprocessed/duplicated in run 2.
    assert second.candidates_processed == 1


def test_document_retrieval_failure_sets_retrieval_failed_status(tmp_path):
    client = _make_client(
        {"00126380": {1: ([_record("20260810000001", "신규시설투자등")], 1)}},
        document_by_rcept={"20260810000001": DartTimeoutError("timed out")},
    )
    provider = _FakeTranslationProvider()

    report = radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)

    candidate = candidate_store.load_candidates(tmp_path)["cand-20260810000001"]
    assert candidate.status == CandidateStatus.RETRIEVAL_FAILED
    assert candidate.extraction_state == ExtractionState.RETRIEVAL_FAILED
    assert report.errors_by_category.get("retrieval_failed") == 1
    # Title translation still attempted even though the document failed.
    assert candidate.title_translation is not None


def test_extraction_failure_sets_parse_failed_status(tmp_path):
    client = _make_client(
        {"00126380": {1: ([_record("20260810000001", "신규시설투자등")], 1)}},
        document_by_rcept={"20260810000001": b"not a zip file"},
    )
    provider = _FakeTranslationProvider()

    report = radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)

    candidate = candidate_store.load_candidates(tmp_path)["cand-20260810000001"]
    assert candidate.status == CandidateStatus.PARSE_FAILED
    assert candidate.extraction_state == ExtractionState.PARSE_FAILED
    assert report.errors_by_category.get("parse_failed") == 1


def test_translation_failure_sets_translation_unavailable_but_review_still_proceeds(tmp_path):
    client = _make_client({"00126380": {1: ([_record("20260810000001", "신규시설투자등")], 1)}})
    provider = _FakeTranslationProvider(error=TranslationApiError("500", "provider down"))

    report = radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)

    candidate = candidate_store.load_candidates(tmp_path)["cand-20260810000001"]
    assert candidate.translation_state == TranslationState.UNAVAILABLE
    assert candidate.title_translation is None
    assert candidate.excerpt_translation is None
    # Korean extraction still succeeded, so the candidate still reaches
    # NEEDS_REVIEW — translation is a convenience layer, not a blocker.
    assert candidate.status == CandidateStatus.NEEDS_REVIEW
    assert report.errors_by_category.get("translation_unavailable") == 1


def test_cache_hit_is_not_re_fetched_or_re_extracted(tmp_path):
    client = _make_client({"00126380": {1: ([_record("20260810000001", "신규시설투자등")], 1)}})
    provider = _FakeTranslationProvider()

    # Pre-warm the document cache directly, simulating a document already
    # retrieved before this scan run.
    document_service.get_or_fetch_excerpt(client, "20260810000001", tmp_path)
    assert client.fetch_document_zip.call_count == 1

    report = radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)

    assert report.cache_hits == 1
    assert report.documents_retrieved == 0  # not counted again — it was a cache hit, not a fresh fetch
    assert client.fetch_document_zip.call_count == 1  # never called a second time


def test_state_transitions_are_recorded_in_order_for_a_successful_candidate(tmp_path):
    client = _make_client({"00126380": {1: ([_record("20260810000001", "신규시설투자등")], 1)}})
    provider = _FakeTranslationProvider()

    radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)

    candidate = candidate_store.load_candidates(tmp_path)["cand-20260810000001"]
    statuses = [t.status for t in candidate.state_history]
    assert statuses == [
        CandidateStatus.CANDIDATE_DETECTED,
        CandidateStatus.QUEUED_FOR_PROCESSING,
        CandidateStatus.RETRIEVAL_IN_PROGRESS,
        CandidateStatus.EXTRACTED,
        CandidateStatus.TRANSLATION_PENDING,
        CandidateStatus.NEEDS_REVIEW,
    ]
    assert all(t.at for t in candidate.state_history)  # every transition timestamped


def test_deferred_candidate_history_includes_a_deferral_detail(tmp_path):
    client = _make_client({"00126380": {1: (
        [_record("20260810000001", "신규시설투자등"), _record("20260810000002", "유상증자결정")], 2,
    )}})
    provider = _FakeTranslationProvider()

    radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path, max_candidates_to_process=1)

    deferred = [c for c in candidate_store.load_candidates(tmp_path).values() if c.status == CandidateStatus.PROCESSING_DEFERRED]
    assert len(deferred) == 1
    assert "budget" in deferred[0].state_history[-1].detail.lower()


def test_structured_scan_report_shape(tmp_path):
    client = _make_client({"00126380": {1: ([_record("20260810000001", "신규시설투자등")], 1)}})
    provider = _FakeTranslationProvider()

    report = radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)

    assert report.scan_id.startswith("scan-")
    assert report.started_at and report.completed_at
    assert report.companies == ("Samsung Electronics",)
    assert report.source == "OpenDART / DART"
    assert report.bgn_de and report.end_de
    assert isinstance(report.errors_by_category, dict)
    assert isinstance(report.warnings, tuple)


def test_max_lookback_is_clamped(tmp_path):
    client = _make_client({"00126380": {1: ([], 0)}})
    provider = _FakeTranslationProvider()

    report = radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path, lookback_days=9999)

    from src.data_access.dart import scan_service
    from datetime import datetime, timezone
    expected_days = scan_service.MAX_LOOKBACK_DAYS
    bgn = datetime.strptime(report.bgn_de, "%Y%m%d").date()
    end = datetime.strptime(report.end_de, "%Y%m%d").date()
    assert (end - bgn).days == expected_days


def test_max_candidates_to_process_is_clamped():
    assert radar_pipeline.clamp_max_candidates(9999) == radar_pipeline.MAX_CANDIDATES_PER_SCAN_CEILING
    assert radar_pipeline.clamp_max_candidates(0) == 1
    assert radar_pipeline.clamp_max_candidates(3) == 3


def test_max_candidates_to_process_is_clamped_within_run_pipeline(tmp_path):
    client = _make_client({"00126380": {1: (
        [_record(f"2026081000000{i}", "신규시설투자등") for i in range(3)], 3,
    )}})
    provider = _FakeTranslationProvider()

    report = radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path, max_candidates_to_process=9999)

    # Ceiling is 10, but only 3 candidates existed — all processed, no
    # artificial deferral, proving the clamp doesn't break normal runs.
    assert report.candidates_processed == 3
    assert report.candidates_deferred == 0


def test_process_single_candidate_processes_the_named_candidate(tmp_path):
    client = _make_client({"00126380": {1: ([_record("20260810000001", "신규시설투자등")], 1)}})
    provider = _FakeTranslationProvider(result="New facility investment decision")
    # A budget of 0 defers everything — the manual seam must still be
    # able to process a deferred candidate on demand.
    radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path, max_candidates_to_process=1)
    deferred_id = next(iter(candidate_store.load_candidates(tmp_path)))
    stored = candidate_store.load_candidates(tmp_path)[deferred_id]
    assert stored.status == CandidateStatus.NEEDS_REVIEW  # already processed by the run above

    # Force it back to PROCESSING_DEFERRED to exercise the on-demand path
    # independent of run_pipeline's own budget logic.
    stored.status = CandidateStatus.PROCESSING_DEFERRED
    candidate_store.update_candidate(tmp_path, stored)

    result = radar_pipeline.process_single_candidate(client, provider, deferred_id, tmp_path)

    assert result is not None
    assert result.status == CandidateStatus.NEEDS_REVIEW
    assert result.extraction_state == ExtractionState.EXTRACTED
    persisted = candidate_store.load_candidates(tmp_path)[deferred_id]
    assert persisted.status == CandidateStatus.NEEDS_REVIEW


def test_process_single_candidate_returns_none_for_unknown_id(tmp_path):
    client = _make_client({})
    provider = _FakeTranslationProvider()
    assert radar_pipeline.process_single_candidate(client, provider, "cand-does-not-exist", tmp_path) is None


def test_process_single_candidate_bypasses_eligible_status_filter(tmp_path):
    # RETRIEVAL_FAILED is not in _ELIGIBLE_STATUSES (run_pipeline never
    # auto-retries it) — process_single_candidate must still be able to
    # reprocess it, since that's exactly the manual-retry seam it exists for.
    client = _make_client({"00126380": {1: ([_record("20260810000001", "신규시설투자등")], 1)}})
    provider = _FakeTranslationProvider()
    radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)
    candidate_id = next(iter(candidate_store.load_candidates(tmp_path)))
    stored = candidate_store.load_candidates(tmp_path)[candidate_id]
    stored.status = CandidateStatus.RETRIEVAL_FAILED
    candidate_store.update_candidate(tmp_path, stored)

    result = radar_pipeline.process_single_candidate(client, provider, candidate_id, tmp_path)

    assert result is not None
    assert result.status in (CandidateStatus.NEEDS_REVIEW, CandidateStatus.RETRIEVAL_FAILED, CandidateStatus.PARSE_FAILED)


# --- Radar Calibration milestone: ownership materiality gate integration ---

_UNCHANGED_OWNERSHIP_EXCERPT = (
    "보유주식등의 수 및 보유비율 보유주식등의 수 보유비율 직전 보고서 1,151,410,032 19.69 "
    "이번 보고서 1,151,375,445 19.69"
)
_MATERIAL_OWNERSHIP_EXCERPT = (
    "보유주식등의 수 및 보유비율 보유주식등의 수 보유비율 직전 보고서 1,000,000,000 10.00 "
    "이번 보고서 1,010,000,000 10.20"
)
_MARKER_ONLY_EXCERPT = "최대주주변경 관련 상세 내용은 첨부 서류를 참조하시기 바랍니다."


def test_unchanged_ownership_candidate_is_demoted_to_not_material(tmp_path):
    client = _make_client(
        {"00126380": {1: ([_record("20260810000001", "주식등의대량보유상황보고서(일반)")], 1)}},
        document_by_rcept={"20260810000001": _valid_document_zip(_UNCHANGED_OWNERSHIP_EXCERPT)},
    )
    provider = _FakeTranslationProvider()

    report = radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)

    assert report.candidates_processed == 1
    candidate = next(iter(candidate_store.load_candidates(tmp_path).values()))
    assert candidate.status == CandidateStatus.NOT_MATERIAL
    assert candidate.materiality_assessment == "Not material · routine ownership update"


def test_material_ownership_change_reaches_needs_review_with_label(tmp_path):
    client = _make_client(
        {"00126380": {1: ([_record("20260810000002", "최대주주등소유주식변동신고서")], 1)}},
        document_by_rcept={"20260810000002": _valid_document_zip(_MATERIAL_OWNERSHIP_EXCERPT)},
    )
    provider = _FakeTranslationProvider()

    radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)

    candidate = next(iter(candidate_store.load_candidates(tmp_path).values()))
    assert candidate.status == CandidateStatus.NEEDS_REVIEW
    assert candidate.materiality_assessment == "Ownership change ≥ 0.05 percentage points"


def test_material_marker_reaches_needs_review_without_percentage_data(tmp_path):
    client = _make_client(
        {"00126380": {1: ([_record("20260810000003", "최대주주등소유주식변동신고서")], 1)}},
        document_by_rcept={"20260810000003": _valid_document_zip(_MARKER_ONLY_EXCERPT)},
    )
    provider = _FakeTranslationProvider()

    radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)

    candidate = next(iter(candidate_store.load_candidates(tmp_path).values()))
    assert candidate.status == CandidateStatus.NEEDS_REVIEW
    assert "Ownership change needs review" in candidate.materiality_assessment
    assert "최대주주변경" in candidate.materiality_assessment


def test_non_ownership_candidates_are_unaffected_by_the_materiality_gate(tmp_path):
    # Earnings, capex, financing, and rumor-response candidates must reach
    # NEEDS_REVIEW exactly as before this milestone, with the field left at
    # its neutral default — no regression from the ownership-only gate.
    client = _make_client({
        "00126380": {1: (
            [
                _record("20260810000004", "신규시설투자등"),  # capex
                _record("20260810000005", "연결재무제표기준영업(잠정)실적(공정공시)"),  # earnings
                _record("20260810000006", "유상증자결정"),  # financing
                _record("20260810000007", "풍문또는보도에대한해명"),  # market_rumor_response
            ],
            4,
        )},
    })
    provider = _FakeTranslationProvider()

    radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path, max_candidates_to_process=10)

    candidates = candidate_store.load_candidates(tmp_path)
    assert len(candidates) == 4
    for candidate in candidates.values():
        assert candidate.status == CandidateStatus.NEEDS_REVIEW
        assert candidate.materiality_assessment == "Not assessed"


# --- Automatic retry of stale RETRIEVAL_FAILED candidates (RETRIEVAL_FAILED
# only — PARSE_FAILED is deliberately never auto-retried, see
# retry_policy.py's module docstring) ---

_FIRST_BACKOFF, _SECOND_BACKOFF = retry_policy.AUTOMATIC_RETRY_BACKOFF_SCHEDULE_MINUTES


def _failed_candidate(rcept_no: str, minutes_ago: int, status: CandidateStatus = CandidateStatus.RETRIEVAL_FAILED, used: int = 1) -> CandidateSignal:
    now = datetime.now(timezone.utc)
    queued_ats = [now - timedelta(minutes=minutes_ago) - timedelta(seconds=i) for i in range(used)]
    history = [StateTransition(status=CandidateStatus.QUEUED_FOR_PROCESSING, at=at.isoformat()) for at in queued_ats]
    history.append(StateTransition(status=status, at=queued_ats[0].isoformat(), detail="DART status 014 rate-limit-shaped failure"))
    filing = FilingEvent(
        rcept_no=rcept_no, corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm="신규시설투자등", rcept_dt="20260810", flr_nm="삼성전자",
    )
    return CandidateSignal(
        id=f"cand-{rcept_no}", filing=filing,
        matched_rules=["capex_or_facility_investment:facility_investment:신규시설투자"],
        confidence="Moderate", status=status, state_history=history,
    )


def test_stale_retrieval_failed_candidate_is_automatically_retried(tmp_path):
    candidate = _failed_candidate("20260810000001", minutes_ago=_FIRST_BACKOFF + 1)
    candidate_store.upsert_new_candidates(tmp_path, [candidate])
    client = _make_client({"00126380": {1: ([], 0)}})  # no new filings this tick
    provider = _FakeTranslationProvider(result="translated")

    radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)

    client.fetch_document_zip.assert_called_once()
    updated = candidate_store.load_candidates(tmp_path)["cand-20260810000001"]
    assert updated.status == CandidateStatus.NEEDS_REVIEW  # the retry succeeded


def test_fresh_retrieval_failed_candidate_is_not_automatically_retried(tmp_path):
    candidate = _failed_candidate("20260810000001", minutes_ago=_FIRST_BACKOFF - 1)
    candidate_store.upsert_new_candidates(tmp_path, [candidate])
    client = _make_client({"00126380": {1: ([], 0)}})
    provider = _FakeTranslationProvider()

    radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)

    client.fetch_document_zip.assert_not_called()
    updated = candidate_store.load_candidates(tmp_path)["cand-20260810000001"]
    assert updated.status == CandidateStatus.RETRIEVAL_FAILED
    assert len(updated.state_history) == len(candidate.state_history)  # completely untouched


def test_stale_parse_failed_candidate_is_never_automatically_retried(tmp_path):
    # The key narrowing this phase: PARSE_FAILED must never be picked up
    # automatically, even when it is well past the first backoff window.
    candidate = _failed_candidate("20260810000001", minutes_ago=_FIRST_BACKOFF + 1000, status=CandidateStatus.PARSE_FAILED)
    candidate_store.upsert_new_candidates(tmp_path, [candidate])
    client = _make_client({"00126380": {1: ([], 0)}})
    provider = _FakeTranslationProvider()

    radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)

    client.fetch_document_zip.assert_not_called()
    updated = candidate_store.load_candidates(tmp_path)["cand-20260810000001"]
    assert updated.status == CandidateStatus.PARSE_FAILED
    assert len(updated.state_history) == len(candidate.state_history)


def test_second_automatic_retry_withheld_until_second_backoff_window(tmp_path):
    # used=2 (one automatic retry already happened) — must NOT retry
    # again just because _FIRST_BACKOFF has elapsed since the second
    # attempt; needs the longer _SECOND_BACKOFF instead. This is the
    # direct proof against "retrying every hourly tick indefinitely."
    candidate = _failed_candidate("20260810000001", minutes_ago=_FIRST_BACKOFF + 1, used=2)
    candidate_store.upsert_new_candidates(tmp_path, [candidate])
    client = _make_client({"00126380": {1: ([], 0)}})
    provider = _FakeTranslationProvider()

    radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)

    client.fetch_document_zip.assert_not_called()


def test_candidate_at_max_retry_attempts_is_never_automatically_retried(tmp_path):
    candidate = _failed_candidate(
        "20260810000001", minutes_ago=_SECOND_BACKOFF * retry_policy.MAX_RETRY_ATTEMPTS + 1000,
        used=retry_policy.MAX_RETRY_ATTEMPTS,
    )
    candidate_store.upsert_new_candidates(tmp_path, [candidate])
    client = _make_client({"00126380": {1: ([], 0)}})
    provider = _FakeTranslationProvider()

    radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)

    client.fetch_document_zip.assert_not_called()


def test_automatic_retry_cap_leaves_excess_stale_failures_completely_untouched(tmp_path):
    candidates = [_failed_candidate(f"2026081000000{i}", minutes_ago=_FIRST_BACKOFF + 1) for i in (1, 2, 3)]
    candidate_store.upsert_new_candidates(tmp_path, candidates)
    client = _make_client({"00126380": {1: ([], 0)}})
    provider = _FakeTranslationProvider()

    radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)

    assert client.fetch_document_zip.call_count == retry_policy.AUTOMATIC_RETRY_MAX_PER_TICK
    store = candidate_store.load_candidates(tmp_path)
    # len(state_history) == 2 (QUEUED_FOR_PROCESSING + RETRIEVAL_FAILED)
    # means this candidate was never picked up this tick at all.
    untouched = [c for c in store.values() if len(c.state_history) == 2]
    assert len(untouched) == 1
    # The untouched candidate must never be relabeled PROCESSING_DEFERRED —
    # it stays exactly RETRIEVAL_FAILED, byte-for-byte unchanged.
    assert untouched[0].status == CandidateStatus.RETRIEVAL_FAILED


def test_automatic_retry_budget_is_independent_of_new_candidate_processing_budget(tmp_path):
    stale = _failed_candidate("20260810000099", minutes_ago=_FIRST_BACKOFF + 1)
    candidate_store.upsert_new_candidates(tmp_path, [stale])
    client = _make_client({"00126380": {1: ([_record("20260810000001", "신규시설투자등")], 1)}})
    provider = _FakeTranslationProvider()

    report = radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path, max_candidates_to_process=1)

    # Unaffected by the stale failure also being retried this same tick.
    assert report.candidates_detected == 1
    assert report.candidates_processed == 1
    assert report.candidates_deferred == 0
    updated_stale = candidate_store.load_candidates(tmp_path)["cand-20260810000099"]
    assert updated_stale.status == CandidateStatus.NEEDS_REVIEW  # still got retried, just off-budget


def test_no_busy_loop_at_most_one_fetch_per_eligible_stale_candidate_per_tick(tmp_path):
    candidate = _failed_candidate("20260810000001", minutes_ago=_FIRST_BACKOFF + 1)
    candidate_store.upsert_new_candidates(tmp_path, [candidate])
    client = _make_client({"00126380": {1: ([], 0)}})
    provider = _FakeTranslationProvider()

    radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)
    radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)

    # Second tick: the candidate already recovered to NEEDS_REVIEW, so it
    # is no longer RETRIEVAL_FAILED and is never touched again.
    client.fetch_document_zip.assert_called_once()


def test_cached_successful_extraction_is_never_automatically_refetched_due_to_age(tmp_path):
    client = _make_client({"00126380": {1: ([_record("20260810000001", "신규시설투자등")], 1)}})
    provider = _FakeTranslationProvider()
    radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)
    assert client.fetch_document_zip.call_count == 1

    # Manually age the (real) EXTRACTED candidate's own last transition
    # far past every automatic-retry backoff window, then run more ticks.
    store = candidate_store.load_candidates(tmp_path)
    candidate = next(iter(store.values()))
    ancient = datetime.now(timezone.utc) - timedelta(days=30)
    candidate.state_history = [StateTransition(status=t.status, at=ancient.isoformat(), detail=t.detail) for t in candidate.state_history]
    candidate_store.update_candidate(tmp_path, candidate)

    radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)

    client.fetch_document_zip.assert_called_once()  # never called again


def test_manual_process_single_candidate_now_actually_refetches_a_stale_failure(tmp_path):
    # Reproduces the pre-fix bug directly: get_or_fetch_excerpt used to
    # always serve a cached failure regardless of caller intent, so the
    # manual "Retry processing" action never actually retried anything.
    client = _make_client(
        {"00126380": {1: ([_record("20260810000001", "신규시설투자등")], 1)}},
        document_by_rcept={"20260810000001": DartApiError("900", "Undefined error")},
    )
    provider = _FakeTranslationProvider(result="translated")
    radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)
    assert client.fetch_document_zip.call_count == 1
    stored = candidate_store.load_candidates(tmp_path)["cand-20260810000001"]
    assert stored.status == CandidateStatus.RETRIEVAL_FAILED

    # DART is now healthy — simulate a human clicking "Retry processing".
    client.fetch_document_zip.side_effect = lambda rcept_no: _valid_document_zip()
    result = radar_pipeline.process_single_candidate(client, provider, "cand-20260810000001", tmp_path)

    assert result.status == CandidateStatus.NEEDS_REVIEW
    assert result.extraction_state == ExtractionState.EXTRACTED
    assert client.fetch_document_zip.call_count == 2  # an actual second network attempt happened


# --- Automatic retry of stale, retryable translation failures (translation
# reliability workstream) — separately budgeted from the RETRIEVAL_FAILED
# retry above; re-attempts only the translation, never the document fetch. ---


def _translation_failed_candidate(rcept_no: str, next_retry_minutes_from_now: int) -> CandidateSignal:
    now = datetime.now(timezone.utc)
    filing = FilingEvent(
        rcept_no=rcept_no, corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm="신규시설투자등", rcept_dt="20260810", flr_nm="삼성전자",
    )
    return CandidateSignal(
        id=f"cand-{rcept_no}", filing=filing,
        matched_rules=["capex_or_facility_investment:facility_investment:신규시설투자"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="신규시설투자등 관련 원문",
        translation_state=TranslationState.UNAVAILABLE, translation_failure_category="rate_limit",
        translation_failure_reason="Translation provider rate limit exceeded.", translation_retry_count=0,
        translation_next_retry_at=(now + timedelta(minutes=next_retry_minutes_from_now)).isoformat(),
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=now.isoformat())],
    )


def test_stale_translation_failure_past_its_scheduled_retry_is_automatically_retried(tmp_path):
    candidate = _translation_failed_candidate("20260810000001", next_retry_minutes_from_now=-1)
    candidate_store.upsert_new_candidates(tmp_path, [candidate])
    client = _make_client({"00126380": {1: ([], 0)}})  # no new filings this tick
    provider = _FakeTranslationProvider(result="Recovered translation.")

    radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)

    client.fetch_document_zip.assert_not_called()  # never re-fetches the document
    updated = candidate_store.load_candidates(tmp_path)["cand-20260810000001"]
    assert updated.translation_state == TranslationState.TRANSLATED
    assert updated.excerpt_translation.translated_text == "Recovered translation."
    assert updated.translation_retry_count == 1
    assert updated.translation_next_retry_at is None
    assert updated.translation_failure_category is None


def test_translation_failure_not_yet_due_is_not_automatically_retried(tmp_path):
    candidate = _translation_failed_candidate("20260810000001", next_retry_minutes_from_now=30)
    candidate_store.upsert_new_candidates(tmp_path, [candidate])
    client = _make_client({"00126380": {1: ([], 0)}})
    provider = _FakeTranslationProvider(result="should not be used")

    radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)

    updated = candidate_store.load_candidates(tmp_path)["cand-20260810000001"]
    assert updated.translation_state == TranslationState.UNAVAILABLE  # completely untouched
    assert updated.translation_retry_count == 0


def test_automatic_translation_retry_continues_backoff_on_a_second_failure(tmp_path):
    candidate = _translation_failed_candidate("20260810000001", next_retry_minutes_from_now=-1)
    candidate_store.upsert_new_candidates(tmp_path, [candidate])
    client = _make_client({"00126380": {1: ([], 0)}})
    provider = _FakeTranslationProvider(error=TranslationApiError("429", "still limited"))

    radar_pipeline.run_pipeline(client, provider, [_SAMSUNG], tmp_path)

    updated = candidate_store.load_candidates(tmp_path)["cand-20260810000001"]
    assert updated.translation_state == TranslationState.UNAVAILABLE
    assert updated.translation_retry_count == 1  # one retry attempt was made
    assert updated.translation_next_retry_at is not None  # a further retry remains scheduled
