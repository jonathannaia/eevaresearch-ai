"""Document artifacts must be rejected consistently, not by luck.

Two mechanisms, both summary-path only:

  * EDINET leaves machine artifacts at the head of the extracted text
    that the existing strip cannot reach — a whitespace-free CJK
    document-title-timestamp, and the exported HTML member's own
    filename. They were rendering as the Filing summary.
  * DART's semiannual cover page was rejected for some issuers and
    accepted for others. The outcome depended on whether the summary
    happened to be cut before an administrative "Fiscal Year:" label,
    and on whether the display title spelled the form "Semiannual" or
    "Semi-Annual". Same form, same month, opposite results.

Neither mechanism makes any claim about what remains after an artifact
is removed: summary_is_substantive still judges the remainder, and a
text that was only an artifact collapses to the honest fallback.

This file deliberately does NOT cover metadata-dominant major-shareholder
summaries, missing translations, unlabelled response selection, or
material-field selection. Those are separate mechanisms.

FIXTURE PROVENANCE, stated so the limits are visible:
  * _EDINET_CJK_TITLE_ARTIFACT, _EDINET_FILENAME_ARTIFACT_JA/_EN and all
    seven _DART_COVER_PAGES entries are EXACT REPRODUCTIONS of text
    rendered on live cards during the read-only UI baselines of
    2026-10-28 and 2026-10-29 (EDINET documents S100Z8FG, S100Z826,
    S100Z80C; DART receipts 20260814001845, 20260814003345,
    20260812000313, 20260812000465, 20260814003753, 20260814001879,
    20260814003198). Some are shortened in the tail by the capture.
  * Everything under "perturbations" is SYNTHETIC: the same shape with
    the issuer, version marker, registry number, title spelling and
    administrative label changed, so this is not a list of repaired
    examples.

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
    CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, Translation,
)

_HARNESS = Path(__file__).parent / "apptest_pages" / "radar_inbox_page.py"


# --- EDINET artifacts (exact reproductions) --------------------------------

_EDINET_CJK_TITLE_ARTIFACT = "自己株券買付状況報告書（法24条の６第１項に基づくもの）_20261015090441"
_EDINET_CJK_BODY = "株式の種類 普通株式 １【取得状況】 （１）【株主総会決議による取得の状況】 該当事項はありません。"
_EDINET_FILENAME_ARTIFACT_JA = "0101010_honbun_0088405703810.htm"
_EDINET_FILENAME_ARTIFACT_EN = "0100000_honbun_0596300283810.htm"
_EDINET_JA_BODY = (
    "１【提出理由】2026年10月16日付の当社取締役会から委任を受けた代表執行役の決定において、"
    "リストリクテッド・ストック・ユニットを付与することを決定いたしました。"
)
_EDINET_EN_BODY = "Class of Stock: Common Stock 1. [Acquisition Status] Not applicable."


# --- DART cover pages (exact reproductions, seven issuers) -----------------

_DART_COVER_PAGES: dict[str, str] = {
    "Wonik IPS": (
        "Semiannual Report 6.7 Wonik IPS Co., Ltd. Y 131311-0163174 Semiannual Report "
        "(11th Interim Period) Fiscal Year: January 1, 2026, through June 30, 2026 To: "
        "Financial Services Commission, Korea Exchange August 14, 2026 Type of Reporting "
        "Entity: Listed Company—Grounds for Exemption: None Company Name: Wonik IPS Co., Ltd."
    ),
    "Doosan Robotics": (
        "Semiannual Report 6.7 Doosan Robotics Y 110111-5798272 Semiannual Report (12th Term) "
        "Fiscal Year: January 1, 2026, through June 30, 2026 To: Financial Services Commission, "
        "Korea Exchange August 14, 2026 Type of Entity Subject to Filing: Listed Company "
        "Grounds for Exemption: None Company Name: Doosan Robotics Co., Ltd."
    ),
    "SFA Semiconductor": (
        "Semiannual Report 6.7 SFA Semiconductor Co., Ltd. Y 161511-0027263 Semiannual Report "
        "(29th Fiscal Year) From January 1, 2026, to June 30, 2026 To: Financial Services "
        "Commission, Korea Exchange August 12, 2026 Type of Reporting Entity: Listed Company "
        "Grounds for Exemption: None Company Name: SFA Semiconductor Co., Ltd."
    ),
    "SFA": (
        "Semiannual Report 6.7 SFA Co., Ltd. Y 194211-0032714 Semiannual Report (29th Fiscal Year) "
        "From January 1, 2026, to June 30, 2026 To: Financial Services Commission, Korea Exchange "
        "August 12, 2026 Type of Entity Required to File: Listed Company Grounds for Exemption: "
        "None Company Name: SFA Co., Ltd."
    ),
    "Hanamicro": (
        "Semiannual Report 6.7 Hanamicro Co., Ltd. Y 164811-0021208 Semiannual Report "
        "(2nd Quarter of the 26th Fiscal Year) Fiscal Year: January 1, 2026 – June 30, 2026 To: "
        "Financial Services Commission, Korea Exchange August 14, 2026 Type of Reporting Entity: "
        "Listed Corporation. Grounds for Exemption: None."
    ),
    "LG Innotek": (
        "Semiannual Report 6.7 LG Innotek Co., Ltd. Y 110111-0192180 Semiannual Report "
        "(51st Interim Period) Fiscal Year: January 1, 2026, to June 30, 2026 To: Financial "
        "Services Commission, Korea Exchange August 14, 2026 Type of Entity Subject to Filing: "
        "Listed Corporation Grounds for Exemption: None Company Name: LG Innotek Co., Ltd."
    ),
    "Hanwha Aerospace": (
        "Semiannual Report 6.7 Hanwha Aerospace Co., Ltd. Y 194211-0000480 Semiannual Report "
        "(50th Fiscal Year) Fiscal Year: January 1, 2026, through June 30, 2026 To: Financial "
        "Services Commission, Korea Exchange August 14, 2026 Type of Entity Required to File: "
        "Listed Company Grounds for Exemption: None Company Name: Hanwha Aerospace Co., Ltd."
    ),
}

_TITLE_SEMIANNUAL = "Earnings or Results Report — Semiannual Report (June 2026)"
_TITLE_SEMI_ANNUAL = "Earnings or Results Report — [Correction] Semi-Annual Report (June 2026)"
_TITLE_UNRELATED = "Major Shareholder Change — Report on Large Holdings of Stocks, etc. (General)"


# --- 1. EDINET: the two unreachable artifacts ------------------------------

@pytest.mark.parametrize("artifact, body", [
    (_EDINET_CJK_TITLE_ARTIFACT, _EDINET_CJK_BODY),
    (_EDINET_FILENAME_ARTIFACT_JA, _EDINET_JA_BODY),
    (_EDINET_FILENAME_ARTIFACT_EN, _EDINET_EN_BODY),
])
def test_observed_edinet_artifacts_are_removed_from_the_summary_copy(artifact, body):
    text = f"{artifact} {body}"
    assert filing_display.strip_edinet_summary_artifacts(text) == body


def test_the_existing_strip_is_unchanged_and_still_misses_these_shapes():
    """Deliberate: strip_edinet_machine_artifacts keeps its own reach,
    which the excerpt toggles depend on. Widening it would change what
    an excerpt view shows."""
    text = f"{_EDINET_CJK_TITLE_ARTIFACT} {_EDINET_CJK_BODY}"
    assert filing_display.strip_edinet_machine_artifacts(text) == text


@pytest.mark.parametrize("text", [
    "The lender is ABC_Bank_20260909153311 and the note [Note 1] applies here.",
    "Report 0101010_honbun_0088405703810.htm is referenced in paragraph two.",
    "当社は、2026年10月16日に覚書を締結しました。",
    "1.5 billion yen was paid on 2026-10-16.",
    "Extraordinary Report_20260909153311 already handled by the existing strip.",
])
def test_edinet_summary_strip_leaves_everything_else_alone(text):
    assert filing_display.strip_edinet_summary_artifacts(text) == text


def test_edinet_artifact_removal_alone_does_not_make_a_summary_substantive():
    """An artifact-only text has nothing behind it, so removing the
    artifact must not produce a 'summary'."""
    only_artifact = filing_display.strip_edinet_summary_artifacts(_EDINET_FILENAME_ARTIFACT_JA)
    assert only_artifact == ""
    assert not filing_display.summary_is_substantive(only_artifact, "Extraordinary Report — 臨時報告書")


@pytest.mark.parametrize("value", ["", None])
def test_edinet_summary_strip_handles_empty_and_none(value):
    assert filing_display.strip_edinet_summary_artifacts(value) == value


# --- 2. DART cover pages: consistent across issuers and spellings ----------

@pytest.mark.parametrize("issuer", sorted(_DART_COVER_PAGES))
@pytest.mark.parametrize("title", [_TITLE_SEMIANNUAL, _TITLE_SEMI_ANNUAL, _TITLE_UNRELATED])
def test_every_observed_cover_page_is_rejected_for_every_issuer_and_spelling(issuer, title):
    """Seven issuers x three title spellings. The judgement must not
    depend on the issuer's name or on how the form was translated —
    before this change, "Semiannual" rejected and "Semi-Annual"
    accepted the identical document."""
    assert not filing_display.summary_is_substantive(
        _DART_COVER_PAGES[issuer], title, (), dart_header_rules=True)


def test_the_cover_page_judgement_needs_no_issuer_registry_entry():
    """Same text, with and without the issuer names the registry would
    supply — identical outcome."""
    text = _DART_COVER_PAGES["Doosan Robotics"]
    without = filing_display.summary_is_substantive(text, _TITLE_SEMIANNUAL, (), dart_header_rules=True)
    with_names = filing_display.summary_is_substantive(
        text, _TITLE_SEMIANNUAL, ("두산로보틱스", "Doosan Robotics Inc."), dart_header_rules=True)
    assert without is False and with_names is False


# Synthetic perturbations of the same SHAPE: a different issuer, a
# different version marker, a different registry number, and a different
# administrative label from any observed record.
@pytest.mark.parametrize("text", [
    "Quarterly Report 6.1 Example Industrial Co., Ltd. N 987654-1234567 Quarterly Report "
    "(3rd Quarter) Reporting Period: July 1, 2026 to September 30, 2026 To: Financial "
    "Services Commission Type of Entity: Listed Company",
    "Business Report 12.3 Another Holdings Corp. Y 100200-3004005 Business Report "
    "(7th Term) Accounting Period: January 1, 2026 – December 31, 2026 Submitted To: "
    "Korea Exchange Grounds for Exemption: None",
    "반기보고서 6.7 예시전자 주식회사 Y 110111-0000001 반기보고서 (제10기) 사업연도: 2026년 1월 1일 "
    "~ 2026년 6월 30일 금융위원회 귀중",
])
def test_the_same_masthead_shape_is_rejected_for_records_never_observed(text):
    assert not filing_display.summary_is_substantive(text, _TITLE_SEMIANNUAL, (), dart_header_rules=True)


# --- 3. Negative controls: real content must survive -----------------------

@pytest.mark.parametrize("text, title", [
    # A real decimal and a real contract amount.
    ("1. Type of Sales and Supply Contract Product Supply - Contract Name: Export of K9 "
     "Howitzers 2. Contract Details Contract Amount (KRW) 641,095,389,786 Recent Revenue (KRW) "
     "26,702,901,248,815 Percentage of Revenue (%) 2.40",
     "Supply or Sales Contract — Execution of a Sole Sales and Supply Contract"),
    # The 3-2-5 business-registration number that appears inside real
    # large-holdings field bodies — NOT the 6-7 corporate registry number.
    ("Part 1: Report Overview 1. Information on the Issuing Company Company Name: Korea "
     "Aerospace Industries Co., Ltd. a. Reporter Date of Birth or Business Registration "
     "Number, etc. 116-81-03693 Occupation Securities, Finance",
     "Major Shareholder Change — Report on the Status of Large Holdings of Stocks, etc."),
    # Genuine colon-labelled fields.
    ("1. Company Name: Korea Aerospace Industries Co., Ltd. 2. Financial Reporting Period: "
     "Start Date: July 1, 2026; End Date: September 30, 2026 3. Scheduled Date for Financial "
     "Results Disclosure: November 3, 2026",
     "Earnings or Results Report — Advance Notice of Financial Results Disclosure"),
    # Short substantive prose with a real decimal and a real date.
    ("The contract is valued at 4.5 trillion won and runs through 2030-09-09.",
     "Supply or Sales Contract — Execution of a Single Sales and Supply Contract"),
    # The existing clarification fixture.
    ("Clarification Regarding the Content of the Rumor or Report: This disclosure is a "
     "clarification regarding the article published on October 26, 2026. - The Company is "
     "reviewing various shareholder return measures; however, nothing has been finalized as "
     "of yet. Scheduled Date of Re-disclosure: October 28, 2026",
     "Rumor Inquiry or Response — Clarification Regarding Rumors or Reports"),
    # The existing inquiry-response fixture.
    ("Title: Response to Request for Disclosure Regarding Reports That SK Hynix Is Building a "
     "Semiconductor Plant in Japan (August 21, 2026) Content: The Company is reviewing various "
     "measures; however, nothing has been finalized as of yet.",
     "Rumor Inquiry or Response — Response to a Request for Disclosure"),
])
def test_real_content_is_never_classified_as_a_header(text, title):
    assert filing_display.summary_is_substantive(text, title, (), dart_header_rules=True)


def test_a_version_marker_far_from_a_registry_number_is_not_a_masthead():
    """Both parts are required, and they must sit close together."""
    text = (
        "1. Contract Details Percentage of Revenue (%) 2.40 and the parties agreed terms over "
        "many further clauses describing delivery, acceptance, warranty, payment schedules, "
        "governing law and dispute resolution before naming the counterparty registry number "
        "131311-0163174 at the very end of the document."
    )
    assert filing_display.summary_is_substantive(text, _TITLE_SEMIANNUAL, (), dart_header_rules=True)


def test_the_cover_page_rules_stay_dart_only():
    text = _DART_COVER_PAGES["Wonik IPS"]
    assert filing_display.summary_is_substantive(text, _TITLE_SEMIANNUAL, (), dart_header_rules=False)


# --- 4. Through the real card ---------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _settings(tmp_path) -> Settings:
    return Settings(
        dart_api_key="dart-key", translation_api_key="deepl-key",
        edgar_user_agent="EevaResearch test@example.com",
        edinet_subscription_key="test-key", cache_dir=tmp_path,
    )


def _run(tmp_path) -> AppTest:
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=20)
        at.run()
    return at


def _text(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown if not m.value.startswith("<style>"))


def _translation(text: str, lang: str) -> Translation:
    return Translation(translated_text=text, provider="DeepL", source_lang=lang,
                       target_lang="en", translated_at=_now_iso())


def _seed_edinet(tmp_path, filing, candidate) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "edinet_filing_events.json").write_text(json.dumps(
        {"seen_keys": [f"EDINET:{filing.corp_code}:{filing.rcept_no}"],
         "filing_events": [asdict(filing)], "candidate_signals": []}, ensure_ascii=False), encoding="utf-8")
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate}, "edinet_candidates.json")


def _seed_dart(tmp_path, filing, candidate) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "dart_corp_codes.json").write_text(json.dumps(
        {"240810": {"corp_code": "01135941", "corp_name": "원익IPS",
                    "source": "OpenDART corpCode.xml", "retrieved_at": _now_iso()}}), encoding="utf-8")
    (tmp_path / "dart_filing_events.json").write_text(json.dumps(
        {"seen_receipt_numbers": [filing.rcept_no], "filing_events": [asdict(filing)],
         "candidate_signals": []}, ensure_ascii=False), encoding="utf-8")
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})


def _edinet_filing(doc_id: str) -> FilingEvent:
    return FilingEvent(
        rcept_no=doc_id, corp_code="E01955", corp_name="HAMAMATSU PHOTONICS K.K.", stock_code="69650",
        report_nm="自己株券買付状況報告書", rcept_dt="2026-10-15", flr_nm="浜松ホトニクス株式会社",
        pblntf_ty="030000", pblntf_detail_ty="120", ordinance_code="010",
        source_url=f"https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}",
        retrieved_at=_now_iso(), source_name="EDINET", original_language="Japanese",
    )


def test_card_drops_the_edinet_artifact_from_the_summary_and_keeps_the_excerpt_verbatim(tmp_path):
    """The machine artifact no longer opens the Filing summary, and the
    excerpt drawer still renders the stored text exactly as stored —
    this change never touches the text an excerpt toggle shows.

    What REMAINS after the artifact (here a field heading and a null
    statement) is judged by the existing substantiveness rules, which
    this task does not change: removing an artifact establishes nothing
    about the remainder, and metadata-dominant bodies are a separate
    mechanism this task makes no claim about."""
    filing = _edinet_filing("S100Z8FG")
    stored = f"{_EDINET_CJK_TITLE_ARTIFACT} 株式の種類 普通株式 １【取得状況】 該当事項はありません。"
    _seed_edinet(tmp_path, filing, CandidateSignal(
        id="edinet-artifact-1", filing=filing, matched_rules=["status_report:010:030000:120"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW,
        extraction_state=ExtractionState.EXTRACTED, excerpt_original=stored,
        excerpt_translation=_translation("Class of Stock: Common Stock Not applicable.", "ja"),
    ))
    at = _run(tmp_path)
    assert not at.exception
    text = _text(at)
    summary_region = text.split("Filing summary", 1)[1][:400]
    assert _EDINET_CJK_TITLE_ARTIFACT not in summary_region
    assert "_20261015090441" not in summary_region

    original_toggle = [b for b in at.button if b.label == "View original filing excerpt"]
    assert original_toggle, "the original-excerpt toggle must still be offered"
    original_toggle[0].click()
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=_settings(tmp_path)):
        at.run()
    expanded = _text(at)
    assert _EDINET_CJK_TITLE_ARTIFACT in expanded, "the stored excerpt must stay verbatim"


def test_card_falls_back_for_a_dart_cover_page(tmp_path):
    filing = FilingEvent(
        rcept_no="20260814001845", corp_code="01135941", corp_name="원익IPS", stock_code="240810",
        report_nm="반기보고서 (2026.06)", rcept_dt="20260814", flr_nm="원익IPS",
        source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260814001845",
        retrieved_at=_now_iso(),
    )
    cover = _DART_COVER_PAGES["Wonik IPS"]
    _seed_dart(tmp_path, filing, CandidateSignal(
        id="dart-cover-1", filing=filing, matched_rules=["earnings:earnings_or_results_report:반기"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW,
        extraction_state=ExtractionState.EXTRACTED, excerpt_original="반기보고서 표지",
        excerpt_translation=_translation(cover, "ko"),
        title_translation=_translation("Semiannual Report (June 2026)", "ko"),
    ))
    at = _run(tmp_path)
    assert not at.exception
    text = _text(at)
    assert filing_display.SUMMARY_UNAVAILABLE_TEXT in text
    assert "131311-0163174" not in text, "the masthead must not render as a summary"


def test_t1_language_behaviour_is_unaffected(tmp_path):
    """A readable Korean excerpt with no translation still gets its
    explicit label and availability notice."""
    filing = FilingEvent(
        rcept_no="20260821800214", corp_code="00164779", corp_name="SK하이닉스", stock_code="000660",
        report_nm="조회공시요구(풍문또는보도)", rcept_dt="20260821", flr_nm="SK하이닉스",
        source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260821800214",
        retrieved_at=_now_iso(),
    )
    stored = ("SK하이닉스/조회공시 요구(풍문 또는 보도)/(2026.08.21)조회공시 요구(풍문 또는 보도) "
              "조회공시 요구(풍문 또는 보도) 1. 조회공시요구내용 일본에 반도체 공장 짓는다 보도에 대한 "
              "사실 여부 및 구체적인 내용 2. 공시시한 2026-08-21 18:00 3. 기타 투자판단과 관련한 중요사항 -")
    _seed_dart(tmp_path, filing, CandidateSignal(
        id="dart-t1-1", filing=filing, matched_rules=["rumor_inquiry:조회공시"],
        confidence="High", status=CandidateStatus.NEEDS_REVIEW,
        extraction_state=ExtractionState.EXTRACTED, excerpt_original=stored,
    ))
    at = _run(tmp_path)
    assert not at.exception
    text = _text(at)
    assert "Original language — Korean" in text
    assert '<div class="er-filing-summary" lang="ko">' in text
    assert filing_display.ENGLISH_TRANSLATION_UNAVAILABLE_TEXT in text
    assert "2026-08-21 18:00" in text
