"""Daily News Filing-Event Shadow Adapter, Batch 2a (corrected) — EDGAR.

Pure, unwired, single-purpose mapping: `map_edgar_filing_to_candidate(filing: FilingEvent,
matched_rules: tuple[str, ...] | None = None) -> FilingDerivedNewsCandidate | None`. No
I/O, no network call, no worker/pipeline/scan-client/database/translation/UI import — see
tests/test_edgar_filing_candidate_adapter.py's own isolation tests for the mechanical
proof. Not imported by daily_news_pipeline.py, scripts/daily_news_worker.py,
feed_registry.py, daily_news_store.py, any UI page, or edgar_pipeline.py/scan_service.py/
client.py — this module is a pure consumer of `FilingEvent`, never a producer or a caller
of anything Radar owns. It never imports `CandidateSignal`, `scan_service`, any pipeline,
worker, Radar store, database, UI, client, or network code — `matched_rules` is accepted
as a plain `tuple[str, ...]`, decoupled from the `CandidateSignal` type that happens to
carry it in the real (unwired) system.

8-K ITEM-LEVEL CORRECTION (Option A, per the approved correction): `FilingEvent.pblntf_ty`
alone holds only SEC's raw form-type string ("8-K"), identical for every item number —
the real item-level detail (SEC's own `items` column, e.g. "1.01,2.03") is parsed only
inside `edgar/scan_service.py::_evaluate_row()` and lives on the resulting
`CandidateSignal.matched_rules`, a DIFFERENT object a bare `FilingEvent` cannot carry.
Rather than excluding every 8-K unconditionally (this batch's original, more conservative
behavior), this adapter now accepts an OPTIONAL, PURE `matched_rules` parameter — the
exact same shape `edgar_rules.refine_8k_evaluation()`/`items_from_matched_rules()` already
produce/consume today ("category:8-K item X.XX", e.g. "material_agreement:8-K item
1.01") — so a caller that already has both objects in hand (e.g. a later, separately-
approved batch reading `ScanResult.new_filing_events` alongside `ScanResult.
new_candidate_signals` from the same scan tick) can supply it. This adapter still never
fetches, parses document text, or constructs a CandidateSignal itself.

Approved 8-K item categories (`_EIGHT_K_APPROVED_CATEGORIES`, reusing
`edgar_rules.EIGHT_K_ITEM_CATEGORIES`'s own real item-number-to-category mapping exactly,
never a duplicated literal): `material_agreement` (1.01), `acquisition_or_disposition`
(2.01), `earnings_or_results` (2.02), `financing_or_debt` (2.03),
`governance_or_management_change` (5.02). `regulation_fd_disclosure` (7.01) and
`other_material_event` (8.01) are never approved, matching the original exclusion
decision exactly — they simply are never in `_EIGHT_K_APPROVED_CATEGORIES`, so
`matched_rules` containing only those (or an unrecognized item, or nothing at all)
resolves to zero approved categories, which returns None (see
`_single_approved_8k_category()`'s own docstring). If `matched_rules` resolves to MORE
THAN ONE distinct approved category, this adapter also returns None — never picking one
arbitrarily. A later, separately-approved design may choose to emit one candidate per
matched item, or apply a deterministic primary-category policy; neither is decided or
implemented here.

10-K/10-Q handling is unchanged from the original batch: determined entirely from
`filing.pblntf_ty` via `edgar_rules.normalize_form_type()`, `matched_rules` is not
consulted for these two form types at all. Every other original exclusion is unchanged:
Forms 3/4/5 (never in `edgar_rules.FORM_TYPE_CATEGORIES`), SC 13D/13D-A/13G/13G-A,
S-1/S-3/424B1-5 (including amendments), and any unknown form type.

Company-identity note: `FilingEvent.corp_name` for EDGAR is already sourced from the
resolved `TrackedCompany.name` at construction time (`edgar/scan_service.py`'s own
`_filing_event_from_row`: `corp_name=company.name`), so using it directly would be safe
today — but this adapter deliberately does NOT rely on that upstream guarantee. It
independently re-resolves identity via `filing.stock_code` matched against
`TrackedCompany.krx_code` (the same identity-key convention `tracked_companies.py`
documents for every source), for consistency with the DART adapter, where the equivalent
shortcut is NOT safe (see dart_filing_candidate_adapter.py's own docstring).

Dedupe-key correction: see `dart_filing_candidate_adapter.py`'s own module docstring for
the full rationale (identical fix applied uniformly across all three adapters) — the key
now always includes `issuer_identifier` and `doc_id`, so it can never collide across two
genuinely distinct filings regardless of what `dedup.normalize_title()` does with the
title text.
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
from src.data_access.edgar import edgar_rules
from src.models.models import FilingEvent

_SOURCE_NAME = "SEC EDGAR"

# 10-K/10-Q are determined from bare form type alone — unchanged from
# the original batch. Forms 3/4/5 (never in edgar_rules.
# FORM_TYPE_CATEGORIES at all), SC 13D/13D-A/13G/13G-A, and every
# S-1/S-3/424B1-5 variant (including amendments) stay excluded
# structurally — none of these ever appears in this set.
_INCLUDED_FORM_TYPES: frozenset[str] = frozenset({"10-K", "10-Q"})
_EARNINGS_CATEGORY = "earnings_or_results"

# Reuses edgar_rules.EIGHT_K_ITEM_CATEGORIES's own real values exactly —
# never a duplicated literal. regulation_fd_disclosure (7.01) and
# other_material_event (8.01) are deliberately absent.
_EIGHT_K_APPROVED_CATEGORIES: frozenset[str] = frozenset({
    "material_agreement", "acquisition_or_disposition", "earnings_or_results",
    "financing_or_debt", "governance_or_management_change",
})


def _resolve_company_name(krx_code: str) -> str | None:
    """Read-only lookup against the real tracked-company registry — never
    a raw, unverified name. Matches on `TrackedCompany.krx_code` (the
    ticker, for SEC EDGAR entries) rather than trusting `FilingEvent.
    corp_name` directly; see module docstring."""
    for company in get_tracked_companies_for_source(_SOURCE_NAME, active_only=False):
        if company.krx_code == krx_code:
            return company.name
    return None


def _resolve_single_approved_8k_item(matched_rules: tuple[str, ...] | None) -> tuple[str, str] | None:
    """Returns (event_category, item_number) when `matched_rules` (the
    same "category:8-K item X.XX" shape edgar_rules.refine_8k_evaluation()
    already produces) resolves to EXACTLY ONE distinct approved category
    — reuses edgar_rules.items_from_matched_rules() for the actual
    parsing, never a duplicated regex/string-split. Returns None when
    matched_rules is missing, empty, contains no recognized item number,
    contains only excluded categories (7.01/8.01/anything unrecognized),
    or resolves to more than one distinct approved category (ambiguous —
    never chosen arbitrarily; see module docstring for the later-design
    options this defers)."""
    if not matched_rules:
        return None

    item_numbers = edgar_rules.items_from_matched_rules(list(matched_rules))
    approved_items_by_category: dict[str, list[str]] = {}
    for item in item_numbers:
        category = edgar_rules.EIGHT_K_ITEM_CATEGORIES.get(item)
        if category in _EIGHT_K_APPROVED_CATEGORIES:
            approved_items_by_category.setdefault(category, []).append(item)

    if len(approved_items_by_category) != 1:
        return None

    (category, items), = approved_items_by_category.items()
    return category, "/".join(items)


def map_edgar_filing_to_candidate(
    filing: FilingEvent,
    matched_rules: tuple[str, ...] | None = None,
) -> FilingDerivedNewsCandidate | None:
    """Pure function, no I/O. Returns None for any excluded/unmapped
    event, an unresolvable company identity, an ambiguous/absent 8-K
    item classification, or a wrongly-routed non-EDGAR FilingEvent —
    never raises, never fabricates a value. `matched_rules` is ignored
    entirely for 10-K/10-Q (determined from bare form type alone) and
    for every already-excluded form type."""
    if filing.source_name != _SOURCE_NAME:
        return None

    normalized_form = edgar_rules.normalize_form_type(filing.pblntf_ty)

    if normalized_form == "8-K":
        resolved = _resolve_single_approved_8k_item(matched_rules)
        if resolved is None:
            return None
        event_category, item_descriptor = resolved
        source_form_code = f"8-K item {item_descriptor}"
    elif normalized_form in _INCLUDED_FORM_TYPES:
        event_category = _EARNINGS_CATEGORY
        source_form_code = normalized_form
    else:
        return None

    if not validate_filing_event_category(FilingSourceSystem.EDGAR, event_category):
        return None

    company_name = _resolve_company_name(filing.stock_code)
    if company_name is None:
        return None

    filing_date = filing.filed_at or filing.rcept_dt
    normalized_title = dedup.normalize_title(filing.report_nm)
    # Format: "{SOURCE}:{issuer_identifier}:{doc_id}:{filing_date}:
    # {dedup.normalize_title(title_native) or ''}" — issuer_identifier +
    # doc_id together already uniquely identify this exact filing within
    # this source (see dart_filing_candidate_adapter.py's own module
    # docstring for why the title component alone is NOT relied upon for
    # uniqueness); the normalized title is kept only as an optional,
    # non-load-bearing diagnostic suffix. Deterministic: built only from
    # this filing's own already-resolved local fields, never a network
    # lookup or a random/incrementing value.
    dedupe_key = f"EDGAR:{filing.corp_code}:{filing.rcept_no}:{filing_date}:{normalized_title}"

    return FilingDerivedNewsCandidate(
        doc_id=filing.rcept_no,
        source_system=FilingSourceSystem.EDGAR,
        company_name=company_name,
        issuer_identifier=filing.corp_code,
        filing_date=filing_date,
        title_native=filing.report_nm,
        event_category=event_category,
        provenance=FilingProvenance(
            source_system=FilingSourceSystem.EDGAR,
            retrieved_at=filing.retrieved_at,
            source_form_code=source_form_code,
        ),
        dedupe_key=dedupe_key,
        status=FilingCandidateStatus.SHADOW,
        # The raw value FilingEvent already stores (EdgarClient.
        # filing_index_url()'s accession-directory URL) — NOT the
        # improved, primary-document-preferring link src/ui/components/
        # radar_card.py::_public_source_url() computes for display;
        # reusing that logic here would require importing a UI module
        # (which transitively imports streamlit), violating this
        # adapter's own no-UI-import isolation contract. A later batch
        # should decide whether to replicate that improvement here or
        # extract it into a shared, non-UI pure-function module.
        official_document_url=filing.source_url or None,
    )
