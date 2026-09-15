"""Gated Japan/Korea source expansion (design/DECISIONS.md) — the one
seam that turns Settings.daily_news_enabled_jp_kr_sources (the
EDGE_DAILY_NEWS_ENABLED_SOURCES_JP_KR allow-list) into an actual list of
DailyNewsSourceEntry objects a discovery run can use. Pure, no I/O.

Mirrors market_news_sources.py's exact shape, deliberately kept as its
own separate module rather than a shared/generalized helper — this
expansion and the market-news one are independently revertible/
extensible (see source_registry.GATED_JP_KR_SOURCE_REGISTRY's own
module-level comment for why). The real worker (scripts/
daily_news_worker.py) is the only real caller."""
from __future__ import annotations

from src.config.settings import Settings
from src.data_access.daily_news.source_registry import GATED_JP_KR_SOURCE_REGISTRY, DailyNewsSourceEntry


def enabled_gated_sources(settings: Settings) -> tuple[DailyNewsSourceEntry, ...]:
    """Every entry in GATED_JP_KR_SOURCE_REGISTRY whose own source_id is
    present in settings.daily_news_enabled_jp_kr_sources — empty
    allow-list (the default) returns an empty tuple, so a caller that
    appends this to EDITORIAL_SOURCE_REGISTRY and passes the result
    straight to run_editorial_discovery() gets byte-identical behavior
    to omitting this function entirely."""
    if not settings.daily_news_enabled_jp_kr_sources:
        return ()
    return tuple(
        entry for entry in GATED_JP_KR_SOURCE_REGISTRY
        if entry.source_id in settings.daily_news_enabled_jp_kr_sources
    )
