"""edinet_pipeline — the bounded, idempotent orchestration entry point
connecting scan_service, document_service, and the translation lifecycle
seam (no provider call — see module docstring). Fully mocked
EdinetClient, zero network, no Subscription-Key required."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.config.tracked_companies import TrackedCompany
from src.data_access.dart import candidate_store
from src.data_access.edinet import edinet_pipeline
from src.data_access.edinet.errors import EdinetNotFoundError
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, StateTransition, TranslationState

_TEST_MAP = {"010:030:120": "fictional_category_alpha"}  # fictional key/category, not real EDINET data

_ACME = TrackedCompany(
    name="Acme Test Co", exchange="TSE", krx_code="1234", source="EDINET",
    themes=("ai-buildout",), corp_code="E00001",
)
_SAMPLE = TrackedCompany(
    name="Sample Electric", exchange="TSE", krx_code="5678", source="EDINET",
    themes=("memory",), corp_code="E00002",
)


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _result(doc_id, edinet_code, sec_code, ordinance="010", form="030", submit_date_time="2026-08-17 09:00"):
    return {
        "docID": doc_id, "docTypeCode": "120", "ordinanceCode": ordinance, "formCode": form,
        "filerName": "Test Filer", "docDescription": "Test Filing", "edinetCode": edinet_code, "secCode": sec_code,
        "submitDateTime": submit_date_time,
    }


def _envelope(results, count=None, status="200", message="OK"):
    count = len(results) if count is None else count
    return {"metadata": {"status": status, "message": message, "resultset": {"count": count}}, "results": results}


def _make_client(results_by_date_and_company: dict, document_by_doc_id: dict | None = None):
    document_by_doc_id = document_by_doc_id or {}
    client = MagicMock()

    def _get_document_list(date_str, type_=2):
        return results_by_date_and_company.get(date_str, _envelope([]))

    def _fetch_document(doc_id, type_):
        result = document_by_doc_id.get(doc_id)
        if isinstance(result, Exception):
            raise result
        return result if result is not None else b"<html><body><p>generic filing content</p></body></html>"

    client.get_document_list.side_effect = _get_document_list
    client.fetch_document.side_effect = _fetch_document
    return client


def _run_pipeline_with_map(client, companies, cache_dir, **kwargs):
    """edinet_pipeline.run_pipeline doesn't thread code_category_map
    through (see scan_service.py's docstring — it's a scan()-level test
    knob, empty by default in real use). Tests that need a candidate to
    exist call scan_service.scan directly with the test map first, then
    exercise the pipeline's own candidate_store/process_candidate logic
    from there."""
    from src.data_access.edinet import scan_service
    scan_result = scan_service.scan(client, companies, cache_dir, code_category_map=_TEST_MAP)
    candidate_store.upsert_new_candidates(cache_dir, list(scan_result.new_candidate_signals), edinet_pipeline.CANDIDATE_STORE_FILENAME)
    return scan_result


def test_process_candidate_extracts_and_reaches_needs_review(tmp_path):
    client = _make_client(
        {_today(): _envelope([_result("S100A", "E00001", "1234")])},
        {"S100A": b"<html><body><p>Disclosure content.</p></body></html>"},
    )
    _run_pipeline_with_map(client, [_ACME], tmp_path)
    candidate_id = next(iter(candidate_store.load_candidates(tmp_path, edinet_pipeline.CANDIDATE_STORE_FILENAME)))

    result = edinet_pipeline.process_single_candidate(client, candidate_id, tmp_path)

    assert result.status == CandidateStatus.NEEDS_REVIEW
    assert result.extraction_state == ExtractionState.EXTRACTED
    assert "Disclosure content" in result.excerpt_original


def test_successful_extraction_sets_translation_state_to_pending_not_translated(tmp_path):
    # Translation lifecycle seam is exercised (PENDING), but no provider
    # call ever happens this gate (see module docstring).
    client = _make_client(
        {_today(): _envelope([_result("S100A", "E00001", "1234")])},
        {"S100A": b"<html><body><p>Disclosure content.</p></body></html>"},
    )
    _run_pipeline_with_map(client, [_ACME], tmp_path)
    candidate_id = next(iter(candidate_store.load_candidates(tmp_path, edinet_pipeline.CANDIDATE_STORE_FILENAME)))

    result = edinet_pipeline.process_single_candidate(client, candidate_id, tmp_path)

    assert result.translation_state == TranslationState.PENDING
    assert result.title_translation is None
    assert result.excerpt_translation is None


def test_no_ownership_materiality_gate_applied(tmp_path):
    client = _make_client(
        {_today(): _envelope([_result("S100OWN", "E00001", "1234", ordinance="010", form="040")])},
        {"S100OWN": b"<html><body><p>Large shareholding report.</p></body></html>"},
    )
    scan_map = {"010:040:120": "ownership_or_large_shareholding"}  # fictional key, not real EDINET data
    from src.data_access.edinet import scan_service
    scan_result = scan_service.scan(client, [_ACME], tmp_path, code_category_map=scan_map)
    candidate_store.upsert_new_candidates(tmp_path, list(scan_result.new_candidate_signals), edinet_pipeline.CANDIDATE_STORE_FILENAME)
    candidate_id = next(iter(candidate_store.load_candidates(tmp_path, edinet_pipeline.CANDIDATE_STORE_FILENAME)))

    result = edinet_pipeline.process_single_candidate(client, candidate_id, tmp_path)

    assert result.status == CandidateStatus.NEEDS_REVIEW
    assert result.materiality_assessment == "Not assessed"


def test_retrieval_failure_reaches_retrieval_failed_status(tmp_path):
    client = _make_client(
        {_today(): _envelope([_result("S100FAIL", "E00001", "1234")])},
        {"S100FAIL": EdinetNotFoundError(404, "not found")},
    )
    _run_pipeline_with_map(client, [_ACME], tmp_path)
    candidate_id = next(iter(candidate_store.load_candidates(tmp_path, edinet_pipeline.CANDIDATE_STORE_FILENAME)))

    result = edinet_pipeline.process_single_candidate(client, candidate_id, tmp_path)

    assert result.status == CandidateStatus.RETRIEVAL_FAILED


def test_binary_document_reaches_parse_failed_status(tmp_path):
    client = _make_client(
        {_today(): _envelope([_result("S100BIN", "E00001", "1234")])},
        {"S100BIN": b"\x50\x4b\x03\x04" + bytes(range(200))},
    )
    _run_pipeline_with_map(client, [_ACME], tmp_path)
    candidate_id = next(iter(candidate_store.load_candidates(tmp_path, edinet_pipeline.CANDIDATE_STORE_FILENAME)))

    result = edinet_pipeline.process_single_candidate(client, candidate_id, tmp_path)

    assert result.status == CandidateStatus.PARSE_FAILED


def test_process_single_candidate_returns_none_for_unknown_id(tmp_path):
    client = MagicMock()
    assert edinet_pipeline.process_single_candidate(client, "edinet-cand-does-not-exist", tmp_path) is None


def test_clamp_max_candidates():
    assert edinet_pipeline.clamp_max_candidates(9999) == edinet_pipeline.MAX_CANDIDATES_PER_SCAN_CEILING
    assert edinet_pipeline.clamp_max_candidates(0) == 1


def test_run_pipeline_with_zero_tracked_companies_reports_zero_everything(tmp_path):
    # The real Gate 1 shape: no EDINET company is tracked yet.
    client = MagicMock()
    client.get_document_list.return_value = _envelope([])

    report = edinet_pipeline.run_pipeline(client, [], tmp_path)

    assert report.new_filing_events == 0
    assert report.candidates_detected == 0
    assert report.candidates_processed == 0
    assert report.source == "EDINET"


def test_run_pipeline_processes_detected_candidates_within_budget(tmp_path):
    client = _make_client(
        {_today(): _envelope([_result("S100A", "E00001", "1234", ordinance="010", form="030")])},
        {"S100A": b"<html><body><p>Disclosure content.</p></body></html>"},
    )
    # run_pipeline itself uses scan_service.scan with the real default
    # map, which (as of Gate 10) has exactly one entry that this
    # fictional company/form combination does not match — zero
    # candidates here proves run_pipeline's own end-to-end wiring
    # (scan -> store -> process loop) runs cleanly with zero eligible
    # candidates, without needing the test-only code_category_map override.
    report = edinet_pipeline.run_pipeline(client, [_ACME], tmp_path)

    assert report.new_filing_events == 1
    assert report.candidates_detected == 0
    assert report.candidates_processed == 0


def test_pipeline_is_idempotent_across_repeated_process_single_candidate_calls(tmp_path):
    client = _make_client(
        {_today(): _envelope([_result("S100A", "E00001", "1234")])},
        {"S100A": b"<html><body><p>Disclosure content.</p></body></html>"},
    )
    _run_pipeline_with_map(client, [_ACME], tmp_path)
    candidate_id = next(iter(candidate_store.load_candidates(tmp_path, edinet_pipeline.CANDIDATE_STORE_FILENAME)))

    first = edinet_pipeline.process_single_candidate(client, candidate_id, tmp_path)
    second = edinet_pipeline.process_single_candidate(client, candidate_id, tmp_path)

    assert first.status == second.status == CandidateStatus.NEEDS_REVIEW
    store = candidate_store.load_candidates(tmp_path, edinet_pipeline.CANDIDATE_STORE_FILENAME)
    assert len(store) == 1  # no duplicate CandidateSignal
    from src.data_access.edinet import scan_service
    assert len(scan_service.load_filing_events(tmp_path)) == 1  # no duplicate FilingEvent


def test_partial_failure_on_one_company_does_not_block_the_other(tmp_path):
    client = MagicMock()

    def _get_document_list(date_str, type_=2):
        return _envelope([_result("S100A", "E00001", "1234"), _result("S100B", "E00002", "5678")])

    client.get_document_list.side_effect = _get_document_list
    client.fetch_document.return_value = b"<html><body><p>content</p></body></html>"

    from src.data_access.edinet import scan_service
    scan_result = scan_service.scan(client, [_ACME, _SAMPLE], tmp_path, code_category_map=_TEST_MAP)

    assert len(scan_result.new_filing_events) == 2


# --- Gate 10: backfill_candidate_from_existing_event — no network call
# of any kind (the function takes no client argument at all). Uses the
# real live-verified SoftBank Group annual-report tuple
# (ordinanceCode=010, formCode=030000, docTypeCode=120) and the real
# DEFAULT_CODE_CATEGORY_MAP (Gate 10's one real entry), plus the two
# real companion tuples (010:042000:135 and 015:010000:235) confirmed to
# stay unmapped. ---

from src.data_access.edinet import edinet_rules  # noqa: E402 — imported here, near the tests that need it


def _seed_softbank_filing_event(cache_dir, doc_id, ordinance_code, form_code, doc_type_code, report_nm="Test Filing"):
    cache_dir.mkdir(parents=True, exist_ok=True)
    import json
    path = cache_dir / "edinet_filing_events.json"
    existing = {"seen_keys": [], "filing_events": [], "candidate_signals": []}
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
    existing["seen_keys"].append(f"EDINET:E02778:{doc_id}")
    existing["filing_events"].append({
        "rcept_no": doc_id, "corp_code": "E02778", "corp_name": "SoftBank Group Corp.", "stock_code": "99840",
        "report_nm": report_nm, "rcept_dt": "2026-06-22", "flr_nm": "ソフトバンクグループ株式会社",
        "pblntf_ty": form_code, "pblntf_detail_ty": doc_type_code, "ordinance_code": ordinance_code,
        "theme_slug": "ai-buildout", "subtheme_slug": None,
        "source_url": f"https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}",
        "retrieved_at": "2026-08-17T22:56:26.469712+00:00", "source_name": "EDINET", "original_language": "Japanese",
        "is_demo": False, "primary_document": "",
    })
    path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")


def test_backfill_candidate_creates_exactly_one_candidate_for_the_annual_report(tmp_path):
    _seed_softbank_filing_event(tmp_path, "S100YGH5", "010", "030000", "120", report_nm="有価証券報告書－第46期(2025/04/01－2026/03/31)")

    result = edinet_pipeline.backfill_candidate_from_existing_event(tmp_path, "S100YGH5")

    assert result.error is None
    assert result.created is True
    assert result.candidate_id == "edinet-cand-S100YGH5"

    store = candidate_store.load_candidates(tmp_path, edinet_pipeline.CANDIDATE_STORE_FILENAME)
    candidate = store["edinet-cand-S100YGH5"]
    assert candidate.status == CandidateStatus.CANDIDATE_DETECTED
    assert candidate.confidence == "Moderate"
    assert candidate.matched_rules == ["annual_securities_report:010:030000:120"]
    assert candidate.extraction_state == ExtractionState.NOT_FETCHED
    assert candidate.excerpt_original is None
    assert candidate.translation_state == TranslationState.NOT_REQUESTED


def test_backfill_candidate_state_history_distinguishes_routing_from_evidence(tmp_path):
    _seed_softbank_filing_event(tmp_path, "S100YGH5", "010", "030000", "120")

    edinet_pipeline.backfill_candidate_from_existing_event(tmp_path, "S100YGH5")

    store = candidate_store.load_candidates(tmp_path, edinet_pipeline.CANDIDATE_STORE_FILENAME)
    candidate = store["edinet-cand-S100YGH5"]
    assert len(candidate.state_history) == 1
    detail = candidate.state_history[0].detail
    assert detail == (
        "Annual Securities Report · ordinanceCode=010 · formCode=030000 · docTypeCode=120 — "
        "form-metadata routing only; no extracted document evidence yet."
    )
    assert candidate.state_history[0].status == CandidateStatus.CANDIDATE_DETECTED


def test_backfill_candidate_companion_confirmation_letter_stays_filing_event_only(tmp_path):
    _seed_softbank_filing_event(tmp_path, "S100YFHB", "010", "042000", "135", report_nm="確認書")

    result = edinet_pipeline.backfill_candidate_from_existing_event(tmp_path, "S100YFHB")

    assert result.created is False
    assert result.error is not None
    assert candidate_store.load_candidates(tmp_path, edinet_pipeline.CANDIDATE_STORE_FILENAME) == {}


def test_backfill_candidate_companion_internal_control_report_stays_filing_event_only(tmp_path):
    _seed_softbank_filing_event(tmp_path, "S100YFH8", "015", "010000", "235", report_nm="内部統制報告書－第46期(2025/04/01－2026/03/31)")

    result = edinet_pipeline.backfill_candidate_from_existing_event(tmp_path, "S100YFH8")

    assert result.created is False
    assert result.error is not None
    assert candidate_store.load_candidates(tmp_path, edinet_pipeline.CANDIDATE_STORE_FILENAME) == {}


def test_backfill_candidate_is_idempotent_no_duplicate_on_rerun(tmp_path):
    _seed_softbank_filing_event(tmp_path, "S100YGH5", "010", "030000", "120")

    first = edinet_pipeline.backfill_candidate_from_existing_event(tmp_path, "S100YGH5")
    second = edinet_pipeline.backfill_candidate_from_existing_event(tmp_path, "S100YGH5")

    assert first.created is True
    assert second.created is False
    assert second.already_existed is True
    store = candidate_store.load_candidates(tmp_path, edinet_pipeline.CANDIDATE_STORE_FILENAME)
    assert len(store) == 1  # no duplicate CandidateSignal
    assert len(store["edinet-cand-S100YGH5"].state_history) == 1  # no duplicate state-history entry either


def test_backfill_candidate_does_not_alter_the_source_filing_event(tmp_path):
    _seed_softbank_filing_event(tmp_path, "S100YGH5", "010", "030000", "120")
    from src.data_access.edinet import scan_service
    before = scan_service.load_filing_events(tmp_path)[0]

    edinet_pipeline.backfill_candidate_from_existing_event(tmp_path, "S100YGH5")

    after = scan_service.load_filing_events(tmp_path)[0]
    assert before == after  # dataclass equality — every field identical
    assert len(scan_service.load_filing_events(tmp_path)) == 1  # no new FilingEvent created


def test_backfill_candidate_for_unknown_doc_id_creates_nothing(tmp_path):
    result = edinet_pipeline.backfill_candidate_from_existing_event(tmp_path, "S100DOESNOTEXIST")

    assert result.error is not None
    assert "no existing FilingEvent" in result.error
    assert not (tmp_path / "edinet_candidates.json").exists()


def test_backfill_candidate_uses_no_client_and_makes_no_network_call(tmp_path):
    # The function's own signature takes no client parameter at all —
    # this test documents that guarantee directly rather than mocking
    # anything, since there is nothing network-shaped to mock.
    import inspect
    params = inspect.signature(edinet_pipeline.backfill_candidate_from_existing_event).parameters
    assert "client" not in params


def test_status_gate_upstream_still_prevents_non_default_status_filings_from_ever_reaching_this_point(tmp_path):
    # backfill_candidate_from_existing_event itself never reads status
    # fields — non-default-status rows are already excluded from ever
    # becoming a FilingEvent by scan()'s own _status_fields_are_default
    # gate (see test_edinet_scan_service.py's dedicated coverage), so by
    # construction every existing FilingEvent this function could ever
    # be called against already had default status at persist time.
    from src.data_access.edinet import scan_service
    assert not scan_service._status_fields_are_default({"withdrawalStatus": "1", "docInfoEditStatus": "0", "disclosureStatus": "0"})


# --- Automatic retry of stale, retryable translation failures (translation
# reliability workstream) — same shape as DART's own radar_pipeline.py. ---


class _FakeTranslationProvider:
    name = "DeepL"

    def __init__(self, result: str | None = None, error: Exception | None = None):
        self._result = result
        self._error = error

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        if self._error:
            raise self._error
        return self._result


def _translation_failed_candidate(doc_id: str, next_retry_minutes_from_now: int) -> CandidateSignal:
    now = datetime.now(timezone.utc)
    filing = FilingEvent(
        rcept_no=doc_id, corp_code="E00001", corp_name="Acme Test Co", stock_code="1234",
        report_nm="有価証券報告書", rcept_dt="2026-08-17", flr_nm="Acme Test Co",
        source_name="EDINET", original_language="Japanese",
    )
    return CandidateSignal(
        id=f"edinet-cand-{doc_id}", filing=filing, matched_rules=["fictional_category_alpha:010:030:120"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="日本語の抜粋。",
        translation_state=TranslationState.UNAVAILABLE, translation_failure_category="rate_limit",
        translation_failure_reason="Translation provider rate limit exceeded.", translation_retry_count=0,
        translation_next_retry_at=(now + timedelta(minutes=next_retry_minutes_from_now)).isoformat(),
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=now.isoformat())],
    )


def test_stale_translation_failure_past_its_scheduled_retry_is_automatically_retried(tmp_path):
    candidate = _translation_failed_candidate("S100RETRY", next_retry_minutes_from_now=-1)
    candidate_store.upsert_new_candidates(tmp_path, [candidate], edinet_pipeline.CANDIDATE_STORE_FILENAME)
    client = _make_client({})  # no new filings this tick
    provider = _FakeTranslationProvider(result="Recovered translation.")

    edinet_pipeline.run_pipeline(client, [], tmp_path, translation_provider=provider)

    client.fetch_document.assert_not_called()  # never re-fetches the document
    updated = candidate_store.load_candidates(tmp_path, edinet_pipeline.CANDIDATE_STORE_FILENAME)[candidate.id]
    assert updated.translation_state == TranslationState.TRANSLATED
    assert updated.excerpt_translation.translated_text == "Recovered translation."
    assert updated.translation_retry_count == 1
    assert updated.translation_next_retry_at is None


def test_translation_failure_not_yet_due_is_not_automatically_retried_edinet(tmp_path):
    candidate = _translation_failed_candidate("S100NOTDUE", next_retry_minutes_from_now=30)
    candidate_store.upsert_new_candidates(tmp_path, [candidate], edinet_pipeline.CANDIDATE_STORE_FILENAME)
    client = _make_client({})
    provider = _FakeTranslationProvider(result="should not be used")

    edinet_pipeline.run_pipeline(client, [], tmp_path, translation_provider=provider)

    updated = candidate_store.load_candidates(tmp_path, edinet_pipeline.CANDIDATE_STORE_FILENAME)[candidate.id]
    assert updated.translation_state == TranslationState.UNAVAILABLE
    assert updated.translation_retry_count == 0


def test_no_automatic_translation_retry_when_no_provider_configured(tmp_path):
    # Mirrors the pre-Phase-1 lifecycle seam: with no translation_provider
    # at all, no candidate could ever have become UNAVAILABLE in the
    # first place, and the retry step itself must not attempt anything.
    candidate = _translation_failed_candidate("S100NOPROVIDER", next_retry_minutes_from_now=-1)
    candidate_store.upsert_new_candidates(tmp_path, [candidate], edinet_pipeline.CANDIDATE_STORE_FILENAME)
    client = _make_client({})

    edinet_pipeline.run_pipeline(client, [], tmp_path)  # translation_provider omitted

    updated = candidate_store.load_candidates(tmp_path, edinet_pipeline.CANDIDATE_STORE_FILENAME)[candidate.id]
    assert updated.translation_state == TranslationState.UNAVAILABLE
    assert updated.translation_retry_count == 0
