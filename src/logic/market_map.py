"""Pure grouping logic for the Dashboard Market Map (Phase E1,
design/DASHBOARD_MARKET_MAP_PHASE_E.md). Reads only
src/config/tracked_companies.py — never a second theme/company mapping —
and preserves valid multi-theme membership (Samsung, SK Hynix) by listing a
company under every theme it belongs to rather than picking one "primary"
theme, per the Phase E report's explicit recommendation.
"""
from __future__ import annotations

from src.config.tracked_companies import TrackedCompany, get_tracked_companies


def group_companies_by_theme(
    theme_slugs_in_order: list[str], companies: tuple[TrackedCompany, ...] | None = None,
) -> dict[str, list[TrackedCompany]]:
    """Returns {theme_slug: [companies]} for every slug in
    `theme_slugs_in_order`, each company appearing once per theme it
    belongs to (not just its first). Companies keep their existing
    TRACKED_COMPANIES relative order within each theme group. A company
    whose `themes` includes a slug not in `theme_slugs_in_order` simply
    doesn't get a group for that slug — there are only five known themes
    today, and every tracked company's themes are drawn from that set."""
    if companies is None:
        companies = get_tracked_companies(active_only=True)
    grouped: dict[str, list[TrackedCompany]] = {slug: [] for slug in theme_slugs_in_order}
    for company in companies:
        for slug in company.themes:
            if slug in grouped:
                grouped[slug].append(company)
    return grouped


def company_selection_key(company: TrackedCompany) -> str:
    """A stable identifier for "which tile was clicked" — (source, krx_code)
    is unique per TRACKED_COMPANIES entry today (confirmed by the registry's
    own construction), unlike `name` alone, which theoretically could
    collide."""
    return f"{company.source}::{company.krx_code}"


def find_company_by_selection_key(key: str, companies: tuple[TrackedCompany, ...] | None = None) -> TrackedCompany | None:
    if companies is None:
        companies = get_tracked_companies(active_only=True)
    return next((c for c in companies if company_selection_key(c) == key), None)


# Region <-> source mapping for the Regional Brief — the same three real
# filing sources this app has anywhere (src/config/tracked_companies.py's
# own `source` field), plus China, which has none (Phase E report, section C).
REGION_SOURCE: dict[str, str] = {
    "United States": "SEC EDGAR",
    "South Korea": "OpenDART / DART",
    "Japan": "EDINET",
}
REGIONS_WITH_COVERAGE = tuple(REGION_SOURCE.keys())
REGIONS_ALL = (*REGIONS_WITH_COVERAGE, "China")

_SOURCE_REGION = {source: region for region, source in REGION_SOURCE.items()}


def jurisdiction_for_source(source_name: str) -> str | None:
    """Reader-facing data-integrity pass (design/DECISIONS.md) — the
    inverse of REGION_SOURCE above, so any real, official-source-backed
    record (a Signal, a Theme evidence item) can display its filing
    jurisdiction from the same one real source-name field it already
    carries, never a guess. Returns None for an unrecognized source name
    — the caller omits the jurisdiction rather than inventing one."""
    return _SOURCE_REGION.get(source_name)
