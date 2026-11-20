"""Focused unit tests for src.ui.components.radar_status — specifically
review_needed_tag_html(), the HTML-rendering wrapper around
filing_display.review_needed() (design/DECISIONS.md, "Review needed"
badge). Calls the function directly with a constructed RadarItem — no
Streamlit runtime, no AppTest — complementing the pure-predicate tests
in tests/test_filing_display.py and the full-page rendering proof in
tests/test_radar_card_public_contract.py by testing this one seam in
isolation, including the exact CSS class the badge must use."""
from __future__ import annotations

from datetime import datetime, timezone

from src.models.models import FilingEvent
from src.ui.components.radar_status import RadarItem, review_needed_tag_html


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _filing(corp_code: str, stock_code: str, source_name: str = "EDINET") -> FilingEvent:
    return FilingEvent(
        rcept_no="S100Z0TEST", corp_code=corp_code, corp_name="Ambiguous Issuer Co.", stock_code=stock_code,
        report_nm="臨時報告書", rcept_dt="2026-09-04", flr_nm="Ambiguous Issuer Co.",
        retrieved_at=_now_iso(), source_name=source_name, original_language="Japanese",
    )


def test_badge_renders_with_uncertainty_styling_for_missing_corp_code():
    item = RadarItem(filing=_filing(corp_code="", stock_code="40630"), candidate=None)
    html = review_needed_tag_html(item)
    assert html is not None
    assert 'class="er-chip er-chip-uncertainty"' in html
    assert "Review needed" in html


def test_badge_renders_with_uncertainty_styling_for_missing_stock_code():
    item = RadarItem(filing=_filing(corp_code="E00776", stock_code=""), candidate=None)
    html = review_needed_tag_html(item)
    assert html is not None
    assert 'class="er-chip er-chip-uncertainty"' in html
    assert "Review needed" in html


def test_badge_includes_the_tooltip_text():
    item = RadarItem(filing=_filing(corp_code="", stock_code="40630"), candidate=None)
    html = review_needed_tag_html(item)
    assert "Issuer or role unresolved. Evidence collected, mapping under review." in html


def test_badge_is_none_for_a_fully_resolved_filing_with_both_identifiers():
    item = RadarItem(filing=_filing(corp_code="E00776", stock_code="40630"), candidate=None)
    assert review_needed_tag_html(item) is None


def test_badge_applies_to_a_bare_new_filing_with_no_candidate():
    """The badge is derived purely from FilingEvent's own stored
    fields — it must never require a CandidateSignal to exist."""
    item = RadarItem(filing=_filing(corp_code="", stock_code="40630"), candidate=None)
    assert item.is_new_filing is True
    assert review_needed_tag_html(item) is not None


def test_badge_never_uses_the_loud_negative_tag_reserved_for_genuine_failures():
    """This codebase reserves er-tag-neg for genuine failures (see
    RETRIEVAL_FAILURE_NOTE's own docstring) — "Review needed" is
    advisory, not a failure, and must use the quiet er-chip-uncertainty
    treatment instead."""
    item = RadarItem(filing=_filing(corp_code="", stock_code="40630"), candidate=None)
    html = review_needed_tag_html(item)
    assert "er-tag-neg" not in html
