"""Daily News Filing-Event Shadow Adapter, Batch 2a — EDINET mapping
tests. Pure, fixture-driven, zero network calls, zero EDINET client/scan
call. Every fixture is a hand-built FilingEvent, never fetched. Triplets
mirror the exact, live-verified values already documented in
edinet_rules.py / material_event_shadow.py."""
from __future__ import annotations

import dataclasses

from src.data_access.daily_news.edinet_filing_candidate_adapter import map_edinet_filing_to_candidate
from src.data_access.daily_news.filing_event_models import FilingCandidateStatus, FilingSourceSystem
from src.models.models import FilingEvent

# SoftBank Group Corp. is a real, tracked EDINET issuer
# (tracked_companies.py) — krx_code="99840" (EDINET's own 5-char
# securities code) — used so _resolve_company_name() succeeds for every
# "included" fixture below.
_SOFTBANK_STOCK_CODE = "99840"
_SOFTBANK_NAME = "SoftBank Group Corp."


def _filing(**overrides) -> FilingEvent:
    fields = dict(
        rcept_no="S100Z0ID", corp_code="E02778", corp_name="ソフトバンクグループ株式会社",
        stock_code=_SOFTBANK_STOCK_CODE, report_nm="臨時報告書", rcept_dt="2026-09-04",
        flr_nm="ソフトバンクグループ株式会社", pblntf_ty="053000", pblntf_detail_ty="180", ordinance_code="010",
        source_url="https://api.edinet-fsa.go.jp/api/v2/documents/S100Z0ID",
        retrieved_at="2026-09-04T00:00:00+00:00", source_name="EDINET", original_language="Japanese",
    )
    fields.update(overrides)
    return FilingEvent(**fields)


# ============================================================
# Included as SHADOW candidates only
# ============================================================


def test_extraordinary_report_is_included_as_shadow_only():
    candidate = map_edinet_filing_to_candidate(_filing(
        report_nm="臨時報告書", ordinance_code="010", pblntf_ty="053000", pblntf_detail_ty="180",
    ))
    assert candidate is not None
    assert candidate.event_category == "extraordinary_report"
    assert candidate.status == FilingCandidateStatus.SHADOW


def test_share_buyback_status_is_included_as_shadow_only():
    candidate = map_edinet_filing_to_candidate(_filing(
        report_nm="自己株券買付状況報告書（法２４条の６第１項に基づくもの）",
        ordinance_code="010", pblntf_ty="170000", pblntf_detail_ty="220",
    ))
    assert candidate is not None
    assert candidate.event_category == "share_buyback_status"
    assert candidate.status == FilingCandidateStatus.SHADOW


def test_edinet_official_document_url_is_always_none():
    for report_nm, ordinance, form, doctype in (
        ("臨時報告書", "010", "053000", "180"),
        ("自己株券買付状況報告書（法２４条の６第１項に基づくもの）", "010", "170000", "220"),
    ):
        candidate = map_edinet_filing_to_candidate(_filing(
            report_nm=report_nm, ordinance_code=ordinance, pblntf_ty=form, pblntf_detail_ty=doctype,
        ))
        assert candidate is not None
        assert candidate.official_document_url is None
        # Never the generic search-portal URL as a substitute either.
        assert candidate.official_document_url != "https://disclosure2.edinet-fsa.go.jp/"


def test_edinet_candidates_are_never_eligible_for_candidate_or_published_status():
    # This adapter itself only ever constructs status=SHADOW — this test
    # additionally proves filing_event_models.validate_filing_candidate()
    # would reject any attempt to promote an EDINET candidate further,
    # as an independent, defense-in-depth confirmation.
    from src.data_access.daily_news.filing_event_models import validate_filing_candidate

    candidate = map_edinet_filing_to_candidate(_filing(
        report_nm="臨時報告書", ordinance_code="010", pblntf_ty="053000", pblntf_detail_ty="180",
    ))
    promoted = dataclasses.replace(candidate, status=FilingCandidateStatus.CANDIDATE)
    violations = validate_filing_candidate(promoted)
    assert any("shadow_only" in v for v in violations)


# ============================================================
# Excluded categories (explicit product decision)
# ============================================================


def test_annual_securities_report_is_excluded():
    candidate = map_edinet_filing_to_candidate(_filing(
        report_nm="有価証券報告書", ordinance_code="010", pblntf_ty="030000", pblntf_detail_ty="120",
    ))
    assert candidate is None


def test_extraordinary_report_correction_is_excluded():
    candidate = map_edinet_filing_to_candidate(_filing(
        report_nm="訂正臨時報告書", ordinance_code="010", pblntf_ty="053001", pblntf_detail_ty="190",
    ))
    assert candidate is None


def test_buyback_correction_is_excluded():
    candidate = map_edinet_filing_to_candidate(_filing(
        report_nm="訂正自己株券買付状況報告書", ordinance_code="010", pblntf_ty="170001", pblntf_detail_ty="230",
    ))
    assert candidate is None


def test_confirmation_letter_is_excluded():
    candidate = map_edinet_filing_to_candidate(_filing(
        report_nm="確認書", ordinance_code="010", pblntf_ty="042000", pblntf_detail_ty="135",
    ))
    assert candidate is None


def test_internal_control_report_is_excluded():
    candidate = map_edinet_filing_to_candidate(_filing(
        report_nm="内部統制報告書", ordinance_code="015", pblntf_ty="010000", pblntf_detail_ty="235",
    ))
    assert candidate is None


def test_domestic_specified_securities_extraordinary_report_lookalike_is_excluded():
    # Same title text as the eligible Extraordinary Report but a
    # different, real, live-confirmed triplet — must not be captured by
    # a title-only check.
    candidate = map_edinet_filing_to_candidate(_filing(
        report_nm="臨時報告書（内国特定有価証券）", ordinance_code="030", pblntf_ty="995000", pblntf_detail_ty="180",
    ))
    assert candidate is None


def test_specified_securities_buyback_lookalike_is_excluded():
    # Same title text as the eligible share-buyback report but a
    # different ordinance (fund/REIT variant) — must not be captured by
    # a title-only check.
    candidate = map_edinet_filing_to_candidate(_filing(
        report_nm="自己株券買付状況報告書（法２４条の６第１項に基づくもの）",
        ordinance_code="030", pblntf_ty="253000", pblntf_detail_ty="220",
    ))
    assert candidate is None


def test_unmapped_unknown_category_is_excluded():
    candidate = map_edinet_filing_to_candidate(_filing(
        report_nm="何か他の書類", ordinance_code="999", pblntf_ty="999999", pblntf_detail_ty="999",
    ))
    assert candidate is None


# ============================================================
# Guards
# ============================================================


def test_non_edinet_source_filing_event_is_ignored():
    filing = _filing(report_nm="臨時報告書", source_name="SEC EDGAR")
    assert map_edinet_filing_to_candidate(filing) is None


def test_unresolvable_company_identity_returns_none():
    filing = _filing(report_nm="臨時報告書", stock_code="00000")
    assert map_edinet_filing_to_candidate(filing) is None


# ============================================================
# Field mapping
# ============================================================


def test_company_name_is_the_resolved_tracked_company_name_never_the_raw_corp_name():
    filing = _filing(report_nm="臨時報告書", corp_name="ソフトバンクグループ株式会社")
    candidate = map_edinet_filing_to_candidate(filing)
    assert candidate.company_name == _SOFTBANK_NAME
    assert candidate.company_name != filing.corp_name


def test_doc_id_issuer_identifier_and_title_native_map_exactly():
    filing = _filing(report_nm="臨時報告書", rcept_no="S100ZXYZ", corp_code="E02778")
    candidate = map_edinet_filing_to_candidate(filing)
    assert candidate.doc_id == "S100ZXYZ"
    assert candidate.issuer_identifier == "E02778"
    assert candidate.title_native == "臨時報告書"
    assert candidate.title_native == filing.report_nm  # byte-identical, never rewritten/translated


def test_filing_date_prefers_filed_at_over_rcept_dt_when_present():
    filing = _filing(report_nm="臨時報告書", rcept_dt="2026-09-04", filed_at="2026-09-04T15:00:00+09:00")
    candidate = map_edinet_filing_to_candidate(filing)
    assert candidate.filing_date == "2026-09-04T15:00:00+09:00"


def test_filing_date_falls_back_to_rcept_dt_when_filed_at_is_absent():
    filing = _filing(report_nm="臨時報告書", rcept_dt="2026-09-04", filed_at=None)
    candidate = map_edinet_filing_to_candidate(filing)
    assert candidate.filing_date == "2026-09-04"


# ============================================================
# Dedupe key — corrected format, collision-resistant for native-script
# (Japanese) titles, which dedup.normalize_title() reduces to an empty
# string
# ============================================================


def test_dedupe_key_format():
    filing = _filing(
        report_nm="臨時報告書", rcept_no="S100Z0ID", corp_code="E02778", rcept_dt="2026-09-04", filed_at=None,
    )
    candidate = map_edinet_filing_to_candidate(filing)
    # "EDINET:{issuer_identifier}:{doc_id}:{filing_date}:{normalized
    # title or empty}" — the Japanese title normalizes to "" (see module
    # docstring), so the key correctly ends with an empty component here.
    assert candidate.dedupe_key == "EDINET:E02778:S100Z0ID:2026-09-04:"


def test_two_distinct_japanese_titled_filings_same_company_same_date_produce_distinct_dedupe_keys():
    # This is the exact collision the correction fixes: both titles
    # normalize to an empty string via dedup.normalize_title(), so only
    # the doc_id component can tell them apart.
    filing_a = _filing(
        report_nm="臨時報告書", rcept_no="S100Z0ID", ordinance_code="010", pblntf_ty="053000", pblntf_detail_ty="180",
        rcept_dt="2026-09-04", filed_at=None,
    )
    filing_b = _filing(
        report_nm="自己株券買付状況報告書（法２４条の６第１項に基づくもの）", rcept_no="S100Z0IE",
        ordinance_code="010", pblntf_ty="170000", pblntf_detail_ty="220", rcept_dt="2026-09-04", filed_at=None,
    )
    candidate_a = map_edinet_filing_to_candidate(filing_a)
    candidate_b = map_edinet_filing_to_candidate(filing_b)
    assert candidate_a is not None and candidate_b is not None
    assert candidate_a.dedupe_key != candidate_b.dedupe_key
    # Confirms the failure mode this test guards against: the OLD
    # company+date+title-only key would have been identical for both.
    old_style_key_a = f"EDINET:{candidate_a.company_name.strip().lower()}:{filing_a.rcept_dt}:"
    old_style_key_b = f"EDINET:{candidate_b.company_name.strip().lower()}:{filing_b.rcept_dt}:"
    assert old_style_key_a == old_style_key_b


def test_translation_fields_are_always_none():
    candidate = map_edinet_filing_to_candidate(_filing(report_nm="臨時報告書"))
    assert candidate.title_translated is None
    assert candidate.translation_language is None
    assert candidate.translation_retrieved_at is None


def test_determinism_same_filing_yields_an_equal_candidate():
    filing = _filing(report_nm="臨時報告書")
    first = map_edinet_filing_to_candidate(filing)
    second = map_edinet_filing_to_candidate(filing)
    assert first == second
    assert first is not second


def test_returned_candidate_is_frozen():
    import pytest

    candidate = map_edinet_filing_to_candidate(_filing(report_nm="臨時報告書"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        candidate.title_native = "changed"  # type: ignore[misc]
