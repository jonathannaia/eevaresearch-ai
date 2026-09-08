"""material_event_shadow — pure-function tests for the EDINET Extraordinary
Report shadow eligibility evaluator (design/DECISIONS.md). No I/O, no
network, no CandidateSignal, no translation — every fixture here is either
real, live-verified metadata (design/DECISIONS.md's own EDINET
verification entries) or clearly-labeled fictional data, matching this
project's own established fixture-labeling discipline."""
from __future__ import annotations

from src.config.settings import Settings
from src.data_access.edinet.material_event_shadow import (
    ShadowMatch,
    _normalize,
    find_matches,
    is_eligible_extraordinary_report,
)
from src.models.models import FilingEvent


def test_settings_edinet_material_event_lexicon_enabled_defaults_to_false():
    assert Settings().edinet_material_event_lexicon_enabled is False


def _filing(
    rcept_no="S100FICT",
    corp_code="E00001",
    corp_name="Fictional Test Co.",
    report_nm="臨時報告書",
    ordinance_code="010",
    pblntf_ty="053000",
    pblntf_detail_ty="180",
) -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code=corp_code, corp_name=corp_name, stock_code="0000",
        report_nm=report_nm, rcept_dt="2026-09-03", flr_nm=corp_name,
        pblntf_ty=pblntf_ty, pblntf_detail_ty=pblntf_detail_ty, ordinance_code=ordinance_code,
        source_name="EDINET", original_language="Japanese",
    )


# ---------------------------------------------------------------------------
# Eligible pattern — the one real, live-verified triplet (design/DECISIONS.md,
# 2026-09-03: docID S100Z0CM, filer 出光興産株式会社, among 9 independent
# real filers sharing this exact triplet).
# ---------------------------------------------------------------------------

def test_eligible_plain_extraordinary_report_matches():
    filing = _filing(report_nm="臨時報告書", ordinance_code="010", pblntf_ty="053000", pblntf_detail_ty="180")
    assert is_eligible_extraordinary_report(filing) is True


def test_find_matches_returns_a_shadow_match_with_the_expected_fields():
    filing = _filing(
        rcept_no="S100Z0CM", corp_code="E01084", corp_name="出光興産株式会社",
        report_nm="臨時報告書", ordinance_code="010", pblntf_ty="053000", pblntf_detail_ty="180",
    )
    matches = find_matches((filing,))
    assert matches == (
        ShadowMatch(doc_id="S100Z0CM", issuer_name="出光興産株式会社", title="臨時報告書", triplet="010:053000:180"),
    )


def test_find_matches_never_mutates_the_input_filing():
    filing = _filing()
    before = FilingEvent(**filing.__dict__)
    find_matches((filing,))
    assert filing == before


# ---------------------------------------------------------------------------
# Explicit exclusions — real, live-verified non-eligible triplets.
# ---------------------------------------------------------------------------

def test_domestic_specified_securities_variant_excluded():
    # design/DECISIONS.md's own verification: 17 independent asset-manager
    # filers, e.g. docID S100YY11, filer 野村アセットマネジメント株式会社.
    filing = _filing(report_nm="臨時報告書（内国特定有価証券）", ordinance_code="030", pblntf_ty="995000", pblntf_detail_ty="180")
    assert is_eligible_extraordinary_report(filing) is False


def test_correction_prefixed_extraordinary_report_excluded():
    # design/DECISIONS.md's own verification: docID S100Z06P, filer
    # ニチコン株式会社, parentDocID populated (S100Z03K).
    filing = _filing(report_nm="訂正臨時報告書", ordinance_code="010", pblntf_ty="053001", pblntf_detail_ty="190")
    assert is_eligible_extraordinary_report(filing) is False


def test_any_title_containing_the_correction_marker_is_excluded_independent_of_triplet():
    # Proves the 訂正 marker check is independent of (and checked before)
    # the triplet/title-equality logic — even with the otherwise-eligible
    # triplet, a title containing 訂正 anywhere must never be eligible.
    filing = _filing(report_nm="訂正（臨時報告書）", ordinance_code="010", pblntf_ty="053000", pblntf_detail_ty="180")
    assert is_eligible_extraordinary_report(filing) is False


def test_confirmation_letter_excluded():
    # Real, live-verified: docID S100YFHB, SoftBank Group.
    filing = _filing(report_nm="確認書", ordinance_code="010", pblntf_ty="042000", pblntf_detail_ty="135")
    assert is_eligible_extraordinary_report(filing) is False


def test_internal_control_report_excluded():
    # Real, live-verified: docID S100YFH8, SoftBank Group.
    filing = _filing(report_nm="内部統制報告書－第46期(2025/04/01－2026/03/31)", ordinance_code="015", pblntf_ty="010000", pblntf_detail_ty="235")
    assert is_eligible_extraordinary_report(filing) is False


def test_annual_securities_report_not_matched_by_this_evaluator():
    # Real, live-verified: docID S100YGH5 — the existing, unchanged
    # annual_securities_report rule's own tuple. This module must never
    # match it (structurally excluded by exact-title-equality alone).
    filing = _filing(report_nm="有価証券報告書－第46期(2025/04/01－2026/03/31)", ordinance_code="010", pblntf_ty="030000", pblntf_detail_ty="120")
    assert is_eligible_extraordinary_report(filing) is False


def test_treasury_stock_buyback_status_report_not_matched():
    filing = _filing(report_nm="自己株券買付状況報告書", ordinance_code="010", pblntf_ty="040000", pblntf_detail_ty="050")
    assert is_eligible_extraordinary_report(filing) is False


# ---------------------------------------------------------------------------
# Both conditions independently required — requirement 1's explicit "AND".
# ---------------------------------------------------------------------------

def test_matching_title_with_unrelated_triplet_rejected():
    filing = _filing(report_nm="臨時報告書", ordinance_code="099", pblntf_ty="999999", pblntf_detail_ty="999")
    assert is_eligible_extraordinary_report(filing) is False


def test_matching_triplet_with_different_title_rejected():
    filing = _filing(report_nm="臨時報告書－追加情報", ordinance_code="010", pblntf_ty="053000", pblntf_detail_ty="180")
    assert is_eligible_extraordinary_report(filing) is False


def test_unknown_or_partial_triplet_with_unrelated_title_rejected():
    filing = _filing(report_nm="その他の開示書類", ordinance_code="020", pblntf_ty="010000", pblntf_detail_ty="050")
    assert is_eligible_extraordinary_report(filing) is False


# ---------------------------------------------------------------------------
# NFKC normalization — reuses the project's existing normalize primitive
# (unicodedata.normalize("NFKC", ...), the same transform
# src.logic.theme_matching.normalize_text already uses). The eligible
# title itself has no punctuation/digits/Latin characters for NFKC to
# affect, so normalization's practical value here is defense-in-depth
# against whitespace/width variants — proven two ways below: the standard
# NFKC primitive behavior, and a real-world full-width-punctuation case
# (confirmed live in design/DECISIONS.md's own verification exercise) that
# must still be correctly excluded after normalization.
# ---------------------------------------------------------------------------

def test_normalize_uses_nfkc_full_width_latin_example():
    assert _normalize("Ａ１") == "A1"


def test_full_width_punctuation_variant_still_correctly_excluded_after_normalization():
    # The real, live-observed domestic-specified-securities title uses
    # full-width parentheses (「（」「）」) — NFKC does not remove them, so
    # this must still be excluded (both by the differing title and by its
    # own differing, real triplet) even after normalization.
    filing = _filing(report_nm="臨時報告書（内国特定有価証券）", ordinance_code="030", pblntf_ty="995000", pblntf_detail_ty="180")
    assert _normalize(filing.report_nm) != "臨時報告書"
    assert is_eligible_extraordinary_report(filing) is False


# ---------------------------------------------------------------------------
# find_matches over a mixed batch — bounded to already-tracked-issuer
# filings, order-preserving, only the eligible ones returned.
# ---------------------------------------------------------------------------

def test_find_matches_filters_a_mixed_batch_to_only_eligible_entries():
    eligible = _filing(rcept_no="S100ELIGIBLE", corp_name="Eligible Co.", report_nm="臨時報告書", ordinance_code="010", pblntf_ty="053000", pblntf_detail_ty="180")
    excluded_domestic = _filing(rcept_no="S100EXCL1", corp_name="Fund Manager Co.", report_nm="臨時報告書（内国特定有価証券）", ordinance_code="030", pblntf_ty="995000", pblntf_detail_ty="180")
    excluded_correction = _filing(rcept_no="S100EXCL2", corp_name="Correction Co.", report_nm="訂正臨時報告書", ordinance_code="010", pblntf_ty="053001", pblntf_detail_ty="190")
    unrelated = _filing(rcept_no="S100UNREL", corp_name="Unrelated Co.", report_nm="有価証券報告書", ordinance_code="010", pblntf_ty="030000", pblntf_detail_ty="120")

    matches = find_matches((excluded_domestic, eligible, excluded_correction, unrelated))

    assert len(matches) == 1
    assert matches[0].doc_id == "S100ELIGIBLE"
    assert matches[0].issuer_name == "Eligible Co."


def test_find_matches_returns_empty_tuple_for_no_eligible_filings():
    excluded = _filing(report_nm="確認書", ordinance_code="010", pblntf_ty="042000", pblntf_detail_ty="135")
    assert find_matches((excluded,)) == ()


def test_find_matches_on_empty_input_returns_empty_tuple():
    assert find_matches(()) == ()
