"""Daily News — an independent, autonomous discovery surface, entirely
separate from Radar Inbox (see design/DECISIONS.md for the product
clarification this follows). Reads only NewsStory records, via
src.data_access.daily_news.daily_news_backend.get_daily_news_repository()
(JSON by default, unless EDGE_DB_BACKEND selects sqlite/postgres — see
the Daily News durability workstream) — never CandidateSignal/
FilingEvent or any Radar-owned file.

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
the underlying store untouched; this page only ever hides them, never
deletes anything.

Per-company stale-feed fallback (design/DECISIONS.md): the default "All
companies" view stays strictly limited to the 7-day window — it never
shows an older story. But selecting one specific company whose own
persisted stories are all older than 7 days (e.g. right after a
previously-broken feed's first successful ingestion) no longer shows an
empty state indistinguishable from "this company has never published
anything" — it shows a clear notice plus that company's own latest
available official stories instead, each still carrying its real,
publisher-provided `published_at` date (never `created_at`/
`retrieved_at`/discovery time — those are never read for this decision,
only ever `sources[0].published_at`, exactly as before this pass). A
company with zero persisted PUBLISHED stories at all still gets the
original, true empty state.
"""
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from src.config.settings import Settings, get_settings
from src.data_access.daily_news import daily_news_backend
from src.logic.formatting import fmt_datetime_local
from src.models.daily_news_models import NewsStory, NewsStoryStatus
from src.ui.components.empty_state import empty_state
from src.ui.components.section import section_header

_FRESHNESS_WINDOW_DAYS = 7
_ALL_COMPANIES_OPTION = "All companies"


def _published_stories(settings: Settings) -> list[NewsStory]:
    stories = daily_news_backend.get_daily_news_repository(settings).load_stories().values()
    published = [s for s in stories if s.status == NewsStoryStatus.PUBLISHED]
    return sorted(published, key=lambda s: s.sources[0].published_at if s.sources else "", reverse=True)


def _company_options(stories: list[NewsStory]) -> list[str]:
    return [_ALL_COMPANIES_OPTION] + sorted({s.company_name for s in stories})


def _stories_for_company(stories: list[NewsStory], company_name: str) -> list[NewsStory]:
    return [s for s in stories if s.company_name == company_name]


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
    all_stories = _published_stories(settings)

    selected_company = st.selectbox("Companies", options=_company_options(all_stories), index=0)

    st.markdown(
        '<div class="er-muted">Showing official company updates from the past 7 days.</div>',
        unsafe_allow_html=True,
    )

    section_header("Latest")

    if selected_company == _ALL_COMPANIES_OPTION:
        # Strictly 7-day-recent, always — never falls back to an older
        # story, regardless of any single company's own fallback below.
        recent = _recent_stories(all_stories)
        if not recent:
            empty_state("No recent company updates in the last 7 days.")
            return
        for story in recent:
            _render_card(story)
        return

    company_stories = _stories_for_company(all_stories, selected_company)
    recent = _recent_stories(company_stories)

    if recent:
        for story in recent:
            _render_card(story)
        return

    if not company_stories:
        # No persisted PUBLISHED story at all for this company — a true
        # empty state, never implying older coverage exists.
        empty_state(f"No recent updates for {selected_company} in the last 7 days.")
        return

    # This company has persisted PUBLISHED stories, just none within the
    # 7-day window (e.g. right after a previously-broken feed's first
    # successful ingestion) — show its latest available official
    # stories instead of an indistinguishable empty state, with a clear
    # neutral notice above them. company_stories is already newest-first
    # (inherited from _published_stories()'s own sort), and every card
    # below still renders its own real published_at date via the same
    # _render_card() the "recent" path already uses.
    st.markdown(
        f'<div class="er-muted">No {selected_company} official updates were published in the last 7 days. '
        "Showing the latest available official updates.</div>",
        unsafe_allow_html=True,
    )
    for story in company_stories:
        _render_card(story)
