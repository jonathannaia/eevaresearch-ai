"""Region/jurisdiction mapping shared across several real-data Dashboard
and Themes modules — src/ui/components/regional_brief.py,
src/ui/components/recently_updated.py, src/ui/components/cards.py, and
src/ui/pages/themes_research.py all import from this module.

Dashboard triage redesign (design/DECISIONS.md): this module previously
also held the Market Map tile grid's own grouping/selection helpers
(group_companies_by_theme, company_selection_key,
find_company_by_selection_key) — removed here as dead code once the
Market Map component (src/ui/components/market_map.py, its one and only
caller) was deleted from the Dashboard in favor of Recent Theme Activity
(src/ui/components/recent_theme_activity.py). REGION_SOURCE/
jurisdiction_for_source below are unrelated to that removal — they are
genuinely still-used, unmodified helpers, kept in this file rather than
moved, since several already-live, unrelated modules import them from
here today and a rename/relocation is a separate, out-of-scope decision.
"""
from __future__ import annotations

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
