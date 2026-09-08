"""Table components — thin layers over st.dataframe (native sorting/
resizing) plus empty-state fallbacks. leaderboard_table is shared by
Overview and Capital Rotation so the leaders/laggards presentation (mono
values, non-truncated headers) only exists in one place.
"""
from __future__ import annotations

import streamlit as st

from src.models.models import CapitalRotationMetric, Theme, Ticker
from src.ui.components.empty_state import empty_state


def leaderboard_table(metrics: list[CapitalRotationMetric], themes: dict[str, Theme]) -> None:
    """Ranked theme table. Column headers are short and non-truncating —
    the original "Relative performance" header clipped in a narrow column
    (Streamlit's metric-style labels use `white-space: nowrap`), so this
    uses the same short mono-friendly labels as the rest of the redesign."""
    rows = [
        {
            "Theme": themes[m.theme_slug].name,
            "Rel. perf.": f"{m.relative_performance_pct:+.1f}%",
            "Breadth": f"{m.breadth_pct:.0f}%",
        }
        for m in metrics
        if m.theme_slug in themes
    ]
    if not rows:
        empty_state("No theme metrics loaded.")
        return
    st.dataframe(rows, hide_index=True, width="stretch")


def ticker_table(tickers: list[Ticker]) -> None:
    if not tickers:
        empty_state(
            "Ticker universe not yet loaded for this theme.",
            "See Methodology for the Phase 3 curated ticker-universe plan.",
        )
        return

    rows = [
        {
            "Symbol": t.symbol,
            "Company": t.company_name,
            "Exposure": t.exposure.value,
            "Market cap": t.market_cap_bucket,
            "Liquidity": t.liquidity_bucket,
            "Technical strength": t.technical_strength,
            "Risk level": t.risk_level,
            "Demo": "Yes" if t.is_demo else "No",
        }
        for t in tickers
    ]
    st.dataframe(rows, hide_index=True, width="stretch")
