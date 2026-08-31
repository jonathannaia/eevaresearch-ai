"""Daily News — an independent, autonomous discovery surface, entirely
separate from Radar Inbox (see design/DECISIONS.md for the product
clarification this follows). Reads only from
src/data_access/daily_news/daily_news_store.py's own JSON store, never
CandidateSignal/FilingEvent or any Radar-owned file.

The default card shows exactly five fields, per the approved scope:
company name; official source/publisher and local publication time;
headline; a short Eeva-authored summary (or nothing, for an original-
language story — see below); and a direct "Read original source" link.
Deliberately excluded from this page: source-class labels, ranking/
dedup data, internal status, technical failures, and any Radar
terminology — that detail lives only in daily_news_admin.py.

The public page shows only stories published within a rolling, inclusive
7*24-hour window (compared in UTC; naive timestamps treated as UTC,
matching src.logic.formatting.days_ago()'s own convention) — a company
selector (all companies with any persisted PUBLISHED story, not only
currently-recent ones) narrows this further. Older stories stay in
daily_news_store.py's JSON file untouched; this page only ever hides
them, never deletes anything.
"""
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from src.config.settings import get_settings
from src.data_access.daily_news import daily_news_store
from src.logic.formatting import fmt_datetime_local
from src.models.daily_news_models import NewsStory, NewsStoryStatus
from src.ui.components.empty_state import empty_state
from src.ui.components.section import section_header

_FRESHNESS_WINDOW_DAYS = 7
_ALL_COMPANIES_OPTION = "All companies"


def _published_stories(cache_dir) -> list[NewsStory]:
    stories = daily_news_store.load_stories(cache_dir).values()
    published = [s for s in stories if s.status == NewsStoryStatus.PUBLISHED]
    return sorted(published, key=lambda s: s.sources[0].published_at if s.sources else "", reverse=True)


def _company_options(stories: list[NewsStory]) -> list[str]:
    return [_ALL_COMPANIES_OPTION] + sorted({s.company_name for s in stories})


def _elapsed_seconds(published_at: str, now: datetime) -> float | None:
    try:
        dt = datetime.fromisoformat(published_at)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds()


def _is_recent(story: NewsStory, now: datetime, window_days: int = _FRESHNESS_WINDOW_DAYS) -> bool:
    """Rolling, inclusive window compared in UTC — a story exactly
    window_days*24 hours old is included; anything older is not. Uses
    exact elapsed seconds rather than formatting.days_ago()'s own
    floor-to-integer-days rounding, which would otherwise let a story
    up to nearly window_days+1 calendar days old slip through."""
    if not story.sources or not story.sources[0].published_at:
        return False
    elapsed = _elapsed_seconds(story.sources[0].published_at, now)
    return elapsed is not None and elapsed <= window_days * 86400


def _recent_stories(stories: list[NewsStory], now: datetime | None = None) -> list[NewsStory]:
    now = now or datetime.now(timezone.utc)
    return [s for s in stories if _is_recent(s, now)]


def _render_card(story: NewsStory) -> None:
    # Text-only layout for every card, regardless of whether a validated
    # image_url/image_alt exists on the story — optional source-image
    # rendering is disabled for now (UI decision; the underlying
    # extraction/validation/storage of those fields is untouched, see
    # rss_atom_client.py / canonical_url.validate_image_url() /
    # daily_news_models.NewsSourceReference).
    source = story.sources[0]
    local_time = fmt_datetime_local(source.published_at) if source.published_at else ""

    with st.container(border=True):
        st.markdown(
            f'<div class="er-muted">{story.company_name} · {source.publisher} · {local_time}</div>',
            unsafe_allow_html=True,
        )
        headline = story.original_title if story.translation_unavailable else story.headline
        st.markdown(f"**{headline}**")

        if story.translation_unavailable:
            st.caption("Translation unavailable — original text shown above.")
        elif story.eeva_summary:
            st.write(story.eeva_summary)

        st.markdown(f"[Read original source →]({source.url})")


def render() -> None:
    st.markdown('<div class="er-page-title">Daily News</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="er-muted">Company updates from official sources.</div>',
        unsafe_allow_html=True,
    )

    settings = get_settings()
    all_stories = _published_stories(settings.cache_dir)

    selected_company = st.selectbox("Companies", options=_company_options(all_stories), index=0)

    st.markdown(
        '<div class="er-muted">Showing official company updates from the past 7 days.</div>',
        unsafe_allow_html=True,
    )

    section_header("Latest")

    scoped_stories = all_stories
    if selected_company != _ALL_COMPANIES_OPTION:
        scoped_stories = [s for s in all_stories if s.company_name == selected_company]

    recent = _recent_stories(scoped_stories)

    if not recent:
        if selected_company == _ALL_COMPANIES_OPTION:
            empty_state("No recent company updates in the last 7 days.")
        else:
            empty_state(f"No recent updates for {selected_company} in the last 7 days.")
        return

    for story in recent:
        _render_card(story)
