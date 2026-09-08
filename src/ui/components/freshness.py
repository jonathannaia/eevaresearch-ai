"""Data freshness — three states (brief §13). Every panel header carries
its own timestamp; a global indicator would hide which panel went cold.
Stale is deliberately the "louder" treatment (solid dot + surface-3 chip +
inline Retry), not a quieter one — it's the dangerous state (connected but
behind), and a binary live/not-live indicator can't distinguish it from
Live otherwise.

This build has zero live data sources connected (foundation phase), so
every call site in practice renders "demo" — the component supports all
three states for correctness/forward-compatibility, not because Live/Stale
are exercised by real logic yet.
"""
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

FRESHNESS_LIVE_THRESHOLD_MINUTES = 15


def freshness_state(last_fetch_at: str | None, has_live_source: bool) -> str:
    """Pure classification — no Streamlit dependency. 'demo' if no live
    source is connected at all; otherwise 'live' if the last fetch was
    under the threshold, else 'stale'."""
    if not has_live_source or not last_fetch_at:
        return "demo"
    try:
        fetched = datetime.fromisoformat(last_fetch_at)
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return "demo"
    age_minutes = (datetime.now(timezone.utc) - fetched).total_seconds() / 60
    return "live" if age_minutes <= FRESHNESS_LIVE_THRESHOLD_MINUTES else "stale"


def freshness_chip(state: str = "demo", timestamp: str | None = None, key: str | None = None) -> None:
    label = {"live": "Live", "stale": "Stale", "demo": "Demo"}.get(state, "Demo")
    ts = timestamp or datetime.now(timezone.utc).strftime("%H:%M UTC")
    st.markdown(
        f'<span class="er-fresh er-fresh-{state}"><span class="er-fresh-dot"></span>{label} · {ts}</span>',
        unsafe_allow_html=True,
    )
    if state == "stale":
        if st.button("Retry", key=key or f"retry-{ts}", help="Re-fetch this panel's data."):
            st.cache_data.clear()
            st.rerun()


def panel_header(title: str, state: str = "demo", timestamp: str | None = None, key: str | None = None) -> None:
    """Section label + its own freshness chip, right-aligned — the
    per-panel-timestamp pattern used across Dashboard/Signals/Themes/
    Company instead of a single global indicator."""
    cols = st.columns([4, 2])
    with cols[0]:
        st.markdown(f'<div class="er-section-label">{title}</div>', unsafe_allow_html=True)
    with cols[1]:
        st.markdown('<div style="text-align:right; margin-top:1.5rem;">', unsafe_allow_html=True)
        freshness_chip(state, timestamp, key=key)
        st.markdown("</div>", unsafe_allow_html=True)
