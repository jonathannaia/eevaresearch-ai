from __future__ import annotations

from src.config.settings import Settings
from src.data_access import loaders
from src.data_access.interfaces import MarketDataProvider
from src.models.models import CapitalRotationMetric

SEED_FILE = "rotation_metrics.json"


def _parse_metric(raw: dict) -> CapitalRotationMetric:
    return CapitalRotationMetric(
        theme_slug=raw["theme_slug"],
        relative_performance_pct=raw["relative_performance_pct"],
        breadth_pct=raw["breadth_pct"],
        leaders=raw.get("leaders", []),
        laggards=raw.get("laggards", []),
        as_of=raw["as_of"],
        is_demo=raw.get("is_demo", True),
    )


class DemoMarketDataProvider(MarketDataProvider):
    def __init__(self, settings: Settings):
        self._settings = settings

    def _all(self) -> list[CapitalRotationMetric]:
        raw = loaders.load(self._settings, SEED_FILE, default=[])
        return [_parse_metric(r) for r in raw]

    def get_rotation_metrics(self) -> list[CapitalRotationMetric]:
        return self._all()

    def get_rotation_metric_for_theme(self, theme_slug: str) -> CapitalRotationMetric | None:
        return next((m for m in self._all() if m.theme_slug == theme_slug), None)
