from src.logic.watchlist_risk import is_moving_against_thesis
from src.models.models import Direction, Horizon, Signal, Strength


def _signal(direction: Direction, related: list[str]) -> Signal:
    return Signal(
        id="s1", title="t", theme_slug="photonics", subtheme_slug=None, direction=direction,
        strength=Strength.MODERATE, horizon=Horizon.SWING, evidence_count=1, interpretation="i",
        contrary_evidence="c", validation_criteria="v", invalidation_criteria="iv",
        related_tickers=related, last_updated="2026-08-15T00:00:00+00:00",
    )


def test_true_when_a_related_signal_is_weakening():
    signals = [_signal(Direction.WEAKENING, ["DEMO"])]
    assert is_moving_against_thesis("DEMO", signals) is True


def test_true_when_a_related_signal_is_mixed():
    signals = [_signal(Direction.MIXED, ["DEMO"])]
    assert is_moving_against_thesis("DEMO", signals) is True


def test_false_when_only_improving_signals():
    signals = [_signal(Direction.IMPROVING, ["DEMO"])]
    assert is_moving_against_thesis("DEMO", signals) is False


def test_false_when_no_related_signals_for_ticker():
    signals = [_signal(Direction.WEAKENING, ["OTHER"])]
    assert is_moving_against_thesis("DEMO", signals) is False


def test_false_for_empty_signal_list():
    assert is_moving_against_thesis("DEMO", []) is False
