"""edinet_scan_service — real `{metadata, results}` envelope validation
(shape confirmed live in Gate 4, 2026-08-17), per-day error isolation,
conservative edinetCode-only company matching, submitDateTime-derived
filing dates, and deferred routing for unconfirmed status values. Fully
mocked EdinetClient; zero network calls, no Subscription-Key required.
Every value below is fictional test data except where explicitly noted
as the real Gate 2/Gate 4 SoftBank Group identifiers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.config.tracked_companies import TrackedCompany
from src.data_access.edinet import scan_service
from src.data_access.edinet.errors import EdinetApiError, EdinetTimeoutError

_TEST_MAP = {"010:030:120": "fictional_category_alpha"}  # fictional key/category, not real EDINET data

_ACME = TrackedCompany(
    name="Acme Test Co", exchange="TSE", krx_code="1234", source="EDINET",
    themes=("ai-buildout",), corp_code="E00001",
)
_UNRESOLVED = TrackedCompany(
    name="Unresolved Co", exchange="TSE", krx_code="9999", source="EDINET",
    themes=("ai-buildout",), corp_code=None,
)
# Real identifiers confirmed live (Gate 2/Gate 4) — used only to prove
# the matcher works against the real shape, not a claim that this data
# was itself retrieved live in this gate.
_SOFTBANK = TrackedCompany(
    name="SoftBank Group Corp.", exchange="TSE", krx_code="9984", source="EDINET",
    themes=("ai-buildout",), corp_code="E02778",
)


def _result(
    doc_id="S100TEST1", edinet_code="E00001", sec_code="1234", ordinance="010", form="030",
    submit_date_time="2026-08-17 09:00", issuer_edinet_code="", withdrawal_status="",
    doc_info_edit_status="", disclosure_status="",
):
    return {
        "docID": doc_id, "docTypeCode": "120", "ordinanceCode": ordinance, "formCode": form,
        "filerName": "Acme Test Co", "docDescription": "Test Filing", "edinetCode": edinet_code, "secCode": sec_code,
        "submitDateTime": submit_date_time, "issuerEdinetCode": issuer_edinet_code,
        "withdrawalStatus": withdrawal_status, "docInfoEditStatus": doc_info_edit_status, "disclosureStatus": disclosure_status,
    }


def _envelope(results, count=None, status="200", message="OK"):
    count = len(results) if count is None else count
    return {"metadata": {"status": status, "message": message, "resultset": {"count": count}}, "results": results}


def _client(results_by_date: dict) -> MagicMock:
    client = MagicMock()

    def _get_document_list(date_str, type_=2):
        return results_by_date.get(date_str, _envelope([]))

    client.get_document_list.side_effect = _get_document_list
    return client


# --- normalize_document_list: real envelope validation ---

def test_normalize_document_list_flattens_valid_results():
    payload = _envelope([_result()])
    rows, warnings = scan_service.normalize_document_list(payload)
    assert warnings == ()
    assert len(rows) == 1
    assert rows[0]["docID"] == "S100TEST1"


def test_normalize_document_list_preserves_all_optional_fields():
    payload = _envelope([_result(issuer_edinet_code="E99999")])
    rows, _ = scan_service.normalize_document_list(payload)
    assert rows[0]["submitDateTime"] == "2026-08-17 09:00"
    assert rows[0]["issuerEdinetCode"] == "E99999"


def test_normalize_document_list_drops_rows_missing_a_required_field():
    entry = _result()
    del entry["formCode"]
    payload = _envelope([entry])
    rows, warnings = scan_service.normalize_document_list(payload)
    assert rows == []


def test_normalize_document_list_non_dict_payload_fails_safely():
    rows, warnings = scan_service.normalize_document_list(["not", "a", "dict"])
    assert rows == []
    assert warnings


def test_normalize_document_list_missing_metadata_fails_safely():
    rows, warnings = scan_service.normalize_document_list({"results": []})
    assert rows == []
    assert "metadata" in warnings[0]


def test_normalize_document_list_malformed_metadata_fails_safely():
    rows, warnings = scan_service.normalize_document_list({"metadata": "not a dict", "results": []})
    assert rows == []
    assert "metadata" in warnings[0]


def test_normalize_document_list_non_success_status_fails_safely():
    payload = {"metadata": {"status": "404", "message": "Not Found"}, "results": []}
    rows, warnings = scan_service.normalize_document_list(payload)
    assert rows == []
    assert "non-success status" in warnings[0]


def test_normalize_document_list_missing_status_fails_safely():
    payload = {"metadata": {"message": "OK"}, "results": []}
    rows, warnings = scan_service.normalize_document_list(payload)
    assert rows == []


def test_normalize_document_list_missing_results_key_fails_safely():
    payload = {"metadata": {"status": "200", "message": "OK"}}
    rows, warnings = scan_service.normalize_document_list(payload)
    assert rows == []
    assert "results" in warnings[0]


def test_normalize_document_list_non_list_results_fails_safely():
    payload = {"metadata": {"status": "200", "message": "OK"}, "results": "not a list"}
    rows, warnings = scan_service.normalize_document_list(payload)
    assert rows == []


def test_normalize_document_list_empty_results_with_success_status_is_not_a_warning():
    payload = _envelope([])
    rows, warnings = scan_service.normalize_document_list(payload)
    assert rows == []
    assert warnings == ()


def test_normalize_document_list_result_count_mismatch_fails_safely():
    payload = _envelope([_result()], count=99)
    rows, warnings = scan_service.normalize_document_list(payload)
    assert rows == []
    assert "declared 99" in warnings[0]


def test_normalize_document_list_malformed_result_count_fails_safely():
    payload = {"metadata": {"status": "200", "message": "OK", "resultset": {"count": "not-a-number"}}, "results": [_result()]}
    rows, warnings = scan_service.normalize_document_list(payload)
    assert rows == []
    assert "malformed" in warnings[0]


def test_normalize_document_list_missing_resultset_count_skips_the_check():
    payload = {"metadata": {"status": "200", "message": "OK"}, "results": [_result()]}
    rows, warnings = scan_service.normalize_document_list(payload)
    assert len(rows) == 1
    assert warnings == ()


def test_normalize_document_list_skips_non_dict_entries():
    payload = _envelope([_result(), "not-a-dict", 42], count=3)
    rows, warnings = scan_service.normalize_document_list(payload)
    assert len(rows) == 1


# --- _derive_filing_date ---

def test_derive_filing_date_uses_submit_date_time_when_present():
    date_str, reason = scan_service._derive_filing_date({"submitDateTime": "2026-08-17 09:00"}, "2026-01-01")
    assert date_str == "2026-08-17"
    assert reason == "submitDateTime"


def test_derive_filing_date_falls_back_when_submit_date_time_missing():
    date_str, reason = scan_service._derive_filing_date({"submitDateTime": ""}, "2026-01-01")
    assert date_str == "2026-01-01"
    assert "missing" in reason


def test_derive_filing_date_falls_back_when_submit_date_time_unparsable():
    date_str, reason = scan_service._derive_filing_date({"submitDateTime": "not-a-date"}, "2026-01-01")
    assert date_str == "2026-01-01"
    assert "unparsable" in reason


# --- _status_fields_are_default (Gate 6.1: "0" is confirmed live —
# docID S100YGH5, a real, ordinary, fully disclosed SoftBank Group
# annual securities report — as the standard/normal value, not just
# empty/missing) ---

def test_status_fields_are_default_when_all_empty():
    assert scan_service._status_fields_are_default(_result())


def test_status_fields_are_default_when_all_missing_from_dict():
    assert scan_service._status_fields_are_default({})


def test_status_fields_are_default_when_all_explicitly_none():
    assert scan_service._status_fields_are_default(
        _result(withdrawal_status=None, doc_info_edit_status=None, disclosure_status=None)
    )


def test_status_fields_are_default_when_all_the_string_zero():
    assert scan_service._status_fields_are_default(
        _result(withdrawal_status="0", doc_info_edit_status="0", disclosure_status="0")
    )


def test_status_fields_are_default_false_when_withdrawal_status_is_one():
    assert not scan_service._status_fields_are_default(_result(withdrawal_status="1"))


def test_status_fields_are_default_false_when_doc_info_edit_status_is_one():
    assert not scan_service._status_fields_are_default(_result(doc_info_edit_status="1"))


def test_status_fields_are_default_false_when_disclosure_status_is_one():
    assert not scan_service._status_fields_are_default(_result(disclosure_status="1"))


def test_status_fields_are_default_false_for_unexpected_text():
    assert not scan_service._status_fields_are_default(_result(withdrawal_status="withdrawn"))


def test_status_fields_are_default_false_for_integer_zero_not_string_zero():
    # The real API returns the string "0" (confirmed live, Gate 6); an
    # integer 0 has never been observed and must not be guessed as
    # equivalent.
    assert not scan_service._status_fields_are_default(_result(withdrawal_status=0))


def test_status_fields_are_default_false_for_whitespace_padded_zero():
    assert not scan_service._status_fields_are_default(_result(withdrawal_status=" 0"))
    assert not scan_service._status_fields_are_default(_result(withdrawal_status="0 "))


def test_status_fields_are_default_false_for_non_zero_numeric_string():
    assert not scan_service._status_fields_are_default(_result(withdrawal_status="2"))


def test_status_fields_are_default_false_for_mixed_default_and_non_default():
    # The live SoftBank Group shape (Gate 6) but with one field altered.
    assert not scan_service._status_fields_are_default(
        _result(withdrawal_status="0", doc_info_edit_status="1", disclosure_status="0")
    )


def test_status_fields_are_default_false_when_one_field_is_missing_and_others_are_non_default():
    row = {"docInfoEditStatus": "1"}  # withdrawalStatus/disclosureStatus absent entirely
    assert not scan_service._status_fields_are_default(row)


def test_status_fields_are_default_true_when_missing_fields_mixed_with_default_values():
    row = {"disclosureStatus": "0"}  # withdrawalStatus/docInfoEditStatus absent -> treated as missing/default
    assert scan_service._status_fields_are_default(row)


def test_live_softbank_shaped_status_triplet_is_eligible_for_normal_persistence(tmp_path):
    # The exact real triplet observed live (Gate 6, docID S100YGH5).
    today = datetime.now(timezone.utc).date().isoformat()
    client = _client({today: _envelope([_result(
        edinet_code="E02778", sec_code="99840",
        withdrawal_status="0", doc_info_edit_status="0", disclosure_status="0",
    )])})

    result = scan_service.scan(client, [_SOFTBANK], tmp_path, lookback_days=1)

    assert len(result.new_filing_events) == 1
    assert result.deferred_status_count == 0


def test_mixed_status_triplet_remains_deferred(tmp_path):
    today = datetime.now(timezone.utc).date().isoformat()
    client = _client({today: _envelope([_result(
        edinet_code="E02778", sec_code="99840",
        withdrawal_status="0", doc_info_edit_status="1", disclosure_status="0",
    )])})

    result = scan_service.scan(client, [_SOFTBANK], tmp_path, lookback_days=1)

    assert result.new_filing_events == ()
    assert result.deferred_status_count == 1


# --- scan(): conservative edinetCode-only matching ---

def test_scan_creates_filing_event_for_direct_edinet_code_match(tmp_path):
    today = datetime.now(timezone.utc).date().isoformat()
    client = _client({today: _envelope([_result(edinet_code="E00001")])})

    result = scan_service.scan(client, [_ACME], tmp_path, lookback_days=1)

    assert len(result.new_filing_events) == 1
    assert result.new_filing_events[0].corp_code == "E00001"
    assert result.new_filing_events[0].source_name == "EDINET"
    assert result.new_filing_events[0].original_language == "Japanese"


def test_scan_uses_submit_date_time_as_the_filing_date(tmp_path):
    today = datetime.now(timezone.utc).date().isoformat()
    client = _client({today: _envelope([_result(edinet_code="E00001", submit_date_time="2026-08-17 09:00")])})

    result = scan_service.scan(client, [_ACME], tmp_path, lookback_days=1)

    assert result.new_filing_events[0].rcept_dt == "2026-08-17"


def test_scan_falls_back_to_query_date_when_submit_date_time_missing(tmp_path):
    today = datetime.now(timezone.utc).date().isoformat()
    client = _client({today: _envelope([_result(edinet_code="E00001", submit_date_time="")])})

    result = scan_service.scan(client, [_ACME], tmp_path, lookback_days=1)

    assert result.new_filing_events[0].rcept_dt == today
    assert any("missing" in e for e in result.errors)


def test_scan_does_not_match_on_secCode_when_edinet_code_differs(tmp_path):
    # Real securities-code lookup normalization lives only in
    # edinet_code_resolver.py's Gate 3 fixture-resolution path — the
    # scan-time matcher must never fall back to secCode.
    today = datetime.now(timezone.utc).date().isoformat()
    client = _client({today: _envelope([_result(edinet_code="E99999", sec_code="1234")])})

    result = scan_service.scan(client, [_ACME], tmp_path, lookback_days=1)

    assert result.new_filing_events == ()
    assert "Acme Test Co" in result.no_data_companies


def test_scan_does_not_match_on_issuer_edinet_code_role_field(tmp_path):
    # A different role field (issuerEdinetCode) matching the tracked
    # company's corp_code must NOT be treated as a match this gate —
    # only the top-level edinetCode is authoritative.
    today = datetime.now(timezone.utc).date().isoformat()
    client = _client({today: _envelope([_result(edinet_code="E99999", issuer_edinet_code="E00001")])})

    result = scan_service.scan(client, [_ACME], tmp_path, lookback_days=1)

    assert result.new_filing_events == ()
    assert "Acme Test Co" in result.no_data_companies


def test_scan_matches_real_softbank_group_edinet_code(tmp_path):
    today = datetime.now(timezone.utc).date().isoformat()
    client = _client({today: _envelope([_result(edinet_code="E02778", sec_code="99840")])})

    result = scan_service.scan(client, [_SOFTBANK], tmp_path, lookback_days=1)

    assert len(result.new_filing_events) == 1
    assert result.new_filing_events[0].corp_code == "E02778"


def test_scan_ignores_results_not_matching_any_tracked_company(tmp_path):
    today = datetime.now(timezone.utc).date().isoformat()
    client = _client({today: _envelope([_result(edinet_code="E99999")])})

    result = scan_service.scan(client, [_ACME], tmp_path, lookback_days=1)

    assert result.new_filing_events == ()
    assert "Acme Test Co" in result.no_data_companies


def test_scan_treats_unresolved_edinet_code_company_as_no_data_not_an_error(tmp_path):
    # A company with no resolved corp_code can never appear in
    # by_edinet_code (no secCode fallback matcher exists this gate — see
    # module docstring) — it lands in no_data_companies, not errors.
    today = datetime.now(timezone.utc).date().isoformat()
    client = _client({today: _envelope([])})

    result = scan_service.scan(client, [_UNRESOLVED], tmp_path, lookback_days=1)

    assert result.new_filing_events == ()
    assert "Unresolved Co" in result.no_data_companies
    assert result.errors == ()


def test_scan_is_idempotent_by_composite_dedup_key(tmp_path):
    today = datetime.now(timezone.utc).date().isoformat()
    client = _client({today: _envelope([_result()])})

    first = scan_service.scan(client, [_ACME], tmp_path, lookback_days=1)
    second = scan_service.scan(client, [_ACME], tmp_path, lookback_days=1)

    assert len(first.new_filing_events) == 1
    assert len(second.new_filing_events) == 0
    assert second.already_seen_count == 1


def test_scan_makes_one_request_per_day_in_the_lookback_window(tmp_path):
    client = _client({})
    scan_service.scan(client, [_ACME], tmp_path, lookback_days=3)
    assert client.get_document_list.call_count == 4  # inclusive of both endpoints


def test_scan_records_error_and_continues_for_remaining_days(tmp_path):
    today = datetime.now(timezone.utc).date().isoformat()
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    client = MagicMock()

    def _get_document_list(date_str, type_=2):
        if date_str == yesterday:
            raise EdinetApiError(500, "server error")
        return _envelope([_result()])

    client.get_document_list.side_effect = _get_document_list

    result = scan_service.scan(client, [_ACME], tmp_path, lookback_days=1)

    assert len(result.errors) == 1
    assert len(result.new_filing_events) == 1  # today's data still processed


def test_scan_isolates_a_malformed_days_envelope_from_other_days(tmp_path):
    today = datetime.now(timezone.utc).date().isoformat()
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    client = MagicMock()

    def _get_document_list(date_str, type_=2):
        if date_str == yesterday:
            return {"metadata": {"status": "500", "message": "Internal Server Error"}, "results": []}
        return _envelope([_result()])

    client.get_document_list.side_effect = _get_document_list

    result = scan_service.scan(client, [_ACME], tmp_path, lookback_days=1)

    assert len(result.errors) == 1
    assert "non-success status" in result.errors[0]
    assert len(result.new_filing_events) == 1


def test_scan_retries_are_not_built_into_scan_service_itself(tmp_path):
    client = MagicMock()
    client.get_document_list.side_effect = EdinetTimeoutError("timed out")

    result = scan_service.scan(client, [_ACME], tmp_path, lookback_days=1)

    assert client.get_document_list.call_count == 2
    assert len(result.errors) == 2


# --- deferred status routing ---

def test_scan_defers_a_row_with_non_empty_withdrawal_status(tmp_path):
    today = datetime.now(timezone.utc).date().isoformat()
    client = _client({today: _envelope([_result(edinet_code="E00001", withdrawal_status="1")])})

    result = scan_service.scan(client, [_ACME], tmp_path, lookback_days=1)

    assert result.new_filing_events == ()
    assert result.deferred_status_count == 1


def test_scan_defers_a_row_with_non_empty_doc_info_edit_status(tmp_path):
    today = datetime.now(timezone.utc).date().isoformat()
    client = _client({today: _envelope([_result(edinet_code="E00001", doc_info_edit_status="1")])})

    result = scan_service.scan(client, [_ACME], tmp_path, lookback_days=1)

    assert result.new_filing_events == ()
    assert result.deferred_status_count == 1


def test_scan_defers_a_row_with_non_empty_disclosure_status(tmp_path):
    today = datetime.now(timezone.utc).date().isoformat()
    client = _client({today: _envelope([_result(edinet_code="E00001", disclosure_status="1")])})

    result = scan_service.scan(client, [_ACME], tmp_path, lookback_days=1)

    assert result.new_filing_events == ()
    assert result.deferred_status_count == 1


def test_scan_deferred_row_is_not_recorded_as_seen_and_is_reconsidered_next_scan(tmp_path):
    today = datetime.now(timezone.utc).date().isoformat()
    client = _client({today: _envelope([_result(edinet_code="E00001", withdrawal_status="1")])})

    first = scan_service.scan(client, [_ACME], tmp_path, lookback_days=1)
    second = scan_service.scan(client, [_ACME], tmp_path, lookback_days=1)

    assert first.deferred_status_count == 1
    assert second.deferred_status_count == 1  # re-evaluated, not silently dropped forever
    assert second.already_seen_count == 0


def test_scan_with_default_empty_code_map_creates_no_candidates(tmp_path):
    today = datetime.now(timezone.utc).date().isoformat()
    client = _client({today: _envelope([_result()])})

    result = scan_service.scan(client, [_ACME], tmp_path, lookback_days=1)

    assert result.new_candidate_signals == ()


def test_scan_with_injected_code_map_creates_a_candidate(tmp_path):
    today = datetime.now(timezone.utc).date().isoformat()
    client = _client({today: _envelope([_result(ordinance="010", form="030")])})  # docTypeCode defaults to "120"

    result = scan_service.scan(client, [_ACME], tmp_path, lookback_days=1, code_category_map=_TEST_MAP)

    assert len(result.new_candidate_signals) == 1
    assert result.new_candidate_signals[0].matched_rules == ["fictional_category_alpha:010:030:120"]


def test_dedup_key_includes_source_edinet_code_and_doc_id():
    key = scan_service.dedup_key("E00001", "S100TEST1")
    assert key == "EDINET:E00001:S100TEST1"


def test_load_filing_events_empty_when_no_cache(tmp_path):
    assert scan_service.load_filing_events(tmp_path) == ()


def test_load_filing_events_round_trips_after_scan(tmp_path):
    today = datetime.now(timezone.utc).date().isoformat()
    client = _client({today: _envelope([_result()])})
    scan_service.scan(client, [_ACME], tmp_path, lookback_days=1)

    events = scan_service.load_filing_events(tmp_path)

    assert len(events) == 1
    assert events[0].rcept_no == "S100TEST1"


def test_clamp_lookback_days_never_exceeds_max():
    assert scan_service.clamp_lookback_days(9999) == scan_service.MAX_LOOKBACK_DAYS


def test_clamp_lookback_days_never_below_one():
    assert scan_service.clamp_lookback_days(-5) == 1


def test_lookback_defaults_are_smaller_than_edgars_since_each_day_is_its_own_request():
    from src.data_access.edgar import scan_service as edgar_scan_service
    assert scan_service.DEFAULT_LOOKBACK_DAYS < edgar_scan_service.DEFAULT_LOOKBACK_DAYS
    assert scan_service.MAX_LOOKBACK_DAYS < edgar_scan_service.MAX_LOOKBACK_DAYS


# --- Gate 8.1: explicit `dates` parameter — ScanScope provenance fix.
# Gate 8's own validation had to monkeypatch the private _iter_dates to
# isolate one target date, and that produced a real, confirmed defect:
# the reported ScanScope.bgn_date/end_date came from a real-"today"-
# relative date_window() call, completely disconnected from the
# monkeypatched date that was actually queried. `dates` makes an
# explicit date (or dates) a first-class, public parameter instead. ---

import datetime as _datetime_module  # noqa: E402 — imported here, near the tests that need it, deliberately not at module top since every other test in this file constructs dates via datetime.now()/timedelta already imported above


def test_explicit_single_date_scan_makes_exactly_one_request(tmp_path):
    target = _datetime_module.date(2026, 6, 22)
    client = _client({target.isoformat(): _envelope([_result(edinet_code="E02778", sec_code="99840")])})

    result = scan_service.scan(client, [_SOFTBANK], tmp_path, dates=target)

    assert client.get_document_list.call_count == 1
    assert len(result.new_filing_events) == 1


def test_explicit_single_date_scope_matches_the_actual_requested_date(tmp_path):
    target = _datetime_module.date(2026, 6, 22)
    client = _client({target.isoformat(): _envelope([_result(edinet_code="E02778", sec_code="99840")])})

    result = scan_service.scan(client, [_SOFTBANK], tmp_path, dates=target)

    # This is the exact Gate 8 defect this fix closes: the scope must
    # equal the actual queried date, never a separately computed window.
    assert result.scope.bgn_date == "2026-06-22"
    assert result.scope.end_date == "2026-06-22"
    assert result.scope.lookback_days == 0


def test_explicit_multi_date_tuple_scope_spans_exactly_those_dates(tmp_path):
    d1, d2, d3 = _datetime_module.date(2026, 6, 20), _datetime_module.date(2026, 6, 21), _datetime_module.date(2026, 6, 22)
    client = _client({
        d1.isoformat(): _envelope([]), d2.isoformat(): _envelope([]),
        d3.isoformat(): _envelope([_result(edinet_code="E02778", sec_code="99840")]),
    })

    result = scan_service.scan(client, [_SOFTBANK], tmp_path, dates=(d1, d2, d3))

    assert client.get_document_list.call_count == 3
    assert result.scope.bgn_date == "2026-06-20"
    assert result.scope.end_date == "2026-06-22"
    assert result.scope.lookback_days == 2


def test_explicit_dates_tuple_is_sorted_regardless_of_input_order(tmp_path):
    d1, d2 = _datetime_module.date(2026, 6, 20), _datetime_module.date(2026, 6, 22)
    client = _client({d1.isoformat(): _envelope([]), d2.isoformat(): _envelope([])})

    result = scan_service.scan(client, [_SOFTBANK], tmp_path, dates=(d2, d1))  # deliberately out of order

    assert result.scope.bgn_date == "2026-06-20"
    assert result.scope.end_date == "2026-06-22"


def test_explicit_empty_dates_tuple_raises_typed_error(tmp_path):
    client = _client({})
    try:
        scan_service.scan(client, [_SOFTBANK], tmp_path, dates=())
        assert False, "expected ValueError"
    except ValueError:
        pass
    assert client.get_document_list.call_count == 0  # never even attempted a request


def test_explicit_date_with_empty_result_set_reports_accurate_scope(tmp_path):
    target = _datetime_module.date(2026, 6, 22)
    client = _client({target.isoformat(): _envelope([])})

    result = scan_service.scan(client, [_SOFTBANK], tmp_path, dates=target)

    assert result.new_filing_events == ()
    assert result.scope.bgn_date == result.scope.end_date == "2026-06-22"


def test_ordinary_bounded_lookback_scope_still_derives_from_actual_query_dates(tmp_path):
    # dates=None (the default, ordinary UI-scan path) must produce a
    # scope that matches what date_window/_iter_dates actually computed
    # and queried — unchanged behavior, now proven structurally rather
    # than by convention.
    client = _client({})
    result = scan_service.scan(client, [_SOFTBANK], tmp_path, lookback_days=3)

    begin, end = scan_service.date_window(3)
    assert result.scope.bgn_date == begin.isoformat()
    assert result.scope.end_date == end.isoformat()
    assert client.get_document_list.call_count == 4  # 3-day lookback -> 4 calendar days


def test_no_mismatch_between_requested_dates_processed_dates_and_reported_scope(tmp_path):
    # The exact Gate 8 defect, proven closed: every date the client was
    # actually called with is bounded by [scope.bgn_date, scope.end_date].
    target = _datetime_module.date(2026, 6, 22)
    client = _client({target.isoformat(): _envelope([_result(edinet_code="E02778", sec_code="99840")])})

    result = scan_service.scan(client, [_SOFTBANK], tmp_path, dates=target)

    requested_dates = [call.args[0] for call in client.get_document_list.call_args_list]
    assert requested_dates == ["2026-06-22"]
    assert result.scope.bgn_date == result.scope.end_date == "2026-06-22"
    for requested in requested_dates:
        assert result.scope.bgn_date <= requested <= result.scope.end_date


# --- Gate 8.1: real EDINET form-code triplet
# (ordinanceCode/formCode/docTypeCode) preservation — previously only
# formCode survived into the persisted FilingEvent; ordinanceCode and
# docTypeCode were read out of the normalized row and silently discarded
# in _filing_event_from_row, a real persistence loss confirmed by
# Gate 8's own live scan. ---

def test_filing_event_preserves_the_full_softbank_shaped_form_code_triplet(tmp_path):
    # code_category_map={} isolates this from Gate 10's now-real default
    # map (010:030000:120 matches this exact fixture) — this test is
    # about form-code persistence, not candidate creation.
    today = datetime.now(timezone.utc).date().isoformat()
    client = _client({today: _envelope([_result(
        edinet_code="E02778", sec_code="99840", ordinance="010", form="030000",
    )])})

    result = scan_service.scan(client, [_SOFTBANK], tmp_path, lookback_days=1, code_category_map={})

    filing = result.new_filing_events[0]
    assert filing.pblntf_ty == "030000"  # formCode
    assert filing.pblntf_detail_ty == "120"  # docTypeCode (_result()'s fixed docTypeCode value)
    assert filing.ordinance_code == "010"  # ordinanceCode


def test_filing_event_form_codes_round_trip_through_the_on_disk_cache(tmp_path):
    today = datetime.now(timezone.utc).date().isoformat()
    client = _client({today: _envelope([_result(
        edinet_code="E02778", sec_code="99840", ordinance="010", form="030000",
    )])})
    scan_service.scan(client, [_SOFTBANK], tmp_path, lookback_days=1, code_category_map={})

    reloaded = scan_service.load_filing_events(tmp_path)

    assert len(reloaded) == 1
    assert reloaded[0].pblntf_ty == "030000"
    assert reloaded[0].pblntf_detail_ty == "120"
    assert reloaded[0].ordinance_code == "010"


# --- Gate 9: backfill_form_codes — targeted, validated, atomic in-place
# repair for records persisted before the Gate 8.1 form-code fix. Every
# fixture below is fictional test data except where explicitly labeled
# as the real Gate 8/Gate 6 SoftBank Group shape. ---

import json  # noqa: E402 — imported here, near the tests that need it, same rationale as the datetime import above


def _seed_existing_filing_event(cache_dir, doc_id, edinet_code="E02778", sec_code="99840", form_code="030000", ordinance_code="", doc_type_code=""):
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "edinet_filing_events.json"
    existing = {"seen_keys": [], "filing_events": [], "candidate_signals": []}
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
    existing["seen_keys"].append(f"EDINET:{edinet_code}:{doc_id}")
    existing["filing_events"].append({
        "rcept_no": doc_id, "corp_code": edinet_code, "corp_name": "SoftBank Group Corp.", "stock_code": sec_code,
        "report_nm": "Test Filing", "rcept_dt": "2026-06-22", "flr_nm": "ソフトバンクグループ株式会社",
        "pblntf_ty": form_code, "pblntf_detail_ty": doc_type_code, "ordinance_code": ordinance_code,
        "theme_slug": "ai-buildout", "subtheme_slug": None,
        "source_url": f"https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}",
        "retrieved_at": "2026-06-22T09:00:00+00:00", "source_name": "EDINET", "original_language": "Japanese",
        "is_demo": False, "primary_document": "",
    })
    path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")


def _backfill_client(results_by_date: dict):
    client = MagicMock()

    def _get_document_list(date_str, type_=2):
        return results_by_date.get(date_str, _envelope([]))

    client.get_document_list.side_effect = _get_document_list
    return client


_BACKFILL_DATE = _datetime_module.date(2026, 6, 22)
_SOFTBANK_TARGETS = ("S100YGH5", "S100YFHB", "S100YFH8")


def _softbank_live_rows():
    return [
        _result(doc_id="S100YGH5", edinet_code="E02778", sec_code="99840", ordinance="010", form="030000"),
        {**_result(doc_id="S100YFHB", edinet_code="E02778", sec_code="99840", ordinance="010", form="042000"), "docTypeCode": "160"},
        {**_result(doc_id="S100YFH8", edinet_code="E02778", sec_code="99840", ordinance="010", form="010000"), "docTypeCode": "140"},
    ]


def test_backfill_updates_all_three_incomplete_records(tmp_path):
    for doc_id, form_code in zip(_SOFTBANK_TARGETS, ("030000", "042000", "010000")):
        _seed_existing_filing_event(tmp_path, doc_id, form_code=form_code)  # ordinance_code/doc_type_code blank, matches Gate 8's real bug
    client = _backfill_client({_BACKFILL_DATE.isoformat(): _envelope(_softbank_live_rows())})

    result = scan_service.backfill_form_codes(client, tmp_path, _SOFTBANK_TARGETS, _BACKFILL_DATE, "E02778")

    assert result.error is None
    assert set(result.updated) == set(_SOFTBANK_TARGETS)
    assert result.already_complete == ()

    reloaded = {f.rcept_no: f for f in scan_service.load_filing_events(tmp_path)}
    assert reloaded["S100YGH5"].ordinance_code == "010"
    assert reloaded["S100YGH5"].pblntf_detail_ty == "120"
    assert reloaded["S100YFHB"].pblntf_detail_ty == "160"
    assert reloaded["S100YFH8"].pblntf_detail_ty == "140"


def test_backfill_target_absent_from_live_response_stops_without_writing(tmp_path):
    for doc_id, form_code in zip(_SOFTBANK_TARGETS, ("030000", "042000", "010000")):
        _seed_existing_filing_event(tmp_path, doc_id, form_code=form_code)
    live_rows = _softbank_live_rows()[:2]  # S100YFH8 missing from this day's response
    client = _backfill_client({_BACKFILL_DATE.isoformat(): _envelope(live_rows)})
    before = (tmp_path / "edinet_filing_events.json").read_text(encoding="utf-8")

    result = scan_service.backfill_form_codes(client, tmp_path, _SOFTBANK_TARGETS, _BACKFILL_DATE, "E02778")

    assert result.error is not None
    assert "S100YFH8" in result.error and "not found" in result.error
    assert result.updated == ()
    assert (tmp_path / "edinet_filing_events.json").read_text(encoding="utf-8") == before  # byte-identical — no write


def test_backfill_target_wrong_edinet_code_stops_without_writing(tmp_path):
    for doc_id in _SOFTBANK_TARGETS:
        _seed_existing_filing_event(tmp_path, doc_id)
    live_rows = _softbank_live_rows()
    live_rows[1] = {**live_rows[1], "edinetCode": "E99999"}  # S100YFHB attributed to a different filer
    client = _backfill_client({_BACKFILL_DATE.isoformat(): _envelope(live_rows)})
    before = (tmp_path / "edinet_filing_events.json").read_text(encoding="utf-8")

    result = scan_service.backfill_form_codes(client, tmp_path, _SOFTBANK_TARGETS, _BACKFILL_DATE, "E02778")

    assert result.error is not None
    assert "S100YFHB" in result.error and "edinetCode mismatch" in result.error
    assert (tmp_path / "edinet_filing_events.json").read_text(encoding="utf-8") == before


def test_backfill_duplicate_target_doc_id_stops_without_writing(tmp_path):
    for doc_id in _SOFTBANK_TARGETS:
        _seed_existing_filing_event(tmp_path, doc_id)
    live_rows = _softbank_live_rows() + [_result(doc_id="S100YGH5", edinet_code="E02778", sec_code="99840")]  # duplicate
    client = _backfill_client({_BACKFILL_DATE.isoformat(): _envelope(live_rows)})
    before = (tmp_path / "edinet_filing_events.json").read_text(encoding="utf-8")

    result = scan_service.backfill_form_codes(client, tmp_path, _SOFTBANK_TARGETS, _BACKFILL_DATE, "E02778")

    assert result.error is not None
    assert "S100YGH5" in result.error and "ambiguous" in result.error
    assert (tmp_path / "edinet_filing_events.json").read_text(encoding="utf-8") == before


def test_backfill_form_code_mismatch_stops_without_overwriting(tmp_path):
    # Cached record's own pblntf_ty ("999999") disagrees with the live
    # response's real formCode for this docID — must refuse, not guess
    # which one is right.
    _seed_existing_filing_event(tmp_path, "S100YGH5", form_code="999999")
    client = _backfill_client({_BACKFILL_DATE.isoformat(): _envelope([_result(doc_id="S100YGH5", edinet_code="E02778", sec_code="99840", form="030000")])})
    before = (tmp_path / "edinet_filing_events.json").read_text(encoding="utf-8")

    result = scan_service.backfill_form_codes(client, tmp_path, ("S100YGH5",), _BACKFILL_DATE, "E02778")

    assert result.error is not None
    assert "formCode mismatch" in result.error
    assert (tmp_path / "edinet_filing_events.json").read_text(encoding="utf-8") == before


def test_backfill_target_with_no_existing_cached_record_stops_without_writing(tmp_path):
    # This helper only updates already-persisted records — it must never
    # create a new FilingEvent.
    client = _backfill_client({_BACKFILL_DATE.isoformat(): _envelope([_result(doc_id="S100NEW1", edinet_code="E02778", sec_code="99840")])})

    result = scan_service.backfill_form_codes(client, tmp_path, ("S100NEW1",), _BACKFILL_DATE, "E02778")

    assert result.error is not None
    assert "no existing cached FilingEvent" in result.error
    assert not (tmp_path / "edinet_filing_events.json").exists()


def test_backfill_rerun_is_idempotent_and_makes_no_change(tmp_path):
    for doc_id, form_code in zip(_SOFTBANK_TARGETS, ("030000", "042000", "010000")):
        _seed_existing_filing_event(tmp_path, doc_id, form_code=form_code)
    client = _backfill_client({_BACKFILL_DATE.isoformat(): _envelope(_softbank_live_rows())})

    first = scan_service.backfill_form_codes(client, tmp_path, _SOFTBANK_TARGETS, _BACKFILL_DATE, "E02778")
    assert set(first.updated) == set(_SOFTBANK_TARGETS)

    after_first_write = (tmp_path / "edinet_filing_events.json").read_text(encoding="utf-8")
    second = scan_service.backfill_form_codes(client, tmp_path, _SOFTBANK_TARGETS, _BACKFILL_DATE, "E02778")

    assert second.error is None
    assert second.updated == ()
    assert set(second.already_complete) == set(_SOFTBANK_TARGETS)
    assert (tmp_path / "edinet_filing_events.json").read_text(encoding="utf-8") == after_first_write  # no-op — byte-identical


def test_backfill_empty_target_tuple_raises_typed_error(tmp_path):
    client = _backfill_client({})
    try:
        scan_service.backfill_form_codes(client, tmp_path, (), _BACKFILL_DATE, "E02778")
        assert False, "expected ValueError"
    except ValueError:
        pass
    assert client.get_document_list.call_count == 0  # never even attempted a request


def test_backfill_makes_exactly_one_request(tmp_path):
    for doc_id, form_code in zip(_SOFTBANK_TARGETS, ("030000", "042000", "010000")):
        _seed_existing_filing_event(tmp_path, doc_id, form_code=form_code)
    client = _backfill_client({_BACKFILL_DATE.isoformat(): _envelope(_softbank_live_rows())})

    scan_service.backfill_form_codes(client, tmp_path, _SOFTBANK_TARGETS, _BACKFILL_DATE, "E02778")

    assert client.get_document_list.call_count == 1


def test_backfill_never_creates_a_candidate_or_scan_report(tmp_path):
    for doc_id, form_code in zip(_SOFTBANK_TARGETS, ("030000", "042000", "010000")):
        _seed_existing_filing_event(tmp_path, doc_id, form_code=form_code)
    client = _backfill_client({_BACKFILL_DATE.isoformat(): _envelope(_softbank_live_rows())})

    result = scan_service.backfill_form_codes(client, tmp_path, _SOFTBANK_TARGETS, _BACKFILL_DATE, "E02778")

    assert not hasattr(result, "scope")  # FormCodeBackfillResult carries no ScanScope/report shape at all
    assert not (tmp_path / "edinet_candidates.json").exists()


def test_backfill_leaves_every_other_field_untouched(tmp_path):
    _seed_existing_filing_event(tmp_path, "S100YGH5", form_code="030000")
    client = _backfill_client({_BACKFILL_DATE.isoformat(): _envelope([_result(doc_id="S100YGH5", edinet_code="E02778", sec_code="99840", form="030000")])})

    scan_service.backfill_form_codes(client, tmp_path, ("S100YGH5",), _BACKFILL_DATE, "E02778")

    filing = scan_service.load_filing_events(tmp_path)[0]
    assert filing.corp_name == "SoftBank Group Corp."
    assert filing.stock_code == "99840"
    assert filing.rcept_dt == "2026-06-22"
    assert filing.source_url == "https://api.edinet-fsa.go.jp/api/v2/documents/S100YGH5"
    assert filing.source_name == "EDINET"
    assert filing.original_language == "Japanese"


# --- fetch_normalized_rows_for_dates: extracted fetch+normalize step
# (EDINET discovery Phase 1) — scan() is refactored to call this
# internally; every test above this point passes unmodified, which is
# itself the primary proof the refactor is behavior-preserving. These
# tests exercise the extracted helper directly, and prove it's the only
# thing scan() calls for the fetch step (no hidden second call). ---

def test_fetch_normalized_rows_for_dates_makes_one_call_per_date():
    d1, d2 = _datetime_module.date(2026, 6, 20), _datetime_module.date(2026, 6, 21)
    client = _client({d1.isoformat(): _envelope([_result()]), d2.isoformat(): _envelope([])})

    fetched = scan_service.fetch_normalized_rows_for_dates(client, (d1, d2))

    assert client.get_document_list.call_count == 2
    assert len(fetched[d1.isoformat()].rows) == 1
    assert fetched[d2.isoformat()].rows == ()


def test_fetch_normalized_rows_for_dates_never_writes_any_cache(tmp_path):
    d1 = _datetime_module.date(2026, 6, 20)
    client = _client({d1.isoformat(): _envelope([_result()])})

    scan_service.fetch_normalized_rows_for_dates(client, (d1,))

    assert not (tmp_path / "edinet_filing_events.json").exists()


def test_fetch_normalized_rows_for_dates_captures_error_as_a_warning_not_a_raise():
    d1 = _datetime_module.date(2026, 6, 20)
    client = MagicMock()
    client.get_document_list.side_effect = EdinetApiError(500, "server error")

    fetched = scan_service.fetch_normalized_rows_for_dates(client, (d1,))

    assert fetched[d1.isoformat()].rows == ()
    assert len(fetched[d1.isoformat()].warnings) == 1


def test_fetch_normalized_rows_for_dates_captures_envelope_failure_as_a_warning():
    d1 = _datetime_module.date(2026, 6, 20)
    client = MagicMock()
    client.get_document_list.return_value = {"metadata": {"status": "500", "message": "err"}, "results": []}

    fetched = scan_service.fetch_normalized_rows_for_dates(client, (d1,))

    assert fetched[d1.isoformat()].rows == ()
    assert "non-success status" in fetched[d1.isoformat()].warnings[0]


def test_scan_calls_get_document_list_exactly_once_per_date_via_the_shared_helper(tmp_path):
    # Proves the refactor didn't introduce a second fetch path inside
    # scan() itself — same call count as before the extraction.
    client = _client({})
    scan_service.scan(client, [_ACME], tmp_path, lookback_days=2)
    assert client.get_document_list.call_count == 3  # 2-day lookback -> 3 calendar days, one call each
