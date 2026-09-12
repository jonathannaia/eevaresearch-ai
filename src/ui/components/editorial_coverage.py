"""Editorial Coverage — Editorial Daily News v1 (design/DECISIONS.md). A
self-contained, additive section rendered after the existing issuer-
based Daily News list. Zero import of NewsStory, daily_news_pipeline.py,
daily_news_store.py, or anything else that owns the issuer lane — reads
only EditorialStory via daily_news_backend.get_editorial_story_repository()
and editorial_pipeline.select_visible_editorial_stories()'s own pure
display-time windowing (72h freshness, 5-per-source cap, 20 total).

Renders nothing at all — no heading, no subtitle, no shell, no
placeholder — unless at least one real editorial story currently passes
that windowing, matching this app's own established convention (see
e.g. Dashboard's Priority Signals / the Federal Register Policy Monitor
Pilot's Policy developments section).

Each card shows only: headline, publisher, published timestamp, the
feed's own extractive excerpt (omitted entirely when unusable — never a
fabricated fallback sentence, and never rendered without its own
"Excerpt provided by {publisher}" attribution line immediately above it,
so it is never mistaken for EevaResearch-authored text), matched
company/theme tags, and a direct outbound link to the canonical source
URL. No generated summary, no "why it matters," no forecast, no
recommendation."""
from __future__ import annotations

import html

import streamlit as st

from src.config.settings import get_settings
from src.data_access.daily_news import daily_news_backend
from src.data_access.daily_news.editorial_pipeline import select_visible_editorial_stories
from src.logic.formatting import fmt_datetime_local
from src.ui.components.section import section_header

_THEME_DISPLAY_NAMES: dict[str, str] = {
    "ai-buildout": "AI Buildout",
    "memory": "Memory",
    "space": "Space",
    "photonics": "Photonics",
    "humanoids": "Humanoids",
}


def _esc(value: object) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


def _tag_chips_html(matched_companies: tuple[str, ...], matched_themes: tuple[str, ...]) -> str:
    labels = list(matched_companies) + [
        _THEME_DISPLAY_NAMES.get(slug, slug) for slug in matched_themes
    ]
    chips = "".join(
        f'<span class="er-chip er-chip-inference" style="margin-right:0.4rem;">{_esc(label)}</span>'
        for label in labels
    )
    return chips


def render_editorial_coverage() -> None:
    settings = get_settings()
    repository = daily_news_backend.get_editorial_story_repository(settings)
    stories = repository.load_stories()
    visible = select_visible_editorial_stories(stories)
    if not visible:
        return

    section_header(
        "Editorial Coverage",
        "Independent business and technology reporting matched to tracked companies and themes.",
    )

    for story in visible:
        with st.container(border=True, key=f"card-editorial-{story.id}"):
            local_time = fmt_datetime_local(story.published_at) if story.published_at else ""
            st.markdown(
                f'<div class="er-muted">{_esc(story.publisher)} · Editorial · {_esc(local_time)}</div>',
                unsafe_allow_html=True,
            )
            st.markdown(f'<div class="er-card-title">{_esc(story.headline)}</div>', unsafe_allow_html=True)
            if story.excerpt:
                st.markdown(
                    f'<div class="er-muted" style="font-size:0.72rem; margin-top:0.4rem;">'
                    f'Excerpt provided by {_esc(story.publisher)}</div>',
                    unsafe_allow_html=True,
                )
                st.write(story.excerpt)
            tag_html = _tag_chips_html(story.matched_companies, story.matched_themes)
            if tag_html:
                st.markdown(f'<div style="margin-top:0.3rem;">{tag_html}</div>', unsafe_allow_html=True)
            st.markdown(f"[Read original source →]({story.source_url})")
