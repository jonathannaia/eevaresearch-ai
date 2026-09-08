"""Pure aggregation helpers over CapitalRotationMetric / Signal data. No
Streamlit dependency, no knowledge of where the data came from — takes
plain model instances in, so these stay correct and testable whether the
data is today's demo JSON or a real Phase 2 source."""
from __future__ import annotations

from src.models.models import CapitalRotationMetric, Signal


def rank_by_performance(metrics: list[CapitalRotationMetric], descending: bool = True) -> list[CapitalRotationMetric]:
    return sorted(metrics, key=lambda m: m.relative_performance_pct, reverse=descending)


def leaders_and_laggards(
    metrics: list[CapitalRotationMetric], top_n: int = 2
) -> tuple[list[CapitalRotationMetric], list[CapitalRotationMetric]]:
    """Top-N by relative performance and bottom-N (worst-first), with no
    overlap between the two lists even when the theme count is small."""
    ranked = rank_by_performance(metrics)
    leaders = ranked[:top_n]
    remaining = ranked[top_n:]
    laggards = remaining[-top_n:][::-1] if remaining else []
    return leaders, laggards


def average_breadth(metrics: list[CapitalRotationMetric]) -> float | None:
    if not metrics:
        return None
    return sum(m.breadth_pct for m in metrics) / len(metrics)


def signals_by_direction(signals: list[Signal]) -> dict[str, list[Signal]]:
    grouped: dict[str, list[Signal]] = {}
    for s in signals:
        grouped.setdefault(s.direction.value, []).append(s)
    return grouped


def strongest_signals(signals: list[Signal], limit: int = 5) -> list[Signal]:
    strength_rank = {"Strong": 2, "Moderate": 1, "Weak": 0}
    return sorted(signals, key=lambda s: strength_rank.get(s.strength.value, 0), reverse=True)[:limit]
