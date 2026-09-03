"""postgres_state_db.candidate_issuer_repository — Company Discovery
Phase 2, against the real local disposable Postgres test container.
Direct sibling of test_state_db_candidate_issuer_repository.py
(SQLite) — same proofs, independently implemented backend."""
from __future__ import annotations

import psycopg
import pytest

from src.data_access.postgres_state_db.candidate_issuer_repository import (
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
from tests._postgres_test_support import pg_conn, pg_isolated_connection  # noqa: F401


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


def test_creating_a_candidate_always_persists_its_first_evidence_row(pg_isolated_connection):
    conn = pg_isolated_connection
    create_candidate_with_evidence(
        conn, issuer_id="candidate:abc123", legal_name="Example Materials Corp.", native_name="",
        country_or_jurisdiction="Unconfirmed", entity_kind="corporate", coverage_state="Discovered",
        resolution_confidence="Medium", discovered_via="test", now="2026-09-01T00:00:00+00:00",
        evidence=_evidence(), alias_text="example materials",
    )
    record = get_candidate(conn, "candidate:abc123")
    assert record is not None
    evidence_rows = get_evidence_for_issuer(conn, "candidate:abc123")
    assert len(evidence_rows) == 1


def test_dedup_key_unique_constraint_prevents_duplicate_evidence(pg_isolated_connection):
    conn = pg_isolated_connection
    create_candidate_with_evidence(
        conn, issuer_id="candidate:abc123", legal_name="Example Materials Corp.", native_name="",
        country_or_jurisdiction="Unconfirmed", entity_kind="corporate", coverage_state="Discovered",
        resolution_confidence="Medium", discovered_via="test", now="2026-09-01T00:00:00+00:00",
        evidence=_evidence(dedup_key="dedup-1"), alias_text="example materials",
    )
    assert evidence_exists(conn, "dedup-1")
    append_evidence_to_existing_candidate(conn, _evidence(dedup_key="dedup-1"), None, "2026-09-02T00:00:00+00:00")
    assert len(get_evidence_for_issuer(conn, "candidate:abc123")) == 1


def test_append_evidence_to_existing_candidate_requires_the_candidate_to_already_exist(pg_isolated_connection):
    conn = pg_isolated_connection
    with pytest.raises(ValueError):
        append_evidence_to_existing_candidate(conn, _evidence(issuer_id="candidate:doesnotexist"), None, "2026-09-01T00:00:00+00:00")


def test_list_candidates_filters_by_coverage_state(pg_isolated_connection):
    conn = pg_isolated_connection
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
    assert {r.issuer.issuer_id for r in discovered} == {"candidate:disc"}
    assert len(list_candidates(conn)) == 2


def test_record_score_updates_cache_and_appends_history(pg_isolated_connection):
    conn = pg_isolated_connection
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


def test_transition_state_updates_coverage_state(pg_isolated_connection):
    conn = pg_isolated_connection
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


def test_identifier_source_native_id_unique_constraint(pg_isolated_connection):
    conn = pg_isolated_connection
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
    with pytest.raises(psycopg.Error):
        add_confirmed_identifier(conn, CandidateIdentifier(
            issuer_id="candidate:b", source="SEC EDGAR", native_id="0000000001",
            confirmed_via="test", confirmed_at="2026-09-01T00:00:00+00:00",
        ))


def test_worker_status_round_trips_and_upserts(pg_isolated_connection):
    conn = pg_isolated_connection
    assert get_worker_status(conn) is None
    upsert_worker_status(conn, CandidateWorkerStatus(
        worker_key="company_discovery", last_tick_started_at="2026-09-01T00:00:00+00:00",
        last_tick_completed_at="2026-09-01T00:00:05+00:00", last_failure_code=None,
        evidence_created_last_run=3, candidates_created_last_run=2, candidates_quarantined_last_run=0,
        updated_at="2026-09-01T00:00:05+00:00",
    ))
    status = get_worker_status(conn)
    assert status.evidence_created_last_run == 3


def test_get_aliases_returns_every_known_alias(pg_isolated_connection):
    conn = pg_isolated_connection
    create_candidate_with_evidence(
        conn, issuer_id="candidate:abc123", legal_name="Example Materials Corp.", native_name="",
        country_or_jurisdiction="Unconfirmed", entity_kind="corporate", coverage_state="Discovered",
        resolution_confidence="Medium", discovered_via="test", now="2026-09-01T00:00:00+00:00",
        evidence=_evidence(dedup_key="d1"), alias_text="example materials",
    )
    assert get_aliases(conn) == {"example materials": "candidate:abc123"}


# --- Identity-stability invariant (same proofs as the SQLite sibling) ------


def test_duplicate_issuer_id_creation_raises_rather_than_silently_overwriting(pg_isolated_connection):
    conn = pg_isolated_connection
    create_candidate_with_evidence(
        conn, issuer_id="candidate:abc123", legal_name="Example Materials Corp.", native_name="",
        country_or_jurisdiction="Unconfirmed", entity_kind="corporate", coverage_state="Discovered",
        resolution_confidence="Medium", discovered_via="test", now="2026-09-01T00:00:00+00:00",
        evidence=_evidence(dedup_key="d1"), alias_text="example materials",
    )
    with pytest.raises(psycopg.Error):
        create_candidate_with_evidence(
            conn, issuer_id="candidate:abc123", legal_name="A Renamed Corp.", native_name="",
            country_or_jurisdiction="South Korea", entity_kind="corporate", coverage_state="Discovered",
            resolution_confidence="High", discovered_via="test-2", now="2026-09-02T00:00:00+00:00",
            evidence=_evidence(dedup_key="d2"), alias_text="a renamed",
        )
    conn.rollback()
    record = get_candidate(conn, "candidate:abc123")
    assert record.issuer.legal_name == "Example Materials Corp."
    assert record.issuer.country_or_jurisdiction == "Unconfirmed"
    assert len(list_candidates(conn)) == 1


def test_issuer_id_and_identity_fields_survive_a_full_lifecycle_unsplit(pg_isolated_connection):
    conn = pg_isolated_connection
    issuer_id = "candidate:abc123"
    create_candidate_with_evidence(
        conn, issuer_id=issuer_id, legal_name="Example Materials Corp.", native_name="",
        country_or_jurisdiction="Unconfirmed", entity_kind="corporate", coverage_state="Discovered",
        resolution_confidence="Medium", discovered_via="test", now="2026-09-01T00:00:00+00:00",
        evidence=_evidence(issuer_id=issuer_id, dedup_key="d1"), alias_text="example materials",
    )
    append_evidence_to_existing_candidate(
        conn, _evidence(issuer_id=issuer_id, dedup_key="d2", source_record_id="daily_news:x", source_type=SourceType.DAILY_NEWS),
        "example materials corp", "2026-09-02T00:00:00+00:00",
    )
    record_score(conn, CandidateScoreSnapshot(
        issuer_id=issuer_id, computed_at="2026-09-03T00:00:00+00:00", composite_score=0.72,
        evidence_count=2, independent_source_count=2, score_breakdown={"relationship_specificity": 1.0},
    ))
    transition_state(conn, CandidateStateTransition(
        issuer_id=issuer_id, from_state="Discovered", to_state="Archived",
        at="2026-09-04T00:00:00+00:00", detail="stale", triggered_by="worker:company_discovery",
    ))
    add_confirmed_identifier(conn, CandidateIdentifier(
        issuer_id=issuer_id, source="SEC EDGAR", native_id="0000000001",
        confirmed_via="test", confirmed_at="2026-09-05T00:00:00+00:00",
    ))

    record = get_candidate(conn, issuer_id)
    assert record.issuer.issuer_id == issuer_id
    assert record.issuer.legal_name == "Example Materials Corp."
    assert record.issuer.country_or_jurisdiction == "Unconfirmed"
    assert len(list_candidates(conn)) == 1

    evidence_rows = get_evidence_for_issuer(conn, issuer_id)
    assert len(evidence_rows) == 2
    assert all(row["issuer_id"] == issuer_id for row in evidence_rows)

    score_rows = conn.execute("SELECT issuer_id FROM candidate_score_history").fetchall()
    assert [r["issuer_id"] for r in score_rows] == [issuer_id]

    transition_rows = conn.execute(
        "SELECT issuer_id, from_state, to_state FROM candidate_state_transitions ORDER BY id",
    ).fetchall()
    assert [r["issuer_id"] for r in transition_rows] == [issuer_id, issuer_id]
    assert transition_rows[0]["to_state"] == "Discovered"
    assert transition_rows[1]["to_state"] == "Archived"

    identifier_rows = conn.execute("SELECT issuer_id FROM candidate_issuer_identifiers").fetchall()
    assert [r["issuer_id"] for r in identifier_rows] == [issuer_id]

    alias_rows = conn.execute("SELECT issuer_id, alias_text FROM candidate_aliases ORDER BY id").fetchall()
    assert {r["issuer_id"] for r in alias_rows} == {issuer_id}
    assert {r["alias_text"] for r in alias_rows} == {"example materials", "example materials corp"}
