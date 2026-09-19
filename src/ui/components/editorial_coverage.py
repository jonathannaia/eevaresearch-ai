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
from src.data_access.daily_news.editorial_pipeline import (
    select_visible_editorial_stories,
    select_visible_editorial_stories_for_company,
)
from src.logic.formatting import fmt_datetime_local, fmt_time_local
from src.models.daily_news_models import EditorialStory, NewsMaterialityTier

_THEME_DISPLAY_NAMES: dict[str, str] = {
    "ai-buildout": "AI Buildout",
    "memory": "Memory",
    "space": "Space",
    "photonics": "Photonics",
    "humanoids": "Humanoids",
}

_DEFAULT_BADGE_LABEL = "Market news"

# Signals materiality classification (design/DECISIONS.md) — same
# tier-badge mapping as src.ui.pages.daily_news's own _TIER_BADGE_CLASS
# (kept as its own small, duplicated constant here rather than a shared
# import, matching this module's own "zero import of daily_news_pipeline/
# daily_news_store or anything else that owns the issuer lane" isolation
# discipline — the issuer page importing FROM this module, not the
# reverse, is the one direction already established).
_TIER_BADGE_CLASS: dict[NewsMaterialityTier, str] = {
    NewsMaterialityTier.HIGH_SIGNAL: "er-tag-high-signal",
    NewsMaterialityTier.WATCHLIST: "er-tag-watchlist",
    NewsMaterialityTier.BACKGROUND: "er-tag-neutral",
}


def _tier_badge_html(tier: NewsMaterialityTier) -> str:
    css_class = _TIER_BADGE_CLASS.get(tier, "er-tag-neutral")
    return f'<span class="er-status-tag {css_class}">{tier.value}</span>'

# Government / Public Sector Daily News lane (design/DECISIONS.md) —
# badge label derived at render time from the story's own already-
# persisted source_feed_id; no new EditorialStory field, no migration.
# Every source_id not listed here (every existing CNBC/Korea Herald id,
# and any future/unknown one) falls through to _DEFAULT_BADGE_LABEL —
# the exact same "Market news" text every editorial card shows today.
_SOURCE_BADGE_LABELS: dict[str, str] = {
    "spaceforce-news-rss": "Public sector",
    "nist-news-rss": "Policy",
}


def _badge_label(source_feed_id: str) -> str:
    return _SOURCE_BADGE_LABELS.get(source_feed_id, _DEFAULT_BADGE_LABEL)


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


def get_editorial_stories_for_company(settings: Settings, company_name: str) -> tuple[EditorialStory, ...]:
    """System-wide company-matched-news fix (design/DECISIONS.md) — the
    per-company counterpart to get_visible_editorial_stories() above.
    Same repository read; scoped via
    select_visible_editorial_stories_for_company() instead of the
    cross-company-capped select_visible_editorial_stories() — see that
    function's own docstring for why the total-20 cap is never applied
    here. Generic: works for any company in the Daily News company
    universe, no company-specific branch."""
    repository = daily_news_backend.get_editorial_story_repository(settings)
    stories = repository.load_stories()
    return select_visible_editorial_stories_for_company(stories, company_name)


def badge_label(source_feed_id: str) -> str:
    """Public alias of the per-feed item-type label (e.g. "Market news")."""
    return _badge_label(source_feed_id)


def theme_display_name(slug: str) -> str:
    return _THEME_DISPLAY_NAMES.get(slug, slug)


def render_editorial_card(story: EditorialStory, tier: NewsMaterialityTier | None = None) -> None:
    """One editorial card — redesign v2 density: time cell, item-type
    label (derived from story.source_feed_id via _badge_label, unchanged),
    tier badge, publisher · Editorial attribution, headline, the feed's
    own bounded excerpt in a serif inset (only when one exists — never a
    fallback), company/theme chips, and the canonical source link. Same
    content and pinned labels as before; only the layout moved. `tier` is
    caller-supplied; this function never reads story.materiality_tier."""
    clock, zone = fmt_time_local(story.published_at) if story.published_at else ("", "")
    with st.container(key=f"card-editorial-{story.id}"):
        time_col, body_col = st.columns([1, 11])
        with time_col:
            st.markdown(f'<div class="er-time-cell">{_esc(clock)}<span class="er-time-zone">{_esc(zone)}</span></div>', unsafe_allow_html=True)
        with body_col:
            tier_badge = f" {_tier_badge_html(tier)}" if tier is not None else ""
            st.markdown(
                '<div class="er-split-head" style="margin:0;">'
                f'<div><span class="er-status-tag er-tag-neutral">{_esc(_badge_label(story.source_feed_id))}</span>{tier_badge}</div>'
                f'<div class="er-muted">{_esc(story.publisher)} · Editorial</div></div>',
                unsafe_allow_html=True,
            )
            st.markdown(f'<div class="er-signal-headline">{_esc(story.headline)}</div>', unsafe_allow_html=True)
            if story.excerpt:
                st.markdown(
                    f'<div class="er-inset"><div class="er-inset-label">Excerpt provided by {_esc(story.publisher)}</div>'
                    f'<div class="er-excerpt">{_esc(story.excerpt)}</div></div>',
                    unsafe_allow_html=True,
                )
            tag_html = _tag_chips_html(story.matched_companies, story.matched_themes)
            st.markdown(
                f'<div class="er-signal-foot"><div>{tag_html}</div>'
                f'<a class="er-feed-link" href="{_esc(story.source_url)}" target="_blank" rel="noopener noreferrer">Read original source →</a></div>',
                unsafe_allow_html=True,
            )
