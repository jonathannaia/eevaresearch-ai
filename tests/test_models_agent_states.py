"""Autonomous Research Agent, Phase 1 — model additions (design/
AUTONOMOUS_EVIDENCE_FIRST_RESEARCH_AGENT_DESIGN_2026_09_17.md, §5.1/§9.2/
§12): two new CandidateStatus values, one provenance-only field on
CandidateSignal, and the separate VerifiedUpdate public model. Every
existing status value and every existing construction site must keep
working unchanged — that backward-compatibility is the main thing this
file guards, alongside the fact that published_by survives every
persistence backend (JSON, SQLite; Postgres shares the SQLite SQL and is
covered by the pg-skipping schema suite)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from src.data_access.dart import candidate_store
from src.data_access.state_db import candidate_repository, connection, schema
from src.logic.signal_promotion import is_eligible_for_signal
from src.models.models import CandidateSignal, CandidateStatus, FilingEvent, StateTransition
from src.models.verified_update import (
    PUBLISHED_BY_AUTONOMOUS_AGENT,
    VERIFIED_COMPANY_ANNOUNCEMENT,
    VERIFIED_FILING_FACT,
    VerifiedUpdate,
    validate_verified_update,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _filing(rcept_no: str = "0001193125-26-000001") -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="0001769628", corp_name="CoreWeave, Inc.", stock_code="CRWV",
        report_nm="10-Q", rcept_dt="2026-08-14", flr_nm="CoreWeave, Inc.", pblntf_ty="10-Q",
        theme_slug="ai-buildout", subtheme_slug="cloud-infrastructure",
        source_url=f"https://www.sec.gov/Archives/edgar/data/0001769628/{rcept_no}/",
        retrieved_at=_now(), source_name="SEC EDGAR", original_language="English",
        primary_document="crwv-10q.htm",
    )


def _candidate(candidate_id: str = "cand-1", **overrides) -> CandidateSignal:
    defaults = dict(
        id=candidate_id, filing=_filing(), matched_rules=["periodic_report:10-Q"], confidence="Moderate",
        status=CandidateStatus.CANDIDATE_DETECTED,
        state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at=_now())],
    )
    defaults.update(overrides)
    return CandidateSignal(**defaults)


# --- CandidateStatus additions ---------------------------------------------

def test_new_terminal_states_exist_with_distinct_values():
    assert CandidateStatus.VERIFIED_DRAFT.value == "Verified draft"
    assert CandidateStatus.INSUFFICIENT_EVIDENCE.value == "Insufficient evidence"
    assert len({s.value for s in CandidateStatus}) == len(list(CandidateStatus))


def test_reused_terminal_states_are_unchanged():
    # The design reuses these verbatim — no rename, no parallel vocabulary.
    assert CandidateStatus.NOT_MATERIAL.value == "Not material"
    assert CandidateStatus.RETRIEVAL_FAILED.value == "Retrieval failed"
    assert CandidateStatus.NEEDS_REVIEW.value == "Needs review"
    assert CandidateStatus.PUBLISHED.value == "Published"


def test_new_states_round_trip_through_their_string_values():
    assert CandidateStatus("Verified draft") is CandidateStatus.VERIFIED_DRAFT
    assert CandidateStatus("Insufficient evidence") is CandidateStatus.INSUFFICIENT_EVIDENCE


# --- CandidateSignal.published_by ------------------------------------------

def test_published_by_defaults_to_none_for_existing_construction_sites():
    """None = provenance not recorded. Never inferred as human-reviewed —
    not even for a PUBLISHED candidate."""
    assert _candidate().published_by is None
    assert _candidate(status=CandidateStatus.PUBLISHED).published_by is None


def test_published_by_round_trips_through_json_candidate_store(tmp_path):
    agent_published = _candidate("agent-1", status=CandidateStatus.PUBLISHED, published_by=PUBLISHED_BY_AUTONOMOUS_AGENT)
    unrecorded = _candidate("unrecorded-1", status=CandidateStatus.PUBLISHED)
    candidate_store.save_candidates(tmp_path, {c.id: c for c in (agent_published, unrecorded)})
    payload = json.loads((tmp_path / "dart_candidates.json").read_text(encoding="utf-8"))
    assert payload["unrecorded-1"]["published_by"] is None
    loaded = candidate_store.load_candidates(tmp_path)
    assert loaded["agent-1"].published_by == PUBLISHED_BY_AUTONOMOUS_AGENT
    assert loaded["unrecorded-1"].published_by is None


def test_legacy_json_record_without_published_by_loads_as_none(tmp_path):
    candidate_store.save_candidates(tmp_path, {"legacy": _candidate("legacy", status=CandidateStatus.PUBLISHED)})
    path = tmp_path / "dart_candidates.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["legacy"]["published_by"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert candidate_store.load_candidates(tmp_path)["legacy"].published_by is None


def test_published_by_round_trips_through_sqlite_insert_and_update():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    assert schema.get_schema_version(conn) == schema.CURRENT_SCHEMA_VERSION
    candidate = _candidate("sq-1")
    candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [candidate])
    stored = candidate_repository.get_candidate(conn, "sq-1")
    assert stored.published_by is None
    assert conn.execute("SELECT published_by FROM candidates WHERE id = 'sq-1'").fetchone()["published_by"] is None

    stored.status = CandidateStatus.PUBLISHED
    stored.published_by = PUBLISHED_BY_AUTONOMOUS_AGENT
    version = candidate_repository.get_candidate_version(conn, "sq-1")
    outcome = candidate_repository.update_candidate(conn, stored, expected_version=version)
    assert outcome.status == "updated"
    assert candidate_repository.get_candidate(conn, "sq-1").published_by == PUBLISHED_BY_AUTONOMOUS_AGENT


# --- signal_promotion is provenance-blind -----------------------------------

def test_published_promotes_identically_regardless_of_provenance():
    assert is_eligible_for_signal(_candidate(status=CandidateStatus.PUBLISHED))
    assert is_eligible_for_signal(
        _candidate(status=CandidateStatus.PUBLISHED, published_by=PUBLISHED_BY_AUTONOMOUS_AGENT)
    )


def test_new_agent_states_are_never_signal_eligible():
    # The existing allowlist (status == PUBLISHED) excludes them by default —
    # exactly the reason the design reuses PUBLISHED instead of adding an
    # AUTO_PUBLISHED status.
    assert not is_eligible_for_signal(_candidate(status=CandidateStatus.VERIFIED_DRAFT))
    assert not is_eligible_for_signal(_candidate(status=CandidateStatus.INSUFFICIENT_EVIDENCE))


# --- VerifiedUpdate --------------------------------------------------------

def _update(**overrides) -> VerifiedUpdate:
    defaults = dict(
        id="vu-1", candidate_id="cand-1", headline="CoreWeave reports all infrastructure GPUs are NVIDIA GPUs",
        issuer="CoreWeave, Inc.", entity_id="coreweave",
        fact_statement="CoreWeave states in its filing that all GPUs in its infrastructure are NVIDIA GPUs.",
        source_label="SEC EDGAR 10-Q", source_date="2026-08-14",
        source_url="https://www.sec.gov/Archives/edgar/data/0001769628/0001193125-26-000001/",
        source_document_id="0001193125-26-000001", excerpt_or_locator="Item 2 — Overview, paragraph 3",
        what_this_does_not_establish="Does not establish future GPU sourcing, pricing, or exclusivity duration.",
        label=VERIFIED_FILING_FACT, published_at=_now(), published_by=PUBLISHED_BY_AUTONOMOUS_AGENT,
        evidence_ids=("ev-1",), audit_session_id="sess-1",
    )
    defaults.update(overrides)
    return VerifiedUpdate(**defaults)


def test_verified_update_round_trips_through_dict():
    original = _update(factual_context=("Prior 10-K also named NVIDIA.",))
    restored = VerifiedUpdate.from_dict(json.loads(json.dumps(original.to_dict())))
    assert restored == original
    assert isinstance(restored.evidence_ids, tuple)
    assert isinstance(restored.factual_context, tuple)


def test_verified_update_carries_no_judgment_fields():
    # The whole reason this is not a Signal: no direction/strength/horizon/
    # interpretation can ever be invented for a raw reported fact.
    names = set(VerifiedUpdate.__dataclass_fields__)
    assert names.isdisjoint({"direction", "strength", "horizon", "interpretation", "contrary_evidence"})


def test_validate_verified_update_accepts_both_labels():
    assert validate_verified_update(_update(label=VERIFIED_FILING_FACT)) == ()
    assert validate_verified_update(_update(label=VERIFIED_COMPANY_ANNOUNCEMENT)) == ()


def test_validate_verified_update_rejects_malformed_records():
    assert "invalid_label" in validate_verified_update(_update(label="TRUST ME"))
    assert "invalid_published_by" in validate_verified_update(_update(published_by="the_model"))
    assert "missing_evidence_ids" in validate_verified_update(_update(evidence_ids=()))
    assert "blank_what_this_does_not_establish" in validate_verified_update(_update(what_this_does_not_establish=" "))
    assert "headline_too_long" in validate_verified_update(_update(headline="x" * 141))
