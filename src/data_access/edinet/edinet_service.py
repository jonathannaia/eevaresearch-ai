"""EDINET pilot's wiring layer (Japan radar pilot). Mirrors
src/data_access/edgar/edgar_service.py's shape exactly (kept as a
separate module from the DART/EDGAR ones, not merged, so each source has
its own independent readiness check and client/provider construction).

Status (corrected — Phase F1, design/DECISIONS.md): `get_edinet_companies`
no longer returns an empty tuple. Since Gate 7, five real EDINET tracked
companies exist in src/config/tracked_companies.py (SoftBank Group,
Kioxia Holdings, Furukawa Electric, FANUC, ispace) with their
`corp_code`/`krx_code` already hardcoded (independently live-verified —
see that module's own docstring), not resolved via a runtime cache the
way DART/EDGAR are. `edinet_readiness(settings).ready` is therefore
already True the moment `EDGE_EDINET_SUBSCRIPTION_KEY` is configured —
there is no separate code-level gate beyond that ordinary readiness
check. EDINET remaining unscanned in production today is a **deployment/
policy choice** (the subscription key has never been configured
anywhere), not a code limitation — see design/RADAR_WORKER_DEPLOYMENT.md
and radar_inbox.py's own "EDINET has never had a live scan run against
it" framing. This module's own `run_scan`/`edinet_readiness` shape was
unchanged by this correction; only this docstring's factual claim was
wrong.
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
    """No EDINET-code resolution cache is read here at all — not because
    no tracked company has an EDINET code (all five do, hardcoded — see
    module docstring), but because none of them ever need runtime
    resolution in the first place. `cache_dir` is accepted only to keep
    this function's shape parallel to edgar_service.get_edgar_companies/
    dart.radar_service.get_radar_companies, which do need it."""
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
        scan_interval_minutes=settings.radar_scan_interval_minutes,
    )


def process_candidate_now(
    settings: Settings, candidate_id: str, candidate_repository: CandidatePersistence | None = None,
) -> CandidateSignal | None:
    """`candidate_repository` (Durable-State Phase 4C-1) — same additive,
    optional, synthetic/local-test-only seam as run_scan above."""
    return edinet_pipeline.process_single_candidate(
        _client(settings), candidate_id, settings.cache_dir, candidate_repository=candidate_repository,
    )
