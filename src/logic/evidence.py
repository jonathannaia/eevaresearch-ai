"""Claim-type and freshness display helpers. Kept separate from the
EvidenceItem/ClaimType definitions in src/models/models.py so the model
stays free of presentation concerns — this module maps a ClaimType/
freshness value to the label and color token a UI badge should use."""
from __future__ import annotations

from src.logic.formatting import fmt_date
from src.models.models import ClaimType, EvidenceItem

CLAIM_TYPE_COLOR: dict[ClaimType, str] = {
    ClaimType.FACT: "fact",
    ClaimType.INTERPRETATION: "interpretation",
    ClaimType.INFERENCE: "inference",
    ClaimType.UNCERTAINTY: "uncertainty",
}

FRESHNESS_COLOR: dict[str, str] = {
    "Fresh": "fresh",
    "Aging": "aging",
    "Stale": "stale",
    "Unknown": "unknown",
}


def claim_type_badge(claim_type: ClaimType) -> tuple[str, str]:
    """Returns (label, color_token) for a claim type badge."""
    return claim_type.value, CLAIM_TYPE_COLOR.get(claim_type, "unknown")


def freshness_badge(evidence: EvidenceItem) -> tuple[str, str]:
    label = evidence.freshness_label
    return label, FRESHNESS_COLOR.get(label, "unknown")


def source_label(evidence: EvidenceItem) -> str:
    """A single display string for an evidence source — always names the
    demo dataset explicitly in this phase rather than presenting a bare
    (and possibly missing) URL."""
    if evidence.source_url:
        return f"{evidence.source_name} — {evidence.source_url}"
    return f"{evidence.source_name} (no external source — demo data)"


def cite_label(evidence: EvidenceItem) -> str:
    """Issuer · venue · date · document location, for the <cite> line under
    a source excerpt (brief §7)."""
    parts = [evidence.source_name, evidence.source_type, fmt_date(evidence.published_at)]
    if evidence.document_location:
        parts.append(evidence.document_location)
    return " · ".join(parts)
