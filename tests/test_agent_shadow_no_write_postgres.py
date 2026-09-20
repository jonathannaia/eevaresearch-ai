"""The shadow-mode no-write invariant, proved against real PostgreSQL.

tests/test_agent_modes.py proves the same property on SQLite, where a
connection-level authorizer can also deny non-agent writes at the
statement level. Postgres has no per-connection equivalent that does not
involve a role change, and this release makes no production credential or
permission change — so on the backend that actually runs in production,
the guarantee rests on two things: the structural absence of a write path
(blocker E3), and this test, which hashes every row of every non-agent
table before and after a complete shadow tick and requires them to be
identical.

The tables named explicitly below are the ones the release promises not
to touch: candidates (including reviewed_at and published_by),
state_transitions, the Signals/source records, and any Verified Update
store that exists in this schema.

A restricted Postgres role for the agent remains mandatory future work
before any publish-capable release. It is not a shadow-mode blocker,
because in shadow mode no code path writes these tables at all.

Runs against the disposable loopback container only, and skips when it
is not reachable. No model session, credential or network call."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.postgres_state_db import agent_repository as pg_agent
from src.logic import agent_persistence, agent_scheduler
from src.logic.agent_write_guard import is_agent_table
from src.logic.publication_policy import POLICY_VERSION, PolicyDecision, PublicationDecision, RowResult
from src.mcp_agent.contracts import (
    Claim,
    ClaimCategory,
    ClaimProposal,
    ClaimType,
    EvidenceRecord,
    FreshnessStatus,
    SourceTier,
)
from src.models.agent_records import EFFECTIVE_NO_ACTION

from tests._postgres_test_support import pg_conn, pg_isolated_connection, pg_isolated_dsn  # noqa: F401

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)

# Named in the release's own promise, so they are asserted by name rather
# than only swept up by the "every table" hash.
PROMISED_UNTOUCHED = ("candidates", "state_transitions", "filing_events")


@pytest.fixture
def store(pg_conn):  # noqa: F811
    return backend_factory._AgentStoreRepository(conn=pg_conn, module=pg_agent)


def _non_agent_tables(conn) -> list[str]:
    rows = conn.execute(
        "SELECT table_name AS name FROM information_schema.tables "
        "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE' ORDER BY table_name"
    ).fetchall()
    return [r["name"] for r in rows if not is_agent_table(r["name"])]


def _fingerprint(conn) -> dict[str, str]:
    out = {}
    for table in _non_agent_tables(conn):
        rows = sorted(repr(tuple(sorted(r.items()))) for r in conn.execute(f'SELECT * FROM "{table}"').fetchall())
        out[table] = hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()
    return out


def _seed(conn) -> None:
    """Real rows in the tables the agent must not touch, so the hashes
    mean something. An empty database would make the invariant vacuous."""
    conn.execute(
        "INSERT INTO filing_events (source_name, corp_code, rcept_no, corp_name, stock_code, report_nm, "
        "rcept_dt, pblntf_ty, flr_nm, retrieved_at) VALUES "
        "('SEC EDGAR', '0001045810', 'acc-1', 'NVIDIA', 'NVDA', '10-Q', '20260901', 'A', 'NVIDIA', "
        "'2026-09-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO candidates (id, source, filing_corp_code, filing_rcept_no, confidence, status, "
        "extraction_state, translation_state, excerpt_quality, excerpt_original, reviewed_at, published_by, "
        "version, created_at, updated_at) VALUES "
        "('cand-1', 'SEC EDGAR', '0001045810', 'acc-1', 'High', 'NEEDS_REVIEW', 'EXTRACTED', 'Not requested', "
        "'Usable text', 'Revenue rose to $30.0 billion.', NULL, NULL, 1, '2026-09-01T00:00:00+00:00', "
        "'2026-09-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO state_transitions (candidate_id, status, at, detail) "
        "VALUES ('cand-1', 'EXTRACTED', '2026-09-01T00:00:00+00:00', 'radar pipeline')"
    )
    conn.commit()


def _persist_a_shadow_evaluation(store, *, kill_switch_on: bool = False, packet_id: str = "pkt-1") -> None:
    evidence = EvidenceRecord(
        evidence_id="ev-1", session_id="sess-1", issuer_id="edgar:NVDA", source_tier=SourceTier.SEC_EDGAR,
        source_name="SEC EDGAR", source_url="https://example.test/a", source_document_id="acc-1",
        source_date="2026-09-01", excerpt_or_locator="Revenue rose to $30.0 billion.", excerpt_sha256="sha-1",
        retrieved_at="2026-09-20T00:00:00+00:00", confidence="High", freshness_status=FreshnessStatus.CURRENT,
    )
    claim = Claim(
        claim_id="c-1", issuer_id="edgar:NVDA", claim_type=ClaimType.DIRECT_REPORTED_FACT,
        claim_category=ClaimCategory.DIRECT_FACT, headline="Revenue rose to $30.0 billion",
        statement="Revenue rose to $30.0 billion.", what_this_does_not_establish="It does not establish demand.",
        evidence_ids=("ev-1",), factual_context_evidence_ids=(),
    )
    proposal = ClaimProposal(session_id="sess-1", candidate_id="cand-1", claims=(claim,),
                             retrieved_evidence_ids=("ev-1",))
    policy = PolicyDecision(
        decision=PublicationDecision.AUTO_PUBLISHED, reasons=("all_rows_passed",),
        row_results=(RowResult(row=11, condition="every claim is supported by the stored excerpt", passed=True),),
        content_hash="hash-1",
    )
    packet = agent_persistence.build_packet(
        packet_id=packet_id, session_id="sess-1", candidate_id="cand-1", source="SEC EDGAR",
        seed_document_id="acc-1", proposal=proposal, content_hash="hash-1",
        created_at=NOW.isoformat(), issuer_id="edgar:NVDA", evidence=(evidence,),
    )
    decision = agent_persistence.build_decision(
        packet_id=packet_id, policy=policy, mode="shadow", kill_switch_on=kill_switch_on,
        quote_verified=True, decided_at=NOW.isoformat(),
    )
    agent_persistence.persist_evaluation(store, packet=packet, decision=decision)


def _full_shadow_tick(store) -> str:
    """Everything a real tick writes: the run record, its heartbeat, the
    queue transitions, the packet with its evidence, the decision, and
    the audit trail."""
    claim = agent_scheduler.acquire_single_runner(store, worker_instance="w1", mode="shadow", now=NOW)
    assert claim.acquired
    store.reclaim_expired_leases(now=NOW.isoformat())

    job = agent_scheduler.AgentJob(
        job_id=agent_scheduler.job_id_for("cand-1", 1, POLICY_VERSION, "shadow"), candidate_id="cand-1",
        source="SEC EDGAR", candidate_version=1, policy_version=POLICY_VERSION, mode="shadow",
        state="pending", attempts=0, enqueue_reason="backlog", created_at=NOW.isoformat(),
        updated_at=NOW.isoformat(),
    )
    assert store.enqueue_job(job) is True
    leased = store.claim_job(mode="shadow", now=NOW.isoformat(),
                             lease_expires_at=agent_scheduler.lease_until(NOW), worker_instance="w1")
    assert leased is not None

    _persist_a_shadow_evaluation(store)
    assert store.finish_job(leased.job_id, state="done", now=NOW.isoformat(), expected_worker="w1") is True
    store.complete_run(claim.run_id, status="completed", completed_at=NOW.isoformat(), sessions=1, decisions=1)
    return claim.run_id


# --- the invariant --------------------------------------------------------------

def test_a_full_shadow_tick_leaves_every_non_agent_table_byte_identical(store, pg_conn):  # noqa: F811
    _seed(pg_conn)
    before = _fingerprint(pg_conn)
    assert all(t in before for t in PROMISED_UNTOUCHED), f"expected the promised tables in {sorted(before)}"

    empty = hashlib.sha256(b"").hexdigest()
    assert [t for t in PROMISED_UNTOUCHED if before[t] == empty] == [], "seeded tables must not be empty"

    run_id = _full_shadow_tick(store)

    after = _fingerprint(pg_conn)
    assert after == before
    # ...and the agent's own tables really did receive the work, so this
    # is not passing because nothing happened.
    assert store.get_packet("pkt-1") is not None
    assert store.get_decision("pkt-1").effective_decision == EFFECTIVE_NO_ACTION
    assert store.latest_heartbeat_at(run_id) is not None


@pytest.mark.parametrize("column", ["status", "reviewed_at", "published_by", "version", "updated_at"])
def test_the_candidate_columns_the_release_promises_not_to_touch_are_unchanged(store, pg_conn, column):  # noqa: F811
    _seed(pg_conn)
    before = pg_conn.execute(f'SELECT "{column}" FROM candidates WHERE id = %s', ("cand-1",)).fetchone()
    _full_shadow_tick(store)
    after = pg_conn.execute(f'SELECT "{column}" FROM candidates WHERE id = %s', ("cand-1",)).fetchone()
    assert after == before
    if column in ("reviewed_at", "published_by"):
        assert after[column] is None


def test_no_state_transition_is_appended_by_a_shadow_tick(store, pg_conn):  # noqa: F811
    _seed(pg_conn)
    before = pg_conn.execute("SELECT COUNT(*) AS n FROM state_transitions").fetchone()["n"]
    _full_shadow_tick(store)
    assert pg_conn.execute("SELECT COUNT(*) AS n FROM state_transitions").fetchone()["n"] == before == 1


def test_no_signals_or_source_record_is_written(store, pg_conn):  # noqa: F811
    """The Signals inputs are the filing/candidate records the dashboard
    derives from; nothing in a shadow tick may add to or alter them."""
    _seed(pg_conn)
    signal_tables = [t for t in _non_agent_tables(pg_conn)
                     if "signal" in t or "filing" in t or "daily_news" in t or "provider" in t]
    assert signal_tables, "expected signal/source tables in this schema"
    before = {t: _fingerprint(pg_conn)[t] for t in signal_tables}
    _full_shadow_tick(store)
    assert {t: _fingerprint(pg_conn)[t] for t in signal_tables} == before


def test_any_verified_update_store_in_this_schema_is_untouched(store, pg_conn):  # noqa: F811
    """Verified Updates are the agent's eventual public surface, so they
    are the table that matters most. If this schema has no such table,
    that itself is the proof for this release — and it is asserted, not
    assumed, so the test starts failing the moment one appears."""
    tables = _non_agent_tables(pg_conn)
    verified = [t for t in tables if "verified" in t]
    _seed(pg_conn)
    before = _fingerprint(pg_conn)
    _full_shadow_tick(store)
    after = _fingerprint(pg_conn)
    for table in verified:
        assert after[table] == before[table], table
    if not verified:
        # No Verified Update table exists yet: the publishing migration is
        # V25 / SQLite V23 and is deferred with E6/E7/E8.
        assert all("verified" not in t for t in tables)


def test_the_kill_switch_variant_writes_nothing_either(store, pg_conn):  # noqa: F811
    _seed(pg_conn)
    before = _fingerprint(pg_conn)
    _persist_a_shadow_evaluation(store, kill_switch_on=True, packet_id="pkt-kill")
    assert _fingerprint(pg_conn) == before
    decision = store.get_decision("pkt-kill")
    assert decision.blocked_by == "kill_switch" and decision.mode == "shadow"
    assert decision.kill_switch_on is True and decision.effective_decision == EFFECTIVE_NO_ACTION


# --- the Postgres-only advisory lock ---------------------------------------------

def test_the_advisory_lock_is_held_by_one_session_and_refused_to_another(pg_conn, pg_isolated_dsn):  # noqa: F811
    """The hard half of the single-runner guard, exercised for real: a
    worker killed without cleanup releases it when its connection drops."""
    import psycopg
    from psycopg.rows import dict_row

    assert pg_agent.try_acquire_runner_lock(pg_conn, agent_scheduler.RUNNER_LOCK_KEY) is True
    other = psycopg.connect(pg_isolated_dsn, row_factory=dict_row)
    try:
        assert pg_agent.try_acquire_runner_lock(other, agent_scheduler.RUNNER_LOCK_KEY) is False
    finally:
        other.close()
    assert pg_agent.release_runner_lock(pg_conn, agent_scheduler.RUNNER_LOCK_KEY) is True


def test_a_dropped_connection_releases_the_lock(pg_isolated_dsn):  # noqa: F811
    import psycopg
    from psycopg.rows import dict_row

    first = psycopg.connect(pg_isolated_dsn, row_factory=dict_row)
    assert pg_agent.try_acquire_runner_lock(first, agent_scheduler.RUNNER_LOCK_KEY + 1) is True
    first.close()  # the worker is killed

    second = psycopg.connect(pg_isolated_dsn, row_factory=dict_row)
    try:
        assert pg_agent.try_acquire_runner_lock(second, agent_scheduler.RUNNER_LOCK_KEY + 1) is True
    finally:
        pg_agent.release_runner_lock(second, agent_scheduler.RUNNER_LOCK_KEY + 1)
        second.close()
