"""Signals — structured, filterable view of every tracked signal (brief §4:
absorbs Signal Board; Watchlists fold in as sidebar filter entries rather
than a standalone page). Sourced from real DART/EDINET Radar candidates
(src/data_access/live/radar_signal_repository.py) — only candidates
eligible per src/logic/signal_promotion.py are ever shown; no demo/sample
rows appear here.

Opening this page is the trigger that advances `last_seen_at` (brief §10)
— read the previous baseline first (for unread-dot display and the sidebar
badge on this same render), then advance it at the end.
"""
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from src.data_access.container import get_repositories
from src.data_access.interfaces import SignalRepository
from src.logic.unread import is_unread
from src.ui.components.cards import signal_card
from src.ui.components.empty_state import empty_state
from src.ui.components.freshness import freshness_chip
from src.ui.pages.watchlists import seed_watchlists
from src.ui.ui import LAST_SEEN_KEY, READ_IDS_KEY, get_page


_FILTER_KEYS = ["signals-filter-theme", "signals-filter-direction", "signals-filter-strength", "signals-filter-horizon"]


def _clear_filters() -> None:
    for key in _FILTER_KEYS:
        st.session_state[key] = []


def render(signal_repository: SignalRepository | None = None) -> None:
    ctx = get_repositories()
    themes = {t.slug: t.name for t in ctx.theme_repository.get_all_themes()}
    signals_repo = signal_repository if signal_repository is not None else ctx.signal_repository

    # Durable-State Phase 4G-1: an explicitly injected collaborator is the
    # only path that can fail here (the default JSON path has no
    # documented failure mode in production use and stays unguarded,
    # exactly as before). On failure, render a static, non-leaky
    # hosted-unavailable state — never the raw exception, never a silent
    # fallback to ctx.signal_repository.
    if signal_repository is not None:
        try:
            signals = signals_repo.get_all_signals()
        except Exception:
            empty_state(
                "Hosted signals are temporarily unavailable.",
                "Try again shortly, or view the standard signal feed.",
                key="signals-hosted-unavailable",
            )
            return
    else:
        signals = signals_repo.get_all_signals()

    prev_last_seen = st.session_state.get(LAST_SEEN_KEY)
    read_ids = st.session_state.setdefault(READ_IDS_KEY, set())

    header_cols = st.columns([4, 2])
    with header_cols[0]:
        st.markdown('<div class="er-page-title">Signals</div>', unsafe_allow_html=True)
    with header_cols[1]:
        st.markdown('<div style="text-align:right; margin-top:0.8rem;">', unsafe_allow_html=True)
        freshness_chip("live", key="fresh-signals-head")
        st.markdown("</div>", unsafe_allow_html=True)
    st.write("Every tracked signal in one place — sourced from real DART and EDINET filings, filterable by theme, direction, strength, and time horizon.")

    if not signals:
        empty_state(
            "No eligible filings yet.",
            "Radar Inbox scans DART and EDINET; a filing appears here once it reaches Extracted or Needs review status.",
        )
        return

    # Watchlists are sidebar entries filtering this same view, not a
    # standalone page (brief §4) — the sidebar's watchlist links set
    # ?watchlist=<name>, resolved here against the same session-state
    # watchlists used everywhere else.
    watchlist_name = st.query_params.get("watchlist")
    watchlist_symbols: set[str] | None = None
    if watchlist_name:
        if "watchlists" not in st.session_state:
            st.session_state["watchlists"] = seed_watchlists()
        watchlist_symbols = {e.ticker_symbol for e in st.session_state["watchlists"].get(watchlist_name, [])}
        notice_cols = st.columns([5, 1])
        with notice_cols[0]:
            st.markdown(
                f'<div class="er-muted">Filtered from the <strong>{watchlist_name}</strong> watchlist '
                f'({len(watchlist_symbols)} name{"s" if len(watchlist_symbols) != 1 else ""}).</div>',
                unsafe_allow_html=True,
            )
        with notice_cols[1]:
            with st.container(key="cta-tertiary-clear-watchlist-filter"):
                if st.button("Clear filter", key="clear-watchlist-filter"):
                    del st.query_params["watchlist"]
                    st.rerun()

    filter_cols = st.columns([3, 3, 3, 3, 2], vertical_alignment="bottom")
    theme_filter = filter_cols[0].multiselect("Theme", sorted(themes.values()), key="signals-filter-theme")
    direction_filter = filter_cols[1].multiselect("Direction", sorted({s.direction.value for s in signals}), key="signals-filter-direction")
    strength_filter = filter_cols[2].multiselect("Strength", sorted({s.strength.value for s in signals}), key="signals-filter-strength")
    horizon_filter = filter_cols[3].multiselect("Time horizon", sorted({s.horizon.value for s in signals}), key="signals-filter-horizon")
    active_filter_count = len(theme_filter) + len(direction_filter) + len(strength_filter) + len(horizon_filter)
    with filter_cols[4]:
        with st.container(key="cta-tertiary-clear-filters"):
            if st.button("Clear filters", key="clear-filters-visible", width="stretch", disabled=active_filter_count == 0):
                _clear_filters()
                st.rerun()
    if active_filter_count:
        st.markdown(
            f'<div class="er-muted" style="font-size:0.78rem; margin-top:-0.5rem;">{active_filter_count} filter{"s" if active_filter_count != 1 else ""} active</div>',
            unsafe_allow_html=True,
        )

    filtered = signals
    if watchlist_symbols is not None:
        filtered = [s for s in filtered if watchlist_symbols.intersection(s.related_tickers)]
    if theme_filter:
        name_to_slug = {v: k for k, v in themes.items()}
        slugs = {name_to_slug[n] for n in theme_filter}
        filtered = [s for s in filtered if s.theme_slug in slugs]
    if direction_filter:
        filtered = [s for s in filtered if s.direction.value in direction_filter]
    if strength_filter:
        filtered = [s for s in filtered if s.strength.value in strength_filter]
    if horizon_filter:
        filtered = [s for s in filtered if s.horizon.value in horizon_filter]

    st.caption(f"{len(filtered)} of {len(signals)} signals shown.")
    if not filtered:
        empty_state(
            "No signals match the current filters.",
            "Try widening the theme, direction, strength, or time-horizon filters above.",
            action_label="Clear all filters",
            on_click=_clear_filters,
            key="signals-no-matches",
        )
        return

    theme_page = get_page("themes")
    for s in sorted(filtered, key=lambda s: s.last_updated, reverse=True):
        signal_card(
            s, theme_page=theme_page, evidence_repository=ctx.evidence_repository,
            unread=is_unread(s, prev_last_seen, read_ids), theme_name=themes.get(s.theme_slug),
        )

    st.session_state[LAST_SEEN_KEY] = datetime.now(timezone.utc).isoformat()
