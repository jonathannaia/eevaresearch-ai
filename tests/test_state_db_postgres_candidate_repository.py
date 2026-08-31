"""Durable-State Phase 4B — candidate insert/load/update, optimistic
version conflict, successful multi-candidate batch persistence, and the
direct Phase 3B-0 batch-atomicity reproduction, for the isolated
Postgres backend (src/data_access/postgres_state_db/candidate_repository.py),
against the real local disposable Postgres test container. Every test
uses pg_conn (an isolated, already-migrated schema) and synthetic
FilingEvent/CandidateSignal fixtures only — see
tests/_postgres_test_support.py. Forced-failure tests use monkeypatch to
inject a real exception mid-batch, proving the generic
"any exception mid-batch rolls back everything" guarantee rather than
simulating one specific SQL-level failure mode."""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from src.data_access.postgres_state_db import candidate_repository
from src.data_access.postgres_state_db import connection as postgres_connection
from src.models.models import (
    CandidateSignal,
    CandidateStatus,
    EvidenceLocation,
    FilingEvent,
    FlagReason,
    LocationKind,
    StateTransition,
)

from tests._postgres_test_support import pg_conn, pg_isolated_connection  # noqa: F401


def _filing(rcept_no: str = "0001045810-26-000001", **overrides) -> FilingEvent:
    fields = dict(
        rcept_no=rcept_no, corp_code="0000045810", corp_name="NVIDIA", stock_code="NVDA",
        report_nm="8-K", rcept_dt="20260101", flr_nm="NVIDIA", source_name="SEC EDGAR",
        original_language="English",
    )
    fields.update(overrides)
    return FilingEvent(**fields)


def _candidate(candidate_id: str, filing: FilingEvent, **overrides) -> CandidateSignal:
    fields = dict(
        id=candidate_id, filing=filing, matched_rules=["earnings"], confidence="Moderate",
        status=CandidateStatus.CANDIDATE_DETECTED,
        state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="2026-01-01T00:00:00+00:00")],
    )
    fields.update(overrides)
    return CandidateSignal(**fields)


# --- Insert / load / update ---


def test_get_candidate_returns_none_when_absent(pg_conn):
    assert candidate_repository.get_candidate(pg_conn, "does-not-exist") is None


def test_upsert_new_candidates_inserts_and_load_round_trips(pg_conn):
    filing = _filing()
    candidate = _candidate("cand-1", filing)
    result = candidate_repository.upsert_new_candidates(pg_conn, "SEC EDGAR", [candidate])
    assert set(result.keys()) == {"cand-1"}
    loaded = candidate_repository.get_candidate(pg_conn, "cand-1")
    assert loaded is not None
    assert loaded.confidence == "Moderate"
    assert loaded.status == CandidateStatus.CANDIDATE_DETECTED
    assert loaded.filing.corp_name == "NVIDIA"
    assert len(loaded.state_history) == 1


def test_upsert_new_candidates_leaves_existing_entry_untouched(pg_conn):
    filing = _filing()
    candidate_repository.upsert_new_candidates(pg_conn, "SEC EDGAR", [_candidate("cand-1", filing)])
    # A second call with the same id but a different status must not
    # overwrite the already-persisted row — matches upsert_new_candidates'
    # documented "leaves existing entry's processing state untouched".
    changed = _candidate("cand-1", filing, status=CandidateStatus.NEEDS_REVIEW)
    candidate_repository.upsert_new_candidates(pg_conn, "SEC EDGAR", [changed])
    loaded = candidate_repository.get_candidate(pg_conn, "cand-1")
    assert loaded.status == CandidateStatus.CANDIDATE_DETECTED


def test_update_candidate_returns_not_found_for_unknown_id(pg_conn):
    unknown = _candidate("does-not-exist", _filing())
    outcome = candidate_repository.update_candidate(pg_conn, unknown, expected_version=1)
    assert outcome.status == "not_found"
    assert outcome.current is None


def test_update_candidate_appends_new_state_transitions_not_overwrite(pg_conn):
    filing = _filing()
    candidate_repository.upsert_new_candidates(pg_conn, "SEC EDGAR", [_candidate("cand-1", filing)])
    stored = candidate_repository.get_candidate(pg_conn, "cand-1")
    stored.status = CandidateStatus.NEEDS_REVIEW
    stored.state_history.append(StateTransition(status=CandidateStatus.NEEDS_REVIEW, at="2026-01-02T00:00:00+00:00"))
    outcome = candidate_repository.update_candidate(pg_conn, stored, expected_version=1)
    assert outcome.status == "updated"
    assert [t.status for t in outcome.current.state_history] == [
        CandidateStatus.CANDIDATE_DETECTED, CandidateStatus.NEEDS_REVIEW,
    ]


# --- Evidence-packet foundation, Phase 1: new optional field round-trip ---


def test_round_trips_evidence_packet_phase1_fields(pg_conn):
    filing = _filing(rcept_no="acc-phase1", corp_code="E02778", corp_name="SoftBank Group", source_name="EDINET", original_language="Japanese", filed_at="2026-08-17T09:00:00")
    candidate = _candidate(
        "cand-phase1", filing, status=CandidateStatus.NEEDS_REVIEW,
        excerpt_original="First extraction.", excerpt_supplemental="Second, different extraction.",
        excerpt_retrieved_at="2026-08-17T09:05:00+00:00",
        flag_reason=FlagReason(
            category="annual_securities_report", matched_terms=("annual_securities_report:010:030000:120",),
            score_inputs=("confidence=Moderate", "category_matches=1"), human_readable_reason="Matched 1 rule.",
            source_detail="ordinanceCode=010 · formCode=030000 · docTypeCode=120",
        ),
        evidence_location=EvidenceLocation(kind=LocationKind.UNAVAILABLE),
    )
    candidate_repository.upsert_new_candidates(pg_conn, "EDINET", [candidate])
    reloaded = candidate_repository.get_candidate(pg_conn, "cand-phase1")

    assert reloaded.filing.filed_at == "2026-08-17T09:00:00"
    assert reloaded.excerpt_original == "First extraction."
    assert reloaded.excerpt_supplemental == "Second, different extraction."
    assert reloaded.excerpt_retrieved_at == "2026-08-17T09:05:00+00:00"
    assert reloaded.flag_reason == candidate.flag_reason
    assert reloaded.evidence_location == candidate.evidence_location


def test_pre_phase1_row_missing_new_columns_still_loads_with_none_defaults(pg_conn):
    from src.data_access.postgres_state_db import filing_event_repository
    from datetime import datetime, timezone

    filing = _filing(rcept_no="acc-pre-phase1")
    filing_event_repository.upsert_filing_event(pg_conn, filing)
    now = datetime.now(timezone.utc).isoformat()
    pg_conn.execute(
        """
        INSERT INTO candidates (
            id, source, filing_corp_code, filing_rcept_no, matched_rules_json, confidence, status,
            extraction_state, translation_state, excerpt_quality, excerpt_original,
            title_translation_json, excerpt_translation_json, reviewed_at, reviewed_note,
            materiality_assessment, version, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)
        """,
        (
            "cand-pre-phase1", filing.source_name, filing.corp_code, filing.rcept_no,
            "[]", "Moderate", "Needs review", "Extracted", "Not requested", "Unknown",
            "Pre-existing excerpt.", None, None, None, "", "Not assessed", now, now,
        ),
    )
    pg_conn.commit()

    reloaded = candidate_repository.get_candidate(pg_conn, "cand-pre-phase1")
    assert reloaded is not None
    assert reloaded.excerpt_original == "Pre-existing excerpt."
    assert reloaded.excerpt_supplemental is None
    assert reloaded.flag_reason is None
    assert reloaded.evidence_location is None


# --- Optimistic version conflict ---


def test_update_candidate_optimistic_version_conflict_never_overwrites_newer_state(pg_conn):
    filing = _filing()
    candidate_repository.upsert_new_candidates(pg_conn, "SEC EDGAR", [_candidate("cand-1", filing)])
    version = candidate_repository.get_candidate_version(pg_conn, "cand-1")
    assert version == 1

    fresh_read = candidate_repository.get_candidate(pg_conn, "cand-1")
    fresh_read.status = CandidateStatus.NEEDS_REVIEW
    outcome_ok = candidate_repository.update_candidate(pg_conn, fresh_read, expected_version=version)
    assert outcome_ok.status == "updated"
    assert candidate_repository.get_candidate_version(pg_conn, "cand-1") == 2

    # A second writer's earlier read (still at version=1) is now stale —
    # its write must be rejected, never silently overwriting the newer,
    # already-applied change above.
    stale_read = candidate_repository.get_candidate(pg_conn, "cand-1")  # re-fetch to build a real object
    stale_read.status = CandidateStatus.DISMISSED
    outcome_conflict = candidate_repository.update_candidate(pg_conn, stale_read, expected_version=1)
    assert outcome_conflict.status == "conflict"
    assert outcome_conflict.current.status == CandidateStatus.NEEDS_REVIEW


# --- Successful multi-candidate batch ---


def test_successful_multi_candidate_batch_persists_all_linked_rows(pg_conn):
    filing1 = _filing(rcept_no="acc-1")
    filing2 = _filing(rcept_no="acc-2", corp_code="0000320193")
    result = candidate_repository.upsert_new_candidates(
        pg_conn, "SEC EDGAR", [_candidate("cand-1", filing1), _candidate("cand-2", filing2)],
    )
    assert set(result.keys()) == {"cand-1", "cand-2"}
    n_candidates = pg_conn.execute("SELECT COUNT(*) AS n FROM candidates").fetchone()["n"]
    n_filings = pg_conn.execute("SELECT COUNT(*) AS n FROM filing_events").fetchone()["n"]
    n_transitions = pg_conn.execute("SELECT COUNT(*) AS n FROM state_transitions").fetchone()["n"]
    assert n_candidates == 2
    assert n_filings == 2
    assert n_transitions == 2


# --- Direct Phase 3B-0 reproduction: forced mid-batch failure ---


def test_forced_mid_batch_failure_leaves_zero_partial_rows(pg_conn, monkeypatch):
    """Reproduces Durable-State Phase 3B-0's own regression scenario
    independently for Postgres: a forced failure on the SECOND
    candidate in a batch must roll back the FIRST candidate's own
    already-inserted filing-event/candidate/transition rows too — not
    just skip the failing one."""
    filing1 = _filing(rcept_no="acc-1")
    filing2 = _filing(rcept_no="acc-2", corp_code="0000320193")
    cand1 = _candidate("cand-1", filing1)
    cand2 = _candidate("cand-2", filing2)

    real_insert = candidate_repository._insert_candidate
    call_count = {"n": 0}

    def _flaky_insert(conn, candidate, now):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("forced failure on second candidate")
        return real_insert(conn, candidate, now)

    monkeypatch.setattr(candidate_repository, "_insert_candidate", _flaky_insert)

    with pytest.raises(RuntimeError, match="forced failure"):
        candidate_repository.upsert_new_candidates(pg_conn, "SEC EDGAR", [cand1, cand2])

    assert candidate_repository.load_candidates(pg_conn, "SEC EDGAR") == {}
    assert pg_conn.execute("SELECT COUNT(*) AS n FROM candidates").fetchone()["n"] == 0
    assert pg_conn.execute("SELECT COUNT(*) AS n FROM filing_events").fetchone()["n"] == 0
    assert pg_conn.execute("SELECT COUNT(*) AS n FROM state_transitions").fetchone()["n"] == 0


def test_pre_existing_rows_survive_a_later_failed_batch(pg_conn, monkeypatch):
    pre_filing = _filing(rcept_no="acc-pre")
    candidate_repository.upsert_new_candidates(pg_conn, "SEC EDGAR", [_candidate("cand-pre", pre_filing)])

    filing1 = _filing(rcept_no="acc-1")
    filing2 = _filing(rcept_no="acc-2", corp_code="0000320193")
    cand1 = _candidate("cand-1", filing1)
    cand2 = _candidate("cand-2", filing2)

    real_insert = candidate_repository._insert_candidate
    call_count = {"n": 0}

    def _flaky_insert(conn, candidate, now):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("forced failure")
        return real_insert(conn, candidate, now)

    monkeypatch.setattr(candidate_repository, "_insert_candidate", _flaky_insert)

    with pytest.raises(RuntimeError):
        candidate_repository.upsert_new_candidates(pg_conn, "SEC EDGAR", [cand1, cand2])

    remaining = candidate_repository.load_candidates(pg_conn, "SEC EDGAR")
    assert set(remaining.keys()) == {"cand-pre"}


def test_upsert_new_candidates_opens_exactly_one_transaction_per_batch(pg_conn, monkeypatch):
    call_count = {"n": 0}
    real_transaction = postgres_connection.transaction

    @contextmanager
    def _counting_transaction(conn):
        call_count["n"] += 1
        with real_transaction(conn) as c:
            yield c

    monkeypatch.setattr(candidate_repository, "transaction", _counting_transaction)

    filing1 = _filing(rcept_no="acc-1")
    filing2 = _filing(rcept_no="acc-2", corp_code="0000320193")
    candidate_repository.upsert_new_candidates(
        pg_conn, "SEC EDGAR", [_candidate("cand-1", filing1), _candidate("cand-2", filing2)],
    )

    assert call_count["n"] == 1


def test_filing_event_helper_never_opens_its_own_transaction():
    """Bytecode-level proof mirroring Phase 3B-0's own SQLite regression
    test: the transaction-free filing-event helper must never reference
    the transaction() context manager at all — the whole point of that
    helper existing separately from upsert_filing_event()."""
    from src.data_access.postgres_state_db import filing_event_repository

    assert "transaction" not in filing_event_repository._upsert_filing_event_no_transaction.__code__.co_names


# --- Durable-State Phase 4M-3 — batched (non-N+1) load_candidates() ---
# Replaces the previous 1 + 3*N per-candidate query pattern with exactly
# 3 queries total regardless of candidate count. See
# design/DECISIONS.md's Phase 4M-3 entry for the full rationale.


def _counting_execute(conn):
    """Wraps conn.execute to count calls, returning the count list and a
    restore function. Used to prove load_candidates() issues a bounded,
    small number of queries regardless of how many candidates exist —
    never one that scales with candidate count."""
    calls = []
    original_execute = conn.execute

    def _wrapped(*args, **kwargs):
        calls.append(args[0] if args else kwargs.get("query"))
        return original_execute(*args, **kwargs)

    conn.execute = _wrapped
    return calls


def test_load_candidates_issues_a_bounded_query_count_regardless_of_row_count(pg_conn):
    filings = [_filing(rcept_no=f"acc-{i}", corp_code=f"000000{i:04d}") for i in range(8)]
    candidates = [_candidate(f"cand-{i}", filings[i]) for i in range(8)]
    candidate_repository.upsert_new_candidates(pg_conn, "SEC EDGAR", candidates)

    calls = _counting_execute(pg_conn)
    loaded = candidate_repository.load_candidates(pg_conn, "SEC EDGAR")

    assert len(loaded) == 8
    # Exactly 3 queries: candidates, filing_events (via load_filing_events),
    # state_transitions — never one that scales with the 8 rows above
    # (the old per-candidate pattern would have issued 1 + 3*8 = 25).
    assert len(calls) == 3


def test_load_candidates_batched_result_matches_per_row_get_candidate_exactly(pg_conn):
    """Object-equivalence proof: the batched hydration path must produce
    byte-identical CandidateSignal objects to the existing, unchanged
    single-row get_candidate() path — a query-count change only, never a
    behavior change."""
    filing1 = _filing(rcept_no="acc-batch-1")
    filing2 = _filing(rcept_no="acc-batch-2", corp_code="0000320193")
    candidate1 = _candidate("cand-batch-1", filing1, confidence="High")
    candidate2 = _candidate(
        "cand-batch-2", filing2,
        state_history=[
            StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="2026-01-01T00:00:00+00:00"),
            StateTransition(status=CandidateStatus.NEEDS_REVIEW, at="2026-01-02T00:00:00+00:00", detail="promoted"),
        ],
    )
    candidate_repository.upsert_new_candidates(pg_conn, "SEC EDGAR", [candidate1, candidate2])

    batched = candidate_repository.load_candidates(pg_conn, "SEC EDGAR")
    individually = {
        cid: candidate_repository.get_candidate(pg_conn, cid) for cid in ("cand-batch-1", "cand-batch-2")
    }

    assert batched == individually


def test_load_candidates_empty_source_returns_empty_dict_without_extra_queries(pg_conn):
    calls = _counting_execute(pg_conn)
    assert candidate_repository.load_candidates(pg_conn, "SEC EDGAR") == {}
    assert len(calls) == 1  # only the initial candidates query — no filing/history queries for zero rows


# Note: a test simulating "candidate references a missing filing_events
# row" was considered but dropped — the schema's own foreign key
# (candidates.(source, filing_corp_code, filing_rcept_no) ->
# filing_events) makes that state genuinely unreachable via any real SQL
# path (confirmed directly: attempting to delete the parent row while a
# candidate still references it raises psycopg.errors.ForeignKeyViolation).
# The LookupError branch in both _row_to_candidate and
# _row_to_candidate_from_lookups remains as defensive code for a
# same-invariant violation this database cannot actually produce.
