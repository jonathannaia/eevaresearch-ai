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


# company name -> its themes, built once from the registry. The key is
# the issuer's legal_name, which is exactly what a filing candidate
# carries: daily_news_models.CandidateSignal documents company_name as
# "matches a real TrackedCompany.name from tracked_companies.py", and
# SEED_ISSUERS.legal_name was migrated from that same tuple losslessly
# (see src/config/issuer_registry.py). Aliases are included so a filing
# that carries a registered alternate name still resolves; an alias is
# only ever added to the map when it does not collide with a different
# issuer's name, because a wrong theme is worse than no theme.
def _company_theme_map() -> dict[str, tuple[str, ...]]:
    by_name: dict[str, tuple[str, ...]] = {}
    for issuer in SEED_ISSUERS:
        if issuer.legal_name:
            by_name[issuer.legal_name] = issuer.themes
    for issuer in SEED_ISSUERS:
        for alias in issuer.aliases:
            if alias and alias not in by_name:
                by_name[alias] = issuer.themes
    return by_name


_COMPANY_THEMES = _company_theme_map()


def themes_for_company(company_name: str) -> tuple[str, ...]:
    """The themes a tracked company carries, for tagging a story about
    it. Returns () for a name the registry does not hold.

    FAILS OPEN, DELIBERATELY. An unrecognised name yields no themes
    rather than a guess: these tags drive what a reader is shown under
    a theme, and a filing filed under the wrong theme is worse than one
    filed under none. The match is exact — no fuzzy matching, no
    substring, no normalisation beyond stripping surrounding whitespace
    — because "Coherent Corp" and "Coherent Inc" are different
    companies and nothing here can tell which was meant.
    """
    return _COMPANY_THEMES.get((company_name or "").strip(), ())
