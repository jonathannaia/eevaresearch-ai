"""Company Discovery Phase 2's own backend-selection seam — the
Company-Discovery-owned counterpart to
src.data_access.backend_factory.py's own get_X_repository() functions,
deliberately NOT added to backend_factory.py itself, mirroring
src.data_access.daily_news.daily_news_backend.py's own identical
architectural choice: backend_factory.py has its own scope-guard test
proving it never imports from src.data_access.daily_news, keeping
Radar's composition root decoupled from Daily News; this module keeps
that same boundary for Company Discovery. No shared code with either
backend_factory.py or daily_news_backend.py — independently
implemented, same "no dialect/module abstraction" choice already made
between src/data_access/state_db/ and src/data_access/postgres_state_db/
themselves.

No JSON branch, deliberately — mirrors daily_news_backend.
get_daily_news_scan_status_repository()'s own "no JSON branch, fail
closed" discipline exactly: JSON is not safe shared persistence for a
continuous worker, and no dashboard entry point needs to read these
tables in Phase 2 (the only reader is the hidden, internal admin page,
itself gated to the same backend)."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Protocol

import psycopg

from src.config.settings import Settings
from src.data_access.backend_factory import BackendConfigurationError
from src.data_access.postgres_state_db import candidate_issuer_repository as postgres_repo
from src.data_access.postgres_state_db import connection as postgres_state_db_connection
from src.data_access.postgres_state_db import schema as postgres_schema
from src.data_access.state_db import candidate_issuer_repository as sqlite_repo
from src.data_access.state_db import connection as state_db_connection
from src.data_access.state_db import schema as state_db_schema
from src.models.company_discovery_models import (
    CandidateEvidence,
    CandidateIssuerRecord,
    CandidateScoreSnapshot,
    CandidateStateTransition,
    CandidateWorkerStatus,
)


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


class CandidateIssuerIdentifier(Protocol):
    issuer_id: str
    source: str
    native_id: str
    confirmed_via: str
    confirmed_at: str


class CandidateIssuerRepositoryProtocol(Protocol):
    def get_candidate(self, issuer_id: str) -> CandidateIssuerRecord | None: ...
    def list_candidates(self, coverage_state: str | None = None) -> tuple[CandidateIssuerRecord, ...]: ...
    def get_aliases(self) -> dict[str, str]: ...
    def evidence_exists(self, dedup_key: str) -> bool: ...
    def create_candidate_with_evidence(self, **kwargs) -> None: ...
    def append_evidence_to_existing_candidate(self, evidence: CandidateEvidence, alias_text: str | None, now: str) -> None: ...
    def create_rejected_or_quarantined_candidate(self, **kwargs) -> None: ...
    def record_score(self, snapshot: CandidateScoreSnapshot) -> None: ...
    def transition_state(self, transition: CandidateStateTransition) -> None: ...
    def get_worker_status(self) -> CandidateWorkerStatus | None: ...
    def upsert_worker_status(self, status: CandidateWorkerStatus) -> None: ...
    def get_evidence_for_issuer(self, issuer_id: str) -> tuple[dict, ...]: ...


class SqliteCandidateIssuerRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def get_candidate(self, issuer_id: str) -> CandidateIssuerRecord | None:
        return sqlite_repo.get_candidate(self.conn, issuer_id)

    def list_candidates(self, coverage_state: str | None = None) -> tuple[CandidateIssuerRecord, ...]:
        return sqlite_repo.list_candidates(self.conn, coverage_state)

    def get_aliases(self) -> dict[str, str]:
        return sqlite_repo.get_aliases(self.conn)

    def evidence_exists(self, dedup_key: str) -> bool:
        return sqlite_repo.evidence_exists(self.conn, dedup_key)

    def create_candidate_with_evidence(self, **kwargs) -> None:
        sqlite_repo.create_candidate_with_evidence(self.conn, **kwargs)

    def append_evidence_to_existing_candidate(self, evidence: CandidateEvidence, alias_text: str | None, now: str) -> None:
        sqlite_repo.append_evidence_to_existing_candidate(self.conn, evidence, alias_text, now)

    def create_rejected_or_quarantined_candidate(self, **kwargs) -> None:
        sqlite_repo.create_rejected_or_quarantined_candidate(self.conn, **kwargs)

    def record_score(self, snapshot: CandidateScoreSnapshot) -> None:
        sqlite_repo.record_score(self.conn, snapshot)

    def transition_state(self, transition: CandidateStateTransition) -> None:
        sqlite_repo.transition_state(self.conn, transition)

    def get_worker_status(self) -> CandidateWorkerStatus | None:
        return sqlite_repo.get_worker_status(self.conn)

    def upsert_worker_status(self, status: CandidateWorkerStatus) -> None:
        sqlite_repo.upsert_worker_status(self.conn, status)

    def get_evidence_for_issuer(self, issuer_id: str) -> tuple[dict, ...]:
        return sqlite_repo.get_evidence_for_issuer(self.conn, issuer_id)


class PostgresCandidateIssuerRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn

    def get_candidate(self, issuer_id: str) -> CandidateIssuerRecord | None:
        return postgres_repo.get_candidate(self.conn, issuer_id)

    def list_candidates(self, coverage_state: str | None = None) -> tuple[CandidateIssuerRecord, ...]:
        return postgres_repo.list_candidates(self.conn, coverage_state)

    def get_aliases(self) -> dict[str, str]:
        return postgres_repo.get_aliases(self.conn)

    def evidence_exists(self, dedup_key: str) -> bool:
        return postgres_repo.evidence_exists(self.conn, dedup_key)

    def create_candidate_with_evidence(self, **kwargs) -> None:
        postgres_repo.create_candidate_with_evidence(self.conn, **kwargs)

    def append_evidence_to_existing_candidate(self, evidence: CandidateEvidence, alias_text: str | None, now: str) -> None:
        postgres_repo.append_evidence_to_existing_candidate(self.conn, evidence, alias_text, now)

    def create_rejected_or_quarantined_candidate(self, **kwargs) -> None:
        postgres_repo.create_rejected_or_quarantined_candidate(self.conn, **kwargs)

    def record_score(self, snapshot: CandidateScoreSnapshot) -> None:
        postgres_repo.record_score(self.conn, snapshot)

    def transition_state(self, transition: CandidateStateTransition) -> None:
        postgres_repo.transition_state(self.conn, transition)

    def get_worker_status(self) -> CandidateWorkerStatus | None:
        return postgres_repo.get_worker_status(self.conn)

    def upsert_worker_status(self, status: CandidateWorkerStatus) -> None:
        postgres_repo.upsert_worker_status(self.conn, status)

    def get_evidence_for_issuer(self, issuer_id: str) -> tuple[dict, ...]:
        return postgres_repo.get_evidence_for_issuer(self.conn, issuer_id)


def get_candidate_issuer_repository(settings: Settings) -> CandidateIssuerRepositoryProtocol:
    """No JSON branch — mirrors daily_news_backend.
    get_daily_news_scan_status_repository() exactly. The caller
    (scripts/company_discovery_worker.py, scripts/backfill_company_
    discovery.py, and the hidden admin page) is expected to construct
    its own explicit Settings from the dedicated
    EDGE_COMPANY_DISCOVERY_WORKER_DB_BACKEND/_STATE_DB_URL configuration
    — never the ambient EDGE_DB_BACKEND/EDGE_STATE_DB_URL pair the
    dashboard uses for anything else."""
    backend = _normalized_backend(settings)
    if backend == "sqlite":
        return SqliteCandidateIssuerRepository(conn=_require_sqlite_connection(settings))
    if backend == "postgres":
        return PostgresCandidateIssuerRepository(conn=_require_postgres_connection(settings))
    raise BackendConfigurationError(
        "Company Discovery candidate persistence requires an explicit "
        f'db_backend of "sqlite" or "postgres" (got {backend!r}). JSON is not supported here.'
    )
