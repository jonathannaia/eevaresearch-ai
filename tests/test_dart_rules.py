"""dart_rules — deterministic keyword matching, routine-filing exclusion,
confidence tiers, and the amendment marker. Pure functions, no I/O, no
network. report_nm strings below are real standardized Korean disclosure-
category terminology (the same fixed vocabulary DART filers use), not
fabricated company facts."""
from src.data_access.dart.dart_rules import (
    AMENDMENT_MARKER,
    ROUTINE_EXCLUDE_PATTERNS,
    evaluate_report_name,
)


def test_routine_insider_ownership_filing_is_excluded_not_promoted():
    result = evaluate_report_name("임원ㆍ주요주주특정증권등소유상황보고서")
    assert result.matched_rules == ()
    assert result.confidence is None


def test_irrelevant_title_produces_no_match():
    result = evaluate_report_name("기업설명회(IR)개최(안내공시)")
    assert result.matched_rules == ()
    assert result.confidence is None


def test_single_category_match_is_moderate_confidence():
    result = evaluate_report_name("신규시설투자등")
    assert result.confidence == "Moderate"
    assert any("capex_or_facility_investment" in r for r in result.matched_rules)


def test_earnings_keyword_matches():
    result = evaluate_report_name("연결재무제표기준영업(잠정)실적(공정공시)")
    assert result.confidence == "Moderate"
    assert any("earnings" in r for r in result.matched_rules)


def test_financing_keyword_matches_treasury_stock_disposal():
    result = evaluate_report_name("주요사항보고서(자기주식처분결정)")
    assert result.confidence == "Moderate"
    assert any("financing" in r for r in result.matched_rules)


def test_risk_disclosure_keyword_matches_serious_accident():
    result = evaluate_report_name("중대재해발생")
    assert result.confidence == "Moderate"
    assert any("risk_disclosure" in r for r in result.matched_rules)


def test_market_rumor_inquiry_matches():
    result = evaluate_report_name("조회공시요구(풍문또는보도)에대한답변(미확정)")
    # Contains both "조회공시요구" and "풍문또는보도" from the same
    # category — still counts as one matched category, so Moderate.
    assert result.confidence == "Moderate"
    assert any("market_rumor_response" in r for r in result.matched_rules)


def test_amendment_marker_alone_does_not_reach_any_confidence():
    result = evaluate_report_name(f"{AMENDMENT_MARKER}어떤보고서")
    assert result.confidence is None
    assert "amendment_or_correction" in result.matched_rules


def test_amendment_marker_plus_category_match_still_counts_as_one_category():
    result = evaluate_report_name(f"{AMENDMENT_MARKER}주요사항보고서(유상증자결정)")
    assert result.confidence == "Moderate"
    assert "amendment_or_correction" in result.matched_rules
    assert any("financing" in r for r in result.matched_rules)


def test_two_independent_category_matches_reach_high_confidence():
    # Genuinely contrived combined title to exercise the "2+ categories"
    # path deterministically, rather than relying on finding a real
    # two-category filing title.
    result = evaluate_report_name("신규시설투자등 및 유상증자결정")
    assert result.confidence == "High"


def test_routine_exclude_wins_even_if_a_category_keyword_also_appears():
    # A routine pattern anywhere in the title suppresses promotion,
    # regardless of any category keyword also present.
    result = evaluate_report_name("임원ㆍ주요주주특정증권등소유상황보고서 실적 관련")
    assert result.confidence is None
    assert result.matched_rules == ()


def test_every_routine_exclude_pattern_is_non_empty_string():
    assert all(isinstance(p, str) and p for p in ROUTINE_EXCLUDE_PATTERNS)


def test_evaluate_is_deterministic_for_the_same_input():
    a = evaluate_report_name("신규시설투자등")
    b = evaluate_report_name("신규시설투자등")
    assert a == b
