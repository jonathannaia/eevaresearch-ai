"""Analyst view — pure template-logic tests (no Streamlit runtime).
AppTest-level rendering tests live in test_radar_inbox_page.py, matching
this repo's existing convention. Zero network calls, zero fixtures beyond
plain in-memory FilingEvent/CandidateSignal construction."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.models.models import (
    CandidateSignal,
    CandidateStatus,
    ExtractionState,
    FilingEvent,
    StateTransition,
    Translation,
    TranslationState,
)
from src.ui.components.analyst_view import (
    _INSUFFICIENT_EXCERPT_TEXT,
    _MIN_SUBSTANTIVE_EXCERPT_CHARS,
    _WHY_IT_MATTERS_TEMPLATES,
    _matched_category,
    _source_facts_html,
    _why_entered_radar_phrases,
    should_render_analyst_view,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _filing(**overrides) -> FilingEvent:
    defaults = dict(
        rcept_no="20260812000001", corp_code="00164779", corp_name="SK Hynix", stock_code="000660",
        report_nm="조회공시요구(풍문또는보도)에대한답변(미확정)", rcept_dt="20260812", flr_nm="SK 하이닉스",
        source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260812000001",
        retrieved_at=_now_iso(),
    )
    defaults.update(overrides)
    return FilingEvent(**defaults)


def _candidate(filing: FilingEvent, **overrides) -> CandidateSignal:
    defaults = dict(
        id="cand-1", filing=filing, matched_rules=["market_rumor_response:rumor_inquiry_or_response:풍문또는보도"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="한국거래소의조회공시요구에대한답변...", translation_state=TranslationState.TRANSLATED,
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    defaults.update(overrides)
    return CandidateSignal(**defaults)


def test_should_render_only_for_extracted_candidate_with_excerpt():
    filing = _filing()
    extracted = _candidate(filing, extraction_state=ExtractionState.EXTRACTED, excerpt_original="text present")
    assert should_render_analyst_view(extracted) is True

    deferred = _candidate(filing, status=CandidateStatus.PROCESSING_DEFERRED, extraction_state=ExtractionState.NOT_FETCHED, excerpt_original=None)
    assert should_render_analyst_view(deferred) is False

    failed = _candidate(filing, status=CandidateStatus.PARSE_FAILED, extraction_state=ExtractionState.PARSE_FAILED, excerpt_original=None)
    assert should_render_analyst_view(failed) is False

    extracted_but_empty_excerpt = _candidate(filing, extraction_state=ExtractionState.EXTRACTED, excerpt_original="")
    assert should_render_analyst_view(extracted_but_empty_excerpt) is False


def test_matched_category_skips_amendment_marker():
    assert _matched_category(["amendment_or_correction", "earnings:earnings_or_results_report:실적"]) == "earnings"
    assert _matched_category(["market_rumor_response:rumor_inquiry_or_response:풍문또는보도"]) == "market_rumor_response"
    assert _matched_category(["amendment_or_correction"]) is None
    assert _matched_category([]) is None


def test_source_facts_text_is_deterministic_and_grounded_only_in_structured_fields():
    filing = _filing(corp_name="SK Hynix", report_nm="조회공시요구(풍문또는보도)에대한답변(미확정)", source_name="OpenDART / DART", rcept_dt="20260812")
    text = _source_facts_html(filing)
    assert text == "SK Hynix filed “조회공시요구(풍문또는보도)에대한답변(미확정)” with OpenDART / DART on 20260812."
    # No invented amount, date, or counterparty beyond the structured fields.
    assert "KRW" not in text
    assert "China" not in text
    assert "trillion" not in text


def test_why_entered_radar_dart_preserves_native_keyword_and_labels_as_keyword_match():
    phrases = _why_entered_radar_phrases("OpenDART / DART", ["market_rumor_response:rumor_inquiry_or_response:풍문또는보도"])
    assert phrases == ["Market rumor response — matched keyword “풍문또는보도”"]


def test_why_entered_radar_edgar_preserves_item_number_as_keyword_match():
    phrases = _why_entered_radar_phrases("SEC EDGAR", ["earnings_or_results:8-K item 2.02"])
    assert phrases == ["Earnings or results (8-K item 2.02)"]


def test_why_entered_radar_edinet_never_calls_a_code_match_a_keyword_match():
    phrases = _why_entered_radar_phrases("EDINET", ["annual_securities_report:010:030000:120"])
    assert len(phrases) == 1
    assert "keyword" not in phrases[0].lower()
    assert "010:030000:120" in phrases[0]
    assert "matched by filing type/form code" in phrases[0]


def test_why_entered_radar_handles_amendment_marker():
    phrases = _why_entered_radar_phrases("OpenDART / DART", ["amendment_or_correction"])
    assert phrases == ["Amends or corrects an earlier filing"]


def test_insufficient_excerpt_text_is_the_exact_approved_sentence():
    """Phase 1 "What happened" fallback — must be this exact sentence,
    verbatim, whenever the excerpt is shorter than
    _MIN_SUBSTANTIVE_EXCERPT_CHARS. Never invented or paraphrased per
    filing."""
    assert _INSUFFICIENT_EXCERPT_TEXT == (
        "The filing was detected, but the available excerpt is not sufficient "
        "to summarize the disclosure reliably. Read the original filing."
    )


def test_min_substantive_excerpt_chars_is_the_approved_threshold():
    """Source-neutral length gate, not ExcerptQuality (DART-only and,
    even within DART, prone to false positives — see analyst_view.py's
    own module docstring for the real Samsung example that motivated
    this change)."""
    assert _MIN_SUBSTANTIVE_EXCERPT_CHARS == 40


def test_why_it_matters_templates_only_cover_market_rumor_response():
    """Deliberately sparse: only one real category has a hand-written
    "Why it matters" template — every other category renders nothing
    there, never a generic hedge invented to fill the section."""
    assert set(_WHY_IT_MATTERS_TEMPLATES.keys()) == {"market_rumor_response"}
    assert _WHY_IT_MATTERS_TEMPLATES["market_rumor_response"] == (
        "This may matter because it is a company's formal response to reported "
        "information — not yet a confirmed transaction."
    )
