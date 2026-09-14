"""Signals materiality classification (design/DECISIONS.md) — pure,
deterministic, no I/O, no LLM, no opaque ranking score. Classifies an
already-admitted issuer or editorial item into exactly one
NewsMaterialityTier (HIGH_SIGNAL/WATCHLIST/BACKGROUND), reusing the same
word-boundary keyword-matching discipline
src.data_access.daily_news.editorial_matching already established for
this codebase (fixed, curated, narrow phrase tables — never a bare
overloaded single word).

Called only at story-construction time by daily_news_pipeline.py and
editorial_pipeline.py, for a NEW item being discovered right now — never
against an already-persisted story. This module never reclassifies
existing data; NewsStory.materiality_tier/EditorialStory.materiality_tier
stay None (see NewsMaterialityTier's own docstring) for every record
persisted before this module existed, by design, until a separate,
explicitly-approved backfill decision is made.

Deliberately never imports src.models.models — only
src.models.daily_news_models and source_registry.SourceCategory (both
already Daily-News-owned) — same scope-guard discipline as every other
module in this package (see tests/test_daily_news_scope_guard.py).

--- The materiality gate (approved design, any ONE qualifies) ---

A. Primary disclosure:
   A1. Regulatory filing (SourceClass.REGULATORY_FILING, or editorial
       SourceCategory.OFFICIAL_FILING).
   A2. Formal earnings/results materials — a narrow, curated phrase match
       against the headline (content-based, independent of source
       category — an earnings release reads the same whether it arrives
       via an issuer's own IR feed or editorial wire coverage).
   A3. Formal government/regulatory action — editorial
       SourceCategory.REGULATOR/REGULATOR_EXCHANGE/EXCHANGE qualifies by
       category alone (these sources' own admission policy already
       restricts them to regulator/exchange press releases); GOVERNMENT_
       POLICY/GOVERNMENT_PROCUREMENT additionally requires a formal-
       action keyword anchor (reusing the same "narrow source, still
       needs a topical anchor" discipline editorial_pipeline.py already
       applies to the NIST source specifically).
B. Quantified change: a materiality-anchor keyword (capacity, backlog,
   shipment, capex, financing, contract, deployment, order, revenue,
   guidance, pricing, ...) co-occurring with a real number/currency/
   percentage/unit pattern.
C. Taxonomy-anchored consequence: a relevance-taxonomy phrase match
   (semiconductors & equipment; AI compute & data-center infrastructure;
   memory/networking/power/cooling/packaging; wafers/materials/critical
   minerals/manufacturing capacity; capital allocation/policy/trade
   controls/supply-chain) AND a materiality-anchor keyword (same list as
   Gate B, without requiring the numeric pattern).
D. Credible editorial reporting: editorial SourceCategory.
   INDEPENDENT_NEWS, a substantive excerpt (not a one-line blurb), and
   fact-attribution language (confirmed/disclosed/revealed/filed/
   obtained documents/according to a regulatory filing/...) — a
   deterministic proxy for "new attributable facts," not true NLP fact
   extraction; documented here as a known approximation, not overclaimed
   precision.

Zero gate hits -> the item can never reach HIGH_SIGNAL (a hard, tested
invariant — see tests/test_daily_news_materiality_classification.py::
test_zero_gate_item_can_never_reach_high_signal). Among zero-gate items:
on-taxonomy (Gate C's phrase match fired, but no anchor) -> WATCHLIST;
off-taxonomy entirely -> BACKGROUND.
"""
from __future__ import annotations

import re
from functools import lru_cache

from src.data_access.daily_news.source_registry import SourceCategory
from src.models.daily_news_models import NewsMaterialityTier, SourceClass

# --- Relevance taxonomy (5 buckets, design/DECISIONS.md) ---
# Same discipline as editorial_matching.THEME_KEYWORDS: exact, approved,
# narrow standalone terms or required multi-word compounds only — never
# a bare overloaded single word. Deliberately its own, separate table
# from THEME_KEYWORDS (that table is curated/approved for a different
# purpose — editorial admission — and this module never modifies it).
TAXONOMY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "semiconductors_and_equipment": (
        "semiconductor", "semiconductors", "wafer fab", "foundry",
        "chip fabrication", "lithography", "EUV", "semiconductor equipment",
        "wafer fabrication equipment", "etch equipment", "deposition equipment",
        "chipmaker",
    ),
    "ai_compute_and_data_center": (
        "AI data center", "AI infrastructure", "AI chip", "AI accelerator",
        "AI cloud", "GPU cluster", "hyperscaler capex", "data center capex",
        "data center", "AI compute", "inference cluster", "training cluster",
        "GPU shortage", "GPU supply", "GPU", "CUDA",
    ),
    "memory_networking_power_cooling_packaging": (
        "HBM", "DRAM", "NAND flash", "NAND chip", "memory chip", "DDR5",
        "high-bandwidth memory", "networking switch", "optical transceiver",
        "co-packaged optics", "advanced packaging", "chiplet",
        "liquid cooling", "data center power", "power delivery",
        "substation capacity", "transformer capacity", "grid interconnect",
    ),
    "wafers_materials_manufacturing_capacity": (
        "wafer supply", "silicon wafer", "critical minerals", "rare earth",
        "photoresist", "specialty gas", "manufacturing capacity",
        "fab capacity", "new fab", "capacity expansion", "wafer capacity",
    ),
    "capital_policy_trade_controls_supply_chain": (
        "export control", "entity list", "CHIPS Act", "trade restriction",
        "tariff", "supply chain constraint", "supply chain disruption",
        "capital allocation", "capex guidance", "financing round",
    ),
}

# --- Materiality anchors (Gates B & C) — the change-type vocabulary the
# user's own materiality gate names verbatim. ---
_MATERIALITY_ANCHOR_KEYWORDS: tuple[str, ...] = (
    "capacity", "backlog", "shipment", "shipments", "capex", "financing",
    "contract", "deployment", "order", "orders", "revenue", "guidance",
    "pricing", "price increase", "price cut", "expansion", "investment",
)

# --- Numeric/currency/unit patterns (Gate B only — an anchor alone is
# never enough for Gate B; a real magnitude must co-occur). ---
_NUMERIC_MAGNITUDE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\$\s?\d[\d,.]*\s?(billion|million|bn|mn|b|m)\b", re.IGNORECASE),
    re.compile(r"\d+(\.\d+)?\s?(GW|MW|gigawatts?|megawatts?)\b", re.IGNORECASE),
    re.compile(r"\d+(\.\d+)?\s?%"),
    re.compile(r"\d[\d,]*\s?(thousand|million|billion)\s?(units|wafers|chips|GPUs|servers)\b", re.IGNORECASE),
)

# --- Gate A2: formal earnings/results materials (content-based, source-
# category-independent). ---
_EARNINGS_KEYWORDS: tuple[str, ...] = (
    "financial results", "quarterly results", "reports fourth quarter",
    "reports third quarter", "reports second quarter", "reports first quarter",
    "full year results", "annual results", "earnings release",
    "fiscal year results", "reports quarterly", "earnings call",
)

# --- Gate A3: formal government/regulatory action anchor — required only
# for GOVERNMENT_POLICY/GOVERNMENT_PROCUREMENT sources (same "narrow
# source, still needs a topical anchor" discipline editorial_pipeline.py
# already applies to the NIST source specifically). ---
_FORMAL_ACTION_KEYWORDS: tuple[str, ...] = (
    "rule", "ruling", "order", "sanction", "restriction", "ban", "approve",
    "approval", "license", "revoke", "enforcement", "investigation",
    "settlement", "export control", "entity list", "tariff",
    "rate decision", "policy statement", "final rule", "proposed rule",
)

# --- Gate D: fact-attribution language — a deterministic proxy for "new
# attributable facts," not true NLP fact extraction (see module
# docstring). ---
_REPORTING_VERB_KEYWORDS: tuple[str, ...] = (
    "confirmed", "disclosed", "revealed", "according to a regulatory filing",
    "obtained documents show", "according to sources", "filed with",
    "reported exclusively",
)

# Below this, an excerpt is treated as a one-line blurb, not substantive
# reporting — a deliberately conservative, documented threshold, not a
# precise measure of journalistic depth.
_SUBSTANTIVE_EXCERPT_MIN_CHARS = 200

_GOVERNMENT_CATEGORY_REQUIRES_ANCHOR = (SourceCategory.GOVERNMENT_POLICY, SourceCategory.GOVERNMENT_PROCUREMENT)
_GOVERNMENT_CATEGORY_QUALIFIES_BY_CATEGORY_ALONE = (
    SourceCategory.REGULATOR, SourceCategory.REGULATOR_EXCHANGE, SourceCategory.EXCHANGE,
)


@lru_cache(maxsize=None)
def _boundary_pattern(phrase: str) -> re.Pattern[str]:
    return re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)


def _contains_any(text: str, phrases: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(phrase for phrase in phrases if _boundary_pattern(phrase).search(text))


def _has_numeric_magnitude(text: str) -> bool:
    return any(pattern.search(text) for pattern in _NUMERIC_MAGNITUDE_PATTERNS)


def _matched_taxonomy_buckets(text: str) -> tuple[str, ...]:
    matched: list[str] = []
    for bucket, phrases in TAXONOMY_KEYWORDS.items():
        if _contains_any(text, phrases):
            matched.append(bucket)
    return tuple(matched)


def _combined_text(title: str, description: str | None) -> str:
    return title if not description else f"{title}\n{description}"


def _classify_core(
    text: str, *, is_primary_disclosure: bool, is_independent_news: bool,
) -> tuple[NewsMaterialityTier, tuple[str, ...]]:
    reasons: list[str] = []

    if is_primary_disclosure:
        reasons.append("primary_disclosure")

    earnings_hit = _contains_any(text, _EARNINGS_KEYWORDS)
    if earnings_hit:
        reasons.append(f"formal_earnings_materials:{earnings_hit[0]}")

    anchor_hits = _contains_any(text, _MATERIALITY_ANCHOR_KEYWORDS)
    numeric_hit = _has_numeric_magnitude(text)
    if anchor_hits and numeric_hit:
        reasons.append(f"quantified_change:{anchor_hits[0]}")

    taxonomy_hits = _matched_taxonomy_buckets(text)
    if taxonomy_hits and anchor_hits:
        reasons.append(f"taxonomy_anchored_consequence:{taxonomy_hits[0]}:{anchor_hits[0]}")

    if is_independent_news:
        reporting_hit = _contains_any(text, _REPORTING_VERB_KEYWORDS)
        substantive = len((text or "").strip()) >= _SUBSTANTIVE_EXCERPT_MIN_CHARS
        if reporting_hit and substantive and (taxonomy_hits or anchor_hits):
            reasons.append(f"credible_editorial_reporting:{reporting_hit[0]}")

    if reasons:
        return NewsMaterialityTier.HIGH_SIGNAL, tuple(reasons)
    if taxonomy_hits:
        return NewsMaterialityTier.WATCHLIST, (f"on_taxonomy_no_anchor:{taxonomy_hits[0]}",)
    return NewsMaterialityTier.BACKGROUND, ("off_taxonomy_no_anchor",)


def classify_issuer_story(
    headline: str, excerpt: str | None, source_class: SourceClass,
) -> tuple[NewsMaterialityTier, tuple[str, ...]]:
    """Issuer-lane classification (daily_news_pipeline.py). SourceClass is
    always OFFICIAL_COMPANY in this pipeline today (see SourceClass's own
    docstring) — REGULATORY_FILING is handled here for forward
    compatibility only, since the type itself already allows it."""
    text = _combined_text(headline, excerpt)
    return _classify_core(
        text, is_primary_disclosure=(source_class == SourceClass.REGULATORY_FILING),
        is_independent_news=False,
    )


def classify_editorial_story(
    headline: str, excerpt: str | None, source_category: SourceCategory,
) -> tuple[NewsMaterialityTier, tuple[str, ...]]:
    """Editorial-lane classification (editorial_pipeline.py). The caller
    already has the item's own DailyNewsSourceEntry in scope at
    construction time, so its real `.category` is passed directly —
    never re-derived from source_feed_id here."""
    text = _combined_text(headline, excerpt)
    is_primary_disclosure = (
        source_category == SourceCategory.OFFICIAL_FILING
        or source_category in _GOVERNMENT_CATEGORY_QUALIFIES_BY_CATEGORY_ALONE
        or (
            source_category in _GOVERNMENT_CATEGORY_REQUIRES_ANCHOR
            and bool(_contains_any(text, _FORMAL_ACTION_KEYWORDS))
        )
    )
    return _classify_core(
        text, is_primary_disclosure=is_primary_disclosure,
        is_independent_news=(source_category == SourceCategory.INDEPENDENT_NEWS),
    )
