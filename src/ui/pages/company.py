"""Company — the reusable per-ticker research template. Renamed/repurposed
from ticker_detail.py (brief §4: Company is a new, not-in-nav route reached
by clicking a ticker, at /company/{ticker}). Reads `symbol` from
st.query_params (defaults to the DEMO ticker if absent).

This phase validates the template's layout, not example data quality: the
fundamental/technical snapshot fields render as explicit placeholders, not
an invented price, earnings figure, contract, or valuation statistic.
"""
from __future__ import annotations

import streamlit as st

from src.data_access.container import get_repositories
from src.logic.formatting import fmt_date
from src.models.models import Exposure
from src.ui.components.badges import demo_badge
from src.ui.components.cards import catalyst_timeline_row, evidence_row, signal_card
from src.ui.components.empty_state import empty_state
from src.ui.components.freshness import panel_header
from src.ui.components.save_dialog import TRIGGER_KEY
from src.ui.components.section import section_header
from src.ui.ui import get_page

DEFAULT_SYMBOL = "DEMO"


def render() -> None:
    ctx = get_repositories()
    symbol = st.query_params.get("symbol", DEFAULT_SYMBOL)
    ticker = ctx.ticker_repository.get_ticker(symbol)

    if ticker is None:
        st.markdown('<div class="er-page-title">Company</div>', unsafe_allow_html=True)
        empty_state(
            f"No ticker found for symbol '{symbol}'.",
            f"The only ticker loaded in this phase is the fictional {DEFAULT_SYMBOL} — see Themes to reach it.",
        )
        return

    theme = ctx.theme_repository.get_theme(ticker.theme_slug)
    subtheme = next((s for s in (theme.subthemes if theme else []) if s.slug == ticker.subtheme_slug), None)
    metrics = ctx.market_data_provider.get_rotation_metrics()
    theme_metric = ctx.market_data_provider.get_rotation_metric_for_theme(ticker.theme_slug)
    breadth_rank = None
    if theme_metric and metrics:
        ranked = sorted(metrics, key=lambda m: m.breadth_pct, reverse=True)
        breadth_rank = next((i + 1 for i, m in enumerate(ranked) if m.theme_slug == ticker.theme_slug), None)
    related_signals = [s for s in ctx.signal_repository.get_all_signals() if ticker.symbol in s.related_tickers]

    header_cols = st.columns([4, 1])
    with header_cols[0]:
        st.markdown(f'<div class="er-page-title">{ticker.symbol} — {ticker.company_name}</div>', unsafe_allow_html=True)
        tag_line = theme.name if theme else ticker.theme_slug
        if subtheme:
            tag_line += f" / {subtheme.name}"
        exposure_label = "Direct exposure" if ticker.exposure == Exposure.PRIMARY else "Second-order exposure"
        st.markdown(f'<div class="er-muted">{tag_line} · {exposure_label}</div>', unsafe_allow_html=True)
    with header_cols[1]:
        demo_badge("Sample")

    with st.container(key=f"cta-secondary-save-watchlist-{ticker.symbol}"):
        if st.button("Save to watchlist", key=f"company-save-{ticker.symbol}"):
            st.session_state[TRIGGER_KEY] = ticker.symbol
            st.rerun()

    panel_header("Key metrics", key=f"fresh-kpi-{ticker.symbol}")
    kpi_cols = st.columns(4)
    kpi_cols[0].markdown('<div class="er-metric-label">Last</div><div class="er-metric-value">—</div>', unsafe_allow_html=True)
    kpi_cols[1].markdown('<div class="er-metric-label">5-day</div><div class="er-metric-value">—</div>', unsafe_allow_html=True)
    kpi_cols[2].markdown(
        f'<div class="er-metric-label">Breadth rank</div><div class="er-metric-value">'
        f'{f"{breadth_rank} / {len(metrics)}" if breadth_rank else "—"}</div>', unsafe_allow_html=True,
    )
    kpi_cols[3].markdown(f'<div class="er-metric-label">Signals</div><div class="er-metric-value">{len(related_signals)}</div>', unsafe_allow_html=True)

    panel_header("Recent signals", key=f"fresh-signals-{ticker.symbol}")
    if not related_signals:
        empty_state(
            "No signals tied to this ticker yet.",
            "New signals across every theme show up on Signals as they're found.",
            action_label="View all signals",
            action_page=get_page("signals"),
            key=f"company-signals-{ticker.symbol}",
        )
    else:
        for s in related_signals:
            signal_card(s, evidence_repository=ctx.evidence_repository)

    panel_header("Thesis", key=f"fresh-thesis-{ticker.symbol}")
    st.markdown(f"**Why it's on the list**\n\n{ticker.thesis}")
    st.markdown(f"**Contrary evidence**\n\n{'; '.join(ticker.bear_factors) if ticker.bear_factors else 'None recorded.'}")
    st.markdown(f"**Invalidates if**\n\n{ticker.what_would_change_thesis or 'Not populated.'}")

    panel_header("Catalysts", key=f"fresh-cocatalysts-{ticker.symbol}")
    ticker_catalysts = ctx.catalyst_repository.get_catalysts_for_ticker(ticker.symbol)
    if not ticker_catalysts:
        empty_state(
            "No catalysts scheduled for this ticker yet.",
            "Upcoming catalysts across every theme are also collected on the Dashboard.",
            action_label="Open Dashboard",
            action_page=get_page("dashboard"),
            key=f"company-catalysts-{ticker.symbol}",
        )
    else:
        for c in ticker_catalysts:
            catalyst_timeline_row(c)

    st.divider()
    section_header("Market expectation")
    st.write(ticker.market_expectation or "Not populated.")

    section_header("What may be underappreciated")
    st.write(ticker.underappreciated or "Not populated.")

    section_header("Fundamental snapshot")
    empty_state(
        "No fundamental data connected yet.",
        "Price, market cap, P/E, revenue growth, and gross margin require a live market-data source — "
        "not built in this phase.",
        key="company-fundamental-snapshot",
    )

    section_header("Technical / relative-strength snapshot")
    empty_state(
        "No technical data connected yet.",
        "RSI, relative strength vs. theme, and trend indicators require a live market-data source — "
        "not built in this phase.",
        key="company-technical-snapshot",
    )

    section_header("Bull / base / bear")
    bull_col, base_col, bear_col = st.columns(3)
    for col, label, factors in [(bull_col, "Bull", ticker.bull_factors), (base_col, "Base", ticker.base_factors), (bear_col, "Bear", ticker.bear_factors)]:
        with col:
            st.markdown(f"**{label}**")
            if factors:
                for f in factors:
                    st.write(f"- {f}")
            else:
                st.caption("Not populated.")

    section_header("Key risks")
    if ticker.key_risks:
        for r in ticker.key_risks:
            st.write(f"- {r}")
    else:
        st.caption("Not populated.")

    section_header("Evidence timeline")
    evidence = ctx.evidence_repository.get_evidence_for_ticker(ticker.symbol)
    if not evidence:
        empty_state(
            "No evidence recorded for this ticker yet.",
            "See Methodology for how evidence is sourced and labeled.",
            action_label="Read Methodology",
            action_page=get_page("methodology"),
            key=f"company-evidence-{ticker.symbol}",
        )
    else:
        for e in sorted(evidence, key=lambda e: e.retrieved_at, reverse=True):
            evidence_row(e)

    section_header("Related peers")
    peers = [t for t in ctx.ticker_repository.get_tickers_for_theme(ticker.theme_slug) if t.symbol != ticker.symbol]
    if not peers:
        empty_state(
            "No peer tickers loaded yet.",
            "The curated ticker universe (Phase 3) will populate this with other names in the same theme.",
        )
    else:
        links = ", ".join(
            f'<a href="company?symbol={p.symbol}" style="color:var(--text); text-decoration:underline;">'
            f'{p.symbol}</a> — {p.company_name}' for p in peers
        )
        st.markdown(links, unsafe_allow_html=True)

    section_header("Sources")
    if evidence:
        for e in evidence:
            st.markdown(f"- {e.source_name} (no external source — demo data), retrieved {fmt_date(e.retrieved_at)}")
    else:
        st.caption("No sources recorded for this ticker yet.")

    st.divider()
    st.markdown(f'<div class="er-muted">Freshness: demo data, not a live feed. Ticker record is_demo={ticker.is_demo}.</div>', unsafe_allow_html=True)
