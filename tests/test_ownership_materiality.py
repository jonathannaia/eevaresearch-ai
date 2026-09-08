"""ownership_materiality — the Radar Calibration milestone's ownership-
change materiality gate. Pure functions, no I/O, no mocks needed.

The two "unchanged"/"trivial" fixtures below are the REAL excerpt text
cached from the live Korea DART radar pilot (Samsung Electronics,
rcept_no 20260724000625 and 20260721801260 — see the Milestone 6
calibration report) — not constructed. The threshold-crossing and
marker-exception fixtures are constructed, matching the same real
document shapes observed in those two excerpts, since no real cached
filing happens to cross the threshold or carry a material marker."""
from __future__ import annotations

import pytest

from src.data_access.dart.ownership_materiality import (
    MATERIAL_OWNERSHIP_MARKERS,
    OWNERSHIP_MATERIALITY_THRESHOLD_PP,
    assess_ownership_materiality,
    extract_ownership_delta_pp,
    find_material_marker,
)

_TITLE_DAERYANG = "주식등의대량보유상황보고서(일반)"
_TITLE_CHOEDAE = "최대주주등소유주식변동신고서"

# Real cached excerpt — unchanged ownership (19.69% -> 19.69%).
_REAL_UNCHANGED_EXCERPT = (
    "주식등의 대량보유상황보고서(일반) 6.1 삼성전자 1100 0001 주식등의 대량보유상황보고서 "
    "(일반서식 : 자본시장과 금융투자업에 관한 법률 제147조에 의한 보고 중 '경영권에 영향을 주기 위한 목적'의 경우) "
    "금융위원회 귀중 보고의무발생일 : 2026년 07월 20일 한국거래소 귀중 보고서작성기준일 : 2026년 07월 22일 "
    "보고자 : 삼성물산주식회사 요약정보 발행회사명 삼성전자주식회사 발행회사와의 관계 계열회사등 보고구분 변동ㆍ변경 "
    "보유주식등의 수 및 보유비율 보유주식등의 수 보유비율 직전 보고서 1,151,410,032 19.69 "
    "이번 보고서 1,151,375,445 19.69 주요계약체결 주식등의 수 및 비율 주식등의 수 비율 직전 보고서 49,359,000 0.84 "
    "이번 보고서 41,914,000 0.72"
)

# Real cached excerpt — trivial change (0.01 percentage points).
_REAL_TRIVIAL_EXCERPT = (
    "삼성전자/최대주주등소유주식변동신고서/(2026.07.21)최대주주등소유주식변동신고서 최대주주등소유주식변동신고서 "
    "1. 발행회사 정보 회사명 삼성전자주식회사 회사코드 005930 담당부서명 IR팀 담당자명 김성원 "
    "2. 발행주식수 정보 보통주식총수(1) 종류주식총수(2) 발행주식총수(1+2) 5,846,278,608 802,371,203 6,648,649,811 "
    "3. 보고의 개요 보고일자 소유주식 구분 주식수 비율 직전보고서제출일 2026-07-06 보통주식 1,151,293,808 19.69 "
    "종류주식 545,816 0.07 증권예탁증권 0 0.00 합계 1,151,839,624 17.32 "
    "이번보고서제출일 2026-07-21 보통주식 1,151,481,050 19.70 종류주식 549,492 0.07 증권예탁증권 0 0.00 합계 1,152,030,542 17.33 "
    "증감 보통주식 187,242 0.01 종류주식 3,676 0.00 증권예탁증권 0 0.00 합계 190,918 0.01"
)

# Real cached excerpt — a DECREASE (Samsung rcept_no 20260731801296),
# found via the milestone-7 one-candidate manual-processing validation.
# The '증감' section reports a signed delta ('-171,178', '-0.01') when
# the holding decreases — this excerpt is what exposed a real bug where
# the extractor's original digit-only pattern didn't match a leading
# '-' and silently returned None (falling through to the "ambiguous"
# path) instead of the real -0.01pp delta.
_REAL_DECREASE_EXCERPT = (
    "삼성전자/최대주주등소유주식변동신고서/(2026.07.31)최대주주등소유주식변동신고서 최대주주등소유주식변동신고서 "
    "1. 발행회사 정보 회사명 삼성전자주식회사 회사코드 005930 담당부서명 IR팀 담당자명 김성원 "
    "2. 발행주식수 정보 보통주식총수(1) 종류주식총수(2) 발행주식총수(1+2) 5,846,278,608 802,371,203 6,648,649,811 "
    "3. 보고의 개요 보고일자 소유주식 구분 주식수 비율 직전보고서제출일 2026-07-21 보통주식 1,151,481,050 19.70 "
    "종류주식 549,492 0.07 증권예탁증권 0 0.00 합계 1,152,030,542 17.33 "
    "이번보고서제출일 2026-07-31 보통주식 1,151,309,872 19.69 종류주식 532,993 0.07 증권예탁증권 0 0.00 합계 1,151,842,865 17.32 "
    "증감 보통주식 -171,178 -0.01 종류주식 -16,499 0.00 증권예탁증권 0 0.00 합계 -187,677 -0.01"
)

# Constructed fixture matching the same 대량보유상황보고서 shape, with a
# threshold-crossing delta (no real cached filing crosses the threshold).
_CONSTRUCTED_THRESHOLD_CROSSING_EXCERPT = (
    "주식등의 대량보유상황보고서(일반) 보유주식등의 수 및 보유비율 보유주식등의 수 보유비율 "
    "직전 보고서 1,000,000,000 10.00 이번 보고서 1,010,000,000 10.20"
)

# Constructed fixture — a controlling-shareholder-change marker present,
# with no parseable percentage figures anywhere.
_CONSTRUCTED_MARKER_ONLY_EXCERPT = "최대주주변경 관련 보고 상세 내용은 첨부 서류를 참조하시기 바랍니다."


def test_real_unchanged_ownership_extracts_zero_delta():
    delta = extract_ownership_delta_pp(_REAL_UNCHANGED_EXCERPT)
    assert delta == 0.0


def test_real_unchanged_ownership_does_not_become_material():
    result = assess_ownership_materiality(_TITLE_DAERYANG, _REAL_UNCHANGED_EXCERPT)
    assert result.outcome == "not_material"
    assert result.detail == "Not material · routine ownership update"


def test_real_trivial_change_extracts_small_delta():
    delta = extract_ownership_delta_pp(_REAL_TRIVIAL_EXCERPT)
    assert delta == 0.01
    assert delta < OWNERSHIP_MATERIALITY_THRESHOLD_PP


def test_real_trivial_change_does_not_become_material():
    result = assess_ownership_materiality(_TITLE_CHOEDAE, _REAL_TRIVIAL_EXCERPT)
    assert result.outcome == "not_material"


def test_real_decrease_extracts_correct_magnitude_not_none():
    # Regression for the bug found during the milestone-7 manual-processing
    # validation: a signed ('-171,178', '-0.01') delta must still be parsed,
    # not silently dropped to None.
    delta = extract_ownership_delta_pp(_REAL_DECREASE_EXCERPT)
    assert delta == pytest.approx(0.01)


def test_real_decrease_does_not_become_material():
    result = assess_ownership_materiality(_TITLE_CHOEDAE, _REAL_DECREASE_EXCERPT)
    assert result.outcome == "not_material"
    assert result.delta_percentage_points == pytest.approx(0.01)


def test_large_decrease_is_still_treated_as_material():
    # A large NEGATIVE delta must compare by magnitude, not raw sign —
    # this is the failure mode the milestone-7 bug could have caused for
    # a real divestiture (a large decrease silently read as "no delta").
    excerpt = (
        "3. 보고의 개요 보고일자 소유주식 구분 주식수 비율 직전보고서제출일 2026-07-21 "
        "보통주식 1,151,481,050 19.70 합계 1,152,030,542 17.33 "
        "이번보고서제출일 2026-07-31 보통주식 1,000,000,000 15.00 합계 1,000,500,000 12.00 "
        "증감 보통주식 -151,481,050 -4.70 합계 -151,530,542 -5.33"
    )
    result = assess_ownership_materiality(_TITLE_CHOEDAE, excerpt)
    assert result.outcome == "material_threshold"
    assert result.delta_percentage_points == pytest.approx(5.33)


def test_threshold_crossing_change_becomes_material():
    delta = extract_ownership_delta_pp(_CONSTRUCTED_THRESHOLD_CROSSING_EXCERPT)
    assert delta == pytest.approx(0.2)  # >= 0.05pp threshold

    result = assess_ownership_materiality(_TITLE_DAERYANG, _CONSTRUCTED_THRESHOLD_CROSSING_EXCERPT)
    assert result.outcome == "material_threshold"
    assert result.detail == f"Ownership change ≥ {OWNERSHIP_MATERIALITY_THRESHOLD_PP} percentage points"


def test_exactly_at_threshold_is_material():
    # Boundary: "0.05pp or greater" per the pilot's own rule wording.
    excerpt = (
        "보유주식등의 수 및 보유비율 보유주식등의 수 보유비율 "
        "직전 보고서 1,000,000,000 10.00 이번 보고서 1,000,500,000 10.05"
    )
    result = assess_ownership_materiality(_TITLE_DAERYANG, excerpt)
    assert result.outcome == "material_threshold"


def test_missing_excerpt_is_not_assumed_material():
    result = assess_ownership_materiality(_TITLE_DAERYANG, None)
    assert result.outcome == "not_material"
    assert result.delta_percentage_points is None


def test_unrecognized_excerpt_shape_is_not_assumed_material():
    result = assess_ownership_materiality(_TITLE_DAERYANG, "완전히 관련 없는 임의의 텍스트입니다.")
    assert result.outcome == "not_material"
    assert result.delta_percentage_points is None


def test_material_marker_promotes_regardless_of_missing_percentage():
    result = assess_ownership_materiality(_TITLE_CHOEDAE, _CONSTRUCTED_MARKER_ONLY_EXCERPT)
    assert result.outcome == "material_marker"
    assert result.matched_marker == "controlling_shareholder_change:최대주주변경"
    assert "Ownership change needs review" in result.detail


def test_material_marker_in_title_alone_is_detected():
    result = assess_ownership_materiality("공개매수신고서", excerpt=None)
    assert result.outcome == "material_marker"
    assert result.matched_marker == "tender_offer:공개매수"


def test_material_marker_overrides_a_small_extracted_delta():
    # Marker present AND the numeric delta would otherwise be sub-threshold —
    # marker still wins per the pilot's own "regardless of numeric threshold" rule.
    excerpt = (
        "질권설정 관련 공시 보유주식등의 수 및 보유비율 보유주식등의 수 보유비율 "
        "직전 보고서 1,000,000,000 10.00 이번 보고서 1,000,010,000 10.001"
    )
    result = assess_ownership_materiality(_TITLE_DAERYANG, excerpt)
    assert result.outcome == "material_marker"
    assert result.matched_marker == "pledge_or_collateral:질권설정"


def test_find_material_marker_returns_none_when_absent():
    assert find_material_marker("전혀 관련 없는 텍스트") is None


def test_all_configured_markers_are_documented_strings():
    # Sanity check on the lexicon shape itself.
    for category, terms in MATERIAL_OWNERSHIP_MARKERS.items():
        assert isinstance(category, str) and category
        assert terms and all(isinstance(t, str) and t for t in terms)
