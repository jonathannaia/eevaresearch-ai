"""Private research-packet store (design §6 save_private_research_packet,
request_publication_decision). Private by construction: nothing public
ever reads this file — signal_promotion reads only CandidateSignal.status,
and the public page reads only verified_update_store.

A packet is written once (first-write-only, like research_store) and its
decision is recorded once (idempotent: a second request returns the
stored decision unchanged). request_publication_decision evaluates
against THIS persisted record, never against anything the agent hands
it at decision time — that is what makes the decision server-side.

Row 9 (duplicate/idempotency) reads previously_published() from here:
the content hashes and evidence-id sets of every packet already decided
AUTO_PUBLISHED or VERIFIED_DRAFT.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from src.logic.publication_policy import content_hash, evidence_id_set
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

PACKET_FILENAME = "agent_research_packets.json"
PUBLISHED_OR_DRAFT_DECISIONS: frozenset[str] = frozenset({"AUTO_PUBLISHED", "VERIFIED_DRAFT"})


@dataclass(frozen=True)
class StoredPacket:
    packet_id: str
    session_id: str
    candidate_id: str
    issuer_id: str
    source_name: str
    saved_at: str
    proposal: ClaimProposal
    evidence: tuple[EvidenceRecord, ...]
    evidence_text: dict[str, str]
    content_hash: str
    evidence_id_set: tuple[str, ...]
    decision: str | None = None
    decision_reasons: tuple[str, ...] = ()
    decided_at: str | None = None


# --- (de)serialization -------------------------------------------------------

def _location_to_dict(location: EvidenceLocation | None) -> dict | None:
    return None if location is None else asdict(location) | {"kind": location.kind.value}


def _location_from_dict(data: dict | None) -> EvidenceLocation | None:
    if not data:
        return None
    return EvidenceLocation(
        kind=LocationKind(data.get("kind", LocationKind.UNAVAILABLE.value)), page=data.get("page"),
        section=data.get("section"), table=data.get("table"), paragraph_index=data.get("paragraph_index"),
    )


def evidence_to_dict(record: EvidenceRecord) -> dict:
    payload = asdict(record)
    payload["source_tier"] = record.source_tier.value
    payload["freshness_status"] = record.freshness_status.value
    payload["evidence_location"] = _location_to_dict(record.evidence_location)
    return payload


def evidence_from_dict(data: dict) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=data["evidence_id"], session_id=data["session_id"], issuer_id=data["issuer_id"],
        source_tier=SourceTier(data["source_tier"]), source_name=data["source_name"], source_url=data["source_url"],
        source_document_id=data["source_document_id"], source_date=data["source_date"],
        excerpt_or_locator=data["excerpt_or_locator"], excerpt_sha256=data["excerpt_sha256"],
        retrieved_at=data["retrieved_at"], confidence=data["confidence"],
        freshness_status=FreshnessStatus(data["freshness_status"]),
        evidence_location=_location_from_dict(data.get("evidence_location")),
    )


def proposal_to_dict(proposal: ClaimProposal) -> dict:
    return {
        "session_id": proposal.session_id,
        "candidate_id": proposal.candidate_id,
        "retrieved_evidence_ids": list(proposal.retrieved_evidence_ids),
        "claims": [
            {
                "claim_id": c.claim_id, "claim_type": c.claim_type.value, "claim_category": c.claim_category.value,
                "headline": c.headline, "issuer_id": c.issuer_id, "statement": c.statement,
                "evidence_ids": list(c.evidence_ids), "what_this_does_not_establish": c.what_this_does_not_establish,
                "factual_context_evidence_ids": list(c.factual_context_evidence_ids),
                "counterparty_issuer_id": c.counterparty_issuer_id,
            }
            for c in proposal.claims
        ],
    }


def proposal_from_dict(data: dict) -> ClaimProposal:
    return ClaimProposal(
        session_id=data["session_id"], candidate_id=data["candidate_id"],
        retrieved_evidence_ids=tuple(data.get("retrieved_evidence_ids", ())),
        claims=tuple(
            Claim(
                claim_id=c["claim_id"], claim_type=ClaimType(c["claim_type"]),
                claim_category=ClaimCategory(c["claim_category"]), headline=c["headline"], issuer_id=c["issuer_id"],
                statement=c["statement"], evidence_ids=tuple(c.get("evidence_ids", ())),
                what_this_does_not_establish=c["what_this_does_not_establish"],
                factual_context_evidence_ids=tuple(c.get("factual_context_evidence_ids", ())),
                counterparty_issuer_id=c.get("counterparty_issuer_id"),
            )
            for c in data.get("claims", ())
        ),
    )


def _packet_to_dict(packet: StoredPacket) -> dict:
    return {
        "packet_id": packet.packet_id, "session_id": packet.session_id, "candidate_id": packet.candidate_id,
        "issuer_id": packet.issuer_id, "source_name": packet.source_name, "saved_at": packet.saved_at,
        "proposal": proposal_to_dict(packet.proposal), "evidence": [evidence_to_dict(e) for e in packet.evidence],
        "evidence_text": dict(packet.evidence_text), "content_hash": packet.content_hash,
        "evidence_id_set": list(packet.evidence_id_set), "decision": packet.decision,
        "decision_reasons": list(packet.decision_reasons), "decided_at": packet.decided_at,
    }


def _packet_from_dict(data: dict) -> StoredPacket:
    return StoredPacket(
        packet_id=data["packet_id"], session_id=data["session_id"], candidate_id=data["candidate_id"],
        issuer_id=data["issuer_id"], source_name=data["source_name"], saved_at=data["saved_at"],
        proposal=proposal_from_dict(data["proposal"]), evidence=tuple(evidence_from_dict(e) for e in data.get("evidence", ())),
        evidence_text=dict(data.get("evidence_text", {})), content_hash=data["content_hash"],
        evidence_id_set=tuple(data.get("evidence_id_set", ())), decision=data.get("decision"),
        decision_reasons=tuple(data.get("decision_reasons", ())), decided_at=data.get("decided_at"),
    )


# --- storage ------------------------------------------------------------------

def _path(cache_dir: Path) -> Path:
    return cache_dir / PACKET_FILENAME


def _load(cache_dir: Path) -> dict[str, dict]:
    path = _path(cache_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_atomic(cache_dir: Path, payload: dict[str, dict]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=PACKET_FILENAME + ".", dir=cache_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, _path(cache_dir))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def build_packet(
    *, packet_id: str, session_id: str, candidate_id: str, issuer_id: str, source_name: str, saved_at: str,
    proposal: ClaimProposal, evidence: tuple[EvidenceRecord, ...], evidence_text: dict[str, str],
) -> StoredPacket:
    return StoredPacket(
        packet_id=packet_id, session_id=session_id, candidate_id=candidate_id, issuer_id=issuer_id,
        source_name=source_name, saved_at=saved_at, proposal=proposal, evidence=evidence, evidence_text=evidence_text,
        content_hash=content_hash(proposal), evidence_id_set=tuple(sorted(evidence_id_set(proposal))),
    )


def save_packet(cache_dir: Path, packet: StoredPacket) -> bool:
    """First-write-only: False (no I/O) if the packet id already exists."""
    store = _load(cache_dir)
    if packet.packet_id in store:
        return False
    store[packet.packet_id] = _packet_to_dict(packet)
    _write_atomic(cache_dir, store)
    return True


def load_packet(cache_dir: Path, packet_id: str) -> StoredPacket | None:
    data = _load(cache_dir).get(packet_id)
    if data is None:
        return None
    try:
        return _packet_from_dict(data)
    except (KeyError, ValueError, TypeError):
        return None


def record_decision(cache_dir: Path, packet_id: str, decision: str, reasons: tuple[str, ...], decided_at: str) -> StoredPacket | None:
    """Idempotent: an already-decided packet is returned unchanged."""
    store = _load(cache_dir)
    data = store.get(packet_id)
    if data is None:
        return None
    existing = _packet_from_dict(data)
    if existing.decision is not None:
        return existing
    decided = replace(existing, decision=decision, decision_reasons=tuple(reasons), decided_at=decided_at)
    store[packet_id] = _packet_to_dict(decided)
    _write_atomic(cache_dir, store)
    return decided


def previously_published(cache_dir: Path, exclude_packet_id: str | None = None) -> tuple[frozenset[str], frozenset[frozenset[str]]]:
    hashes: set[str] = set()
    evidence_sets: set[frozenset[str]] = set()
    for packet_id, data in _load(cache_dir).items():
        if packet_id == exclude_packet_id or data.get("decision") not in PUBLISHED_OR_DRAFT_DECISIONS:
            continue
        hashes.add(data["content_hash"])
        evidence_sets.add(frozenset(data.get("evidence_id_set", ())))
    return frozenset(hashes), frozenset(evidence_sets)
