"""Recently Updated — Dashboard Batch 1 (design/DECISIONS.md). A single,
compact, reverse-chronological feed merging real filing events from SEC
EDGAR/DART/EDINET (via the same backend_factory.
get_filing_event_repository() regional_brief.py already reads) with real
Daily News stories (via daily_news_backend.get_daily_news_repository()).
No new source, no new network call, no new cache format — purely a
different read-time merge/sort/render over two already-existing,
already-real data sources.

Sort key, per row (approved rule, design/DECISIONS.md):
  Filings: filing.filed_at when present and parseable; else filing.
    rcept_dt parsed as a source-claimed filing date; else filing.
    retrieved_at as the final fallback.
  Daily News: source.published_at exactly as stored — already resolved
    to publisher-claimed-or-retrieved at write time by
    daily_news_pipeline.py, so no further fallback is applied here.
Every parsed instant with no explicit UTC offset is treated as UTC for
sorting purposes only (same "naive input assumed UTC" convention
src.logic.formatting already documents); a bare filing date (rcept_dt)
is combined with UTC midnight for sorting only — never displayed as a
fabricated time when the source didn't supply one (see
_filing_display_date, which falls back to a date-only label in that
case).

No claim-type, confidence, strength, importance, priority, score,
summary, "why it matters", or bullish/bearish language anywhere — this
is a factual, source-attributed feed only. Fails closed per source: one
source's repository error degrades that source to zero items, never a
raised exception reaching the Dashboard (same discipline
regional_brief.py's own _load_recent_filings already uses).

Company name, title, and source URL are real, external-sourced strings
(filer/publisher-supplied) — escaped via html.escape() before being
placed inside unsafe_allow_html, matching the discipline
src/ui/components/radar_card.py and src/ui/pages/themes_research.py
already establish for the same category of data."""
from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date, datetime, timezone

import streamlit as st

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.daily_news import daily_news_backend
from src.logic.formatting import fmt_date, fmt_datetime_local
from src.logic.market_map import REGION_SOURCE
from src.models.models import FilingEvent
from src.ui.ui import get_page

PREVIEW_COUNT = 8

_FILING_SOURCE_LABEL = {
    "SEC EDGAR": "SEC EDGAR",
    "OpenDART / DART": "Korea DART",
    "EDINET": "Japan EDINET",
}


@dataclass(frozen=True)
class _Row:
    sort_key: datetime
    company_name: str
    title: str
    source_label: str
    display_date: str
    source_url: str | None


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
    """Same tolerant parser as regional_brief.py's/radar_inbox.py's own
    private _parse_rcept_date — duplicated here rather than imported,
    matching this codebase's own existing precedent of each page/
    component keeping its own copy rather than sharing a private symbol
    across modules. FilingEvent.rcept_dt is each source's own raw date
    string — DART's unconverted "YYYYMMDD", EDGAR/EDINET's dashed
    "YYYY-MM-DD"."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw.replace("-", ""), "%Y%m%d").date()
    except ValueError:
        return None


def _filing_sort_key(filing: FilingEvent) -> datetime | None:
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


def _load_filing_rows(settings: Settings) -> list[_Row]:
    rows: list[_Row] = []
    for source in REGION_SOURCE.values():
        try:
            filings = backend_factory.get_filing_event_repository(settings, source).load_filing_events()
        except Exception:  # noqa: BLE001 — fail closed; one source's error must never take down the feed
            continue
        for filing in filings:
            sort_key = _filing_sort_key(filing)
            if sort_key is None:
                continue
            rows.append(_Row(
                sort_key=sort_key,
                company_name=filing.corp_name,
                title=filing.report_nm,
                source_label=_FILING_SOURCE_LABEL.get(filing.source_name, filing.source_name),
                display_date=_filing_display_date(filing),
                source_url=filing.source_url or None,
            ))
    return rows


def _load_daily_news_rows(settings: Settings) -> list[_Row]:
    rows: list[_Row] = []
    try:
        stories = daily_news_backend.get_daily_news_repository(settings).load_stories()
    except Exception:  # noqa: BLE001 — fail closed; Daily News unavailability must never take down the feed
        return rows
    for story in stories.values():
        if not story.sources:
            continue
        source_ref = story.sources[0]
        sort_key = _parse_iso(source_ref.published_at)
        if sort_key is None:
            continue
        rows.append(_Row(
            sort_key=sort_key,
            company_name=story.company_name,
            title=story.headline,
            source_label="Daily News",
            display_date=fmt_datetime_local(source_ref.published_at),
            source_url=source_ref.url or None,
        ))
    return rows


def _render_row(row: _Row) -> None:
    company_line = f"{_esc(row.company_name)} · " if row.company_name else ""
    st.markdown(
        f'<div class="er-row" style="border-bottom:none; padding-bottom:var(--space-1);">'
        f'<div class="er-card-title" style="font-size:0.88rem;">{_esc(row.title)}</div>'
        f'<div class="er-muted" style="font-size:0.78rem;">{company_line}{_esc(row.source_label)} · {_esc(row.display_date)}</div>'
        f"</div>",
        unsafe_allow_html=True,
    )
    if row.source_url:
        st.markdown(
            f'<div class="er-muted" style="font-size:0.76rem; margin-top:-0.2rem; margin-bottom:0.3rem;">'
            f'<a href="{html.escape(row.source_url, quote=True)}" target="_blank" rel="noopener noreferrer" '
            f'style="color:var(--text-2); text-decoration:underline;">View source ↗</a></div>',
            unsafe_allow_html=True,
        )


def render_recently_updated(settings: Settings) -> None:
    st.markdown('<div class="er-section-label">Recently Updated</div>', unsafe_allow_html=True)

    rows = _load_filing_rows(settings) + _load_daily_news_rows(settings)
    rows.sort(key=lambda r: r.sort_key, reverse=True)
    shown = rows[:PREVIEW_COUNT]

    if not shown:
        st.markdown(
            '<div class="er-muted" style="margin-top:0.3rem;">No recent updates available.</div>',
            unsafe_allow_html=True,
        )
    else:
        for row in shown:
            _render_row(row)

    link_cols = st.columns(2)
    with link_cols[0]:
        filings_page = get_page("radar_inbox")
        if filings_page is not None:
            with st.container(key="cta-tertiary-recently-updated-filings"):
                st.page_link(filings_page, label="View all filings →")
    with link_cols[1]:
        daily_news_page = get_page("daily_news")
        if daily_news_page is not None:
            with st.container(key="cta-tertiary-recently-updated-daily-news"):
                st.page_link(daily_news_page, label="View all Daily News →")
