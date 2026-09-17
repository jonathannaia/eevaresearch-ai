"""low_value_filing_rules — the shared, centralized scoping predicates the
DART low-value filing suppression design (design/DART_LOW_VALUE_FILING_
SUPPRESSION_DESIGN_2026_09_17.md) uses at every surface. Pure functions,
no I/O.

Regression fixture: Wonik IPS's real September 2026 disclosure — the
title shape below ("주요사항보고서(자기주식처분결정)") is the real,
standardized DART title already used by tests/test_dart_rules.py's own
fixture."""
from __future__ import annotations

from src.data_access.dart.low_value_filing_rules import (
    is_low_value_filing_title,
    matched_rules_are_low_value_only,
)

_WONIK_IPS_TITLE = "주요사항보고서(자기주식처분결정)"


# ============================================================
# matched_rules_are_low_value_only
# ============================================================


def test_bare_treasury_activity_match_is_low_value_only():
    assert matched_rules_are_low_value_only(["treasury_stock_activity:treasury_stock_disposal_or_acquisition:자기주식처분"]) is True


def test_amendment_marker_alongside_treasury_activity_is_still_low_value_only():
    assert matched_rules_are_low_value_only([
        "treasury_stock_activity:treasury_stock_disposal_or_acquisition:자기주식처분", "amendment_or_correction",
    ]) is True


def test_a_second_real_category_disqualifies_low_value_only():
    assert matched_rules_are_low_value_only([
        "treasury_stock_activity:treasury_stock_disposal_or_acquisition:자기주식처분",
        "capex_or_facility_investment:facility_investment:신규시설투자",
    ]) is False


def test_empty_matched_rules_is_never_low_value_only():
    assert matched_rules_are_low_value_only([]) is False


def test_amendment_marker_alone_is_never_low_value_only():
    assert matched_rules_are_low_value_only(["amendment_or_correction"]) is False


def test_ownership_change_alone_is_not_the_low_value_category():
    assert matched_rules_are_low_value_only(["ownership_change:major_shareholder_change:대량보유상황보고서"]) is False


# ============================================================
# is_low_value_filing_title
# ============================================================


def test_wonik_ips_style_title_is_low_value():
    assert is_low_value_filing_title(_WONIK_IPS_TITLE) is True


def test_treasury_acquisition_title_is_low_value():
    assert is_low_value_filing_title("주요사항보고서(자기주식취득결정)") is True


def test_title_with_a_control_change_marker_is_not_low_value():
    assert is_low_value_filing_title(_WONIK_IPS_TITLE + "(최대주주변경)") is False


def test_title_with_a_founder_marker_is_not_low_value():
    assert is_low_value_filing_title("대표이사 관련 " + _WONIK_IPS_TITLE) is False


def test_title_combined_with_another_real_category_is_not_low_value():
    assert is_low_value_filing_title("자기주식처분결정 및 신규시설투자등") is False


def test_genuine_capital_raise_title_is_not_low_value():
    assert is_low_value_filing_title("주요사항보고서(유상증자결정)") is False


def test_unrecognized_title_is_not_assumed_low_value():
    assert is_low_value_filing_title("전혀 관련 없는 임의의 공시 제목입니다") is False


def test_blank_title_is_not_low_value():
    assert is_low_value_filing_title("") is False
