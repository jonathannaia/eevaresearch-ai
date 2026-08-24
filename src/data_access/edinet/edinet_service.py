"""EDINET pilot's wiring layer (Japan radar pilot, planning Gate 1).
Mirrors src/data_access/edgar/edgar_service.py's shape exactly (kept as a
separate module from the DART/EDGAR ones, not merged, so each source has
its own independent readiness check and client/provider construction).

Gate 1 status: `get_edinet_companies` will always return an empty tuple
in real use this gate — no company has been added to tracked
configuration for source "EDINET" (explicitly forbidden this gate, see
edinet_code_resolver.py's docstring). `edinet_readiness` and `run_scan`
are still wired up now so a later gate can add tracked companies and a
resolved-CIK-equivalent cache without touching this module's shape.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.config.settings import Settings
from src.config.tracked_companies import TrackedCompany, get_tracked_companies_for_source
from src.data_access.dart.candidate_store import CandidatePersistence
from src.data_access.edinet import edinet_pipeline, scan_service
from src.data_access.edinet.client import EdinetClient
from src.models.models import CandidateSignal

_EDINET_SOURCE = "EDINET"


@dataclass(frozen=True)
class EdinetReadiness:
    subscription_key_configured: bool
    unresolved_companies: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.subscription_key_configured and not self.unresolved_companies


def get_edinet_companies(cache_dir: Path) -> tuple[TrackedCompany, ...]:
    """No EDINET-code resolution cache is read here yet — no tracked
    company has an EDINET code to resolve this gate (see module
    docstring). Kept parallel in shape to
    edgar_service.get_edgar_companies so a later gate's addition (an
    EDINET-code-cache lookup, mirroring cik_resolver's cache) is a small,
    additive change rather than a new pattern."""
    return get_tracked_companies_for_source(_EDINET_SOURCE)


def edinet_readiness(settings: Settings) -> EdinetReadiness:
    """Never raises and never makes a network call — a page-load-time
    check only. This gate never reads/validates the real credential
    value (see errors.py's EdinetConfigError docstring) — only whether
    something non-empty is configured."""
    companies = get_edinet_companies(settings.cache_dir)
    unresolved = tuple(c.name for c in companies if not c.corp_code)
    return EdinetReadiness(
        subscription_key_configured=bool(settings.edinet_subscription_key),
        unresolved_companies=unresolved,
    )


def _client(settings: Settings) -> EdinetClient:
    return EdinetClient(settings.edinet_subscription_key)


def run_scan(
    settings: Settings,
    lookback_days: int = scan_service.DEFAULT_LOOKBACK_DAYS,
    max_candidates: int = edinet_pipeline.DEFAULT_MAX_CANDIDATES_PER_SCAN,
    candidate_repository: CandidatePersistence | None = None,
) -> edinet_pipeline.ScanReport:
    """`candidate_repository` (Durable-State Phase 4C-1) is additive and
    optional, threaded straight through to edinet_pipeline.run_pipeline's
    own parameter of the same name. Omitted — every real caller this
    phase, including scripts/run_scan.py's default invocation — behavior
    is byte-for-byte identical to before: today's JSON candidate_store.py
    path. Supplied — synthetic/local tests only, never a real service
    entry point in production this phase — every candidate-store touch
    inside this one call routes through the given collaborator instead."""
    companies = get_edinet_companies(settings.cache_dir)
    return edinet_pipeline.run_pipeline(
        _client(settings), list(companies), settings.cache_dir,
        lookback_days=lookback_days, max_candidates_to_process=max_candidates,
        candidate_repository=candidate_repository,
    )


def process_candidate_now(
    settings: Settings, candidate_id: str, candidate_repository: CandidatePersistence | None = None,
) -> CandidateSignal | None:
    """`candidate_repository` (Durable-State Phase 4C-1) — same additive,
    optional, synthetic/local-test-only seam as run_scan above."""
    return edinet_pipeline.process_single_candidate(
        _client(settings), candidate_id, settings.cache_dir, candidate_repository=candidate_repository,
    )
