"""Alias/entity resolution, canonical ID generation, and quarantine/
reject classification (Company Discovery Phase 2). Pure, no I/O — every
input here (Core company names, Discovery stub names, previously-seen
Candidate aliases) is supplied by the caller, never fetched by this
module.

Resolution order (approved plan): (1) exact match against Core
(`TrackedCompany.name`/`.native_name`) — never creates or touches a
Candidate; (2) exact match against `DISCOVERY_STUBS` — informational
only, never converted into a Candidate in Phase 2 (approved binding
decision — legacy-stub formalization is a separate, later decision);
(3) exact match against a previously-seen Candidate alias; (4) a
deterministic ambiguity check (substring containment against any known
name, never a fuzzy/similarity match) -> QUARANTINED; (5) otherwise a
new Candidate, unless the mention is non-corporate or too generic to be
meaningful, in which case REJECTED.
"""
from __future__ import annotations

import hashlib
import re

from src.models.company_discovery_models import EntityKind

# Filing source display name -> short namespace prefix, kept as this
# module's own explicit constant rather than importing
# issuer_registry.py's private (underscore-prefixed) _SOURCE_ID_PREFIX
# across modules — same three literal values, for naming consistency
# with the existing issuer_id scheme.
_FILING_SOURCE_PREFIX: dict[str, str] = {
    "OpenDART / DART": "dart",
    "SEC EDGAR": "edgar",
    "EDINET": "edinet",
}

_LEGAL_SUFFIX_STRIP_PATTERN = re.compile(
    r"[,.]?\s*\b(Incorporated|Corporation|Corp|Inc|Limited|Ltd|LLC|PLC|GmbH|AG|K\.K\.|S\.A\.|N\.V\.|Holdings|Group|Co)\.?\s*$",
    re.IGNORECASE,
)
_WHITESPACE_PATTERN = re.compile(r"\s+")

# Deliberately keyword-based, not NLP/NER — a name containing any of
# these (case-insensitive) is classified non-corporate and REJECTED
# before ever reaching scoring, per the approved coverage-policy rules
# for funds/agencies/governments.
_NON_CORPORATE_KEYWORDS: tuple[tuple[str, EntityKind], ...] = (
    ("ministry", EntityKind.GOVERNMENT),
    ("government of", EntityKind.GOVERNMENT),
    ("department of", EntityKind.AGENCY),
    ("authority", EntityKind.AGENCY),
    ("commission", EntityKind.AGENCY),
    ("fund", EntityKind.FUND),
)

# After legal-suffix stripping, a mention shorter than this is too
# generic/fragment-like to be a meaningful entity — REJECTED rather
# than scored.
_MIN_NORMALIZED_NAME_LENGTH = 4


def normalize_entity_name(raw: str) -> str:
    """Case-folded, legal-suffix-stripped, whitespace-collapsed. The one
    normalization every exact-match/substring comparison in this module
    uses — never a fuzzy/similarity score."""
    stripped = _LEGAL_SUFFIX_STRIP_PATTERN.sub("", raw.strip())
    collapsed = _WHITESPACE_PATTERN.sub(" ", stripped).strip()
    return collapsed.lower()


def classify_entity_kind(raw_name: str) -> EntityKind:
    lowered = raw_name.lower()
    for keyword, kind in _NON_CORPORATE_KEYWORDS:
        if keyword in lowered:
            return kind
    return EntityKind.CORPORATE


def is_well_formed_mention(normalized_name: str) -> bool:
    return len(normalized_name) >= _MIN_NORMALIZED_NAME_LENGTH


def generate_issuer_id(legal_name: str, country_or_jurisdiction: str) -> str:
    """`candidate:{sha256(normalized_legal_name | country_or_jurisdiction)[:16]}`
    — the exact form approved. Deterministic and stable: the same real-
    world entity, seen again under the identical normalized name and
    jurisdiction, always resolves to the same issuer_id; a later-
    confirmed ticker/exchange never changes it (see
    `candidate_issuer_identifiers`, not `issuer_id`, for that)."""
    normalized = normalize_entity_name(legal_name)
    digest = hashlib.sha256(f"{normalized}|{country_or_jurisdiction}".encode("utf-8")).hexdigest()[:16]
    return f"candidate:{digest}"


def canonical_filing_record_id(source_name: str, rcept_no: str) -> str:
    """Source-namespaced so a DART/EDGAR/EDINET rcept_no can never
    collide with a Daily News story id even if the raw values happen to
    coincide as strings."""
    prefix = _FILING_SOURCE_PREFIX.get(source_name, "filing")
    return f"{prefix}:{rcept_no}"


def canonical_daily_news_record_id(story_id: str) -> str:
    return f"daily_news:{story_id}"


def generate_evidence_dedup_key(
    issuer_id: str, source_record_id: str, relationship_type: str, matched_pattern_category: str,
) -> str:
    """Re-processing the identical (issuer, source record, relationship,
    rule) combination — whether from the recurring worker's rolling
    overlap window or a re-run of the one-shot backfill — always
    produces this same key, so the `UNIQUE(dedup_key)` constraint makes
    reprocessing safe rather than duplicating evidence."""
    raw = f"{issuer_id}|{source_record_id}|{relationship_type}|{matched_pattern_category}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class ResolutionOutcome:
    MATCHED_CORE = "matched_core"
    MATCHED_STUB = "matched_stub"
    MATCHED_EXISTING_CANDIDATE = "matched_existing_candidate"
    NEW_REJECTED = "new_rejected"
    NEW_QUARANTINED = "new_quarantined"
    NEW_CANDIDATE = "new_candidate"


class ResolutionResult:
    __slots__ = ("outcome", "normalized_name", "matched_issuer_id", "entity_kind", "reason")

    def __init__(
        self, outcome: str, normalized_name: str, matched_issuer_id: str | None = None,
        entity_kind: EntityKind = EntityKind.CORPORATE, reason: str | None = None,
    ) -> None:
        self.outcome = outcome
        self.normalized_name = normalized_name
        self.matched_issuer_id = matched_issuer_id
        self.entity_kind = entity_kind
        self.reason = reason

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ResolutionResult):
            return NotImplemented
        return (
            self.outcome, self.normalized_name, self.matched_issuer_id, self.entity_kind, self.reason,
        ) == (other.outcome, other.normalized_name, other.matched_issuer_id, other.entity_kind, other.reason)

    def __repr__(self) -> str:
        return (
            f"ResolutionResult(outcome={self.outcome!r}, normalized_name={self.normalized_name!r}, "
            f"matched_issuer_id={self.matched_issuer_id!r}, entity_kind={self.entity_kind!r}, reason={self.reason!r})"
        )


def resolve_mention(
    org_text: str,
    *,
    core_names: frozenset[str],
    stub_names: frozenset[str],
    known_aliases: dict[str, str],
) -> ResolutionResult:
    """`core_names`/`stub_names` are already-normalized name sets (Core
    `TrackedCompany.name`/`.native_name`, `DISCOVERY_STUBS` `.legal_name`/
    `.aliases`); `known_aliases` maps an already-normalized alias string
    to an existing Candidate `issuer_id`. All three are supplied by the
    caller — this function never reads a registry itself, keeping it
    pure and independently testable."""
    normalized = normalize_entity_name(org_text)

    entity_kind = classify_entity_kind(org_text)
    if entity_kind != EntityKind.CORPORATE:
        return ResolutionResult(
            ResolutionOutcome.NEW_REJECTED, normalized, entity_kind=entity_kind,
            reason=f"non_corporate:{entity_kind.value}",
        )

    if not is_well_formed_mention(normalized):
        return ResolutionResult(ResolutionOutcome.NEW_REJECTED, normalized, reason="too_short_or_generic")

    if normalized in core_names:
        return ResolutionResult(ResolutionOutcome.MATCHED_CORE, normalized)

    if normalized in stub_names:
        return ResolutionResult(ResolutionOutcome.MATCHED_STUB, normalized)

    if normalized in known_aliases:
        return ResolutionResult(
            ResolutionOutcome.MATCHED_EXISTING_CANDIDATE, normalized,
            matched_issuer_id=known_aliases[normalized],
        )

    # Deterministic ambiguity check: substring containment against any
    # already-known name (Core, stub, or existing Candidate) — never a
    # fuzzy/similarity score. Catches the genuinely ambiguous subsidiary/
    # lookalike case (e.g. "NVIDIA Korea Ltd." containing "nvidia") that
    # an exact match alone would miss, without guessing which relationship
    # it actually is.
    all_known_names = core_names | stub_names | frozenset(known_aliases)
    for known in all_known_names:
        if known and (known in normalized or normalized in known) and known != normalized:
            return ResolutionResult(
                ResolutionOutcome.NEW_QUARANTINED, normalized,
                reason=f"ambiguous_overlap_with:{known}",
            )

    return ResolutionResult(ResolutionOutcome.NEW_CANDIDATE, normalized)
