"""Daily News — an independent, autonomous discovery surface, entirely
separate from Radar Inbox (see design/DECISIONS.md for the product
clarification this follows). Reads NewsStory records via
src.data_access.daily_news.daily_news_backend.get_daily_news_repository()
(JSON by default, unless EDGE_DB_BACKEND selects sqlite/postgres — see
the Daily News durability workstream) and EditorialStory records via
src.ui.components.editorial_coverage.get_visible_editorial_stories() —
never CandidateSignal/FilingEvent or any Radar-owned file.

Unified feed presentation (design/DECISIONS.md): issuer and editorial
stories render as one reverse-chronological feed, newest published_at
first, each card carrying a compact "Company news"/"Market news" label
so provenance stays visible without a separate large section heading.
This is a presentation-only merge — the two repositories, their own
matching/freshness/cap/dedup rules, and their own persisted data stay
completely separate and untouched; _merge_feed_items() below only ever
combines two already-independently-filtered lists in memory, at render
time, for display order.

The issuer card shows exactly six fields, per the approved scope:
company name; publisher; a visible source-type label (design/
DECISIONS.md, source-attribution pass — see _SOURCE_CLASS_LABELS below)
resolved from the story's own already-typed NewsSourceReference.
source_class, never inferred or guessed; local publication time;
headline; a short Eeva-authored summary (or nothing, for an original-
language story — see below); and a direct "Read original source" link.
Every story produced by this pipeline today is still
SourceClass.OFFICIAL_COMPANY (see daily_news_pipeline.py — no non-
official source is wired in by this pass), so the source-type label
reads "Official company source" on every currently-real card; the label
itself is what keeps this honest once/if that ever changes. Deliberately
still excluded from this page: ranking/dedup data, internal status,
technical failures, and any Radar terminology — that detail lives only
in daily_news_admin.py.

The public page shows only issuer stories published within a rolling,
inclusive 7*24-hour window (compared in UTC; naive timestamps treated as
UTC, matching src.logic.formatting.days_ago()'s own convention) — a
company selector (all companies with any persisted PUBLISHED story, not
only currently-recent ones) narrows this further. Older stories stay in
the underlying store untouched; this page only ever hides them, never
deletes anything. Editorial stories keep their own separate 72-hour
freshness window and per-source/total caps (editorial_pipeline.py),
entirely independent of the issuer 7-day window — see
_merge_feed_items() for how the two independently-filtered lists are
combined for display only.

When a specific company is selected: issuer stories are those whose
company_name matches; editorial stories are those whose matched_companies
contains that company (a valid, real EditorialStory field — never
inferred). A theme-only editorial story with no matched company is only
ever visible in the default "All companies" view.

Per-company stale-feed fallback (design/DECISIONS.md): the default "All
companies" view stays strictly limited to the 7-day window for issuer
stories — it never shows an older issuer story. But selecting one
specific company whose own persisted issuer stories are all older than 7
days (e.g. right after a previously-broken feed's first successful
ingestion) no longer shows an empty state indistinguishable from "this
company has never published anything" — it shows a clear notice plus
that company's own latest available official stories instead, each
still carrying its real, publisher-provided `published_at` date (never
`created_at`/`retrieved_at`/discovery time — those are never read for
this decision, only ever `sources[0].published_at`, exactly as before
this pass). This fallback is issuer-only: it is never triggered or
satisfied by editorial stories, and activating it never waives editorial
stories' own independent 72-hour freshness window. A company with zero
persisted PUBLISHED issuer stories and zero matching editorial stories
still gets the original, true empty state.
"""
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from src.config.settings import Settings, get_settings
from src.data_access.daily_news import daily_news_backend
from src.logic.formatting import fmt_datetime_local
from src.models.daily_news_models import EditorialStory, NewsStory, NewsStoryStatus, SourceClass
from src.ui.components.editorial_coverage import get_visible_editorial_stories, render_editorial_card
from src.ui.components.empty_state import empty_state
from src.ui.components.section import section_header

_FRESHNESS_WINDOW_DAYS = 7
_ALL_COMPANIES_OPTION = "All companies"

# Source-attribution pass (design/DECISIONS.md): exact, approved
# user-facing labels for each existing SourceClass value — no new
# category invented, no wording beyond what was explicitly approved.
_SOURCE_CLASS_LABELS: dict[SourceClass, str] = {
    SourceClass.OFFICIAL_COMPANY: "Official company source",
    SourceClass.REGULATORY_FILING: "Regulatory filing",
    SourceClass.PRESS_RELEASE_WIRE: "Press-release wire",
    SourceClass.INDEPENDENT_JOURNALISM: "Independent journalism",
}
_OFFICIAL_SUBTITLE = "Company updates from official sources."
_MIXED_SUBTITLE = "Tracked-company and thematic coverage from official and editorial sources."


def _published_stories(settings: Settings) -> list[NewsStory]:
    stories = daily_news_backend.get_daily_news_repository(settings).load_stories().values()
    published = [s for s in stories if s.status == NewsStoryStatus.PUBLISHED]
    return sorted(published, key=lambda s: s.sources[0].published_at if s.sources else "", reverse=True)


def _company_options(stories: list[NewsStory]) -> list[str]:
    return [_ALL_COMPANIES_OPTION] + sorted({s.company_name for s in stories})


def _stories_for_company(stories: list[NewsStory], company_name: str) -> list[NewsStory]:
    return [s for s in stories if s.company_name == company_name]


def _editorial_stories_for_company(
    stories: tuple[EditorialStory, ...], company_name: str
) -> list[EditorialStory]:
    return [s for s in stories if company_name in s.matched_companies]


def _feed_sort_key(kind: str, item: NewsStory | EditorialStory) -> str:
    if kind == "issuer":
        return item.sources[0].published_at if item.sources else ""
    return item.published_at


def _merge_feed_items(
    issuer_items: list[NewsStory], editorial_items: list[EditorialStory] | tuple[EditorialStory, ...],
) -> list[tuple[str, NewsStory | EditorialStory]]:
    """Presentation-only merge (design/DECISIONS.md, unified Daily News
    feed) — combines two already-independently-filtered lists (issuer's
    own 7-day/fallback rules already applied; editorial's own 72-hour/
    cap rules already applied) into one reverse-chronological list, by
    each item's own type-appropriate published_at field. Never re-derives
    or waives either lane's own freshness/cap decision — this function
    only ever reorders items both lanes already decided to show."""
    tagged: list[tuple[str, NewsStory | EditorialStory]] = (
        [("issuer", s) for s in issuer_items] + [("editorial", s) for s in editorial_items]
    )
    tagged.sort(key=lambda pair: _feed_sort_key(*pair), reverse=True)
    return tagged


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
    # Source-attribution pass (design/DECISIONS.md): resolved only from
    # the story's own already-typed source_class — an unrecognized
    # enum-shaped value (should never occur; every SourceClass member is
    # mapped above) degrades to the raw stored value rather than
    # inventing a label or crashing the card.
    source_type_label = _SOURCE_CLASS_LABELS.get(source.source_class, source.source_class.value)

    with st.container(border=True):
        st.markdown('<span class="er-status-tag er-tag-neutral">Company news</span>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="er-muted" style="margin-top:0.3rem;">{story.company_name} · {source.publisher} · {source_type_label} · {local_time}</div>',
            unsafe_allow_html=True,
        )
        headline = story.original_title if story.translation_unavailable else story.headline
        st.markdown(f"**{headline}**")

        if story.translation_unavailable:
            st.caption("Translation unavailable — original text shown above.")
        elif story.eeva_summary:
            st.write(story.eeva_summary)

        st.markdown(f"[Read original source →]({source.url})")


def _page_subtitle(recent_stories: list[NewsStory]) -> str:
    """Source-attribution pass (design/DECISIONS.md), future-safe
    subtitle strategy: computed from the same 7-day, all-companies
    `recent_stories` set the default view itself shows — an empty set is
    treated as official-only (the conservative default; nothing visible
    contradicts it). Never claims every non-official category is
    "editorial" — the per-card source-type label above is what carries
    the actual category distinction; this subtitle only ever picks
    between the two approved, generic sentences."""
    all_official = all(
        source.source_class == SourceClass.OFFICIAL_COMPANY
        for story in recent_stories for source in story.sources
    )
    return _OFFICIAL_SUBTITLE if all_official else _MIXED_SUBTITLE


def render() -> None:
    """Unified Daily News feed (design/DECISIONS.md): one page, one
    "Daily News" heading, issuer and editorial stories interleaved into
    one reverse-chronological list. Each lane applies its own existing,
    unmodified rules first (issuer: 7-day window + per-company stale-feed
    fallback; editorial: 72-hour window + per-source/total caps, via
    get_visible_editorial_stories()) — only the already-decided display
    lists are merged, via _merge_feed_items(), never the underlying
    filtering logic itself."""
    settings = get_settings()
    all_stories = _published_stories(settings)
    recent_for_subtitle = _recent_stories(all_stories)
    visible_editorial = get_visible_editorial_stories(settings)

    st.markdown('<div class="er-page-title">Daily News</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="er-muted">{_page_subtitle(recent_for_subtitle)}</div>',
        unsafe_allow_html=True,
    )

    selected_company = st.selectbox("Companies", options=_company_options(all_stories), index=0)

    st.markdown(
        '<div class="er-muted">Showing tracked coverage from the past 7 days.</div>',
        unsafe_allow_html=True,
    )

    section_header("Latest")

    fallback_notice: str | None = None

    if selected_company == _ALL_COMPANIES_OPTION:
        # Strictly 7-day-recent, always — never falls back to an older
        # issuer story, regardless of any single company's own fallback
        # below. Every visible editorial story (including a valid
        # theme-only story with no matched company) is included here.
        issuer_items = _recent_stories(all_stories)
        editorial_items: list[EditorialStory] | tuple[EditorialStory, ...] = visible_editorial
    else:
        company_stories = _stories_for_company(all_stories, selected_company)
        recent = _recent_stories(company_stories)
        editorial_items = _editorial_stories_for_company(visible_editorial, selected_company)

        if recent:
            issuer_items = recent
        elif company_stories:
            # This company has persisted PUBLISHED issuer stories, just
            # none within the 7-day window (e.g. right after a
            # previously-broken feed's first successful ingestion) —
            # show its latest available official stories instead of an
            # indistinguishable empty state, with a clear neutral notice.
            # company_stories is already newest-first (inherited from
            # _published_stories()'s own sort). This fallback is
            # issuer-only: editorial_items above already applied its own
            # independent 72-hour window and is never widened here.
            issuer_items = company_stories
            fallback_notice = (
                f'<div class="er-muted">No {selected_company} official updates were published in the last 7 days. '
                "Showing the latest available official updates.</div>"
            )
        else:
            # No persisted PUBLISHED issuer story at all for this
            # company — issuer_items stays empty; any matching editorial
            # stories still render below via the merged list.
            issuer_items = []

    merged = _merge_feed_items(issuer_items, editorial_items)

    if not merged:
        # No persisted PUBLISHED story at all for this company — a true
        # empty state, never implying older coverage exists.
        if selected_company == _ALL_COMPANIES_OPTION:
            empty_state("No recent company updates in the last 7 days.")
        else:
            empty_state(f"No recent updates for {selected_company} in the last 7 days.")
        return

    if fallback_notice:
        st.markdown(fallback_notice, unsafe_allow_html=True)

    for kind, item in merged:
        if kind == "issuer":
            _render_card(item)
        else:
            render_editorial_card(item)
