"""The theme tag taxonomy — the five content themes a company or a story
is tagged with ("AI Buildout", "Memory", "Space", "Photonics",
"Humanoids").

NAMING, because this repository uses the word "theme" for two different
things and the UI redesign gives them two different nav items:

  * THIS module's themes are the **tag taxonomy**: short slugs
    ("ai-buildout", "memory") carried on `Issuer.themes` /
    `TrackedCompany.themes` and on `EditorialStory.matched_themes`. They
    classify; they assert nothing. User-facing name: **Themes**.
  * `src.models.theme_registry` / `theme_store` / `theme_workspace.py` /
    `themes_research.py` own something else entirely: curated, cross-
    company research narratives with evidence, categories and a
    publication lifecycle. Those are internally called "Theme"/"theme_id"
    too, but their user-facing name is **Research Theses** — see
    themes_research.py's own docstring. Nothing in this module touches
    them.

Streamlit-free on purpose (same discipline as the rest of src/logic):
the counts below are computed from the static issuer registry, so a page
renders them without a repository, a connection, or a cache.

Counts are **tracked companies**, never events or stories: filing-derived
stories carry `matched_themes=()` (see filing_signal_bridge.py), so a
per-theme event count computed today would silently omit every filing and
report only the news lane. The reader-facing label has to say "companies"
for that reason, and a real event count needs the themes to be projected
onto filing stories first.
"""
from __future__ import annotations

from src.config.issuer_registry import SEED_ISSUERS
from src.models.issuer import Issuer

# Display name per slug. The slug is the stored value and never changes;
# this map is display only, exactly like
# src.ui.components.tier_labels.TIER_DISPLAY_LABELS. Ordered by the
# tracked-company count the registry actually carries today (AI Buildout
# 90, then the four smaller cohorts), so the nav and the index agree.
THEME_DISPLAY_NAMES: dict[str, str] = {
    "ai-buildout": "AI Buildout",
    "memory": "Memory",
    "photonics": "Photonics",
    "humanoids": "Humanoids",
    "space": "Space",
}


def theme_display_name(slug: str) -> str:
    """The display name for a theme slug, falling back to the slug
    itself — an unmapped slug shows as written rather than vanishing or
    raising."""
    return THEME_DISPLAY_NAMES.get(slug, slug)


def known_theme_slugs() -> tuple[str, ...]:
    """Every slug that appears on at least one seed issuer, in
    THEME_DISPLAY_NAMES order, with any unmapped slug appended after
    (sorted) rather than dropped — the registry is the source of truth
    for which themes exist, not this module's display map."""
    present = {slug for issuer in SEED_ISSUERS for slug in issuer.themes}
    ordered = [slug for slug in THEME_DISPLAY_NAMES if slug in present]
    return tuple(ordered + sorted(present - set(THEME_DISPLAY_NAMES)))


def companies_for_theme(slug: str) -> tuple[Issuer, ...]:
    """Seed issuers tagged with this theme, in registry order."""
    return tuple(issuer for issuer in SEED_ISSUERS if slug in issuer.themes)


def company_counts_by_theme() -> dict[str, int]:
    """slug -> number of seed issuers carrying it. A company tagged with
    two themes counts once in each, so these do not sum to the universe
    size (deliberate: `allow_multi_theme_assignment: true` in
    config/eevaresearch_theme_registry.yaml)."""
    return {slug: len(companies_for_theme(slug)) for slug in known_theme_slugs()}
