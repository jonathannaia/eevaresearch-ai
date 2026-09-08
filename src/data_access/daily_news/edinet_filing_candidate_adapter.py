"""Daily News Filing-Event Shadow Adapter, Batch 2a — EDINET.

Pure, unwired, single-purpose mapping: `map_edinet_filing_to_candidate(filing: FilingEvent)
-> FilingDerivedNewsCandidate | None`. No I/O, no network call, no worker/pipeline/
scan-client/database/translation/UI import — see
tests/test_edinet_filing_candidate_adapter.py's own isolation tests for the mechanical
proof. Not imported by daily_news_pipeline.py, scripts/daily_news_worker.py,
feed_registry.py, daily_news_store.py, any UI page, or edinet_pipeline.py/scan_service.py/
client.py. This module never edits `material_event_shadow.py` — it only imports and
reuses its existing, live-verified `is_eligible_extraordinary_report()` function exactly
as-is.

Product decision, enforced structurally: every candidate this adapter produces has
`status=FilingCandidateStatus.SHADOW` and `official_document_url=None` — never the
generic EDINET search-portal URL (`https://disclosure2.edinet-fsa.go.jp/`) as a
substitute. This is not merely a policy choice: `src/ui/components/radar_card.py`'s own
`_edinet_locator_line()`/`_public_source_url()` already establish, from a real prior
investigation, that EDINET has NO working direct per-document link at all in this
codebase — `EdinetClient.document_index_url()`'s own docstring calls its URL
"provisional... no dedicated public 'viewer' page was confirmed," and the Radar card
itself gives up and falls back to a search-portal link plus a human-readable locator
line rather than ever rendering a clickable EDINET document link. This adapter does the
same "give up cleanly" thing at the data level: `official_document_url` stays `None`
rather than being set to a link nobody has shown actually works.

Company-identity note: `FilingEvent.corp_name` for EDINET is already sourced from the
resolved `TrackedCompany.name` at construction time (`edinet/scan_service.py`'s own
filing-construction call: `corp_name=company.name`), so using it directly would be safe
today — but this adapter deliberately does NOT rely on that upstream guarantee, for the
same consistency/defense-in-depth reasoning as the EDGAR adapter. It independently
re-resolves identity via `filing.stock_code` matched against `TrackedCompany.krx_code`
(EDINET's own 5-character source-native securities code, per `tracked_companies.py`'s
own module docstring).

Dedupe-key correction: see `dart_filing_candidate_adapter.py`'s own module docstring for
the full rationale (identical fix applied uniformly across all three adapters, since
`dedup.normalize_title()`'s Latin-only regex normalizes a native Japanese `report_nm` to
an empty string too) — the key now always includes `issuer_identifier` and `doc_id`.
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
from src.data_access.edinet import edinet_rules
from src.data_access.edinet.material_event_shadow import is_eligible_extraordinary_report
from src.models.models import FilingEvent

_SOURCE_NAME = "EDINET"
_SHARE_BUYBACK_CATEGORY = "share_buyback_status"
_EXTRAORDINARY_REPORT_CATEGORY = "extraordinary_report"


def _resolve_company_name(krx_code: str) -> str | None:
    """Read-only lookup against the real tracked-company registry — never
    a raw, unverified name."""
    for company in get_tracked_companies_for_source(_SOURCE_NAME, active_only=False):
        if company.krx_code == krx_code:
            return company.name
    return None


def map_edinet_filing_to_candidate(filing: FilingEvent) -> FilingDerivedNewsCandidate | None:
    """Pure function, no I/O. Returns None for any excluded/unmapped
    event (including annual securities reports, corrections, confirmation
    letters, internal-control reports, and the domestic-specified-
    security variant — all already excluded by the reused rule functions
    themselves), an unresolvable company identity, or a wrongly-routed
    non-EDINET FilingEvent — never raises, never fabricates a value.
    Every returned candidate has status=SHADOW and official_document_url
    =None; see module docstring."""
    if filing.source_name != _SOURCE_NAME:
        return None

    triplet_key = f"{filing.ordinance_code}:{filing.pblntf_ty}:{filing.pblntf_detail_ty}"
    mapped_category = edinet_rules.DEFAULT_CODE_CATEGORY_MAP.get(triplet_key)

    if mapped_category == _SHARE_BUYBACK_CATEGORY:
        event_category = _SHARE_BUYBACK_CATEGORY
        source_form_code = triplet_key
    elif is_eligible_extraordinary_report(filing):
        event_category = _EXTRAORDINARY_REPORT_CATEGORY
        source_form_code = triplet_key
    else:
        # Covers: annual_securities_report (mapped_category would be that
        # string, never share_buyback_status), every correction/
        # confirmation-letter/internal-control-report/domestic-specified-
        # security-variant exclusion already enforced inside
        # is_eligible_extraordinary_report() itself, and every other
        # unmapped triplet.
        return None

    if not validate_filing_event_category(FilingSourceSystem.EDINET, event_category):
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
    # to the EDGAR/DART adapters.
    dedupe_key = f"EDINET:{filing.corp_code}:{filing.rcept_no}:{filing_date}:{normalized_title}"

    return FilingDerivedNewsCandidate(
        doc_id=filing.rcept_no,
        source_system=FilingSourceSystem.EDINET,
        company_name=company_name,
        issuer_identifier=filing.corp_code,
        filing_date=filing_date,
        title_native=filing.report_nm,
        event_category=event_category,
        provenance=FilingProvenance(
            source_system=FilingSourceSystem.EDINET,
            retrieved_at=filing.retrieved_at,
            source_form_code=source_form_code,
        ),
        dedupe_key=dedupe_key,
        status=FilingCandidateStatus.SHADOW,
        official_document_url=None,  # explicit product decision — never the generic search-portal URL; see module docstring
    )
