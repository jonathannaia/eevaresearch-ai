"""Postgres-backed SignalRepository — the isolated Postgres counterpart
to src/data_access/state_db/signal_repository.py, mirrored exactly in
shape: Signals are never stored, only derived, on every call, from
whichever candidates currently have status=PUBLISHED. No signals table
exists in this schema (see schema.py) and none should ever be added —
reusing src/logic/signal_promotion.py's existing, unmodified
is_eligible_for_signal()/candidate_to_signal() is what guarantees this
backend can never produce a Signal the JSON- or SQLite-backed
repositories wouldn't also produce from the same underlying candidate
data."""
from __future__ import annotations

import psycopg

from src.data_access.interfaces import SignalRepository
from src.data_access.postgres_state_db import candidate_repository
from src.logic.signal_promotion import candidate_to_signal, is_eligible_for_signal
from src.models.models import Signal

_SOURCES: tuple[str, ...] = ("OpenDART / DART", "SEC EDGAR", "EDINET")


class PostgresSignalRepository(SignalRepository):
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn

    def _eligible_candidates(self):
        all_candidates = []
        for source in _SOURCES:
            all_candidates.extend(candidate_repository.load_candidates(self._conn, source).values())
        return [c for c in all_candidates if is_eligible_for_signal(c)]

    def _all(self) -> list[Signal]:
        return [candidate_to_signal(c) for c in self._eligible_candidates()]

    def get_all_signals(self) -> list[Signal]:
        return self._all()

    def get_signals_for_theme(self, theme_slug: str) -> list[Signal]:
        return [s for s in self._all() if s.theme_slug == theme_slug]
