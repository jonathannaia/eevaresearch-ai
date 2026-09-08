from src.logic.theme_metrics import (
    average_breadth,
    leaders_and_laggards,
    rank_by_performance,
    signals_by_direction,
    strongest_signals,
)
from src.models.models import CapitalRotationMetric, Direction, Horizon, Signal, Strength


def _metric(theme_slug: str, perf: float, breadth: float) -> CapitalRotationMetric:
    return CapitalRotationMetric(theme_slug=theme_slug, relative_performance_pct=perf, breadth_pct=breadth, leaders=[], laggards=[], as_of="2026-08-15T00:00:00+00:00")


def _signal(theme_slug: str, direction: Direction, strength: Strength) -> Signal:
    return Signal(
        id=f"s-{theme_slug}", title="t", theme_slug=theme_slug, subtheme_slug=None, direction=direction,
        strength=strength, horizon=Horizon.SWING, evidence_count=1, interpretation="i", contrary_evidence="c",
        validation_criteria="v", invalidation_criteria="iv", related_tickers=[], last_updated="2026-08-15T00:00:00+00:00",
    )


def test_rank_by_performance_descending():
    metrics = [_metric("a", 1.0, 50), _metric("b", 5.0, 50), _metric("c", -2.0, 50)]
    ranked = rank_by_performance(metrics)
    assert [m.theme_slug for m in ranked] == ["b", "a", "c"]


def test_leaders_and_laggards_no_overlap_five_themes():
    metrics = [_metric(s, p, 50) for s, p in [("a", 4.0), ("b", -1.5), ("c", 1.0), ("d", 6.5), ("e", 3.0)]]
    leaders, laggards = leaders_and_laggards(metrics, top_n=2)
    assert [m.theme_slug for m in leaders] == ["d", "a"]
    assert [m.theme_slug for m in laggards] == ["b", "c"]
    assert set(m.theme_slug for m in leaders).isdisjoint(m.theme_slug for m in laggards)


def test_leaders_and_laggards_handles_fewer_than_top_n():
    metrics = [_metric("a", 1.0, 50)]
    leaders, laggards = leaders_and_laggards(metrics, top_n=2)
    assert len(leaders) == 1
    assert laggards == []


def test_average_breadth():
    metrics = [_metric("a", 0, 40), _metric("b", 0, 60)]
    assert average_breadth(metrics) == 50.0


def test_average_breadth_empty_list_returns_none():
    assert average_breadth([]) is None


def test_signals_by_direction_groups_correctly():
    signals = [
        _signal("a", Direction.IMPROVING, Strength.STRONG),
        _signal("b", Direction.IMPROVING, Strength.WEAK),
        _signal("c", Direction.WEAKENING, Strength.MODERATE),
    ]
    grouped = signals_by_direction(signals)
    assert len(grouped["Improving"]) == 2
    assert len(grouped["Weakening"]) == 1


def test_strongest_signals_orders_strong_first():
    signals = [
        _signal("a", Direction.MIXED, Strength.WEAK),
        _signal("b", Direction.MIXED, Strength.STRONG),
        _signal("c", Direction.MIXED, Strength.MODERATE),
    ]
    result = strongest_signals(signals, limit=3)
    assert [s.theme_slug for s in result] == ["b", "c", "a"]
