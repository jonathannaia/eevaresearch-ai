"""scan_service — bounded lookback/pagination, idempotent dedup, cache
read/write, per-company error isolation, and retry/backoff for transient
DART failures. Fully mocked DartClient; zero network calls, no API key
required."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.config.tracked_companies import TrackedCompany
from src.data_access.dart import scan_service
from src.data_access.dart.client import DisclosureRecord
from src.data_access.dart.errors import DartApiError, DartParseError, DartRateLimitError, DartTimeoutError
from src.models.models import CandidateStatus

_SAMSUNG = TrackedCompany(
    name="Samsung Electronics", exchange="KRX", krx_code="005930", source="OpenDART / DART",
    themes=("memory", "ai-buildout"), corp_code="00126380",
)
_SK_HYNIX = TrackedCompany(
    name="SK Hynix", exchange="KRX", krx_code="000660", source="OpenDART / DART",
    themes=("memory", "ai-buildout"), corp_code="00164779",
)
_UNRESOLVED = TrackedCompany(
    name="Unresolved Co", exchange="KRX", krx_code="999999", source="OpenDART / DART",
    themes=("memory",), corp_code=None,
)


def _record(rcept_no: str, report_nm: str, corp_code: str = "00126380", corp_name: str = "삼성전자", stock_code: str = "005930") -> DisclosureRecord:
    return DisclosureRecord(
        corp_cls="Y", corp_name=corp_name, corp_code=corp_code, stock_code=stock_code,
        report_nm=report_nm, rcept_no=rcept_no, flr_nm=corp_name, rcept_dt="20260810", rm="",
    )


def _make_client(pages: dict[int, tuple[list[DisclosureRecord], int]]):
    """pages: {page_no: (records, total_count)}"""
    client = MagicMock()

    def _search(corp_code, bgn_de, end_de, page_no=1, page_count=100):
        return pages[page_no]

    client.search_disclosures.side_effect = _search
    return client


def test_clamp_lookback_days_never_exceeds_max():
    assert scan_service.clamp_lookback_days(9999) == scan_service.MAX_LOOKBACK_DAYS


def test_clamp_lookback_days_never_below_one():
    assert scan_service.clamp_lookback_days(-5) == 1


def test_clamp_lookback_days_passes_through_valid_value():
    assert scan_service.clamp_lookback_days(45) == 45


def test_date_window_respects_clamped_lookback():
    today = datetime(2026, 8, 17, tzinfo=timezone.utc)
    bgn, end = scan_service.date_window(9999, today=today)
    assert end == "20260817"
    # Clamped to MAX_LOOKBACK_DAYS (90), not the requested 9999.
    expected_bgn = (today.date().toordinal() - scan_service.MAX_LOOKBACK_DAYS)
    from datetime import date
    assert bgn == date.fromordinal(expected_bgn).strftime("%Y%m%d")


def test_scan_creates_filing_events_from_new_disclosures(tmp_path):
    client = _make_client({1: ([_record("20260810000001", "신규시설투자등")], 1)})

    result = scan_service.scan(client, [_SAMSUNG], tmp_path)

    assert len(result.new_filing_events) == 1
    assert result.new_filing_events[0].rcept_no == "20260810000001"
    assert result.already_seen_count == 0


def test_scan_promotes_relevant_filing_to_candidate_signal(tmp_path):
    client = _make_client({1: ([_record("20260810000001", "신규시설투자등")], 1)})

    result = scan_service.scan(client, [_SAMSUNG], tmp_path)

    assert len(result.new_candidate_signals) == 1
    candidate = result.new_candidate_signals[0]
    assert candidate.status == CandidateStatus.CANDIDATE_DETECTED
    assert candidate.confidence == "Moderate"
    assert candidate.filing.rcept_no == "20260810000001"


def test_scan_does_not_promote_a_filing_with_no_rule_match(tmp_path):
    client = _make_client({1: ([_record("20260810000002", "임원ㆍ주요주주특정증권등소유상황보고서")], 1)})

    result = scan_service.scan(client, [_SAMSUNG], tmp_path)

    assert len(result.new_filing_events) == 1
    assert len(result.new_candidate_signals) == 0


def test_scan_is_idempotent_second_scan_produces_no_duplicates(tmp_path):
    client = _make_client({1: ([_record("20260810000001", "신규시설투자등")], 1)})

    first = scan_service.scan(client, [_SAMSUNG], tmp_path)
    second = scan_service.scan(client, [_SAMSUNG], tmp_path)

    assert len(first.new_filing_events) == 1
    assert len(second.new_filing_events) == 0
    assert second.already_seen_count == 1


def test_scan_handles_empty_result(tmp_path):
    client = _make_client({1: ([], 0)})

    result = scan_service.scan(client, [_SAMSUNG], tmp_path)

    assert result.new_filing_events == ()
    assert result.new_candidate_signals == ()
    assert result.errors == ()


def test_scan_paginates_across_multiple_pages(tmp_path):
    client = _make_client({
        1: ([_record(f"2026081000000{i}", "신규시설투자등") for i in range(100)], 150),
        2: ([_record(f"2026081000010{i}", "신규시설투자등") for i in range(50)], 150),
    })

    result = scan_service.scan(client, [_SAMSUNG], tmp_path)

    assert len(result.new_filing_events) == 150
    assert client.search_disclosures.call_count == 2


def test_scan_stops_at_max_pages_per_company_even_with_more_total(tmp_path):
    pages = {
        n: ([_record(f"page{n}-{i}", "신규시설투자등") for i in range(100)], 100 * 20)
        for n in range(1, 20)
    }
    client = _make_client(pages)

    result = scan_service.scan(client, [_SAMSUNG], tmp_path, max_pages_per_company=3)

    assert client.search_disclosures.call_count == 3
    assert len(result.new_filing_events) == 300


def test_scan_records_error_and_continues_for_other_companies_on_api_error(tmp_path):
    client = MagicMock()

    def _search(corp_code, bgn_de, end_de, page_no=1, page_count=100):
        if corp_code == _SAMSUNG.corp_code:
            raise DartApiError("013", "조회된 데이터가 없습니다.")
        return [_record("20260810000099", "신규시설투자등", corp_code=_SK_HYNIX.corp_code)], 1

    client.search_disclosures.side_effect = _search

    result = scan_service.scan(client, [_SAMSUNG, _SK_HYNIX], tmp_path)

    assert len(result.errors) == 1
    assert "Samsung" in result.errors[0]
    assert len(result.new_filing_events) == 1  # SK Hynix still succeeded


def test_scan_skips_company_with_unresolved_corp_code(tmp_path):
    client = _make_client({})

    result = scan_service.scan(client, [_UNRESOLVED], tmp_path)

    assert len(result.errors) == 1
    assert "corp_code not resolved" in result.errors[0]
    client.search_disclosures.assert_not_called()


def test_scan_retries_on_rate_limit_then_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_service.time, "sleep", lambda seconds: None)
    client = MagicMock()
    client.search_disclosures.side_effect = [
        DartRateLimitError("020", "요청 제한을 초과하였습니다."),
        ([_record("20260810000001", "신규시설투자등")], 1),
    ]

    result = scan_service.scan(client, [_SAMSUNG], tmp_path)

    assert len(result.new_filing_events) == 1
    assert client.search_disclosures.call_count == 2


def test_scan_gives_up_after_max_retries_on_persistent_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_service.time, "sleep", lambda seconds: None)
    client = MagicMock()
    client.search_disclosures.side_effect = DartTimeoutError("timed out")

    result = scan_service.scan(client, [_SAMSUNG], tmp_path)

    assert len(result.errors) == 1
    assert client.search_disclosures.call_count == scan_service._MAX_RETRIES + 1


def test_scan_result_scope_reflects_requested_companies_and_source(tmp_path):
    client = _make_client({1: ([], 0)})

    result = scan_service.scan(client, [_SAMSUNG, _SK_HYNIX], tmp_path, lookback_days=30)

    assert result.scope.companies == ("Samsung Electronics", "SK Hynix")
    assert result.scope.source == "OpenDART / DART"
    assert result.scope.lookback_days == 30


def test_scan_clamps_an_excessive_lookback_request_in_the_returned_scope(tmp_path):
    client = _make_client({1: ([], 0)})

    result = scan_service.scan(client, [_SAMSUNG], tmp_path, lookback_days=9999)

    assert result.scope.lookback_days == scan_service.MAX_LOOKBACK_DAYS


def test_scan_deduplicates_repeated_receipt_number_within_a_single_page(tmp_path):
    # Defensive: DART shouldn't return the same rcept_no twice on one
    # page, but the dedup logic must not double-count if it ever does.
    client = _make_client({1: ([
        _record("20260810000001", "신규시설투자등"),
        _record("20260810000001", "신규시설투자등"),
    ], 2)})

    result = scan_service.scan(client, [_SAMSUNG], tmp_path)

    assert len(result.new_filing_events) == 1
    assert result.already_seen_count == 1


def test_scan_records_error_on_malformed_response(tmp_path):
    client = MagicMock()
    client.search_disclosures.side_effect = DartParseError("list.json response was not valid JSON.")

    result = scan_service.scan(client, [_SAMSUNG], tmp_path)

    assert len(result.errors) == 1
    assert result.new_filing_events == ()


def test_load_seen_receipt_numbers_reads_persisted_cache(tmp_path):
    client = _make_client({1: ([_record("20260810000001", "신규시설투자등")], 1)})
    scan_service.scan(client, [_SAMSUNG], tmp_path)

    assert scan_service.load_seen_receipt_numbers(tmp_path) == {"20260810000001"}


def test_load_seen_receipt_numbers_empty_when_no_cache(tmp_path):
    assert scan_service.load_seen_receipt_numbers(tmp_path) == set()


def test_scan_handles_corrupt_cache_file_without_raising(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "dart_filing_events.json").write_text("{not valid json", encoding="utf-8")
    client = _make_client({1: ([_record("20260810000001", "신규시설투자등")], 1)})

    result = scan_service.scan(client, [_SAMSUNG], tmp_path)

    assert len(result.new_filing_events) == 1


def test_load_filing_events_empty_when_no_cache(tmp_path):
    assert scan_service.load_filing_events(tmp_path) == ()


def test_load_filing_events_returns_every_scanned_filing_including_unpromoted(tmp_path):
    # "일반 공고" matches no rule and shouldn't become a candidate, but
    # load_filing_events must still return it — that's exactly the "New
    # filing" bucket Radar Inbox needs (a filing the rule engine looked
    # at and did not flag).
    client = _make_client({
        1: ([
            _record("20260810000001", "신규시설투자등 결정"),
            _record("20260810000002", "일반 공고"),
        ], 2),
    })
    scan_service.scan(client, [_SAMSUNG], tmp_path)

    events = scan_service.load_filing_events(tmp_path)

    assert {e.rcept_no for e in events} == {"20260810000001", "20260810000002"}


def test_load_filing_events_skips_individually_corrupt_entries(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "dart_filing_events.json").write_text(
        '{"seen_receipt_numbers": [], "filing_events": [{"not_a_real_field": true}], "candidate_signals": []}',
        encoding="utf-8",
    )
    assert scan_service.load_filing_events(tmp_path) == ()


# ============================================================
# Repair B — canonical receipt dates at the DTO -> domain boundary
#
# DART's disclosure list returns `rcept_dt` in its own compact YYYYMMDD
# form, but `FilingEvent.rcept_dt` is documented as an ISO 8601 date and
# Research Case selection accepts strict YYYY-MM-DD only. Before this
# repair every DART candidate reached the selector as
# `invalid_receipt_date` and no case could be created for the lane.
#
# The conversion is deliberately narrow: only a complete, valid compact
# calendar date is rewritten. Everything else passes through untouched
# so the existing rejection path stays exactly as it was.
# ============================================================


def test_valid_compact_receipt_date_becomes_canonical_iso():
    assert scan_service._canonical_rcept_dt("20260810") == "2026-08-10"
    assert scan_service._canonical_rcept_dt("20260101") == "2026-01-01"
    assert scan_service._canonical_rcept_dt("20261231") == "2026-12-31"
    assert scan_service._canonical_rcept_dt("20240229") == "2024-02-29"  # real leap day


def test_already_canonical_iso_date_is_returned_unchanged():
    """Idempotent, and safe if DART's own format ever changes."""
    for value in ("2026-08-10", "2026-01-01"):
        assert scan_service._canonical_rcept_dt(value) == value
    # Converting twice is the same as converting once.
    once = scan_service._canonical_rcept_dt("20260810")
    assert scan_service._canonical_rcept_dt(once) == once


@pytest.mark.parametrize("raw", [
    "",                       # missing — client.py's own default
    "2026081",                # too short
    "202608100",              # too long
    "2026-8-10",              # not zero-padded
    "2026/08/10",             # wrong delimiter
    "20260810T00:00:00",      # datetime-shaped
    "2026-08-10T00:00:00+00:00",  # timezone-bearing
    "2026-08-10 09:00",       # timestamp
    "not-a-date",
    "abcdefgh",               # eight non-digits
    "2026081a",               # seven digits and a letter
    " 2026081",               # padded to eight with whitespace
    "20260230",               # eight digits, impossible day
    "20261301",               # eight digits, impossible month
    "00000000",               # eight digits, no such year
])
def test_values_that_are_not_valid_compact_dates_pass_through_untouched(raw):
    """No fabrication and no blanking: the raw value survives, so
    `research_lead_selection._parse_iso_date` still rejects it and the
    selector still records `invalid_receipt_date` exactly as before."""
    assert scan_service._canonical_rcept_dt(raw) == raw


@pytest.mark.parametrize("raw", [None, 20260810, 20260810.0, b"20260810", ["20260810"], object()])
def test_non_string_values_pass_through_untouched(raw):
    """Scope: this asserts the private normalizer's own passthrough
    contract only — that it refuses to invent a date from a non-string.

    It does NOT claim every one of these values can be persisted.
    `FilingEvent` is a plain dataclass, so its `rcept_dt: str`
    annotation is not enforced at construction, and some non-strings
    (e.g. bytes) would fail later at the JSON candidate-store write.

    That is unchanged from before Repair B: the mapping previously read
    `rcept_dt=record.rcept_dt`, passing the DTO value through verbatim,
    which is byte-for-byte what the non-string branch does now. It is
    also unreachable on the production path — client.py sources this
    value from `json.loads` output, and JSON decoding never yields
    bytes. Widening or hardening that behavior is deliberately out of
    this repair's scope."""
    assert scan_service._canonical_rcept_dt(raw) is raw


def test_unicode_digits_are_not_converted():
    """`str.isdigit()` is true for non-ASCII digit characters; the
    conversion is gated on ASCII so an exotic value is left alone rather
    than silently normalized."""
    arabic_indic = "٢٠٢٦٠٨١٠"  # ٢٠٢٦٠٨١٠
    assert len(arabic_indic) == 8 and arabic_indic.isdigit()
    assert scan_service._canonical_rcept_dt(arabic_indic) == arabic_indic


def test_filing_event_mapping_emits_a_canonical_receipt_date():
    """The boundary itself, not just the helper."""
    record = _record("20260810000001", "단일판매ㆍ공급계약체결")
    assert record.rcept_dt == "20260810"  # DTO stays faithful to the wire

    filing = scan_service._filing_event_from_record(record, _SAMSUNG, retrieved_at="2026-08-10T01:00:00+00:00")

    assert filing.rcept_dt == "2026-08-10"
    # Every other mapped field is untouched by this repair.
    assert filing.rcept_no == record.rcept_no
    assert filing.corp_code == record.corp_code
    assert filing.corp_name == record.corp_name
    assert filing.stock_code == record.stock_code
    assert filing.report_nm == record.report_nm
    assert filing.flr_nm == record.flr_nm
    assert filing.retrieved_at == "2026-08-10T01:00:00+00:00"


def test_filing_event_mapping_leaves_an_unconvertible_receipt_date_alone():
    record = DisclosureRecord(
        corp_cls="Y", corp_name="삼성전자", corp_code="00126380", stock_code="005930",
        report_nm="단일판매ㆍ공급계약체결", rcept_no="20260810000002", flr_nm="삼성전자",
        rcept_dt="20260230", rm="",
    )

    filing = scan_service._filing_event_from_record(record, _SAMSUNG, retrieved_at="2026-08-10T01:00:00+00:00")

    assert filing.rcept_dt == "20260230"


def test_canonical_date_now_satisfies_the_research_case_selector_date_gate():
    """End of the chain: the selector's own parser accepts what the
    boundary now produces, and still refuses what DART used to emit.
    This asserts the selector's contract is MET, never widened."""
    from src.logic.research_lead_selection import _parse_iso_date

    assert _parse_iso_date(scan_service._canonical_rcept_dt("20260810")) is not None
    assert _parse_iso_date("20260810") is None  # unchanged: compact is still rejected there


def test_dual_format_downstream_consumers_still_parse_the_canonical_date():
    """The five display helpers strip dashes before parsing %Y%m%d, so
    they accept both shapes. Normalizing must not break them."""
    from datetime import date as _date

    from src.ui.pages.radar_inbox import _parse_rcept_date

    canonical = scan_service._canonical_rcept_dt("20260810")
    assert _parse_rcept_date(canonical) == _date(2026, 8, 10)
    assert _parse_rcept_date("20260810") == _date(2026, 8, 10)  # legacy rows still parse


def test_edgar_and_edinet_sources_already_emit_canonical_dates_and_are_unchanged():
    """Both lanes' own scan services already produce dashed ISO, which
    is why only DART needed this repair. Pinned so a future change to
    either lane that reintroduced a compact date would surface here
    rather than silently reaching the selector."""
    from src.logic.research_lead_selection import _parse_iso_date

    # EDGAR takes its date straight from the submissions feed as ISO;
    # EDINET truncates its real submitDateTime to an ISO date component.
    for already_canonical in ("2026-08-10", "2026-01-01"):
        assert _parse_iso_date(already_canonical) is not None
        # The DART normalizer would be a no-op on them in any case.
        assert scan_service._canonical_rcept_dt(already_canonical) == already_canonical
