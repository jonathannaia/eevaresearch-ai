"""Daily News Filing-Event Shadow Adapter, Batch 2a (corrected) — EDGAR
mapping tests. Pure, fixture-driven, zero network calls, zero EDGAR
client/scan call. Every fixture is a hand-built FilingEvent, never
fetched. `matched_rules` fixtures use the exact "category:8-K item
X.XX" shape edgar_rules.refine_8k_evaluation() itself produces — never
an invented shape."""
from __future__ import annotations

import dataclasses

from src.data_access.daily_news.edgar_filing_candidate_adapter import map_edgar_filing_to_candidate
from src.data_access.daily_news.filing_event_models import FilingCandidateStatus, FilingSourceSystem
from src.models.models import FilingEvent

# NVIDIA is a real, tracked SEC EDGAR issuer (tracked_companies.py) —
# krx_code="NVDA" — used so _resolve_company_name() succeeds for every
# "included" fixture below.
_NVDA_STOCK_CODE = "NVDA"
_NVDA_NAME = "NVIDIA"


def _filing(**overrides) -> FilingEvent:
    fields = dict(
        rcept_no="0001045810-26-000078", corp_code="0001045810", corp_name=_NVDA_NAME,
        stock_code=_NVDA_STOCK_CODE, report_nm="Annual Report", rcept_dt="2026-08-01",
        flr_nm=_NVDA_NAME, pblntf_ty="10-K",
        source_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000078/",
        retrieved_at="2026-08-01T00:00:00+00:00", source_name="SEC EDGAR", original_language="English",
    )
    fields.update(overrides)
    return FilingEvent(**fields)


# ============================================================
# 10-K/10-Q — unchanged: determined from bare form type alone,
# matched_rules ignored entirely
# ============================================================


def test_10k_is_included_as_earnings_or_results():
    candidate = map_edgar_filing_to_candidate(_filing(pblntf_ty="10-K"))
    assert candidate is not None
    assert candidate.event_category == "earnings_or_results"
    assert candidate.source_system == FilingSourceSystem.EDGAR
    assert candidate.status == FilingCandidateStatus.SHADOW


def test_10q_is_included_as_earnings_or_results():
    candidate = map_edgar_filing_to_candidate(_filing(pblntf_ty="10-Q"))
    assert candidate is not None
    assert candidate.event_category == "earnings_or_results"


def test_10k_ignores_matched_rules_entirely():
    without = map_edgar_filing_to_candidate(_filing(pblntf_ty="10-K"))
    with_unrelated = map_edgar_filing_to_candidate(
        _filing(pblntf_ty="10-K"), matched_rules=("regulation_fd_disclosure:8-K item 7.01",),
    )
    assert without == with_unrelated


# ============================================================
# 8-K — one approved category supplied via matched_rules
# ============================================================


def test_8k_item_101_material_agreement_is_included_when_supplied():
    candidate = map_edgar_filing_to_candidate(
        _filing(pblntf_ty="8-K"), matched_rules=("material_agreement:8-K item 1.01",),
    )
    assert candidate is not None
    assert candidate.event_category == "material_agreement"
    assert candidate.provenance.source_form_code == "8-K item 1.01"


def test_8k_item_201_acquisition_or_disposition_is_included_when_supplied():
    candidate = map_edgar_filing_to_candidate(
        _filing(pblntf_ty="8-K"), matched_rules=("acquisition_or_disposition:8-K item 2.01",),
    )
    assert candidate is not None
    assert candidate.event_category == "acquisition_or_disposition"


def test_8k_item_202_earnings_or_results_is_included_when_supplied():
    candidate = map_edgar_filing_to_candidate(
        _filing(pblntf_ty="8-K"), matched_rules=("earnings_or_results:8-K item 2.02",),
    )
    assert candidate is not None
    assert candidate.event_category == "earnings_or_results"


def test_8k_item_203_financing_or_debt_is_included_when_supplied():
    candidate = map_edgar_filing_to_candidate(
        _filing(pblntf_ty="8-K"), matched_rules=("financing_or_debt:8-K item 2.03",),
    )
    assert candidate is not None
    assert candidate.event_category == "financing_or_debt"


def test_8k_item_502_governance_or_management_change_is_included_when_supplied():
    candidate = map_edgar_filing_to_candidate(
        _filing(pblntf_ty="8-K"), matched_rules=("governance_or_management_change:8-K item 5.02",),
    )
    assert candidate is not None
    assert candidate.event_category == "governance_or_management_change"


# ============================================================
# 8-K — missing/empty/unknown/excluded-only matched_rules -> None
# ============================================================


def test_8k_with_no_matched_rules_argument_is_excluded():
    assert map_edgar_filing_to_candidate(_filing(pblntf_ty="8-K")) is None


def test_8k_with_none_matched_rules_is_excluded():
    assert map_edgar_filing_to_candidate(_filing(pblntf_ty="8-K"), matched_rules=None) is None


def test_8k_with_empty_matched_rules_is_excluded():
    assert map_edgar_filing_to_candidate(_filing(pblntf_ty="8-K"), matched_rules=()) is None


def test_8k_with_unrecognized_item_is_excluded():
    candidate = map_edgar_filing_to_candidate(
        _filing(pblntf_ty="8-K"), matched_rules=("some_made_up_category:8-K item 9.99",),
    )
    assert candidate is None


def test_8k_with_only_excluded_item_701_is_excluded():
    candidate = map_edgar_filing_to_candidate(
        _filing(pblntf_ty="8-K"), matched_rules=("regulation_fd_disclosure:8-K item 7.01",),
    )
    assert candidate is None


def test_8k_with_only_excluded_item_801_is_excluded():
    candidate = map_edgar_filing_to_candidate(
        _filing(pblntf_ty="8-K"), matched_rules=("other_material_event:8-K item 8.01",),
    )
    assert candidate is None


def test_8k_report_nm_text_hinting_at_an_item_number_is_never_used_to_guess():
    # report_nm text is never parsed for item numbers — only the
    # explicit matched_rules argument drives inclusion.
    candidate = map_edgar_filing_to_candidate(
        _filing(pblntf_ty="8-K", report_nm="Item 1.01 Material Agreement"),
    )
    assert candidate is None


# ============================================================
# 8-K — ambiguous: more than one distinct approved category supplied
# ============================================================


def test_8k_with_two_distinct_approved_categories_is_ambiguous_and_excluded():
    candidate = map_edgar_filing_to_candidate(
        _filing(pblntf_ty="8-K"),
        matched_rules=("material_agreement:8-K item 1.01", "financing_or_debt:8-K item 2.03"),
    )
    assert candidate is None


def test_8k_with_one_approved_and_one_excluded_category_still_resolves_to_the_single_approved_one():
    # Only one APPROVED category is present (7.01 is excluded, not
    # approved) — this is not the "multiple approved categories" case,
    # so it resolves normally rather than being treated as ambiguous.
    candidate = map_edgar_filing_to_candidate(
        _filing(pblntf_ty="8-K"),
        matched_rules=("material_agreement:8-K item 1.01", "regulation_fd_disclosure:8-K item 7.01"),
    )
    assert candidate is not None
    assert candidate.event_category == "material_agreement"


# ============================================================
# Excluded: ownership (SC 13D/13D-A/13G/13G-A), including alias spellings
# ============================================================


def test_sc_13d_and_variants_are_excluded():
    for form in ("SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"):
        assert map_edgar_filing_to_candidate(_filing(pblntf_ty=form)) is None, form


def test_schedule_13d_alias_spelling_is_also_excluded():
    assert map_edgar_filing_to_candidate(_filing(pblntf_ty="SCHEDULE 13D")) is None


# ============================================================
# Excluded: registration/prospectus forms, including amendments
# ============================================================


def test_s1_s3_and_424b_variants_are_excluded():
    for form in ("S-1", "S-3", "424B1", "424B2", "424B3", "424B4", "424B5"):
        assert map_edgar_filing_to_candidate(_filing(pblntf_ty=form)) is None, form


def test_s1_amendment_is_also_excluded():
    assert map_edgar_filing_to_candidate(_filing(pblntf_ty="S-1/A")) is None


# ============================================================
# Excluded: Forms 3/4/5 (never mapped at all) and unknown forms
# ============================================================


def test_forms_3_4_5_are_excluded():
    for form in ("3", "4", "5"):
        assert map_edgar_filing_to_candidate(_filing(pblntf_ty=form)) is None, form


def test_unknown_unmapped_form_type_is_excluded():
    assert map_edgar_filing_to_candidate(_filing(pblntf_ty="NOT-A-REAL-FORM")) is None


# ============================================================
# Guards
# ============================================================


def test_non_edgar_source_filing_event_is_ignored():
    filing = _filing(pblntf_ty="10-K", source_name="OpenDART / DART")
    assert map_edgar_filing_to_candidate(filing) is None


def test_unresolvable_company_identity_returns_none():
    filing = _filing(pblntf_ty="10-K", stock_code="NOT-A-REAL-TICKER")
    assert map_edgar_filing_to_candidate(filing) is None


def test_unresolvable_company_identity_returns_none_for_8k_too():
    filing = _filing(pblntf_ty="8-K", stock_code="NOT-A-REAL-TICKER")
    candidate = map_edgar_filing_to_candidate(filing, matched_rules=("material_agreement:8-K item 1.01",))
    assert candidate is None


# ============================================================
# Field mapping, determinism, invariants
# ============================================================


def test_doc_id_issuer_identifier_and_title_native_map_exactly():
    filing = _filing(pblntf_ty="10-K", rcept_no="0001045810-26-000099", corp_code="0001045810", report_nm="Real Title")
    candidate = map_edgar_filing_to_candidate(filing)
    assert candidate.doc_id == "0001045810-26-000099"
    assert candidate.issuer_identifier == "0001045810"
    assert candidate.title_native == "Real Title"
    assert candidate.title_native == filing.report_nm  # byte-identical, never rewritten


def test_filing_date_uses_rcept_dt_when_filed_at_is_absent():
    filing = _filing(pblntf_ty="10-K", rcept_dt="2026-07-15")
    candidate = map_edgar_filing_to_candidate(filing)
    assert candidate.filing_date == "2026-07-15"


def test_official_document_url_uses_filing_source_url_verbatim():
    filing = _filing(pblntf_ty="10-K", source_url="https://www.sec.gov/Archives/edgar/data/1045810/x/")
    candidate = map_edgar_filing_to_candidate(filing)
    assert candidate.official_document_url == "https://www.sec.gov/Archives/edgar/data/1045810/x/"


def test_translation_fields_are_always_none():
    candidate = map_edgar_filing_to_candidate(_filing(pblntf_ty="10-K"))
    assert candidate.title_translated is None
    assert candidate.translation_language is None
    assert candidate.translation_retrieved_at is None


def test_status_is_always_shadow():
    candidate = map_edgar_filing_to_candidate(_filing(pblntf_ty="10-Q"))
    assert candidate.status == FilingCandidateStatus.SHADOW


# ============================================================
# Dedupe key — corrected format, collision-resistant
# ============================================================


def test_dedupe_key_format():
    filing = _filing(pblntf_ty="10-K", report_nm="Annual Report", rcept_dt="2026-08-01")
    candidate = map_edgar_filing_to_candidate(filing)
    # "EDGAR:{issuer_identifier}:{doc_id}:{filing_date}:{normalized title}"
    assert candidate.dedupe_key == "EDGAR:0001045810:0001045810-26-000078:2026-08-01:annual report"


def test_two_distinct_filings_same_company_same_date_produce_distinct_dedupe_keys():
    filing_a = _filing(pblntf_ty="10-K", rcept_no="0001045810-26-000078", rcept_dt="2026-08-01")
    filing_b = _filing(pblntf_ty="10-Q", rcept_no="0001045810-26-000079", rcept_dt="2026-08-01")
    candidate_a = map_edgar_filing_to_candidate(filing_a)
    candidate_b = map_edgar_filing_to_candidate(filing_b)
    assert candidate_a.dedupe_key != candidate_b.dedupe_key


def test_determinism_same_filing_yields_an_equal_candidate():
    filing = _filing(pblntf_ty="10-K")
    first = map_edgar_filing_to_candidate(filing)
    second = map_edgar_filing_to_candidate(filing)
    assert first == second
    assert first is not second  # frozen dataclasses compare by value, not identity


def test_determinism_holds_for_8k_with_matched_rules_too():
    filing = _filing(pblntf_ty="8-K")
    rules = ("material_agreement:8-K item 1.01",)
    first = map_edgar_filing_to_candidate(filing, matched_rules=rules)
    second = map_edgar_filing_to_candidate(filing, matched_rules=rules)
    assert first == second


def test_returned_candidate_is_frozen():
    import pytest

    candidate = map_edgar_filing_to_candidate(_filing(pblntf_ty="10-K"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        candidate.title_native = "changed"  # type: ignore[misc]


def test_public_signature_has_the_corrected_optional_matched_rules_parameter():
    import inspect

    signature = inspect.signature(map_edgar_filing_to_candidate)
    params = list(signature.parameters.values())
    assert params[0].name == "filing"
    assert params[1].name == "matched_rules"
    assert params[1].default is None
