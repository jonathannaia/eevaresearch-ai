"""Radar evidence-packet foundation, Phase 3, Step 1 (design/DECISIONS.md)
— pure prior-candidate selection and matched-rule-category comparison.
Every fixture below is synthetic and local; no source is fetched, no
JSON/SQLite/Postgres/network I/O occurs anywhere in this file."""
from __future__ import annotations

import ast
import copy
import dataclasses
import subprocess
from pathlib import Path

import pytest

from src.logic.prior_disclosure_comparison import (
    COMPARISON_BASIS,
    ComparisonResult,
    ComparisonStatus,
    RuleSetDiff,
    build_comparison_result,
    compare_matched_rules,
    select_prior_candidate,
)
from src.models.models import (
    CandidateSignal,
    CandidateStatus,
    ExtractionState,
    FilingEvent,
    Translation,
)

_COMPUTED_AT = "2026-08-20T00:00:00+00:00"


def _edgar_filing(rcept_no: str, **overrides) -> FilingEvent:
    defaults = dict(
        rcept_no=rcept_no, corp_code="0000320193", corp_name="Apple Inc.", stock_code="AAPL",
        report_nm="8-K filing", rcept_dt="2026-08-01", flr_nm="Apple Inc.", pblntf_ty="8-K",
        source_name="SEC EDGAR", original_language="English",
    )
    defaults.update(overrides)
    return FilingEvent(**defaults)


def _dart_filing(rcept_no: str, **overrides) -> FilingEvent:
    defaults = dict(
        rcept_no=rcept_no, corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm="실적발표", rcept_dt="20260801", flr_nm="삼성전자", source_name="OpenDART / DART",
    )
    defaults.update(overrides)
    return FilingEvent(**defaults)


def _edinet_filing(rcept_no: str, **overrides) -> FilingEvent:
    defaults = dict(
        rcept_no=rcept_no, corp_code="E02778", corp_name="SoftBank Group", stock_code="9984",
        report_nm="有価証券報告書", rcept_dt="2026-08-01", flr_nm="SoftBank Group", source_name="EDINET",
        original_language="Japanese", ordinance_code="010", pblntf_ty="030000", pblntf_detail_ty="120",
    )
    defaults.update(overrides)
    return FilingEvent(**defaults)


def _candidate(candidate_id: str, filing: FilingEvent, matched_rules: list[str], **overrides) -> CandidateSignal:
    defaults = dict(
        id=candidate_id, filing=filing, matched_rules=matched_rules, confidence="Moderate",
        status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="Default synthetic excerpt text.",
    )
    defaults.update(overrides)
    return CandidateSignal(**defaults)


# ============================================================
# Part A — NOT_AVAILABLE (test 1, 6, 7, 8)
# ============================================================


def test_no_prior_candidates_returns_not_available():
    current = _candidate("cur-1", _edgar_filing("acc-1", rcept_dt="2026-08-10"), ["financing_or_debt:8-K item 2.03"])
    result = build_comparison_result(current, [], computed_at=_COMPUTED_AT)
    assert result.comparison_status == ComparisonStatus.NOT_AVAILABLE.value
    assert result.prior_document_id is None
    assert result.prior_filed_at is None
    assert result.added_categories == ()
    assert result.removed_categories == ()
    assert result.prior_excerpt is None
    assert result.current_excerpt is None
    assert result.limitations == ()
    assert result.comparison_basis == COMPARISON_BASIS
    assert result.computed_at == _COMPUTED_AT


def test_prior_missing_extraction_state_or_excerpt_does_not_qualify():
    current = _candidate("cur-1", _edgar_filing("acc-3", rcept_dt="2026-08-10"), ["financing_or_debt:8-K item 2.03"])
    prior_no_excerpt = _candidate(
        "prior-no-excerpt", _edgar_filing("acc-1", rcept_dt="2026-07-01"),
        ["financing_or_debt:8-K item 2.03"], excerpt_original=None,
    )
    prior_not_extracted = _candidate(
        "prior-not-extracted", _edgar_filing("acc-2", rcept_dt="2026-07-02"),
        ["financing_or_debt:8-K item 2.03"], extraction_state=ExtractionState.PENDING,
    )
    result = build_comparison_result(current, [prior_no_excerpt, prior_not_extracted], computed_at=_COMPUTED_AT)
    assert result.comparison_status == ComparisonStatus.NOT_AVAILABLE.value


def test_same_or_later_timestamp_prior_is_excluded():
    current = _candidate("cur-1", _edgar_filing("acc-1", rcept_dt="2026-08-10"), ["financing_or_debt:8-K item 2.03"])
    same_time = _candidate("prior-same", _edgar_filing("acc-2", rcept_dt="2026-08-10"), ["financing_or_debt:8-K item 2.03"])
    later = _candidate("prior-later", _edgar_filing("acc-3", rcept_dt="2026-08-11"), ["financing_or_debt:8-K item 2.03"])
    result = build_comparison_result(current, [same_time, later], computed_at=_COMPUTED_AT)
    assert result.comparison_status == ComparisonStatus.NOT_AVAILABLE.value


def test_self_included_in_prior_pool_is_excluded():
    current = _candidate("cur-1", _edgar_filing("acc-1", rcept_dt="2026-08-10"), ["financing_or_debt:8-K item 2.03"])
    result = build_comparison_result(current, [current], computed_at=_COMPUTED_AT)
    assert result.comparison_status == ComparisonStatus.NOT_AVAILABLE.value


def test_same_document_identity_is_excluded_even_with_an_earlier_timestamp():
    current = _candidate("cur-1", _edgar_filing("acc-1", rcept_dt="2026-08-10"), ["financing_or_debt:8-K item 2.03"])
    # Same (source_name, corp_code, rcept_no) identity as `current`, but a
    # different candidate id and an earlier rcept_dt — proving identity
    # exclusion is independent of, and checked before, the time gate.
    same_identity_earlier = _candidate(
        "different-id", _edgar_filing("acc-1", rcept_dt="2026-08-01"), ["financing_or_debt:8-K item 2.03"],
    )
    result = build_comparison_result(current, [same_identity_earlier], computed_at=_COMPUTED_AT)
    assert result.comparison_status == ComparisonStatus.NOT_AVAILABLE.value


# ============================================================
# Part B — NOT_COMPARABLE: issuer/source/family mismatch (test 2, 3, 4, 5)
# ============================================================


def test_family_mismatch_with_earlier_same_issuer_candidate_returns_not_comparable():
    prior = _candidate("prior-1", _edgar_filing("acc-1", pblntf_ty="10-Q", rcept_dt="2026-07-01"), ["earnings_or_results:10-Q"])
    current = _candidate("cur-1", _edgar_filing("acc-2", pblntf_ty="8-K", rcept_dt="2026-08-10"), ["financing_or_debt:8-K item 2.03"])
    result = build_comparison_result(current, [prior], computed_at=_COMPUTED_AT)
    assert result.comparison_status == ComparisonStatus.NOT_COMPARABLE.value
    assert result.prior_document_id is None


def test_issuer_mismatch_is_excluded_even_when_an_earlier_same_issuer_candidate_exists():
    same_issuer_prior = _candidate(
        "prior-same-issuer", _edgar_filing("acc-1", corp_code="0000320193", pblntf_ty="10-Q", rcept_dt="2026-07-01"),
        ["earnings_or_results:10-Q"],
    )
    # Different issuer, but otherwise earlier AND family-matching — must
    # never be selected regardless of how well it would "fit" family-wise.
    different_issuer_prior = _candidate(
        "prior-diff-issuer", _edgar_filing("acc-2", corp_code="0000000001", pblntf_ty="8-K", rcept_dt="2026-07-05"),
        ["financing_or_debt:8-K item 2.03"],
    )
    current = _candidate("cur-1", _edgar_filing("acc-3", corp_code="0000320193", pblntf_ty="8-K", rcept_dt="2026-08-10"), ["financing_or_debt:8-K item 2.03"])
    result = build_comparison_result(current, [same_issuer_prior, different_issuer_prior], computed_at=_COMPUTED_AT)
    assert result.comparison_status == ComparisonStatus.NOT_COMPARABLE.value
    assert result.prior_document_id is None


def test_source_mismatch_is_excluded_even_when_an_earlier_same_source_issuer_candidate_exists():
    same_source_prior = _candidate(
        "prior-same-source", _edgar_filing("acc-1", pblntf_ty="10-Q", rcept_dt="2026-07-01"), ["earnings_or_results:10-Q"],
    )
    # Same corp_code string, but a DART filing — must never be treated as
    # comparable just because the identifier string happens to match.
    different_source_prior = _candidate(
        "prior-diff-source", _dart_filing("R-1", corp_code="0000320193", rcept_dt="20260705"),
        ["financing:capital_increase:유상증자"],
    )
    current = _candidate("cur-1", _edgar_filing("acc-2", pblntf_ty="8-K", rcept_dt="2026-08-10"), ["financing_or_debt:8-K item 2.03"])
    result = build_comparison_result(current, [same_source_prior, different_source_prior], computed_at=_COMPUTED_AT)
    assert result.comparison_status == ComparisonStatus.NOT_COMPARABLE.value
    assert result.prior_document_id is None


def test_dart_form_family_mismatch_returns_not_comparable():
    prior = _candidate("prior-1", _dart_filing("R-1", rcept_dt="20260701"), ["risk_or_incident:risk_disclosure:중대재해발생"])
    current = _candidate("cur-1", _dart_filing("R-2", rcept_dt="20260810"), ["financing:capital_increase:유상증자"])
    result = build_comparison_result(current, [prior], computed_at=_COMPUTED_AT)
    assert result.comparison_status == ComparisonStatus.NOT_COMPARABLE.value


# ============================================================
# Part C — selection tie-breaking (test 9, 10)
# ============================================================


def test_multiple_eligible_priors_selects_most_recent_strictly_earlier():
    older = _candidate("prior-older", _edgar_filing("acc-1", pblntf_ty="8-K", rcept_dt="2026-07-01"), ["financing_or_debt:8-K item 2.03"])
    newer = _candidate("prior-newer", _edgar_filing("acc-2", pblntf_ty="8-K", rcept_dt="2026-07-20"), ["financing_or_debt:8-K item 2.03"])
    current = _candidate("cur-1", _edgar_filing("acc-3", pblntf_ty="8-K", rcept_dt="2026-08-10"), ["financing_or_debt:8-K item 2.03"])
    result = build_comparison_result(current, [older, newer], computed_at=_COMPUTED_AT)
    assert result.prior_document_id == "acc-2"


def test_equal_prior_timestamps_returns_not_comparable():
    tie_a = _candidate("prior-a", _edgar_filing("acc-1", pblntf_ty="8-K", rcept_dt="2026-07-20"), ["financing_or_debt:8-K item 2.03"])
    tie_b = _candidate("prior-b", _edgar_filing("acc-2", pblntf_ty="8-K", rcept_dt="2026-07-20"), ["financing_or_debt:8-K item 2.03"])
    current = _candidate("cur-1", _edgar_filing("acc-3", pblntf_ty="8-K", rcept_dt="2026-08-10"), ["financing_or_debt:8-K item 2.03"])
    result = build_comparison_result(current, [tie_a, tie_b], computed_at=_COMPUTED_AT)
    assert result.comparison_status == ComparisonStatus.NOT_COMPARABLE.value
    assert result.prior_document_id is None


# ============================================================
# Part D — EDGAR family gate (test 11, 12)
# ============================================================


def test_edgar_8k_requires_overlapping_item_codes():
    prior = _candidate("prior-1", _edgar_filing("acc-1", pblntf_ty="8-K", rcept_dt="2026-07-01"), ["governance_or_management_change:8-K item 5.02"])
    current = _candidate("cur-1", _edgar_filing("acc-2", pblntf_ty="8-K", rcept_dt="2026-08-10"), ["financing_or_debt:8-K item 2.03"])
    result = build_comparison_result(current, [prior], computed_at=_COMPUTED_AT)
    assert result.comparison_status == ComparisonStatus.NOT_COMPARABLE.value


def test_edgar_8k_with_overlapping_item_code_is_comparable_and_detects_added_category():
    prior = _candidate("prior-1", _edgar_filing("acc-1", pblntf_ty="8-K", rcept_dt="2026-07-01"), ["financing_or_debt:8-K item 2.03"])
    current = _candidate(
        "cur-1", _edgar_filing("acc-2", pblntf_ty="8-K", rcept_dt="2026-08-10"),
        ["financing_or_debt:8-K item 2.03", "governance_or_management_change:8-K item 5.02"],
    )
    result = build_comparison_result(current, [prior], computed_at=_COMPUTED_AT)
    assert result.comparison_status == ComparisonStatus.CHANGE_DETECTED.value
    assert result.added_categories == ("governance_or_management_change",)
    assert result.removed_categories == ()
    assert result.prior_document_id == "acc-1"


def test_edgar_8k_and_10q_are_never_comparable():
    prior = _candidate("prior-1", _edgar_filing("acc-1", pblntf_ty="10-Q", rcept_dt="2026-07-01"), ["earnings_or_results:10-Q"])
    current = _candidate("cur-1", _edgar_filing("acc-2", pblntf_ty="8-K", rcept_dt="2026-08-10"), ["financing_or_debt:8-K item 2.03"])
    result = build_comparison_result(current, [prior], computed_at=_COMPUTED_AT)
    assert result.comparison_status == ComparisonStatus.NOT_COMPARABLE.value


def test_edgar_form_alias_normalization_treats_spelled_out_and_abbreviated_as_the_same_family():
    prior = _candidate("prior-1", _edgar_filing("acc-1", pblntf_ty="SCHEDULE 13G", rcept_dt="2026-07-01"), ["ownership_change:SC 13G"])
    current = _candidate("cur-1", _edgar_filing("acc-2", pblntf_ty="SC 13G", rcept_dt="2026-08-10"), ["ownership_change:SC 13G"])
    result = build_comparison_result(current, [prior], computed_at=_COMPUTED_AT)
    assert result.comparison_status == ComparisonStatus.NO_MATERIAL_CHANGE.value


# ============================================================
# Part E — DART family gate (test 13)
# ============================================================


def test_dart_requires_exact_matched_rule_category():
    prior = _candidate("prior-1", _dart_filing("R-1", rcept_dt="20260701"), ["risk_or_incident:risk_disclosure:중대재해발생"])
    current = _candidate("cur-1", _dart_filing("R-2", rcept_dt="20260810"), ["financing:capital_increase:유상증자"])
    result = build_comparison_result(current, [prior], computed_at=_COMPUTED_AT)
    assert result.comparison_status == ComparisonStatus.NOT_COMPARABLE.value


def test_dart_same_category_is_comparable():
    prior = _candidate("prior-1", _dart_filing("R-1", rcept_dt="20260701"), ["financing:capital_increase:유상증자"])
    current = _candidate("cur-1", _dart_filing("R-2", rcept_dt="20260810"), ["financing:capital_increase:유상증자"])
    result = build_comparison_result(current, [prior], computed_at=_COMPUTED_AT)
    assert result.comparison_status == ComparisonStatus.NO_MATERIAL_CHANGE.value


def test_dart_candidate_with_only_amendment_marker_and_no_category_is_not_comparable():
    # matched_rules containing only the bare amendment marker (no
    # category-prefixed rule) cannot truthfully establish a DART family —
    # must fail closed to NOT_COMPARABLE, never guess a category.
    prior = _candidate("prior-1", _dart_filing("R-1", rcept_dt="20260701"), ["amendment_or_correction"])
    current = _candidate("cur-1", _dart_filing("R-2", rcept_dt="20260810"), ["amendment_or_correction"])
    result = build_comparison_result(current, [prior], computed_at=_COMPUTED_AT)
    assert result.comparison_status == ComparisonStatus.NOT_COMPARABLE.value


# ============================================================
# Part F — EDINET family gate (test 14, 15)
# ============================================================


def test_edinet_requires_full_routing_triple_match():
    prior = _candidate(
        "prior-1", _edinet_filing("S100AAAA", ordinance_code="010", pblntf_ty="030000", pblntf_detail_ty="120", rcept_dt="2026-07-01"),
        ["annual_securities_report:010:030000:120"],
    )
    current = _candidate(
        "cur-1", _edinet_filing("S100BBBB", ordinance_code="010", pblntf_ty="030000", pblntf_detail_ty="120", rcept_dt="2026-08-10"),
        ["annual_securities_report:010:030000:120"],
    )
    result = build_comparison_result(current, [prior], computed_at=_COMPUTED_AT)
    assert result.comparison_status == ComparisonStatus.NO_MATERIAL_CHANGE.value


def test_edinet_routing_triple_mismatch_returns_not_comparable():
    prior = _candidate(
        "prior-1", _edinet_filing("S100AAAA", ordinance_code="010", pblntf_ty="030000", pblntf_detail_ty="999", rcept_dt="2026-07-01"),
        ["other:010:030000:999"],
    )
    current = _candidate(
        "cur-1", _edinet_filing("S100BBBB", ordinance_code="010", pblntf_ty="030000", pblntf_detail_ty="120", rcept_dt="2026-08-10"),
        ["annual_securities_report:010:030000:120"],
    )
    result = build_comparison_result(current, [prior], computed_at=_COMPUTED_AT)
    assert result.comparison_status == ComparisonStatus.NOT_COMPARABLE.value


def test_edinet_missing_routing_triple_returns_not_comparable():
    prior = _candidate(
        "prior-1", _edinet_filing("S100AAAA", ordinance_code="", pblntf_ty="030000", pblntf_detail_ty="120", rcept_dt="2026-07-01"),
        ["annual_securities_report:010:030000:120"],
    )
    current = _candidate(
        "cur-1", _edinet_filing("S100BBBB", ordinance_code="010", pblntf_ty="030000", pblntf_detail_ty="120", rcept_dt="2026-08-10"),
        ["annual_securities_report:010:030000:120"],
    )
    result = build_comparison_result(current, [prior], computed_at=_COMPUTED_AT)
    assert result.comparison_status == ComparisonStatus.NOT_COMPARABLE.value


# ============================================================
# Part G — rule-set diff (test 16, 17, 18, 19)
# ============================================================


def test_identical_categories_yield_no_diff():
    diff = compare_matched_rules(["financing_or_debt:8-K item 2.03"], ["financing_or_debt:8-K item 2.03"])
    assert diff == RuleSetDiff(added=(), removed=())


def test_added_category_yields_deterministic_added_categories():
    diff = compare_matched_rules(
        ["financing_or_debt:8-K item 2.03", "governance_or_management_change:8-K item 5.02"],
        ["financing_or_debt:8-K item 2.03"],
    )
    assert diff.added == ("governance_or_management_change",)
    assert diff.removed == ()


def test_removed_category_yields_deterministic_removed_categories():
    diff = compare_matched_rules(
        ["financing_or_debt:8-K item 2.03"],
        ["financing_or_debt:8-K item 2.03", "governance_or_management_change:8-K item 5.02"],
    )
    assert diff.removed == ("governance_or_management_change",)
    assert diff.added == ()


def test_duplicate_unordered_mixed_case_tokens_normalize_without_false_changes():
    diff = compare_matched_rules(
        ["Financing_or_debt:8-K item 2.03", "financing_or_debt:8-K item 2.03", "GOVERNANCE_OR_MANAGEMENT_CHANGE:8-K item 5.02"],
        ["governance_or_management_change:8-K item 5.02", "FINANCING_OR_DEBT:8-K item 2.03"],
    )
    assert diff == RuleSetDiff(added=(), removed=())


def test_added_and_removed_categories_are_sorted_deterministically():
    diff = compare_matched_rules(
        ["zeta_category:x", "alpha_category:x"],
        ["beta_category:x", "gamma_category:x"],
    )
    assert diff.added == ("alpha_category", "zeta_category")
    assert diff.removed == ("beta_category", "gamma_category")


# ============================================================
# Part H — translation/boilerplate/language safety (test 20, 21, 22)
# ============================================================


def test_translation_differences_never_affect_comparison_result():
    prior = _candidate(
        "prior-1", _dart_filing("R-1", rcept_dt="20260701"), ["financing:capital_increase:유상증자"],
        excerpt_translation=Translation(translated_text="Old translation text.", provider="DeepL", source_lang="ko", target_lang="en", translated_at=_COMPUTED_AT),
    )
    current = _candidate(
        "cur-1", _dart_filing("R-2", rcept_dt="20260810"), ["financing:capital_increase:유상증자"],
        excerpt_translation=Translation(translated_text="Completely different new translation text.", provider="DeepL", source_lang="ko", target_lang="en", translated_at=_COMPUTED_AT),
    )
    result = build_comparison_result(current, [prior], computed_at=_COMPUTED_AT)
    assert result.comparison_status == ComparisonStatus.NO_MATERIAL_CHANGE.value


def test_boilerplate_only_excerpt_difference_with_identical_rules_is_no_material_change():
    prior = _candidate(
        "prior-1", _dart_filing("R-1", rcept_dt="20260701"), ["financing:capital_increase:유상증자"],
        excerpt_original="회사명: 삼성전자 대표이사: 홍길동 본점소재지: 서울",
    )
    current = _candidate(
        "cur-1", _dart_filing("R-2", rcept_dt="20260810"), ["financing:capital_increase:유상증자"],
        excerpt_original="This excerpt text is entirely different boilerplate wording, but the rule categories match.",
    )
    result = build_comparison_result(current, [prior], computed_at=_COMPUTED_AT)
    assert result.comparison_status == ComparisonStatus.NO_MATERIAL_CHANGE.value


def test_original_language_excerpts_are_preserved_verbatim_never_translated():
    prior_excerpt = "有価証券報告書の本文抜粋。"
    current_excerpt = "本文の別の抜粋、内容は異なる。"
    prior = _candidate(
        "prior-1", _edinet_filing("S100AAAA", rcept_dt="2026-07-01"), ["annual_securities_report:010:030000:120"],
        excerpt_original=prior_excerpt,
    )
    current = _candidate(
        "cur-1", _edinet_filing("S100BBBB", rcept_dt="2026-08-10"), ["annual_securities_report:010:030000:120"],
        excerpt_original=current_excerpt,
    )
    result = build_comparison_result(current, [prior], computed_at=_COMPUTED_AT)
    assert result.prior_excerpt == prior_excerpt
    assert result.current_excerpt == current_excerpt


# ============================================================
# Part I — limitations (test 23, 24)
# ============================================================


@pytest.mark.parametrize("current_rules,prior_rules,expected_status", [
    (["financing_or_debt:8-K item 2.03"], ["financing_or_debt:8-K item 2.03"], ComparisonStatus.NO_MATERIAL_CHANGE.value),
    (["financing_or_debt:8-K item 2.03", "governance_or_management_change:8-K item 5.02"], ["financing_or_debt:8-K item 2.03"], ComparisonStatus.CHANGE_DETECTED.value),
])
def test_successful_comparison_always_includes_period_limitation(current_rules, prior_rules, expected_status):
    prior = _candidate("prior-1", _edgar_filing("acc-1", pblntf_ty="8-K", rcept_dt="2026-07-01"), prior_rules)
    current = _candidate("cur-1", _edgar_filing("acc-2", pblntf_ty="8-K", rcept_dt="2026-08-10"), current_rules)
    result = build_comparison_result(current, [prior], computed_at=_COMPUTED_AT)
    assert result.comparison_status == expected_status
    assert "Comparable reporting period is not available in current metadata." in result.limitations


def test_not_available_and_not_comparable_results_carry_no_period_limitation():
    current = _candidate("cur-1", _edgar_filing("acc-1", rcept_dt="2026-08-10"), ["financing_or_debt:8-K item 2.03"])
    not_available = build_comparison_result(current, [], computed_at=_COMPUTED_AT)
    assert not_available.limitations == ()

    mismatched_prior = _candidate("prior-1", _edgar_filing("acc-2", pblntf_ty="10-Q", rcept_dt="2026-07-01"), ["earnings_or_results:10-Q"])
    not_comparable = build_comparison_result(current, [mismatched_prior], computed_at=_COMPUTED_AT)
    assert not_comparable.limitations == ()


def test_dart_amendment_marker_surfaced_as_limitation_not_structural_link():
    prior = _candidate("prior-1", _dart_filing("R-1", rcept_dt="20260701"), ["financing:capital_increase:유상증자", "amendment_or_correction"])
    current = _candidate("cur-1", _dart_filing("R-2", rcept_dt="20260810"), ["financing:capital_increase:유상증자"])
    result = build_comparison_result(current, [prior], computed_at=_COMPUTED_AT)
    # The amendment marker never becomes a category diff (excluded from
    # _rule_categories) and never claims to know WHICH filing it amends —
    # only surfaced as an explicit, honest limitation string.
    assert result.comparison_status == ComparisonStatus.NO_MATERIAL_CHANGE.value
    assert any("기재정정" in lim for lim in result.limitations)


def test_edgar_amendment_suffix_surfaced_as_limitation_not_structural_link():
    prior = _candidate("prior-1", _edgar_filing("acc-1", pblntf_ty="SC 13D/A", rcept_dt="2026-07-01"), ["ownership_change:SC 13D/A"])
    current = _candidate("cur-1", _edgar_filing("acc-2", pblntf_ty="SC 13D/A", rcept_dt="2026-08-10"), ["ownership_change:SC 13D/A"])
    result = build_comparison_result(current, [prior], computed_at=_COMPUTED_AT)
    assert result.comparison_status == ComparisonStatus.NO_MATERIAL_CHANGE.value
    assert any("/A" in lim for lim in result.limitations)


def test_edinet_never_fabricates_an_amendment_limitation():
    # EDINET has no existing amendment marker in this codebase at all —
    # confirmed by the Phase 3 audit. No limitation should ever be
    # invented for it.
    prior = _candidate("prior-1", _edinet_filing("S100AAAA", rcept_dt="2026-07-01"), ["annual_securities_report:010:030000:120"])
    current = _candidate("cur-1", _edinet_filing("S100BBBB", rcept_dt="2026-08-10"), ["annual_securities_report:010:030000:120"])
    result = build_comparison_result(current, [prior], computed_at=_COMPUTED_AT)
    assert not any("amendment" in lim.lower() or "correction" in lim.lower() for lim in result.limitations)


def test_missing_or_empty_current_excerpt_is_flagged_as_a_limitation():
    prior = _candidate("prior-1", _edgar_filing("acc-1", pblntf_ty="8-K", rcept_dt="2026-07-01"), ["financing_or_debt:8-K item 2.03"])
    current = _candidate("cur-1", _edgar_filing("acc-2", pblntf_ty="8-K", rcept_dt="2026-08-10"), ["financing_or_debt:8-K item 2.03"], excerpt_original=None)
    result = build_comparison_result(current, [prior], computed_at=_COMPUTED_AT)
    assert any("Current excerpt is missing or empty." == lim for lim in result.limitations)


def test_truncation_bound_excerpt_is_flagged_as_a_limitation():
    prior = _candidate("prior-1", _edgar_filing("acc-1", pblntf_ty="8-K", rcept_dt="2026-07-01"), ["financing_or_debt:8-K item 2.03"])
    long_excerpt = "x" * 600
    current = _candidate("cur-1", _edgar_filing("acc-2", pblntf_ty="8-K", rcept_dt="2026-08-10"), ["financing_or_debt:8-K item 2.03"], excerpt_original=long_excerpt)
    result = build_comparison_result(current, [prior], computed_at=_COMPUTED_AT)
    assert any("truncation bound" in lim for lim in result.limitations)


# ============================================================
# Part J — purity / immutability (test 25, 26, 27)
# ============================================================


def test_inputs_are_not_mutated():
    prior = _candidate("prior-1", _edgar_filing("acc-1", pblntf_ty="8-K", rcept_dt="2026-07-01"), ["financing_or_debt:8-K item 2.03"])
    current = _candidate("cur-1", _edgar_filing("acc-2", pblntf_ty="8-K", rcept_dt="2026-08-10"), ["financing_or_debt:8-K item 2.03", "governance_or_management_change:8-K item 5.02"])
    prior_snapshot = copy.deepcopy(prior)
    current_snapshot = copy.deepcopy(current)

    build_comparison_result(current, [prior], computed_at=_COMPUTED_AT)

    assert prior == prior_snapshot
    assert current == current_snapshot


def test_comparison_result_is_frozen():
    current = _candidate("cur-1", _edgar_filing("acc-1", rcept_dt="2026-08-10"), ["financing_or_debt:8-K item 2.03"])
    result = build_comparison_result(current, [], computed_at=_COMPUTED_AT)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.comparison_status = "TAMPERED"  # type: ignore[misc]


def test_rule_set_diff_is_frozen():
    diff = compare_matched_rules(["a:x"], ["b:x"])
    with pytest.raises(dataclasses.FrozenInstanceError):
        diff.added = ("tampered",)  # type: ignore[misc]


def test_computed_at_is_entirely_caller_supplied():
    current = _candidate("cur-1", _edgar_filing("acc-1", rcept_dt="2026-08-10"), ["financing_or_debt:8-K item 2.03"])
    custom_timestamp = "2099-01-01T00:00:00+00:00"
    result = build_comparison_result(current, [], computed_at=custom_timestamp)
    assert result.computed_at == custom_timestamp


def test_module_never_reads_wall_clock_time():
    source = (Path(__file__).parent.parent / "src" / "logic" / "prior_disclosure_comparison.py").read_text(encoding="utf-8")
    assert "datetime.now(" not in source
    assert "date.today(" not in source
    assert "time.time(" not in source


# ============================================================
# Part K — scope guard (test 28)
# ============================================================


def test_module_imports_are_limited_to_stdlib_and_shared_models():
    path = Path(__file__).parent.parent / "src" / "logic" / "prior_disclosure_comparison.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    allowed_exact_modules = {"src.models.models"}
    allowed_stdlib_top_levels = {"__future__", "re", "dataclasses", "datetime", "enum", "typing"}
    offenders = []
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for module in modules:
            if module in allowed_exact_modules:
                continue
            if module.split(".")[0] in allowed_stdlib_top_levels:
                continue
            offenders.append(module)
    assert not offenders, offenders


def test_no_new_dependency_added_to_requirements():
    repo_root = Path(__file__).parent.parent
    result = subprocess.run(
        ["git", "diff", "HEAD", "--", "requirements.txt"], cwd=repo_root, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return
    assert result.stdout.strip() == "", f"requirements.txt was modified: {result.stdout}"


def test_scope_guard_only_new_module_and_test_file_changed():
    """Runs against `git diff HEAD` — only meaningful in a real checkout
    with this step's changes present; spuriously fires while ANY other
    legitimate uncommitted change is present and resolves once committed
    — same documented convention as this repo's other phase-scoped scope
    guards (tests/test_evidence_packet_phase1.py etc.)."""
    repo_root = Path(__file__).parent.parent
    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    allowed = {
        "src/logic/prior_disclosure_comparison.py",
        "tests/test_prior_disclosure_comparison.py",
    }
    assert changed <= allowed, changed - allowed


def test_no_pipeline_ui_persistence_or_client_files_touched():
    repo_root = Path(__file__).parent.parent
    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    forbidden_prefixes = (
        "src/ui/", "src/data_access/daily_news/", "src/data_access/edgar/", "src/data_access/dart/",
        "src/data_access/edinet/", "src/data_access/state_db/", "src/data_access/postgres_state_db/",
        "src/data_access/translation/", "scripts/",
    )
    hit = {c for c in changed if any(c.startswith(p) for p in forbidden_prefixes)}
    assert not hit, hit
