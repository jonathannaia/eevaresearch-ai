"""Editorial story data access + card rendering — Editorial Daily News
(design/DECISIONS.md). Zero import of NewsStory, daily_news_pipeline.py,
daily_news_store.py, or anything else that owns the issuer lane — reads
only EditorialStory via daily_news_backend.get_editorial_story_repository()
and editorial_pipeline.select_visible_editorial_stories()'s own pure
display-time windowing (72h freshness, 5-per-source cap, 20 total).

Unified Daily News feed (design/DECISIONS.md): this module no longer
renders its own section/heading — get_visible_editorial_stories()
returns the already-filtered, already-capped EditorialStory list, and
render_editorial_card() renders one card's content; src.ui.pages.
daily_news.render() merges that list with the issuer list and calls
render_editorial_card() per item, interleaved, in one unified feed. The
underlying filtering (72h freshness, per-source/total caps) is
unchanged — only where the section heading and per-item loop used to
live has moved.

Each card shows only: a compact "Market news" item-type label, headline,
publisher, published timestamp, the feed's own extractive excerpt
(omitted entirely when unusable — never a fabricated fallback sentence,
and never rendered without its own "Excerpt provided by {publisher}"
attribution line immediately above it, so it is never mistaken for
EevaResearch-authored text), matched company/theme tags, and a direct
outbound link to the canonical source URL. No generated summary, no
"why it matters," no forecast, no recommendation."""
from __future__ import annotations

import html

import streamlit as st

from src.config.settings import Settings
from src.data_access.daily_news import daily_news_backend
from src.data_access.daily_news.editorial_pipeline import select_visible_editorial_stories
from src.logic.formatting import fmt_datetime_local
from src.models.daily_news_models import EditorialStory

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


def get_visible_editorial_stories(settings: Settings) -> tuple[EditorialStory, ...]:
    """Unchanged filtering: load every persisted EditorialStory via the
    existing repository factory, then apply editorial_pipeline's own
    72-hour freshness + per-source/total display caps — the exact same
    call sequence render_editorial_coverage() used to make internally.
    Returns the already-decided display list for the caller (daily_news.
    render()) to merge with the issuer list; applies no additional
    filtering of its own."""
    repository = daily_news_backend.get_editorial_story_repository(settings)
    stories = repository.load_stories()
    return select_visible_editorial_stories(stories)


def render_editorial_card(story: EditorialStory) -> None:
    """One editorial card's content — extracted, unchanged in substance,
    from the former render_editorial_coverage()'s own per-item loop body.
    Only addition: a compact 'Market news' item-type label (unified Daily
    News feed, design/DECISIONS.md), styled with the same neutral
    er-status-tag/er-tag-neutral pattern already used elsewhere in this
    app — no new CSS, no change to the excerpt/attribution/tag content
    below it."""
    with st.container(border=True, key=f"card-editorial-{story.id}"):
        st.markdown('<span class="er-status-tag er-tag-neutral">Market news</span>', unsafe_allow_html=True)
        local_time = fmt_datetime_local(story.published_at) if story.published_at else ""
        st.markdown(
            f'<div class="er-muted" style="margin-top:0.3rem;">{_esc(story.publisher)} · Editorial · {_esc(local_time)}</div>',
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
