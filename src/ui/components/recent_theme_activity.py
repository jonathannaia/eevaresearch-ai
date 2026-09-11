"""Recent Theme Activity — Dashboard replacement for the Market Map tile
grid (design/DECISIONS.md). Supersedes commit ee685cb ("Redesign Market
Map as a grouped tile mosaic") cleanly rather than reverting it: that
component's code stays intact and available in git history, it is simply
no longer called from the Dashboard.

A compact, single-column, bounded (at most MAX_ROWS) list of themes with
real, recent (last WINDOW_DAYS) filing/Daily News activity — grouped by
the same already-reliable `theme_slug` field FilingEvent/NewsStory
already carry (`company.themes[0]` at discovery time, see
src/config/tracked_companies.py and each provider's own
scan_service.py/daily_news_pipeline.py). No new data source: this reads
the exact same two repositories src/ui/components/recently_updated.py
already reads (backend_factory.get_filing_event_repository per real
source, daily_news_backend.get_daily_news_repository), re-aggregated by
theme instead of flattened to one row per item. Filtering, grouping,
ordering, and fail-closed theme-name resolution are all delegated to the
pure src.logic.recent_theme_activity.build_recent_theme_activity — this
module only loads/adapts the two real sources into ThemeActivityItem and
renders the result.

Presented explicitly as recent activity, never as priority, ranking,
sentiment, or investment relevance — the only number shown is "N item(s)
in the last 14 days," never a score. Theme names come only from
ctx.theme_repository.get_all_themes() (the same real, permanent taxonomy
Market Map itself read — see that component's own former docstring in
git history) — never list_published_themes()'s much smaller curated
ResearchTheme set, which uses a completely different id namespace with
no reliable mapping to a plain theme_slug (confirmed by inspection:
themes_research.py's only query-param route, `theme_id`, resolves against
ResearchTheme.id via repository.get_published_theme(theme_id), not
against the taxonomy slugs this rollup groups by).

Corrective pass (design/DECISIONS.md): an earlier version of this module
rendered a per-row "Explore <theme name> in Themes ->" link for every
row, each pointing at the identical unfiltered Themes index — misleading,
since the wording implied a theme-specific destination that does not
exist (no taxonomy-slug-aware route exists on the Themes page at all, as
established above). There is now exactly ONE component-level, generic
"Browse all themes ->" link, rendered once regardless of row count — the
same honest, single-link pattern Market Map itself already used for the
same "browse the full universe" handoff
(`st.page_link(themes_page, label="+N more — view all in Themes →")`) —
never a fabricated per-theme deep link into a namespace that does not
actually correspond to this data.

Renders nothing at all (no heading, no subtitle, no empty-state filler)
when zero themes qualify — the same fail-closed discipline every other
real-data-only Dashboard module already follows (Theme Health, Priority
Signals)."""
from __future__ import annotations

import html
from datetime import date, datetime, timezone

import streamlit as st

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.daily_news import daily_news_backend
from src.logic.formatting import fmt_date, fmt_datetime_local
from src.logic.market_map import REGION_SOURCE
from src.logic.recent_theme_activity import ThemeActivityItem, ThemeActivityRow, build_recent_theme_activity
from src.logic.source_link import public_source_url
from src.models.models import FilingEvent
from src.ui.ui import get_page

WINDOW_DAYS = 14
MAX_ROWS = 5


def _esc(value: object) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return _as_utc(datetime.fromisoformat(raw))
    except ValueError:
        return None


def _parse_filing_date(raw: str) -> date | None:
    """Same tolerant parser as recently_updated.py's/regional_brief.py's
    own private copy — duplicated here rather than imported, matching
    this codebase's own existing precedent of each component keeping its
    own copy rather than sharing a private symbol across modules (see
    recently_updated.py's own docstring for the same rationale).
    FilingEvent.rcept_dt is each source's own raw date string — DART's
    unconverted "YYYYMMDD", EDGAR/EDINET's dashed "YYYY-MM-DD"."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw.replace("-", ""), "%Y%m%d").date()
    except ValueError:
        return None


def _filing_timestamp(filing: FilingEvent) -> datetime | None:
    parsed = _parse_iso(filing.filed_at)
    if parsed is not None:
        return parsed
    filing_date = _parse_filing_date(filing.rcept_dt)
    if filing_date is not None:
        return _as_utc(datetime.combine(filing_date, datetime.min.time()))
    return _parse_iso(filing.retrieved_at)


def _filing_display_date(filing: FilingEvent) -> str:
    if filing.filed_at and _parse_iso(filing.filed_at) is not None:
        return fmt_datetime_local(filing.filed_at)
    filing_date = _parse_filing_date(filing.rcept_dt)
    if filing_date is not None:
        return fmt_date(filing_date.isoformat())
    if filing.retrieved_at and _parse_iso(filing.retrieved_at) is not None:
        return fmt_datetime_local(filing.retrieved_at)
    return ""


def _load_filing_items(settings: Settings) -> list[ThemeActivityItem]:
    items: list[ThemeActivityItem] = []
    for source in REGION_SOURCE.values():
        try:
            filings = backend_factory.get_filing_event_repository(settings, source).load_filing_events()
        except Exception:  # noqa: BLE001 — fail closed; one source's error must never take down the rollup
            continue
        for filing in filings:
            if not filing.theme_slug:
                continue
            timestamp = _filing_timestamp(filing)
            if timestamp is None:
                continue
            items.append(ThemeActivityItem(
                theme_slug=filing.theme_slug, company_name=filing.corp_name, item_type="Filing",
                timestamp=timestamp, display_date=_filing_display_date(filing),
                # EDINET-safety fix (design/DECISIONS.md): see
                # src.logic.source_link.public_source_url's own docstring —
                # rewrites a raw, key-required EDINET API URL to the public
                # disclosure portal root; every other source passes through
                # unchanged.
                source_url=public_source_url(filing.source_url) or None,
            ))
    return items


def _load_daily_news_items(settings: Settings) -> list[ThemeActivityItem]:
    items: list[ThemeActivityItem] = []
    try:
        stories = daily_news_backend.get_daily_news_repository(settings).load_stories()
    except Exception:  # noqa: BLE001 — fail closed; Daily News unavailability must never take down the rollup
        return items
    for story in stories.values():
        if not story.theme_slug or not story.sources:
            continue
        source_ref = story.sources[0]
        timestamp = _parse_iso(source_ref.published_at)
        if timestamp is None:
            continue
        items.append(ThemeActivityItem(
            theme_slug=story.theme_slug, company_name=story.company_name, item_type="Daily News",
            timestamp=timestamp, display_date=fmt_datetime_local(source_ref.published_at), source_url=source_ref.url or None,
        ))
    return items


def _render_row(row: ThemeActivityRow) -> None:
    """Renders one theme's row: name, honest count, most-recent item
    line, and its own conditional "View ->" link to that item's real
    source_url. No theme-specific Themes-page link here — see
    render_recent_theme_activity's own single, generic "Browse all
    themes ->" link, rendered once for the whole component instead (see
    module docstring's "Corrective pass" note for why)."""
    with st.container(border=True, key=f"card-recent-theme-activity-{row.theme_slug}"):
        item_word = "item" if row.count == 1 else "items"
        st.markdown(
            '<div style="display:flex; align-items:baseline; justify-content:space-between; gap:var(--space-2); flex-wrap:wrap;">'
            f'<div class="er-card-title" style="font-size:0.92rem;">{_esc(row.theme_name)}</div>'
            f'<div class="er-muted" style="font-size:0.78rem; white-space:nowrap;">{row.count} {item_word} in the last 14 days</div>'
            "</div>"
            f'<div class="er-muted" style="font-size:0.82rem; margin-top:0.2rem;">'
            f"{_esc(row.most_recent.company_name)} · {_esc(row.most_recent.item_type)} · {_esc(row.most_recent.display_date)}</div>",
            unsafe_allow_html=True,
        )
        if row.most_recent.source_url:
            with st.container(key=f"cta-tertiary-rta-view-{row.theme_slug}"):
                st.link_button("View →", row.most_recent.source_url, width="stretch")


def render_recent_theme_activity(ctx, settings: Settings) -> None:
    theme_names_by_slug = {t.slug: t.name for t in ctx.theme_repository.get_all_themes()}
    items = _load_filing_items(settings) + _load_daily_news_items(settings)
    rows = build_recent_theme_activity(items, theme_names_by_slug, window_days=WINDOW_DAYS, max_rows=MAX_ROWS)
    if not rows:
        return

    st.markdown('<div class="er-section-label">Recent Theme Activity</div>', unsafe_allow_html=True)
    st.markdown('<div class="er-muted">Where tracked coverage has moved recently</div>', unsafe_allow_html=True)

    for row in rows:
        _render_row(row)

    themes_page = get_page("themes")
    if themes_page is not None:
        with st.container(key="cta-tertiary-rta-browse-all-themes"):
            st.page_link(themes_page, label="Browse all themes →")
