"""Coverage page query/aggregation layer — pure, Streamlit-free reads over
the static Issuer Registry (`src/config/issuer_registry.py`). No I/O, no
network, no cache access: every function here is a plain, testable
transformation over already-in-memory `Issuer` tuples, matching this
codebase's existing convention of keeping `src/logic/*.py` free of any
Streamlit or data-access import (see `signal_promotion.py`,
`review_actions.py`).

Robust to an empty or incomplete registry by construction: every field
this module reads off `Issuer` is optional/defaulted on the model itself
(`src/models/issuer.py`), and every function here does plain attribute
reads and set/list operations — no field is assumed non-empty."""
from __future__ import annotations

from dataclasses import dataclass

from src.config.issuer_registry import DISCOVERY_STUBS, SEED_ISSUERS, source_name_for_seed_issuer
from src.models.issuer import Issuer

_AMBIGUOUS_MARKER = "EXPLICITLY FLAGGED AS AMBIGUOUS"
_NO_ADAPTER_MARKER = "source adapter exists"


@dataclass(frozen=True)
class CoverageSummary:
    active_seed_count: int
    discovery_count: int
    scan_eligible_count: int
    unverified_excluded_count: int
    seed_count_by_source: dict[str, int]


def get_coverage_summary() -> CoverageSummary:
    by_source: dict[str, int] = {}
    for issuer in SEED_ISSUERS:
        source = source_name_for_seed_issuer(issuer)
        by_source[source] = by_source.get(source, 0) + 1
    return CoverageSummary(
        active_seed_count=len(SEED_ISSUERS),
        discovery_count=len(DISCOVERY_STUBS),
        # Every SEED issuer is scan-eligible and every DISCOVERY issuer is
        # not — Phase A has no partial-eligibility state. Kept as its own
        # field (rather than reusing active_seed_count directly at the call
        # site) so a future phase that changes this relationship doesn't
        # need a call-site rewrite, only a definition change here.
        scan_eligible_count=len(SEED_ISSUERS),
        unverified_excluded_count=len(DISCOVERY_STUBS),
        seed_count_by_source=by_source,
    )


def filter_seed_issuers(
    issuers: tuple[Issuer, ...] | list[Issuer] = SEED_ISSUERS,
    *,
    search: str = "",
    themes: tuple[str, ...] = (),
    layers: tuple[str, ...] = (),
    sources: tuple[str, ...] = (),
    countries: tuple[str, ...] = (),
) -> list[Issuer]:
    """Search matches legal name or primary ticker, case-insensitive.
    Every other filter is an OR-within-field, AND-across-fields set
    intersection — the same semantics `radar_inbox.py`'s filter bar already
    uses. An issuer with no theme/layer/country set simply never matches a
    non-empty theme/layer/country filter, rather than raising."""
    query = (search or "").strip().lower()
    result = list(issuers)
    if query:
        result = [
            i for i in result
            if query in (i.legal_name or "").lower() or query in (i.primary_ticker or "").lower()
        ]
    if themes:
        theme_set = set(themes)
        result = [i for i in result if theme_set & set(i.themes)]
    if layers:
        layer_set = set(layers)
        result = [i for i in result if layer_set & set(i.supply_chain_layers)]
    if sources:
        source_set = set(sources)
        result = [i for i in result if source_name_for_seed_issuer(i) in source_set]
    if countries:
        country_set = set(countries)
        result = [i for i in result if i.country_or_jurisdiction in country_set]
    return result


def get_jurisdiction_gaps() -> list[str]:
    """Distinct jurisdictions named by a discovery stub whose
    normalization_status says no source adapter exists for it — derived
    from DISCOVERY_STUBS' own text rather than a separately maintained
    list, so it can't drift out of sync with the registry data. Each
    country_or_jurisdiction value looks like "Taiwan (inferred from ticker
    suffix — unverified)"; only the leading name is kept."""
    gaps: list[str] = []
    for issuer in DISCOVERY_STUBS:
        if _NO_ADAPTER_MARKER not in issuer.normalization_status:
            continue
        name = issuer.country_or_jurisdiction.split(" (", 1)[0].strip()
        if name and name not in gaps:
            gaps.append(name)
    return gaps


def get_ambiguous_stub_labels() -> list[str]:
    """Tickers/labels for discovery stubs explicitly flagged as ambiguous
    in their own normalization_status — same derive-don't-duplicate
    approach as get_jurisdiction_gaps."""
    return [
        issuer.primary_ticker or issuer.legal_name
        for issuer in DISCOVERY_STUBS
        if _AMBIGUOUS_MARKER in issuer.normalization_status
    ]
