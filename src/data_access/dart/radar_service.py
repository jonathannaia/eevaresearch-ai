"""Radar Inbox's wiring layer — this pilot's equivalent of
container.py, but deliberately kept separate from AppContext/
get_repositories(). AppContext wires the demo-data system every other
page reads from; Radar is real, narrowly-scoped live data (Korea DART
pilot only) with its own missing-configuration states, and merging the
two would blur the live/demo boundary the rest of this app is careful to
keep explicit (see src/ui/components/freshness.py).

Client/provider construction lives here, not in the page module, so
run_scan/process_candidate_now are callable and testable from plain
pytest without a Streamlit runtime — same reasoning as container.py's
own docstring.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.config.settings import Settings
from src.config.tracked_companies import TrackedCompany, get_tracked_companies_for_source, with_resolved_corp_codes

_DART_SOURCE = "OpenDART / DART"
from src.data_access import backend_factory
from src.data_access.dart import corp_code_resolver, radar_pipeline, scan_service
from src.data_access.dart.candidate_store import CandidatePersistence
from src.data_access.dart.client import DartClient
from src.data_access.translation.deepl_provider import DeepLProvider
from src.models.models import CandidateSignal


@dataclass(frozen=True)
class RadarReadiness:
    dart_key_configured: bool
    translation_key_configured: bool
    unresolved_companies: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.dart_key_configured and self.translation_key_configured and not self.unresolved_companies


def get_radar_companies(cache_dir: Path, settings: Settings | None = None) -> tuple[TrackedCompany, ...]:
    """DART-only — filtered by source so the SEC EDGAR pilot cohort
    (added in milestone 8, same shared registry) never leaks into a DART
    scan/readiness check. EDGAR has its own get_edgar_companies().

    `settings` is additive and optional (Durable-State Phase 2B) — see
    backend_factory.py's module docstring. Omitted, behavior is
    unchanged. Supplied with `settings.db_backend == "sqlite"`, reads the
    SQLite-backed identifier repository instead of
    the on-disk dart_corp_codes.json cache — a read-only lookup either way,
    never a live resolution. `run_scan`/`process_candidate_now` below
    deliberately do NOT pass `settings` here — see their own note."""
    use_sqlite = settings is not None and (settings.db_backend or "json").strip().lower() == "sqlite"
    if use_sqlite:
        records = backend_factory.get_identifier_repository(settings, _DART_SOURCE).load_identifiers()
        resolved = {krx: record.identifier for krx, record in records.items()}
    else:
        resolved = {krx: record.corp_code for krx, record in corp_code_resolver.load_cached_corp_codes(cache_dir).items()}
    return with_resolved_corp_codes(get_tracked_companies_for_source(_DART_SOURCE), resolved)


def radar_readiness(settings: Settings) -> RadarReadiness:
    """Never raises and never makes a network call — a page-load-time
    check only, so a missing key or an unresolved company shows a clear
    empty state instead of a crash or a silent demo fallback."""
    companies = get_radar_companies(settings.cache_dir, settings)
    unresolved = tuple(c.name for c in companies if not c.corp_code)
    return RadarReadiness(
        dart_key_configured=bool(settings.dart_api_key),
        translation_key_configured=bool(settings.translation_api_key),
        unresolved_companies=unresolved,
    )


def _client(settings: Settings) -> DartClient:
    return DartClient(settings.dart_api_key)


def _translation_provider(settings: Settings) -> DeepLProvider:
    return DeepLProvider(settings.translation_api_key)


def run_scan(
    settings: Settings,
    lookback_days: int = scan_service.DEFAULT_LOOKBACK_DAYS,
    max_candidates: int = radar_pipeline.DEFAULT_MAX_CANDIDATES_PER_SCAN,
    candidate_repository: CandidatePersistence | None = None,
) -> radar_pipeline.ScanReport:
    """`candidate_repository` (Durable-State Phase 4C-1) is additive and
    optional, threaded straight through to radar_pipeline.run_pipeline's
    own parameter of the same name. Omitted — every real caller this
    phase, including scripts/run_scan.py's default invocation — behavior
    is byte-for-byte identical to before: today's JSON candidate_store.py
    path. Supplied — synthetic/local tests only, never a real service
    entry point in production this phase — every candidate-store touch
    inside this one call routes through the given collaborator instead."""
    companies = get_radar_companies(settings.cache_dir)
    return radar_pipeline.run_pipeline(
        _client(settings), _translation_provider(settings), list(companies), settings.cache_dir,
        lookback_days=lookback_days, max_candidates_to_process=max_candidates,
        candidate_repository=candidate_repository,
    )


def process_candidate_now(
    settings: Settings, candidate_id: str, candidate_repository: CandidatePersistence | None = None,
) -> CandidateSignal | None:
    """`candidate_repository` (Durable-State Phase 4C-1) — same additive,
    optional, synthetic/local-test-only seam as run_scan above."""
    return radar_pipeline.process_single_candidate(
        _client(settings), _translation_provider(settings), candidate_id, settings.cache_dir,
        candidate_repository=candidate_repository,
    )
