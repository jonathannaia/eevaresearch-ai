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
"""
from __future__ import annotations

import streamlit as st

from src.config.settings import get_settings
from src.data_access.daily_news import daily_news_store
from src.logic.formatting import fmt_datetime_local
from src.models.daily_news_models import NewsStory, NewsStoryStatus
from src.ui.components.empty_state import empty_state
from src.ui.components.section import section_header


def _published_stories(cache_dir) -> list[NewsStory]:
    stories = daily_news_store.load_stories(cache_dir).values()
    published = [s for s in stories if s.status == NewsStoryStatus.PUBLISHED]
    return sorted(published, key=lambda s: s.sources[0].published_at if s.sources else "", reverse=True)


def _render_card(story: NewsStory) -> None:
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
        '<div class="er-muted">Autonomously discovered company updates from official sources.</div>',
        unsafe_allow_html=True,
    )

    settings = get_settings()
    stories = _published_stories(settings.cache_dir)

    section_header("Latest")
    if not stories:
        empty_state("No Daily News stories yet.")
        return

    for story in stories:
        _render_card(story)
