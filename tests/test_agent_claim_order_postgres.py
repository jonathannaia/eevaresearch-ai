"""Claim ordering, proved against real PostgreSQL.

tests/test_agent_scheduler.py proves this behaviourally on SQLite and
asserts structurally that both repository modules name the same ORDER BY
columns. Neither of those is PostgreSQL executing the query: the two
repositories are independent implementations, and the Postgres one adds
`FOR UPDATE SKIP LOCKED`, which interacts with ordering in a way a
string comparison cannot check. This file closes that gap.

The seeded rows are deliberately hostile to an implementation that
leans on insertion order or on the job-id hash: candidate timestamps are
inserted out of order, and three rows share an identical created_at with
distinct candidate_ids so the tie-break is the only thing that can
decide them.

Runs against the disposable loopback container only, and skips when it
is not reachable. No model session, credential or network call."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.data_access import backend_factory
from src.data_access.postgres_state_db import agent_repository as pg_agent
from src.logic import agent_scheduler
from src.models.agent_records import AgentJob

from tests._postgres_test_support import pg_conn, pg_isolated_connection  # noqa: F401

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
SHARED = "2025-06-01T00:00:00+00:00"

# (candidate_id, created_at) — insertion order is deliberately NOT the
# expected claim order, and the three SHARED rows are inserted c, a, b.
SEEDED: tuple[tuple[str, str], ...] = (
    ("cand-mid", "2025-01-01T00:00:00+00:00"),
    ("cand-newest", "2026-08-01T00:00:00+00:00"),
    ("tie-c", SHARED),
    ("cand-oldest", "2023-03-03T00:00:00+00:00"),
    ("tie-a", SHARED),
    ("cand-second", "2024-02-02T00:00:00+00:00"),
    ("tie-b", SHARED),
)

EXPECTED = [
    "cand-oldest",    # 2023-03-03
    "cand-second",    # 2024-02-02
    "cand-mid",       # 2025-01-01
    "tie-a", "tie-b", "tie-c",   # identical created_at -> candidate_id order
    "cand-newest",    # 2026-08-01
]


@pytest.fixture
def store(pg_conn):  # noqa: F811
    return backend_factory._AgentStoreRepository(conn=pg_conn, module=pg_agent)


def _seed(store) -> None:
    for candidate_id, created_at in SEEDED:
        assert store.enqueue_job(AgentJob(
            job_id=agent_scheduler.job_id_for(candidate_id, 1, "test-policy", "shadow"),
            candidate_id=candidate_id, source="SEC EDGAR", candidate_version=1,
            policy_version="test-policy", mode="shadow", state="pending", attempts=0,
            enqueue_reason="backlog", created_at=created_at, updated_at=NOW.isoformat(),
        )) is True


def _drain(store, *, worker="w1") -> list[str]:
    drained = []
    while True:
        job = store.claim_job(mode="shadow", now=NOW.isoformat(),
                              lease_expires_at=agent_scheduler.lease_until(NOW), worker_instance=worker)
        if job is None:
            return drained
        drained.append(job.candidate_id)
        assert store.finish_job(job.job_id, state="done", now=NOW.isoformat(), expected_worker=worker) is True


def test_postgres_claims_in_exact_created_at_then_candidate_id_order(store):
    _seed(store)
    assert _drain(store) == EXPECTED


def test_the_insertion_order_really_was_different_from_the_claim_order():
    """Guards the guard: if the fixture ever became pre-sorted, the test
    above would pass without proving anything."""
    inserted = [candidate_id for candidate_id, _ in SEEDED]
    assert inserted != EXPECTED
    assert sorted(inserted) == sorted(EXPECTED)


def test_equal_timestamps_are_decided_by_candidate_id_not_by_the_job_id_hash(store):
    """The three tied rows are inserted c, a, b, and their derived job
    ids happen to sort in the OPPOSITE order to their candidate ids — so
    a claim order of a, b, c can only have come from the candidate_id
    tie-break, never from the job-id hash or from insertion order."""
    _seed(store)
    by_job_id = sorted(("tie-a", "tie-b", "tie-c"),
                       key=lambda cid: agent_scheduler.job_id_for(cid, 1, "test-policy", "shadow"))
    assert by_job_id == ["tie-c", "tie-b", "tie-a"], (
        "fixture no longer discriminates: job-id order must differ from candidate-id order"
    )
    drained = [c for c in _drain(store) if c.startswith("tie-")]
    assert drained == ["tie-a", "tie-b", "tie-c"]


def test_a_backoff_still_holds_a_job_back_without_disturbing_the_order(store):
    """next_attempt_at filtering and ORDER BY have to compose: the held
    row must be skipped, and the rest must keep their order."""
    _seed(store)
    held = agent_scheduler.job_id_for("cand-oldest", 1, "test-policy", "shadow")
    job = store.claim_job(mode="shadow", now=NOW.isoformat(),
                          lease_expires_at=agent_scheduler.lease_until(NOW), worker_instance="w1")
    assert job.job_id == held
    store.finish_job(held, state="pending", now=NOW.isoformat(), last_error_code="FAILED_RETRIEVAL",
                     next_attempt_at="2099-01-01T00:00:00+00:00", expected_worker="w1")
    assert _drain(store) == [c for c in EXPECTED if c != "cand-oldest"]


def test_two_workers_never_claim_the_same_job_and_still_drain_in_order(store):
    """FOR UPDATE SKIP LOCKED is the Postgres-only clause the structural
    test cannot check; interleaving two workers proves it neither
    double-issues a job nor reorders the queue."""
    _seed(store)
    drained: list[str] = []
    while True:
        first = store.claim_job(mode="shadow", now=NOW.isoformat(),
                                lease_expires_at=agent_scheduler.lease_until(NOW), worker_instance="w1")
        second = store.claim_job(mode="shadow", now=NOW.isoformat(),
                                 lease_expires_at=agent_scheduler.lease_until(NOW), worker_instance="w2")
        for job, worker in ((first, "w1"), (second, "w2")):
            if job is None:
                continue
            drained.append(job.candidate_id)
            store.finish_job(job.job_id, state="done", now=NOW.isoformat(), expected_worker=worker)
        if first is None and second is None:
            break
    assert drained == EXPECTED
    assert len(drained) == len(set(drained)), "a job was issued twice"


def test_this_is_real_postgres_not_a_sqlite_stand_in(pg_conn):  # noqa: F811
    """The whole point of this file, asserted rather than assumed."""
    row = pg_conn.execute("SELECT version() AS v").fetchone()
    assert "PostgreSQL" in row["v"]
    assert pg_agent.__name__.startswith("src.data_access.postgres_state_db")
