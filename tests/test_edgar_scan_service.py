"""edgar_scan_service — bounded lookback, columnar filings.recent
normalization, idempotent dedup by (source, CIK, accession number), and
per-company error isolation. Fully mocked EdgarClient; zero network
calls, no User-Agent required."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.config.tracked_companies import TrackedCompany
from src.data_access.edgar import scan_service
from src.data_access.edgar.errors import EdgarApiError, EdgarTimeoutError

_NVDA = TrackedCompany(
    name="NVIDIA", exchange="NASDAQ", krx_code="NVDA", source="SEC EDGAR",
    themes=("ai-buildout",), corp_code="0001045810",
)
_UNRESOLVED = TrackedCompany(
    name="Unresolved Co", exchange="NASDAQ", krx_code="ZZZZ", source="SEC EDGAR",
    themes=("ai-buildout",), corp_code=None,
)


def _today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _recent(accession_numbers, filing_dates=None, forms=None, primary_docs=None, items=None):
    n = len(accession_numbers)
    recent = {
        "accessionNumber": accession_numbers,
        "filingDate": filing_dates or [_today_str()] * n,
        "form": forms or ["8-K"] * n,
        "primaryDocument": primary_docs or ["doc.htm"] * n,
        "primaryDocDescription": ["desc"] * n,
    }
    if items is not None:
        recent["items"] = items
    return recent


def _client(submissions: dict) -> MagicMock:
    client = MagicMock()
    client.get_submissions.return_value = submissions
    return client


def test_normalize_recent_filings_zips_columnar_arrays_correctly():
    recent = {
        "accessionNumber": ["0001045810-26-000001", "0001045810-26-000002"],
        "filingDate": ["2026-08-01", "2026-08-05"],
        "form": ["8-K", "10-Q"],
        "primaryDocument": ["a.htm", "b.htm"],
        "primaryDocDescription": ["8-K desc", "10-Q desc"],
    }
    rows, warnings = scan_service.normalize_recent_filings(recent)

    assert warnings == ()
    assert len(rows) == 2
    assert rows[0]["accessionNumber"] == "0001045810-26-000001"
    assert rows[0]["form"] == "8-K"
    assert rows[1]["accessionNumber"] == "0001045810-26-000002"
    assert rows[1]["form"] == "10-Q"


def test_normalize_recent_filings_missing_required_column_fails_safely():
    recent = {"accessionNumber": ["x"], "filingDate": ["2026-08-01"], "form": ["8-K"]}  # missing primaryDocument
    rows, warnings = scan_service.normalize_recent_filings(recent)
    assert rows == []
    assert warnings


def test_normalize_recent_filings_mismatched_lengths_fails_safely():
    recent = _recent(["a", "b"])
    recent["form"] = ["8-K"]  # only one, should be two
    rows, warnings = scan_service.normalize_recent_filings(recent)
    assert rows == []
    assert "mismatched lengths" in warnings[0]


def test_normalize_recent_filings_non_dict_input_fails_safely():
    rows, warnings = scan_service.normalize_recent_filings(["not", "a", "dict"])
    assert rows == []
    assert warnings


def test_normalize_recent_filings_missing_optional_column_defaults_to_empty_strings():
    recent = _recent(["a"])
    del recent["primaryDocDescription"]
    rows, warnings = scan_service.normalize_recent_filings(recent)
    assert warnings == ()
    assert rows[0]["primaryDocDescription"] == ""


def test_normalize_recent_filings_empty_recent_block_returns_no_rows():
    rows, warnings = scan_service.normalize_recent_filings({"accessionNumber": [], "filingDate": [], "form": [], "primaryDocument": []})
    assert rows == []
    assert warnings == ()


def test_scan_creates_filing_event_and_candidate_for_matching_form():
    client = _client({"filings": {"recent": _recent(["0001045810-26-000001"])}})
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        result = scan_service.scan(client, [_NVDA], Path(d))
        assert len(result.new_filing_events) == 1
        assert len(result.new_candidate_signals) == 1
        assert result.new_filing_events[0].source_name == "SEC EDGAR"
        assert result.new_filing_events[0].corp_code == "0001045810"
        assert result.new_filing_events[0].rcept_no == "0001045810-26-000001"


def test_scan_is_idempotent_by_composite_dedup_key():
    import tempfile
    from pathlib import Path
    client = _client({"filings": {"recent": _recent(["0001045810-26-000001"])}})
    with tempfile.TemporaryDirectory() as d:
        first = scan_service.scan(client, [_NVDA], Path(d))
        second = scan_service.scan(client, [_NVDA], Path(d))
        assert len(first.new_filing_events) == 1
        assert len(second.new_filing_events) == 0
        assert second.already_seen_count == 1


def test_scan_filters_out_filings_outside_the_lookback_window():
    import tempfile
    from pathlib import Path
    old_date = (datetime.now(timezone.utc) - timedelta(days=200)).date().isoformat()
    client = _client({"filings": {"recent": _recent(["0001045810-26-000001"], filing_dates=[old_date])}})
    with tempfile.TemporaryDirectory() as d:
        result = scan_service.scan(client, [_NVDA], Path(d), lookback_days=30)
        assert result.new_filing_events == ()


def test_scan_records_error_and_skips_company_with_unresolved_cik():
    import tempfile
    from pathlib import Path
    client = _client({"filings": {"recent": _recent([])}})
    with tempfile.TemporaryDirectory() as d:
        result = scan_service.scan(client, [_UNRESOLVED], Path(d))
        assert len(result.errors) == 1
        assert "CIK not resolved" in result.errors[0]


def test_scan_continues_for_remaining_companies_after_one_fails():
    import tempfile
    from pathlib import Path
    failing_client = MagicMock()
    failing_client.get_submissions.side_effect = EdgarApiError(500, "server error")
    with tempfile.TemporaryDirectory() as d:
        result = scan_service.scan(failing_client, [_NVDA], Path(d))
        assert len(result.errors) == 1
        assert result.new_filing_events == ()


def test_scan_retries_on_transient_timeout_then_succeeds(monkeypatch):
    import tempfile
    from pathlib import Path
    monkeypatch.setattr(scan_service.time, "sleep", lambda s: None)
    client = MagicMock()
    client.get_submissions.side_effect = [EdgarTimeoutError("timeout"), {"filings": {"recent": _recent(["0001045810-26-000001"])}}]
    with tempfile.TemporaryDirectory() as d:
        result = scan_service.scan(client, [_NVDA], Path(d))
        assert len(result.new_filing_events) == 1


def test_scan_gives_up_after_max_retries(monkeypatch):
    import tempfile
    from pathlib import Path
    monkeypatch.setattr(scan_service.time, "sleep", lambda s: None)
    client = MagicMock()
    client.get_submissions.side_effect = EdgarTimeoutError("still timing out")
    with tempfile.TemporaryDirectory() as d:
        result = scan_service.scan(client, [_NVDA], Path(d))
        assert len(result.errors) == 1


def test_dedup_key_includes_source_cik_and_accession_number():
    key = scan_service.dedup_key("0001045810", "0001045810-26-000001")
    assert key == "SEC EDGAR:0001045810:0001045810-26-000001"


def test_load_filing_events_empty_when_no_cache(tmp_path):
    assert scan_service.load_filing_events(tmp_path) == ()


def test_load_filing_events_round_trips_after_scan(tmp_path):
    client = _client({"filings": {"recent": _recent(["0001045810-26-000001"])}})
    scan_service.scan(client, [_NVDA], tmp_path)
    events = scan_service.load_filing_events(tmp_path)
    assert len(events) == 1
    assert events[0].rcept_no == "0001045810-26-000001"


def test_clamp_lookback_days_never_exceeds_max():
    assert scan_service.clamp_lookback_days(9999) == scan_service.MAX_LOOKBACK_DAYS


def test_clamp_lookback_days_never_below_one():
    assert scan_service.clamp_lookback_days(-5) == 1


# --- Scan-time 8-K `items` metadata wiring (milestone 8, Gate 4 fix —
# real live NVDA accession 0001045810-26-000079 exposed this real
# scan-time `items` column, e.g. items="1.01,2.03,7.01") ---

def test_valid_items_metadata_produces_refined_candidate_at_scan_time(tmp_path):
    client = _client({"filings": {"recent": _recent(["0001045810-26-000001"], items=["1.01,2.03,7.01"])}})

    result = scan_service.scan(client, [_NVDA], tmp_path)

    assert len(result.new_candidate_signals) == 1
    candidate = result.new_candidate_signals[0]
    assert candidate.confidence == "High"
    assert candidate.matched_rules == [
        "material_agreement:8-K item 1.01",
        "financing_or_debt:8-K item 2.03",
        "regulation_fd_disclosure:8-K item 7.01",
    ]


def test_empty_items_metadata_falls_back_to_coarse_classification(tmp_path):
    client = _client({"filings": {"recent": _recent(["0001045810-26-000001"], items=[""])}})

    result = scan_service.scan(client, [_NVDA], tmp_path)

    candidate = result.new_candidate_signals[0]
    assert candidate.confidence == "Moderate"
    assert candidate.matched_rules == ["material_event_8k_pending_items:8-K"]


def test_missing_items_column_falls_back_to_coarse_classification(tmp_path):
    # No "items" key in the recent block at all (older/partial payload shape).
    client = _client({"filings": {"recent": _recent(["0001045810-26-000001"])}})  # items=None -> no column

    result = scan_service.scan(client, [_NVDA], tmp_path)

    candidate = result.new_candidate_signals[0]
    assert candidate.confidence == "Moderate"
    assert candidate.matched_rules == ["material_event_8k_pending_items:8-K"]


def test_malformed_items_metadata_falls_back_to_coarse_classification(tmp_path):
    client = _client({"filings": {"recent": _recent(["0001045810-26-000001"], items=["garbage;not;csv"])}})

    result = scan_service.scan(client, [_NVDA], tmp_path)

    candidate = result.new_candidate_signals[0]
    assert candidate.confidence == "Moderate"
    assert candidate.matched_rules == ["material_event_8k_pending_items:8-K"]


def test_multi_item_8k_classification_end_to_end(tmp_path):
    client = _client({"filings": {"recent": _recent(["0001045810-26-000001"], items=["2.02"])}})

    result = scan_service.scan(client, [_NVDA], tmp_path)

    candidate = result.new_candidate_signals[0]
    assert candidate.confidence == "Moderate"  # single category
    assert candidate.matched_rules == ["earnings_or_results:8-K item 2.02"]


def test_non_8k_forms_are_unaffected_by_items_column(tmp_path):
    client = _client({"filings": {"recent": _recent(["0001045810-26-000001"], forms=["10-Q"], items=["1.01"])}})

    result = scan_service.scan(client, [_NVDA], tmp_path)

    candidate = result.new_candidate_signals[0]
    assert candidate.matched_rules == ["earnings_or_results:10-Q"]  # items ignored for non-8-K forms


def test_rescanning_the_same_filing_does_not_duplicate_or_reclassify(tmp_path):
    client = _client({"filings": {"recent": _recent(["0001045810-26-000001"], items=["1.01,2.03,7.01"])}})

    first = scan_service.scan(client, [_NVDA], tmp_path)
    second = scan_service.scan(client, [_NVDA], tmp_path)

    assert len(first.new_candidate_signals) == 1
    assert len(second.new_candidate_signals) == 0  # already seen, not re-created
    assert second.already_seen_count == 1
    events = scan_service.load_filing_events(tmp_path)
    assert len(events) == 1  # no duplicate FilingEvent either


def test_real_schedule_13g_form_creates_ownership_change_candidate(tmp_path):
    # Real SEC form spelling verified live (Gate 3) — was previously
    # silently missed before edgar_rules.normalize_form_type existed.
    client = _client({"filings": {"recent": _recent(["0001045810-26-000002"], forms=["SCHEDULE 13G"])}})

    result = scan_service.scan(client, [_NVDA], tmp_path)

    assert len(result.new_candidate_signals) == 1
    assert result.new_candidate_signals[0].matched_rules == ["ownership_change:SC 13G"]
