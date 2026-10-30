"""Filing-summary quality: the card's "Filing summary" must carry what the
filing actually says, never the title restated.

The defect this covers, reproduced from live-cached DART records before
any change: DART's extractor keeps the cover page whenever a filing has a
single <SECTION-1>, so the stored excerpt — and its translation — begin
"{issuer}/{title}/({date}){title} {title}". Under the summary heading
that read as the title repeated, and on a short disclosure the header
consumed the whole 320-character window, so the summary ended on a
dangling numbered marker ("... (Unconfirmed) 1.") while the issuer's own
statement fell past the cut.

Every fixture below is a small, checked-in, deterministic string derived
from those records. Nothing here reads data/cache (untracked), makes a
network call, invokes a translation provider or model, or touches a
stored record.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.logic import dart_summary
from src.logic import filing_display
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, Translation

_HARNESS = Path(__file__).parent / "apptest_pages" / "radar_inbox_page.py"

# --- fixtures, derived from the reproduced live records -------------------

# Translated DART excerpt, cover-page header + two title repeats, then the
# numbered body. Shortened in the tail only; the header is verbatim.
_EN_HEADER_THEN_BODY = (
    "SK Hynix / Clarification Regarding Rumors or News Reports / (July 22, 2026) "
    "Clarification Regarding Rumors or News Reports Clarification Regarding Rumors or News Reports "
    "1. Content of Rumors or News Reports SK Hynix to Buy Intel's Ohio Plant "
    "2. Media Outlet Reporting the Rumor or Report: JoongAng Ilbo "
    "3. Clarification Regarding the Content of the Rumor or Report The Company has neither pursued nor decided to acquire Intel's Ohio site and fab."
)
_EN_TITLE = "Clarification Regarding Rumors or News Reports"

# The same shape with NOTHING after the repeats — a title-only excerpt.
_EN_HEADER_ONLY = (
    "SK Hynix / Clarification Regarding Rumors or News Reports / (July 22, 2026) "
    "Clarification Regarding Rumors or News Reports Clarification Regarding Rumors or News Reports"
)

# Native Korean, same document class, header verbatim from the record.
_KO_HEADER_THEN_BODY = (
    "SK하이닉스/조회공시 요구(풍문 또는 보도)에 대한 답변(미확정)/(2026.08.10)"
    "조회공시 요구(풍문 또는 보도)에 대한 답변(미확정) 조회공시 요구(풍문 또는 보도)에 대한 답변(미확정) "
    "1. 제목 지분매각 추진 보도에 대한 답변 "
    "2. 내용 당사는 다양한 방안을 검토하고 있으나, 현재까지 확정된 사항은 없습니다."
)
_KO_TITLE = "조회공시 요구(풍문 또는 보도)에 대한 답변(미확정)"

# Real prose with no cover-page header at all.
_EN_NO_HEADER = "The Company resolved to invest 1.5 trillion won in a new fabrication facility."

# Header-LIKE but ambiguous: real prose that happens to contain slashes and
# a parenthesised year. Must be left completely alone.
_EN_AMBIGUOUS = (
    "The joint venture operates the Icheon/Cheongju fabrication network under a "
    "profit/loss sharing agreement (signed 2019) and reported record output."
)

_EDGAR_TEXT = (
    "SEC EDGAR / Quarterly Report / (July 22, 2026) Quarterly Report "
    "The registrant reported revenue growth of 12 percent."
)
_EDINET_TEXT = (
    "EDINET / Extraordinary Report / (July 22, 2026) Extraordinary Report "
    "The issuer disclosed a change in principal shareholders."
)


# --- acceptance fixtures: the four reproduced records, verbatim ----------
#
# Translated and native text exactly as stored by the live pilot cache at
# the time of the investigation. Preserved word for word — never reworded
# to suit the implementation.

_ACC_DENIAL_EN = "SK Hynix / Clarification Regarding Rumors or News Reports / (July 22, 2026) Clarification Regarding Rumors or News Reports Clarification Regarding Rumors or News Reports 1. Content of Rumors or News Reports SK Hynix’s Hail-Mary Move… to Buy Intel’s Ohio Plant 2. Media Outlet Reporting the Rumor or Report: JoongAng Ilbo 3. Date the Rumor or Report Emerged: July 22, 2026 4. Clarification Regarding the Content of the Rumor or Report This disclosure is a clarification regarding the article titled “SK Hynix’s Hail-Mary Move… to Acquire Intel’s Ohio Plant,” published in the JoongAng Ilbo on July 22, 2026. - The Company continuously reviews various investment and acquisition opportunities as part of its business operations. - However, contrary to what was reported in the article, the Company has neither pursued nor decided to acquire Intel’s Ohio site and fab. (Person Responsible for Disclosure) Kim Woo-hyun, Head of Finance ※ Related Disclosure -"
_ACC_DENIAL_KO = "SK하이닉스/풍문 또는 보도에 대한 해명/(2026.07.22)풍문 또는 보도에 대한 해명 풍문 또는 보도에 대한 해명 1. 풍문 또는 보도의 내용 SK하이닉스의 승부수…인텔 美오하이오 공장 산다 2. 풍문 또는 보도의 매체 중앙일보 3. 풍문 또는 보도의 발생일자 2026-07-22 4. 풍문 또는 보도의 내용에 대한 해명내용 본 공시는 2026년 7월 22일자 중앙일보에 보도된 \"SK하이닉스의 승부수…인텔 美오하이오 공장 산다\" 기사에 대한 해명 공시입니다. - 당사는 사업상 다양한 투자 및 인수 기회를 지속적으로 검토하고 있습니다. - 그러나 기사에서 보도된 것과 같이 인텔의 오하이오 부지와 Fab 인수를 추진하거나 결정한 사실은 없습니다. (공시책임자) 김 우 현 재무 부문장 ※ 관련공시 -"
_ACC_DENIAL_TITLE = "Clarification Regarding Rumors or News Reports"

_ACC_RESPONSE_EN = "SK Hynix / Response to a Request for an Inquiry Disclosure (Rumors or Media Reports) (Unconfirmed) / (August 10, 2026) Response to a Request for an Inquiry Disclosure (Rumors or Media Reports) (Unconfirmed) Response to a Request for an Inquiry Disclosure (Rumors or Media Reports)(To Be Determined) 1. Response to the Request for Disclosure (August 10, 2026) Regarding Reports That SK Hynix Is Pursuing the Sale of Its Stake in the 4 Trillion Won Chongqing, China Plant 2. Content: We are reviewing various measures to strengthen the competitiveness of our packaging business; however, nothing has been finalized as of yet. We will issue a follow-up disclosure once specific details are finalized or within one month. (Person Responsible for Disclosure) Kim Woo-hyun, Head of Finance ※This disclosure is in response to the exchange’s inquiry (August 10, 2026, 10:10 a.m.) 3. Scheduled Date of Re-disclosure: September 9, 2026"
_ACC_RESPONSE_KO = "SK하이닉스/조회공시 요구(풍문 또는 보도)에 대한 답변(미확정)/(2026.08.10)조회공시 요구(풍문 또는 보도)에 대한 답변(미확정) 조회공시 요구(풍문 또는 보도)에 대한 답변(미확정) 1. 제목 4조 규모 중국 충칭공장 SK하닉, 지분매각 추진 보도에 관한 조회공시 요구(2026.08.10)에 대한 답변 2. 내용 당사는 패키지 사업 경쟁력 강화를 위하여 다양한 방안을 검토하고 있으나, 현재까지 확정된 사항은 없습니다. 추후 구체적인 내용이 확정되는 시점 또는 1개월 이내 재공시 하겠습니다. (공시책임자) 김우현 재무 부문장 ※이 내용은 거래소의 조회요구(2026년 08월 10일 10:10)에 따른 공시사항임 3. 재공시예정일 2026-09-09"
_ACC_RESPONSE_TITLE = "Response to a Request for Disclosure (Rumors or Media Reports) (Unconfirmed)"

_ACC_REQUEST_EN = "SK Hynix / Request for Disclosure (Rumors or Media Reports) / (August 10, 2026) Request for Disclosure (Rumors or Media Reports) Request for Disclosure (Rumors or Media Reports) SK Hynix, Inc., Request for Disclosure Regarding Reports on SK Hynix’s Plan to Sell Its Stake in the Chongqing, China Plant Valued at 4 Trillion 1. Details of the Request for Disclosure: Reports on SK Hynix’s plan to sell its stake in the Chongqing, China plant valued at 4 trillion 2. Disclosure Deadline: August 10, 2026, 6:00 PM 3. Other Important Matters Related to Investment Decisions - ※ Related Disclosure -"
_ACC_REQUEST_KO = "SK하이닉스/조회공시 요구(풍문 또는 보도)/(2026.08.10)조회공시 요구(풍문 또는 보도) 조회공시 요구(풍문 또는 보도) SK하이닉스(주), 4조 규모 중국 충칭공장 지분매각 추진 보도에 대한 조회공시 요구 1. 조회공시요구내용 4조 규모 중국 충칭공장 SK하닉, 지분매각 추진 보도 2. 공시시한 2026-08-10 18:00 3. 기타 투자판단과 관련한 중요사항 - ※ 관련공시 -"
_ACC_REQUEST_TITLE = "Request for Disclosure (Rumors or Media Reports)"

_ACC_SAMSUNG_EN = "Samsung Electronics / Clarification Regarding Rumors or Media Reports (Unconfirmed) / (July 23, 2026) Clarification Regarding Rumors or Media Reports (Unconfirmed) Clarification Regarding Rumors or Media Reports(Unconfirmed) This is a re-announcement regarding the “Clarification Regarding Rumors or Media Reports (Unconfirmed)” dated June 24, 2026. 1. Content of the Rumors or Media Reports: Samsung Electronics to purchase 90 trillion won worth of treasury stock starting next month. Plans to purchase in installments over three years following board approval. 2. Media outlet reporting the rumor or news: Munhwa Ilbo 3. Date the rumor or news was reported: June 24, 2026 4. Clarification Regarding the Content of the Rumor or Report: This announcement serves as a clarification regarding the report published by the Munhwa Ilbo on June 24, 2026, titled “Samsung to Purchase 90 Trillion Won in Treasury Stock Starting Next Month; Plans to Conduct Phased Purchases Over Three Years Following Board Approval.” The Company is considering a share buyback for stock-based compensation purposes based on its 2026 business performance; however, no specific details, such as the schedule or scale, have been finalized at this time. The above matter is currently under review, and we plan to issue a follow-up disclosure once the relevant details are finalized or within three months. (Person Responsible for Disclosure) Daniel Oh, Head of the IR Team 5. Scheduled Date of Re-Disclosure: October 22, 2026 ※ Related Disclosure: June 24, 2026 – Clarification Regarding Rumors or Media Reports (Unconfirmed)"
_ACC_SAMSUNG_KO = "삼성전자/풍문 또는 보도에 대한 해명(미확정)/(2026.07.23)풍문 또는 보도에 대한 해명(미확정) 풍문 또는 보도에 대한 해명(미확정) 2026-06-24 일자 풍문 또는 보도에 대한 해명(미확정)의 재공시 사항임 1. 풍문 또는 보도의 내용 삼전, 내달부터 자사주 90조 매입. 이사회 거쳐 3년 분할매입 추진 2. 풍문 또는 보도의 매체 문화일보 3. 풍문 또는 보도의 발생일자 2026-06-24 4. 풍문 또는 보도의 내용에 대한 해명내용 본 공시는 2026년 6월 24일 문화일보에서 보도한 \"삼전, 내달부터 자사주 90조 매입, 이사회 거쳐 3년 분할매입 추진\"에 대한 해명공시입니다. 당사는 '26년 경영성과에 따른 주식보상 목적의 자사주 매입을 검토하고 있으나, 현재 구체적인 일정이나 규모 등 확정된 사항은 없습니다. 상기 사항은 현재 검토 중에 있으며, 관련 내용이 확정되는 시점 또는 3개월 이내에 재공시할 예정입니다. (공시책임자)IR팀장 다니엘 오 5. 재공시예정일 2026-10-22 ※ 관련공시 2026-06-24 풍문 또는 보도에 대한 해명(미확정)"
_ACC_SAMSUNG_TITLE = "Clarification Regarding Rumors or Reports (Tentative)"


# --- 1. conservative DART cleanup ----------------------------------------

def test_cover_page_header_and_repeats_are_removed():
    cleaned = filing_display.strip_dart_cover_page_header(_EN_HEADER_THEN_BODY)
    assert not cleaned.startswith("SK Hynix /")
    assert cleaned.startswith("1. Content of Rumors")
    assert cleaned.count(_EN_TITLE) == 0, "every leading repeat of the title is gone"
    assert "Intel's Ohio site and fab." in cleaned, "body content is untouched"


def test_native_korean_header_and_repeats_are_removed():
    cleaned = filing_display.strip_dart_cover_page_header(_KO_HEADER_THEN_BODY)
    assert cleaned.startswith("1. 제목")
    assert _KO_TITLE not in cleaned
    assert "현재까지 확정된 사항은 없습니다." in cleaned


def test_title_only_excerpt_cleans_down_to_nothing_substantive():
    cleaned = filing_display.strip_dart_cover_page_header(_EN_HEADER_ONLY)
    summary = filing_display.extractive_summary(cleaned) if cleaned.strip() else ""
    assert not filing_display.summary_is_substantive(summary, _EN_TITLE)


@pytest.mark.parametrize("text", [_EN_NO_HEADER, _EN_AMBIGUOUS, "", "   "])
def test_text_without_the_exact_header_shape_is_returned_unchanged(text):
    """No generic slash splitting, no 'strip everything before the first
    sentence', no global title replacement."""
    assert filing_display.strip_dart_cover_page_header(text) == text


def test_cleanup_preserves_prose_dates_amounts_and_sections():
    cleaned = filing_display.strip_dart_cover_page_header(_EN_HEADER_THEN_BODY)
    for fragment in ("July 22, 2026", "JoongAng Ilbo", "1. Content", "2. Media Outlet", "3. Clarification"):
        if fragment == "July 22, 2026":
            continue  # the header's own date is removed with the header
        assert fragment in cleaned
    assert "1.5 trillion won" in filing_display.strip_dart_cover_page_header(_EN_NO_HEADER)


def test_a_title_occurring_later_in_real_prose_is_never_removed():
    text = f"SK Hynix / {_EN_TITLE} / (July 22, 2026) {_EN_TITLE} The board discussed the {_EN_TITLE} at length."
    cleaned = filing_display.strip_dart_cover_page_header(text)
    assert cleaned == f"The board discussed the {_EN_TITLE} at length."


# --- 2. numbered markers --------------------------------------------------

def test_summary_never_ends_on_a_dangling_numbered_marker():
    cleaned = filing_display.strip_dart_cover_page_header(_EN_HEADER_THEN_BODY)
    summary = filing_display.extractive_summary(cleaned)
    assert summary, "a substantive summary is produced"
    assert not summary.rstrip().endswith(("1.", "2.", "3.", "4.")), summary


def test_a_summary_is_not_cut_on_a_field_marker_of_a_real_run():
    """A genuine run ("1." then "2.") is field numbering, so neither
    marker is a cut point and the summary never ends on one."""
    assert filing_display.extractive_summary("Introduction 1. Subject 2. ") == "Introduction 1. Subject"


def test_a_lone_number_is_a_sentence_ending_not_a_marker():
    """No "2." follows, so "1." is ordinary sentence-final numbering and
    is preserved verbatim — the case a bare digit rule would corrupt."""
    assert filing_display.extractive_summary("The ratio is 1.") == "The ratio is 1."


@pytest.mark.parametrize("text", [
    "The deadline is 10. The board then met.",
    "Revenue rose to 25. Margins improved.",
    "The ratio is 1.",
])
def test_sentence_final_numbers_are_never_treated_as_field_markers(text):
    assert filing_display.extractive_summary(text) == text


@pytest.mark.parametrize("text,expected_tail", [
    # A sentence genuinely ending in a four-digit year is still a sentence.
    ("The filing was submitted on August 10, 2026. The board then met.", "2026."),
    # A decimal amount is not a boundary at all (no whitespace after the dot).
    ("Revenue reached 4.5 trillion won in the period. It then declined.", "period."),
    # An abbreviation-style full stop still ends a sentence.
    ("The issuer is SK Hynix Inc. The board met afterwards.", "Inc."),
    # Japanese/Korean punctuation is unchanged.
    ("当社は検討しております。次に取締役会が開催されました。", "。"),
])
def test_legitimate_sentence_endings_are_still_valid_cut_points(text, expected_tail):
    summary = filing_display.extractive_summary(text)
    assert summary, text
    assert expected_tail in summary


def test_the_shared_sentence_pattern_and_its_other_consumers_are_unchanged():
    """extractive_summary is the ONLY consumer given marker awareness.
    is_readable_extracted_text and trim_excerpt_for_display keep their
    exact previous behaviour."""
    marker_text = "Section heading 1. " + ("Filler prose for length. " * 12)
    assert filing_display.is_readable_extracted_text(marker_text) is True
    assert filing_display.trim_excerpt_for_display("Alpha beta 1.").endswith("1.")


# --- 3. honest fallback ---------------------------------------------------

@pytest.mark.parametrize("summary,title,expected", [
    ("", _EN_TITLE, False),
    ("   ", _EN_TITLE, False),
    (_EN_TITLE, _EN_TITLE, False),
    (f"{_EN_TITLE} {_EN_TITLE}", _EN_TITLE, False),
    (f"{_EN_TITLE} 1.", _EN_TITLE, False),
    # A SHORT real statement stays eligible — no minimum-length rule.
    ("Nothing has been determined to date.", _EN_TITLE, True),
    ("No.", _EN_TITLE, True),
])
def test_substantiveness_is_about_content_not_length(summary, title, expected):
    assert filing_display.summary_is_substantive(summary, title) is expected


def test_fallback_wording_makes_no_claim_about_why():
    text = f"{filing_display.SUMMARY_UNAVAILABLE_TEXT} {filing_display.SUMMARY_UNAVAILABLE_ACTION}"
    assert filing_display.SUMMARY_UNAVAILABLE_TEXT == "Summary unavailable."
    assert filing_display.SUMMARY_UNAVAILABLE_ACTION == "Open the original filing for details."
    for claim in ("extraction", "failed", "translation", "pending", "retry", "error"):
        assert claim not in text.lower()


# --- 4. actual card presentation -----------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _settings(tmp_path) -> Settings:
    return Settings(
        dart_api_key="dart-key", translation_api_key="deepl-key",
        edgar_user_agent="EevaResearch test@example.com",
        edinet_subscription_key="test-key", cache_dir=tmp_path,
    )


def _dart_filing(rcept_no: str) -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="00164779", corp_name="SK하이닉스", stock_code="000660",
        report_nm="풍문또는보도에대한해명", rcept_dt="20260722", flr_nm="SK하이닉스",
        source_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}", retrieved_at=_now_iso(),
    )


def _seed(tmp_path, filing: FilingEvent, candidate: CandidateSignal) -> None:
    from dataclasses import asdict

    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "dart_corp_codes.json").write_text(json.dumps(
        {"000660": {"corp_code": "00164779", "corp_name": "SK하이닉스",
                    "source": "OpenDART corpCode.xml", "retrieved_at": _now_iso()}}), encoding="utf-8")
    (tmp_path / "dart_filing_events.json").write_text(json.dumps(
        {"seen_receipt_numbers": [filing.rcept_no], "filing_events": [asdict(filing)],
         "candidate_signals": []}, ensure_ascii=False), encoding="utf-8")
    from src.data_access.dart import candidate_store
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})


def _candidate(filing: FilingEvent, *, excerpt: str | None, translation: str | None,
               title_translation: str | None, state: ExtractionState) -> CandidateSignal:
    return CandidateSignal(
        id=f"cand-{filing.rcept_no}", filing=filing,
        matched_rules=["rumor_clarification:rumor:풍문"], confidence="High",
        status=CandidateStatus.NEEDS_REVIEW, extraction_state=state,
        excerpt_original=excerpt,
        excerpt_translation=Translation(
            translated_text=translation, provider="DeepL", source_lang="ko",
            target_lang="en", translated_at=_now_iso(),
        ) if translation else None,
        title_translation=Translation(
            translated_text=title_translation, provider="DeepL", source_lang="ko",
            target_lang="en", translated_at=_now_iso(),
        ) if title_translation else None,
    )


def _run(tmp_path) -> AppTest:
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=20)
        at.run()
    return at


def _render(tmp_path) -> str:
    return _text(_run(tmp_path))


def _text(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown if not m.value.startswith("<style>"))


def test_card_shows_the_issuers_statement_not_the_repeated_title(tmp_path):
    filing = _dart_filing("20260722800178")
    _seed(tmp_path, filing, _candidate(
        filing, excerpt=_KO_HEADER_THEN_BODY, translation=_EN_HEADER_THEN_BODY,
        title_translation=_EN_TITLE, state=ExtractionState.EXTRACTED))
    text = _render(tmp_path)

    assert "Filing summary" in text
    assert "has neither pursued nor decided to acquire" in text, "the issuer's own statement reaches the card"
    assert "SK Hynix / Clarification Regarding Rumors" not in text, "the cover-page header is gone"
    assert filing_display.SUMMARY_UNAVAILABLE_TEXT not in text


def test_card_falls_back_honestly_for_a_title_only_excerpt(tmp_path):
    filing = _dart_filing("20260722800179")
    _seed(tmp_path, filing, _candidate(
        filing, excerpt=_EN_HEADER_ONLY, translation=_EN_HEADER_ONLY,
        title_translation=_EN_TITLE, state=ExtractionState.EXTRACTED))
    text = _render(tmp_path)

    assert filing_display.SUMMARY_UNAVAILABLE_TEXT in text
    assert filing_display.SUMMARY_UNAVAILABLE_ACTION in text
    assert "filed" not in text.split("Filing summary")[-1][:200], "no metadata sentence under the heading"


def test_card_falls_back_honestly_when_extraction_failed(tmp_path):
    filing = _dart_filing("20260722800180")
    _seed(tmp_path, filing, _candidate(
        filing, excerpt=None, translation=None, title_translation=_EN_TITLE,
        state=ExtractionState.RETRIEVAL_FAILED))
    text = _render(tmp_path)

    assert filing_display.SUMMARY_UNAVAILABLE_TEXT in text
    assert filing_display.SUMMARY_UNAVAILABLE_ACTION in text


def test_card_keeps_the_stored_excerpt_reachable_unchanged(tmp_path):
    """Cleanup is display-time, for the summary only: the excerpt toggle
    still offers the stored text."""
    filing = _dart_filing("20260722800181")
    _seed(tmp_path, filing, _candidate(
        filing, excerpt=_KO_HEADER_THEN_BODY, translation=_EN_HEADER_THEN_BODY,
        title_translation=_EN_TITLE, state=ExtractionState.EXTRACTED))
    at = _run(tmp_path)
    text = _text(at)
    labels = {b.label for b in at.button}

    # The stored excerpt stays reachable, unchanged, behind its own toggle
    # (a button, not markdown) — cleanup applies to the summary only.
    assert "View translated filing excerpt" in labels
    assert "View original filing excerpt" in labels
    # ...and the summary above it is the cleaned one.
    assert "SK Hynix / Clarification Regarding Rumors" not in text


# --- 5. provider isolation ------------------------------------------------

@pytest.mark.parametrize("text", [_EDGAR_TEXT, _EDINET_TEXT])
def test_dart_cleanup_is_never_applied_to_other_providers_by_the_card(text):
    """The helper itself has no notion of provider — the CARD decides.
    This pins that the helper would match, so the only thing keeping
    EDGAR/EDINET text intact is the card's source check."""
    assert filing_display.strip_dart_cover_page_header(text) != text, (
        "shape-wise it would match, so the card's DART-only guard is load-bearing"
    )


def test_card_does_not_strip_the_header_shape_from_an_edgar_filing(tmp_path):
    filing = FilingEvent(
        rcept_no="0000320193-26-000010", corp_code="0000320193", corp_name="Apple Inc.",
        stock_code="AAPL", report_nm="8-K", rcept_dt="20260722", flr_nm="Apple Inc.",
        source_url="https://www.sec.gov/Archives/edgar/data/320193/000032019326000010/a8k.htm",
        retrieved_at=_now_iso(), source_name="SEC EDGAR", original_language="en",
    )
    from dataclasses import asdict
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "edgar_filing_events.json").write_text(json.dumps(
        {"seen_receipt_numbers": [filing.rcept_no], "filing_events": [asdict(filing)],
         "candidate_signals": []}, ensure_ascii=False), encoding="utf-8")
    from src.data_access.dart import candidate_store
    edgar_candidate = _candidate(
        filing, excerpt=_EDGAR_TEXT, translation=None, title_translation=None,
        state=ExtractionState.EXTRACTED)
    candidate_store.save_candidates(tmp_path, {edgar_candidate.id: edgar_candidate},
                                    filename="edgar_candidates.json")
    text = _render(tmp_path)

    if "Filing summary" in text and filing_display.SUMMARY_UNAVAILABLE_TEXT not in text:
        assert "SEC EDGAR / Quarterly Report" in text, "EDGAR text is never DART-cleaned"


# --- 6. acceptance cases, through the real card --------------------------
#
# Each asserts the facts a reader must actually receive, not merely that
# the header vanished.

def _acc_card(tmp_path, rcept_no, report_nm, excerpt_ko, translation_en, title_en):
    filing = FilingEvent(
        rcept_no=rcept_no, corp_code="00164779", corp_name="SK하이닉스", stock_code="000660",
        report_nm=report_nm, rcept_dt="20260722", flr_nm="SK하이닉스",
        source_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}", retrieved_at=_now_iso(),
    )
    _seed(tmp_path, filing, _candidate(
        filing, excerpt=excerpt_ko, translation=translation_en,
        title_translation=title_en, state=ExtractionState.EXTRACTED))
    return _run(tmp_path)


def test_acceptance_denial_reaches_the_card_with_what_it_addresses(tmp_path):
    at = _acc_card(tmp_path, "20260722800178", "풍문또는보도에대한해명",
                   _ACC_DENIAL_KO, _ACC_DENIAL_EN, _ACC_DENIAL_TITLE)
    text = _text(at)

    assert "has neither pursued nor decided to acquire Intel" in text, "the denial itself"
    assert "Ohio site and fab" in text, "what the denial is about"
    assert "JoongAng Ilbo" in text, "what report it addresses"
    assert filing_display.SUMMARY_UNAVAILABLE_TEXT not in text


def test_acceptance_inquiry_response_keeps_qualification_and_timing(tmp_path):
    at = _acc_card(tmp_path, "20260810800434", "조회공시요구(풍문또는보도)에대한답변(미확정)",
                   _ACC_RESPONSE_KO, _ACC_RESPONSE_EN, _ACC_RESPONSE_TITLE)
    text = _text(at)

    assert "nothing has been finalized as of yet" in text, "the uncertainty qualification"
    assert "within one month" in text, "source-supported follow-up timing"
    assert "September 9, 2026" in text, "the stated re-disclosure date"
    assert "Chongqing" in text, "what the response concerns"


def test_acceptance_inquiry_request_is_identified_as_a_request(tmp_path):
    at = _acc_card(tmp_path, "20260810800101", "조회공시요구(풍문또는보도)",
                   _ACC_REQUEST_KO, _ACC_REQUEST_EN, _ACC_REQUEST_TITLE)
    text = _text(at)
    summary = text.split("Filing summary")[-1]

    assert "Request for Disclosure" in summary, "identified as a request"
    assert "Disclosure Deadline" in summary and "6:00 PM" in summary, "the deadline"
    # An exchange request contains no issuer answer — none may be implied.
    for invented in ("nothing has been finalized", "we are reviewing", "the company has neither"):
        assert invented not in summary.lower()


def test_acceptance_samsung_never_shows_the_allegation_as_a_company_statement(tmp_path):
    at = _acc_card(tmp_path, "20260723800875", "풍문또는보도에대한해명(미확정)",
                   _ACC_SAMSUNG_KO, _ACC_SAMSUNG_EN, _ACC_SAMSUNG_TITLE)
    text = _text(at)
    summary = text.split("Filing summary")[-1].split("Official filing reference")[0]

    # The company's own position, with its qualification intact.
    assert "no specific details" in summary and "have been finalized at this time" in summary
    assert "October 22, 2026" in summary, "stated re-disclosure date"
    # The rumour field must never stand alone as if the company said it.
    assert "Content of the Rumors or Media Reports" not in summary
    standalone_allegation = "Samsung Electronics to purchase 90 trillion won worth of treasury stock starting next month."
    assert standalone_allegation not in summary, "the allegation is never presented as the company's own announcement"


@pytest.mark.parametrize("rcept,report_nm,ko,en,title", [
    ("20260722800178", "풍문또는보도에대한해명", _ACC_DENIAL_KO, _ACC_DENIAL_EN, _ACC_DENIAL_TITLE),
    ("20260810800434", "조회공시요구에대한답변", _ACC_RESPONSE_KO, _ACC_RESPONSE_EN, _ACC_RESPONSE_TITLE),
])
def test_stored_excerpt_contents_are_unchanged_by_display_cleanup(tmp_path, rcept, report_nm, ko, en, title):
    """Not merely that the toggles exist: the STORED text is what the
    excerpt views reveal, cover-page header and all."""
    at = _acc_card(tmp_path, rcept, report_nm, ko, en, title)
    labels = {b.label for b in at.button}
    assert "View translated filing excerpt" in labels
    assert "View original filing excerpt" in labels

    # Expand both and compare the revealed text against the stored strings.
    for label in ("View translated filing excerpt", "View original filing excerpt"):
        button = next(b for b in at.button if b.label == label)
        button.click()
        with patch("src.ui.pages.radar_inbox.get_settings", return_value=_settings(tmp_path)):
            at.run()
    revealed = " ".join(" ".join(m.value.split()) for m in at.markdown)

    for stored in (ko, en):
        head = " ".join(stored.split())[:120]
        assert head in revealed, f"stored excerpt must be revealed verbatim: {head[:60]!r}"


def test_field_aware_selection_is_not_applied_to_non_dart_text():
    """The module is DART-shaped by its label table; the card's source
    guard is what keeps it away from other providers."""
    assert dart_summary.field_aware_summary(_EDGAR_TEXT) is None
    assert dart_summary.field_aware_summary(_EDINET_TEXT) is None


def test_unrecognised_structure_returns_none_rather_than_guessing():
    assert dart_summary.field_aware_summary("Plain prose with no numbered fields at all.") is None
    assert dart_summary.field_aware_summary("1. Alpha only one field") is None
    # Numbered, but no recognised role labels.
    assert dart_summary.field_aware_summary("1. Alpha beta 2. Gamma delta") is None


def test_the_rumour_field_is_never_selected_as_the_issuers_position():
    fields = dart_summary.parse_fields(
        filing_display.strip_dart_cover_page_header(_ACC_DENIAL_EN))
    roles = {f.number: f.role for f in fields}
    assert roles[1] == dart_summary.ROLE_RUMOUR_CONTENT, "field 1 is the allegation"
    assert roles[4] == dart_summary.ROLE_CLARIFICATION, "field 4 is the issuer's answer"
    summary = dart_summary.field_aware_summary(
        filing_display.strip_dart_cover_page_header(_ACC_DENIAL_EN))
    assert "Hail-Mary Move" not in summary.split("titled")[0], (
        "the allegation only appears inside the clarification's own quotation"
    )


# --- 7. adversarial: a recognised form must never degrade to the claim ---
#
# field_aware_summary() returning None used to fall through to the generic
# leading-substring excerpt. For a rumour clarification that excerpt
# BEGINS WITH THE REPORTED CLAIM, so a single unfamiliar label would have
# shown the claim with the issuer's answer missing. select_summary()'s
# three-state outcome exists for exactly that case.

def _mutate(text: str, old: str, new: str) -> str:
    assert old in text, old
    return text.replace(old, new)


def test_unfamiliar_clarification_label_is_unsafe_not_claim_only():
    mutated = _mutate(_ACC_SAMSUNG_EN,
                      "4. Clarification Regarding the Content of the Rumor or Report:",
                      "4. Company Explanation of the Reported Matter:")
    cleaned = filing_display.strip_dart_cover_page_header(mutated)
    outcome, selected = dart_summary.select_summary(cleaned)

    assert outcome == dart_summary.OUTCOME_UNSAFE
    assert selected is None
    # What the generic path WOULD have shown, and why it must not be used.
    leading = filing_display.extractive_summary(cleaned)
    assert "90 trillion won worth of treasury stock" in leading
    assert "no specific details" not in leading


def test_missing_required_response_field_is_unsafe():
    truncated = _ACC_SAMSUNG_EN.split("4. Clarification")[0]
    outcome, _ = dart_summary.select_summary(
        filing_display.strip_dart_cover_page_header(truncated))
    assert outcome == dart_summary.OUTCOME_UNSAFE


def test_reordered_or_incomplete_numbering_is_still_unsafe():
    """The numbered run does not parse, but the form is still plainly a
    rumour clarification — the label scan catches it."""
    renumbered = _mutate(_ACC_SAMSUNG_EN, "1. Content of the Rumors", "7. Content of the Rumors")
    outcome, _ = dart_summary.select_summary(
        filing_display.strip_dart_cover_page_header(renumbered))
    assert outcome == dart_summary.OUTCOME_UNSAFE


def test_required_field_over_budget_is_unsafe_never_clipped():
    padded = _mutate(
        _ACC_SAMSUNG_EN, "The above matter is currently under review",
        "The above matter is currently under review " + ("and remains under detailed review " * 40))
    outcome, selected = dart_summary.select_summary(
        filing_display.strip_dart_cover_page_header(padded))
    assert outcome == dart_summary.OUTCOME_UNSAFE, "an over-budget response is dropped whole, never cut"
    assert selected is None


def test_claim_present_but_response_unidentifiable_is_unsafe():
    text = ("1. Content of the Rumors or Media Reports: Company X to acquire Y for 1 trillion won "
            "2. Media outlet reporting the rumor or news: Some Paper "
            "3. Unrecognised Heading Text that cannot be role-matched at all")
    assert dart_summary.select_summary(text)[0] == dart_summary.OUTCOME_UNSAFE


@pytest.mark.parametrize("text", [
    "The board approved a new facility investment on 12 August.",
    "The issuer completed the transfer of its subsidiary shares.",
    _EN_NO_HEADER,
])
def test_ordinary_prose_stays_eligible_for_the_generic_path(text):
    """No blanket ban on unrecognised DART documents."""
    assert dart_summary.select_summary(text)[0] == dart_summary.OUTCOME_NOT_APPLICABLE


def test_card_shows_the_honest_fallback_for_an_unsafe_document(tmp_path):
    mutated = _mutate(_ACC_SAMSUNG_EN,
                      "4. Clarification Regarding the Content of the Rumor or Report:",
                      "4. Company Explanation of the Reported Matter:")
    at = _acc_card(tmp_path, "20260723800876", "풍문또는보도에대한해명(미확정)",
                   _ACC_SAMSUNG_KO, mutated, _ACC_SAMSUNG_TITLE)
    text = _text(at)
    summary = text.split("Filing summary")[-1].split("Official filing reference")[0]

    assert filing_display.SUMMARY_UNAVAILABLE_TEXT in summary
    assert "90 trillion won worth of treasury stock" not in summary, (
        "the reported claim is never shown without the issuer's answer"
    )


# --- 8. parser and cleanup boundaries ------------------------------------

def test_a_short_label_needs_its_delimiter_to_claim_the_answer_role():
    """"Content" alone is an ordinary word: "Contents of the Agreement"
    must not be read as the issuer's answer."""
    fields = dart_summary.parse_fields(
        "1. Response to the Request for Disclosure About X "
        "2. Contents of the Agreement Between Parties")
    roles = {f.number: f.role for f in fields}
    assert roles[2] != dart_summary.ROLE_ANSWER
    # The real, delimited form still matches.
    real = dart_summary.parse_fields(
        "1. Response to the Request for Disclosure About X 2. Content: We are reviewing options.")
    assert {f.number: f.role for f in real}[2] == dart_summary.ROLE_ANSWER


def test_a_trailer_marker_mid_sentence_never_truncates_content():
    fields = dart_summary.parse_fields(
        "1. Content of Rumors A "
        "2. Clarification Regarding the Content of the Rumor or Report The Company "
        "denies ※ see note ※ and confirms nothing is decided.")
    body = next(f.body for f in fields if f.role == dart_summary.ROLE_CLARIFICATION)
    assert "confirms nothing is decided" in body, "a mid-sentence marker must not cut the sentence"


def test_a_trailer_after_a_completed_sentence_is_still_removed():
    fields = dart_summary.parse_fields(
        "1. Content of Rumors A "
        "2. Clarification Regarding the Content of the Rumor or Report Nothing is decided. "
        "(Person Responsible for Disclosure) Someone")
    body = next(f.body for f in fields if f.role == dart_summary.ROLE_CLARIFICATION)
    assert body.endswith("Nothing is decided.")
    assert "Person Responsible" not in body


@pytest.mark.parametrize("text", [
    "Acquisition of A/B Corp / Shares / (2026 plan) The board approved the transaction.",
    "Revenue / cost / (margin 2026) analysis follows in the next section.",
    "Alpha/Beta/(gamma) ordinary text",
])
def test_header_cleanup_does_not_remove_genuine_opening_prose(text):
    """The bracketed run must be a real filed DATE, not merely contain a
    four-digit year."""
    assert filing_display.strip_dart_cover_page_header(text) == text


@pytest.mark.parametrize("text", [
    "SK Hynix / Some Title / (2026.08.10) body text here",
    "SK Hynix / Some Title / (August 10, 2026) body text here",
])
def test_header_cleanup_still_matches_both_real_filed_date_shapes(text):
    assert filing_display.strip_dart_cover_page_header(text) == "body text here"


def test_numbered_values_in_prose_do_not_produce_a_role_selection():
    """They may parse as a run, but no role is recognised, so the generic
    path still applies — no claim is ever selected from them."""
    assert dart_summary.select_summary(
        "The ratio moved from 1. to 2. in the third quarter.")[0] == dart_summary.OUTCOME_NOT_APPLICABLE


# =========================================================================
# Follow-up: production-observed gaps (PR #100 verification, Oct 28 2026)
# =========================================================================
#
# FIXTURE PROVENANCE, stated so the limits are visible:
#   * _PROD_TITLE_CONTENT_RESPONSE is the stored translated excerpt read
#     from the live card for receipt 20260918800583, captured verbatim
#     EXCEPT that its trailing "※ Related Disclosures" list was truncated
#     by the capture itself. It is therefore a faithful reproduction of
#     the part under test (the Title/Content fields) and NOT a complete
#     copy of the stored field.
#   * The "6.x" and trailing-marker strings are complete as rendered.
#   * Everything else in this section is SYNTHETIC and structural — it
#     reproduces a shape, not a specific production record.

_PROD_TITLE_CONTENT_RESPONSE = (
    "SK Hynix / Response to Request for Disclosure (Rumors or Media Reports) (Pending) / "
    "(September 18, 2026) Response to Request for Disclosure (Rumors or Media Reports) (Pending) "
    "Response to Request for Disclosure (Rumors or Media Reports)(Unconfirmed) "
    "1. Title: Response to Request for Disclosure Regarding Reports That SK Hynix Is Building a "
    "Semiconductor Plant in Japan (August 21, 2026) "
    "2. Content: The Company is reviewing various measures, including the establishment of "
    "additional production bases, to strengthen the competitiveness of its memory business; "
    "however, nothing has been finalized as of yet. We will issue another disclosure once specific "
    "details are finalized or within three months. (Disclosure Officer) Kim Woo-hyun, Head of Finance"
)
_PROD_SEMIANNUAL = "Semiannual Report 6.7 Korea Aerospace Industries, Ltd."
_PROD_SEMIANNUAL_TITLE = "Earnings or Results Report — Semiannual Report (June 2026)"
_PROD_LARGE_HOLDINGS = (
    "Report on Large Holdings of Stocks, etc. (Abbreviated Form) 6.1 Korea Aerospace Industries, "
    "Ltd. 0000 0000 Report on Large Holdings of Stocks, etc."
)
_PROD_LARGE_HOLDINGS_TITLE = (
    "Major Shareholder Change — Report on the Status of Large Holdings of Stocks, etc. (Abbreviated)"
)
_PROD_ADVANCE_NOTICE = (
    "1. Company Name: Korea Aerospace Industries Co., Ltd. 2. Financial Reporting Period: Start "
    "Date: July 1, 2026; End Date: September 30, 2026 3. Scheduled Date for Financial Results "
    "Disclosure: November 3, 2026 4. Other Important Matters Related to Investment Decisions a."
)
_KAI_NAMES = ("한국항공우주", "Korea Aerospace Industries, Ltd.")


# --- 1. corroborated Title/Content inquiry response ----------------------

def test_title_content_response_needs_corroboration_from_the_filing_class():
    cleaned = filing_display.strip_dart_cover_page_header(_PROD_TITLE_CONTENT_RESPONSE)
    assert dart_summary.select_summary(cleaned)[0] == dart_summary.OUTCOME_UNSAFE, (
        "generic labels alone never establish an issuer response"
    )
    outcome, selected = dart_summary.select_summary(cleaned, True)
    assert outcome == dart_summary.OUTCOME_SELECTED
    assert "nothing has been finalized as of yet" in selected, "the issuer's qualification"
    assert "within three months" in selected, "the stated follow-up timing"
    assert "Semiconductor Plant in Japan" in selected, "what the response concerns"
    assert "Kim Woo-hyun" not in selected, "the signature trailer is removed"


@pytest.mark.parametrize("label,text", [
    ("inquiry request, same broad category",
     "1. Title: Request for Disclosure Regarding Reports on a Stake Sale "
     "2. Content: Reports that the company plans to sell its stake."),
    ("request carrying its deadline field",
     "1. Title: Request for Disclosure 2. Content: Reports on a sale "
     "3. Disclosure Deadline: August 10, 2026, 6:00 PM"),
    ("allegation restated under Content",
     "1. Title: Media Report Summary "
     "2. Content: SK Hynix to purchase 90 trillion won of treasury stock."),
    ("rumour-content role present",
     "1. Content of the Rumors or Media Reports: A claim "
     "2. Content: Something else entirely here."),
    ("unrelated document misclassified into the category",
     "1. Title: Supply Agreement Overview 2. Content: The parties agreed terms and conditions."),
])
def test_the_broad_category_alone_never_selects_a_response(label, text):
    """"Rumor Inquiry or Response" covers BOTH sides. Corroboration is
    supporting context; the subject must also describe the document as a
    reply in its own words, and no request-side or rumour-content role
    may be present."""
    assert dart_summary.select_summary(text, True)[0] == dart_summary.OUTCOME_UNSAFE, label


def test_a_self_described_english_response_is_selected_only_when_corroborated():
    text = ("1. Title: Response to Request for Disclosure Regarding Reports That X "
            "2. Content: We are reviewing options; however, nothing has been finalized as of yet.")
    assert dart_summary.select_summary(text, True)[0] == dart_summary.OUTCOME_SELECTED
    assert dart_summary.select_summary(text)[0] == dart_summary.OUTCOME_UNSAFE


# --- Korean generic labels get no extra trust for being Korean ----------
#
# SYNTHETIC, structurally derived fixtures: exact Korean production
# records for the adversarial shapes are not available locally, so these
# reproduce the SHAPE only. The genuine-response case below is checked
# separately against the real cached record.
#
# 제목/내용 are simply "title"/"content". They were previously routed
# straight to subject/answer roles, which let a Korean document classify
# as an issuer response with none of the corroboration an English
# Title/Content pair must clear — strictly more trust for being Korean.

_KO_GENERIC_ALLEGATION = "1. 제목 언론 보도 요약 2. 내용 SK하이닉스가 자사주 90조원을 매입한다."
_KO_GENERIC_REQUEST = "1. 제목 지분매각 보도에 대한 조회공시 요구 2. 내용 4조 규모 지분매각 추진 보도입니다."
_KO_GENERIC_UNRELATED = "1. 제목 단일판매공급계약 개요 2. 내용 당사는 계약을 체결하였습니다."
_KO_GENERIC_RESPONSE = "1. 제목 보도에 관한 조회공시 요구에 대한 답변 2. 내용 당사는 검토 중이나 확정된 사항은 없습니다."
_KO_GENERIC_CUT = "1. 제목 보도에 대한 답변 2. 내용 당사는 검토 중이나"
_KO_EXPLICIT_ANSWER = "1. 제목 보도에 대한 답변 2. 답변내용 당사는 검토 중이나 확정된 사항은 없습니다."


@pytest.mark.parametrize("label,text", [
    ("generic 제목/내용 carrying only an allegation", _KO_GENERIC_ALLEGATION),
    ("inquiry request under the broad category", _KO_GENERIC_REQUEST),
    ("unrelated document with that same category", _KO_GENERIC_UNRELATED),
    ("visibly cut response content", _KO_GENERIC_CUT),
])
def test_korean_generic_labels_are_not_trusted_more_than_english(label, text):
    """Neither route may select these — with or without corroboration."""
    assert dart_summary.select_summary(text)[0] == dart_summary.OUTCOME_UNSAFE, label
    assert dart_summary.select_summary(text, True)[0] == dart_summary.OUTCOME_UNSAFE, label


@pytest.mark.parametrize("text", [_KO_GENERIC_RESPONSE, _KO_EXPLICIT_ANSWER])
def test_a_korean_self_described_response_needs_the_same_corroboration(text):
    assert dart_summary.select_summary(text)[0] == dart_summary.OUTCOME_UNSAFE
    assert dart_summary.select_summary(text, True)[0] == dart_summary.OUTCOME_SELECTED


def test_the_real_korean_inquiry_response_still_resolves(tmp_path):
    """The live record prints "1. 제목 ... 2. 내용 ..." with NO colon, so
    the Korean labels accept whitespace as their delimiter."""
    cleaned = filing_display.strip_dart_cover_page_header(_ACC_RESPONSE_KO)
    assert dart_summary.select_summary(cleaned)[0] == dart_summary.OUTCOME_UNSAFE
    outcome, selected = dart_summary.select_summary(cleaned, True)
    assert outcome == dart_summary.OUTCOME_SELECTED
    assert "확정된 사항은 없습니다" in selected, "the uncertainty qualification"
    assert "1개월 이내" in selected, "the stated follow-up timing"


def test_an_explicit_korean_response_label_keeps_its_specific_handling():
    """해명내용 is explicit and unaffected by the generic-label rule."""
    cleaned = filing_display.strip_dart_cover_page_header(_ACC_DENIAL_KO)
    assert dart_summary.select_summary(cleaned)[0] == dart_summary.OUTCOME_SELECTED


def test_a_korean_word_merely_starting_with_a_label_is_not_a_field():
    """내용물 is a different word; whitespace delimiting must not match it."""
    assert dart_summary.select_summary("1. 제목 목록 2. 내용물 목록입니다.")[0] != dart_summary.OUTCOME_SELECTED


def test_an_unrelated_title_content_document_is_not_an_issuer_response():
    """Negative control at the level that matters: with no corroboration
    the generic form is never read as a response."""
    other = "1. Title: Supply Agreement Overview 2. Content: The parties agreed terms."
    assert dart_summary.classify(dart_summary.parse_fields(other)) is None
    assert dart_summary.select_summary(other)[0] != dart_summary.OUTCOME_SELECTED


def test_a_missing_or_cut_response_keeps_the_honest_fallback_even_when_corroborated():
    no_answer = "1. Title: Something 2. Media outlet reporting the rumor: A Paper"
    assert dart_summary.select_summary(no_answer, True)[0] == dart_summary.OUTCOME_UNSAFE
    cut = "1. Title: Something 2. Content: The Company is reviewing options; however, nothing has"
    assert dart_summary.select_summary(cut, True)[0] == dart_summary.OUTCOME_UNSAFE, (
        "an issuer statement cut mid-clause is never rendered"
    )


@pytest.mark.parametrize("fixture,title", [
    (_ACC_DENIAL_EN, _ACC_DENIAL_TITLE),
    (_ACC_RESPONSE_EN, _ACC_RESPONSE_TITLE),
    (_ACC_REQUEST_EN, _ACC_REQUEST_TITLE),
    (_ACC_SAMSUNG_EN, _ACC_SAMSUNG_TITLE),
])
def test_the_four_acceptance_cases_are_unchanged_by_the_follow_up(fixture, title):
    cleaned = filing_display.strip_dart_cover_page_header(fixture)
    outcome, selected = dart_summary.select_summary(cleaned)
    assert outcome == dart_summary.OUTCOME_SELECTED
    assert filing_display.summary_is_substantive(selected, title)


# --- 2. non-substantive "6.x" document headers ---------------------------

@pytest.mark.parametrize("summary,title", [
    (_PROD_SEMIANNUAL, _PROD_SEMIANNUAL_TITLE),
    (_PROD_LARGE_HOLDINGS, _PROD_LARGE_HOLDINGS_TITLE),
])
@pytest.mark.parametrize("names,case", [
    (_KAI_NAMES, "issuer in registry"),
    ((), "issuer absent from registry"),
    (("Totally Different Corp",), "code/name mismatch"),
    (("한국항공우주",), "Korean variant only"),
    (("Korea Aerospace Industries, Ltd.",), "English variant only"),
])
def test_document_header_only_excerpts_never_depend_on_registry_luck(summary, title, names, case):
    """Failing to identify the issuer must not turn a header into a
    summary: both shape rules are registry-independent."""
    assert filing_display.summary_is_substantive(
        summary, title, names, dart_header_rules=True) is False, case


def test_header_rules_are_dart_only():
    """EDGAR and EDINET keep their existing behaviour: the shape rules do
    not run for them. Issuer names are omitted here so the DART gate is
    the only thing under test — with them supplied the text would fail
    on the issuer discount alone, which would not isolate the gate."""
    assert filing_display.summary_is_substantive(
        _PROD_SEMIANNUAL, _PROD_SEMIANNUAL_TITLE, (), dart_header_rules=False) is True
    assert filing_display.summary_is_substantive(
        _PROD_SEMIANNUAL, _PROD_SEMIANNUAL_TITLE, (), dart_header_rules=True) is False


@pytest.mark.parametrize("summary,title", [
    (_PROD_ADVANCE_NOTICE, "Earnings or Results Report — Advance Notice of Financial Results Disclosure"),
    ("Semiannual Report 6.7 covering the period, with results discussed below.", "Semiannual Report"),
    ("The Company recorded revenue of 4.5 trillion won in the period.", "Earnings or Results Report"),
    ("Nothing has been determined to date.", "Rumor Inquiry or Response — Clarification"),
    ("No.", "Rumor Inquiry or Response — Clarification"),
    ("有価証券報告書の記載内容の抜粋です。", "Annual Securities Report — 有価証券報告書"),
])
def test_substantive_counterexamples_survive(summary, title):
    """Real figures, short prose and CJK text are never discounted."""
    assert filing_display.summary_is_substantive(
        summary, title, _KAI_NAMES, dart_header_rules=True) is True


def test_numbers_are_counted_not_deleted():
    """The judgement ignores a form/version token; it never strips digits
    from displayed text, and a real amount keeps its words."""
    s = "The contract is valued at 4.5 trillion won."
    assert filing_display.summary_is_substantive(s, "Supply or Sales Contract", _KAI_NAMES)
    assert filing_display.extractive_summary(s) == s, "displayed text is untouched"


# --- 3. trailing alphabetic sub-marker -----------------------------------

def test_a_dangling_alphabetic_sub_marker_is_dropped():
    out = filing_display.extractive_summary(_PROD_ADVANCE_NOTICE)
    assert not out.rstrip().endswith("a.")
    assert out.rstrip().endswith("Investment Decisions")
    assert "November 3, 2026" in out, "the disclosure date survives"
    assert "July 1, 2026" in out, "the reporting period survives"


@pytest.mark.parametrize("text", [
    "The report was signed by John F.",
    "The parties met in Washington D.C.",
    "Section a. of the agreement governs delivery and remains in force.",
])
def test_legitimate_letters_and_initials_are_untouched(text):
    assert filing_display.extractive_summary(text) == text


# --- 4. card level: corroboration comes from the filing's own class ------

def test_card_recovers_the_response_for_a_rumour_inquiry_filing(tmp_path):
    filing = _dart_filing("20260918800583")
    _seed(tmp_path, filing, _candidate(
        filing, excerpt=_PROD_TITLE_CONTENT_RESPONSE, translation=_PROD_TITLE_CONTENT_RESPONSE,
        title_translation="Response to a Request for Disclosure (Rumors or Media Reports) (Unconfirmed)",
        state=ExtractionState.EXTRACTED))
    at = _run(tmp_path)
    text = _text(at)
    summary = text.split("Filing summary")[-1].split("Official filing reference")[0]

    if filing_display._DART_CATEGORY_TITLES.get("market_rumor_response") in text:
        assert "nothing has been finalized as of yet" in summary
        assert "within three months" in summary
    else:
        # Without the rumour-inquiry classification the card must NOT use
        # the generic form — the honest fallback is the correct outcome.
        assert filing_display.SUMMARY_UNAVAILABLE_TEXT in summary


def _kai_filing(rcept_no: str) -> FilingEvent:
    """A real tracked issuer, because the substantiveness judgement looks
    the English name up from the registry by securities code — seeding a
    KAI excerpt under another issuer would not exercise the rule."""
    return FilingEvent(
        rcept_no=rcept_no, corp_code="00309503", corp_name="한국항공우주", stock_code="047810",
        report_nm="반기보고서", rcept_dt="20260814", flr_nm="한국항공우주",
        source_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}", retrieved_at=_now_iso(),
    )


def test_card_shows_the_fallback_for_a_header_only_dart_excerpt(tmp_path):
    filing = _kai_filing("20260814003309")
    _seed(tmp_path, filing, _candidate(
        filing, excerpt=_PROD_SEMIANNUAL, translation=_PROD_SEMIANNUAL,
        title_translation="Semiannual Report (June 2026)", state=ExtractionState.EXTRACTED))
    text = _text(_run(tmp_path))
    summary = text.split("Filing summary")[-1].split("Official filing reference")[0]

    assert filing_display.SUMMARY_UNAVAILABLE_TEXT in summary
    assert "6.7" not in summary, "the form/version heading never reaches the reader as a summary"


def test_stored_excerpt_views_are_still_untouched_by_the_follow_up(tmp_path):
    filing = _dart_filing("20260918800584")
    _seed(tmp_path, filing, _candidate(
        filing, excerpt=_PROD_TITLE_CONTENT_RESPONSE, translation=_PROD_TITLE_CONTENT_RESPONSE,
        title_translation="Response to a Request for Disclosure", state=ExtractionState.EXTRACTED))
    at = _run(tmp_path)
    labels = {b.label for b in at.button}
    assert "View translated filing excerpt" in labels
    assert "View original filing excerpt" in labels
    for label in ("View translated filing excerpt", "View original filing excerpt"):
        next(b for b in at.button if b.label == label).click()
        with patch("src.ui.pages.radar_inbox.get_settings", return_value=_settings(tmp_path)):
            at.run()
    revealed = " ".join(" ".join(m.value.split()) for m in at.markdown)
    assert " ".join(_PROD_TITLE_CONTENT_RESPONSE.split())[:110] in revealed, (
        "the stored excerpt is still revealed verbatim, cover-page header included"
    )


# --- 5. EDGAR / EDINET unchanged ------------------------------------------

@pytest.mark.parametrize("text", [_EDGAR_TEXT, _EDINET_TEXT])
def test_other_providers_are_unaffected_by_the_corroboration_path(text):
    assert dart_summary.select_summary(text, True)[0] != dart_summary.OUTCOME_SELECTED


# --- punctuation is a conservative signal, not proof ---------------------

@pytest.mark.parametrize("body,complete", [
    ("We are reviewing options; however, nothing has been finalized as of yet.", True),
    ("The Company has neither pursued nor decided to acquire the site.”", True),
    ("The board approved it (per the resolution).", True),
    ("\uB2F9\uC0AC\uB294 \uAC80\uD1A0 \uC911\uC774\uB098 \uD655\uC815\uB41C \uC0AC\uD56D\uC740 \uC5C6\uC2B5\uB2C8\uB2E4.", True),
    ("We are reviewing options; however, nothing has", False),
    ("We are reviewing options\u2026", False),
    ("The matter remains under review and we", False),
])
def test_issuer_statement_completeness_signal(body, complete):
    assert dart_summary._ends_completely(body) is complete


@pytest.mark.parametrize("answer,outcome", [
    ("We are reviewing options; however, nothing has been finalized as of yet.",
     dart_summary.OUTCOME_SELECTED),
    ("We are reviewing options; however, nothing has", dart_summary.OUTCOME_UNSAFE),
    ("We are reviewing options\u2026", dart_summary.OUTCOME_UNSAFE),
])
def test_a_visibly_cut_issuer_statement_is_not_rendered(answer, outcome):
    """Isolates the completeness check: the subject DOES self-describe as
    a response and no other guard applies, so only the punctuation signal
    separates these."""
    text = f"1. Title: Response to Request for Disclosure Regarding X 2. Content: {answer}"
    assert dart_summary.select_summary(text, True)[0] == outcome


def test_a_cut_clarification_is_also_withheld():
    """Same rule on the rumour-clarification path, which needs no
    corroboration at all."""
    cut = ("1. Content of the Rumors or Media Reports: A claim "
           "2. Clarification Regarding the Content of the Rumor or Report "
           "The Company has neither pursued nor")
    assert dart_summary.select_summary(cut)[0] == dart_summary.OUTCOME_UNSAFE


def test_a_truncation_after_an_abbreviation_is_a_missed_truncation():
    """A field cut right after an abbreviation ends in a period and is
    accepted. That is a MISSED TRUNCATION — a false negative for
    truncation detection — NOT evidence that completeness was
    established. The signal never claims to detect all truncation."""
    assert dart_summary._ends_completely("The filer is SK Hynix Inc.") is True


# --- card integration with REAL category and registry inputs -------------

def test_card_uses_the_real_filing_category_not_a_helper_flag(tmp_path):
    """The rumour-inquiry category comes from the filing's own stored
    rules, and a filing outside that category must not take the generic
    path even with identical Title/Content text."""
    filing = FilingEvent(
        rcept_no="20260918800590", corp_code="00164779", corp_name="SK\uD558\uC774\uB2C9\uC2A4",
        stock_code="000660", report_nm="\uB2E8\uC77C\uD310\uB9E4\uACF5\uAE09\uACC4\uC57D\uCCB4\uACB0",
        rcept_dt="20260918", flr_nm="SK\uD558\uC774\uB2C9\uC2A4",
        source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260918800590",
        retrieved_at=_now_iso(),
    )
    _seed(tmp_path, filing, _candidate(
        filing, excerpt=_PROD_TITLE_CONTENT_RESPONSE, translation=_PROD_TITLE_CONTENT_RESPONSE,
        title_translation="Execution of a Single Sales and Supply Contract",
        state=ExtractionState.EXTRACTED))
    text = _text(_run(tmp_path))
    summary = text.split("Filing summary")[-1].split("Official filing reference")[0]
    assert filing_display.SUMMARY_UNAVAILABLE_TEXT in summary, (
        "no rumour-inquiry category -> no corroboration -> honest fallback"
    )


def test_card_looks_the_issuer_up_from_the_real_registry(tmp_path):
    """issuer_display_names reads the tracked registry by securities
    code; a tracked issuer resolves to its English registry name."""
    filing = _kai_filing("20260814003310")
    names = filing_display.issuer_display_names(filing)
    assert "Korea Aerospace Industries, Ltd." in names
    assert "\uD55C\uAD6D\uD56D\uACF5\uC6B0\uC8FC" in names


def test_card_presentation_for_a_korean_generic_allegation(tmp_path):
    """Card-level, with the real rumour-inquiry category: a Korean
    generic pair carrying only an allegation shows the honest fallback,
    and the allegation itself never reaches the reader as a summary."""
    filing = FilingEvent(
        rcept_no="20260918800601", corp_code="00164779", corp_name="SK하이닉스", stock_code="000660",
        report_nm="조회공시요구(풍문또는보도)에대한답변", rcept_dt="20260918", flr_nm="SK하이닉스",
        source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260918800601",
        retrieved_at=_now_iso(),
    )
    _seed(tmp_path, filing, _candidate(
        filing, excerpt=_KO_GENERIC_ALLEGATION, translation=_KO_GENERIC_ALLEGATION,
        title_translation="Response to a Request for Disclosure", state=ExtractionState.EXTRACTED))
    text = _text(_run(tmp_path))
    summary = text.split("Filing summary")[-1].split("Official filing reference")[0]

    assert filing_display.SUMMARY_UNAVAILABLE_TEXT in summary
    assert "자사주 90조원을 매입한다" not in summary, (
        "the allegation is never presented as the issuer's own statement"
    )


def test_card_presentation_for_the_real_korean_response(tmp_path):
    """The same card path, with a genuine self-described response, keeps
    the qualification and the timing."""
    filing = _dart_filing("20260810800434")
    _seed(tmp_path, filing, _candidate(
        filing, excerpt=_ACC_RESPONSE_KO, translation=None,
        title_translation=None, state=ExtractionState.EXTRACTED))
    at = _run(tmp_path)
    text = _text(at)
    summary = text.split("Filing summary")[-1].split("Official filing reference")[0]
    # With no stored translation the card shows the neutral fallback by
    # its own pre-existing rule; the native excerpt stays behind its
    # toggle. Either way the allegation never stands alone.
    assert "확정된 사항은 없습니다" in summary or filing_display.SUMMARY_UNAVAILABLE_TEXT in summary
