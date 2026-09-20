"""Durable persistence of one finished evaluation (blocker E5), and the
rule that a live agent run requires Postgres.

Everything here is fixture-driven: no model session, credential, network
call or live agent is involved."""
from __future__ import annotations

import json

import pytest

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.agent_audit import build_audit_event
from src.data_access.state_db import agent_repository as sqlite_agent
from src.data_access.state_db import connection, schema
from src.logic import agent_persistence
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

import scripts.research_agent_worker as worker


@pytest.fixture
def store():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    return backend_factory._AgentStoreRepository(conn=conn, module=sqlite_agent)


def _evidence() -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id="ev-1", session_id="sess-1", issuer_id="NVDA", source_tier=SourceTier.ISSUER_IR,
        source_name="NVIDIA IR", source_url="https://example.test/a", source_document_id="doc-1",
        source_date="2026-09-01", excerpt_or_locator="Revenue rose to $30.0 billion.",
        excerpt_sha256="sha-1", retrieved_at="2026-09-20T00:00:00+00:00", confidence="High",
        freshness_status=FreshnessStatus.CURRENT,
    )


def _proposal() -> ClaimProposal:
    claim = Claim(
        claim_id="c-1", issuer_id="NVDA", claim_type=ClaimType.DIRECT_REPORTED_FACT,
        claim_category=ClaimCategory.DIRECT_FACT,
        headline="Revenue rose to $30.0 billion", statement="Revenue rose to $30.0 billion.",
        what_this_does_not_establish="It does not establish future demand.",
        evidence_ids=("ev-1",), factual_context_evidence_ids=(),
    )
    return ClaimProposal(session_id="sess-1", candidate_id="cand-1", claims=(claim,),
                         retrieved_evidence_ids=("ev-1",))


def _policy(decision: PublicationDecision = PublicationDecision.AUTO_PUBLISHED) -> PolicyDecision:
    return PolicyDecision(
        decision=decision, reasons=("every row passed",),
        row_results=(RowResult(row=0, condition="publication kill switch disabled", passed=True),),
        content_hash="hash-1",
    )


def _persist(store, *, mode: str, kill_switch_on: bool, decision=PublicationDecision.AUTO_PUBLISHED,
             quote_verified: bool = True) -> str:
    packet = agent_persistence.build_packet(
        packet_id="pkt-1", session_id="sess-1", candidate_id="cand-1", source="SEC EDGAR",
        seed_document_id="acc-1", proposal=_proposal(), content_hash="hash-1",
        created_at="2026-09-20T00:01:00+00:00", job_id="job-1", run_id="run-1", issuer_id="NVDA",
        evidence=(_evidence(),),
    )
    row = agent_persistence.build_decision(
        packet_id="pkt-1", policy=_policy(decision), mode=mode, kill_switch_on=kill_switch_on,
        quote_verified=quote_verified, decided_at="2026-09-20T00:02:00+00:00",
    )
    events = agent_persistence.audit_rows(
        [build_audit_event(session_id="sess-1", candidate_id="cand-1", event_type="session_started",
                           inputs={"scope": "cand-1"}, output_summary="started", at="2026-09-20T00:00:30+00:00")],
        run_id="run-1", packet_id="pkt-1",
    )
    return agent_persistence.persist_evaluation(store, packet=packet, decision=row, events=events)


def test_a_shadow_evaluation_lands_in_the_agent_tables_only(store):
    assert _persist(store, mode="shadow", kill_switch_on=False) == "pkt-1"
    packet = store.get_packet("pkt-1")
    assert (packet.candidate_id, packet.source, packet.issuer_id) == ("cand-1", "SEC EDGAR", "NVDA")
    assert json.loads(packet.proposal_json)["claims"][0]["claim_id"] == "c-1"
    assert [e.evidence_id for e in packet.evidence] == ["ev-1"]
    assert packet.evidence[0].excerpt_or_locator == "Revenue rose to $30.0 billion."
    assert packet.evidence[0].source_tier == SourceTier.ISSUER_IR.value


def test_shadow_records_the_would_be_decision_as_policy_and_no_action_as_effective(store):
    _persist(store, mode="shadow", kill_switch_on=False)
    decision = store.get_decision("pkt-1")
    assert decision.policy_decision == "AUTO_PUBLISHED"
    assert decision.effective_decision == EFFECTIVE_NO_ACTION
    assert decision.blocked_by == "shadow_mode"
    assert (decision.mode, decision.kill_switch_on) == ("shadow", False)
    assert (decision.candidate_status_written, decision.published) == (False, False)
    assert decision.policy_version == POLICY_VERSION


def test_the_kill_switch_is_the_blocking_reason_but_shadow_context_is_kept(store):
    _persist(store, mode="shadow", kill_switch_on=True)
    decision = store.get_decision("pkt-1")
    assert decision.blocked_by == "kill_switch"
    assert (decision.mode, decision.kill_switch_on) == ("shadow", True)
    assert decision.effective_decision == EFFECTIVE_NO_ACTION


def test_with_no_hold_at_all_the_effective_decision_is_the_policy_decision(store):
    _persist(store, mode="publish", kill_switch_on=False)
    decision = store.get_decision("pkt-1")
    assert decision.blocked_by is None
    assert decision.effective_decision == decision.policy_decision == "AUTO_PUBLISHED"


def test_reasons_and_row_results_survive_as_structured_json(store):
    _persist(store, mode="shadow", kill_switch_on=False)
    decision = store.get_decision("pkt-1")
    assert json.loads(decision.reasons_json) == ["every row passed"]
    assert json.loads(decision.row_results_json)[0][:3] == [0, "publication kill switch disabled", True]


def test_the_session_audit_trail_is_stored_against_the_packet(store):
    _persist(store, mode="shadow", kill_switch_on=False)
    events = store.audit_events_for_packet("pkt-1")
    assert [e.event_type for e in events] == ["session_started"]
    assert events[0].candidate_id == "cand-1"
    # The stored input is the session's own sanitized hash, never raw input.
    assert events[0].inputs_json and len(events[0].inputs_json) == 64


def test_a_decision_can_never_be_stored_without_its_packet(store):
    decision = agent_persistence.build_decision(
        packet_id="pkt-missing", policy=_policy(), mode="shadow", kill_switch_on=False,
        quote_verified=True, decided_at="t",
    )
    # persist_evaluation always writes the packet first; a caller that skips
    # it is writing an orphan, which the review page would never join.
    assert store.get_packet("pkt-missing") is None
    store.record_decision(decision)
    assert store.list_decisions() == ()


# --- live mode requires Postgres (blocker E5's configuration half) ---------------

@pytest.mark.parametrize("backend", ["json", "sqlite", ""])
def test_a_live_run_refuses_any_backend_other_than_postgres(backend):
    problem = worker._validate_live_settings(Settings(db_backend=backend, research_agent_service_token="t" * 32))
    assert problem is not None and "postgres" in problem


def test_a_live_run_accepts_postgres():
    assert worker._validate_live_settings(
        Settings(db_backend="postgres", research_agent_service_token="t" * 32, research_agent_mode="shadow")
    ) is None


@pytest.mark.parametrize("mode", ["off", "", "on", "enabled"])
def test_a_live_run_refuses_a_mode_that_resolves_to_off(mode):
    """Everything else can be configured correctly and the worker still
    does nothing unless the mode says so — including when the mode is a
    typo, which resolves to off rather than to something permissive."""
    problem = worker._validate_live_settings(
        Settings(db_backend="postgres", research_agent_service_token="t" * 32, research_agent_mode=mode)
    )
    assert problem is not None and "resolves to off" in problem


def test_a_live_run_still_refuses_without_its_service_credential():
    problem = worker._validate_live_settings(Settings(db_backend="postgres", research_agent_service_token=None))
    assert problem is not None and "SERVICE_TOKEN" in problem
