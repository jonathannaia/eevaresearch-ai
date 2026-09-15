"""Gated market-news source expansion (design/DECISIONS.md) — the one
seam that turns Settings.daily_news_enabled_market_news_sources (the
EDGE_DAILY_NEWS_ENABLED_SOURCES allow-list) into an actual list of
DailyNewsSourceEntry objects a discovery run can use. Pure, no I/O.

Deliberately kept separate from source_registry.py itself (which stays
a pure data module, no Settings dependency) and from editorial_pipeline.
py (whose own run_editorial_discovery() stays source-list-agnostic —
it already accepts any source_entries tuple, gated or not, with zero
changes). The real worker (scripts/daily_news_worker.py) is the only
real caller: see that module's own _run_editorial_tick for exactly how
this is combined with EDITORIAL_SOURCE_REGISTRY's own always-on 36
sources."""
from __future__ import annotations

from src.config.settings import Settings
from src.data_access.daily_news.source_registry import GATED_MARKET_NEWS_SOURCE_REGISTRY, DailyNewsSourceEntry


def enabled_gated_sources(settings: Settings) -> tuple[DailyNewsSourceEntry, ...]:
    """Every entry in GATED_MARKET_NEWS_SOURCE_REGISTRY whose own
    source_id is present in settings.daily_news_enabled_market_news_
    sources — empty allow-list (the default) returns an empty tuple,
    so a caller that appends this to EDITORIAL_SOURCE_REGISTRY and
    passes the result straight to run_editorial_discovery() gets
    byte-identical behavior to omitting this function entirely."""
    if not settings.daily_news_enabled_market_news_sources:
        return ()
    return tuple(
        entry for entry in GATED_MARKET_NEWS_SOURCE_REGISTRY
        if entry.source_id in settings.daily_news_enabled_market_news_sources
    )
