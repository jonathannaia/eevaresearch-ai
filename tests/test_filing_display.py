"""Pure-function tests for src.logic.filing_display — the deterministic
display-title mapping, the extraction quality gate, and Summary
generation that back the public filing card's "no raw XBRL, no dead
report_nm titles" fix (design/DECISIONS.md). No Streamlit, no I/O."""
from __future__ import annotations

from datetime import datetime, timezone

from src.logic import filing_display
from src.models.models import CandidateSignal, CandidateStatus, FilingEvent, StateTransition, Translation


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _edgar_filing(pblntf_ty: str, report_nm: str = "") -> FilingEvent:
    return FilingEvent(
        rcept_no="0001045810-26-000001", corp_code="0001045810", corp_name="NVIDIA", stock_code="NVDA",
        report_nm=report_nm or pblntf_ty, rcept_dt="2026-08-28", flr_nm="NVIDIA", pblntf_ty=pblntf_ty,
        source_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000001/",
        retrieved_at=_now_iso(), source_name="SEC EDGAR", original_language="English",
    )


def _dart_filing(report_nm: str) -> FilingEvent:
    return FilingEvent(
        rcept_no="20260812000001", corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm=report_nm, rcept_dt="20260812", flr_nm="삼성전자",
        source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260812000001", retrieved_at=_now_iso(),
    )


# ============================================================
# A: display_title
# ============================================================


def test_edgar_form_types_map_to_the_documented_readable_titles():
    assert filing_display.display_title(_edgar_filing("10-K"), None) == "Annual Report — Form 10-K"
    assert filing_display.display_title(_edgar_filing("10-Q"), None) == "Quarterly Report — Form 10-Q"
    assert filing_display.display_title(_edgar_filing("8-K"), None) == "Current Report — Form 8-K"
    assert filing_display.display_title(_edgar_filing("6-K"), None) == "Foreign Private Issuer Report — Form 6-K"


def test_edgar_mapped_title_wins_even_when_report_nm_is_the_bare_form_code():
    """The MRVL/NVDA real-world case: primaryDocDescription for a
    standard periodic report is usually just the form code restated —
    never a useful title on its own."""
    filing = _edgar_filing("10-Q", report_nm="10-Q")
    assert filing_display.display_title(filing, None) == "Quarterly Report — Form 10-Q"


def test_edgar_form_identifier_is_always_preserved_in_the_title():
    for form in ("10-K", "10-Q", "8-K", "6-K"):
        assert form in filing_display.display_title(_edgar_filing(form), None)


def test_edgar_unmapped_form_prefers_a_genuinely_distinct_official_description():
    filing = _edgar_filing("4", report_nm="Statement of Changes in Beneficial Ownership")
    assert filing_display.display_title(filing, None) == "Statement of Changes in Beneficial Ownership — Form 4"


def test_edgar_unmapped_form_with_no_distinct_description_falls_back_to_the_bare_form():
    filing = _edgar_filing("4", report_nm="4")
    assert filing_display.display_title(filing, None) == "Form 4"


def test_edgar_scheduled_spelled_out_form_alias_still_maps_correctly():
    """Real live SEC data returns the spelled-out "SCHEDULE 13G" rather
    than the abbreviated "SC 13G" (edgar_rules.py's own Gate 3 finding) —
    normalize_form_type is reused here so this table is never silently
    missed for that real-world spelling."""
    filing = _edgar_filing("SCHEDULE 13G", report_nm="SCHEDULE 13G")
    assert filing_display.display_title(filing, None) == "Beneficial Ownership Report — Schedule 13G"


def test_dart_title_prefers_stored_translation_unchanged_behavior():
    filing = _dart_filing("신규시설투자등 결정")
    candidate = CandidateSignal(
        id="cand-1", filing=filing, matched_rules=[], confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW,
        title_translation=Translation(translated_text="New facility investment decision", provider="DeepL", source_lang="ko", target_lang="en", translated_at=_now_iso()),
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    assert filing_display.display_title(filing, candidate) == "New facility investment decision"


def test_dart_title_falls_back_to_native_official_title_when_untranslated():
    filing = _dart_filing("신규시설투자등 결정")
    assert filing_display.display_title(filing, None) == "신규시설투자등 결정"


# ============================================================
# C: is_readable_extracted_text — the extraction quality gate
# ============================================================


def test_gate_rejects_raw_xbrl_context_and_namespace_markup():
    xbrl = (
        '<xbrli:context id="FD2026Q3QTD_us-gaap_StatementClassOfStockAxis">'
        '<xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0001835632</xbrli:identifier></xbrli:entity>'
        '</xbrli:context> us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="FD2026Q3QTD"'
    )
    assert filing_display.is_readable_extracted_text(xbrl) is False


def test_gate_rejects_bare_taxonomy_url():
    assert filing_display.is_readable_extracted_text(
        'xmlns:us-gaap="http://fasb.org/us-gaap/2026" some short trailer'
    ) is False


def test_gate_rejects_long_machine_style_identifiers_even_without_tags():
    dump = "us-gaap:Revenues 1234000000 us-gaap:CostOfRevenue 567000000 dei:DocumentType 10-Q"
    assert filing_display.is_readable_extracted_text(dump) is False


def test_gate_rejects_empty_or_none():
    assert filing_display.is_readable_extracted_text(None) is False
    assert filing_display.is_readable_extracted_text("") is False
    assert filing_display.is_readable_extracted_text("   ") is False


def test_gate_accepts_ordinary_english_financial_prose():
    text = "Item 2.02 Results of Operations. Revenue increased for the quarter compared to the prior year period."
    assert filing_display.is_readable_extracted_text(text) is True


def test_gate_accepts_ordinary_korean_prose():
    text = "당사는 2026년 8월 12일 이사회 결의를 통해 신규 시설 투자를 결정하였습니다. 투자 금액은 총 1,000억원입니다."
    assert filing_display.is_readable_extracted_text(text) is True


def test_gate_accepts_ordinary_japanese_prose():
    text = "当社は2026年6月22日開催の取締役会において、有価証券報告書を提出することを決議いたしました。詳細は添付書類をご参照ください。"
    assert filing_display.is_readable_extracted_text(text) is True


def test_gate_does_not_over_filter_a_single_short_legal_sentence():
    text = "The offering priced $50 million of 1,000,000 shares with net proceeds to the company."
    assert filing_display.is_readable_extracted_text(text) is True


# ============================================================
# B: extractive_summary / metadata_only_summary
# ============================================================


def test_extractive_summary_returns_short_text_verbatim():
    text = "Item 2.02 Results of Operations. Revenue increased."
    assert filing_display.extractive_summary(text) == text


def test_extractive_summary_truncates_long_text_at_a_sentence_boundary():
    text = "First sentence here. " + ("Filler word " * 60) + "Final sentence never included."
    summary = filing_display.extractive_summary(text)
    assert summary == "First sentence here."
    assert "Final sentence" not in summary


def test_extractive_summary_is_grounded_only_in_the_given_text():
    """Never introduces any word not present in the source text — the
    summary is always a literal prefix of the (whitespace-normalized)
    source, never a paraphrase or a fabricated addition."""
    text = "Revenue increased year over year across every reporting segment we track internally."
    summary = filing_display.extractive_summary(text)
    assert text.startswith(summary)


def test_metadata_only_summary_uses_the_documented_template_with_date():
    filing = _dart_filing("신규시설투자등 결정")
    summary = filing_display.metadata_only_summary(filing, "New facility investment decision", "Aug 12, 2026")
    assert summary == "삼성전자 filed New facility investment decision on Aug 12, 2026."


def test_metadata_only_summary_omits_the_date_clause_when_none_is_available():
    filing = _dart_filing("신규시설투자등 결정")
    summary = filing_display.metadata_only_summary(filing, "New facility investment decision", None)
    assert summary == "삼성전자 filed New facility investment decision."


def test_metadata_only_summary_never_uses_prohibited_wording():
    filing = _edgar_filing("10-Q")
    summary = filing_display.metadata_only_summary(filing, "Quarterly Report — Form 10-Q", "Aug 28, 2026")
    for prohibited in ("material", "signal", "review", "detected", "potential", "analysis", "metadata-only", "pending", "unavailable", "Phase 1"):
        assert prohibited not in summary.lower()
