"""One deliberate exception to "no third-party charting library": st.bar_chart
cannot do per-bar conditional colors (leader vs. rest), which the redesign
explicitly calls for on the rotation chart. Altair already ships as a
transitive dependency of Streamlit (used internally by st.bar_chart itself)
— this adds no new package to requirements.txt, just a small inline spec
instead of the simplified wrapper.

Currently unrouted: this chart backed the standalone Capital Rotation page,
which brief §4 dissolves into a Dashboard panel and a per-theme tab (see
src/ui/pages/capital_rotation.py's docstring) — kept on disk, not deleted,
per the non-goal against deleting content. Colors are neutral text tokens
only (brief §5: zero accent colour, no red/green), read from
src/ui/theme_tokens.py for the theme being rendered — a Vega spec is
drawn server-side and cannot read CSS custom properties.
"""
from __future__ import annotations

import altair as alt
import pandas as pd

from src.models.models import CapitalRotationMetric, Theme
from src.ui.theme_tokens import ThemeName, token


def _color_scale(theme: ThemeName) -> alt.Scale:
    return alt.Scale(
        domain=["leader", "positive", "negative"],
        range=[token(theme, "text"), token(theme, "text-secondary"), token(theme, "text-muted")],
    )


def rotation_bar_chart(
    metrics: list[CapitalRotationMetric], themes: dict[str, Theme], theme: ThemeName = "dark",
) -> alt.LayerChart:
    ranked = sorted(
        (m for m in metrics if m.theme_slug in themes),
        key=lambda m: m.relative_performance_pct,
        reverse=True,
    )
    rows = [
        {
            "Theme": themes[m.theme_slug].name,
            "Value": m.relative_performance_pct,
            "Color": "leader" if i == 0 else ("positive" if m.relative_performance_pct >= 0 else "negative"),
        }
        for i, m in enumerate(ranked)
    ]
    df = pd.DataFrame(rows)

    muted = token(theme, "text-muted")
    rule = token(theme, "border-strong")
    bars = (
        alt.Chart(df)
        .mark_bar(size=32)
        .encode(
            x=alt.X("Theme:N", sort=None, axis=alt.Axis(labelAngle=0, title=None)),
            y=alt.Y("Value:Q", axis=alt.Axis(title="Rel. perf. (%)", grid=True)),
            color=alt.Color("Color:N", scale=_color_scale(theme), legend=None),
            tooltip=[alt.Tooltip("Theme:N"), alt.Tooltip("Value:Q", format="+.1f")],
        )
    )
    zero_rule = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color=rule).encode(y="y:Q")

    return (
        (bars + zero_rule)
        .properties(height=260, background="transparent")
        .configure_view(strokeWidth=0)
        .configure_axis(
            domainColor=rule, gridColor=token(theme, "border-subtle"), tickColor=rule,
            labelColor=muted, titleColor=muted, labelFont="Geist", titleFont="Geist",
        )
    )
