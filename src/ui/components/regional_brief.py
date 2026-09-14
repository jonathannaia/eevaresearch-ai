"""Regional Brief (Phase E1, design/DASHBOARD_MARKET_MAP_PHASE_E.md) — a
compact, per-region tabbed view of real, dated tracked-issuer filing
events. Not market news or a market summary (the app has neither — see
the Phase E report, section B): United States/South Korea/Japan each show
up to 3 real, recent FilingEvents from that region's existing filing
source via `backend_factory.get_filing_event_repository` (the same
read-only, backend-aware accessor `radar_inbox.py` already uses) — no new
provider, network call, or cache format. China shows a flat, honest
"not connected" state — there is no CNINFO/HKEX adapter and no tracked
China issuer anywhere in the registry, so nothing here is invented for it.
"""
from __future__ import annotations

from datetime import date, datetime

import streamlit as st

from src.config.settings import Settings
from src.data_access import backend_factory
from src.logic.formatting import fmt_date
from src.logic.market_map import REGION_SOURCE
from src.logic.source_link import public_source_url
from src.models.models import FilingEvent
from src.ui.ui import get_page

MAX_ITEMS_PER_REGION = 3


def _parse_rcept_date(raw: str) -> date | None:
    """Same tolerant parser as radar_inbox.py's own `_parse_rcept_date` —
    FilingEvent.rcept_dt is each source's raw date string (DART's
    unconverted "YYYYMMDD", EDGAR/EDINET's dashed ISO "YYYY-MM-DD")."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw.replace("-", ""), "%Y%m%d").date()
    except ValueError:
        return None


def _load_recent_filings(source: str, settings: Settings) -> list[FilingEvent]:
    """Read-only, fail-closed exactly like radar_inbox.py's own repository
    reads — a misconfigured/unreachable backend degrades to an empty list
    for this region only, never a raw exception surfaced to the page."""
    try:
        filings = backend_factory.get_filing_event_repository(settings, source).load_filing_events()
    except Exception:  # noqa: BLE001 — fail closed; never leak a raw connection/config error into the UI
        return []
    dated = [(f, _parse_rcept_date(f.rcept_dt)) for f in filings]
    dated = [(f, d) for f, d in dated if d is not None]
    dated.sort(key=lambda pair: pair[1], reverse=True)
    return [f for f, _ in dated[:MAX_ITEMS_PER_REGION]]


def _render_filing_item(filing: FilingEvent) -> None:
    """Compact single-row-plus-metadata layout (Dashboard layout-
    tightening pass, design/DECISIONS.md): title on its own line,
    company/date/source and "View source document ->" together on one
    tight second line, the link floated to the right rather than
    occupying a separate third line as before — same content, same real
    URL, no new markup element added beyond a flex wrapper."""
    parsed = _parse_rcept_date(filing.rcept_dt)
    date_label = fmt_date(parsed.isoformat()) if parsed else filing.rcept_dt
    # EDINET-safety fix (design/DECISIONS.md): public_source_url() rewrites
    # a raw, key-required EDINET API URL to the public disclosure portal
    # root; every other source's URL passes through unchanged.
    safe_url = public_source_url(filing.source_url)
    link_html = (
        f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer" '
        f'style="color:var(--text-2); font-size:0.76rem; text-decoration:underline; white-space:nowrap;">'
        "View source document ↗</a>"
    ) if safe_url else ""
    st.markdown(
        f'<div class="er-row" style="border-bottom:none; padding:0 0 var(--space-2) 0;">'
        f'<div class="er-card-title" style="font-size:0.88rem;">{filing.report_nm}</div>'
        f'<div style="display:flex; align-items:baseline; justify-content:space-between; gap:var(--space-2); '
        f'flex-wrap:wrap; margin-top:0.1rem;">'
        f'<div class="er-muted" style="font-size:0.78rem;">{filing.corp_name} · {date_label} · {filing.source_name}</div>'
        f"{link_html}"
        f"</div></div>",
        unsafe_allow_html=True,
    )


def _render_region_tab(region: str, settings: Settings) -> None:
    st.markdown(
        '<div class="er-muted" style="font-size:0.82rem;">Recent issuer disclosures from tracked coverage</div>',
        unsafe_allow_html=True,
    )
    source = REGION_SOURCE[region]
    filings = _load_recent_filings(source, settings)
    if not filings:
        st.markdown(
            '<div class="er-muted" style="margin-top:0.3rem;">No recent tracked-issuer disclosures available.</div>',
            unsafe_allow_html=True,
        )
    else:
        for f in filings:
            _render_filing_item(f)
    radar_page = get_page("radar_inbox")
    if radar_page is not None:
        with st.container(key=f"cta-tertiary-brief-radar-{region.replace(' ', '-').lower()}"):
            st.page_link(radar_page, label="Open Radar Inbox →")


def _render_china_tab() -> None:
    st.markdown(
        '<div class="er-muted">China coverage is not connected yet.</div>'
        '<div class="er-muted" style="margin-top:0.2rem;">Eeva currently has no tracked China issuers or '
        "filing-source coverage.</div>",
        unsafe_allow_html=True,
    )


def render_regional_brief(settings: Settings) -> None:
    # Standardized to the shared .er-section-label top margin (Dashboard
    # layout-tightening pass, design/DECISIONS.md) — this section
    # previously carried its own smaller inline override, the one real
    # inconsistency in Dashboard's inter-section spacing; every other
    # section header (Recently Updated, Recent Theme Activity, Theme
    # Health) already uses the bare class.
    st.markdown('<div class="er-section-label">Regional Brief</div>', unsafe_allow_html=True)
    tabs = st.tabs(["United States", "South Korea", "Japan", "China"])
    with tabs[0]:
        _render_region_tab("United States", settings)
    with tabs[1]:
        _render_region_tab("South Korea", settings)
    with tabs[2]:
        _render_region_tab("Japan", settings)
    with tabs[3]:
        _render_china_tab()
