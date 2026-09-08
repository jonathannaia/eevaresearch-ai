from __future__ import annotations

from src.config.settings import Settings
from src.data_access import loaders
from src.data_access.interfaces import EvidenceRepository
from src.models.models import ClaimType, EvidenceItem

SEED_FILE = "evidence.json"


def _parse_evidence(raw: dict) -> EvidenceItem:
    return EvidenceItem(
        id=raw["id"],
        title=raw["title"],
        source_name=raw["source_name"],
        source_type=raw["source_type"],
        published_at=raw["published_at"],
        retrieved_at=raw["retrieved_at"],
        excerpt=raw["excerpt"],
        claim_type=ClaimType(raw["claim_type"]),
        source_url=raw.get("source_url"),
        is_demo=raw.get("is_demo", True),
        ticker_symbol=raw.get("ticker_symbol"),
        theme_slug=raw.get("theme_slug"),
    )


class DemoEvidenceRepository(EvidenceRepository):
    def __init__(self, settings: Settings):
        self._settings = settings

    def _all(self) -> list[EvidenceItem]:
        raw = loaders.load(self._settings, SEED_FILE, default=[])
        return [_parse_evidence(r) for r in raw]

    def get_evidence_for_ticker(self, symbol: str) -> list[EvidenceItem]:
        return [e for e in self._all() if e.ticker_symbol and e.ticker_symbol.upper() == symbol.upper()]

    def get_evidence_for_theme(self, theme_slug: str) -> list[EvidenceItem]:
        return [e for e in self._all() if e.theme_slug == theme_slug]

    def get_recent_evidence(self, limit: int = 10) -> list[EvidenceItem]:
        return sorted(self._all(), key=lambda e: e.retrieved_at, reverse=True)[:limit]
