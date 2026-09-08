from src.logic.unread import is_unread, seed_initial_last_seen, unread_count
from src.models.models import Direction, Horizon, Signal, Strength


def _signal(id_: str, updated: str, related: list[str] | None = None) -> Signal:
    return Signal(
        id=id_, title="t", theme_slug="ai-buildout", subtheme_slug=None, direction=Direction.IMPROVING,
        strength=Strength.MODERATE, horizon=Horizon.SWING, evidence_count=1, interpretation="i",
        contrary_evidence="c", validation_criteria="v", invalidation_criteria="iv",
        related_tickers=related or [], last_updated=updated,
    )


def test_is_unread_true_when_newer_than_last_seen_and_not_read():
    s = _signal("s1", "2026-08-15T10:00:00+00:00")
    assert is_unread(s, "2026-08-15T09:00:00+00:00", set()) is True


def test_is_unread_false_when_older_than_last_seen():
    s = _signal("s1", "2026-08-15T08:00:00+00:00")
    assert is_unread(s, "2026-08-15T09:00:00+00:00", set()) is False


def test_is_unread_false_when_no_baseline_yet():
    s = _signal("s1", "2026-08-15T10:00:00+00:00")
    assert is_unread(s, None, set()) is False


def test_is_unread_false_once_individually_marked_read():
    s = _signal("s1", "2026-08-15T10:00:00+00:00")
    assert is_unread(s, "2026-08-15T09:00:00+00:00", {"s1"}) is False


def test_unread_count_sums_matching_signals():
    signals = [
        _signal("s1", "2026-08-15T10:00:00+00:00"),
        _signal("s2", "2026-08-15T11:00:00+00:00"),
        _signal("s3", "2026-08-14T08:00:00+00:00"),
    ]
    assert unread_count(signals, "2026-08-15T09:00:00+00:00", set()) == 2


def test_seed_initial_last_seen_uses_third_most_recent_date():
    signals = [
        _signal("s1", "2026-08-15T10:00:00+00:00"),
        _signal("s2", "2026-08-15T09:00:00+00:00"),
        _signal("s3", "2026-08-15T08:00:00+00:00"),
        _signal("s4", "2026-08-14T08:00:00+00:00"),
    ]
    assert seed_initial_last_seen(signals) == "2026-08-15T08:00:00+00:00"


def test_seed_initial_last_seen_none_when_fewer_than_three_signals():
    signals = [_signal("s1", "2026-08-15T10:00:00+00:00")]
    assert seed_initial_last_seen(signals) is None
