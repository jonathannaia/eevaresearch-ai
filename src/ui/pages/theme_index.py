"""Themes — the index of the content-theme tag taxonomy (UI redesign
item 1).

WHICH "THEME" THIS IS. The redesign's nav carries both **Themes** and
**Theses**, and this repository uses the word "theme" for both:

  * This page lists the **tag taxonomy** — the five slugs carried on
    `Issuer.themes` and `EditorialStory.matched_themes` ("ai-buildout",
    "memory", …). See src/logic/theme_taxonomy.py, which owns the
    display names and the counts.
  * `src/ui/pages/themes_research.py` is the other one: curated,
    cross-company **Research Theses** with evidence and a publication
    lifecycle. Its internal identifiers are also "Theme"/"theme_id";
    nothing on this page touches them.

Read-only and static: counts come from the issuer registry, so this page
makes no repository read, no source request and no write.

COUNTS ARE COMPANIES, NOT EVENTS, and the label says so. A per-theme
event count is the number a reader actually wants, and it is now
possible but not yet correct: `project_filing_signals()` does tag every
filing-derived story with its issuer's themes, but stories persisted
BEFORE that change still carry an empty list, because
`upsert_new_stories()` skips rows that already exist. Until
`scripts/backfill_filing_story_themes.py` has run against the store, an
event count would under-report every theme by however many filings
predate the change. This page counts the tracked universe meanwhile,
which is exact, and item 4's theme pages are where the event count
belongs.

NO THEME DETAIL PAGES YET. Per-theme pages are UI redesign item 4, which
also fixes the theme "View" links that currently open external articles.
Until those exist, each theme here lists its companies inline rather
than linking to a page that does not exist.
"""
from __future__ import annotations

import streamlit as st

from src.logic.theme_taxonomy import companies_for_theme, known_theme_slugs, theme_display_name
from src.ui import render_timing
from src.ui.components.primitives import cjk_html, esc, page_header, theme_dot_html

_PAGE_TITLE = "Themes"

# How many company names each theme card spells out before falling back
# to a "+N more" count. AI Buildout carries 90 of the 129 tracked
# issuers, so an uncapped list would bury the other four themes.
_NAMES_SHOWN = 12


def _company_names_html(slug: str) -> str:
    companies = companies_for_theme(slug)
    shown = companies[:_NAMES_SHOWN]
    # English name and native name go inside ONE span, not two siblings.
    # The container is a wrapping flex row, so two siblings are two flex
    # items and the native name wraps away from its own company — which
    # renders "FANUC CORPORATION" and "ファナック株式会社" as two separate
    # companies in the list.
    names = [
        '<span class="er-theme-company">'
        + esc(c.legal_name)
        + (f'<span class="er-theme-company-native">{cjk_html(c.native_name)}</span>' if c.native_name else "")
        + "</span>"
        for c in shown
    ]
    rest = len(companies) - len(shown)
    if rest > 0:
        names.append(f'<span class="er-theme-company er-mono-muted">+{rest} more</span>')
    return '<div class="er-theme-companies">' + "".join(names) + "</div>"


def _subtheme_labels(slug: str) -> tuple[str, ...]:
    """Subthemes present on the companies in this theme, in first-seen
    registry order. Slugs are shown as written — the registry has no
    display-name map for subthemes, and inventing one here would put
    unverified labels on the page."""
    seen: list[str] = []
    for company in companies_for_theme(slug):
        for subtheme in company.subthemes:
            if subtheme not in seen:
                seen.append(subtheme)
    return tuple(seen)


def render() -> None:
    with render_timing.data_load():
        slugs = known_theme_slugs()

    page_header(
        _PAGE_TITLE,
        "The tags every tracked company and every news story is classified against. "
        "Counts are tracked companies, not events.",
    )

    for slug in slugs:
        companies = companies_for_theme(slug)
        subthemes = _subtheme_labels(slug)
        subtheme_html = (
            '<div class="er-theme-subthemes">'
            + "".join(f'<span class="er-status-tag er-tag-theme">{esc(s)}</span>' for s in subthemes)
            + "</div>"
            if subthemes
            else ""
        )
        with st.container(border=True, key=f"card-theme-index-{slug}"):
            st.markdown(
                '<div class="er-theme-index-head">'
                f'<div class="er-card-title">{theme_dot_html(slug)}{esc(theme_display_name(slug))}</div>'
                f'<div class="er-mono er-mono-muted">{len(companies)} companies</div>'
                "</div>"
                + subtheme_html
                + _company_names_html(slug),
                unsafe_allow_html=True,
            )
