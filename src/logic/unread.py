"""Pure unread/read-state helpers for signals (brief §10). No Streamlit
dependency — session-state reads/writes happen at the call sites. A signal
is unread if it's newer than the last-seen baseline AND hasn't been
individually opened (its drawer marks it read on open, not on hover —
independent of the coarser last_seen_at baseline, which only advances when
Signals is opened)."""
from __future__ import annotations

from src.models.models import Signal


def is_unread(signal: Signal, last_seen_at: str | None, read_ids: set[str]) -> bool:
    if signal.id in read_ids:
        return False
    if not last_seen_at:
        return False
    return signal.last_updated > last_seen_at


def unread_count(signals: list[Signal], last_seen_at: str | None, read_ids: set[str]) -> int:
    return sum(1 for s in signals if is_unread(s, last_seen_at, read_ids))


def seed_initial_last_seen(signals: list[Signal]) -> str | None:
    """First-ever visit this session has no real baseline — seed one just
    before the two most-recent signals so the unread pattern has something
    to demonstrate immediately, rather than starting empty."""
    if len(signals) < 3:
        return None
    dates = sorted((s.last_updated for s in signals), reverse=True)
    return dates[2]
