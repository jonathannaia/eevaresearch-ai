"""Public "verified update" model for the Autonomous Research Agent
(design/AUTONOMOUS_EVIDENCE_FIRST_RESEARCH_AGENT_DESIGN_2026_09_17.md,
§9.2) — deliberately a separate, narrow model rather than a repurposing
of src.models.models.Signal.

Signal is an analytical-thesis shape (direction/strength/horizon/
interpretation/contrary_evidence/validation criteria), populated only by
human-curated promotion. A VerifiedUpdate is a raw reported fact with its
evidence pointers and an explicit "what this does not establish" — it
carries no judgment fields at all, so none can be invented. Kept in its
own module (the design's own suggested alternative to models.py) so the
agent's public surface stays fully separate from the Radar/Signal family.

Every field is a plain str/tuple so the record round-trips through JSON
losslessly (to_dict/from_dict below) — this is what the public page reads
and what request_publication_decision persists on AUTO_PUBLISHED.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

VERIFIED_FILING_FACT = "VERIFIED FILING FACT"
VERIFIED_COMPANY_ANNOUNCEMENT = "VERIFIED COMPANY ANNOUNCEMENT"
VERIFIED_UPDATE_LABELS: frozenset[str] = frozenset({VERIFIED_FILING_FACT, VERIFIED_COMPANY_ANNOUNCEMENT})

PUBLISHED_BY_AUTONOMOUS_AGENT = "autonomous_agent"
PUBLISHED_BY_HUMAN_REVIEWER = "human_reviewer"
PUBLISHED_BY_VALUES: frozenset[str] = frozenset({PUBLISHED_BY_AUTONOMOUS_AGENT, PUBLISHED_BY_HUMAN_REVIEWER})


@dataclass(frozen=True)
class VerifiedUpdate:
    id: str
    candidate_id: str
    headline: str
    issuer: str
    entity_id: str
    fact_statement: str
    source_label: str
    source_date: str
    source_url: str
    source_document_id: str
    excerpt_or_locator: str
    what_this_does_not_establish: str
    label: str
    published_at: str
    published_by: str
    evidence_ids: tuple[str, ...]
    audit_session_id: str
    factual_context: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["evidence_ids"] = list(self.evidence_ids)
        payload["factual_context"] = list(self.factual_context)
        return payload

    @classmethod
    def from_dict(cls, data: dict) -> "VerifiedUpdate":
        return cls(
            id=data["id"],
            candidate_id=data["candidate_id"],
            headline=data["headline"],
            issuer=data["issuer"],
            entity_id=data["entity_id"],
            fact_statement=data["fact_statement"],
            source_label=data["source_label"],
            source_date=data["source_date"],
            source_url=data["source_url"],
            source_document_id=data["source_document_id"],
            excerpt_or_locator=data["excerpt_or_locator"],
            what_this_does_not_establish=data["what_this_does_not_establish"],
            label=data["label"],
            published_at=data["published_at"],
            published_by=data["published_by"],
            evidence_ids=tuple(data.get("evidence_ids", ())),
            audit_session_id=data["audit_session_id"],
            factual_context=tuple(data.get("factual_context", ())),
        )


def validate_verified_update(update: VerifiedUpdate) -> tuple[str, ...]:
    """Deterministic shape check for the public model — returns violation
    codes, never raises. Used by the publication service before anything
    is written to the public store, so a malformed record is rejected
    rather than rendered."""
    violations: list[str] = []
    for name in (
        "id", "candidate_id", "headline", "issuer", "entity_id", "fact_statement", "source_label",
        "source_date", "source_url", "source_document_id", "excerpt_or_locator",
        "what_this_does_not_establish", "published_at", "audit_session_id",
    ):
        if not str(getattr(update, name) or "").strip():
            violations.append(f"blank_{name}")
    if update.label not in VERIFIED_UPDATE_LABELS:
        violations.append("invalid_label")
    if update.published_by not in PUBLISHED_BY_VALUES:
        violations.append("invalid_published_by")
    if not update.evidence_ids or any(not e.strip() for e in update.evidence_ids):
        violations.append("missing_evidence_ids")
    if len(update.headline) > 140:
        violations.append("headline_too_long")
    if len(update.fact_statement) > 400:
        violations.append("fact_statement_too_long")
    if len(update.what_this_does_not_establish) > 300:
        violations.append("what_this_does_not_establish_too_long")
    return tuple(violations)
