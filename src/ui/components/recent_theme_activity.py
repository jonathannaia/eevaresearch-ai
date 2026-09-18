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
from src.logic import filing_display
from src.logic.filing_visibility import not_material_rcept_nos
from src.logic.formatting import fmt_date, fmt_datetime_local
from src.logic.market_map import REGION_SOURCE
from src.logic.recent_theme_activity import ThemeActivityItem, ThemeActivityRow, build_recent_theme_activity
from src.logic.source_link import public_source_url
from src.models.models import FilingEvent
from src.ui.components.primitives import theme_dot_html
from src.ui.ui import get_page

WINDOW_DAYS = 14
MAX_ROWS = 4


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
        # Redesign v2 materiality policy: a NOT_MATERIAL candidate's filing
        # never counts toward, or becomes the latest item of, a theme here.
        suppressed = not_material_rcept_nos(settings, source)
        for filing in filings:
            if not filing.theme_slug or filing.rcept_no in suppressed:
                continue
            timestamp = _filing_timestamp(filing)
            if timestamp is None:
                continue
            is_edinet = filing.source_name == filing_display.EDINET_SOURCE_NAME
            items.append(ThemeActivityItem(
                theme_slug=filing.theme_slug, company_name=filing.corp_name, item_type="Filing",
                timestamp=timestamp, display_date=_filing_display_date(filing),
                # EDINET-safety fix (design/DECISIONS.md): see
                # src.logic.source_link.public_source_url's own docstring —
                # rewrites a raw, key-required EDINET API URL to the public
                # disclosure portal root; every other source passes through
                # unchanged.
                source_url=public_source_url(filing.source_url) or None,
                # EDINET filing-source usability fix (design/
                # EDINET_FILING_SOURCE_USABILITY_DESIGN.md): compact-tier
                # display fields, EDINET only — see ThemeActivityItem's
                # own docstring.
                edinet_title=filing_display.edinet_type_label(filing) if is_edinet else "",
                edinet_stock_code=filing_display.edinet_display_securities_code(filing.stock_code) if is_edinet else "",
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
            theme_slug=story.theme_slug, company_name=story.company_name, item_type="Signals",
            timestamp=timestamp, display_date=fmt_datetime_local(source_ref.published_at), source_url=source_ref.url or None,
        ))
    return items


def _render_row(row: ThemeActivityRow) -> None:
    """Renders one theme's row as a compact single-row card: theme name +
    most-recent item line on the left, the honest "N item(s) in the last
    14 days" count and a small "View ->" affordance stacked on the right
    (Dashboard layout-tightening pass, design/DECISIONS.md) — replaces
    the former full-width st.link_button, which rendered as a tall,
    empty-looking input-style box. "View ->" stays the exact same
    st.link_button widget (only its width/wrapper changed, de-emphasized
    via the existing sitewide cta-tertiary treatment in assets/
    styles.css — same mechanism recently_updated.py's own tertiary links
    already use) pointing at that item's real source_url — never a
    fabricated per-theme "theme detail" link: no taxonomy-slug-aware
    route exists on the Themes page at all (see module docstring's
    "Corrective pass" note); this row's only honest click target is the
    real item it names. No theme-specific Themes-page link here — see
    render_recent_theme_activity's own single, generic "Browse all
    themes ->" link, rendered once for the whole component instead."""
    with st.container(key=f"card-recent-theme-activity-{row.theme_slug}"):
        item_word = "item" if row.count == 1 else "items"
        left, right = st.columns([3.4, 1.3], vertical_alignment="center")
        with left:
            # EDINET filing-source usability fix (design/
            # EDINET_FILING_SOURCE_USABILITY_DESIGN.md): compact-tier —
            # the 4-digit public securities code appended to the company
            # name, and (only when a curated/native EDINET title is
            # available) a second small line showing it. Empty/"" for
            # every non-EDINET item, so this is a no-op for EDGAR/DART/
            # Daily News rows.
            company_html = _esc(row.most_recent.company_name)
            if row.most_recent.edinet_stock_code:
                company_html += f" ({_esc(row.most_recent.edinet_stock_code)})"
            title_html = (
                f'<div class="er-muted er-rta-title">{_esc(row.most_recent.edinet_title)}</div>'
                if row.most_recent.edinet_title else ""
            )
            st.markdown(
                f'<div class="er-card-title er-rta-name">{theme_dot_html(row.theme_slug)}{_esc(row.theme_name)}</div>'
                f'<div class="er-muted er-rta-latest">'
                f"Latest · {company_html} · {_esc(row.most_recent.item_type)} · {_esc(row.most_recent.display_date)}</div>"
                f"{title_html}",
                unsafe_allow_html=True,
            )
        with right:
            st.markdown(
                f'<div class="er-rta-count er-mono-muted">{row.count} {item_word} in the last 14 days</div>',
                unsafe_allow_html=True,
            )
            if row.most_recent.source_url:
                with st.container(key=f"cta-tertiary-rta-view-{row.theme_slug}"):
                    st.link_button("View →", row.most_recent.source_url)


def load_theme_activity_rows(ctx, settings: Settings, max_rows: int = MAX_ROWS) -> list[ThemeActivityRow]:
    """The real 14-day rollup — exposed (redesign v2) so the Dashboard's
    "Theme items · 14d" tile can total every theme's count while the list
    below still shows only the top MAX_ROWS. Same inputs, same pure
    builder, no new data source."""
    theme_names_by_slug = {t.slug: t.name for t in ctx.theme_repository.get_all_themes()}
    items = _load_filing_items(settings) + _load_daily_news_items(settings)
    return build_recent_theme_activity(items, theme_names_by_slug, window_days=WINDOW_DAYS, max_rows=max_rows)


def render_recent_theme_activity(ctx, settings: Settings, rows: list[ThemeActivityRow] | None = None) -> None:
    if rows is None:
        rows = load_theme_activity_rows(ctx, settings)
    if not rows:
        return

    total = sum(r.count for r in rows)
    st.markdown(
        '<div class="er-split-head"><div class="er-section-label er-tight">Theme activity</div>'
        f'<div class="er-split-meta">14d · {total} items</div></div>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="er-muted er-section-sub">Where tracked coverage has moved recently</div>', unsafe_allow_html=True)

    for row in rows[:MAX_ROWS]:
        _render_row(row)

    themes_page = get_page("themes")
    if themes_page is not None:
        with st.container(key="cta-tertiary-rta-browse-all-themes"):
            st.page_link(themes_page, label="Browse all themes →")
