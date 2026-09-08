"""The single wiring point between the interfaces in interfaces.py and a
concrete implementation. Phase 1 hardcodes the demo/* implementations below
— Phase 2 swaps in real implementations here only, without touching any
page or component code, since every caller depends on the interfaces, not
on this file's contents.

Deliberately a plain function, not a Streamlit-cached singleton — demo
repository construction does no I/O itself (I/O happens lazily inside
loaders.load_seed_json, which is already st.cache_data-cached), so there's
no cost to re-constructing this bundle per call, and it stays trivially
usable from plain pytest without a Streamlit runtime.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.config.settings import Settings, get_settings
from src.data_access import backend_factory
from src.data_access.demo.catalyst_repository import DemoCatalystRepository
from src.data_access.demo.evidence_repository import DemoEvidenceRepository
from src.data_access.demo.market_data_provider import DemoMarketDataProvider
from src.data_access.demo.research_answer_provider import DemoResearchAnswerProvider
from src.data_access.demo.theme_repository import DemoThemeRepository
from src.data_access.demo.ticker_repository import DemoTickerRepository
from src.data_access.interfaces import (
    CatalystRepository,
    EvidenceRepository,
    MarketDataProvider,
    ResearchAnswerProvider,
    SignalRepository,
    ThemeRepository,
    TickerRepository,
)


@dataclass(frozen=True)
class AppContext:
    theme_repository: ThemeRepository
    ticker_repository: TickerRepository
    evidence_repository: EvidenceRepository
    catalyst_repository: CatalystRepository
    signal_repository: SignalRepository
    market_data_provider: MarketDataProvider
    research_answer_provider: ResearchAnswerProvider


def get_repositories(settings: Settings | None = None) -> AppContext:
    settings = settings or get_settings()
    return AppContext(
        theme_repository=DemoThemeRepository(settings),
        ticker_repository=DemoTickerRepository(settings),
        evidence_repository=DemoEvidenceRepository(settings),
        catalyst_repository=DemoCatalystRepository(settings),
        # Durable-State Phase 2A: JSON (RadarSignalRepository) by default,
        # exactly as before — SQLite (SqliteSignalRepository) only when
        # settings.db_backend is explicitly "sqlite". See
        # backend_factory.py's own module docstring for why this is the
        # one collaborator actually wired here in this phase.
        signal_repository=backend_factory.get_signal_repository(settings),
        market_data_provider=DemoMarketDataProvider(settings),
        research_answer_provider=DemoResearchAnswerProvider(settings),
    )
