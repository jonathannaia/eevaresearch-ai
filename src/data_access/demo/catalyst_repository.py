from __future__ import annotations

from src.config.settings import Settings
from src.data_access import loaders
from src.data_access.interfaces import CatalystRepository
from src.models.models import Catalyst

SEED_FILE = "catalysts.json"


def _parse_catalyst(raw: dict) -> Catalyst:
    return Catalyst(
        id=raw["id"],
        title=raw["title"],
        date=raw["date"],
        catalyst_type=raw["catalyst_type"],
        description=raw["description"],
        theme_slug=raw["theme_slug"],
        ticker_symbol=raw.get("ticker_symbol"),
        is_demo=raw.get("is_demo", True),
    )


class DemoCatalystRepository(CatalystRepository):
    def __init__(self, settings: Settings):
        self._settings = settings

    def _all(self) -> list[Catalyst]:
        raw = loaders.load(self._settings, SEED_FILE, default=[])
        return [_parse_catalyst(r) for r in raw]

    def get_catalysts_for_theme(self, theme_slug: str) -> list[Catalyst]:
        return sorted((c for c in self._all() if c.theme_slug == theme_slug), key=lambda c: c.date)

    def get_catalysts_for_ticker(self, symbol: str) -> list[Catalyst]:
        return sorted(
            (c for c in self._all() if c.ticker_symbol and c.ticker_symbol.upper() == symbol.upper()),
            key=lambda c: c.date,
        )

    def get_upcoming_catalysts(self, limit: int = 10) -> list[Catalyst]:
        return sorted(self._all(), key=lambda c: c.date)[:limit]
