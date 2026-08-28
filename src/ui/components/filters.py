"""Reusable ticker filter bar. Options are derived from whatever data is
actually loaded rather than hardcoded, so this works unchanged once a real
ticker universe replaces the single DEMO row — right now, with one ticker,
most filters will have at most one option and won't visibly narrow
anything, which is expected and not a bug.
"""
from __future__ import annotations

import streamlit as st

from src.models.models import Catalyst, Subtheme, Ticker


def ticker_filter_bar(
    tickers: list[Ticker], subthemes: list[Subtheme], catalysts: list[Catalyst] | None = None, key_prefix: str = "default"
) -> list[Ticker]:
    catalysts = catalysts or []
    subtheme_by_name = {s.name: s.slug for s in subthemes}

    # Phase B (UI audit): only render a multiselect when it actually has
    # options to offer — with a single demo ticker, most of these are
    # empty and rendered as dead controls otherwise. Order/labels/keys are
    # unchanged from before; a dimension reappears automatically the
    # moment real option values exist for it.
    specs = [
        ("subcategory", "Subcategory", list(subtheme_by_name.keys()), f"{key_prefix}-subcategory"),
        ("exposure", "Exposure", sorted({t.exposure.value for t in tickers}), f"{key_prefix}-exposure"),
        ("market_cap", "Market cap", sorted({t.market_cap_bucket for t in tickers}), f"{key_prefix}-market-cap"),
        ("risk", "Risk level", sorted({t.risk_level for t in tickers}), f"{key_prefix}-risk"),
        ("liquidity", "Liquidity", sorted({t.liquidity_bucket for t in tickers}), f"{key_prefix}-liquidity"),
        ("tech", "Technical strength", sorted({t.technical_strength for t in tickers}), f"{key_prefix}-tech"),
        ("catalyst_type", "Catalyst type", sorted({c.catalyst_type for c in catalysts}), f"{key_prefix}-catalyst-type"),
    ]
    active_specs = [s for s in specs if s[2]]
    selected: dict[str, list[str]] = {name: [] for name, _, _, _ in specs}

    with st.expander("Filters (market cap, liquidity, subcategory, exposure, technical strength, catalyst type, risk level)", expanded=False):
        if not active_specs:
            st.caption("No filter dimensions available yet for this ticker universe.")
        for i in range(0, len(active_specs), 4):
            row = st.columns(len(active_specs[i : i + 4]))
            for col, (name, label, options, key) in zip(row, active_specs[i : i + 4]):
                selected[name] = col.multiselect(label, options, key=key)

    selected_subthemes = selected["subcategory"]
    selected_exposure = selected["exposure"]
    selected_market_cap = selected["market_cap"]
    selected_risk = selected["risk"]
    selected_liquidity = selected["liquidity"]
    selected_tech = selected["tech"]
    selected_catalyst_types = selected["catalyst_type"]

    filtered = tickers
    if selected_subthemes:
        slugs = {subtheme_by_name[n] for n in selected_subthemes}
        filtered = [t for t in filtered if t.subtheme_slug in slugs]
    if selected_exposure:
        filtered = [t for t in filtered if t.exposure.value in selected_exposure]
    if selected_market_cap:
        filtered = [t for t in filtered if t.market_cap_bucket in selected_market_cap]
    if selected_risk:
        filtered = [t for t in filtered if t.risk_level in selected_risk]
    if selected_liquidity:
        filtered = [t for t in filtered if t.liquidity_bucket in selected_liquidity]
    if selected_tech:
        filtered = [t for t in filtered if t.technical_strength in selected_tech]
    if selected_catalyst_types:
        symbols_with_catalyst = {c.ticker_symbol for c in catalysts if c.catalyst_type in selected_catalyst_types and c.ticker_symbol}
        filtered = [t for t in filtered if t.symbol in symbols_with_catalyst]
    return filtered
