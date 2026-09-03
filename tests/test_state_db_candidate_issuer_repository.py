"""state_db.candidate_issuer_repository — Company Discovery Phase 2.
In-memory SQLite only; no real fetch/pipeline/network call. Proves the
evidence-required invariant, dedup/idempotency, and UNIQUE constraints
at the repository layer directly."""
from __future__ import annotations

import sqlite3

import pytest

from src.data_access.state_db import connection, schema
from src.data_access.state_db.candidate_issuer_repository import (
    CandidateIdentifier,
    append_evidence_to_existing_candidate,
    create_candidate_with_evidence,
    create_rejected_or_quarantined_candidate,
    evidence_exists,
    get_aliases,
    get_candidate,
    get_evidence_for_issuer,
    get_worker_status,
    list_candidates,
    record_score,
    transition_state,
    add_confirmed_identifier,
    upsert_worker_status,
)
from src.models.company_discovery_models import (
    CandidateEvidence,
    CandidateScoreSnapshot,
    CandidateStateTransition,
    CandidateWorkerStatus,
    RelationshipType,
    SourceType,
)


def _conn():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    return conn


def _evidence(**overrides) -> CandidateEvidence:
    fields = dict(
        issuer_id="candidate:abc123", source_type=SourceType.FILING, source_name="SEC EDGAR",
        source_record_id="edgar:0001045810-26-000078", source_url="https://www.sec.gov/test",
        source_snippet="components supplied by Example Materials Corp.",
        relationship_type=RelationshipType.SUPPLIER, matched_pattern_category="supplied_by",
        extraction_timestamp="2026-09-01T00:00:00+00:00", dedup_key="dedup-1",
    )
    fields.update(overrides)
    return CandidateEvidence(**fields)


def test_creating_a_candidate_always_persists_its_first_evidence_row():
    conn = _conn()
    create_candidate_with_evidence(
        conn, issuer_id="candidate:abc123", legal_name="Example Materials Corp.", native_name="",
        country_or_jurisdiction="Unconfirmed", entity_kind="corporate", coverage_state="Discovered",
        resolution_confidence="Medium", discovered_via="test", now="2026-09-01T00:00:00+00:00",
        evidence=_evidence(), alias_text="example materials",
    )
    record = get_candidate(conn, "candidate:abc123")
    assert record is not None
    assert record.issuer.legal_name == "Example Materials Corp."
    evidence_rows = get_evidence_for_issuer(conn, "candidate:abc123")
    assert len(evidence_rows) == 1
    assert evidence_rows[0]["relationship_type"] == "supplier"


def test_evidence_less_candidate_is_structurally_impossible():
    """There is no bare `insert candidate` function exposed at all —
    only functions that also require evidence in the same call."""
    import src.data_access.state_db.candidate_issuer_repository as repo_module

    public_names = [name for name in dir(repo_module) if not name.startswith("_")]
    insert_only_functions = [n for n in public_names if "create" in n.lower() and "candidate" in n.lower()]
    assert insert_only_functions  # sanity: the creation functions exist
    for name in insert_only_functions:
        func = getattr(repo_module, name)
        import inspect
        params = inspect.signature(func).parameters
        assert "evidence" in params, f"{name} does not require evidence — evidence-less candidate would be possible"


def test_dedup_key_unique_constraint_prevents_duplicate_evidence():
    conn = _conn()
    create_candidate_with_evidence(
        conn, issuer_id="candidate:abc123", legal_name="Example Materials Corp.", native_name="",
        country_or_jurisdiction="Unconfirmed", entity_kind="corporate", coverage_state="Discovered",
        resolution_confidence="Medium", discovered_via="test", now="2026-09-01T00:00:00+00:00",
        evidence=_evidence(dedup_key="dedup-1"), alias_text="example materials",
    )
    assert evidence_exists(conn, "dedup-1")
    # Re-inserting the identical dedup_key is silently skipped (INSERT OR
    # IGNORE), never raises, never duplicates.
    append_evidence_to_existing_candidate(conn, _evidence(dedup_key="dedup-1"), None, "2026-09-02T00:00:00+00:00")
    assert len(get_evidence_for_issuer(conn, "candidate:abc123")) == 1


def test_append_evidence_to_existing_candidate_requires_the_candidate_to_already_exist():
    conn = _conn()
    with pytest.raises(ValueError):
        append_evidence_to_existing_candidate(conn, _evidence(issuer_id="candidate:doesnotexist"), None, "2026-09-01T00:00:00+00:00")


def test_append_evidence_updates_last_evidence_at():
    conn = _conn()
    create_candidate_with_evidence(
        conn, issuer_id="candidate:abc123", legal_name="Example Materials Corp.", native_name="",
        country_or_jurisdiction="Unconfirmed", entity_kind="corporate", coverage_state="Discovered",
        resolution_confidence="Medium", discovered_via="test", now="2026-09-01T00:00:00+00:00",
        evidence=_evidence(dedup_key="dedup-1"), alias_text="example materials",
    )
    append_evidence_to_existing_candidate(
        conn, _evidence(dedup_key="dedup-2", source_record_id="edgar:other"), "example materials", "2026-09-05T00:00:00+00:00",
    )
    record = get_candidate(conn, "candidate:abc123")
    assert record.last_evidence_at == "2026-09-05T00:00:00+00:00"
    assert len(get_evidence_for_issuer(conn, "candidate:abc123")) == 2


def test_list_candidates_filters_by_coverage_state():
    conn = _conn()
    create_candidate_with_evidence(
        conn, issuer_id="candidate:disc", legal_name="Discovered Co.", native_name="",
        country_or_jurisdiction="Unconfirmed", entity_kind="corporate", coverage_state="Discovered",
        resolution_confidence="Medium", discovered_via="test", now="2026-09-01T00:00:00+00:00",
        evidence=_evidence(issuer_id="candidate:disc", dedup_key="d1"), alias_text="discovered co",
    )
    create_rejected_or_quarantined_candidate(
        conn, issuer_id="candidate:rej", legal_name="Ministry of Trade", country_or_jurisdiction="Unconfirmed",
        entity_kind="government", coverage_state="Rejected", discovered_via="test", now="2026-09-01T00:00:00+00:00",
        evidence=_evidence(issuer_id="candidate:rej", dedup_key="d2"), alias_text="ministry of trade",
    )
    discovered = list_candidates(conn, coverage_state="Discovered")
    rejected = list_candidates(conn, coverage_state="Rejected")
    assert {r.issuer.issuer_id for r in discovered} == {"candidate:disc"}
    assert {r.issuer.issuer_id for r in rejected} == {"candidate:rej"}
    assert len(list_candidates(conn)) == 2


def test_get_aliases_returns_every_known_alias():
    conn = _conn()
    create_candidate_with_evidence(
        conn, issuer_id="candidate:abc123", legal_name="Example Materials Corp.", native_name="",
        country_or_jurisdiction="Unconfirmed", entity_kind="corporate", coverage_state="Discovered",
        resolution_confidence="Medium", discovered_via="test", now="2026-09-01T00:00:00+00:00",
        evidence=_evidence(dedup_key="d1"), alias_text="example materials",
    )
    assert get_aliases(conn) == {"example materials": "candidate:abc123"}


def test_record_score_updates_cache_and_appends_history():
    conn = _conn()
    create_candidate_with_evidence(
        conn, issuer_id="candidate:abc123", legal_name="Example Materials Corp.", native_name="",
        country_or_jurisdiction="Unconfirmed", entity_kind="corporate", coverage_state="Discovered",
        resolution_confidence="Medium", discovered_via="test", now="2026-09-01T00:00:00+00:00",
        evidence=_evidence(dedup_key="d1"), alias_text="example materials",
    )
    record_score(conn, CandidateScoreSnapshot(
        issuer_id="candidate:abc123", computed_at="2026-09-02T00:00:00+00:00", composite_score=0.65,
        evidence_count=1, independent_source_count=1, score_breakdown={"relationship_specificity": 1.0},
    ))
    record = get_candidate(conn, "candidate:abc123")
    assert record.composite_score == 0.65
    rows = conn.execute("SELECT * FROM candidate_score_history WHERE issuer_id = ?", ("candidate:abc123",)).fetchall()
    assert len(rows) == 1


def test_transition_state_updates_coverage_state_and_logs_transition():
    conn = _conn()
    create_candidate_with_evidence(
        conn, issuer_id="candidate:abc123", legal_name="Example Materials Corp.", native_name="",
        country_or_jurisdiction="Unconfirmed", entity_kind="corporate", coverage_state="Discovered",
        resolution_confidence="Medium", discovered_via="test", now="2026-09-01T00:00:00+00:00",
        evidence=_evidence(dedup_key="d1"), alias_text="example materials",
    )
    transition_state(conn, CandidateStateTransition(
        issuer_id="candidate:abc123", from_state="Discovered", to_state="Archived",
        at="2026-12-01T00:00:00+00:00", detail="stale", triggered_by="worker:company_discovery",
    ))
    record = get_candidate(conn, "candidate:abc123")
    assert record.issuer.coverage_state.value == "Archived"
    rows = conn.execute("SELECT * FROM candidate_state_transitions WHERE issuer_id = ?", ("candidate:abc123",)).fetchall()
    assert len(rows) == 2  # creation transition + this one


def test_identifier_source_native_id_unique_constraint():
    conn = _conn()
    create_candidate_with_evidence(
        conn, issuer_id="candidate:a", legal_name="A Corp.", native_name="",
        country_or_jurisdiction="Unconfirmed", entity_kind="corporate", coverage_state="Discovered",
        resolution_confidence="Medium", discovered_via="test", now="2026-09-01T00:00:00+00:00",
        evidence=_evidence(issuer_id="candidate:a", dedup_key="d1"), alias_text="a",
    )
    create_candidate_with_evidence(
        conn, issuer_id="candidate:b", legal_name="B Corp.", native_name="",
        country_or_jurisdiction="Unconfirmed", entity_kind="corporate", coverage_state="Discovered",
        resolution_confidence="Medium", discovered_via="test", now="2026-09-01T00:00:00+00:00",
        evidence=_evidence(issuer_id="candidate:b", dedup_key="d2"), alias_text="b",
    )
    add_confirmed_identifier(conn, CandidateIdentifier(
        issuer_id="candidate:a", source="SEC EDGAR", native_id="0000000001",
        confirmed_via="test", confirmed_at="2026-09-01T00:00:00+00:00",
    ))
    with pytest.raises(sqlite3.IntegrityError):
        add_confirmed_identifier(conn, CandidateIdentifier(
            issuer_id="candidate:b", source="SEC EDGAR", native_id="0000000001",
            confirmed_via="test", confirmed_at="2026-09-01T00:00:00+00:00",
        ))


def test_worker_status_round_trips_and_upserts():
    conn = _conn()
    assert get_worker_status(conn) is None
    upsert_worker_status(conn, CandidateWorkerStatus(
        worker_key="company_discovery", last_tick_started_at="2026-09-01T00:00:00+00:00",
        last_tick_completed_at="2026-09-01T00:00:05+00:00", last_failure_code=None,
        evidence_created_last_run=3, candidates_created_last_run=2, candidates_quarantined_last_run=0,
        updated_at="2026-09-01T00:00:05+00:00",
    ))
    status = get_worker_status(conn)
    assert status.evidence_created_last_run == 3
    upsert_worker_status(conn, CandidateWorkerStatus(
        worker_key="company_discovery", last_tick_started_at="2026-09-02T00:00:00+00:00",
        last_tick_completed_at="2026-09-02T00:00:05+00:00", last_failure_code="ValueError",
        evidence_created_last_run=0, candidates_created_last_run=0, candidates_quarantined_last_run=1,
        updated_at="2026-09-02T00:00:05+00:00",
    ))
    status = get_worker_status(conn)
    assert status.last_failure_code == "ValueError"
    assert status.candidates_quarantined_last_run == 1


def test_foreign_key_enforced_for_evidence_without_a_candidate_row():
    """create_candidate_with_evidence always creates the parent row
    first in the same transaction, so this constraint is never hit in
    practice — this test proves the FK itself is real, not merely
    assumed, matching test_foreign_keys_are_enforced_on_every_
    connection's own existing convention for the candidates table."""
    conn = _conn()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO candidate_evidence (issuer_id, source_type, source_name, source_record_id, source_url, "
            "source_snippet, relationship_type, matched_pattern_category, extraction_timestamp, dedup_key) "
            "VALUES ('candidate:orphan', 'Filing', 'SEC EDGAR', 'edgar:x', 'https://x', 's', 'supplier', 'x', 'now', 'dk1')"
        )
        conn.commit()


# --- Identity-stability invariant: issuer_id is immutable once a ------------
# --- candidate row exists; no later operation can recompute it, ------------
# --- create a duplicate row, or split its evidence/score/state history. ----


def test_duplicate_issuer_id_creation_raises_rather_than_silently_overwriting():
    """create_candidate_with_evidence()'s own INSERT has no ON CONFLICT
    clause — a second call with the same issuer_id must raise, never
    silently overwrite the existing row or create a duplicate."""
    conn = _conn()
    create_candidate_with_evidence(
        conn, issuer_id="candidate:abc123", legal_name="Example Materials Corp.", native_name="",
        country_or_jurisdiction="Unconfirmed", entity_kind="corporate", coverage_state="Discovered",
        resolution_confidence="Medium", discovered_via="test", now="2026-09-01T00:00:00+00:00",
        evidence=_evidence(dedup_key="d1"), alias_text="example materials",
    )
    with pytest.raises(sqlite3.IntegrityError):
        create_candidate_with_evidence(
            conn, issuer_id="candidate:abc123", legal_name="A Renamed Corp.", native_name="",
            country_or_jurisdiction="South Korea", entity_kind="corporate", coverage_state="Discovered",
            resolution_confidence="High", discovered_via="test-2", now="2026-09-02T00:00:00+00:00",
            evidence=_evidence(dedup_key="d2"), alias_text="a renamed",
        )
    # The original row is untouched by the failed attempt.
    record = get_candidate(conn, "candidate:abc123")
    assert record.issuer.legal_name == "Example Materials Corp."
    assert record.issuer.country_or_jurisdiction == "Unconfirmed"
    assert len(list_candidates(conn)) == 1


def test_issuer_id_and_identity_fields_survive_a_full_lifecycle_unsplit():
    """A full sequence of later operations — a second evidence row (from
    a different source), a score computation, a state transition, and a
    confirmed identifier attachment — must never change issuer_id,
    legal_name, or country_or_jurisdiction, and every evidence/score/
    state-transition row must stay attached to that one issuer_id: no
    later operation recomputes identity or splits history across two
    rows. There is no code path in this module that updates legal_name,
    country_or_jurisdiction, or issuer_id after creation (verified
    directly against the module's own UPDATE statements above) — this
    test proves the observable behavior that structural fact implies."""
    conn = _conn()
    issuer_id = "candidate:abc123"
    create_candidate_with_evidence(
        conn, issuer_id=issuer_id, legal_name="Example Materials Corp.", native_name="",
        country_or_jurisdiction="Unconfirmed", entity_kind="corporate", coverage_state="Discovered",
        resolution_confidence="Medium", discovered_via="test", now="2026-09-01T00:00:00+00:00",
        evidence=_evidence(issuer_id=issuer_id, dedup_key="d1"), alias_text="example materials",
    )

    # Alias attachment (a second, differently-worded mention of the same entity).
    append_evidence_to_existing_candidate(
        conn, _evidence(issuer_id=issuer_id, dedup_key="d2", source_record_id="daily_news:x", source_type=SourceType.DAILY_NEWS),
        "example materials corp", "2026-09-02T00:00:00+00:00",
    )
    # Score computation.
    record_score(conn, CandidateScoreSnapshot(
        issuer_id=issuer_id, computed_at="2026-09-03T00:00:00+00:00", composite_score=0.72,
        evidence_count=2, independent_source_count=2, score_breakdown={"relationship_specificity": 1.0},
    ))
    # State transition (e.g. later archived).
    transition_state(conn, CandidateStateTransition(
        issuer_id=issuer_id, from_state="Discovered", to_state="Archived",
        at="2026-09-04T00:00:00+00:00", detail="stale", triggered_by="worker:company_discovery",
    ))
    # Identifier attachment (a later-confirmed public-market identifier).
    add_confirmed_identifier(conn, CandidateIdentifier(
        issuer_id=issuer_id, source="SEC EDGAR", native_id="0000000001",
        confirmed_via="test", confirmed_at="2026-09-05T00:00:00+00:00",
    ))

    # Identity is exactly what it was at creation — never recomputed.
    record = get_candidate(conn, issuer_id)
    assert record.issuer.issuer_id == issuer_id
    assert record.issuer.legal_name == "Example Materials Corp."
    assert record.issuer.country_or_jurisdiction == "Unconfirmed"

    # No duplicate row was ever created for this entity.
    assert len(list_candidates(conn)) == 1

    # Every piece of history stayed attached to the one issuer_id — none
    # of it split off under a second, different id.
    evidence_rows = get_evidence_for_issuer(conn, issuer_id)
    assert len(evidence_rows) == 2
    assert all(row["issuer_id"] == issuer_id for row in evidence_rows)

    score_rows = conn.execute("SELECT issuer_id FROM candidate_score_history").fetchall()
    assert [r["issuer_id"] for r in score_rows] == [issuer_id]

    transition_rows = conn.execute(
        "SELECT issuer_id, from_state, to_state FROM candidate_state_transitions ORDER BY id",
    ).fetchall()
    assert [r["issuer_id"] for r in transition_rows] == [issuer_id, issuer_id]
    assert transition_rows[0]["to_state"] == "Discovered"  # creation transition
    assert transition_rows[1]["to_state"] == "Archived"

    identifier_rows = conn.execute("SELECT issuer_id FROM candidate_issuer_identifiers").fetchall()
    assert [r["issuer_id"] for r in identifier_rows] == [issuer_id]

    alias_rows = conn.execute("SELECT issuer_id, alias_text FROM candidate_aliases ORDER BY id").fetchall()
    assert {r["issuer_id"] for r in alias_rows} == {issuer_id}
    assert {r["alias_text"] for r in alias_rows} == {"example materials", "example materials corp"}
