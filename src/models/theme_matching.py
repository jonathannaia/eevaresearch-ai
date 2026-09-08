"""EevaResearch — Phase A0 (design/DECISIONS.md). Internal-only model
types for deterministic, rule-based Research-Case-to-Theme matching.

These types support a future autonomous semiconductor bottleneck
research pipeline: connecting already-persisted internal Research
Cases to internal Theme drafts via explicit, auditable, non-semantic
rules — never an LLM, never an investment conclusion, never a company-
role inference.

Deliberately internal-only: nothing here is imported by
src/ui/pages/themes_research.py, any other public UI page, or
ThemeRepositoryProtocol (the public/UI-facing, published-only Theme
read seam in src/data_access/backend_factory.py). A ResearchCaseThemeMatch
references an internal Research Case (case_id) and an internal Theme
(theme_id) by id only — it never carries or exposes Research Case
content itself.

This module defines types only — no I/O, no persistence, no wall-clock
reads. Standard-library imports only, plus the one additive
EvidenceDirection.CONTEXT member from src.models.theme_research. Every
dataclass is frozen; no mutable default collections."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.models.theme_research import EvidenceDirection


class MatchConfidence(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class MatchReviewStatus(str, Enum):
    PENDING_REVIEW = "PENDING_REVIEW"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class ThemeMatchingScope:
    """The deterministic matching configuration for one internal Theme
    draft — never shown to a public user, never derived automatically.
    `sector_tags`/`sector_subtags` are compared against the existing
    FilingEvent.theme_slug/subtheme_slug fields already populated by
    Radar's own scan pipelines (see src.config.tracked_companies) —
    this module never introduces a second taxonomy."""

    theme_id: str
    sector_tags: tuple[str, ...]
    sector_subtags: tuple[str, ...]
    allowed_matched_rule_categories: tuple[str, ...]
    required_keywords: tuple[str, ...]
    excluded_keywords: tuple[str, ...]


@dataclass(frozen=True)
class ResearchCaseThemeMatch:
    """One deterministic, auditable candidate link between an internal
    Research Case and an internal Theme. Never constructed by anything
    but src.logic.research_case_theme_matching.evaluate_theme_match();
    this module itself performs no matching logic. `direction` is always
    EvidenceDirection.CONTEXT for an automatically produced match — see
    that enum member's own docstring for why."""

    id: str
    case_id: str
    theme_id: str
    confidence: MatchConfidence
    direction: EvidenceDirection
    matched_sector_tag: str | None
    matched_rule_categories: tuple[str, ...]
    matched_keywords: tuple[str, ...]
    rationale: str
    created_at: str


@dataclass(frozen=True)
class ThemeMatchReviewDecision:
    """A human reviewer's own, separately recorded decision on one
    ResearchCaseThemeMatch — never produced automatically. Not
    constructed or consumed by anything in this Phase A0 step; the
    shape is defined now so a later, separately approved persistence
    step has an agreed-upon record to store."""

    id: str
    match_id: str
    decision: MatchReviewStatus
    reviewer_note: str | None
    reviewed_at: str


# Note: the pure matching function itself (evaluate_theme_match) lives
# in src.logic.research_case_theme_matching — deliberately NOT
# src.logic.theme_matching, which is the pre-existing, unrelated Theme
# Registry Foundation's own free-text alias/event-pattern classifier
# (see design/THEME_REGISTRY_FOUNDATION.md). The two share no types.
