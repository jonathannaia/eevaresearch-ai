"""SignalRepository backed by the real DART, EDINET, and EDGAR candidate
caches that Radar Inbox's own ingestion pipelines already write — no new
cache file, no new ingestion path, no change to scanning/credentials/
schedules. Only candidates eligible per src/logic/signal_promotion.py are
ever promoted; everything else (including deferred/not-material
candidates) is silently excluded, never shown as a Signal."""
from __future__ import annotations

from src.config.settings import Settings
from src.data_access.dart import candidate_store
from src.data_access.edgar import edgar_pipeline
from src.data_access.edinet import edinet_pipeline
from src.data_access.interfaces import SignalRepository
from src.logic.signal_promotion import candidate_to_signal, is_eligible_for_signal
from src.models.models import Signal

_DART_CANDIDATES_FILENAME = "dart_candidates.json"


class RadarSignalRepository(SignalRepository):
    def __init__(self, settings: Settings):
        self._settings = settings

    def _eligible_candidates(self):
        dart_candidates = candidate_store.load_candidates(self._settings.cache_dir, _DART_CANDIDATES_FILENAME)
        edinet_candidates = candidate_store.load_candidates(
            self._settings.cache_dir, edinet_pipeline.CANDIDATE_STORE_FILENAME
        )
        edgar_candidates = candidate_store.load_candidates(
            self._settings.cache_dir, edgar_pipeline.CANDIDATE_STORE_FILENAME
        )
        all_candidates = list(dart_candidates.values()) + list(edinet_candidates.values()) + list(edgar_candidates.values())
        return [c for c in all_candidates if is_eligible_for_signal(c)]

    def _all(self) -> list[Signal]:
        return [candidate_to_signal(c) for c in self._eligible_candidates()]

    def get_all_signals(self) -> list[Signal]:
        return self._all()

    def get_signals_for_theme(self, theme_slug: str) -> list[Signal]:
        return [s for s in self._all() if s.theme_slug == theme_slug]
