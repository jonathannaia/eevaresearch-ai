"""Daily News Filing-Event Shadow Adapter, Batch 2a — DART mapping
tests. Pure, fixture-driven, zero network calls, zero DART client/scan
call. Every fixture is a hand-built FilingEvent, never fetched."""
from __future__ import annotations

import dataclasses

from src.data_access.daily_news.dart_filing_candidate_adapter import map_dart_filing_to_candidate
from src.data_access.daily_news.filing_event_models import FilingCandidateStatus, FilingSourceSystem
from src.models.models import FilingEvent

# Samsung Electronics is a real, tracked OpenDART / DART issuer
# (tracked_companies.py) — krx_code="005930" — used so
# _resolve_company_name() succeeds for every "included" fixture below.
_SAMSUNG_STOCK_CODE = "005930"
_SAMSUNG_NAME = "Samsung Electronics"


def _filing(**overrides) -> FilingEvent:
    fields = dict(
        rcept_no="20260901000123", corp_code="00126380", corp_name="삼성전자", stock_code=_SAMSUNG_STOCK_CODE,
        report_nm="실적", rcept_dt="2026-09-01", flr_nm="삼성전자",
        source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260901000123",
        retrieved_at="2026-09-01T00:00:00+00:00", source_name="OpenDART / DART", original_language="Korean",
    )
    fields.update(overrides)
    return FilingEvent(**fields)


# ============================================================
# Included categories — one real keyword per category, from
# dart_rules.KOREAN_KEYWORD_LEXICON's own "observed live" entries
# ============================================================


def test_earnings_is_included():
    candidate = map_dart_filing_to_candidate(_filing(report_nm="실적"))
    assert candidate is not None
    assert candidate.event_category == "earnings"


def test_guidance_is_included():
    candidate = map_dart_filing_to_candidate(_filing(report_nm="장래사업ㆍ경영계획"))
    assert candidate is not None
    assert candidate.event_category == "guidance"


def test_capex_or_facility_investment_is_included():
    candidate = map_dart_filing_to_candidate(_filing(report_nm="신규시설투자"))
    assert candidate is not None
    assert candidate.event_category == "capex_or_facility_investment"


def test_supply_or_sales_contract_is_included():
    candidate = map_dart_filing_to_candidate(_filing(report_nm="단일판매ㆍ공급계약체결"))
    assert candidate is not None
    assert candidate.event_category == "supply_or_sales_contract"


def test_equity_or_jv_investment_is_included():
    candidate = map_dart_filing_to_candidate(_filing(report_nm="타법인주식및출자증권취득"))
    assert candidate is not None
    assert candidate.event_category == "equity_or_jv_investment"


def test_financing_is_included():
    candidate = map_dart_filing_to_candidate(_filing(report_nm="유상증자"))
    assert candidate is not None
    assert candidate.event_category == "financing"


def test_risk_disclosure_is_included():
    candidate = map_dart_filing_to_candidate(_filing(report_nm="중대재해발생"))
    assert candidate is not None
    assert candidate.event_category == "risk_disclosure"


# ============================================================
# Excluded categories (explicit product decision)
# ============================================================


def test_listing_or_market_event_is_excluded():
    assert map_dart_filing_to_candidate(_filing(report_nm="상장결정")) is None


def test_ownership_change_is_excluded():
    assert map_dart_filing_to_candidate(_filing(report_nm="최대주주등소유주식변동")) is None
    assert map_dart_filing_to_candidate(_filing(report_nm="대량보유상황보고서")) is None


def test_market_rumor_response_is_excluded():
    assert map_dart_filing_to_candidate(_filing(report_nm="조회공시요구")) is None


# ============================================================
# Amendment marker — excluded regardless of underlying category
# ============================================================


def test_amendment_marker_excludes_an_otherwise_included_category():
    candidate_without_marker = map_dart_filing_to_candidate(_filing(report_nm="실적", rcept_no="a"))
    assert candidate_without_marker is not None  # sanity: this category is normally included
    assert map_dart_filing_to_candidate(_filing(report_nm="[기재정정]실적", rcept_no="b")) is None


# ============================================================
# Routine-exclude patterns (already excluded inside
# dart_rules.evaluate_report_name() itself — no separate check needed)
# ============================================================


def test_routine_exclude_pattern_is_excluded():
    assert map_dart_filing_to_candidate(_filing(report_nm="임원ㆍ주요주주특정증권등소유상황보고서")) is None
    assert map_dart_filing_to_candidate(_filing(report_nm="기업설명회(IR)개최")) is None


# ============================================================
# Unknown/unmapped title
# ============================================================


def test_unknown_title_matching_no_keyword_rule_is_excluded():
    assert map_dart_filing_to_candidate(_filing(report_nm="전혀 관련 없는 제목")) is None


# ============================================================
# Guards
# ============================================================


def test_non_dart_source_filing_event_is_ignored():
    filing = _filing(report_nm="실적", source_name="SEC EDGAR")
    assert map_dart_filing_to_candidate(filing) is None


def test_unresolvable_company_identity_returns_none():
    filing = _filing(report_nm="실적", stock_code="999999")
    assert map_dart_filing_to_candidate(filing) is None


# ============================================================
# Field mapping — including the corp_name-vs-resolved-name distinction
# ============================================================


def test_company_name_is_the_resolved_tracked_company_name_never_the_raw_corp_name():
    # filing.corp_name is the raw Korean native name ("삼성전자") — the
    # candidate's company_name must be the resolved TrackedCompany.name
    # ("Samsung Electronics"), never filing.corp_name itself.
    filing = _filing(report_nm="실적", corp_name="삼성전자")
    candidate = map_dart_filing_to_candidate(filing)
    assert candidate.company_name == _SAMSUNG_NAME
    assert candidate.company_name != filing.corp_name


def test_doc_id_issuer_identifier_and_title_native_map_exactly():
    filing = _filing(report_nm="실적", rcept_no="20260901000999", corp_code="00126380")
    candidate = map_dart_filing_to_candidate(filing)
    assert candidate.doc_id == "20260901000999"
    assert candidate.issuer_identifier == "00126380"
    assert candidate.title_native == "실적"
    assert candidate.title_native == filing.report_nm  # byte-identical, never rewritten/translated


def test_filing_date_uses_rcept_dt_when_filed_at_is_absent():
    filing = _filing(report_nm="실적", rcept_dt="2026-06-30")
    candidate = map_dart_filing_to_candidate(filing)
    assert candidate.filing_date == "2026-06-30"


# ============================================================
# Dedupe key — corrected format, collision-resistant for native-script
# (Korean) titles, which dedup.normalize_title() reduces to an empty
# string
# ============================================================


def test_dedupe_key_format():
    filing = _filing(report_nm="실적", rcept_no="20260901000123", corp_code="00126380", rcept_dt="2026-09-01")
    candidate = map_dart_filing_to_candidate(filing)
    # "DART:{issuer_identifier}:{doc_id}:{filing_date}:{normalized title
    # or empty}" — the Korean title normalizes to "" (see module
    # docstring), so the key correctly ends with an empty component here.
    assert candidate.dedupe_key == "DART:00126380:20260901000123:2026-09-01:"


def test_two_distinct_korean_titled_filings_same_company_same_date_produce_distinct_dedupe_keys():
    # This is the exact collision the correction fixes: both titles
    # normalize to an empty string via dedup.normalize_title(), so only
    # the doc_id component can tell them apart.
    filing_a = _filing(report_nm="실적", rcept_no="20260901000123", rcept_dt="2026-09-01")
    filing_b = _filing(report_nm="장래사업ㆍ경영계획", rcept_no="20260901000124", rcept_dt="2026-09-01")
    candidate_a = map_dart_filing_to_candidate(filing_a)
    candidate_b = map_dart_filing_to_candidate(filing_b)
    assert candidate_a is not None and candidate_b is not None
    assert candidate_a.dedupe_key != candidate_b.dedupe_key
    # Confirms the failure mode this test guards against: the OLD
    # company+date+title-only key would have been identical for both.
    old_style_key_a = f"DART:{candidate_a.company_name.strip().lower()}:{filing_a.rcept_dt}:"
    old_style_key_b = f"DART:{candidate_b.company_name.strip().lower()}:{filing_b.rcept_dt}:"
    assert old_style_key_a == old_style_key_b


def test_official_document_url_uses_filing_source_url_verbatim():
    filing = _filing(report_nm="실적", source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260901000123")
    candidate = map_dart_filing_to_candidate(filing)
    assert candidate.official_document_url == "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260901000123"


def test_translation_fields_are_always_none():
    candidate = map_dart_filing_to_candidate(_filing(report_nm="실적"))
    assert candidate.title_translated is None
    assert candidate.translation_language is None
    assert candidate.translation_retrieved_at is None


def test_status_is_always_shadow():
    candidate = map_dart_filing_to_candidate(_filing(report_nm="실적"))
    assert candidate.status == FilingCandidateStatus.SHADOW
    assert candidate.source_system == FilingSourceSystem.DART


def test_determinism_same_filing_yields_an_equal_candidate():
    filing = _filing(report_nm="실적")
    first = map_dart_filing_to_candidate(filing)
    second = map_dart_filing_to_candidate(filing)
    assert first == second
    assert first is not second


def test_returned_candidate_is_frozen():
    import pytest

    candidate = map_dart_filing_to_candidate(_filing(report_nm="실적"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        candidate.title_native = "changed"  # type: ignore[misc]
