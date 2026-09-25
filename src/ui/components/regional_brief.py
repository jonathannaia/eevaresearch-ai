"""Regional Brief (Phase E1, design/DASHBOARD_MARKET_MAP_PHASE_E.md; redesign
v2 table treatment) — a compact, per-region tabbed table of real, dated
tracked-issuer filing events. Not market news or a market summary (the
app has neither — see the Phase E report, section B): United States/South
Korea/Japan each show up to 3 real, recent FilingEvents from that region's
existing filing source via `backend_factory.get_filing_event_repository`
(the same read-only, backend-aware accessor `radar_inbox.py` already uses)
— no new provider, network call, or cache format. China shows a flat,
honest "not connected" state — there is no CNINFO/HKEX adapter and no
tracked China issuer anywhere in the registry, so nothing here is invented
for it.

Materiality (redesign v2, non-negotiable policy): a filing whose Radar
candidate already resolved to CandidateStatus.NOT_MATERIAL — e.g. a
treasury-only DART disposal the existing equity-transaction gate marked
routine — is excluded from every region here, via
src.logic.filing_visibility (the persisted status only; no materiality is
computed on this page). Previously this component rendered bare
FilingEvents with no such gate, so a suppressed filing could surface on
the Dashboard while Filings hid it.

Columns are only ever real FilingEvent fields: form type (EDGAR/EDINET's
own stored form code; DART stores none, so that cell is left blank —
never a placeholder), issuer, venue, filed date, and the public source
link. A non-EDINET row shows no securities code (an existing, tested
decision); an EDINET row shows the 4-digit public code and the curated
type label exactly as before.
"""
from __future__ import annotations

from datetime import date, datetime

import streamlit as st

from src.config.settings import Settings
from src.data_access import backend_factory
from src.logic import filing_display
from src.logic.filing_visibility import not_material_rcept_nos

from src.logic.formatting import fmt_date
from src.logic.market_map import REGION_SOURCE
from src.logic.source_link import public_source_url
from src.models.models import FilingEvent

# (filings for a source, that source's NOT_MATERIAL receipt ids)
SourceReads = tuple[list[FilingEvent], frozenset[str]]
from src.ui.components.primitives import cjk_html, esc, lang_attr, venue_badge_html
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


def _load_recent_filings(
    source: str, settings: Settings, preloaded: "SourceReads | None" = None,
) -> list[FilingEvent]:
    """Read-only, fail-closed exactly like radar_inbox.py's own repository
    reads — a misconfigured/unreachable backend degrades to an empty list
    for this region only, never a raw exception surfaced to the page.
    NOT_MATERIAL candidates' filings are subtracted before the top-N cut.

    Phase 2B: `preloaded` lets the Dashboard supply the (filings,
    suppressed) pair it already read for this source, so the same
    source-wide data is not loaded once per region. When it is omitted —
    every other caller — this function loads exactly as it always has.
    The filtering, ordering and top-N cut below are identical either
    way."""
    if preloaded is not None:
        filings, suppressed = preloaded
    else:
        try:
            filings = backend_factory.get_filing_event_repository(settings, source).load_filing_events()
        except Exception:  # noqa: BLE001 — fail closed; never leak a raw connection/config error into the UI
            return []
        suppressed = not_material_rcept_nos(settings, source)
    dated = [(f, _parse_rcept_date(f.rcept_dt)) for f in filings if f.rcept_no not in suppressed]
    dated = [(f, d) for f, d in dated if d is not None]
    dated.sort(key=lambda pair: pair[1], reverse=True)
    return [f for f, _ in dated[:MAX_ITEMS_PER_REGION]]


def _row_html(filing: FilingEvent) -> str:
    parsed = _parse_rcept_date(filing.rcept_dt)
    date_label = fmt_date(parsed.isoformat()) if parsed else filing.rcept_dt
    # EDINET-safety fix (design/DECISIONS.md): public_source_url() rewrites
    # a raw, key-required EDINET API URL to the public disclosure portal
    # root; every other source's URL passes through unchanged.
    safe_url = public_source_url(filing.source_url)
    link_html = (
        f'<a class="er-feed-link" href="{esc(safe_url)}" target="_blank" rel="noopener noreferrer">Open ↗</a>'
        if safe_url else ""
    )

    is_edinet = filing.source_name == filing_display.EDINET_SOURCE_NAME
    title_text = filing_display.edinet_type_label(filing) if is_edinet else filing.report_nm
    form_code = (filing.pblntf_ty or "").strip() if not filing.source_name.startswith("OpenDART") else ""
    form_html = f'<span class="er-status-tag er-tag-mono er-tag-theme">{esc(form_code)}</span>' if form_code else ""
    issuer_html = cjk_html(filing.corp_name, filing.original_language)
    if is_edinet:
        code = filing_display.edinet_display_securities_code(filing.stock_code)
        if code:
            issuer_html += f' <span class="er-mono er-mono-muted">{esc(code)}</span>'
    # A DART title is Korean end to end, so the whole cell carries lang;
    # an EDINET label mixes an English type name with the Japanese
    # original, so only its Japanese run is tagged.
    title_attr = lang_attr(filing.original_language) if is_edinet is False else ""
    title_html = esc(title_text) if title_attr else cjk_html(title_text, filing.original_language)
    return (
        "<tr>"
        f'<td class="er-brief-form">{form_html}</td>'
        f'<td><div class="er-brief-issuer">{issuer_html}</div>'
        f'<div class="er-brief-title"{title_attr}>{title_html}</div></td>'
        f"<td>{venue_badge_html(filing.source_name)}</td>"
        f'<td class="er-mono er-brief-date">{esc(date_label)}</td>'
        f'<td class="er-brief-link">{link_html}</td>'
        "</tr>"
    )


def _render_region_tab(
    region: str, settings: Settings, preloaded_by_source: "dict[str, SourceReads] | None" = None,
) -> None:
    source = REGION_SOURCE[region]
    preloaded = (preloaded_by_source or {}).get(source)
    filings = _load_recent_filings(source, settings, preloaded)
    if not filings:
        st.markdown(
            '<div class="er-muted" style="margin-top:0.3rem;">No recent tracked-issuer disclosures available.</div>',
            unsafe_allow_html=True,
        )
    else:
        rows = "".join(_row_html(f) for f in filings)
        st.markdown(
            '<table class="er-table er-brief"><thead><tr>'
            "<th>Form</th><th>Issuer</th><th>Venue</th><th>Filed</th><th>Source</th>"
            f"</tr></thead><tbody>{rows}</tbody></table>",
            unsafe_allow_html=True,
        )
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


def render_regional_brief(
    settings: Settings, preloaded_by_source: "dict[str, SourceReads] | None" = None,
) -> None:
    """`preloaded_by_source` (Phase 2B, additive and optional) maps a
    source name to the (filings, not-material ids) pair the caller
    already read. Supplied, no repository is constructed here at all;
    omitted, behavior is exactly as before. Rendered rows, ordering,
    links, headings and the empty state are identical either way."""
    with st.container(key="card-regional-brief"):
        st.markdown(
            '<div class="er-section-label" style="margin-top:0;">Regional Brief</div>'
            '<div class="er-muted" style="margin-top:-0.4rem; margin-bottom:0.4rem;">Recent issuer disclosures from tracked coverage</div>',
            unsafe_allow_html=True,
        )
        tabs = st.tabs(["United States", "South Korea", "Japan", "China"])
        with tabs[0]:
            _render_region_tab("United States", settings, preloaded_by_source)
        with tabs[1]:
            _render_region_tab("South Korea", settings, preloaded_by_source)
        with tabs[2]:
            _render_region_tab("Japan", settings, preloaded_by_source)
        with tabs[3]:
            _render_china_tab()
