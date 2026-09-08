from __future__ import annotations

from src.config.settings import Settings
from src.data_access import loaders
from src.data_access.interfaces import TickerRepository
from src.models.models import Exposure, Ticker

SEED_FILE = "tickers_demo.json"


def _parse_ticker(raw: dict) -> Ticker:
    return Ticker(
        symbol=raw["symbol"],
        company_name=raw["company_name"],
        theme_slug=raw["theme_slug"],
        subtheme_slug=raw.get("subtheme_slug"),
        exposure=Exposure(raw["exposure"]),
        market_cap_bucket=raw["market_cap_bucket"],
        liquidity_bucket=raw["liquidity_bucket"],
        technical_strength=raw["technical_strength"],
        risk_level=raw["risk_level"],
        thesis=raw["thesis"],
        market_expectation=raw.get("market_expectation", ""),
        underappreciated=raw.get("underappreciated", ""),
        bull_factors=raw.get("bull_factors", []),
        base_factors=raw.get("base_factors", []),
        bear_factors=raw.get("bear_factors", []),
        key_risks=raw.get("key_risks", []),
        what_would_change_thesis=raw.get("what_would_change_thesis", ""),
        is_demo=raw.get("is_demo", True),
    )


class DemoTickerRepository(TickerRepository):
    def __init__(self, settings: Settings):
        self._settings = settings

    def _all(self) -> list[Ticker]:
        raw = loaders.load(self._settings, SEED_FILE, default=[])
        return [_parse_ticker(r) for r in raw]

    def get_ticker(self, symbol: str) -> Ticker | None:
        return next((t for t in self._all() if t.symbol.upper() == symbol.upper()), None)

    def get_tickers_for_theme(self, theme_slug: str) -> list[Ticker]:
        return [t for t in self._all() if t.theme_slug == theme_slug]

    def get_all_tickers(self) -> list[Ticker]:
        return self._all()
