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
from typing import Protocol

import psycopg

from src.config.settings import Settings
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
from src.data_access.postgres_state_db import connection as postgres_state_db_connection
from src.data_access.postgres_state_db import filing_event_repository as postgres_filing_events
from src.data_access.postgres_state_db import identifier_repository as postgres_identifiers
from src.data_access.postgres_state_db import scan_status_repository as postgres_scan_status
from src.data_access.postgres_state_db import schema as postgres_schema
from src.data_access.postgres_state_db.identifier_repository import (
    ResolvedIdentifierRecord as PostgresResolvedIdentifierRecord,
)
from src.data_access.postgres_state_db.scan_status_repository import ProviderScanStatus as PostgresProviderScanStatus
from src.data_access.postgres_state_db.signal_repository import PostgresSignalRepository
from src.data_access.state_db import candidate_repository as sqlite_candidates
from src.data_access.state_db import connection as state_db_connection
from src.data_access.state_db import filing_event_repository as sqlite_filing_events
from src.data_access.state_db import identifier_repository as sqlite_identifiers
from src.data_access.state_db import scan_status_repository as sqlite_scan_status
from src.data_access.state_db import schema as state_db_schema
from src.data_access.state_db.identifier_repository import ResolvedIdentifierRecord
from src.data_access.state_db.scan_status_repository import ProviderScanStatus
from src.data_access.state_db.signal_repository import SqliteSignalRepository
from src.models.models import CandidateSignal, FilingEvent

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
