"""Autonomous Research Agent, Phase 3 — the three persistence surfaces
behind the policy service (design §8.2, §6, §5.3): the append-only audit
stream, the private research-packet store (first-write-only, idempotent
decisions), and the public verified-update store (validated,
insert-if-absent). All against tmp_path; no network, no model call."""
from __future__ import annotations

import json

from src.data_access import agent_audit, verified_update_store
from src.logic.publication_policy import content_hash
from src.mcp_agent import packet_store
from src.mcp_agent.contracts import (
    Claim,
    ClaimCategory,
    ClaimProposal,
    ClaimType,
    EvidenceRecord,
    FreshnessStatus,
    SourceTier,
)
from src.models.models import EvidenceLocation, LocationKind
from src.models.verified_update import PUBLISHED_BY_AUTONOMOUS_AGENT, VERIFIED_FILING_FACT, VerifiedUpdate

_AT = "2026-09-17T12:00:00+00:00"


# --- audit stream -------------------------------------------------------------

def _event(event_type: str = "tool_called", inputs=None, at: str = _AT, **overrides) -> agent_audit.AuditEvent:
    return agent_audit.build_audit_event(
        session_id=overrides.pop("session_id", "sess-1"), candidate_id="cand-1", event_type=event_type,
        inputs=inputs if inputs is not None else {"issuer_id": "coreweave"}, output_summary="ok", at=at, **overrides,
    )


def test_input_hash_is_deterministic_and_key_order_independent():
    assert agent_audit.hash_inputs({"a": 1, "b": [1, 2]}) == agent_audit.hash_inputs({"b": [1, 2], "a": 1})
    assert agent_audit.hash_inputs({"a": 1}) != agent_audit.hash_inputs({"a": 2})


def test_event_id_is_deterministic_for_identical_inputs():
    assert _event().event_id == _event().event_id
    assert _event().event_id != _event(inputs={"issuer_id": "nvidia"}).event_id


def test_output_summary_is_capped_to_keep_the_stream_small():
    event = agent_audit.build_audit_event(session_id="s", candidate_id="c", event_type="t", inputs={}, output_summary="x" * 5_000, at=_AT)
    assert len(event.output_summary) == agent_audit.OUTPUT_SUMMARY_MAX_CHARS


def test_append_is_append_only_and_preserves_order(tmp_path):
    first, second = _event("issuer_resolution_attempted"), _event("filing_metadata_searched", at="2026-09-17T12:00:01+00:00")
    agent_audit.append_audit_event(tmp_path, first)
    agent_audit.append_audit_event(tmp_path, second)
    assert agent_audit.load_all_audit_events(tmp_path) == (first, second)
    lines = (tmp_path / agent_audit.AUDIT_FILENAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["event_type"] == "issuer_resolution_attempted"


def test_load_for_session_filters_by_session_and_skips_corrupt_lines(tmp_path):
    mine, theirs = _event(), _event(session_id="sess-2")
    agent_audit.append_audit_event(tmp_path, mine)
    with open(tmp_path / agent_audit.AUDIT_FILENAME, "a", encoding="utf-8") as handle:
        handle.write("{not json\n\n")
    agent_audit.append_audit_event(tmp_path, theirs)
    assert agent_audit.load_audit_events_for_session(tmp_path, "sess-1") == (mine,)
    assert agent_audit.load_audit_events_for_session(tmp_path, "sess-2") == (theirs,)


def test_missing_audit_file_reads_as_empty(tmp_path):
    assert agent_audit.load_all_audit_events(tmp_path) == ()


# --- packet store ---------------------------------------------------------------

def _evidence(evidence_id: str = "ev-1") -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id, session_id="sess-1", issuer_id="coreweave", source_tier=SourceTier.SEC_EDGAR,
        source_name="SEC EDGAR", source_url="https://www.sec.gov/x", source_document_id="0001-26-1", source_date="2026-08-14",
        excerpt_or_locator="Item 2, paragraph 3", excerpt_sha256="abc", retrieved_at=_AT, confidence="high",
        freshness_status=FreshnessStatus.CURRENT, evidence_location=EvidenceLocation(kind=LocationKind.SECTION, section="Item 2"),
    )


def _proposal(statement: str = "CoreWeave states that all GPUs in its infrastructure are NVIDIA GPUs.", evidence_ids=("ev-1",)) -> ClaimProposal:
    claim = Claim("c1", ClaimType.DIRECT_REPORTED_FACT, ClaimCategory.DIRECT_FACT, "CoreWeave reports an all-NVIDIA GPU fleet",
                  "coreweave", statement, tuple(evidence_ids), "Does not establish future sourcing.", counterparty_issuer_id=None)
    return ClaimProposal("sess-1", "cand-1", (claim,), tuple(evidence_ids))


def _packet(packet_id: str = "pk-1", proposal: ClaimProposal | None = None) -> packet_store.StoredPacket:
    proposal = proposal or _proposal()
    return packet_store.build_packet(
        packet_id=packet_id, session_id="sess-1", candidate_id="cand-1", issuer_id="coreweave", source_name="SEC EDGAR",
        saved_at=_AT, proposal=proposal, evidence=(_evidence(),), evidence_text={"ev-1": "All GPUs are NVIDIA GPUs."},
    )


def test_packet_round_trips_losslessly_including_evidence_location(tmp_path):
    packet = _packet()
    assert packet_store.save_packet(tmp_path, packet)
    loaded = packet_store.load_packet(tmp_path, "pk-1")
    assert loaded == packet
    assert loaded.evidence[0].evidence_location == EvidenceLocation(kind=LocationKind.SECTION, section="Item 2")
    assert loaded.content_hash == content_hash(packet.proposal)


def test_packet_save_is_first_write_only(tmp_path):
    assert packet_store.save_packet(tmp_path, _packet())
    changed = _packet(proposal=_proposal(statement="Something else entirely."))
    assert not packet_store.save_packet(tmp_path, changed)
    assert packet_store.load_packet(tmp_path, "pk-1").proposal.claims[0].statement.startswith("CoreWeave states")


def test_record_decision_is_idempotent(tmp_path):
    packet_store.save_packet(tmp_path, _packet())
    first = packet_store.record_decision(tmp_path, "pk-1", "AUTO_PUBLISHED", ("all_rows_passed",), _AT)
    second = packet_store.record_decision(tmp_path, "pk-1", "REVIEW_REQUIRED", ("later",), "2026-09-18T00:00:00+00:00")
    assert first == second
    assert second.decision == "AUTO_PUBLISHED" and second.decided_at == _AT
    assert packet_store.record_decision(tmp_path, "missing", "AUTO_PUBLISHED", (), _AT) is None


def test_previously_published_counts_only_published_or_draft_packets(tmp_path):
    published, draft, reviewed = _packet("pk-pub"), _packet("pk-draft", _proposal(statement="Draft fact.")), _packet("pk-rev", _proposal(statement="Reviewed fact."))
    for packet in (published, draft, reviewed):
        packet_store.save_packet(tmp_path, packet)
    packet_store.record_decision(tmp_path, "pk-pub", "AUTO_PUBLISHED", (), _AT)
    packet_store.record_decision(tmp_path, "pk-draft", "VERIFIED_DRAFT", (), _AT)
    packet_store.record_decision(tmp_path, "pk-rev", "REVIEW_REQUIRED", (), _AT)
    hashes, evidence_sets = packet_store.previously_published(tmp_path)
    assert hashes == {published.content_hash, draft.content_hash}
    assert evidence_sets == {frozenset({"ev-1"})}
    hashes_excl, _ = packet_store.previously_published(tmp_path, exclude_packet_id="pk-pub")
    assert hashes_excl == {draft.content_hash}


def test_undecided_and_missing_packets_are_never_previously_published(tmp_path):
    packet_store.save_packet(tmp_path, _packet())
    assert packet_store.previously_published(tmp_path) == (frozenset(), frozenset())
    assert packet_store.load_packet(tmp_path, "nope") is None


# --- public verified-update store -------------------------------------------------

def _update(update_id: str = "vu-1", published_at: str = _AT, **overrides) -> VerifiedUpdate:
    defaults = dict(
        id=update_id, candidate_id="cand-1", headline="CoreWeave reports an all-NVIDIA GPU fleet", issuer="CoreWeave, Inc.",
        entity_id="coreweave", fact_statement="All GPUs in its infrastructure are NVIDIA GPUs.", source_label="SEC EDGAR 10-Q",
        source_date="2026-08-14", source_url="https://www.sec.gov/x", source_document_id="0001-26-1",
        excerpt_or_locator="Item 2, paragraph 3", what_this_does_not_establish="Does not establish future sourcing.",
        label=VERIFIED_FILING_FACT, published_at=published_at, published_by=PUBLISHED_BY_AUTONOMOUS_AGENT,
        evidence_ids=("ev-1",), audit_session_id="sess-1",
    )
    defaults.update(overrides)
    return VerifiedUpdate(**defaults)


def test_verified_updates_insert_if_absent_and_reject_invalid_records(tmp_path):
    valid, invalid = _update(), _update("vu-bad", label="TRUST ME")
    assert verified_update_store.append_verified_updates(tmp_path, (valid, invalid, valid)) == ("vu-1",)
    assert verified_update_store.append_verified_updates(tmp_path, (valid,)) == ()
    assert verified_update_store.load_verified_updates(tmp_path) == (valid,)


def test_verified_updates_load_newest_first_and_skip_corrupt_records(tmp_path):
    older, newer = _update("vu-old", "2026-09-16T00:00:00+00:00"), _update("vu-new", "2026-09-17T00:00:00+00:00")
    verified_update_store.append_verified_updates(tmp_path, (older, newer))
    path = tmp_path / verified_update_store.VERIFIED_UPDATES_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    data["vu-corrupt"] = {"id": "vu-corrupt"}
    path.write_text(json.dumps(data), encoding="utf-8")
    assert verified_update_store.load_verified_updates(tmp_path) == (newer, older)


def test_missing_verified_update_file_reads_as_empty(tmp_path):
    assert verified_update_store.load_verified_updates(tmp_path) == ()
