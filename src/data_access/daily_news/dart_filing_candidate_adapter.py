"""Daily News Filing-Event Shadow Adapter, Batch 2a — DART.

Pure, unwired, single-purpose mapping: `map_dart_filing_to_candidate(filing: FilingEvent)
-> FilingDerivedNewsCandidate | None`. No I/O, no network call, no worker/pipeline/
scan-client/database/translation/UI import — see
tests/test_dart_filing_candidate_adapter.py's own isolation tests for the mechanical
proof. Not imported by daily_news_pipeline.py, scripts/daily_news_worker.py,
feed_registry.py, daily_news_store.py, any UI page, or radar_pipeline.py/scan_service.py/
client.py — this module is a pure consumer of `FilingEvent`, never a producer or a caller
of anything Radar owns.

Company-identity note (a real taxonomy/field mismatch, discovered while writing this
adapter, not assumed): DART's own `FilingEvent.corp_name` is populated from the RAW DART
API response's own native-Korean name — `dart/scan_service.py`'s own filing-construction
call sets `corp_name=record.corp_name` (the disclosure record's own field), NOT the
resolved `TrackedCompany.name` the way EDGAR's and EDINET's `FilingEvent.corp_name` both
already are (`company.name` in each of those two sources' own construction). Using
`filing.corp_name` directly here would place an unverified, source-native name (e.g.
"삼성전자") into `FilingDerivedNewsCandidate.company_name`, which this project's own
existing convention requires to match a real `TrackedCompany.name` exactly (see
`daily_news_models.NewsStory.company_name`'s own docstring). This adapter therefore never
reads `filing.corp_name` for identity — it resolves `company_name` independently via
`filing.stock_code` matched against `TrackedCompany.krx_code` (the DART entries' own real
KRX stock code, the identity-key convention `tracked_companies.py` already documents),
returning the resolved `TrackedCompany.name` instead. The same resolution approach is
applied uniformly in the EDGAR/EDINET adapters too, even though their own `corp_name` is
already safe to use directly today, for consistency and defense-in-depth.

Dedupe-key correction (found during review, not assumed): `dedup.normalize_title()`'s
regex keeps only `[a-z0-9]` after lowercasing, so a native Korean `report_nm` normalizes
to an EMPTY string. The original batch's dedupe key was `{SOURCE}:{company}:{date}:
{normalized_title}` — with the title component always empty for DART, two DIFFERENT
same-company, same-day Korean-titled filings collided onto an IDENTICAL key. Corrected
format: `{SOURCE}:{issuer_identifier}:{doc_id}:{filing_date}:{normalized_title_or_empty}`
— `issuer_identifier` (`filing.corp_code`) + `doc_id` (`filing.rcept_no`) together already
uniquely identify this exact filing within DART, so the key can never collide regardless
of what the (still-reused, still-unmodified) `dedup.normalize_title()` does with the
title; the normalized title is kept only as an optional, non-load-bearing diagnostic
suffix. `dedup.py` itself is untouched — no new normalization algorithm was invented.
"""
from __future__ import annotations

from src.config.tracked_companies import get_tracked_companies_for_source
from src.data_access.daily_news import dedup
from src.data_access.daily_news.filing_event_models import (
    FilingCandidateStatus,
    FilingDerivedNewsCandidate,
    FilingProvenance,
    FilingSourceSystem,
    validate_filing_event_category,
)
from src.data_access.dart import dart_rules
from src.models.models import FilingEvent

_SOURCE_NAME = "OpenDART / DART"

# Approved-for-Daily-News subset of dart_rules.KOREAN_KEYWORD_LEXICON's
# own real category keys — deliberately excludes listing_or_market_event,
# ownership_change, and market_rumor_response, even though all three are
# real, live-mapped dart_rules categories that dart_rules.
# evaluate_report_name() itself would happily flag.
_INCLUDED_CATEGORIES: frozenset[str] = frozenset({
    "earnings", "guidance", "capex_or_facility_investment", "supply_or_sales_contract",
    "equity_or_jv_investment", "financing", "risk_disclosure",
})


def _resolve_company_name(krx_code: str) -> str | None:
    """Read-only lookup against the real tracked-company registry — never
    a raw, unverified name. See module docstring for why `filing.
    corp_name` itself is never used for this."""
    for company in get_tracked_companies_for_source(_SOURCE_NAME, active_only=False):
        if company.krx_code == krx_code:
            return company.name
    return None


def map_dart_filing_to_candidate(filing: FilingEvent) -> FilingDerivedNewsCandidate | None:
    """Pure function, no I/O. Returns None for any excluded/unmapped
    event, an amendment, an unresolvable company identity, or a
    wrongly-routed non-DART FilingEvent — never raises, never fabricates
    a value."""
    if filing.source_name != _SOURCE_NAME:
        return None

    # Checked first, independent of category: dart_rules.
    # evaluate_report_name() flags the amendment marker as an *additional*
    # matched_rules entry but does NOT itself suppress an underlying
    # category match (an amended earnings report still shows
    # confidence="Moderate"/"High") — the approved scope requires
    # excluding every amendment regardless of category, so this adapter
    # checks it explicitly, before consulting the rule evaluation at all.
    if dart_rules.AMENDMENT_MARKER in filing.report_nm:
        return None

    evaluation = dart_rules.evaluate_report_name(filing.report_nm)
    if evaluation.confidence is None:
        # Covers dart_rules.ROUTINE_EXCLUDE_PATTERNS matches (already
        # excluded inside evaluate_report_name() itself) and any title
        # matching no keyword rule at all.
        return None

    event_category: str | None = None
    for matched_rule in evaluation.matched_rules:
        category = matched_rule.split(":", 1)[0]
        if category in _INCLUDED_CATEGORIES:
            event_category = category
            break
    if event_category is None:
        # Every matched category was one this batch's approved scope
        # excludes (listing_or_market_event / ownership_change /
        # market_rumor_response) — never included even though
        # evaluate_report_name() itself flagged the filing.
        return None

    if not validate_filing_event_category(FilingSourceSystem.DART, event_category):
        return None

    company_name = _resolve_company_name(filing.stock_code)
    if company_name is None:
        return None

    filing_date = filing.filed_at or filing.rcept_dt
    normalized_title = dedup.normalize_title(filing.report_nm)
    # Format: "{SOURCE}:{issuer_identifier}:{doc_id}:{filing_date}:
    # {dedup.normalize_title(title_native) or ''}" — see this module's
    # own docstring ("Dedupe-key correction") for why issuer_identifier +
    # doc_id, not the title, guarantee uniqueness here. Identical shape
    # to the EDGAR/EDINET adapters.
    dedupe_key = f"DART:{filing.corp_code}:{filing.rcept_no}:{filing_date}:{normalized_title}"

    return FilingDerivedNewsCandidate(
        doc_id=filing.rcept_no,
        source_system=FilingSourceSystem.DART,
        company_name=company_name,
        issuer_identifier=filing.corp_code,
        filing_date=filing_date,
        title_native=filing.report_nm,
        event_category=event_category,
        provenance=FilingProvenance(
            source_system=FilingSourceSystem.DART,
            retrieved_at=filing.retrieved_at,
            source_form_code=event_category,
        ),
        dedupe_key=dedupe_key,
        status=FilingCandidateStatus.SHADOW,
        # DartClient.viewer_url()'s own real, working, already-proven
        # viewer page — used by the public Radar card as-is, no rewrite
        # needed (unlike EDGAR's own directory-listing source_url).
        official_document_url=filing.source_url or None,
    )
