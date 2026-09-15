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
# A2: cross-market filing-card title clarity fix — DART/EDINET
# event-category prefix (design/DECISIONS.md)
# ============================================================


def _edinet_filing(report_nm: str, pblntf_ty: str = "", pblntf_detail_ty: str = "", ordinance_code: str = "") -> FilingEvent:
    return FilingEvent(
        rcept_no="S100Z0ID", corp_code="E00776", corp_name="Shin-Etsu Chemical Co., Ltd.", stock_code="40630",
        report_nm=report_nm, rcept_dt="2026-09-04", flr_nm="信越化学工業株式会社",
        pblntf_ty=pblntf_ty, pblntf_detail_ty=pblntf_detail_ty, ordinance_code=ordinance_code,
        source_url="https://api.edinet-fsa.go.jp/api/v2/documents/S100Z0ID", retrieved_at=_now_iso(),
        source_name="EDINET", original_language="Japanese",
    )


def _candidate_with_matched_rules(filing: FilingEvent, matched_rules: list[str], **kwargs) -> CandidateSignal:
    return CandidateSignal(
        id="cand-1", filing=filing, matched_rules=matched_rules, confidence="Moderate",
        status=CandidateStatus.NEEDS_REVIEW,
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
        **kwargs,
    )


def test_dart_title_prefixes_with_the_known_event_category_in_native_mode():
    filing = _dart_filing("신규시설투자등 결정")
    candidate = _candidate_with_matched_rules(filing, ["capex_or_facility_investment:facility_investment:신규시설투자"])
    assert filing_display.display_title(filing, candidate) == "Facility Investment — 신규시설투자등 결정"


def test_dart_title_prefix_still_applies_over_a_stored_translation():
    filing = _dart_filing("신규시설투자등 결정")
    candidate = _candidate_with_matched_rules(
        filing, ["capex_or_facility_investment:facility_investment:신규시설투자"],
        title_translation=Translation(
            translated_text="New facility investment decision", provider="DeepL",
            source_lang="ko", target_lang="en", translated_at=_now_iso(),
        ),
    )
    assert filing_display.display_title(filing, candidate) == "Facility Investment — New facility investment decision"


def test_dart_title_never_duplicates_when_mapped_phrase_equals_existing_title():
    filing = _dart_filing("신규시설투자등 결정")
    candidate = _candidate_with_matched_rules(
        filing, ["capex_or_facility_investment:facility_investment:신규시설투자"],
        title_translation=Translation(
            translated_text="Facility Investment", provider="DeepL",
            source_lang="ko", target_lang="en", translated_at=_now_iso(),
        ),
    )
    assert filing_display.display_title(filing, candidate) == "Facility Investment"


def test_dart_title_unmapped_category_falls_back_to_unchanged_behavior():
    filing = _dart_filing("신규시설투자등 결정")
    candidate = _candidate_with_matched_rules(filing, ["some_future_category:some_rule:keyword"])
    assert filing_display.display_title(filing, candidate) == "신규시설투자등 결정"


def test_dart_title_skips_a_leading_non_category_marker_to_find_the_real_category():
    """dart_rules.py's own "amendment_or_correction" matched_rules entry
    carries no category prefix at all (see dart_rules.evaluate_report_
    name) — a pathological ordering with it first must never be mistaken
    for a category and must never suppress a real category later in the
    list."""
    filing = _dart_filing("[기재정정] 신규시설투자등 결정")
    candidate = _candidate_with_matched_rules(
        filing, ["amendment_or_correction", "capex_or_facility_investment:facility_investment:신규시설투자"],
    )
    assert filing_display.display_title(filing, candidate) == "Facility Investment — [기재정정] 신규시설투자등 결정"


def test_dart_title_with_no_candidate_falls_back_to_unchanged_behavior():
    filing = _dart_filing("신규시설투자등 결정")
    assert filing_display.display_title(filing, None) == "신규시설투자등 결정"


def test_edinet_title_prefixes_with_the_known_event_category_in_native_mode():
    filing = _edinet_filing(
        "自己株券買付状況報告書（法２４条の６第１項に基づくもの）",
        pblntf_ty="170000", pblntf_detail_ty="220", ordinance_code="010",
    )
    candidate = _candidate_with_matched_rules(filing, ["share_buyback_status:010:170000:220"])
    assert filing_display.display_title(filing, candidate) == (
        "Status Report of Purchase of Own Shares — 自己株券買付状況報告書（法２４条の６第１項に基づくもの）"
    )


def test_edinet_title_prefix_collapses_when_translation_already_matches():
    filing = _edinet_filing("臨時報告書", pblntf_ty="053000", pblntf_detail_ty="180", ordinance_code="010")
    candidate = _candidate_with_matched_rules(
        filing, ["extraordinary_report:010:053000:180"],
        title_translation=Translation(
            translated_text="Extraordinary Report", provider="DeepL",
            source_lang="ja", target_lang="en", translated_at=_now_iso(),
        ),
    )
    assert filing_display.display_title(filing, candidate) == "Extraordinary Report"


def test_edinet_title_unmapped_category_falls_back_to_unchanged_behavior():
    filing = _edinet_filing("四半期報告書", pblntf_ty="999999", pblntf_detail_ty="999", ordinance_code="010")
    candidate = _candidate_with_matched_rules(filing, [])
    assert filing_display.display_title(filing, candidate) == "四半期報告書"


def test_every_real_dart_lexicon_category_has_a_curated_display_title():
    """Drift guard: dart_rules.py's own KOREAN_KEYWORD_LEXICON is the one
    source of truth for which categories are real; _DART_CATEGORY_TITLES
    must never silently fall behind it."""
    from src.data_access.dart.dart_rules import KOREAN_KEYWORD_LEXICON

    for category in KOREAN_KEYWORD_LEXICON:
        assert category in filing_display._DART_CATEGORY_TITLES, category


def test_every_real_edinet_mapped_category_has_a_curated_display_title():
    """Drift guard: edinet_rules.py's own DEFAULT_CODE_CATEGORY_MAP is
    the one source of truth for which categories are real and live-
    verified; _EDINET_CATEGORY_TITLES must never silently fall behind
    it."""
    from src.data_access.edinet.edinet_rules import DEFAULT_CODE_CATEGORY_MAP

    for category in DEFAULT_CODE_CATEGORY_MAP.values():
        assert category in filing_display._EDINET_CATEGORY_TITLES, category


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


def test_extractive_summary_extends_into_the_bounded_window_for_a_late_sentence_boundary():
    """No sentence terminator exists inside the 320-char target window
    here, but one exists shortly after it, well inside the 640-char
    bounded search window — the summary must run long enough to reach it
    rather than being cut at the target."""
    filler = "filler word " * 30  # 360 chars, no terminator anywhere in it
    text = filler + "First sentence ends right here. " + ("more filler word " * 40)
    summary = filing_display.extractive_summary(text)
    assert summary.endswith("First sentence ends right here.")
    assert not summary.endswith("…")
    assert not summary.endswith("...")
    assert 320 < len(summary) <= 640
    assert text.startswith(summary)


def test_extractive_summary_returns_empty_when_no_terminator_exists_within_the_bounded_window():
    """No sentence terminator anywhere in the text at all, and the text
    is long enough that it cannot be returned verbatim — must never fall
    back to a word-boundary-truncated excerpt plus an ellipsis. An empty
    return is the caller's signal to use metadata_only_summary()."""
    text = "word " * 200  # 1000 chars, zero sentence-ending punctuation
    summary = filing_display.extractive_summary(text)
    assert summary == ""


def test_extractive_summary_returns_empty_when_the_only_terminator_is_beyond_the_bounded_window():
    """A sentence terminator does exist, but only past the 640-char
    bounded search window — still must not return a raw truncation."""
    filler = "filler word " * 60  # 720 chars, no terminator anywhere in it
    text = filler + "Sentence finally ends far too late."
    summary = filing_display.extractive_summary(text)
    assert summary == ""


def test_extractive_summary_still_returns_short_boundary_free_text_verbatim():
    """A short text with no sentence terminator at all still needs no
    truncation decision, so it is returned verbatim — unchanged from the
    pre-existing behavior this fix must not regress."""
    text = "Loan amount stated without a terminating period"
    assert filing_display.extractive_summary(text) == text


# ============================================================
# D2: trim_excerpt_for_display
# ============================================================


def test_trim_excerpt_for_display_drops_trailing_unterminated_clause():
    """Real production shape (docID S100Z0OT): a trailing clause with no
    sentence terminator before the extraction cut. Must keep every
    complete sentence that precedes it and drop only the fragment."""
    text = (
        "The Company hereby submits this report. (2) Overview of the loan "
        "agreement is as follows. (3) Details of Financial Covenants As of "
        "the end of each fiscal year, the consolidated"
    )
    assert filing_display.trim_excerpt_for_display(text) == (
        "The Company hereby submits this report. (2) Overview of the loan "
        "agreement is as follows."
    )


def test_trim_excerpt_for_display_keeps_a_single_complete_sentence_when_it_is_the_last_one():
    text = "This is a complete sentence. And another one."
    assert filing_display.trim_excerpt_for_display(text) == text


def test_trim_excerpt_for_display_handles_japanese_full_width_terminators():
    """_SENTENCE_END_PATTERN (shared with extractive_summary/
    is_readable_extracted_text) requires the terminator to be followed
    by whitespace or end-of-string — matching real extracted text, where
    document_extractor.py's whitespace-collapse turns an original
    line/paragraph break between sentences into exactly one space, as
    reproduced here."""
    text = "これは完全な文です。 これは未完成な部分で終わ"
    assert filing_display.trim_excerpt_for_display(text) == "これは完全な文です。"


def test_trim_excerpt_for_display_handles_korean_ascii_terminators():
    """Korean prose uses the same ASCII '.'/'!'/'?' terminators as
    English, not a distinct full-width convention — already covered by
    the shared pattern with no per-language branching needed."""
    text = "이것은 완전한 문장입니다. 이것은 미완성 부분으로 끝"
    assert filing_display.trim_excerpt_for_display(text) == "이것은 완전한 문장입니다."


def test_trim_excerpt_for_display_returns_empty_when_no_boundary_exists_anywhere():
    text = "word " * 100
    assert filing_display.trim_excerpt_for_display(text) == ""


def test_trim_excerpt_for_display_handles_empty_and_whitespace_only():
    assert filing_display.trim_excerpt_for_display("") == ""
    assert filing_display.trim_excerpt_for_display("   ") == ""


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


# ============================================================
# D: strip_edinet_machine_artifacts
# ============================================================


def test_strip_edinet_machine_artifacts_removes_english_title_timestamp_and_heading():
    """Real production shape (docID S100Z0OT): a translated document-
    title-timestamp label immediately followed by a numbered, ASCII-
    bracketed item heading — both removed, leaving only the substantive
    sentence that follows."""
    text = "Extraordinary Report_20260909153311 1 [Reason for Submission] The Company resolved to borrow funds."
    assert filing_display.strip_edinet_machine_artifacts(text) == "The Company resolved to borrow funds."


def test_strip_edinet_machine_artifacts_removes_japanese_title_timestamp_and_heading():
    """Same real shape in the native Japanese text — full-width brackets
    and a full-width leading numeral, both handled without any per-
    language branching."""
    text = "臨時報告書_20260909153311 １【提出理由】本日開催の取締役会において、資金を借り入れることを決議した。"
    assert filing_display.strip_edinet_machine_artifacts(text) == "本日開催の取締役会において、資金を借り入れることを決議した。"


def test_strip_edinet_machine_artifacts_handles_either_artifact_alone():
    only_timestamp = "Extraordinary Report_20260909153311 The Company resolved to borrow funds."
    assert filing_display.strip_edinet_machine_artifacts(only_timestamp) == "The Company resolved to borrow funds."

    only_heading = "1 [Reason for Submission] The Company resolved to borrow funds."
    assert filing_display.strip_edinet_machine_artifacts(only_heading) == "The Company resolved to borrow funds."


def test_strip_edinet_machine_artifacts_preserves_mid_text_underscore_timestamp_shape():
    """A coincidental "word_14digits" shape that is NOT at the very start
    of the text — because real prose precedes it — must never be
    stripped. The leading-word-boundary constraint (no underscore
    permitted inside any of the leading label's own words) is what makes
    this safe: "ABC_Bank" can never be consumed as one leading word."""
    text = "The lender is ABC_Bank_20260909153311 and the note [Note 1] applies here."
    assert filing_display.strip_edinet_machine_artifacts(text) == text


def test_strip_edinet_machine_artifacts_preserves_mid_text_bracket():
    text = "The Company entered into an agreement [see Note 1] for working capital purposes."
    assert filing_display.strip_edinet_machine_artifacts(text) == text


def test_strip_edinet_machine_artifacts_is_a_noop_on_already_clean_text():
    text = "The Company entered into a loan agreement with a lender for working capital."
    assert filing_display.strip_edinet_machine_artifacts(text) == text


def test_strip_edinet_machine_artifacts_handles_empty_and_none():
    assert filing_display.strip_edinet_machine_artifacts("") == ""
    assert filing_display.strip_edinet_machine_artifacts(None) is None


def test_strip_edinet_machine_artifacts_removes_leading_bom_before_english_artifacts():
    """Live regression (docID S100Z0OT, found after PR #16): a leading
    U+FEFF (invisible Unicode BOM) sat in front of the title-timestamp
    label and blocked the existing start-anchored regexes from matching
    at all, since U+FEFF is neither \\w nor \\s. Must clean to the
    substantive sentence with no title-timestamp or heading artifact."""
    text = "\ufeff Extraordinary Report_20260909153311 1 [Reason for Submission] As an event has occurred..."
    assert filing_display.strip_edinet_machine_artifacts(text) == "As an event has occurred..."


def test_strip_edinet_machine_artifacts_removes_leading_bom_before_japanese_artifacts():
    text = "\ufeff 臨時報告書_20260909153311 １【提出理由】当社の財政状態..."
    assert filing_display.strip_edinet_machine_artifacts(text) == "当社の財政状態..."


def test_strip_edinet_machine_artifacts_preserves_interior_bom():
    """The leading-BOM fix must never become a body-wide replace — a
    U+FEFF occurring anywhere other than the very start of the text is a
    legitimate Unicode character and must be preserved untouched."""
    text = "The Company noted\ufeffsomething here without further comment."
    assert filing_display.strip_edinet_machine_artifacts(text) == text


# ============================================================
# E: excerpt_may_be_incomplete
# ============================================================


def test_excerpt_may_be_incomplete_false_below_the_cap():
    assert filing_display.excerpt_may_be_incomplete("a" * 599) is False


def test_excerpt_may_be_incomplete_true_at_the_cap():
    assert filing_display.excerpt_may_be_incomplete("a" * 600) is True


def test_excerpt_may_be_incomplete_true_above_the_cap():
    assert filing_display.excerpt_may_be_incomplete("a" * 601) is True


def test_excerpt_may_be_incomplete_false_for_none_or_empty():
    assert filing_display.excerpt_may_be_incomplete(None) is False
    assert filing_display.excerpt_may_be_incomplete("") is False


# ============================================================
# F: official_filing_reference
# ============================================================


def test_official_filing_reference_edinet_labels_and_values():
    filing = FilingEvent(
        rcept_no="S100Z0OT", corp_code="E37584", corp_name="ispace, inc.", stock_code="93480",
        report_nm="臨時報告書", rcept_dt="2026-09-10", flr_nm="株式会社ispace",
        source_url="https://api.edinet-fsa.go.jp/api/v2/documents/S100Z0OT",
        retrieved_at=_now_iso(), source_name="EDINET", original_language="Japanese",
    )
    reference = filing_display.official_filing_reference(filing, "Sep 10, 2026")
    assert "EDINET issuer code: E37584" in reference
    assert "Securities code: 93480" in reference
    assert "Document ID: S100Z0OT" in reference
    assert "Filed: Sep 10, 2026" in reference
    assert "DART issuer code" not in reference
    assert "CIK" not in reference


def test_official_filing_reference_dart_labels_never_say_edinet():
    filing = _dart_filing("신규시설투자등 결정")
    reference = filing_display.official_filing_reference(filing, "Aug 12, 2026")
    assert "DART issuer code: 00126380" in reference
    assert "Securities code: 005930" in reference
    assert "Receipt number: 20260812000001" in reference
    assert "EDINET" not in reference
    assert "CIK" not in reference


def test_official_filing_reference_edgar_labels_never_say_edinet():
    filing = _edgar_filing("10-Q")
    reference = filing_display.official_filing_reference(filing, "Aug 28, 2026")
    assert "CIK: 0001045810" in reference
    assert "Accession number: 0001045810-26-000001" in reference
    assert "EDINET" not in reference
    assert "DART issuer code" not in reference


def test_official_filing_reference_omits_absent_fields():
    filing = FilingEvent(
        rcept_no="", corp_code="", corp_name="No Codes Corp.", stock_code="",
        report_nm="有価証券報告書", rcept_dt="", flr_nm="", retrieved_at=_now_iso(), source_name="EDINET",
    )
    reference = filing_display.official_filing_reference(filing, None)
    assert reference == "Provider: EDINET"


# ============================================================
# G: review_needed — the "Review needed" badge's pure predicate
# ============================================================


def test_review_needed_is_false_for_a_normal_dart_filing_with_both_identifiers():
    filing = _dart_filing("신규시설투자등 결정")
    flag = filing_display.review_needed(filing)
    assert flag.flagged is False
    assert flag.reason is None


def test_review_needed_is_false_for_a_normal_edinet_filing_with_both_identifiers():
    filing = _edinet_filing("臨時報告書", pblntf_ty="053000", pblntf_detail_ty="180", ordinance_code="010")
    assert filing_display.review_needed(filing).flagged is False


def test_review_needed_is_false_for_a_normal_edgar_filing():
    filing = _edgar_filing("10-K")
    assert filing_display.review_needed(filing).flagged is False


def test_review_needed_flags_a_missing_issuer_code():
    filing = FilingEvent(
        rcept_no="S100Z0ID", corp_code="", corp_name="Ambiguous Issuer Co.", stock_code="40630",
        report_nm="臨時報告書", rcept_dt="2026-09-04", flr_nm="Ambiguous Issuer Co.",
        retrieved_at=_now_iso(), source_name="EDINET",
    )
    flag = filing_display.review_needed(filing)
    assert flag.flagged is True
    assert flag.reason == "missing_issuer_code"


def test_review_needed_flags_a_missing_exchange_ticker():
    filing = FilingEvent(
        rcept_no="20260812000001", corp_code="00126380", corp_name="Ambiguous Issuer Co.", stock_code="",
        report_nm="신규시설투자등 결정", rcept_dt="20260812", flr_nm="Ambiguous Issuer Co.", retrieved_at=_now_iso(),
    )
    flag = filing_display.review_needed(filing)
    assert flag.flagged is True
    assert flag.reason == "missing_exchange_ticker"


def test_review_needed_flags_both_identifiers_missing_with_the_combined_reason():
    filing = FilingEvent(
        rcept_no="X", corp_code="", corp_name="Ambiguous Issuer Co.", stock_code="",
        report_nm="Unresolved Filing", rcept_dt="2026-09-04", flr_nm="Ambiguous Issuer Co.",
        retrieved_at=_now_iso(),
    )
    flag = filing_display.review_needed(filing)
    assert flag.flagged is True
    assert flag.reason == "missing_issuer_code_and_ticker"


def test_review_needed_treats_whitespace_only_identifiers_as_missing():
    filing = FilingEvent(
        rcept_no="X", corp_code="   ", corp_name="Ambiguous Issuer Co.", stock_code="40630",
        report_nm="臨時報告書", rcept_dt="2026-09-04", flr_nm="Ambiguous Issuer Co.", retrieved_at=_now_iso(),
    )
    assert filing_display.review_needed(filing).flagged is True
