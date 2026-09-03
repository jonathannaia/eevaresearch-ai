"""Deterministic organization-mention + relationship-pattern extraction
(Company Discovery Phase 2 — approved binding decision: deterministic
rules and existing known-issuer alias matching only, no NLP/NER library,
no LLM call). Same class of technique as `src/data_access/edgar/
edgar_rules.py`/`dart_rules.py`'s own keyword/phrase -> category tables
— table-driven, pure, no I/O.

An "organization mention" is a capitalized word sequence bounded by a
recognized legal-entity suffix, or an exact match against a known alias
(checked separately, in `entity_resolution.py`). A relationship is only
ever assigned when a trigger phrase from `_RELATIONSHIP_PATTERNS` is
found within a bounded character window of an organization mention —
never inferred from proximity or co-occurrence alone, except the
deliberately lowest-confidence `THEMATIC_MENTION` category, which is
exactly that: a theme/supply-chain-layer keyword and an org mention in
the same bounded window, no relationship verb at all.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from src.config.ontology import PRIMARY_THEMES, SUPPLY_CHAIN_LAYERS
from src.models.company_discovery_models import RelationshipType

# Window of characters searched around a trigger phrase / theme keyword
# for a nearby organization mention. Deliberately small — a mention
# several sentences away from the trigger phrase is not the same
# assertion, and a large window would just accumulate false positives.
_MENTION_WINDOW_CHARS = 90
# Bounded snippet stored on the evidence row — enough context to be
# independently readable, never the whole source text.
_SNIPPET_WINDOW_CHARS = 160

_LEGAL_SUFFIXES: tuple[str, ...] = (
    "Incorporated", "Corporation", "Corp", "Inc", "Limited", "Ltd", "LLC", "PLC",
    "GmbH", "AG", "K.K.", "S.A.", "N.V.", "Holdings", "Group", "Co",
)
_ORG_SUFFIX_ALTERNATION = "|".join(re.escape(s) for s in _LEGAL_SUFFIXES)
_ORG_MENTION_PATTERN = re.compile(
    r"\b[A-Z][A-Za-z0-9&.,'\-]*(?:\s+[A-Z][A-Za-z0-9&.,'\-]*){0,5}\s+(?:" + _ORG_SUFFIX_ALTERNATION + r")\.?"
)

# (relationship_type, matched_pattern_category, compiled trigger pattern).
# High/high/low weight tiers are applied later in scoring.py, not here —
# this table only ever answers "which category, if any, matched."
_RELATIONSHIP_PATTERNS: tuple[tuple[RelationshipType, str, re.Pattern], ...] = (
    (RelationshipType.SUPPLIER, "supplied_by", re.compile(r"\bsupplied by\b", re.IGNORECASE)),
    (RelationshipType.SUPPLIER, "sourced_from", re.compile(r"\bsourced from\b", re.IGNORECASE)),
    (RelationshipType.SUPPLIER, "our_supplier", re.compile(r"\bour supplier\b", re.IGNORECASE)),
    (RelationshipType.SUPPLIER, "manufactured_by", re.compile(r"\bmanufactured by\b", re.IGNORECASE)),
    (RelationshipType.SUPPLIER, "provided_by", re.compile(r"\bprovided by\b", re.IGNORECASE)),
    (RelationshipType.CUSTOMER, "customer_of", re.compile(r"\bcustomer\b", re.IGNORECASE)),
    (RelationshipType.CUSTOMER, "sold_to", re.compile(r"\bsold to\b", re.IGNORECASE)),
    (RelationshipType.CUSTOMER, "purchase_agreement_with", re.compile(r"\bpurchase agreement with\b", re.IGNORECASE)),
    (RelationshipType.CUSTOMER, "order_from", re.compile(r"\border(?:ed)? from\b", re.IGNORECASE)),
    (RelationshipType.PARTNER, "in_partnership_with", re.compile(r"\bin partnership with\b", re.IGNORECASE)),
    (RelationshipType.PARTNER, "strategic_partnership", re.compile(r"\bstrategic partnership\b", re.IGNORECASE)),
    (RelationshipType.PARTNER, "joint_venture_with", re.compile(r"\bjoint venture with\b", re.IGNORECASE)),
    (RelationshipType.PARTNER, "collaborated_with", re.compile(r"\bcollaborat(?:ed|ion)\b", re.IGNORECASE)),
    (RelationshipType.PARTNER, "teamed_up_with", re.compile(r"\bteamed up with\b", re.IGNORECASE)),
    (RelationshipType.COMPETITOR, "competitor_of", re.compile(r"\bcompetitors?\b", re.IGNORECASE)),
    (RelationshipType.COMPETITOR, "competes_with", re.compile(r"\bcompetes? with\b", re.IGNORECASE)),
    (RelationshipType.COMPETITOR, "rival_of", re.compile(r"\brival\b", re.IGNORECASE)),
)

_THEME_KEYWORDS: tuple[str, ...] = tuple(sorted({*PRIMARY_THEMES, *SUPPLY_CHAIN_LAYERS}))


@dataclass(frozen=True)
class ExtractionMatch:
    org_text: str
    relationship_type: RelationshipType
    matched_pattern_category: str
    snippet: str
    theme_slug: str | None = None
    supply_chain_layer: str | None = None


def _snippet_around(text: str, center: int) -> str:
    start = max(0, center - _SNIPPET_WINDOW_CHARS // 2)
    end = min(len(text), center + _SNIPPET_WINDOW_CHARS // 2)
    return text[start:end].strip()


def _nearest_org_mention(text: str, anchor_start: int, anchor_end: int) -> re.Match | None:
    window_start = max(0, anchor_start - _MENTION_WINDOW_CHARS)
    window_end = min(len(text), anchor_end + _MENTION_WINDOW_CHARS)
    window = text[window_start:window_end]
    candidates = list(_ORG_MENTION_PATTERN.finditer(window))
    if not candidates:
        return None
    anchor_center_in_window = (anchor_start + anchor_end) // 2 - window_start
    closest = min(candidates, key=lambda m: abs((m.start() + m.end()) // 2 - anchor_center_in_window))
    return closest


def extract_relationship_matches(text: str) -> tuple[ExtractionMatch, ...]:
    """Every relationship-pattern match (supplier/customer/partner/
    competitor) found in `text`, each paired with its nearest
    organization mention within `_MENTION_WINDOW_CHARS`. A trigger
    phrase with no nearby org mention yields nothing — never a guessed
    company. Deterministic, order-preserving, no I/O."""
    if not text:
        return ()
    matches: list[ExtractionMatch] = []
    for relationship_type, category, pattern in _RELATIONSHIP_PATTERNS:
        for trigger in pattern.finditer(text):
            org_match = _nearest_org_mention(text, trigger.start(), trigger.end())
            if org_match is None:
                continue
            window_start = max(0, trigger.start() - _MENTION_WINDOW_CHARS)
            org_text = org_match.group(0).strip()
            snippet = _snippet_around(text, window_start + org_match.start())
            matches.append(ExtractionMatch(
                org_text=org_text, relationship_type=relationship_type,
                matched_pattern_category=category, snippet=snippet,
            ))
    return tuple(matches)


def extract_thematic_mentions(text: str) -> tuple[ExtractionMatch, ...]:
    """Lowest-confidence category: a theme/supply-chain-layer keyword
    and an org mention co-occurring in the same bounded window, with no
    relationship verb at all. Only ever used as a Candidate-creation
    signal, never alone sufficient for anything beyond that (see
    scoring.py's own low weight for THEMATIC_MENTION)."""
    if not text:
        return ()
    matches: list[ExtractionMatch] = []
    lowered = text.lower()
    for keyword in _THEME_KEYWORDS:
        keyword_pattern = re.compile(r"\b" + re.escape(keyword.replace("-", " ")) + r"\b", re.IGNORECASE)
        for kw_match in keyword_pattern.finditer(lowered):
            org_match = _nearest_org_mention(text, kw_match.start(), kw_match.end())
            if org_match is None:
                continue
            window_start = max(0, kw_match.start() - _MENTION_WINDOW_CHARS)
            org_text = org_match.group(0).strip()
            snippet = _snippet_around(text, window_start + org_match.start())
            is_theme = keyword in PRIMARY_THEMES
            matches.append(ExtractionMatch(
                org_text=org_text, relationship_type=RelationshipType.THEMATIC_MENTION,
                matched_pattern_category=f"theme_keyword:{keyword}", snippet=snippet,
                theme_slug=keyword if is_theme else None,
                supply_chain_layer=keyword if not is_theme else None,
            ))
    return tuple(matches)


def extract_all_matches(text: str) -> tuple[ExtractionMatch, ...]:
    """Relationship matches first (higher-confidence categories), then
    thematic-only matches for any org mention not already covered by a
    stronger relationship match in this same text — avoids double-
    counting one mention under both a real relationship and the weak
    thematic-co-mention fallback."""
    relationship_matches = extract_relationship_matches(text)
    already_covered = {m.org_text for m in relationship_matches}
    thematic_matches = tuple(m for m in extract_thematic_mentions(text) if m.org_text not in already_covered)
    return relationship_matches + thematic_matches
