"""Repository/provider interfaces. Pages and UI components depend on these,
never on a raw seed filename or JSON shape — this is the swap point Phase 2
uses to replace demo data with real sources (SEC filings, a licensed market
data provider, news/evidence ingestion, an LLM retrieval layer) without
touching any page-rendering code. See src/data_access/container.py for the
single place implementations are wired up, and src/data_access/demo/ for
the Phase 1 implementations.

Deliberately simple: plain ABCs, no dependency-injection framework, no
database. One implementation per interface today; Phase 2 adds a second
implementation and repoints container.py.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from src.models.models import (
    CapitalRotationMetric,
    Catalyst,
    ChatAnswer,
    EvidenceItem,
    Signal,
    Theme,
    Ticker,
)


class ThemeRepository(ABC):
    @abstractmethod
    def get_all_themes(self) -> list[Theme]: ...

    @abstractmethod
    def get_theme(self, slug: str) -> Theme | None: ...


class TickerRepository(ABC):
    @abstractmethod
    def get_ticker(self, symbol: str) -> Ticker | None: ...

    @abstractmethod
    def get_tickers_for_theme(self, theme_slug: str) -> list[Ticker]: ...

    @abstractmethod
    def get_all_tickers(self) -> list[Ticker]: ...


class EvidenceRepository(ABC):
    @abstractmethod
    def get_evidence_for_ticker(self, symbol: str) -> list[EvidenceItem]: ...

    @abstractmethod
    def get_evidence_for_theme(self, theme_slug: str) -> list[EvidenceItem]: ...

    @abstractmethod
    def get_recent_evidence(self, limit: int = 10) -> list[EvidenceItem]: ...


class CatalystRepository(ABC):
    @abstractmethod
    def get_catalysts_for_theme(self, theme_slug: str) -> list[Catalyst]: ...

    @abstractmethod
    def get_catalysts_for_ticker(self, symbol: str) -> list[Catalyst]: ...

    @abstractmethod
    def get_upcoming_catalysts(self, limit: int = 10) -> list[Catalyst]: ...


class SignalRepository(ABC):
    @abstractmethod
    def get_all_signals(self) -> list[Signal]: ...

    @abstractmethod
    def get_signals_for_theme(self, theme_slug: str) -> list[Signal]: ...


class MarketDataProvider(ABC):
    @abstractmethod
    def get_rotation_metrics(self) -> list[CapitalRotationMetric]: ...

    @abstractmethod
    def get_rotation_metric_for_theme(self, theme_slug: str) -> CapitalRotationMetric | None: ...


class ResearchAnswerProvider(ABC):
    @abstractmethod
    def get_answer(self, question: str) -> ChatAnswer: ...

    @abstractmethod
    def get_suggested_questions(self) -> list[str]: ...
