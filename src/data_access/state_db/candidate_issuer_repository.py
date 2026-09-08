"""Company Discovery Phase 2 — SQLite repository for the seven
candidate_* tables (schema v13). Read and written only by
scripts/company_discovery_worker.py, scripts/backfill_company_
discovery.py, and the hidden company_discovery_admin.py page (read-only
there) — no existing Radar/Filings/Daily News pipeline, page, or
component reads or writes any table this module touches.

`create_candidate_with_evidence()` is the ONLY function that inserts a
new `candidate_issuers` row — there is no bare "insert candidate"
function exposed, so a Candidate without its first evidence row is not
just discouraged, it is unreachable through this module's public API.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from src.data_access.state_db.connection import transaction
from src.models.company_discovery_models import (
    CandidateEvidence,
    CandidateIssuerRecord,
    CandidateScoreSnapshot,
    CandidateStateTransition,
    CandidateWorkerStatus,
    EntityKind,
    RelationshipType,
    ResolutionConfidence,
    SourceType,
)
from src.models.issuer import CoverageState, Issuer, LifecycleState

WORKER_STATUS_KEY = "company_discovery"


@dataclass(frozen=True)
class CandidateIdentifier:
    issuer_id: str
    source: str
    native_id: str
    confirmed_via: str
    confirmed_at: str


def _row_to_issuer(row: sqlite3.Row, identifiers: dict[str, str]) -> Issuer:
    return Issuer(
        issuer_id=row["issuer_id"],
        legal_name=row["legal_name"],
        native_name=row["native_name"],
        country_or_jurisdiction=row["country_or_jurisdiction"],
        coverage_state=CoverageState(row["coverage_state"]),
        lifecycle_state=LifecycleState.ACTIVE,
        primary_ticker=identifiers.get("SEC EDGAR") or identifiers.get("OpenDART / DART") or identifiers.get("EDINET"),
        identifiers=identifiers,
        discovered_via=row["discovered_via"],
        entity_kind=row["entity_kind"],
        parent_issuer_id=row["parent_issuer_id"],
    )


def _row_to_record(row: sqlite3.Row, identifiers: dict[str, str]) -> CandidateIssuerRecord:
    return CandidateIssuerRecord(
        issuer=_row_to_issuer(row, identifiers),
        resolution_confidence=ResolutionConfidence(row["resolution_confidence"]),
        composite_score=row["composite_score"],
        first_evidence_at=row["first_evidence_at"],
        last_evidence_at=row["last_evidence_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def get_identifiers(conn: sqlite3.Connection, issuer_id: str) -> dict[str, str]:
    rows = conn.execute(
        "SELECT source, native_id FROM candidate_issuer_identifiers WHERE issuer_id = ?", (issuer_id,),
    ).fetchall()
    return {row["source"]: row["native_id"] for row in rows}


def get_candidate(conn: sqlite3.Connection, issuer_id: str) -> CandidateIssuerRecord | None:
    row = conn.execute("SELECT * FROM candidate_issuers WHERE issuer_id = ?", (issuer_id,)).fetchone()
    if row is None:
        return None
    return _row_to_record(row, get_identifiers(conn, issuer_id))


def list_candidates(conn: sqlite3.Connection, coverage_state: str | None = None) -> tuple[CandidateIssuerRecord, ...]:
    if coverage_state is not None:
        rows = conn.execute(
            "SELECT * FROM candidate_issuers WHERE coverage_state = ? ORDER BY last_evidence_at DESC", (coverage_state,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM candidate_issuers ORDER BY last_evidence_at DESC").fetchall()
    return tuple(_row_to_record(row, get_identifiers(conn, row["issuer_id"])) for row in rows)


def get_aliases(conn: sqlite3.Connection) -> dict[str, str]:
    """Every known alias -> issuer_id, across every Candidate regardless
    of coverage_state — used by entity_resolution.resolve_mention() so
    a mention already resolved (even to a Rejected/Quarantined/Archived
    row) is recognized again rather than re-litigated every tick."""
    rows = conn.execute("SELECT alias_text, issuer_id FROM candidate_aliases").fetchall()
    return {row["alias_text"]: row["issuer_id"] for row in rows}


def evidence_exists(conn: sqlite3.Connection, dedup_key: str) -> bool:
    row = conn.execute("SELECT 1 FROM candidate_evidence WHERE dedup_key = ?", (dedup_key,)).fetchone()
    return row is not None


def create_candidate_with_evidence(
    conn: sqlite3.Connection,
    *,
    issuer_id: str, legal_name: str, native_name: str, country_or_jurisdiction: str,
    entity_kind: str, coverage_state: str, resolution_confidence: str, discovered_via: str,
    now: str, evidence: CandidateEvidence, alias_text: str,
) -> None:
    """The only way to create a new `candidate_issuers` row — always
    together with its first `candidate_evidence` row and its first
    `candidate_aliases` entry, in one transaction. Structurally makes an
    evidence-less Candidate impossible through this module."""
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO candidate_issuers (
                issuer_id, legal_name, native_name, country_or_jurisdiction, entity_kind,
                coverage_state, resolution_confidence, composite_score, discovered_via,
                first_evidence_at, last_evidence_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0.0, ?, ?, ?, ?, ?)
            """,
            (
                issuer_id, legal_name, native_name, country_or_jurisdiction, entity_kind,
                coverage_state, resolution_confidence, discovered_via, now, now, now, now,
            ),
        )
        _insert_evidence(conn, evidence)
        conn.execute(
            "INSERT OR IGNORE INTO candidate_aliases (issuer_id, alias_text, created_at) VALUES (?, ?, ?)",
            (issuer_id, alias_text, now),
        )
        conn.execute(
            "INSERT INTO candidate_state_transitions (issuer_id, from_state, to_state, at, detail, triggered_by) "
            "VALUES (?, NULL, ?, ?, ?, ?)",
            (issuer_id, coverage_state, now, f"Created from evidence: {evidence.matched_pattern_category}", "worker:company_discovery"),
        )


def _insert_evidence(conn: sqlite3.Connection, evidence: CandidateEvidence) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO candidate_evidence (
            issuer_id, source_type, source_name, source_record_id, source_url, source_snippet,
            relationship_type, matched_pattern_category, related_core_issuer_id, theme_slug,
            supply_chain_layer, extraction_timestamp, source_published_at, dedup_key
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            evidence.issuer_id, evidence.source_type.value, evidence.source_name, evidence.source_record_id,
            evidence.source_url, evidence.source_snippet, evidence.relationship_type.value,
            evidence.matched_pattern_category, evidence.related_core_issuer_id, evidence.theme_slug,
            evidence.supply_chain_layer, evidence.extraction_timestamp, evidence.source_published_at, evidence.dedup_key,
        ),
    )


def append_evidence_to_existing_candidate(
    conn: sqlite3.Connection, evidence: CandidateEvidence, alias_text: str | None, now: str,
) -> None:
    """Appends evidence to a Candidate that already exists. Never
    creates a `candidate_issuers` row — raises if `issuer_id` doesn't
    already exist, matching the "evidence-less candidate is impossible"
    invariant from the other direction (evidence always attaches to a
    real, already-created candidate)."""
    with transaction(conn):
        exists = conn.execute("SELECT 1 FROM candidate_issuers WHERE issuer_id = ?", (evidence.issuer_id,)).fetchone()
        if exists is None:
            raise ValueError(f"append_evidence_to_existing_candidate: no such candidate {evidence.issuer_id!r}")
        _insert_evidence(conn, evidence)
        if alias_text:
            conn.execute(
                "INSERT OR IGNORE INTO candidate_aliases (issuer_id, alias_text, created_at) VALUES (?, ?, ?)",
                (evidence.issuer_id, alias_text, now),
            )
        conn.execute(
            "UPDATE candidate_issuers SET last_evidence_at = ?, updated_at = ? WHERE issuer_id = ?",
            (now, now, evidence.issuer_id),
        )


def create_rejected_or_quarantined_candidate(
    conn: sqlite3.Connection,
    *,
    issuer_id: str, legal_name: str, country_or_jurisdiction: str, entity_kind: str,
    coverage_state: str, discovered_via: str, now: str, evidence: CandidateEvidence, alias_text: str,
) -> None:
    """Same structural guarantee as create_candidate_with_evidence — a
    Rejected/Quarantined row is remembered (with its own evidence) so
    the same mention is never re-litigated on the next overlap-window
    pass, but its composite_score always stays 0.0 (see scoring.py's own
    docstring — never computed for these two states)."""
    create_candidate_with_evidence(
        conn, issuer_id=issuer_id, legal_name=legal_name, native_name="", country_or_jurisdiction=country_or_jurisdiction,
        entity_kind=entity_kind, coverage_state=coverage_state, resolution_confidence="Low",
        discovered_via=discovered_via, now=now, evidence=evidence, alias_text=alias_text,
    )


def record_score(
    conn: sqlite3.Connection, snapshot: CandidateScoreSnapshot,
) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE candidate_issuers SET composite_score = ?, updated_at = ? WHERE issuer_id = ?",
            (snapshot.composite_score, snapshot.computed_at, snapshot.issuer_id),
        )
        conn.execute(
            """
            INSERT INTO candidate_score_history (
                issuer_id, computed_at, composite_score, evidence_count, independent_source_count, score_breakdown
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.issuer_id, snapshot.computed_at, snapshot.composite_score,
                snapshot.evidence_count, snapshot.independent_source_count, json.dumps(snapshot.score_breakdown),
            ),
        )


def transition_state(conn: sqlite3.Connection, transition: CandidateStateTransition) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE candidate_issuers SET coverage_state = ?, updated_at = ? WHERE issuer_id = ?",
            (transition.to_state, transition.at, transition.issuer_id),
        )
        conn.execute(
            "INSERT INTO candidate_state_transitions (issuer_id, from_state, to_state, at, detail, triggered_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (transition.issuer_id, transition.from_state, transition.to_state, transition.at, transition.detail, transition.triggered_by),
        )


def add_confirmed_identifier(conn: sqlite3.Connection, identifier: CandidateIdentifier) -> None:
    """Never guessed — the caller is responsible for having independently
    confirmed `native_id` from source text before calling this."""
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO candidate_issuer_identifiers (issuer_id, source, native_id, confirmed_via, confirmed_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (issuer_id, source) DO UPDATE SET
                native_id = excluded.native_id, confirmed_via = excluded.confirmed_via, confirmed_at = excluded.confirmed_at
            """,
            (identifier.issuer_id, identifier.source, identifier.native_id, identifier.confirmed_via, identifier.confirmed_at),
        )


def get_worker_status(conn: sqlite3.Connection, worker_key: str = WORKER_STATUS_KEY) -> CandidateWorkerStatus | None:
    row = conn.execute("SELECT * FROM candidate_worker_status WHERE worker_key = ?", (worker_key,)).fetchone()
    if row is None:
        return None
    return CandidateWorkerStatus(
        worker_key=row["worker_key"], last_tick_started_at=row["last_tick_started_at"],
        last_tick_completed_at=row["last_tick_completed_at"], last_failure_code=row["last_failure_code"],
        evidence_created_last_run=row["evidence_created_last_run"], candidates_created_last_run=row["candidates_created_last_run"],
        candidates_quarantined_last_run=row["candidates_quarantined_last_run"], updated_at=row["updated_at"],
    )


def upsert_worker_status(conn: sqlite3.Connection, status: CandidateWorkerStatus) -> None:
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO candidate_worker_status (
                worker_key, last_tick_started_at, last_tick_completed_at, last_failure_code,
                evidence_created_last_run, candidates_created_last_run, candidates_quarantined_last_run, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (worker_key) DO UPDATE SET
                last_tick_started_at = excluded.last_tick_started_at,
                last_tick_completed_at = excluded.last_tick_completed_at,
                last_failure_code = excluded.last_failure_code,
                evidence_created_last_run = excluded.evidence_created_last_run,
                candidates_created_last_run = excluded.candidates_created_last_run,
                candidates_quarantined_last_run = excluded.candidates_quarantined_last_run,
                updated_at = excluded.updated_at
            """,
            (
                status.worker_key, status.last_tick_started_at, status.last_tick_completed_at, status.last_failure_code,
                status.evidence_created_last_run, status.candidates_created_last_run,
                status.candidates_quarantined_last_run, status.updated_at,
            ),
        )


def get_evidence_for_issuer(conn: sqlite3.Connection, issuer_id: str) -> tuple[dict, ...]:
    """Read-only, for the admin page's evidence drill-down — returns raw
    dict rows (not CandidateEvidence, since it's simpler for a display-
    only table and avoids re-parsing the enums for a view that never
    round-trips the data anywhere)."""
    rows = conn.execute(
        "SELECT * FROM candidate_evidence WHERE issuer_id = ? ORDER BY extraction_timestamp DESC", (issuer_id,),
    ).fetchall()
    return tuple(dict(row) for row in rows)
