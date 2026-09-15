"""edgar_rules — pure functions, no I/O. Covers scan-time form-type
routing and the post-extraction 8-K item-number refinement, including
normal metadata, an unmatched form type, and malformed/garbled text
that yields no item numbers."""
from __future__ import annotations

from src.data_access.edgar.edgar_rules import (
    EIGHT_K_ITEM_CATEGORIES,
    evaluate_form_type,
    evaluate_six_k,
    extract_item_numbers,
    items_from_matched_rules,
    iter_item_header_positions,
    merge_8k_item_evaluation,
    normalize_form_type,
    parse_items_metadata,
    refine_8k_evaluation,
)


def test_8k_gets_coarse_moderate_confidence_at_scan_time():
    result = evaluate_form_type("8-K")
    assert result.confidence == "Moderate"
    assert result.matched_rules == ("material_event_8k_pending_items:8-K",)


def test_10q_gets_earnings_category_directly():
    result = evaluate_form_type("10-Q")
    assert result.confidence == "Moderate"
    assert result.matched_rules == ("earnings_or_results:10-Q",)


def test_10k_gets_earnings_category_directly():
    result = evaluate_form_type("10-K")
    assert result.matched_rules == ("earnings_or_results:10-K",)


def test_13d_gets_ownership_change_category():
    result = evaluate_form_type("SC 13D")
    assert result.matched_rules == ("ownership_change:SC 13D",)


def test_13g_amendment_gets_ownership_change_category():
    result = evaluate_form_type("SC 13G/A")
    assert result.matched_rules == ("ownership_change:SC 13G/A",)


def test_real_spelled_out_schedule_13g_is_recognized():
    # Real SEC data returns "SCHEDULE 13G", not the abbreviated "SC 13G"
    # this module originally assumed — verified live (milestone 8, Gate
    # 3, a real NVDA SCHEDULE 13G was silently missed before this fix).
    result = evaluate_form_type("SCHEDULE 13G")
    assert result.matched_rules == ("ownership_change:SC 13G",)
    assert result.confidence == "Moderate"


def test_real_spelled_out_schedule_13d_is_recognized():
    result = evaluate_form_type("SCHEDULE 13D")
    assert result.matched_rules == ("ownership_change:SC 13D",)


def test_real_spelled_out_schedule_13g_amendment_is_recognized():
    result = evaluate_form_type("SCHEDULE 13G/A")
    assert result.matched_rules == ("ownership_change:SC 13G/A",)


def test_real_spelled_out_schedule_13d_amendment_is_recognized():
    result = evaluate_form_type("SCHEDULE 13D/A")
    assert result.matched_rules == ("ownership_change:SC 13D/A",)


def test_normalize_form_type_maps_both_spellings_to_the_same_canonical_form():
    assert normalize_form_type("SCHEDULE 13G") == normalize_form_type("SC 13G") == "SC 13G"
    assert normalize_form_type("schedule 13g") == "SC 13G"  # case-insensitive


def test_normalize_form_type_passes_through_unrecognized_forms_unchanged():
    assert normalize_form_type("10-Q") == "10-Q"
    assert normalize_form_type("DEF 14A") == "DEF 14A"


def test_s1_financing_form_gets_financing_category():
    result = evaluate_form_type("S-1")
    assert result.matched_rules == ("financing_or_debt:S-1",)


def test_unrecognized_form_type_does_not_become_a_candidate():
    result = evaluate_form_type("DEF 14A")
    assert result.confidence is None
    assert result.matched_rules == ()


def test_form_type_matching_is_case_and_whitespace_insensitive():
    result = evaluate_form_type("  8-k  ")
    assert result.confidence == "Moderate"


def test_extract_item_numbers_finds_configured_items_in_real_looking_text():
    text = "Item 1.01 Entry into a Material Definitive Agreement. ... Item 2.02 Results of Operations."
    items = extract_item_numbers(text)
    assert items == ("1.01", "2.02")


def test_extract_item_numbers_ignores_unconfigured_item_numbers():
    text = "Item 9.99 Some Unrecognized Section. Item 2.02 Results of Operations."
    items = extract_item_numbers(text)
    assert items == ("2.02",)


def test_extract_item_numbers_deduplicates():
    text = "Item 2.02 Results. Later in the document: Item 2.02 Results again."
    items = extract_item_numbers(text)
    assert items == ("2.02",)


def test_extract_item_numbers_returns_empty_for_garbled_text():
    assert extract_item_numbers("completely unrelated garbled text with no items") == ()


def test_extract_item_numbers_returns_empty_for_empty_text():
    assert extract_item_numbers("") == ()
    assert extract_item_numbers(None) == ()


def test_refine_8k_evaluation_single_item_is_moderate():
    result = refine_8k_evaluation(("2.02",))
    assert result.confidence == "Moderate"
    assert result.matched_rules == ("earnings_or_results:8-K item 2.02",)


def test_refine_8k_evaluation_multiple_categories_is_high():
    result = refine_8k_evaluation(("1.01", "2.02"))
    assert result.confidence == "High"
    assert len(result.matched_rules) == 2


def test_refine_8k_evaluation_empty_items_yields_no_confidence():
    # Caller (edgar_pipeline) must not apply this — keep the original
    # coarse scan-time classification instead.
    result = refine_8k_evaluation(())
    assert result.confidence is None
    assert result.matched_rules == ()


def test_all_eight_k_categories_have_the_brief_required_items():
    assert set(EIGHT_K_ITEM_CATEGORIES.keys()) == {"1.01", "2.01", "2.02", "2.03", "5.02", "7.01", "8.01"}


# --- parse_items_metadata (real scan-time `items` column, verified live
# in milestone 8 Gate 3 — NVDA accession 0001045810-26-000069 returned
# items="1.01,2.03,7.01" directly in filings.recent) ---

def test_parse_items_metadata_valid_multi_item_string():
    assert parse_items_metadata("1.01,2.03,7.01") == ("1.01", "2.03", "7.01")


def test_parse_items_metadata_single_item():
    assert parse_items_metadata("2.02") == ("2.02",)


def test_parse_items_metadata_empty_string_is_absence():
    assert parse_items_metadata("") == ()
    assert parse_items_metadata("   ") == ()


def test_parse_items_metadata_missing_column_is_absence():
    assert parse_items_metadata(None) == ()


def test_parse_items_metadata_malformed_tokens_are_dropped_not_guessed():
    # A garbage token, an unconfigured item number, and a real one mixed
    # together — only the real, configured one survives.
    assert parse_items_metadata("not-a-number,9.99,2.02") == ("2.02",)


def test_parse_items_metadata_fully_malformed_string_is_absence():
    assert parse_items_metadata("garbage;not;csv;at;all") == ()


def test_parse_items_metadata_deduplicates_preserving_order():
    assert parse_items_metadata("2.02,1.01,2.02") == ("2.02", "1.01")


def test_parse_items_metadata_tolerates_whitespace_around_tokens():
    assert parse_items_metadata(" 1.01 , 2.03 ,7.01") == ("1.01", "2.03", "7.01")


def test_multi_item_8k_classification_from_real_metadata_is_high_confidence():
    # The exact real value observed live for NVDA accession
    # 0001045810-26-000069.
    items = parse_items_metadata("1.01,2.03,7.01")
    result = refine_8k_evaluation(items)
    assert result.confidence == "High"
    assert result.matched_rules == (
        "material_agreement:8-K item 1.01",
        "financing_or_debt:8-K item 2.03",
        "regulation_fd_disclosure:8-K item 7.01",
    )


def test_reevaluating_the_same_items_metadata_twice_is_idempotent():
    # Scan-time classification, then a later post-extraction consistency
    # check finding the same items in the document text — must produce
    # an identical result, not a duplicate or drifted one.
    items = parse_items_metadata("1.01,2.03,7.01")
    first = refine_8k_evaluation(items)
    second = refine_8k_evaluation(parse_items_metadata("1.01,2.03,7.01"))
    assert first == second


# --- items_from_matched_rules / iter_item_header_positions (used by
# document_extractor's excerpt anchoring, milestone 8 Gate 6) ---

def test_items_from_matched_rules_extracts_real_shape():
    rules = ["material_agreement:8-K item 1.01", "financing_or_debt:8-K item 2.03"]
    assert items_from_matched_rules(rules) == ("1.01", "2.03")


def test_items_from_matched_rules_empty_for_coarse_classification():
    assert items_from_matched_rules(["material_event_8k_pending_items:8-K"]) == ()


def test_items_from_matched_rules_empty_for_non_8k_rules():
    assert items_from_matched_rules(["earnings_or_results:10-Q"]) == ()


def test_iter_item_header_positions_returns_ordered_pairs():
    text = "Item 1.01 First. Item 2.03 Second."
    positions = iter_item_header_positions(text)
    assert [p[0] for p in positions] == ["1.01", "2.03"]
    assert positions[0][1] < positions[1][1]


def test_iter_item_header_positions_empty_for_no_headers():
    assert iter_item_header_positions("no headers here") == []
    assert iter_item_header_positions("") == []


# --- merge_8k_item_evaluation (milestone 8, Gate 8 — monotonic merge:
# real scan-time SEC item metadata is authoritative and must never be
# downgraded by a bounded document excerpt reaching fewer items) ---

def test_merge_scan_time_superset_retains_all_categories_and_high_confidence():
    # The exact real NVDA case: scan-time knows all three items; the
    # bounded excerpt only reaches Item 1.01.
    evaluation, newly_added = merge_8k_item_evaluation(("1.01", "2.03", "7.01"), ("1.01",))
    assert evaluation.confidence == "High"
    assert set(i.split(" item ")[1] for i in evaluation.matched_rules) == {"1.01", "2.03", "7.01"}
    assert newly_added == ()


def test_merge_excerpt_adds_new_item_beyond_scan_time():
    evaluation, newly_added = merge_8k_item_evaluation(("1.01",), ("1.01", "2.03"))
    assert evaluation.confidence == "High"
    assert set(i.split(" item ")[1] for i in evaluation.matched_rules) == {"1.01", "2.03"}
    assert newly_added == ("2.03",)


def test_merge_no_scan_time_items_falls_back_to_document_only_behavior():
    evaluation, newly_added = merge_8k_item_evaluation((), ("2.02",))
    assert evaluation.confidence == "Moderate"
    assert evaluation.matched_rules == ("earnings_or_results:8-K item 2.02",)
    assert newly_added == ("2.02",)


def test_merge_empty_scan_time_and_empty_excerpt_yields_no_confidence():
    # Caller must not overwrite the existing coarse classification here.
    evaluation, newly_added = merge_8k_item_evaluation((), ())
    assert evaluation.confidence is None
    assert newly_added == ()


def test_merge_is_order_independent_for_the_final_category_set():
    evaluation_a, _ = merge_8k_item_evaluation(("2.03", "1.01"), ("7.01",))
    evaluation_b, _ = merge_8k_item_evaluation(("1.01", "2.03"), ("7.01",))
    assert set(evaluation_a.matched_rules) == set(evaluation_b.matched_rules)
    assert evaluation_a.confidence == evaluation_b.confidence == "High"


def test_merge_reapplying_the_same_inputs_is_idempotent():
    first, first_new = merge_8k_item_evaluation(("1.01", "2.03", "7.01"), ("1.01",))
    second, second_new = merge_8k_item_evaluation(("1.01", "2.03", "7.01"), ("1.01",))
    assert first == second
    assert first_new == second_new == ()


# ============================================================
# EDGAR foreign-private-issuer 20-F/6-K admission, Phase 1
# (design/DECISIONS.md) — 20-F direct mapping, 6-K filename gate.
# ============================================================


def test_20f_gets_earnings_category_directly():
    """20-F is SEC's own annual-report equivalent for a foreign private
    issuer — mapped exactly like 10-K, unconditionally."""
    result = evaluate_form_type("20-F")
    assert result.matched_rules == ("earnings_or_results:20-F",)
    assert result.confidence == "Moderate"


def test_material_6k_quarterly_filename_is_admitted():
    """Real, live ASML Holding N.V. filename (CIK 0000937966, fetched
    2026-09-15)."""
    result = evaluate_six_k("form6-kquarterlyfilings.htm")
    assert result.confidence == "Moderate"
    assert result.matched_rules == ("foreign_issuer_current_report:6-K:quarterly",)


def test_material_6k_annual_report_filename_is_admitted():
    """Real, live ASML Holding N.V. filename (CIK 0000937966, fetched
    2026-09-15)."""
    result = evaluate_six_k("form6-kannualreportbasedon.htm")
    assert result.confidence == "Moderate"
    assert result.matched_rules == ("foreign_issuer_current_report:6-K:annualreport",)


def test_routine_6k_agm_disclosure_filename_is_rejected():
    """Real, live ASML Holding N.V. filename (CIK 0000937966, fetched
    2026-09-15) — a routine Annual General Meeting disclosure, never a
    corporate financial event."""
    result = evaluate_six_k("form6-kagmdisclosureofagmr.htm")
    assert result.confidence is None
    assert result.matched_rules == ()


def test_opaque_arm_style_6k_filename_is_rejected_in_phase_1():
    """Real, live Arm Holdings plc /UK filename (CIK 0001973239, fetched
    2026-09-15) — a date-stamped, non-descriptive filename. Documents
    the known, deliberate Phase 1 limitation: this issuer's own filing
    convention carries no scan-time content signal this gate can read,
    so it fails closed exactly like an unrecognized form type today,
    rather than being guessed into a category."""
    result = evaluate_six_k("arm-20260910.htm")
    assert result.confidence is None
    assert result.matched_rules == ()


def test_unknown_6k_filename_is_rejected():
    result = evaluate_six_k("form6-kexhibit991.htm")
    assert result.confidence is None


def test_6k_with_missing_primary_document_is_rejected():
    result = evaluate_six_k("")
    assert result.confidence is None
    result_none = evaluate_six_k(None)
    assert result_none.confidence is None


def test_6k_routine_term_wins_even_when_a_material_term_also_appears():
    """The deny-list is checked before the allow-list — a coincidental
    substantive-sounding word elsewhere in a routine filename must never
    override an explicit routine-content signal."""
    result = evaluate_six_k("form6-kquarterly-agm-notice.htm")
    assert result.confidence is None


def test_6k_shareholder_proxy_and_governance_filenames_are_all_rejected():
    for filename in (
        "form6-kshareholdernotice.htm",
        "form6-kproxystatement.htm",
        "form6-kgovernanceupdate.htm",
        "form6-kadministrativenotice.htm",
    ):
        result = evaluate_six_k(filename)
        assert result.confidence is None, filename


def test_bare_6k_form_type_never_promotes_via_the_generic_path():
    """Proves 6-K is deliberately never a FORM_TYPE_CATEGORIES key — the
    ONLY path to a 6-K candidate is evaluate_six_k()'s own filename
    gate, never evaluate_form_type() alone."""
    result = evaluate_form_type("6-K")
    assert result.confidence is None
    assert result.matched_rules == ()


def test_no_generic_6k_can_become_a_candidate_without_passing_the_filename_gate():
    """End-to-end proof combining the two properties above: neither the
    bare form type nor an empty/unrecognized filename is ever enough on
    its own."""
    assert evaluate_form_type("6-K").confidence is None
    assert evaluate_six_k("").confidence is None
    assert evaluate_six_k("some-unrecognized-file.htm").confidence is None
    # Only a real, curated material-content term in the filename admits:
    assert evaluate_six_k("form6-kquarterlyfilings.htm").confidence == "Moderate"


# ============================================================
# 6-K allow-list final review (design/DECISIONS.md, "EDGAR foreign-
# private-issuer 20-F/6-K admission, Phase 1 allow-list review") —
# "financial", "results", and "order" were removed as too generic/
# ambiguous to prove materiality alone; each is subsumed by a more
# specific surviving term. These tests lock in the removal so it can
# never silently regress back in.
# ============================================================


def test_bare_financial_term_alone_no_longer_promotes_a_6k():
    result = evaluate_six_k("form6-kfinancial.htm")
    assert result.confidence is None


def test_bare_results_term_alone_no_longer_promotes_a_6k():
    """The specific, real-world collision risk this removal closes: a
    routine AGM voting-results announcement must never be promoted just
    because it happens to say "results"."""
    result = evaluate_six_k("form6-kvotingresults.htm")
    assert result.confidence is None


def test_bare_order_term_alone_no_longer_promotes_a_6k():
    result = evaluate_six_k("form6-korder.htm")
    assert result.confidence is None


def test_financialresults_compound_term_still_promotes_a_6k():
    """The removal of bare "financial"/"results" does not remove
    genuine coverage — the compound form they were subsumed into still
    works."""
    result = evaluate_six_k("form6-kfinancialresults.htm")
    assert result.confidence == "Moderate"
    assert result.matched_rules == ("foreign_issuer_current_report:6-K:financialresults",)


def test_contract_term_still_covers_the_major_contract_or_order_category():
    """The removal of bare "order" does not remove genuine coverage —
    "contract" still represents the "material contract/order" category."""
    result = evaluate_six_k("form6-kmaterialcontractannouncement.htm")
    assert result.confidence == "Moderate"
    assert result.matched_rules == ("foreign_issuer_current_report:6-K:contract",)


def test_filename_extension_is_stripped_before_matching():
    """Normalization proof: a recognized file extension never
    participates in matching (defensive — no current term is short
    enough to accidentally match inside ".htm"/".html"/".txt"/".pdf",
    but this proves the stripping itself actually happens)."""
    from src.data_access.edgar.edgar_rules import _normalized_filename

    assert _normalized_filename("form6-kquarterlyfilings.HTM") == "form6-kquarterlyfilings"
    assert _normalized_filename("report.PDF") == "report"
    assert _normalized_filename(None) == ""
