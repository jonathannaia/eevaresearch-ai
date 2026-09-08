"""Phase A ontology foundation — a static, descriptive vocabulary only
(design/ISSUER_REGISTRY_FOUNDATION.md). Nothing here changes dashboard
grouping, reclassifies an existing tracked company, or creates a scanning
rule; it exists so `Issuer.themes`/`Issuer.supply_chain_layers` values have
something real to validate against, and so real, already-known
classification disagreements are recorded as explicit unresolved metadata
instead of being silently picked one way or the other.

`PRIMARY_THEMES` matches the five dashboard themes exactly
(data/seed/themes.json's top-level `slug` values). `SUPPLY_CHAIN_LAYERS`
is new — the detailed ontology layers named in the product-direction brief
this phase was approved from. The two vocabularies are deliberately not
merged: a theme is a dashboard-facing, cross-layer research grouping; a
layer is a supply-chain position a company occupies, and a single company
can span multiple layers within one theme (or the same layer across
multiple themes) — collapsing them would lose that distinction."""
from __future__ import annotations

from dataclasses import dataclass

PRIMARY_THEMES: tuple[str, ...] = (
    "ai-buildout",
    "humanoids",
    "space",
    "memory",
    "photonics",
)

SUPPLY_CHAIN_LAYERS: tuple[str, ...] = (
    "application",
    "ai-model",
    "software-infrastructure",
    "cloud-infrastructure",
    "compute-hardware",
    "memory",
    "interconnect",
    "advanced-packaging",
    "semiconductor-foundry",
    "semiconductor-equipment",
    "semiconductor-materials",
    "critical-minerals",
    "power-infrastructure",
    "thermal-management",
    "security",
    "edge-physical-ai",
)


def is_valid_theme(slug: str) -> bool:
    return slug in PRIMARY_THEMES


def is_valid_layer(slug: str) -> bool:
    return slug in SUPPLY_CHAIN_LAYERS


@dataclass(frozen=True)
class KnownCategoryConflict:
    """One explicit, unresolved classification disagreement — recorded so
    it's visible and trackable, never silently picked one way by this
    phase's ontology work. `status` is deliberately the same fixed string
    for every entry in this phase (nothing gets resolved here)."""

    subject: str
    description: str
    status: str = "Unresolved — documented, not silently changed (Phase A)"


KNOWN_CATEGORY_CONFLICTS: tuple[KnownCategoryConflict, ...] = (
    KnownCategoryConflict(
        subject="MRVL / Marvell Technology, Inc.",
        description=(
            "Registered under theme 'photonics' in "
            "src/config/tracked_companies.py (chosen to fill the SEC EDGAR "
            "photonics-pilot slot during a prior radar-expansion batch). The "
            "user-provided portfolio-map seed list instead groups MRVL under "
            "'AI compute/cloud'. Both are defensible — Marvell sells into both "
            "optical/interconnect and AI-compute markets — this is a real "
            "classification disagreement, not a data error, and is left "
            "unresolved in Phase A."
        ),
    ),
    KnownCategoryConflict(
        subject="TSEM / Tower Semiconductor Ltd",
        description=(
            "Registered under theme 'ai-buildout' (subtheme "
            "'compute-accelerators') in src/config/tracked_companies.py. The "
            "portfolio-map seed list instead groups TSEM under "
            "'Foundry/manufacturing'. Tower is a specialty foundry, so the "
            "seed list's grouping is arguably the more literal fit; the "
            "registry's current theme reflects which EDGAR pilot slot it was "
            "added to fill, not a foundry-vs-compute judgment. Left unresolved "
            "in Phase A."
        ),
    ),
    KnownCategoryConflict(
        subject="'networking-interconnect' / 'interconnect-switching' naming overlap",
        description=(
            "data/seed/themes.json defines 'networking-interconnect' as an "
            "ai-buildout subtheme (switches/NICs/fabric linking accelerators) "
            "and separately defines 'interconnect-switching' as a photonics "
            "subtheme (optical switching fabric) — two similarly-named, "
            "differently-scoped subthemes under different themes. This is the "
            "demo-catalog taxonomy, already found (in an earlier theme-model "
            "audit) to be architecturally disconnected from the real "
            "DART/EDGAR/EDINET pipeline. This ontology module deliberately "
            "does not attempt to reconcile it — SUPPLY_CHAIN_LAYERS above uses "
            "a single 'interconnect' layer instead, at a coarser granularity "
            "than either subtheme."
        ),
    ),
    KnownCategoryConflict(
        subject="Kioxia Holdings — seed ticker '285A.T' vs. registry krx_code '285A0'",
        description=(
            "The user-provided seed list gives Kioxia's ticker as '285A.T' (a "
            "conventional Tokyo-ticker notation). src/config/tracked_companies.py "
            "stores krx_code='285A0' for the same company, sourced from a live "
            "EDINET code-list pull and documented there as EDINET's own "
            "5-character securities code (4-character TSE code plus a trailing "
            "check digit), not the bare 4-character TSE ticker. These are very "
            "likely the same underlying instrument, but that equivalence has "
            "not been independently re-verified — flagged rather than assumed."
        ),
    ),
)
