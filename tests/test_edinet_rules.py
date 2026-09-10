"""edinet_rules — pure functions, no I/O.

Gate 10 populated DEFAULT_CODE_CATEGORY_MAP with its first real entry —
the live-verified SoftBank Group annual securities report tuple
(ordinanceCode=010, formCode=030000, docTypeCode=120 →
"annual_securities_report"; see design/DECISIONS.md's Gate 10 entry for
the full evidence chain). A second real entry (share_buyback_status,
010:170000:220) and a third (extraordinary_report, 010:053000:180 —
promoted from the existing, already-tested material_event_shadow.py
evaluator) were added later — see edinet_rules.py's own
DEFAULT_CODE_CATEGORY_MAP docstring for each entry's own evidence.
Every other real ordinanceCode/formCode/docTypeCode combination —
including the four verified companion/look-alike filings' own tuples
(010:042000:135, 015:010000:235, 010:170001:230, 030:253000:220,
030:995000:180, 010:053001:190), deliberately left unmapped — must
still yield confidence=None. Tests exercising the *generic matching
mechanism* (not these real mappings) use their own explicit,
deliberately fictional test codes/category names — never "earnings_or_
results" (retired after Gate 10's correction) and never any of the real,
live-verified tuples — so a fictional-mechanism test can never be
mistaken for a claim about real EDINET data."""
from __future__ import annotations

from src.data_access.edinet.edinet_rules import (
    DEFAULT_CODE_CATEGORY_MAP,
    EDINET_CATEGORIES,
    evaluate_document,
    merge_evaluations,
)

# Fictional test codes/categories — NOT real EDINET
# ordinanceCode/formCode/docTypeCode values, and deliberately distinct
# from all three live-verified SoftBank Group tuples.
_TEST_MAP = {
    "999:888:001": "fictional_category_alpha",
    "999:888:002": "fictional_category_beta",
}

# The three real, live-verified mappings — used only by tests that
# specifically exercise them, never implicitly via the fictional _TEST_MAP.
_REAL_ANNUAL_REPORT_KEY = "010:030000:120"
_REAL_SHARE_BUYBACK_STATUS_KEY = "010:170000:220"
_REAL_EXTRAORDINARY_REPORT_KEY = "010:053000:180"

# Real, confirmed look-alike triplets that must never match the base
# share-buyback rule — see edinet_rules.py's own DEFAULT_CODE_CATEGORY_MAP
# docstring for the full evidence chain (384 filings / 239 independent
# issuers sampled 2026-06-03 through 2026-09-04).
_REAL_CORRECTED_BUYBACK_KEY = "010:170001:230"  # 訂正自己株券買付状況報告書
_REAL_SPECIFIED_SECURITIES_BUYBACK_KEY = "030:253000:220"  # fund/REIT variant

# Real, confirmed look-alike triplets that must never match the
# extraordinary-report rule — same four exclusions
# material_event_shadow.py's own eligibility check already established.
_REAL_DOMESTIC_SPECIFIED_SECURITIES_EXTRAORDINARY_KEY = "030:995000:180"  # 臨時報告書（内国特定有価証券）
_REAL_CORRECTED_EXTRAORDINARY_REPORT_KEY = "010:053001:190"  # 訂正臨時報告書


def test_default_code_category_map_has_exactly_three_real_entries():
    assert DEFAULT_CODE_CATEGORY_MAP == {
        _REAL_ANNUAL_REPORT_KEY: "annual_securities_report",
        _REAL_SHARE_BUYBACK_STATUS_KEY: "share_buyback_status",
        _REAL_EXTRAORDINARY_REPORT_KEY: "extraordinary_report",
    }


def test_evaluate_document_default_map_matches_only_the_real_annual_report_tuple():
    result = evaluate_document("010", "030000", "120")
    assert result.confidence == "Moderate"
    assert result.matched_rules == ("annual_securities_report:010:030000:120",)


def test_evaluate_document_default_map_matches_the_real_share_buyback_status_tuple():
    result = evaluate_document("010", "170000", "220")
    assert result.confidence == "Moderate"
    assert result.matched_rules == ("share_buyback_status:010:170000:220",)


def test_evaluate_document_default_map_does_not_match_corrected_buyback_variant():
    # 訂正自己株券買付状況報告書 (corrected/amended) — a different
    # docTypeCode from the base rule; must remain unmapped.
    result = evaluate_document("010", "170001", "230")
    assert result.confidence is None
    assert result.matched_rules == ()


def test_evaluate_document_default_map_does_not_match_specified_securities_buyback_variant():
    # The fund/REIT variant, filed under a different ordinance despite an
    # identical title string; must remain unmapped.
    result = evaluate_document("030", "253000", "220")
    assert result.confidence is None
    assert result.matched_rules == ()


def test_evaluate_document_default_map_matches_the_real_extraordinary_report_tuple():
    # 臨時報告書 (Extraordinary Report) — promoted from the existing,
    # already-tested shadow evaluator (material_event_shadow.py).
    result = evaluate_document("010", "053000", "180")
    assert result.confidence == "Moderate"
    assert result.matched_rules == ("extraordinary_report:010:053000:180",)


def test_evaluate_document_default_map_does_not_match_domestic_specified_securities_extraordinary_report_variant():
    # 臨時報告書（内国特定有価証券）— filed by asset-management/fund
    # entities about securities they manage, never a tracked issuer's own
    # material corporate event; must remain unmapped.
    result = evaluate_document("030", "995000", "180")
    assert result.confidence is None
    assert result.matched_rules == ()


def test_evaluate_document_default_map_does_not_match_corrected_extraordinary_report_variant():
    # 訂正臨時報告書 (corrected/amended) — a different docTypeCode from
    # the base rule; must remain unmapped.
    result = evaluate_document("010", "053001", "190")
    assert result.confidence is None
    assert result.matched_rules == ()


def test_evaluate_document_default_map_does_not_match_fictional_codes():
    result = evaluate_document("999", "888", "001")
    assert result.confidence is None
    assert result.matched_rules == ()


def test_evaluate_document_default_map_does_not_match_softbank_companion_tuples():
    # The two verified SoftBank Group companion filings — 確認書 and
    # 内部統制報告書 — deliberately remain unmapped; excluded from both
    # the annual-report and the extraordinary-report mappings.
    confirmation_letter = evaluate_document("010", "042000", "135")
    internal_control_report = evaluate_document("015", "010000", "235")
    assert confirmation_letter.confidence is None
    assert confirmation_letter.matched_rules == ()
    assert internal_control_report.confidence is None
    assert internal_control_report.matched_rules == ()


def test_evaluate_document_requires_all_three_values_not_ordinance_form_pair_alone():
    # Same ordinanceCode+formCode as the real annual-report tuple, but a
    # different docTypeCode — must NOT match. This is the exact
    # correction Gate 10 required: routing keys on all three fields, not
    # ordinance:form alone.
    result = evaluate_document("010", "030000", "999")
    assert result.confidence is None
    assert result.matched_rules == ()


def test_evaluate_document_matches_when_given_an_explicit_fictional_test_map():
    result = evaluate_document("999", "888", "001", code_category_map=_TEST_MAP)
    assert result.confidence == "Moderate"
    assert result.matched_rules == ("fictional_category_alpha:999:888:001",)


def test_evaluate_document_unmatched_code_with_fictional_test_map_yields_no_confidence():
    result = evaluate_document("111", "222", "333", code_category_map=_TEST_MAP)
    assert result.confidence is None
    assert result.matched_rules == ()


def test_evaluate_document_is_whitespace_tolerant_in_routing_key():
    result = evaluate_document(" 999 ", " 888 ", " 001 ", code_category_map=_TEST_MAP)
    assert result.confidence == "Moderate"


def test_all_edinet_categories_are_english_slugs():
    assert all(isinstance(c, str) and c == c.lower() for c in EDINET_CATEGORIES)
    assert "annual_securities_report" in EDINET_CATEGORIES
    assert "ownership_or_large_shareholding" in EDINET_CATEGORIES
    assert "other" in EDINET_CATEGORIES


def test_merge_evaluations_unions_matched_rules_without_duplicates():
    a = evaluate_document("999", "888", "001", code_category_map=_TEST_MAP)
    b = evaluate_document("999", "888", "002", code_category_map=_TEST_MAP)
    merged = merge_evaluations([a, b])
    assert merged.confidence == "High"  # two distinct categories
    assert set(merged.matched_rules) == {"fictional_category_alpha:999:888:001", "fictional_category_beta:999:888:002"}


def test_merge_evaluations_deduplicates_identical_rules():
    a = evaluate_document("999", "888", "001", code_category_map=_TEST_MAP)
    merged = merge_evaluations([a, a])
    assert merged.matched_rules == a.matched_rules
    assert merged.confidence == "Moderate"


def test_merge_evaluations_empty_list_yields_no_confidence():
    merged = merge_evaluations([])
    assert merged.confidence is None
    assert merged.matched_rules == ()


def test_merge_evaluations_all_no_match_yields_no_confidence():
    a = evaluate_document("111", "222", "333", code_category_map=_TEST_MAP)
    b = evaluate_document("444", "555", "666", code_category_map=_TEST_MAP)
    merged = merge_evaluations([a, b])
    assert merged.confidence is None
