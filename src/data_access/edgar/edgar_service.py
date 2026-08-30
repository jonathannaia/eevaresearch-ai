"""SEC EDGAR pilot's wiring layer — mirrors
src/data_access/dart/radar_service.py's shape and reasoning exactly
(kept as a separate module from the DART one, not merged, so each
source has its own independent readiness check and client/provider
construction — see that module's own docstring for why).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.config.settings import Settings
from src.config.tracked_companies import TrackedCompany, get_tracked_companies_for_source, with_resolved_ciks
from src.data_access import backend_factory
from src.data_access.dart.candidate_store import CandidatePersistence
from src.data_access.edgar import cik_resolver, edgar_pipeline, scan_service
from src.data_access.edgar.client import EdgarClient
from src.models.models import CandidateSignal

_EDGAR_SOURCE = "SEC EDGAR"


@dataclass(frozen=True)
class EdgarReadiness:
    user_agent_configured: bool
    unresolved_companies: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.user_agent_configured and not self.unresolved_companies


def get_edgar_companies(cache_dir: Path, settings: Settings | None = None) -> tuple[TrackedCompany, ...]:
    """`settings` is additive and optional (Durable-State Phase 2B;
    extended to Postgres in Phase 4M-0) — see backend_factory.py's
    module docstring. Omitted, behavior is unchanged (reads the on-disk
    edgar_ciks.json cache via cik_resolver directly). Supplied with
    `settings.db_backend` of `"sqlite"` or `"postgres"`, reads the
    matching backend-specific identifier repository instead — a
    read-only lookup either way, never a live resolution; this function
    itself never calls cik_resolver.resolve_and_cache()."""
    backend = (settings.db_backend or "json").strip().lower() if settings is not None else "json"
    use_repository_backend = backend in ("sqlite", "postgres")
    if use_repository_backend:
        records = backend_factory.get_identifier_repository(settings, _EDGAR_SOURCE).load_identifiers()
        resolved = {ticker: record.identifier for ticker, record in records.items()}
    else:
        resolved = {ticker: record.cik for ticker, record in cik_resolver.load_cached_ciks(cache_dir).items()}
    return with_resolved_ciks(get_tracked_companies_for_source(_EDGAR_SOURCE), resolved)


def edgar_readiness(settings: Settings) -> EdgarReadiness:
    """Never raises and never makes a network call — a page-load-time
    check only, so a missing User-Agent or an unresolved company shows a
    clear empty state instead of a crash or a silent demo fallback."""
    companies = get_edgar_companies(settings.cache_dir, settings)
    unresolved = tuple(c.name for c in companies if not c.corp_code)
    return EdgarReadiness(
        user_agent_configured=bool(settings.edgar_user_agent),
        unresolved_companies=unresolved,
    )


def _client(settings: Settings) -> EdgarClient:
    return EdgarClient(settings.edgar_user_agent)


def run_scan(
    settings: Settings,
    lookback_days: int = scan_service.DEFAULT_LOOKBACK_DAYS,
    max_candidates: int = edgar_pipeline.DEFAULT_MAX_CANDIDATES_PER_SCAN,
    candidate_repository: CandidatePersistence | None = None,
) -> edgar_pipeline.ScanReport:
    """`candidate_repository` (Durable-State Phase 4A) is additive and
    optional, threaded straight through to edgar_pipeline.run_pipeline's
    own parameter of the same name. Omitted — every real caller this
    phase, including scripts/run_scan.py's default invocation — behavior
    is byte-for-byte identical to before: today's JSON candidate_store.py
    path. Supplied — synthetic/local tests only, never a real service
    entry point in production this phase — every candidate-store touch
    inside this one call routes through the given collaborator instead.
    See edgar_pipeline.run_pipeline's own docstring for the full
    single-backend-atomicity contract this inherits unchanged.

    Durable-State Phase 4M-0: `settings` is now passed through to
    get_edgar_companies() (previously omitted here), so a `"sqlite"`/
    `"postgres"` `db_backend` resolves identifiers from that backend's
    own identifier repository instead of always the on-disk JSON cache.
    This is a no-op for every existing caller: `(settings.db_backend or
    "json")` already defaults to `"json"`, the exact branch this
    function always took before. `auto_publish_enabled` is unchanged and
    still reads `settings.edgar_auto_publish_enabled` directly — a
    separate, pre-existing feature this phase does not alter. Any
    caller that must guarantee this scan can never autonomously publish
    (scripts/radar_worker.py does) is responsible for passing a
    `settings` object with `edgar_auto_publish_enabled=False` forced —
    see that script's own docstring for why."""
    companies = get_edgar_companies(settings.cache_dir, settings)
    return edgar_pipeline.run_pipeline(
        _client(settings), list(companies), settings.cache_dir,
        lookback_days=lookback_days, max_candidates_to_process=max_candidates,
        candidate_repository=candidate_repository,
        auto_publish_enabled=settings.edgar_auto_publish_enabled,
        scan_interval_minutes=settings.radar_scan_interval_minutes,
    )


def process_candidate_now(
    settings: Settings, candidate_id: str, candidate_repository: CandidatePersistence | None = None,
) -> CandidateSignal | None:
    """`candidate_repository` (Durable-State Phase 4A) — same additive,
    optional, synthetic/local-test-only seam as run_scan above."""
    return edgar_pipeline.process_single_candidate(
        _client(settings), candidate_id, settings.cache_dir,
        candidate_repository=candidate_repository,
        auto_publish_enabled=settings.edgar_auto_publish_enabled,
    )
