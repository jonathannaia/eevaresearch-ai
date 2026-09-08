"""Pure helper for brief §15's "surface the stored [invalidation] text when
that name moves against the thesis" — no Streamlit dependency."""
from __future__ import annotations

from src.models.models import Direction, Signal


def is_moving_against_thesis(ticker_symbol: str, signals: list[Signal]) -> bool:
    """True if any tracked signal for this ticker isn't reading as
    Improving — Weakening is the clear case; Mixed is included too, since
    it's not a confirming read either and the whole point of writing an
    invalidation condition down is to notice early, not just at the
    unambiguous end state."""
    against = {Direction.WEAKENING, Direction.MIXED}
    return any(ticker_symbol in s.related_tickers and s.direction in against for s in signals)
