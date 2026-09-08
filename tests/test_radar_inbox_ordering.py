"""Radar ordering correction (design/DECISIONS.md) — `_build_items`'s
global, cross-source newest-first ordering by official filed date/time
(never capture/retrieval time), via `_official_filed_at`/`_radar_sort_key`.

The bug this fixes: the previous sort compared `FilingEvent.rcept_dt` as
raw *strings* ("20260828" for DART vs. "2026-09-02" for EDGAR/EDINET) —
lexicographic comparison of those two shapes does not agree with
chronological order (the dash character sorts below every digit), so a
newer EDGAR/EDINET filing could render below an older DART one. Every
test here calls `radar_inbox._build_items` directly against JSON
fixtures — no Streamlit rendering needed for that part — except the
source-filter test, which needs the full page. Zero network calls, no
real API key, and the real data/cache/ (gitignored live pilot cache) is
never touched."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.models.models import FilingEvent
from src.ui.pages import radar_inbox

_HARNESS = Path(__file__).parent / "apptest_pages" / "radar_inbox_page.py"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_dart_filing_events(cache_dir, filings: list[FilingEvent]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {"seen_receipt_numbers": [f.rcept_no for f in filings], "filing_events": [asdict(f) for f in filings], "candidate_signals": []}
    (cache_dir / "dart_filing_events.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _seed_edgar_filing_events(cache_dir, filings: list[FilingEvent]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "seen_keys": [f"SEC EDGAR:{f.corp_code}:{f.rcept_no}" for f in filings],
        "filing_events": [asdict(f) for f in filings], "candidate_signals": [],
    }
    (cache_dir / "edgar_filing_events.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _seed_edinet_filing_events(cache_dir, filings: list[FilingEvent]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "seen_keys": [f"EDINET:{f.corp_code}:{f.rcept_no}" for f in filings],
        "filing_events": [asdict(f) for f in filings], "candidate_signals": [],
    }
    (cache_dir / "edinet_filing_events.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _dart_filing(rcept_no: str, rcept_dt: str, corp_name: str = "삼성전자") -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="00126380", corp_name=corp_name, stock_code="005930",
        report_nm="공시", rcept_dt=rcept_dt, flr_nm=corp_name,
        source_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}", retrieved_at=_now_iso(),
    )


def _edgar_filing(rcept_no: str, rcept_dt: str, report_nm: str = "8-K filing", corp_name: str = "NVIDIA") -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="0001045810", corp_name=corp_name, stock_code="NVDA",
        report_nm=report_nm, rcept_dt=rcept_dt, flr_nm=corp_name, pblntf_ty="8-K",
        source_url=f"https://www.sec.gov/Archives/edgar/data/1045810/{rcept_no}/",
        retrieved_at=_now_iso(), source_name="SEC EDGAR", original_language="English",
    )


def _edinet_filing(rcept_no: str, rcept_dt: str, filed_at: str | None = None, corp_name: str = "SoftBank Group Corp.") -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="E02778", corp_name=corp_name, stock_code="99840",
        report_nm="有価証券報告書", rcept_dt=rcept_dt, flr_nm=corp_name,
        source_url=f"https://api.edinet-fsa.go.jp/api/v2/documents/{rcept_no}",
        retrieved_at=_now_iso(), source_name="EDINET", original_language="Japanese", filed_at=filed_at,
    )


# ============================================================
# The reported bug: a newer EDGAR filing must render before an older
# DART one, even though DART's rcept_dt string sorts higher as raw text.
# ============================================================


def test_newer_edgar_filing_renders_before_older_dart_filing(tmp_path):
    _seed_dart_filing_events(tmp_path, [_dart_filing("dart-1", "20260828")])
    _seed_edgar_filing_events(tmp_path, [_edgar_filing("edgar-1", "2026-09-02")])

    items = radar_inbox._build_items(tmp_path)

    assert [i.filing.rcept_no for i in items] == ["edgar-1", "dart-1"]


# ============================================================
# Mixed-source records sort globally, not in source/repository order
# ============================================================


def test_mixed_source_records_sort_globally_by_date_not_by_source_order(tmp_path):
    # _build_items always loads DART, then EDGAR, then EDINET, in that
    # fixed order — deliberately seeded here so the oldest filing is
    # loaded FIRST and the newest is loaded LAST, proving the final order
    # is driven purely by date, never by load/insertion order.
    _seed_dart_filing_events(tmp_path, [_dart_filing("dart-old", "2026-08-15")])
    _seed_edgar_filing_events(tmp_path, [_edgar_filing("edgar-mid", "2026-09-01")])
    _seed_edinet_filing_events(tmp_path, [_edinet_filing("edinet-new", "2026-09-03")])

    items = radar_inbox._build_items(tmp_path)

    assert [i.filing.rcept_no for i in items] == ["edinet-new", "edgar-mid", "dart-old"]


def test_full_official_filed_datetime_is_preferred_over_date_only_fallback(tmp_path):
    """Requirement 2: when a source supplies a full official filed
    datetime (EDINET's filed_at), it must be used — including breaking a
    same-calendar-day tie against a source that only has a date."""
    _seed_dart_filing_events(tmp_path, [_dart_filing("dart-midnight", "2026-09-01")])
    _seed_edinet_filing_events(tmp_path, [
        _edinet_filing("edinet-morning", "2026-09-01", filed_at="2026-09-01T09:00:00"),
    ])

    items = radar_inbox._build_items(tmp_path)

    # Same calendar day, but EDINET's real 09:00 filed time is later than
    # DART's date-only midnight fallback — EDINET must render first.
    assert [i.filing.rcept_no for i in items] == ["edinet-morning", "dart-midnight"]


# ============================================================
# Equal-date ordering is stable and deterministic (never depends on
# repository/dict iteration order), via the (source_name, rcept_no)
# tie-breaker
# ============================================================


def test_equal_date_ordering_is_stable_and_deterministic(tmp_path):
    _seed_dart_filing_events(tmp_path, [
        _dart_filing("dart-b", "2026-08-28"),
        _dart_filing("dart-a", "2026-08-28"),
    ])
    _seed_edgar_filing_events(tmp_path, [_edgar_filing("edgar-1", "2026-08-28")])

    first_run = [i.filing.rcept_no for i in radar_inbox._build_items(tmp_path)]
    second_run = [i.filing.rcept_no for i in radar_inbox._build_items(tmp_path)]

    assert first_run == second_run
    # Same date across all three -> tie-break is strictly (source_name,
    # rcept_no) ascending: "OpenDART / DART" sorts before "SEC EDGAR",
    # and within DART, "dart-a" sorts before "dart-b".
    assert first_run == ["dart-a", "dart-b", "edgar-1"]


# ============================================================
# Missing/unparseable filed dates sort after every dated filing, never
# ahead of them
# ============================================================


def test_missing_or_malformed_filed_date_sorts_last(tmp_path):
    _seed_dart_filing_events(tmp_path, [
        _dart_filing("dart-dated", "2026-08-28"),
        _dart_filing("dart-empty", ""),
        _dart_filing("dart-malformed", "not-a-date"),
    ])

    items = radar_inbox._build_items(tmp_path)

    assert items[0].filing.rcept_no == "dart-dated"
    assert {i.filing.rcept_no for i in items[1:]} == {"dart-empty", "dart-malformed"}


# ============================================================
# Source filtering preserves newest-first ordering
# ============================================================


def test_source_filter_preserves_newest_first_ordering(tmp_path):
    _seed_dart_filing_events(tmp_path, [_dart_filing("dart-old", "2026-08-15")])
    _seed_edgar_filing_events(tmp_path, [
        _edgar_filing("edgar-new", "2026-09-02", report_nm="8-K filing newer"),
        _edgar_filing("edgar-mid", "2026-08-20", report_nm="8-K filing older"),
    ])

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", edgar_user_agent="EevaResearch test@example.com", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=15)
        at.run()
        at.multiselect(key="radar-filter-source").set_value(["SEC EDGAR"])
        at.run()

    assert not at.exception
    markdown_values = [m.value for m in at.markdown]
    assert not any("삼성전자" in v for v in markdown_values)  # DART filtered out entirely

    # Both EDGAR items map to the identical clean title ("Current Report
    # — Form 8-K"), so ordering is proven via each item's own distinct
    # filed-date badge instead of its (no longer distinguishing) title.
    newer_idx = next(i for i, v in enumerate(markdown_values) if "Filed Sep 2, 2026" in v)
    older_idx = next(i for i, v in enumerate(markdown_values) if "Filed Aug 20, 2026" in v)
    assert newer_idx < older_idx
