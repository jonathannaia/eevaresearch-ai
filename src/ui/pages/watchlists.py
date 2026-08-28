"""Watchlists — four demo lists. Add/remove works within a session
(st.session_state only) so the UI feels functional; nothing here persists
across a reload. Watchlists are deliberately not behind the repository
interfaces used elsewhere — they're user-generated session state, not
sourced market data.
"""
from __future__ import annotations

import streamlit as st

from src.config.settings import get_settings
from src.data_access import loaders
from src.data_access.container import get_repositories
from src.logic.formatting import fmt_date
from src.logic.watchlist_risk import is_moving_against_thesis
from src.models.models import WatchlistEntry
from src.ui.components.empty_state import empty_state
from src.ui.components.save_dialog import TRIGGER_KEY
from src.ui.ui import get_page

SESSION_KEY = "watchlists"
WATCHLIST_NAMES = ["Core Themes", "High-Conviction Research", "Earnings/Catalysts", "Emerging Names"]


def seed_watchlists() -> dict[str, list[WatchlistEntry]]:
    raw = loaders.load(get_settings(), "watchlists.json", default={})
    return {
        name: [
            WatchlistEntry(
                list_name=name, ticker_symbol=e["ticker_symbol"], added_at=e["added_at"],
                note=e.get("note", ""), invalidates_if=e.get("invalidates_if", ""),
            )
            for e in raw.get(name, [])
        ]
        for name in WATCHLIST_NAMES
    }


def render() -> None:
    ctx = get_repositories()
    if SESSION_KEY not in st.session_state:
        st.session_state[SESSION_KEY] = seed_watchlists()
    lists: dict[str, list[WatchlistEntry]] = st.session_state[SESSION_KEY]

    st.markdown('<div class="er-page-title">Watchlists</div>', unsafe_allow_html=True)
    st.write(
        "Sample watchlists, seeded from illustrative data. Adding or removing a ticker updates this session "
        "only — reloading the page resets to the seeded lists. Built so real, persisted watchlists "
        "can be added later without a UI rewrite."
    )

    all_symbols = [t.symbol for t in ctx.ticker_repository.get_all_tickers()]
    signals = ctx.signal_repository.get_all_signals()

    tabs = st.tabs(WATCHLIST_NAMES)
    for tab, name in zip(tabs, WATCHLIST_NAMES):
        with tab:
            entries = lists[name]
            if not entries:
                empty_state(
                    "Nothing here yet",
                    "Add companies from a theme map, a signal drawer, or by ticker.",
                    action_label="Add a company",
                    action_page=get_page("themes"),
                    key=f"watchlist-{name}",
                )
            else:
                for e in entries:
                    against = is_moving_against_thesis(e.ticker_symbol, signals)
                    st.markdown(
                        f'<div class="er-row" style="display:flex; justify-content:space-between;">'
                        f'<a href="company?symbol={e.ticker_symbol}" class="er-mono" '
                        f'style="color:var(--text); text-decoration:underline;">{e.ticker_symbol}</a>'
                        f'<span class="er-muted">Added {fmt_date(e.added_at)}</span></div>',
                        unsafe_allow_html=True,
                    )
                    if against and e.invalidates_if:
                        st.markdown(
                            f'<div class="er-muted" style="background:var(--neg-dim); '
                            f'border-radius:var(--r-sm); padding:0.5rem 0.7rem; margin:-0.2rem 0 0.5rem 0; font-size:0.82rem;">'
                            f'<strong style="color:var(--text);">Moving against thesis</strong> — you wrote: "{e.invalidates_if}"</div>',
                            unsafe_allow_html=True,
                        )

            # Adding enforces the same rule as everywhere else (brief §15) —
            # this used to add directly with no invalidation condition,
            # which was a real loophole around the enforcement built
            # elsewhere; it now routes through the same save dialog.
            available_to_add = [s for s in all_symbols if s not in {e.ticker_symbol for e in entries}]
            add_cols = st.columns([3, 1])
            with add_cols[0]:
                to_add = st.selectbox("Add ticker", ["—"] + available_to_add, key=f"add-select-{name}")
            with add_cols[1]:
                st.markdown("&nbsp;")
                with st.container(key=f"cta-secondary-add-{name}"):
                    if st.button("Add", key=f"add-btn-{name}", width="stretch") and to_add != "—":
                        st.session_state[TRIGGER_KEY] = to_add
                        st.session_state["save_dialog_default_list"] = name
                        st.rerun()

            if entries:
                remove_cols = st.columns([3, 1])
                with remove_cols[0]:
                    to_remove = st.selectbox("Remove ticker", ["—"] + [e.ticker_symbol for e in entries], key=f"remove-select-{name}")
                with remove_cols[1]:
                    st.markdown("&nbsp;")
                    with st.container(key=f"cta-secondary-remove-{name}"):
                        if st.button("Remove", key=f"remove-btn-{name}", width="stretch") and to_remove != "—":
                            lists[name] = [e for e in entries if e.ticker_symbol != to_remove]
