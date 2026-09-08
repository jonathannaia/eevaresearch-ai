"""Durable-State — the one composition seam that decides JSON-backed vs.
SQLite-backed collaborators, based on `Settings.db_backend`. No source
service (a pipeline, a resolver, a UI page) imports `sqlite3` or picks
its own storage here — this module is the single place that does,
matching the existing `container.py` composition-root pattern exactly
(its own docstring: "Phase 2 swaps in real implementations here only,
without touching any page or component code").

**Phase 2A** wired only `get_signal_repository()` into the running app
(via `container.py`). **Phase 2B** additionally wires the standalone,
already-network-free application-facing paths: Radar Inbox's display
read path (`radar_inbox._build_items`/`_edinet_scope_line`), human
review decisions (`review_actions.record_review_decision`), and
identifier-cache reads used for readiness/company resolution
(`edgar_service.get_edgar_companies`, `dart/radar_service.
get_radar_companies`) — each via an **additive, optional `settings`
parameter** that preserves every existing caller/test exactly when
omitted.

**Deliberately NOT wired this phase, and why**: `run_pipeline()`/
`process_single_candidate()` in all three pipelines
(`edgar_pipeline.py`, `radar_pipeline.py`, `edinet_pipeline.py`) call
`candidate_store.upsert_new_candidates()`/`update_candidate()` from
*inside* the same function bodies that make live network calls
(`document_service`, translation, `scan_service.scan()` itself) — there
is no already-separated persistence-only seam within those functions to
swap without restructuring live, already-production-validated control
flow (these exact pipelines processed real AMD/Marvell/SK Hynix/
Trio-Tech/Rocket Lab filings earlier this session). Candidate
persistence *during a scan or a "Process" action* therefore remains
JSON-only regardless of `EDGE_DB_BACKEND` — a deliberate, documented
limitation, not an oversight (see design/DECISIONS.md). EDINET's
identifier cache has no equivalent read function to wire at all: its
five tracked companies' identifiers are hardcoded directly in
`tracked_companies.py`, never resolved from a runtime cache (confirmed
by that module's own docstring) — not a gap, genuinely not applicable.
`candidate_backfill.py` is out of scope too — a separate, already-
executed, one-off production tool with its own bespoke atomic
multi-file rollback transaction, not an ongoing pipeline path.

"json" (the default, whenever `EDGE_DB_BACKEND` is unset/blank/
unrecognized) always returns exactly today's existing collaborators,
completely unchanged. "sqlite" requires an explicit, non-empty
`EDGE_STATE_DB_PATH` — selecting sqlite without one raises
`BackendConfigurationError` rather than silently falling back to JSON.

Durable-State Phase 4B adds "postgres" alongside "sqlite" — an
isolated, independently-implemented backend (`src/data_access/
postgres_state_db/`, no shared code with `state_db/`, per that phase's
explicit no-dialect-abstraction constraint) requiring an explicit,
non-empty `EDGE_STATE_DB_URL`; selecting postgres without one raises
the same `BackendConfigurationError`, never a silent JSON fallback. Local-
test-only this phase (see design/DECISIONS.md's Phase 4B-0/4B-1
records) — no real service entry point selects it; it is reachable only
through direct calls to this module's own factory functions, exactly
matching how "sqlite" itself was introduced in Phase 2A before later,
separate phases wired it into specific call sites. Unlike the sqlite
path, a postgres connection-establishment failure is deliberately
reported as `BackendConfigurationError` with only the underlying
exception's class name — never `str(exc)`, the DSN, host, port,
database, role, user, or password — since a real network connection
error can embed exactly that information in its message text, unlike a
local SQLite file-path error."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

import psycopg

from src.config.settings import Settings
from src.data_access import comparison_store
from src.data_access import research_store
from src.data_access import theme_matching_store
from src.data_access import theme_store
from src.data_access.comparison_store import ComparisonRecord
from src.data_access.dart import candidate_store
from src.data_access.dart import corp_code_resolver
from src.data_access.dart import scan_service as dart_scan_service
from src.data_access.edgar import cik_resolver
from src.data_access.edgar import edgar_pipeline
from src.data_access.edgar import scan_service as edgar_scan_service
from src.data_access.edinet import edinet_pipeline
from src.data_access.edinet import scan_service as edinet_scan_service
from src.data_access.interfaces import SignalRepository
from src.data_access.live.radar_signal_repository import RadarSignalRepository
from src.data_access.postgres_state_db import candidate_repository as postgres_candidates
from src.data_access.postgres_state_db import comparison_repository as postgres_comparisons
from src.data_access.postgres_state_db import connection as postgres_state_db_connection
from src.data_access.postgres_state_db import filing_event_repository as postgres_filing_events
from src.data_access.postgres_state_db import identifier_repository as postgres_identifiers
from src.data_access.postgres_state_db import research_repository as postgres_research
from src.data_access.postgres_state_db import theme_matching_repository as postgres_theme_matching
from src.data_access.postgres_state_db import theme_repository as postgres_themes
from src.data_access.postgres_state_db import scan_status_repository as postgres_scan_status
from src.data_access.postgres_state_db import schema as postgres_schema
from src.data_access.postgres_state_db.identifier_repository import (
    ResolvedIdentifierRecord as PostgresResolvedIdentifierRecord,
)
from src.data_access.postgres_state_db.scan_status_repository import ProviderScanStatus as PostgresProviderScanStatus
from src.data_access.postgres_state_db.signal_repository import PostgresSignalRepository
from src.data_access.state_db import candidate_repository as sqlite_candidates
from src.data_access.state_db import comparison_repository as sqlite_comparisons
from src.data_access.state_db import connection as state_db_connection
from src.data_access.state_db import filing_event_repository as sqlite_filing_events
from src.data_access.state_db import identifier_repository as sqlite_identifiers
from src.data_access.state_db import research_repository as sqlite_research
from src.data_access.state_db import theme_matching_repository as sqlite_theme_matching
from src.data_access.state_db import theme_repository as sqlite_themes
from src.data_access.state_db import scan_status_repository as sqlite_scan_status
from src.data_access.state_db import schema as state_db_schema
from src.data_access.state_db.identifier_repository import ResolvedIdentifierRecord
from src.data_access.state_db.scan_status_repository import ProviderScanStatus
from src.data_access.state_db.signal_repository import SqliteSignalRepository
from src.models.models import CandidateSignal, FilingEvent
from src.logic.research_case_validation import ResearchCaseBundle
from src.models.research_case import DependencyAssertion, RelationshipAssertion, ResearchCase, ResearchEvidenceItem
from src.models.theme_matching import ResearchCaseThemeMatch, ThemeMatchingScope, ThemeMatchReviewDecision
from src.models.theme_research import (
    ResearchTheme,
    ThemeCompanyMapEntry,
    ThemeEvidenceItem,
    ThemeResearchNote,
    ThemeVisibility,
)

_CANDIDATE_FILENAME_BY_SOURCE = {
    "OpenDART / DART": "dart_candidates.json",
    "SEC EDGAR": edgar_pipeline.CANDIDATE_STORE_FILENAME,
    "EDINET": edinet_pipeline.CANDIDATE_STORE_FILENAME,
}
_FILING_EVENTS_LOADER_BY_SOURCE = {
    "OpenDART / DART": dart_scan_service.load_filing_events,
    "SEC EDGAR": edgar_scan_service.load_filing_events,
    "EDINET": edinet_scan_service.load_filing_events,
}


class BackendConfigurationError(Exception):
    """Raised when db_backend="sqlite" is explicitly selected but the
    configuration needed to use it is missing or invalid — never a
    silent fallback to JSON. Raised before any database is opened."""


def _normalized_backend(settings: Settings) -> str:
    return (settings.db_backend or "json").strip().lower()


def _require_sqlite_connection(settings: Settings) -> sqlite3.Connection:
    path_value = settings.state_db_path
    if not path_value or not str(path_value).strip():
        raise BackendConfigurationError(
            "EDGE_DB_BACKEND=sqlite requires an explicit, non-empty EDGE_STATE_DB_PATH — "
            "none was configured. Refusing to silently use the JSON backend instead."
        )
    conn = state_db_connection.connect(Path(path_value))
    state_db_schema.migrate(conn)
    return conn


def _require_postgres_connection(settings: Settings) -> psycopg.Connection:
    """Durable-State Phase 4B. Same fail-closed discipline as
    _require_sqlite_connection above — an explicit, non-empty
    `EDGE_STATE_DB_URL` is required, and a missing one raises before any
    connection is attempted. A genuine connection-establishment failure
    is caught and re-raised with only the exception's class name (see
    this module's own docstring for why) — never str(exc), which for a
    real network driver can embed host/port/dbname/user."""
    dsn = settings.state_db_url
    if not dsn or not str(dsn).strip():
        raise BackendConfigurationError(
            "EDGE_DB_BACKEND=postgres requires an explicit, non-empty EDGE_STATE_DB_URL — "
            "none was configured. Refusing to silently use the JSON backend instead."
        )
    try:
        conn = postgres_state_db_connection.connect(dsn)
    except psycopg.Error as exc:
        raise BackendConfigurationError(
            f"EDGE_DB_BACKEND=postgres connection attempt failed ({type(exc).__name__}). "
            "No connection information is included in this message."
        ) from None
    postgres_schema.migrate(conn)
    return conn


# --- Signal repository — the one factory actually wired into container.py ---

def get_signal_repository(settings: Settings) -> SignalRepository:
    backend = _normalized_backend(settings)
    if backend == "sqlite":
        return SqliteSignalRepository(_require_sqlite_connection(settings))
    if backend == "postgres":
        return PostgresSignalRepository(_require_postgres_connection(settings))
    return RadarSignalRepository(settings)


# --- Candidate repository — factory exists and is tested; not yet wired into any pipeline ---

@dataclass(frozen=True)
class UpdateOutcome:
    status: str  # "updated" | "conflict" | "not_found" — see each adapter's own docstring
    current: CandidateSignal | None


class CandidateRepositoryProtocol(Protocol):
    def load_candidates(self) -> dict[str, CandidateSignal]: ...
    def get_candidate(self, candidate_id: str) -> CandidateSignal | None: ...
    def get_candidate_version(self, candidate_id: str) -> int | None: ...
    def upsert_new_candidates(self, new_candidates: list[CandidateSignal]) -> dict[str, CandidateSignal]: ...
    def update_candidate(self, candidate: CandidateSignal, expected_version: int | None = None) -> UpdateOutcome: ...
    # Durable-State Phase 4M-2 (Stage 0) — see CandidatePersistence's own
    # copy of this method in src/data_access/dart/candidate_store.py for
    # the full contract; declared identically here so this Protocol's own
    # structural shape stays a superset of that one.
    def upsert_filing_events_only(self, filings: list[FilingEvent]) -> None: ...


@dataclass(frozen=True)
class JsonCandidateRepository:
    """Wraps candidate_store.py exactly as-is — no new write path, no
    format change. update_candidate() always succeeds (JSON has no
    optimistic-concurrency concept — see candidate_store.update_candidate's
    own docstring on why that's an accepted, documented pilot-scale
    assumption); `expected_version` is accepted for Protocol compatibility
    and ignored, matching that real behavior honestly rather than faking
    a lock JSON doesn't have."""

    cache_dir: Path
    filename: str

    def load_candidates(self) -> dict[str, CandidateSignal]:
        return candidate_store.load_candidates(self.cache_dir, self.filename)

    def get_candidate(self, candidate_id: str) -> CandidateSignal | None:
        return self.load_candidates().get(candidate_id)

    def get_candidate_version(self, candidate_id: str) -> int | None:
        # JSON has no version concept at all — always None, honestly,
        # rather than faking a number update_candidate() below ignores
        # anyway (see this class's own docstring).
        return None

    def upsert_new_candidates(self, new_candidates: list[CandidateSignal]) -> dict[str, CandidateSignal]:
        return candidate_store.upsert_new_candidates(self.cache_dir, new_candidates, self.filename)

    def update_candidate(self, candidate: CandidateSignal, expected_version: int | None = None) -> UpdateOutcome:
        candidate_store.update_candidate(self.cache_dir, candidate, self.filename)
        return UpdateOutcome(status="updated", current=candidate)

    def upsert_filing_events_only(self, filings: list[FilingEvent]) -> None:
        # No-op: scan_service.scan() (for every source) already writes
        # every scanned FilingEvent into its own on-disk JSON cache
        # unconditionally, matched or not, before this method could ever
        # be called — the JSON backend has no equivalent gap to close.
        return None


@dataclass(frozen=True)
class SqliteCandidateRepository:
    """`update_candidate`'s `expected_version` is a real optimistic-lock
    check when the caller supplies one obtained from its own earlier
    `get_candidate`/`get_candidate_version` call — a mismatch at write
    time means someone else changed the row in between, and the write is
    rejected (see UpdateOutcome). Left as `None` (the default), this
    class re-reads the current version immediately before writing —
    convenient for a caller that only wants "write, don't care about
    races" (matches Phase 2A's original scope), but that path provides
    *no* actual conflict protection, since the version it compares
    against is fetched fresh rather than carried from an earlier read.
    `review_actions.record_review_decision` (Phase 2B) always supplies
    an explicit `expected_version` for exactly this reason."""

    conn: sqlite3.Connection
    source: str

    def load_candidates(self) -> dict[str, CandidateSignal]:
        return sqlite_candidates.load_candidates(self.conn, self.source)

    def get_candidate(self, candidate_id: str) -> CandidateSignal | None:
        return sqlite_candidates.get_candidate(self.conn, candidate_id)

    def get_candidate_version(self, candidate_id: str) -> int | None:
        return sqlite_candidates.get_candidate_version(self.conn, candidate_id)

    def upsert_new_candidates(self, new_candidates: list[CandidateSignal]) -> dict[str, CandidateSignal]:
        return sqlite_candidates.upsert_new_candidates(self.conn, self.source, new_candidates)

    def update_candidate(self, candidate: CandidateSignal, expected_version: int | None = None) -> UpdateOutcome:
        if expected_version is None:
            expected_version = sqlite_candidates.get_candidate_version(self.conn, candidate.id) or 1
        outcome = sqlite_candidates.update_candidate(self.conn, candidate, expected_version)
        return UpdateOutcome(status=outcome.status, current=outcome.current)

    def upsert_filing_events_only(self, filings: list[FilingEvent]) -> None:
        for filing in filings:
            sqlite_filing_events.upsert_filing_event(self.conn, filing)


@dataclass(frozen=True)
class PostgresCandidateRepository:
    """Durable-State Phase 4B — the isolated Postgres counterpart to
    SqliteCandidateRepository above, same `expected_version` re-read
    convenience/no-real-conflict-protection caveat when omitted, backed
    by src/data_access/postgres_state_db/candidate_repository.py instead
    of the SQLite module. Local-test-only this phase; never constructed
    by any real service entry point (see this module's own docstring)."""

    conn: psycopg.Connection
    source: str

    def load_candidates(self) -> dict[str, CandidateSignal]:
        return postgres_candidates.load_candidates(self.conn, self.source)

    def get_candidate(self, candidate_id: str) -> CandidateSignal | None:
        return postgres_candidates.get_candidate(self.conn, candidate_id)

    def get_candidate_version(self, candidate_id: str) -> int | None:
        return postgres_candidates.get_candidate_version(self.conn, candidate_id)

    def upsert_new_candidates(self, new_candidates: list[CandidateSignal]) -> dict[str, CandidateSignal]:
        return postgres_candidates.upsert_new_candidates(self.conn, self.source, new_candidates)

    def update_candidate(self, candidate: CandidateSignal, expected_version: int | None = None) -> UpdateOutcome:
        if expected_version is None:
            expected_version = postgres_candidates.get_candidate_version(self.conn, candidate.id) or 1
        outcome = postgres_candidates.update_candidate(self.conn, candidate, expected_version)
        return UpdateOutcome(status=outcome.status, current=outcome.current)

    def upsert_filing_events_only(self, filings: list[FilingEvent]) -> None:
        for filing in filings:
            postgres_filing_events.upsert_filing_event(self.conn, filing)


def get_candidate_repository(settings: Settings, source: str) -> CandidateRepositoryProtocol:
    """`source` is one of "OpenDART / DART" / "SEC EDGAR" / "EDINET" —
    the same source_name strings used throughout this codebase."""
    backend = _normalized_backend(settings)
    if backend == "sqlite":
        return SqliteCandidateRepository(conn=_require_sqlite_connection(settings), source=source)
    if backend == "postgres":
        return PostgresCandidateRepository(conn=_require_postgres_connection(settings), source=source)
    return JsonCandidateRepository(cache_dir=settings.cache_dir, filename=_CANDIDATE_FILENAME_BY_SOURCE[source])


_SOURCE_BY_CANDIDATE_FILENAME = {filename: source for source, filename in _CANDIDATE_FILENAME_BY_SOURCE.items()}


def source_for_candidate_filename(filename: str) -> str:
    """Reverse lookup for callers (review_actions.py) that only have the
    JSON filename on hand — the same routing-by-id-prefix convention
    radar_inbox.py already uses to pick that filename in the first
    place."""
    return _SOURCE_BY_CANDIDATE_FILENAME[filename]


# --- Filing-event repository — read-only in both backends (see module docstring) ---
#
# Neither backend exposes a standalone, non-network "write one filing
# event" path in production: the JSON stores only ever write inside
# scan_service.scan() (a live-network-calling function, correctly out of
# scope this phase); the SQLite store's own upsert_filing_event() exists
# but has no real caller yet either (see candidate_repository above for
# the same "factory exists, not yet wired" posture). This adapter is
# read-only for both, matching what's actually safe/legitimate to expose
# without a scan.

class FilingEventRepositoryProtocol(Protocol):
    def load_filing_events(self) -> tuple[FilingEvent, ...]: ...
    def exists(self, corp_code: str, rcept_no: str) -> bool: ...


@dataclass(frozen=True)
class JsonFilingEventRepository:
    cache_dir: Path
    source: str

    def load_filing_events(self) -> tuple[FilingEvent, ...]:
        loader = _FILING_EVENTS_LOADER_BY_SOURCE[self.source]
        return loader(self.cache_dir)

    def exists(self, corp_code: str, rcept_no: str) -> bool:
        return any(f.corp_code == corp_code and f.rcept_no == rcept_no for f in self.load_filing_events())


@dataclass(frozen=True)
class SqliteFilingEventRepository:
    conn: sqlite3.Connection
    source: str

    def load_filing_events(self) -> tuple[FilingEvent, ...]:
        return sqlite_filing_events.load_filing_events(self.conn, self.source)

    def exists(self, corp_code: str, rcept_no: str) -> bool:
        return sqlite_filing_events.filing_event_exists(self.conn, self.source, corp_code, rcept_no)


@dataclass(frozen=True)
class PostgresFilingEventRepository:
    """Durable-State Phase 4B — the isolated Postgres counterpart to
    SqliteFilingEventRepository above. Read-only in the same sense as
    the SQLite/JSON adapters (see this section's own header comment
    above SqliteFilingEventRepository) — no standalone, non-network
    write path exists in production for any backend this phase."""

    conn: psycopg.Connection
    source: str

    def load_filing_events(self) -> tuple[FilingEvent, ...]:
        return postgres_filing_events.load_filing_events(self.conn, self.source)

    def exists(self, corp_code: str, rcept_no: str) -> bool:
        return postgres_filing_events.filing_event_exists(self.conn, self.source, corp_code, rcept_no)


def get_filing_event_repository(settings: Settings, source: str) -> FilingEventRepositoryProtocol:
    backend = _normalized_backend(settings)
    if backend == "sqlite":
        return SqliteFilingEventRepository(conn=_require_sqlite_connection(settings), source=source)
    if backend == "postgres":
        return PostgresFilingEventRepository(conn=_require_postgres_connection(settings), source=source)
    return JsonFilingEventRepository(cache_dir=settings.cache_dir, source=source)


# --- Identifier repository — read-only in both backends, same reasoning as filing events ---
#
# resolve_and_cache() (both EDGAR's and DART's) bundles writing inside a
# live-network-calling function too — out of scope this phase. Read-only
# here for the same honesty-over-convenience reason as filing events.

class IdentifierRepositoryProtocol(Protocol):
    def load_identifiers(self) -> dict[str, ResolvedIdentifierRecord]: ...
    def get_identifier(self, lookup_key: str) -> ResolvedIdentifierRecord | None: ...


@dataclass(frozen=True)
class JsonIdentifierRepository:
    cache_dir: Path
    source: str  # "SEC EDGAR" | "OpenDART / DART"

    def load_identifiers(self) -> dict[str, ResolvedIdentifierRecord]:
        if self.source == "SEC EDGAR":
            cached = cik_resolver.load_cached_ciks(self.cache_dir)
            return {
                ticker: ResolvedIdentifierRecord(
                    identifier=r.cik, display_name=r.company_name,
                    resolution_method=r.source, retrieved_at=r.retrieved_at,
                )
                for ticker, r in cached.items()
            }
        cached = corp_code_resolver.load_cached_corp_codes(self.cache_dir)
        return {
            krx: ResolvedIdentifierRecord(
                identifier=r.corp_code, display_name=r.corp_name,
                resolution_method=r.source, retrieved_at=r.retrieved_at,
            )
            for krx, r in cached.items()
        }

    def get_identifier(self, lookup_key: str) -> ResolvedIdentifierRecord | None:
        return self.load_identifiers().get(lookup_key)


@dataclass(frozen=True)
class SqliteIdentifierRepository:
    conn: sqlite3.Connection
    source: str

    def load_identifiers(self) -> dict[str, ResolvedIdentifierRecord]:
        return sqlite_identifiers.load_resolved_identifiers(self.conn, self.source)

    def get_identifier(self, lookup_key: str) -> ResolvedIdentifierRecord | None:
        return sqlite_identifiers.get_resolved_identifier(self.conn, self.source, lookup_key)


@dataclass(frozen=True)
class PostgresIdentifierRepository:
    """Durable-State Phase 4B — the isolated Postgres counterpart to
    SqliteIdentifierRepository above. Read-only, same reasoning as
    SqliteIdentifierRepository's own header comment."""

    conn: psycopg.Connection
    source: str

    def load_identifiers(self) -> dict[str, PostgresResolvedIdentifierRecord]:
        return postgres_identifiers.load_resolved_identifiers(self.conn, self.source)

    def get_identifier(self, lookup_key: str) -> PostgresResolvedIdentifierRecord | None:
        return postgres_identifiers.get_resolved_identifier(self.conn, self.source, lookup_key)


def get_identifier_repository(settings: Settings, source: str) -> IdentifierRepositoryProtocol:
    backend = _normalized_backend(settings)
    if backend == "sqlite":
        return SqliteIdentifierRepository(conn=_require_sqlite_connection(settings), source=source)
    if backend == "postgres":
        return PostgresIdentifierRepository(conn=_require_postgres_connection(settings), source=source)
    return JsonIdentifierRepository(cache_dir=settings.cache_dir, source=source)


# --- Provider scan-status/cursor repository — sqlite/postgres only ---
#
# Durable-State Phase 4M-0. Deliberately no JSON implementation and no
# JSON fallback: JSON is not safe shared persistence for a separate
# dashboard + continuous-worker process pair (see
# design/RADAR_WORKER_DEPLOYMENT.md), and scripts/run_scan.py's existing
# one-shot invocation needs no cursor at all — its own idempotent
# FilingEvent dedup is sufficient for a single, manually-triggered run.
# Only scripts/radar_worker.py calls get_scan_status_repository(); no
# existing pipeline, page, or component does.

class ScanStatusRepositoryProtocol(Protocol):
    def get_scan_status(self, provider: str) -> ProviderScanStatus | None: ...
    def get_all_scan_statuses(self) -> dict[str, ProviderScanStatus]: ...
    def upsert_scan_status(self, status: ProviderScanStatus) -> None: ...


@dataclass(frozen=True)
class SqliteScanStatusRepository:
    conn: sqlite3.Connection

    def get_scan_status(self, provider: str) -> ProviderScanStatus | None:
        return sqlite_scan_status.get_scan_status(self.conn, provider)

    def get_all_scan_statuses(self) -> dict[str, ProviderScanStatus]:
        return sqlite_scan_status.get_all_scan_statuses(self.conn)

    def upsert_scan_status(self, status: ProviderScanStatus) -> None:
        sqlite_scan_status.upsert_scan_status(self.conn, status)


@dataclass(frozen=True)
class PostgresScanStatusRepository:
    conn: psycopg.Connection

    def get_scan_status(self, provider: str) -> PostgresProviderScanStatus | None:
        return postgres_scan_status.get_scan_status(self.conn, provider)

    def get_all_scan_statuses(self) -> dict[str, PostgresProviderScanStatus]:
        return postgres_scan_status.get_all_scan_statuses(self.conn)

    def upsert_scan_status(self, status: PostgresProviderScanStatus) -> None:
        postgres_scan_status.upsert_scan_status(self.conn, status)


def get_scan_status_repository(settings: Settings) -> ScanStatusRepositoryProtocol:
    """Unlike every other factory function in this module, this one has
    no JSON branch at all — `db_backend` of `"json"` (the default) or
    any other unrecognized/blank value raises BackendConfigurationError
    rather than silently returning something. The caller (
    scripts/radar_worker.py) is expected to construct its own explicit
    `Settings(db_backend=..., state_db_url=...)` from its dedicated
    EDGE_RADAR_WORKER_DB_BACKEND/EDGE_RADAR_WORKER_STATE_DB_URL
    configuration before calling this — never the ambient
    EDGE_DB_BACKEND/EDGE_STATE_DB_URL pair `get_settings()` would
    resolve, which belongs to the ordinary dashboard path."""
    backend = _normalized_backend(settings)
    if backend == "sqlite":
        return SqliteScanStatusRepository(conn=_require_sqlite_connection(settings))
    if backend == "postgres":
        return PostgresScanStatusRepository(conn=_require_postgres_connection(settings))
    raise BackendConfigurationError(
        "Provider scan-status/cursor persistence requires an explicit "
        f'db_backend of "sqlite" or "postgres" for continuous worker mode '
        f"(got {backend!r}). JSON is not supported here — "
        "scripts/run_scan.py's existing one-shot path needs no cursor at all."
    )


# --- Comparison repository — Radar evidence-packet foundation, Phase 3,
# Step 3A (design/DECISIONS.md). Factory exists and is tested; not yet
# wired into radar_inbox.py or any other caller. Deliberately not
# source-scoped (no `source` parameter) — comparison_results, like
# Signals, is not partitioned per source the way candidates/filing_events
# are (see comparison_store.py's own single shared JSON file). The one
# method this Protocol exposes is a pure, read-only bulk lookup — no
# insert/update method is exposed here, matching the underlying
# repositories' own insert-only-elsewhere, read-only-here shape. ---

class ComparisonRepositoryProtocol(Protocol):
    def latest_for_candidate_ids(self, candidate_ids: Sequence[str]) -> dict[str, ComparisonRecord]: ...


@dataclass(frozen=True)
class JsonComparisonRepository:
    cache_dir: Path

    def latest_for_candidate_ids(self, candidate_ids: Sequence[str]) -> dict[str, ComparisonRecord]:
        return comparison_store.latest_comparison_records_for_candidate_ids(self.cache_dir, candidate_ids)


@dataclass(frozen=True)
class SqliteComparisonRepository:
    conn: sqlite3.Connection

    def latest_for_candidate_ids(self, candidate_ids: Sequence[str]) -> dict[str, ComparisonRecord]:
        return sqlite_comparisons.get_latest_comparison_records_for_candidate_ids(self.conn, candidate_ids)


@dataclass(frozen=True)
class PostgresComparisonRepository:
    conn: psycopg.Connection

    def latest_for_candidate_ids(self, candidate_ids: Sequence[str]) -> dict[str, ComparisonRecord]:
        return postgres_comparisons.get_latest_comparison_records_for_candidate_ids(self.conn, candidate_ids)


def get_comparison_repository(settings: Settings) -> ComparisonRepositoryProtocol:
    """Same `settings.db_backend` selection convention as every other
    factory function above — "json" (default/unrecognized) returns the
    JSON adapter, "sqlite"/"postgres" open (and migrate) a real
    connection via the same `_require_sqlite_connection`/
    `_require_postgres_connection` helpers every other backend-selecting
    factory already uses. Not called from any real service entry point
    yet — a future Radar-page caller (Phase 3, Step 3B) is expected to
    use this exactly once per page render, not once per card."""
    backend = _normalized_backend(settings)
    if backend == "sqlite":
        return SqliteComparisonRepository(conn=_require_sqlite_connection(settings))
    if backend == "postgres":
        return PostgresComparisonRepository(conn=_require_postgres_connection(settings))
    return JsonComparisonRepository(cache_dir=settings.cache_dir)


# --- Research Case repository — EevaResearch Phase 4, Step 3C (design/
# DECISIONS.md). Read-only by construction: no append/insert/update/
# delete/bundle-persistence method is exposed here — the only write path
# for Research Cases remains scripts/create_research_case.py's direct
# calls to research_store.append_research_case_bundle()/
# state_db.research_repository.insert_research_case_bundle()/
# postgres_state_db.research_repository.insert_research_case_bundle(),
# none of which this factory or its Protocol ever calls. Same
# `settings.db_backend` selection convention, and the same narrow-
# Protocol shape, as ComparisonRepositoryProtocol above. ---

class ResearchCaseRepositoryProtocol(Protocol):
    def list_recent_cases(self, limit: int) -> tuple[ResearchCase, ...]: ...
    def get_case(self, case_id: str) -> ResearchCase | None: ...
    def evidence_items_for_case_ids(self, case_ids: Sequence[str]) -> dict[str, tuple[ResearchEvidenceItem, ...]]: ...
    def assertions_for_case_ids(
        self, case_ids: Sequence[str],
    ) -> dict[str, tuple[RelationshipAssertion | DependencyAssertion, ...]]: ...
    def existing_case_ids(self, case_ids: Sequence[str]) -> frozenset[str]: ...


@dataclass(frozen=True)
class JsonResearchCaseRepository:
    cache_dir: Path

    def list_recent_cases(self, limit: int) -> tuple[ResearchCase, ...]:
        return research_store.list_recent_cases(self.cache_dir, limit)

    def get_case(self, case_id: str) -> ResearchCase | None:
        return research_store.get_research_case(self.cache_dir, case_id)

    def evidence_items_for_case_ids(self, case_ids: Sequence[str]) -> dict[str, tuple[ResearchEvidenceItem, ...]]:
        return research_store.evidence_items_for_case_ids(self.cache_dir, case_ids)

    def assertions_for_case_ids(
        self, case_ids: Sequence[str],
    ) -> dict[str, tuple[RelationshipAssertion | DependencyAssertion, ...]]:
        return research_store.assertions_for_case_ids(self.cache_dir, case_ids)

    def existing_case_ids(self, case_ids: Sequence[str]) -> frozenset[str]:
        if not case_ids:
            return frozenset()
        existing = research_store.load_research_cases(self.cache_dir)
        return frozenset(case_id for case_id in case_ids if case_id in existing)


@dataclass(frozen=True)
class SqliteResearchCaseRepository:
    conn: sqlite3.Connection

    def list_recent_cases(self, limit: int) -> tuple[ResearchCase, ...]:
        return sqlite_research.list_recent_cases(self.conn, limit)

    def get_case(self, case_id: str) -> ResearchCase | None:
        return sqlite_research.get_research_case(self.conn, case_id)

    def evidence_items_for_case_ids(self, case_ids: Sequence[str]) -> dict[str, tuple[ResearchEvidenceItem, ...]]:
        return sqlite_research.get_evidence_items_for_case_ids(self.conn, case_ids)

    def assertions_for_case_ids(
        self, case_ids: Sequence[str],
    ) -> dict[str, tuple[RelationshipAssertion | DependencyAssertion, ...]]:
        return sqlite_research.get_assertions_for_case_ids(self.conn, case_ids)

    def existing_case_ids(self, case_ids: Sequence[str]) -> frozenset[str]:
        return sqlite_research.get_existing_case_ids(self.conn, case_ids)


@dataclass(frozen=True)
class PostgresResearchCaseRepository:
    conn: psycopg.Connection

    def list_recent_cases(self, limit: int) -> tuple[ResearchCase, ...]:
        return postgres_research.list_recent_cases(self.conn, limit)

    def get_case(self, case_id: str) -> ResearchCase | None:
        return postgres_research.get_research_case(self.conn, case_id)

    def evidence_items_for_case_ids(self, case_ids: Sequence[str]) -> dict[str, tuple[ResearchEvidenceItem, ...]]:
        return postgres_research.get_evidence_items_for_case_ids(self.conn, case_ids)

    def assertions_for_case_ids(
        self, case_ids: Sequence[str],
    ) -> dict[str, tuple[RelationshipAssertion | DependencyAssertion, ...]]:
        return postgres_research.get_assertions_for_case_ids(self.conn, case_ids)

    def existing_case_ids(self, case_ids: Sequence[str]) -> frozenset[str]:
        return postgres_research.get_existing_case_ids(self.conn, case_ids)


def get_research_case_repository(settings: Settings) -> ResearchCaseRepositoryProtocol:
    """Same `settings.db_backend` selection convention as every other
    factory function above — "json" (default/unrecognized) returns the
    JSON adapter, "sqlite"/"postgres" open (and migrate) a real
    connection via the same `_require_sqlite_connection`/
    `_require_postgres_connection` helpers every other backend-selecting
    factory already uses. The only caller this phase is
    src/ui/pages/research_cases.py, once per page render — never once
    per row/card."""
    backend = _normalized_backend(settings)
    if backend == "sqlite":
        return SqliteResearchCaseRepository(conn=_require_sqlite_connection(settings))
    if backend == "postgres":
        return PostgresResearchCaseRepository(conn=_require_postgres_connection(settings))
    return JsonResearchCaseRepository(cache_dir=settings.cache_dir)


# --- Research Case bundle writer — EevaResearch Phase 4, Step 4B-1
# (design/DECISIONS.md). A separate, narrow, write-only seam from
# ResearchCaseRepositoryProtocol above (which stays read-only by
# construction, per Step 3C) — this Protocol exposes exactly one
# method, wrapping only the existing atomic, validation-first,
# all-or-nothing bundle-persistence functions (Step 3B). No update/
# upsert/delete/merge/query/single-record write method is exposed here,
# and no new persistence algorithm or SQL is implemented in this
# module — every adapter below delegates entirely to an already-
# existing function.
#
# Unlike every other Research Case factory function above, this one has
# no JSON branch in its dispatch function — same deliberate omission as
# get_scan_status_repository() above, and for the same reason: the only
# intended real caller is a future worker-only entry point
# (scripts/radar_worker.py), which is already hard-required (by that
# script's own _build_worker_settings()) to run on "sqlite" or
# "postgres" only, never "json". JsonResearchCaseBundleWriter exists
# only for direct, isolated unit testing of the adapter class itself —
# it is never reachable through get_research_case_bundle_writer().
#
# Not called by any runtime entry point yet — no worker code references
# this factory or its Protocol as of this step.

class ResearchCaseBundleWriterProtocol(Protocol):
    def insert_bundle(self, bundle: ResearchCaseBundle) -> bool: ...


@dataclass(frozen=True)
class JsonResearchCaseBundleWriter:
    """Test-parity only — see this section's own module-level comment.
    Never returned by get_research_case_bundle_writer()."""

    cache_dir: Path

    def insert_bundle(self, bundle: ResearchCaseBundle) -> bool:
        return research_store.append_research_case_bundle(self.cache_dir, bundle)


@dataclass(frozen=True)
class SqliteResearchCaseBundleWriter:
    conn: sqlite3.Connection

    def insert_bundle(self, bundle: ResearchCaseBundle) -> bool:
        return sqlite_research.insert_research_case_bundle(self.conn, bundle)


@dataclass(frozen=True)
class PostgresResearchCaseBundleWriter:
    conn: psycopg.Connection

    def insert_bundle(self, bundle: ResearchCaseBundle) -> bool:
        return postgres_research.insert_research_case_bundle(self.conn, bundle)


def get_research_case_bundle_writer(settings: Settings) -> ResearchCaseBundleWriterProtocol:
    """No JSON branch — see this section's own module-level comment for
    why. `db_backend` of "json" (the default) or any other
    unrecognized/blank value raises BackendConfigurationError rather
    than silently returning something a future worker must never use."""
    backend = _normalized_backend(settings)
    if backend == "sqlite":
        return SqliteResearchCaseBundleWriter(conn=_require_sqlite_connection(settings))
    if backend == "postgres":
        return PostgresResearchCaseBundleWriter(conn=_require_postgres_connection(settings))
    raise BackendConfigurationError(
        "Autonomous Research Case bundle persistence requires an explicit "
        'db_backend of "sqlite" or "postgres" for worker mode '
        f"(got {backend!r}). JSON is not supported here — this seam exists "
        "only for a future standalone worker entry point, never the ordinary "
        "dashboard's own JSON-backed default."
    )


# --- Theme repository — Evidence-First Themes MVP (design/DECISIONS.md).
# Read-only, PUBLISHED-only by construction: every method here is
# server-side filtered to ThemeVisibility.PUBLISHED — an internal,
# ready_to_publish, or archived theme is indistinguishable from a
# nonexistent one through this Protocol. This is the *only* seam
# src/ui/pages/themes_research.py (the public web UI) is allowed to
# use. A separate, non-public ThemeCuratorRepositoryProtocol below
# supports the insert/update operations a human curator (via
# scripts/create_theme.py) needs — the public protocol exposes none of
# them. ---

class ThemeRepositoryProtocol(Protocol):
    def list_published_themes(self) -> tuple[ResearchTheme, ...]: ...
    def get_published_theme(self, theme_id: str) -> ResearchTheme | None: ...
    def evidence_for_theme(self, theme_id: str) -> tuple[ThemeEvidenceItem, ...]: ...
    def company_map_for_theme(self, theme_id: str) -> tuple[ThemeCompanyMapEntry, ...]: ...
    # Deliberately no research-notes method here at all — unlike
    # evidence/company-map, ThemeResearchNote (hypotheses, decisions,
    # watch items) is internal curator scratchpad content that must
    # never reach the public surface, even after a theme is published.
    # See ThemeCuratorRepositoryProtocol below for the read/write seam.


@dataclass(frozen=True)
class JsonThemeRepository:
    cache_dir: Path

    def list_published_themes(self) -> tuple[ResearchTheme, ...]:
        return theme_store.list_published_themes(self.cache_dir)

    def get_published_theme(self, theme_id: str) -> ResearchTheme | None:
        return theme_store.get_published_theme(self.cache_dir, theme_id)

    def evidence_for_theme(self, theme_id: str) -> tuple[ThemeEvidenceItem, ...]:
        return theme_store.evidence_for_theme_ids(self.cache_dir, [theme_id]).get(theme_id, ())

    def company_map_for_theme(self, theme_id: str) -> tuple[ThemeCompanyMapEntry, ...]:
        return theme_store.company_map_for_theme_ids(self.cache_dir, [theme_id]).get(theme_id, ())


@dataclass(frozen=True)
class SqliteThemeRepository:
    conn: sqlite3.Connection

    def list_published_themes(self) -> tuple[ResearchTheme, ...]:
        return sqlite_themes.list_published_themes(self.conn)

    def get_published_theme(self, theme_id: str) -> ResearchTheme | None:
        return sqlite_themes.get_published_theme(self.conn, theme_id)

    def evidence_for_theme(self, theme_id: str) -> tuple[ThemeEvidenceItem, ...]:
        return sqlite_themes.evidence_for_theme_ids(self.conn, [theme_id]).get(theme_id, ())

    def company_map_for_theme(self, theme_id: str) -> tuple[ThemeCompanyMapEntry, ...]:
        return sqlite_themes.company_map_for_theme_ids(self.conn, [theme_id]).get(theme_id, ())


@dataclass(frozen=True)
class PostgresThemeRepository:
    conn: psycopg.Connection

    def list_published_themes(self) -> tuple[ResearchTheme, ...]:
        return postgres_themes.list_published_themes(self.conn)

    def get_published_theme(self, theme_id: str) -> ResearchTheme | None:
        return postgres_themes.get_published_theme(self.conn, theme_id)

    def evidence_for_theme(self, theme_id: str) -> tuple[ThemeEvidenceItem, ...]:
        return postgres_themes.evidence_for_theme_ids(self.conn, [theme_id]).get(theme_id, ())

    def company_map_for_theme(self, theme_id: str) -> tuple[ThemeCompanyMapEntry, ...]:
        return postgres_themes.company_map_for_theme_ids(self.conn, [theme_id]).get(theme_id, ())


def get_theme_repository(settings: Settings) -> ThemeRepositoryProtocol:
    """Same `settings.db_backend` selection convention as every other
    factory function above. The only intended caller is
    src/ui/pages/themes_research.py, once or twice per page render
    (one list_published_themes() call for the index, or one
    get_published_theme()+evidence_for_theme()+company_map_for_theme()
    trio for the detail view) — never once per card/row."""
    backend = _normalized_backend(settings)
    if backend == "sqlite":
        return SqliteThemeRepository(conn=_require_sqlite_connection(settings))
    if backend == "postgres":
        return PostgresThemeRepository(conn=_require_postgres_connection(settings))
    return JsonThemeRepository(cache_dir=settings.cache_dir)


# --- Theme curator repository — private, non-public write seam. Never
# imported by src/ui/pages/themes_research.py or any other runtime UI
# path; the only intended caller is scripts/create_theme.py. Unlike
# get_research_case_bundle_writer() (worker-only, no JSON branch), this
# supports all three backends — the curator script's own --backend
# json|sqlite|postgres flag requires it. Exposes get_theme() (any
# visibility — the curator needs to see a theme regardless of its
# publish state) and the one visibility-transition update, alongside
# the three insert functions. No query/search/listing method beyond
# get_theme() is exposed here — that stays out of scope for this MVP's
# curator tool, per its own approval's "narrow" instruction. ---

class ThemeCuratorRepositoryProtocol(Protocol):
    def get_theme(self, theme_id: str) -> ResearchTheme | None: ...
    # Citrini-style Theme research workspace vertical slice (design/
    # DECISIONS.md) — list_themes()/evidence_for_theme()/
    # company_map_for_theme() are new curator-side reads for
    # src/ui/pages/theme_workspace.py, the internal-only workspace UI.
    # The latter two mirror ThemeRepositoryProtocol's own read shape
    # above, but through this private seam — the internal page must
    # never depend on the public, published-only protocol.
    def list_themes(self) -> tuple[ResearchTheme, ...]: ...
    def insert_theme(self, theme: ResearchTheme) -> bool: ...
    def insert_evidence_item(self, item: ThemeEvidenceItem) -> bool: ...
    def evidence_for_theme(self, theme_id: str) -> tuple[ThemeEvidenceItem, ...]: ...
    def insert_company_map_entry(self, entry: ThemeCompanyMapEntry) -> bool: ...
    def company_map_for_theme(self, theme_id: str) -> tuple[ThemeCompanyMapEntry, ...]: ...
    def insert_research_note(self, note: ThemeResearchNote) -> bool: ...
    def research_notes_for_theme(self, theme_id: str) -> tuple[ThemeResearchNote, ...]: ...
    def set_visibility(self, theme_id: str, new_visibility: ThemeVisibility, updated_at: str) -> ResearchTheme | None: ...


@dataclass(frozen=True)
class JsonThemeCuratorRepository:
    cache_dir: Path

    def get_theme(self, theme_id: str) -> ResearchTheme | None:
        return theme_store.get_theme(self.cache_dir, theme_id)

    def list_themes(self) -> tuple[ResearchTheme, ...]:
        return theme_store.list_themes(self.cache_dir)

    def insert_theme(self, theme: ResearchTheme) -> bool:
        return theme_store.append_theme(self.cache_dir, theme)

    def insert_evidence_item(self, item: ThemeEvidenceItem) -> bool:
        return theme_store.append_theme_evidence_item(self.cache_dir, item)

    def evidence_for_theme(self, theme_id: str) -> tuple[ThemeEvidenceItem, ...]:
        return theme_store.evidence_for_theme_ids(self.cache_dir, [theme_id]).get(theme_id, ())

    def insert_company_map_entry(self, entry: ThemeCompanyMapEntry) -> bool:
        return theme_store.append_theme_company_map_entry(self.cache_dir, entry)

    def company_map_for_theme(self, theme_id: str) -> tuple[ThemeCompanyMapEntry, ...]:
        return theme_store.company_map_for_theme_ids(self.cache_dir, [theme_id]).get(theme_id, ())

    def insert_research_note(self, note: ThemeResearchNote) -> bool:
        return theme_store.append_theme_research_note(self.cache_dir, note)

    def research_notes_for_theme(self, theme_id: str) -> tuple[ThemeResearchNote, ...]:
        return theme_store.research_notes_for_theme_ids(self.cache_dir, [theme_id]).get(theme_id, ())

    def set_visibility(self, theme_id: str, new_visibility: ThemeVisibility, updated_at: str) -> ResearchTheme | None:
        return theme_store.set_theme_visibility(self.cache_dir, theme_id, new_visibility, updated_at)


@dataclass(frozen=True)
class SqliteThemeCuratorRepository:
    conn: sqlite3.Connection

    def get_theme(self, theme_id: str) -> ResearchTheme | None:
        return sqlite_themes.get_theme(self.conn, theme_id)

    def list_themes(self) -> tuple[ResearchTheme, ...]:
        return sqlite_themes.list_themes(self.conn)

    def insert_theme(self, theme: ResearchTheme) -> bool:
        return sqlite_themes.insert_theme(self.conn, theme)

    def insert_evidence_item(self, item: ThemeEvidenceItem) -> bool:
        return sqlite_themes.insert_theme_evidence_item(self.conn, item)

    def evidence_for_theme(self, theme_id: str) -> tuple[ThemeEvidenceItem, ...]:
        return sqlite_themes.evidence_for_theme_ids(self.conn, [theme_id]).get(theme_id, ())

    def insert_company_map_entry(self, entry: ThemeCompanyMapEntry) -> bool:
        return sqlite_themes.insert_theme_company_map_entry(self.conn, entry)

    def company_map_for_theme(self, theme_id: str) -> tuple[ThemeCompanyMapEntry, ...]:
        return sqlite_themes.company_map_for_theme_ids(self.conn, [theme_id]).get(theme_id, ())

    def insert_research_note(self, note: ThemeResearchNote) -> bool:
        return sqlite_themes.insert_theme_research_note(self.conn, note)

    def research_notes_for_theme(self, theme_id: str) -> tuple[ThemeResearchNote, ...]:
        return sqlite_themes.research_notes_for_theme_ids(self.conn, [theme_id]).get(theme_id, ())

    def set_visibility(self, theme_id: str, new_visibility: ThemeVisibility, updated_at: str) -> ResearchTheme | None:
        return sqlite_themes.set_theme_visibility(self.conn, theme_id, new_visibility, updated_at)


@dataclass(frozen=True)
class PostgresThemeCuratorRepository:
    conn: psycopg.Connection

    def get_theme(self, theme_id: str) -> ResearchTheme | None:
        return postgres_themes.get_theme(self.conn, theme_id)

    def list_themes(self) -> tuple[ResearchTheme, ...]:
        return postgres_themes.list_themes(self.conn)

    def insert_theme(self, theme: ResearchTheme) -> bool:
        return postgres_themes.insert_theme(self.conn, theme)

    def insert_evidence_item(self, item: ThemeEvidenceItem) -> bool:
        return postgres_themes.insert_theme_evidence_item(self.conn, item)

    def evidence_for_theme(self, theme_id: str) -> tuple[ThemeEvidenceItem, ...]:
        return postgres_themes.evidence_for_theme_ids(self.conn, [theme_id]).get(theme_id, ())

    def insert_company_map_entry(self, entry: ThemeCompanyMapEntry) -> bool:
        return postgres_themes.insert_theme_company_map_entry(self.conn, entry)

    def company_map_for_theme(self, theme_id: str) -> tuple[ThemeCompanyMapEntry, ...]:
        return postgres_themes.company_map_for_theme_ids(self.conn, [theme_id]).get(theme_id, ())

    def insert_research_note(self, note: ThemeResearchNote) -> bool:
        return postgres_themes.insert_theme_research_note(self.conn, note)

    def research_notes_for_theme(self, theme_id: str) -> tuple[ThemeResearchNote, ...]:
        return postgres_themes.research_notes_for_theme_ids(self.conn, [theme_id]).get(theme_id, ())

    def set_visibility(self, theme_id: str, new_visibility: ThemeVisibility, updated_at: str) -> ResearchTheme | None:
        return postgres_themes.set_theme_visibility(self.conn, theme_id, new_visibility, updated_at)


def get_theme_curator_repository(settings: Settings) -> ThemeCuratorRepositoryProtocol:
    """Private/curator seam — only scripts/create_theme.py is expected
    to call this. Supports all three backends, matching that script's
    own --backend json|sqlite|postgres option."""
    backend = _normalized_backend(settings)
    if backend == "sqlite":
        return SqliteThemeCuratorRepository(conn=_require_sqlite_connection(settings))
    if backend == "postgres":
        return PostgresThemeCuratorRepository(conn=_require_postgres_connection(settings))
    return JsonThemeCuratorRepository(cache_dir=settings.cache_dir)


# --- Theme-matching repository — Phase A1 (design/DECISIONS.md).
# Wholly internal: unlike ThemeRepositoryProtocol/ThemeCuratorRepositoryProtocol
# above, there is no public counterpart to this seam at all — matching
# data is never shown to any user under any Theme visibility state, so
# no split between a public and a private protocol applies here. Never
# imported by src/ui/pages/themes_research.py, any other public UI
# page, ThemeRepositoryProtocol, Radar, Daily News, Watchlists, or any
# public route. Supports all three backends (like
# ThemeCuratorRepositoryProtocol, unlike the worker-only
# ResearchCaseBundleWriterProtocol) since a future review-decision
# recorder is not necessarily worker-constrained. Not called by any
# runtime entry point yet — no worker/UI code references this factory
# or its Protocol as of this step. ---

class ThemeMatchingRepositoryProtocol(Protocol):
    def insert_scope(self, scope: ThemeMatchingScope) -> bool: ...
    def get_scope(self, theme_id: str) -> ThemeMatchingScope | None: ...
    def list_active_scopes(self) -> tuple[ThemeMatchingScope, ...]: ...
    def insert_match(self, match: ResearchCaseThemeMatch) -> bool: ...
    def get_match(self, match_id: str) -> ResearchCaseThemeMatch | None: ...
    def existing_match_ids_for_case_ids(self, case_ids: Sequence[str]) -> frozenset[str]: ...
    def list_pending_matches(self) -> tuple[ResearchCaseThemeMatch, ...]: ...
    def insert_review_decision(self, decision: ThemeMatchReviewDecision) -> bool: ...
    def list_review_decisions_for_match(self, match_id: str) -> tuple[ThemeMatchReviewDecision, ...]: ...


@dataclass(frozen=True)
class JsonThemeMatchingRepository:
    cache_dir: Path

    def insert_scope(self, scope: ThemeMatchingScope) -> bool:
        return theme_matching_store.insert_scope(self.cache_dir, scope)

    def get_scope(self, theme_id: str) -> ThemeMatchingScope | None:
        return theme_matching_store.get_scope(self.cache_dir, theme_id)

    def list_active_scopes(self) -> tuple[ThemeMatchingScope, ...]:
        return theme_matching_store.list_active_scopes(self.cache_dir)

    def insert_match(self, match: ResearchCaseThemeMatch) -> bool:
        return theme_matching_store.insert_match(self.cache_dir, match)

    def get_match(self, match_id: str) -> ResearchCaseThemeMatch | None:
        return theme_matching_store.get_match(self.cache_dir, match_id)

    def existing_match_ids_for_case_ids(self, case_ids: Sequence[str]) -> frozenset[str]:
        return theme_matching_store.existing_match_ids_for_case_ids(self.cache_dir, case_ids)

    def list_pending_matches(self) -> tuple[ResearchCaseThemeMatch, ...]:
        return theme_matching_store.list_pending_matches(self.cache_dir)

    def insert_review_decision(self, decision: ThemeMatchReviewDecision) -> bool:
        return theme_matching_store.insert_review_decision(self.cache_dir, decision)

    def list_review_decisions_for_match(self, match_id: str) -> tuple[ThemeMatchReviewDecision, ...]:
        return theme_matching_store.list_review_decisions_for_match(self.cache_dir, match_id)


@dataclass(frozen=True)
class SqliteThemeMatchingRepository:
    conn: sqlite3.Connection

    def insert_scope(self, scope: ThemeMatchingScope) -> bool:
        return sqlite_theme_matching.insert_scope(self.conn, scope)

    def get_scope(self, theme_id: str) -> ThemeMatchingScope | None:
        return sqlite_theme_matching.get_scope(self.conn, theme_id)

    def list_active_scopes(self) -> tuple[ThemeMatchingScope, ...]:
        return sqlite_theme_matching.list_active_scopes(self.conn)

    def insert_match(self, match: ResearchCaseThemeMatch) -> bool:
        return sqlite_theme_matching.insert_match(self.conn, match)

    def get_match(self, match_id: str) -> ResearchCaseThemeMatch | None:
        return sqlite_theme_matching.get_match(self.conn, match_id)

    def existing_match_ids_for_case_ids(self, case_ids: Sequence[str]) -> frozenset[str]:
        return sqlite_theme_matching.existing_match_ids_for_case_ids(self.conn, case_ids)

    def list_pending_matches(self) -> tuple[ResearchCaseThemeMatch, ...]:
        return sqlite_theme_matching.list_pending_matches(self.conn)

    def insert_review_decision(self, decision: ThemeMatchReviewDecision) -> bool:
        return sqlite_theme_matching.insert_review_decision(self.conn, decision)

    def list_review_decisions_for_match(self, match_id: str) -> tuple[ThemeMatchReviewDecision, ...]:
        return sqlite_theme_matching.list_review_decisions_for_match(self.conn, match_id)


@dataclass(frozen=True)
class PostgresThemeMatchingRepository:
    conn: psycopg.Connection

    def insert_scope(self, scope: ThemeMatchingScope) -> bool:
        return postgres_theme_matching.insert_scope(self.conn, scope)

    def get_scope(self, theme_id: str) -> ThemeMatchingScope | None:
        return postgres_theme_matching.get_scope(self.conn, theme_id)

    def list_active_scopes(self) -> tuple[ThemeMatchingScope, ...]:
        return postgres_theme_matching.list_active_scopes(self.conn)

    def insert_match(self, match: ResearchCaseThemeMatch) -> bool:
        return postgres_theme_matching.insert_match(self.conn, match)

    def get_match(self, match_id: str) -> ResearchCaseThemeMatch | None:
        return postgres_theme_matching.get_match(self.conn, match_id)

    def existing_match_ids_for_case_ids(self, case_ids: Sequence[str]) -> frozenset[str]:
        return postgres_theme_matching.existing_match_ids_for_case_ids(self.conn, case_ids)

    def list_pending_matches(self) -> tuple[ResearchCaseThemeMatch, ...]:
        return postgres_theme_matching.list_pending_matches(self.conn)

    def insert_review_decision(self, decision: ThemeMatchReviewDecision) -> bool:
        return postgres_theme_matching.insert_review_decision(self.conn, decision)

    def list_review_decisions_for_match(self, match_id: str) -> tuple[ThemeMatchReviewDecision, ...]:
        return postgres_theme_matching.list_review_decisions_for_match(self.conn, match_id)


def get_theme_matching_repository(settings: Settings) -> ThemeMatchingRepositoryProtocol:
    """Same `settings.db_backend` selection convention as every other
    factory function above. No runtime caller exists yet — a future,
    separately approved worker-wiring step (Phase A2) and/or review-
    decision recorder are the only intended callers."""
    backend = _normalized_backend(settings)
    if backend == "sqlite":
        return SqliteThemeMatchingRepository(conn=_require_sqlite_connection(settings))
    if backend == "postgres":
        return PostgresThemeMatchingRepository(conn=_require_postgres_connection(settings))
    return JsonThemeMatchingRepository(cache_dir=settings.cache_dir)
