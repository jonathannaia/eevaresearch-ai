from __future__ import annotations

from src.config.settings import Settings
from src.data_access import loaders
from src.data_access.interfaces import SignalRepository
from src.models.models import Direction, Horizon, Signal, Strength

SEED_FILE = "signals.json"


def _parse_signal(raw: dict) -> Signal:
    return Signal(
        id=raw["id"],
        title=raw["title"],
        theme_slug=raw["theme_slug"],
        subtheme_slug=raw.get("subtheme_slug"),
        direction=Direction(raw["direction"]),
        strength=Strength(raw["strength"]),
        horizon=Horizon(raw["horizon"]),
        evidence_count=raw["evidence_count"],
        interpretation=raw["interpretation"],
        contrary_evidence=raw["contrary_evidence"],
        validation_criteria=raw["validation_criteria"],
        invalidation_criteria=raw["invalidation_criteria"],
        related_tickers=raw.get("related_tickers", []),
        last_updated=raw["last_updated"],
        is_demo=raw.get("is_demo", True),
    )


class DemoSignalRepository(SignalRepository):
    def __init__(self, settings: Settings):
        self._settings = settings

    def _all(self) -> list[Signal]:
        raw = loaders.load(self._settings, SEED_FILE, default=[])
        return [_parse_signal(r) for r in raw]

    def get_all_signals(self) -> list[Signal]:
        return self._all()

    def get_signals_for_theme(self, theme_slug: str) -> list[Signal]:
        return [s for s in self._all() if s.theme_slug == theme_slug]
