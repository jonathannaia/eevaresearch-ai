"""equity_transaction_materiality — DART low-value filing suppression gate
(design/DART_LOW_VALUE_FILING_SUPPRESSION_DESIGN_2026_09_17.md). Pure
functions, no I/O, no mocks needed — same style as
tests/test_ownership_materiality.py, which this gate mirrors.

Regression fixture: Wonik IPS's real September 15/16, 2026 disclosure —
an employee-directed disposal of 51,456 treasury shares for KRW
6,143,846,400, with no operational/customer/capex/demand/technology/
earnings/supply-chain content. No real cached Wonik IPS filing exists
locally (see the design report's own §0) — the title below is the real,
standardized DART title shape already used by tests/test_dart_rules.py;
the excerpt is CONSTRUCTED to match the given facts, not claimed as a
real cached document (same "constructed, not real" labeling convention
test_ownership_materiality.py already uses for its own threshold-crossing
fixture)."""
from __future__ import annotations

import pytest

from src.data_access.dart.equity_transaction_materiality import (
    FOUNDER_OR_CONTROLLING_HOLDER_MARKERS,
    assess_equity_transaction_materiality,
    find_escape_hatch_marker,
)
from src.data_access.dart.ownership_materiality import OWNERSHIP_MATERIALITY_THRESHOLD_PP

_WONIK_IPS_TITLE = "주요사항보고서(자기주식처분결정)"

# Constructed to match the exact facts given for the Wonik IPS September
# 2026 filing — no real cached excerpt exists locally for this issuer.
_WONIK_IPS_EXCERPT_CONSTRUCTED = (
    "자기주식처분결정 1. 처분예정주식(주) 보통주식 51,456 2. 처분예정금액(원) 6,143,846,400 "
    "3. 처분목적 임직원 성과급 지급을 위한 자기주식 처분 4. 처분방법 시간외대량매매 "
    "5. 처분예정기간 2026년 09월 17일 ~ 2026년 09월 18일"
    # No named commercial counterparty, contract, capex, capacity,
    # order/backlog, guidance, financial result, governance/control
    # change, product, technology, or strategic-transaction language
    # anywhere in this excerpt.
)


def test_wonik_ips_style_employee_directed_disposal_is_not_material():
    result = assess_equity_transaction_materiality(_WONIK_IPS_TITLE, _WONIK_IPS_EXCERPT_CONSTRUCTED)
    assert result.outcome == "not_material"
    assert result.detail == "Not material · routine internal equity transaction"


def test_missing_excerpt_is_not_assumed_material():
    result = assess_equity_transaction_materiality(_WONIK_IPS_TITLE, None)
    assert result.outcome == "not_material"
    assert result.delta_percentage_points is None


def test_unrecognized_excerpt_shape_is_not_assumed_material():
    result = assess_equity_transaction_materiality(_WONIK_IPS_TITLE, "완전히 관련 없는 임의의 텍스트입니다.")
    assert result.outcome == "not_material"
    assert result.delta_percentage_points is None


# ============================================================
# Escape hatches
# ============================================================


def test_controlling_shareholder_change_marker_overrides_a_routine_disposal():
    excerpt = _WONIK_IPS_EXCERPT_CONSTRUCTED + " 최대주주변경을 수반하는 처분"
    result = assess_equity_transaction_materiality(_WONIK_IPS_TITLE, excerpt)
    assert result.outcome == "material_marker"
    assert result.matched_marker == "controlling_shareholder_change:최대주주변경"


def test_tender_offer_marker_in_title_alone_is_detected():
    result = assess_equity_transaction_materiality("공개매수신고서 및 자기주식처분결정", excerpt=None)
    assert result.outcome == "material_marker"
    assert result.matched_marker == "tender_offer:공개매수"


def test_merger_or_restructuring_marker_overrides_a_routine_disposal():
    excerpt = _WONIK_IPS_EXCERPT_CONSTRUCTED + " 합병에 따른 자기주식 처분"
    result = assess_equity_transaction_materiality(_WONIK_IPS_TITLE, excerpt)
    assert result.outcome == "material_marker"
    assert result.matched_marker == "compulsory_acquisition_or_merger:합병"


def test_founder_or_controlling_holder_marker_overrides_a_routine_disposal():
    excerpt = _WONIK_IPS_EXCERPT_CONSTRUCTED + " 최대주주 홍길동으로부터 처분"
    result = assess_equity_transaction_materiality(_WONIK_IPS_TITLE, excerpt)
    assert result.outcome == "material_marker"
    assert result.matched_marker == "founder_or_controlling_holder_involvement:최대주주"


def test_ceo_marker_in_title_is_detected():
    result = assess_equity_transaction_materiality("대표이사 관련 자기주식처분결정", excerpt=None)
    assert result.outcome == "material_marker"
    assert result.matched_marker == "founder_or_controlling_holder_involvement:대표이사"


def test_free_float_threshold_crossing_change_becomes_material():
    excerpt = (
        "자기주식처분결정 보유주식등의 수 및 보유비율 보유주식등의 수 보유비율 "
        "직전 보고서 1,000,000,000 10.00 이번 보고서 1,010,000,000 10.20"
    )
    result = assess_equity_transaction_materiality(_WONIK_IPS_TITLE, excerpt)
    assert result.outcome == "material_threshold"
    assert result.delta_percentage_points == pytest.approx(0.2)
    assert result.delta_percentage_points >= OWNERSHIP_MATERIALITY_THRESHOLD_PP


def test_find_escape_hatch_marker_returns_none_when_absent():
    assert find_escape_hatch_marker(_WONIK_IPS_EXCERPT_CONSTRUCTED) is None


def test_all_founder_or_controlling_holder_markers_are_documented_strings():
    for category, terms in FOUNDER_OR_CONTROLLING_HOLDER_MARKERS.items():
        assert isinstance(category, str) and category
        assert terms and all(isinstance(t, str) and t for t in terms)
