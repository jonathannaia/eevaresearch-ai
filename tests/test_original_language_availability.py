"""Original-language availability: when a filing has readable text of its
own and no usable English translation of it, the card shows that text —
explicitly labelled — instead of showing nothing.

Before this change the Filing summary rendered the honest fallback and
nothing else whenever no stored translation existed, even though the
original excerpt was stored, readable, and already reachable through its
own toggle immediately below. The content was never missing; the English
was. What was missing from the card was the labelling that makes showing
the original honest.

This file tests the RENDERED CARD, not helper return values.

FIXTURE PROVENANCE, stated so the limits are visible:
  * _KO_TREASURY is transcribed from the original-language excerpt
    rendered on the live card for DART receipt 20260821000616 (삼성전자,
    주요사항보고서(자기주식취득결정)) during the read-only UI baseline of
    2026-10-28. Faithful for the fields under test; SHORTENED IN THE TAIL
    by the capture, so it is not a complete copy of the stored excerpt.
  * _KO_REQUEST is transcribed the same way from receipt 20260821800214
    (SK하이닉스, 조회공시요구(풍문또는보도)) and is complete as rendered.
  * _JA_EXTRAORDINARY is transcribed the same way from EDINET document
    S100Z7OF (Mitsubishi Electric, 臨時報告書); shortened in the tail.
  * Everything else is SYNTHETIC and structural — a shape, not a record.

Nothing here reads data/cache, makes a network call, invokes a
translation provider or model, or touches a stored record.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access.dart import candidate_store
from src.logic import filing_display
from src.models.models import (
    CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, Translation, TranslationState,
)
from src.ui.components.radar_card import MACHINE_TRANSLATION_NOTE, TRANSLATED_TITLE_NOTE

_HARNESS = Path(__file__).parent / "apptest_pages" / "radar_inbox_page.py"


# --- fixtures -------------------------------------------------------------

_KO_TREASURY = (
    "자기주식 취득 결정 1. 취득예정주식(주) 보통주식 53,285,968 기타주식 - "
    "2. 취득예정금액(원) 보통주식 14,999,999,992,000 기타주식 - "
    "3. 취득예상기간 시작일 2026년 08월 24일 종료일 2026년 11월 21일 "
    "4. 보유예상기간 시작일 - 종료일 - 5. 취득목적 임직원 주식보상 "
    "6. 취득방법 유가증권시장을 통한 장내 매수 "
    "7. 위탁투자중개업자 삼성증권, 신한투자증권, KB증권"
)
_KO_TREASURY_TITLE_NATIVE = "주요사항보고서(자기주식취득결정)"

_KO_REQUEST = (
    "SK하이닉스/조회공시 요구(풍문 또는 보도)/(2026.08.21)조회공시 요구(풍문 또는 보도) "
    "조회공시 요구(풍문 또는 보도) SK하이닉스(주), 일본에 반도체 공장 짓는다 보도에 대한 조회공시 요구 "
    "1. 조회공시요구내용 일본에 반도체 공장 짓는다 보도에 대한 사실 여부 및 구체적인 내용 "
    "2. 공시시한 2026-08-21 18:00 3. 기타 투자판단과 관련한 중요사항 - ※ 관련공시 -"
)

_JA_EXTRAORDINARY = (
    "当社の財政状態、経営成績及びキャッシュ・フローの状況に著しい影響を与える事象が発生する見込みとなりましたので、"
    "金融商品取引法第24条の５第４項の規定に基づき、本臨時報告書を提出します。 ２【報告内容】 "
    "(1）当該事象の発生年月日 2026年10月16日 (2）当該事象の内容 当社は、鴻海精密工業股份有限公司と、"
    "自動車機器事業の共同運営を通じた戦略的提携の検討開始に関する覚書を2026年4月24日に締結しました。"
)

# A cover page and its two repeats, with nothing after them.
_KO_HEADER_ONLY = (
    "SK하이닉스/풍문 또는 보도에 대한 해명/(2026.07.22)"
    "풍문 또는 보도에 대한 해명 풍문 또는 보도에 대한 해명"
)

# Raw XBRL — fails the readability gate, so there is nothing to offer.
_KO_UNREADABLE = (
    "<xbrli:context id='c1'><xbrli:entity><xbrli:identifier scheme='http://dart.fss.or.kr'>"
    "00126380</xbrli:identifier></xbrli:entity></xbrli:context>"
    "<dart:AcquisitionAmount contextRef='c1' unitRef='KRW'>14999999992000</dart:AcquisitionAmount>"
)

# A rumour clarification whose own answer is visibly cut mid-clause: the
# recognised-form/UNSAFE path, which must never fall through to the
# leading substring (that substring is the ALLEGATION).
_KO_CUT_CLARIFICATION = (
    "1. 풍문 또는 보도의 내용 SK하이닉스, 일본 공장 인수 추진 "
    "2. 풍문 또는 보도의 매체 중앙일보 "
    "3. 풍문 또는 보도의 내용에 대한 해명내용 당사는 다양한 방안을 검토하고 있으나, 현재까지"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _settings(tmp_path) -> Settings:
    return Settings(
        dart_api_key="dart-key", translation_api_key="deepl-key",
        edgar_user_agent="EevaResearch test@example.com",
        edinet_subscription_key="test-key", cache_dir=tmp_path,
    )


def _translation(text: str, source_lang: str) -> Translation:
    return Translation(
        translated_text=text, provider="DeepL", source_lang=source_lang,
        target_lang="en", translated_at=_now_iso(),
    )


def _dart_filing(rcept_no: str, report_nm: str) -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm=report_nm, rcept_dt="20260821", flr_nm="삼성전자",
        source_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
        retrieved_at=_now_iso(),
    )


def _edinet_filing(doc_id: str) -> FilingEvent:
    return FilingEvent(
        rcept_no=doc_id, corp_code="E01739", corp_name="Mitsubishi Electric Corporation",
        stock_code="65030", report_nm="臨時報告書", rcept_dt="2026-10-19",
        flr_nm="三菱電機株式会社", pblntf_ty="030000", pblntf_detail_ty="120", ordinance_code="010",
        source_url=f"https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}",
        retrieved_at=_now_iso(), source_name="EDINET", original_language="Japanese",
    )


def _edgar_filing(accession: str) -> FilingEvent:
    return FilingEvent(
        rcept_no=accession, corp_code="0000319201", corp_name="KLA Corp", stock_code="KLAC",
        report_nm="8-K", rcept_dt="2026-10-28", flr_nm="KLA Corp", pblntf_ty="8-K",
        source_url="https://www.sec.gov/Archives/edgar/data/319201/000031920126000032/",
        retrieved_at=_now_iso(), source_name="SEC EDGAR", original_language="English",
        primary_document="klac-8k.htm",
    )


def _seed_dart(tmp_path, filing: FilingEvent, candidate: CandidateSignal) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "dart_corp_codes.json").write_text(json.dumps(
        {"005930": {"corp_code": "00126380", "corp_name": "삼성전자",
                    "source": "OpenDART corpCode.xml", "retrieved_at": _now_iso()}}), encoding="utf-8")
    (tmp_path / "dart_filing_events.json").write_text(json.dumps(
        {"seen_receipt_numbers": [filing.rcept_no], "filing_events": [asdict(filing)],
         "candidate_signals": []}, ensure_ascii=False), encoding="utf-8")
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})


def _seed_edinet(tmp_path, filing: FilingEvent, candidate: CandidateSignal) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "edinet_filing_events.json").write_text(json.dumps(
        {"seen_keys": [f"EDINET:{filing.corp_code}:{filing.rcept_no}"],
         "filing_events": [asdict(filing)], "candidate_signals": []}, ensure_ascii=False), encoding="utf-8")
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate}, "edinet_candidates.json")


def _seed_edgar(tmp_path, filing: FilingEvent, candidate: CandidateSignal) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "edgar_filing_events.json").write_text(json.dumps(
        {"seen_keys": [f"SEC EDGAR:{filing.corp_code}:{filing.rcept_no}"],
         "filing_events": [asdict(filing)], "candidate_signals": []}, ensure_ascii=False), encoding="utf-8")
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate}, "edgar_candidates.json")


def _candidate(
    filing: FilingEvent, *, excerpt: str | None, excerpt_translation: str | None = None,
    title_translation: str | None = None, state: TranslationState = TranslationState.NOT_REQUESTED,
    rules: list[str] | None = None, source_lang: str = "ko",
) -> CandidateSignal:
    return CandidateSignal(
        id=f"cand-{filing.rcept_no}", filing=filing,
        matched_rules=rules if rules is not None else ["treasury_decision:자기주식"],
        confidence="High", status=CandidateStatus.NEEDS_REVIEW,
        extraction_state=ExtractionState.EXTRACTED if excerpt else ExtractionState.NOT_FETCHED,
        translation_state=state,
        excerpt_original=excerpt,
        excerpt_translation=_translation(excerpt_translation, source_lang) if excerpt_translation else None,
        title_translation=_translation(title_translation, source_lang) if title_translation else None,
    )


def _run(tmp_path) -> AppTest:
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=20)
        at.run()
    return at


def _text(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown if not m.value.startswith("<style>"))


def _render_dart(tmp_path, **kwargs) -> str:
    filing = _dart_filing(kwargs.pop("rcept_no", "20260821000616"),
                          kwargs.pop("report_nm", _KO_TREASURY_TITLE_NATIVE))
    _seed_dart(tmp_path, filing, _candidate(filing, **kwargs))
    at = _run(tmp_path)
    assert not at.exception
    return _text(at)


_KO_LABEL = "Original language — Korean"
_JA_LABEL = "Original language — Japanese"
_NOTICE = filing_display.ENGLISH_TRANSLATION_UNAVAILABLE_TEXT

# The attribute must be asserted ON THE SUMMARY ELEMENT. A bare
# `lang="ko" in text` is vacuous: this card already puts lang_attr on an
# untranslated TITLE, so such an assertion passes even when the summary
# carries no attribute at all (proved by mutation control C3).
_SUMMARY_KO = '<div class="er-filing-summary" lang="ko">'
_SUMMARY_JA = '<div class="er-filing-summary" lang="ja">'
_SUMMARY_NO_LANG = '<div class="er-filing-summary">'


# --- 1. no translation at all ---------------------------------------------

def test_korean_excerpt_with_no_translation_is_shown_and_labelled(tmp_path):
    text = _render_dart(tmp_path, excerpt=_KO_TREASURY)
    assert _KO_LABEL in text
    assert _SUMMARY_KO in text
    assert _SUMMARY_NO_LANG not in text
    # The filing's own figures reach the card instead of an empty summary.
    assert "53,285,968" in text
    assert "14,999,999,992,000" in text
    assert filing_display.SUMMARY_UNAVAILABLE_TEXT not in text
    assert _NOTICE in text
    # Nothing here was translated, so the machine-translation caveat must
    # not appear anywhere on this card.
    assert MACHINE_TRANSLATION_NOTE not in text


def test_the_notice_names_no_retry_eta_or_failure_detail(tmp_path):
    text = _render_dart(tmp_path, excerpt=_KO_TREASURY, state=TranslationState.UNAVAILABLE)
    for banned in ("retry", "Retry", "will be", "shortly", "soon", "being prepared",
                   "pending", "rate_limit", "timeout", "DeepL", "API"):
        assert banned not in text, banned


# --- 2. title translated, excerpt not -------------------------------------

def test_title_only_translation_still_shows_the_original_and_says_which_half(tmp_path):
    text = _render_dart(
        tmp_path, excerpt=_KO_TREASURY,
        title_translation="Report on Material Matters (Treasury Stock Acquisition Decision)",
    )
    assert _KO_LABEL in text
    assert "53,285,968" in text
    assert _NOTICE in text
    assert "The title was translated; the excerpt was not." in text


def test_a_translated_title_above_an_original_language_summary_scopes_its_caveat(tmp_path):
    """The real rendered combination, in order: an English title, the
    translation caveat, then a KOREAN summary. The caveat sits directly
    above that summary, so it must name the TITLE — an unqualified
    "Translation is machine-generated" there reads as describing the
    original-language text below it."""
    text = _render_dart(
        tmp_path, excerpt=_KO_TREASURY,
        title_translation="Report on Material Matters (Treasury Stock Acquisition Decision)",
    )
    assert "Report on Material Matters (Treasury Stock Acquisition Decision)" in text
    assert TRANSLATED_TITLE_NOTE in text
    assert MACHINE_TRANSLATION_NOTE not in text
    # ...and the caveat really does precede the original-language summary.
    assert text.index(TRANSLATED_TITLE_NOTE) < text.index(_KO_LABEL)
    assert text.index(_KO_LABEL) < text.index("53,285,968")


def test_a_translated_title_above_an_english_summary_keeps_the_title_caveat(tmp_path):
    """The title notice stays accurate in the unchanged case too."""
    text = _render_dart(
        tmp_path, excerpt=_KO_TREASURY,
        title_translation="Report on Material Matters",
        excerpt_translation="Treasury stock acquisition decision. Shares to be acquired: 53,285,968.",
        state=TranslationState.TRANSLATED,
    )
    assert TRANSLATED_TITLE_NOTE in text
    assert _KO_LABEL not in text
    assert _NOTICE not in text


def test_an_excerpt_translation_without_a_title_translation_is_not_called_title_only(tmp_path):
    text = _render_dart(
        tmp_path, excerpt=_KO_TREASURY,
        excerpt_translation="Treasury stock acquisition decision. 1. Shares to be acquired: 53,285,968.",
        state=TranslationState.TRANSLATED,
    )
    assert "The title was translated; the excerpt was not." not in text


# --- 3. usable excerpt translation: unchanged -----------------------------

def test_usable_excerpt_translation_is_unchanged(tmp_path):
    english = ("Treasury stock acquisition decision. 1. Shares to be acquired (common): 53,285,968. "
               "2. Planned acquisition amount (KRW): 14,999,999,992,000.")
    text = _render_dart(tmp_path, excerpt=_KO_TREASURY, excerpt_translation=english,
                        title_translation="Report on Material Matters",
                        state=TranslationState.TRANSLATED)
    assert "Treasury stock acquisition decision." in text
    assert _KO_LABEL not in text
    assert _NOTICE not in text


# --- 4. translation state wording ------------------------------------------

@pytest.mark.parametrize("state", [
    TranslationState.NOT_REQUESTED, TranslationState.PENDING,
    TranslationState.UNAVAILABLE, TranslationState.TRANSLATED,
])
def test_no_translation_state_is_ever_described_on_the_card(tmp_path, state):
    """The candidate-level state cannot establish THIS excerpt's
    translation status at render time (see filing_display's own comment:
    DART may have written it from the title attempt, EDINET's PENDING
    means "no provider configured", and NOT_REQUESTED is the dataclass
    default). So the card states only what it can see — that no usable
    English is present — for every state alike."""
    text = _render_dart(tmp_path, excerpt=_KO_TREASURY, state=state)
    assert _NOTICE in text
    for banned in ("was requested", "has not completed", "did not succeed",
                   "Not requested", "Translation pending", "Translation unavailable", "Translated"):
        assert banned not in text, banned


def test_the_notice_is_identical_whatever_the_stored_state_says(tmp_path):
    """Mutation-visible: if any state ever reintroduced a detail, these
    two renderings would stop matching."""
    import re
    renders = []
    for state in (TranslationState.NOT_REQUESTED, TranslationState.UNAVAILABLE):
        target = tmp_path / state.name
        text = _render_dart(target, excerpt=_KO_TREASURY, state=state)
        start = text.index(_NOTICE)
        renders.append(re.sub(r'style="[^"]*"', "", text[start:start + 200]))
    assert renders[0] == renders[1]


# --- 5. nothing substantive is manufactured -------------------------------

def test_header_only_native_text_keeps_the_honest_fallback(tmp_path):
    text = _render_dart(tmp_path, excerpt=_KO_HEADER_ONLY,
                        rules=["rumor_clarification:rumor:풍문"],
                        report_nm="풍문또는보도에대한해명")
    assert filing_display.SUMMARY_UNAVAILABLE_TEXT in text
    assert _KO_LABEL not in text          # nothing was promoted to a summary
    assert _NOTICE in text                 # but the reason is still stated


def test_unreadable_native_text_keeps_the_honest_fallback_and_offers_nothing(tmp_path):
    text = _render_dart(tmp_path, excerpt=_KO_UNREADABLE)
    assert filing_display.SUMMARY_UNAVAILABLE_TEXT in text
    assert _KO_LABEL not in text
    assert _NOTICE not in text             # there is no readable original to offer


def test_no_excerpt_at_all_keeps_the_honest_fallback(tmp_path):
    text = _render_dart(tmp_path, excerpt=None)
    assert filing_display.SUMMARY_UNAVAILABLE_TEXT in text
    assert _KO_LABEL not in text
    assert _NOTICE not in text


def test_unsafe_routing_still_blocks_the_leading_substring_on_the_native_path(tmp_path):
    """The clarification field is visibly cut, so the form is recognised
    but unsafe. The allegation must not reach the card in its place —
    the native path changes WHICH TEXT is eligible, never this rule."""
    text = _render_dart(tmp_path, excerpt=_KO_CUT_CLARIFICATION,
                        rules=["rumor_clarification:rumor:풍문"],
                        report_nm="풍문또는보도에대한해명")
    assert filing_display.SUMMARY_UNAVAILABLE_TEXT in text
    assert "일본 공장 인수 추진" not in text
    assert _KO_LABEL not in text


# --- 6. Japanese -----------------------------------------------------------

def test_japanese_excerpt_with_no_translation_is_labelled_japanese(tmp_path):
    filing = _edinet_filing("S100Z7OF")
    _seed_edinet(tmp_path, filing, _candidate(
        filing, excerpt=_JA_EXTRAORDINARY, source_lang="ja",
        rules=["extraordinary_report:010:030000:120"],
    ))
    at = _run(tmp_path)
    assert not at.exception
    text = _text(at)
    assert _JA_LABEL in text
    assert _KO_LABEL not in text
    assert _SUMMARY_JA in text
    assert _SUMMARY_KO not in text
    assert "2026年10月16日" in text
    assert _NOTICE in text
    assert MACHINE_TRANSLATION_NOTE not in text


def test_a_korean_card_is_never_labelled_japanese(tmp_path):
    text = _render_dart(tmp_path, excerpt=_KO_TREASURY)
    assert _JA_LABEL not in text
    assert _SUMMARY_JA not in text


def test_an_english_summary_keeps_no_language_attribute(tmp_path):
    english = "Treasury stock acquisition decision. Shares to be acquired: 53,285,968."
    text = _render_dart(tmp_path, excerpt=_KO_TREASURY, excerpt_translation=english,
                        state=TranslationState.TRANSLATED)
    assert _SUMMARY_NO_LANG in text
    assert _SUMMARY_KO not in text


# --- 7. EDGAR unaffected ---------------------------------------------------

def test_edgar_card_is_unaffected(tmp_path):
    filing = _edgar_filing("0000319201-26-000032")
    _seed_edgar(tmp_path, filing, _candidate(
        filing,
        excerpt=("Item 2.02 Results of Operations and Financial Condition. On October 28, 2026, "
                 "KLA Corporation issued a press release announcing selected financial and "
                 "operating results for its first quarter of fiscal year 2027."),
        rules=["earnings_release:8-K:2.02"],
    ))
    at = _run(tmp_path)
    assert not at.exception
    text = _text(at)
    assert "KLA Corporation issued a press release" in text
    assert "Original language" not in text
    assert _NOTICE not in text
    assert _SUMMARY_KO not in text and _SUMMARY_JA not in text


# --- 8. stored text is untouched ------------------------------------------

def test_the_stored_excerpt_view_is_still_the_stored_text_verbatim(tmp_path):
    filing = _dart_filing("20260821000616", _KO_TREASURY_TITLE_NATIVE)
    _seed_dart(tmp_path, filing, _candidate(filing, excerpt=_KO_TREASURY))
    at = _run(tmp_path)
    assert not at.exception
    buttons = [b for b in at.button if b.label == "View original filing excerpt"]
    assert buttons, "the original-excerpt toggle must still be offered"
    buttons[0].click()
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=_settings(tmp_path)):
        at.run()
    text = _text(at)
    assert _KO_TREASURY in text       # verbatim, uncleaned
    assert "Original filing excerpt" in text
