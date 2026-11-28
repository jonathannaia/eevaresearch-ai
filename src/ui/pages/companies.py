"""Companies — the reader-facing index of the tracked company universe
(UI redesign item 1).

Read-only and static: every row comes from `src.config.issuer_registry`'s
`SEED_ISSUERS` through the same pure query layer Coverage uses, so this
page performs no scan, no source request, no repository read and no
write. It is the *reader's* list ("which companies does EevaResearch
follow, and what is each one tagged with") — distinct from Coverage,
which is the operator's observability map over the same universe and
additionally shows coverage/lifecycle state, discovery stubs and
jurisdiction gaps.

DISCOVERY STUBS ARE DELIBERATELY ABSENT. `DISCOVERY_STUBS` are
unverified portfolio-map candidates, structurally excluded from every
scan path (see src/models/issuer.py's CoverageState docstring). Listing
them on a reader-facing "Companies" page would present an unreviewed
candidate as covered. Coverage shows them, labelled as such; this page
shows the seed universe only.

NO COMPANY LINKS YET. Per-company detail pages are UI redesign item 3;
until they exist every row here is text, not a link. A row that looked
clickable and did nothing, or that routed to a filtered feed the reader
did not ask for, would be worse than a plain list.

NATIVE NAMES. The redesign asks for the English name with the Korean or
Japanese name smaller underneath. `Issuer.native_name` is populated for
13 of the 129 seed issuers — the EDINET entries whose native legal name
was captured from EDINET's own code list — and is empty for every
EDGAR and DART issuer, Korean ones included. Those 13 get their native
name; the rest show the English name alone. Nothing here romanizes,
translates or reconstructs a name that the registry does not hold.
"""
from __future__ import annotations

import streamlit as st

from src.config.issuer_registry import SEED_ISSUERS, source_name_for_seed_issuer
from src.logic.theme_taxonomy import theme_display_name
from src.models.issuer import Issuer
from src.ui import render_timing
from src.ui.components.empty_state import empty_state
from src.ui.components.primitives import cjk_html, esc, page_header, theme_dot_html, venue_badge_html

_PAGE_TITLE = "Companies"

_SEARCH_KEY = "companies-search"
_REGION_KEY = "companies-region"
_THEME_KEY = "companies-theme"
_ALL_LABEL = "All"

# Jurisdiction string -> the short reader-facing region label. The
# registry's own `country_or_jurisdiction` values carry a "(listing
# exchange)" qualifier that is accurate but not worth a column heading;
# an unmapped value falls through to its own text rather than being
# bucketed into a guess.
_REGION_LABELS: dict[str, str] = {
    "United States (listing exchange)": "United States",
    "Japan (listing exchange)": "Japan",
    "South Korea (listing exchange)": "South Korea",
}
# Display order for the region groups — largest cohort first, matching
# the universe the registry actually holds (US 90, Japan 27, Korea 12).
_REGION_ORDER: tuple[str, ...] = ("United States", "Japan", "South Korea")


def _region_label(issuer: Issuer) -> str:
    raw = issuer.country_or_jurisdiction or ""
    return _REGION_LABELS.get(raw, raw or "Unknown")


def _region_sort_key(label: str) -> tuple[int, str]:
    return (_REGION_ORDER.index(label), "") if label in _REGION_ORDER else (len(_REGION_ORDER), label)


def _matches_query(issuer: Issuer, query: str) -> bool:
    """Name, ticker, native name and registry aliases — the same four
    fields the command palette searches, so a reader who finds a company
    with ⌘K also finds it by typing here."""
    if not query:
        return True
    needle = query.strip().lower()
    haystack = [issuer.legal_name, issuer.primary_ticker or "", issuer.native_name or "", *issuer.aliases]
    return any(needle in field.lower() for field in haystack if field)


def _company_row_html(issuer: Issuer) -> str:
    native = (
        f'<div class="er-company-native">{cjk_html(issuer.native_name)}</div>'
        if issuer.native_name
        else ""
    )
    themes = "".join(
        f'<span class="er-company-theme">{theme_dot_html(slug)}{esc(theme_display_name(slug))}</span>'
        for slug in issuer.themes
    )
    ticker = esc(issuer.primary_ticker or "—")
    exchange = esc(issuer.primary_exchange or "—")
    return (
        '<div class="er-company-row">'
        f'<div class="er-company-identity"><div class="er-company-name">{esc(issuer.legal_name)}</div>{native}</div>'
        f'<div class="er-company-ticker">{ticker}<span class="er-company-exchange">{exchange}</span></div>'
        f'<div class="er-company-venue">{venue_badge_html(source_name_for_seed_issuer(issuer))}</div>'
        f'<div class="er-company-themes">{themes}</div>'
        "</div>"
    )


def render() -> None:
    with render_timing.data_load():
        issuers = tuple(SEED_ISSUERS)

    regions = sorted({_region_label(i) for i in issuers}, key=_region_sort_key)
    theme_slugs = sorted({slug for i in issuers for slug in i.themes}, key=lambda s: theme_display_name(s))

    page_header(
        _PAGE_TITLE,
        f"{len(issuers)} companies across {len(regions)} markets, tagged against "
        f"{len(theme_slugs)} themes. Filings for these issuers are what the Feed is built from.",
    )

    filter_cols = st.columns([3, 2, 2])
    with filter_cols[0]:
        query = st.text_input(
            "Search", placeholder="Company name or ticker",
            label_visibility="collapsed", key=_SEARCH_KEY,
        )
    with filter_cols[1]:
        region = st.selectbox("Market", [_ALL_LABEL, *regions], key=_REGION_KEY)
    with filter_cols[2]:
        theme = st.selectbox(
            "Theme", [_ALL_LABEL, *theme_slugs],
            format_func=lambda s: s if s == _ALL_LABEL else theme_display_name(s),
            key=_THEME_KEY,
        )

    shown = [
        i for i in issuers
        if _matches_query(i, query)
        and (region == _ALL_LABEL or _region_label(i) == region)
        and (theme == _ALL_LABEL or theme in i.themes)
    ]

    if not shown:
        empty_state(
            "No companies match",
            "Clear the search box or widen the market and theme filters.",
        )
        return

    if len(shown) != len(issuers):
        st.markdown(
            f'<div class="er-muted">{len(shown)} of {len(issuers)} companies</div>',
            unsafe_allow_html=True,
        )

    by_region: dict[str, list[Issuer]] = {}
    for issuer in shown:
        by_region.setdefault(_region_label(issuer), []).append(issuer)

    for label in sorted(by_region, key=_region_sort_key):
        group = by_region[label]
        st.markdown(
            f'<div class="er-list-head"><div class="er-section-label">{esc(label)}</div>'
            f'<div class="er-mono er-mono-muted">{len(group)}</div></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="er-company-list">' + "".join(_company_row_html(i) for i in group) + "</div>",
            unsafe_allow_html=True,
        )
