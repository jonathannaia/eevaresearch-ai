"""Postgres persistence for the seven agent tables (schema.py's own V24) —
the live/shadow half of the durable agent store that replaces the agent's
ephemeral JSON files (blocker E5). Independent of the SQLite module of the
same name, per this package's no-dialect-abstraction constraint: same
contract, separate implementation.

Every read runs inside its own transaction and ends it, so a connection is
never left idle in transaction holding a lock (the pattern
user_preferences_repository.py established after the V22 release)."""

from __future__ import annotations

import json
import psycopg
from collections.abc import Sequence

from src.data_access.postgres_state_db.connection import transaction
from src.models.agent_records import (
    HEARTBEAT_EVENT,
    AgentAuditRow,
    AgentControl,
    AgentDecisionRow,
    AgentEvidenceRow,
    AgentJob,
    AgentPacket,
    AgentRun,
    DecisionListRow,
)

_JOB_COLUMNS = (
    "job_id, candidate_id, source, candidate_version, policy_version, mode, state, attempts, "
    "lease_expires_at, worker_instance, last_error_code, next_attempt_at, enqueue_reason, created_at, updated_at"
)


def _job(row: dict) -> AgentJob:
    return AgentJob(
        job_id=row["job_id"], candidate_id=row["candidate_id"], source=row["source"],
        candidate_version=row["candidate_version"], policy_version=row["policy_version"], mode=row["mode"],
        state=row["state"], attempts=row["attempts"], lease_expires_at=row["lease_expires_at"],
        worker_instance=row["worker_instance"], last_error_code=row["last_error_code"],
        next_attempt_at=row["next_attempt_at"], enqueue_reason=row["enqueue_reason"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )



def _fetchone(conn: psycopg.Connection, sql: str, params: tuple = ()) -> dict | None:
    """A read that ends its own transaction: psycopg opens one implicitly on
    first use, and a connection left idle in transaction holds locks."""
    with transaction(conn):
        return conn.execute(sql, params).fetchone()


def _fetchall(conn: psycopg.Connection, sql: str, params: tuple = ()) -> list[dict]:
    with transaction(conn):
        return conn.execute(sql, params).fetchall()

# --- runs -------------------------------------------------------------------

def start_run(conn: psycopg.Connection, run: AgentRun) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT INTO agent_runs (run_id, worker_instance, mode, started_at, completed_at, status, "
            "considered, sessions, decisions, errors, model, policy_version, cost_usd) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (run.run_id, run.worker_instance, run.mode, run.started_at, run.completed_at, run.status,
             run.considered, run.sessions, run.decisions, run.errors, run.model, run.policy_version, run.cost_usd),
        )


def complete_run(conn: psycopg.Connection, run_id: str, *, status: str, completed_at: str,
                 considered: int = 0, sessions: int = 0, decisions: int = 0, errors: int = 0,
                 cost_usd: str | None = None) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE agent_runs SET status = %s, completed_at = %s, considered = %s, sessions = %s, decisions = %s, "
            "errors = %s, cost_usd = %s WHERE run_id = %s",
            (status, completed_at, considered, sessions, decisions, errors, cost_usd, run_id),
        )


def _run(row) -> AgentRun:
    return AgentRun(
        run_id=row["run_id"], worker_instance=row["worker_instance"], mode=row["mode"], started_at=row["started_at"],
        policy_version=row["policy_version"], status=row["status"], completed_at=row["completed_at"],
        considered=row["considered"], sessions=row["sessions"], decisions=row["decisions"], errors=row["errors"],
        model=row["model"], cost_usd=row["cost_usd"],
    )


def latest_run(conn: psycopg.Connection) -> AgentRun | None:
    row = _fetchone(conn, "SELECT * FROM agent_runs ORDER BY started_at DESC, run_id DESC LIMIT 1")
    return _run(row) if row is not None else None


def active_runs(conn: psycopg.Connection) -> tuple[AgentRun, ...]:
    """Every run still claiming to be alive. The single-runner guard reads
    this, then asks each one's heartbeat whether it really is."""
    rows = _fetchall(conn, "SELECT * FROM agent_runs WHERE status = 'running' ORDER BY started_at, run_id")
    return tuple(_run(row) for row in rows)


def latest_heartbeat_at(conn: psycopg.Connection, run_id: str) -> str | None:
    """The heartbeat lives in the append-only audit stream rather than in a
    column, so liveness carries its own history and needs no migration."""
    row = _fetchone(
        conn,
        "SELECT created_at FROM agent_audit_events WHERE run_id = %s AND event_type = %s "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (run_id, HEARTBEAT_EVENT),
    )
    return row["created_at"] if row is not None else None


def try_acquire_runner_lock(conn: psycopg.Connection, key: int) -> bool:
    """Postgres-only hard guard on top of the portable heartbeat check: the
    session holds the advisory lock until it disconnects, so a worker that
    is killed without cleanup releases it automatically."""
    with transaction(conn):
        row = conn.execute("SELECT pg_try_advisory_lock(%s) AS locked", (key,)).fetchone()
    return bool(row["locked"])


def release_runner_lock(conn: psycopg.Connection, key: int) -> bool:
    with transaction(conn):
        row = conn.execute("SELECT pg_advisory_unlock(%s) AS released", (key,)).fetchone()
    return bool(row["released"])


# --- jobs -------------------------------------------------------------------

def enqueue_job(conn: psycopg.Connection, job: AgentJob) -> bool:
    """False when an identical (candidate, version, policy, mode) job already
    exists — the idempotency key, so a replay never creates a second job."""
    with transaction(conn):
        cursor = conn.execute(
            f"INSERT INTO agent_jobs ({_JOB_COLUMNS}) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (candidate_id, candidate_version, policy_version, mode) DO NOTHING",
            (job.job_id, job.candidate_id, job.source, job.candidate_version, job.policy_version, job.mode,
             job.state, job.attempts, job.lease_expires_at, job.worker_instance, job.last_error_code,
             job.next_attempt_at, job.enqueue_reason, job.created_at, job.updated_at),
        )
        return cursor.rowcount > 0


def claim_job(conn: psycopg.Connection, *, mode: str, now: str, lease_expires_at: str,
              worker_instance: str) -> AgentJob | None:
    with transaction(conn):
        row = conn.execute(
            "SELECT * FROM agent_jobs WHERE mode = %s AND state = 'pending' "
            "AND (next_attempt_at IS NULL OR next_attempt_at <= %s) ORDER BY created_at, job_id "
            "LIMIT 1 FOR UPDATE SKIP LOCKED",
            (mode, now),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE agent_jobs SET state = 'leased', attempts = attempts + 1, lease_expires_at = %s, "
            "worker_instance = %s, updated_at = %s WHERE job_id = %s",
            (lease_expires_at, worker_instance, now, row["job_id"]),
        )
        claimed = conn.execute("SELECT * FROM agent_jobs WHERE job_id = %s", (row["job_id"],)).fetchone()
    return _job(claimed)


def finish_job(conn: psycopg.Connection, job_id: str, *, state: str, now: str,
               last_error_code: str | None = None, next_attempt_at: str | None = None,
               expected_worker: str | None = None) -> bool:
    """Fencing: pass `expected_worker` and the update applies only while
    this worker still holds the lease. A worker whose lease expired and
    was reclaimed by another therefore cannot overwrite the new holder's
    result when it finally finishes. Returns whether the row changed."""
    with transaction(conn):
        if expected_worker is None:
            cursor = conn.execute(
                "UPDATE agent_jobs SET state = %s, lease_expires_at = NULL, worker_instance = NULL, "
                "last_error_code = %s, next_attempt_at = %s, updated_at = %s WHERE job_id = %s",
                (state, last_error_code, next_attempt_at, now, job_id),
            )
        else:
            cursor = conn.execute(
                "UPDATE agent_jobs SET state = %s, lease_expires_at = NULL, worker_instance = NULL, "
                "last_error_code = %s, next_attempt_at = %s, updated_at = %s "
                "WHERE job_id = %s AND state = 'leased' AND worker_instance = %s",
                (state, last_error_code, next_attempt_at, now, job_id, expected_worker),
            )
        return cursor.rowcount > 0


def reclaim_expired_leases(conn: psycopg.Connection, *, now: str) -> int:
    with transaction(conn):
        cursor = conn.execute(
            "UPDATE agent_jobs SET state = 'pending', lease_expires_at = NULL, worker_instance = NULL, "
            "updated_at = %s WHERE state = 'leased' AND lease_expires_at IS NOT NULL AND lease_expires_at <= %s",
            (now, now),
        )
        return cursor.rowcount


def get_job(conn: psycopg.Connection, job_id: str) -> AgentJob | None:
    row = _fetchone(conn, "SELECT * FROM agent_jobs WHERE job_id = %s", (job_id,))
    return _job(row) if row is not None else None


def count_jobs_by_state(conn: psycopg.Connection, *, since: str | None = None) -> dict[str, int]:
    if since is None:
        rows = _fetchall(conn, "SELECT state, COUNT(*) AS n FROM agent_jobs GROUP BY state")
    else:
        rows = _fetchall(conn, 
            "SELECT state, COUNT(*) AS n FROM agent_jobs WHERE updated_at >= %s GROUP BY state", (since,),
        )
    return {row["state"]: row["n"] for row in rows}


def count_packets_since(conn: psycopg.Connection, *, since: str) -> int:
    row = _fetchone(conn, "SELECT COUNT(*) AS n FROM agent_packets WHERE created_at >= %s", (since,))
    return row["n"]


# --- packets, evidence, decisions -------------------------------------------

def save_packet(conn: psycopg.Connection, packet: AgentPacket) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT INTO agent_packets (packet_id, job_id, run_id, session_id, candidate_id, source, issuer_id, "
            "issuer_resolution_json, seed_document_id, proposal_json, content_hash, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (packet.packet_id, packet.job_id, packet.run_id, packet.session_id, packet.candidate_id, packet.source,
             packet.issuer_id, packet.issuer_resolution_json, packet.seed_document_id, packet.proposal_json,
             packet.content_hash, packet.created_at),
        )
        for item in packet.evidence:
            conn.execute(
                "INSERT INTO agent_evidence (packet_id, evidence_id, source_tier, source_name, source_url, "
                "source_document_id, source_date, excerpt_or_locator, excerpt_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (item.packet_id, item.evidence_id, item.source_tier, item.source_name, item.source_url,
                 item.source_document_id, item.source_date, item.excerpt_or_locator, item.excerpt_sha256),
            )


def record_decision(conn: psycopg.Connection, decision: AgentDecisionRow) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT INTO agent_decisions (packet_id, policy_decision, effective_decision, blocked_by, mode, "
            "kill_switch_on, quote_verified, reasons_json, row_results_json, policy_version, decided_at, "
            "candidate_status_written, published) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (decision.packet_id, decision.policy_decision, decision.effective_decision, decision.blocked_by,
             decision.mode, int(decision.kill_switch_on), int(decision.quote_verified), decision.reasons_json,
             decision.row_results_json, decision.policy_version, decision.decided_at,
             int(decision.candidate_status_written), int(decision.published)),
        )


def append_audit_events(conn: psycopg.Connection, events: Sequence[AgentAuditRow]) -> None:
    with transaction(conn):
        for event in events:
            conn.execute(
                "INSERT INTO agent_audit_events (run_id, session_id, packet_id, candidate_id, event_type, "
                "tool_name, inputs_json, outcome, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (event.run_id, event.session_id, event.packet_id, event.candidate_id, event.event_type,
                 event.tool_name, event.inputs_json, event.outcome, event.created_at),
            )


# --- reads for the Agent Review page ------------------------------------------

_LIST_SELECT = (
    "SELECT d.packet_id, p.candidate_id, p.source, p.issuer_id, p.seed_document_id, p.created_at, "
    "d.policy_decision, d.effective_decision, d.blocked_by, d.mode, d.kill_switch_on, d.quote_verified, "
    "d.decided_at, d.reasons_json FROM agent_decisions d JOIN agent_packets p ON p.packet_id = d.packet_id"
)


def _list_row(row: dict) -> DecisionListRow:
    return DecisionListRow(
        packet_id=row["packet_id"], candidate_id=row["candidate_id"], source=row["source"],
        issuer_id=row["issuer_id"], seed_document_id=row["seed_document_id"], created_at=row["created_at"],
        policy_decision=row["policy_decision"], effective_decision=row["effective_decision"],
        blocked_by=row["blocked_by"], mode=row["mode"], kill_switch_on=bool(row["kill_switch_on"]),
        quote_verified=bool(row["quote_verified"]), decided_at=row["decided_at"], reasons_json=row["reasons_json"],
    )


def _list_filters(
    *, policy_decision, effective_decision, blocked_only, issuer_id, source, decided_from, decided_to,
    candidate_ids, event_type,
) -> tuple[str, list]:
    """One filter builder shared by the page query and its total, so a
    page can never be counted against a different filter than it shows."""
    where, params = [], []
    if policy_decision:
        where.append("d.policy_decision = %s"); params.append(policy_decision)
    if effective_decision:
        where.append("d.effective_decision = %s"); params.append(effective_decision)
    if blocked_only:
        where.append("d.blocked_by IS NOT NULL")
    if issuer_id:
        where.append("p.issuer_id = %s"); params.append(issuer_id)
    if source:
        where.append("p.source = %s"); params.append(source)
    if decided_from:
        where.append("d.decided_at >= %s"); params.append(decided_from)
    if decided_to:
        where.append("d.decided_at <= %s"); params.append(decided_to)
    if candidate_ids is not None:
        ids = list(candidate_ids)
        if not ids:
            return " WHERE 1 = 0", []  # an empty allow-list matches nothing, never everything
        where.append("p.candidate_id = ANY(%s)"); params.append(ids)
    if event_type:
        where.append("EXISTS (SELECT 1 FROM agent_audit_events e WHERE e.packet_id = d.packet_id "
                     "AND e.event_type = %s)")
        params.append(event_type)
    return ((" WHERE " + " AND ".join(where)) if where else ""), params


def list_decisions(
    conn: psycopg.Connection, *, policy_decision: str | None = None, effective_decision: str | None = None,
    blocked_only: bool = False, issuer_id: str | None = None, source: str | None = None,
    decided_from: str | None = None, decided_to: str | None = None,
    candidate_ids: Sequence[str] | None = None, event_type: str | None = None,
    limit: int = 50, offset: int = 0,
) -> tuple[DecisionListRow, ...]:
    clause, params = _list_filters(
        policy_decision=policy_decision, effective_decision=effective_decision, blocked_only=blocked_only,
        issuer_id=issuer_id, source=source, decided_from=decided_from, decided_to=decided_to,
        candidate_ids=candidate_ids, event_type=event_type,
    )
    rows = _fetchall(conn,
        f"{_LIST_SELECT}{clause} ORDER BY d.decided_at DESC, d.packet_id DESC LIMIT %s OFFSET %s",
        (*params, limit, offset),
    )
    return tuple(_list_row(row) for row in rows)


def count_matching_decisions(
    conn: psycopg.Connection, *, policy_decision: str | None = None, effective_decision: str | None = None,
    blocked_only: bool = False, issuer_id: str | None = None, source: str | None = None,
    decided_from: str | None = None, decided_to: str | None = None,
    candidate_ids: Sequence[str] | None = None, event_type: str | None = None,
) -> int:
    """How many rows the current filter matches in total — what the page
    counter and the last-page boundary are computed from."""
    clause, params = _list_filters(
        policy_decision=policy_decision, effective_decision=effective_decision, blocked_only=blocked_only,
        issuer_id=issuer_id, source=source, decided_from=decided_from, decided_to=decided_to,
        candidate_ids=candidate_ids, event_type=event_type,
    )
    row = _fetchone(conn,
        "SELECT COUNT(*) AS n FROM agent_decisions d JOIN agent_packets p ON p.packet_id = d.packet_id"
        f"{clause}", tuple(params),
    )
    return int(row["n"])


def distinct_issuers(conn: psycopg.Connection) -> tuple[str, ...]:
    rows = _fetchall(conn,
        "SELECT DISTINCT issuer_id FROM agent_packets WHERE issuer_id IS NOT NULL ORDER BY issuer_id")
    return tuple(r["issuer_id"] for r in rows)


def distinct_event_types(conn: psycopg.Connection) -> tuple[str, ...]:
    rows = _fetchall(conn, "SELECT DISTINCT event_type FROM agent_audit_events ORDER BY event_type")
    return tuple(r["event_type"] for r in rows)


def count_decisions(conn: psycopg.Connection, *, since: str | None = None) -> dict[str, int]:
    if since is None:
        rows = _fetchall(conn, "SELECT effective_decision AS k, COUNT(*) AS n FROM agent_decisions GROUP BY k")
    else:
        rows = _fetchall(conn, 
            "SELECT effective_decision AS k, COUNT(*) AS n FROM agent_decisions WHERE decided_at >= %s GROUP BY k",
            (since,),
        )
    return {row["k"]: row["n"] for row in rows}


def get_packet(conn: psycopg.Connection, packet_id: str) -> AgentPacket | None:
    row = _fetchone(conn, "SELECT * FROM agent_packets WHERE packet_id = %s", (packet_id,))
    if row is None:
        return None
    evidence = _fetchall(conn, 
        "SELECT * FROM agent_evidence WHERE packet_id = %s ORDER BY evidence_id", (packet_id,),
    )
    return AgentPacket(
        packet_id=row["packet_id"], job_id=row["job_id"], run_id=row["run_id"], session_id=row["session_id"],
        candidate_id=row["candidate_id"], source=row["source"], issuer_id=row["issuer_id"],
        issuer_resolution_json=row["issuer_resolution_json"], seed_document_id=row["seed_document_id"],
        proposal_json=row["proposal_json"], content_hash=row["content_hash"], created_at=row["created_at"],
        evidence=tuple(
            AgentEvidenceRow(
                packet_id=e["packet_id"], evidence_id=e["evidence_id"], source_tier=e["source_tier"],
                source_name=e["source_name"], source_url=e["source_url"], source_document_id=e["source_document_id"],
                source_date=e["source_date"], excerpt_or_locator=e["excerpt_or_locator"],
                excerpt_sha256=e["excerpt_sha256"],
            ) for e in evidence
        ),
    )


def get_decision(conn: psycopg.Connection, packet_id: str) -> AgentDecisionRow | None:
    row = _fetchone(conn, "SELECT * FROM agent_decisions WHERE packet_id = %s", (packet_id,))
    if row is None:
        return None
    return AgentDecisionRow(
        packet_id=row["packet_id"], policy_decision=row["policy_decision"],
        effective_decision=row["effective_decision"], blocked_by=row["blocked_by"], mode=row["mode"],
        kill_switch_on=bool(row["kill_switch_on"]), quote_verified=bool(row["quote_verified"]),
        reasons_json=row["reasons_json"], row_results_json=row["row_results_json"],
        policy_version=row["policy_version"], decided_at=row["decided_at"],
        candidate_status_written=bool(row["candidate_status_written"]), published=bool(row["published"]),
    )


def audit_events_for_packet(conn: psycopg.Connection, packet_id: str) -> tuple[AgentAuditRow, ...]:
    rows = _fetchall(conn, 
        "SELECT * FROM agent_audit_events WHERE packet_id = %s ORDER BY created_at, id", (packet_id,),
    )
    return tuple(
        AgentAuditRow(
            id=row["id"], run_id=row["run_id"], session_id=row["session_id"], packet_id=row["packet_id"],
            candidate_id=row["candidate_id"], event_type=row["event_type"], tool_name=row["tool_name"],
            inputs_json=row["inputs_json"], outcome=row["outcome"], created_at=row["created_at"],
        ) for row in rows
    )


# --- emergency control ----------------------------------------------------------

def get_control(conn: psycopg.Connection) -> AgentControl | None:
    row = _fetchone(conn, "SELECT * FROM agent_control WHERE id = 1")
    if row is None:
        return None
    return AgentControl(
        mode_override=row["mode_override"], reason=row["reason"], updated_by=row["updated_by"],
        updated_at=row["updated_at"],
    )


def set_mode_override(conn: psycopg.Connection, *, mode: str | None, reason: str | None, updated_by: str | None,
                      now: str) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT INTO agent_control (id, mode_override, reason, updated_by, updated_at) VALUES (1,%s,%s,%s,%s) "
            "ON CONFLICT (id) DO UPDATE SET mode_override = excluded.mode_override, reason = excluded.reason, "
            "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
            (mode, reason, updated_by, now),
        )


def json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def describe_database(conn: psycopg.Connection) -> tuple[int | None, tuple[str, ...]]:
    """What the database actually is, for the worker's startup self-check —
    asked of the database rather than inferred from configuration."""
    row = _fetchone(conn, "SELECT MAX(version) AS v FROM schema_version")
    version = row["v"] if row is not None else None
    tables = _fetchall(
        conn,
        "SELECT table_name AS name FROM information_schema.tables WHERE table_schema = current_schema() ORDER BY table_name",
    )
    return (int(version) if version is not None else None, tuple(r["name"] for r in tables))
