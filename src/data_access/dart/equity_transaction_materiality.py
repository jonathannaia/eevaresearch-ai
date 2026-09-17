"""Equity/treasury-transaction materiality gate (DART low-value filing
suppression, design/DART_LOW_VALUE_FILING_SUPPRESSION_DESIGN_2026_09_17.md).
A narrow noise-reduction exception for the `treasury_stock_activity` rule
category only (see dart_rules.KOREAN_KEYWORD_LEXICON) — never applied to
any other category, and never applied when a candidate's own matched_rules
already carry a second, independent category (earnings, guidance, capex,
supply/sales contract, equity/JV investment, financing, listing,
ownership_change, risk disclosure, market rumor response). That second
category is itself the real, already-recognized content the "material or
strategic exception" language calls for (a named customer, contract,
capacity, product/technology investment, capital raise, earnings, or
guidance event) — see radar_pipeline.py's own scoping check
(low_value_filing_rules.matched_rules_are_low_value_only), which this
module's caller is responsible for applying before calling this gate,
exactly mirroring how ownership_materiality.py documents the same
responsibility for its own `ownership_change:`-only scoping.

Mirrors ownership_materiality.py's own two-decision shape exactly, and
deliberately reuses its threshold/extractor rather than building a
parallel scoring model:

1. A control/ownership-change marker (ownership_materiality.
   MATERIAL_OWNERSHIP_MARKERS — tender offer, pledge/collateral,
   merger/share-swap, controlling-shareholder change) or a founder/CEO/
   controlling-holder marker (FOUNDER_OR_CONTROLLING_HOLDER_MARKERS
   below) present in the title or excerpt always routes to a human look
   (`material_marker`), regardless of transaction size.
2. Otherwise, if a before/after ownership percentage can be extracted
   from the excerpt via ownership_materiality.extract_ownership_delta_pp
   — the same two real, documented document shapes that module already
   verified against live cached excerpts, never a new, unverified regex
   over an undocumented DART shape — the delta is compared against the
   same OWNERSHIP_MATERIALITY_THRESHOLD_PP (`material_threshold`).
3. Otherwise — no marker, no extractable delta — `not_material`, per the
   same "never assume material just because parsing failed" rule
   ownership_materiality.py already established for its own gate.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.data_access.dart.ownership_materiality import (
    OWNERSHIP_MATERIALITY_THRESHOLD_PP,
    extract_ownership_delta_pp,
    find_material_marker,
)

# Standard, documented Korean securities-disclosure terms naming a
# founder/CEO/controlling shareholder as a party to a transaction — same
# "standard, not observed in the current cache" convention as
# ownership_materiality.MATERIAL_OWNERSHIP_MARKERS (see that module's own
# docstring for why this project keeps that distinction explicit rather
# than presenting every term as independently live-verified).
FOUNDER_OR_CONTROLLING_HOLDER_MARKERS: dict[str, tuple[str, ...]] = {
    "founder_or_controlling_holder_involvement": ("최대주주", "대표이사", "창업자"),
}


@dataclass(frozen=True)
class EquityTransactionMaterialityResult:
    outcome: str  # "material_marker" | "material_threshold" | "not_material"
    detail: str
    delta_percentage_points: float | None
    matched_marker: str | None = None


def find_escape_hatch_marker(text: str) -> tuple[str, str] | None:
    """Returns (category, matched_term) for the first configured escape-
    hatch marker found in the given text, or None. Checks both
    ownership_materiality's own control/ownership-change markers and this
    module's founder/controlling-holder markers — plain substring search,
    same convention as dart_rules.evaluate_report_name and
    ownership_materiality.find_material_marker."""
    marker = find_material_marker(text)
    if marker is not None:
        return marker
    for category, terms in FOUNDER_OR_CONTROLLING_HOLDER_MARKERS.items():
        for term in terms:
            if term in text:
                return category, term
    return None


def assess_equity_transaction_materiality(
    title: str,
    excerpt: str | None,
    threshold_pp: float = OWNERSHIP_MATERIALITY_THRESHOLD_PP,
) -> EquityTransactionMaterialityResult:
    """The treasury/equity-transaction materiality gate. Only meaningful
    for candidates whose matched_rules are exclusively
    `treasury_stock_activity:`-prefixed (plus, optionally, the amendment
    marker) — callers are responsible for that scoping (see
    radar_pipeline.py and low_value_filing_rules.matched_rules_are_low_
    value_only)."""
    marker = find_escape_hatch_marker(title)
    if marker is None and excerpt:
        marker = find_escape_hatch_marker(excerpt)
    if marker is not None:
        category, term = marker
        return EquityTransactionMaterialityResult(
            outcome="material_marker",
            detail=f"Equity/treasury transaction needs review — material marker matched: {category} ({term})",
            delta_percentage_points=None,
            matched_marker=f"{category}:{term}",
        )

    delta = extract_ownership_delta_pp(excerpt)
    if delta is None:
        # Extraction unavailable or the excerpt didn't match a known
        # shape — no marker either, so per the same "do not assume a
        # material change" rule ownership_materiality.py already applies,
        # this is treated as a routine, low-value internal equity
        # transaction rather than promoted on ambiguous grounds.
        return EquityTransactionMaterialityResult(
            outcome="not_material",
            detail="Not material · routine internal equity transaction",
            delta_percentage_points=None,
        )

    if delta >= threshold_pp:
        return EquityTransactionMaterialityResult(
            outcome="material_threshold",
            detail=f"Equity/treasury transaction affects free float by ≥ {threshold_pp} percentage points",
            delta_percentage_points=delta,
        )
    return EquityTransactionMaterialityResult(
        outcome="not_material",
        detail="Not material · routine internal equity transaction",
        delta_percentage_points=delta,
    )
