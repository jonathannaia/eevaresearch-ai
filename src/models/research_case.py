"""EevaResearch Phase 4, Step 1 (design/DECISIONS.md) — immutable Research
Case model types: the durable, source-grounded record foundation for a
future evidence-backed deep-research capability (bottlenecks,
dependencies, cross-company relationships, transmission paths).

This module defines types only — no I/O, no persistence, no wall-clock
reads, no automatic research generation, no entity resolution. Every
timestamp (`created_at`, `added_at`) is a plain caller-supplied `str`;
nothing here ever calls `datetime.now()`.

Deliberately decoupled from every other model in this codebase: no
import of `src.models.models` (CandidateSignal/FilingEvent/Signal/etc.),
`src.models.daily_news_models` (NewsStory), any source client, or any UI
type — mirroring the exact isolation already established between Radar
and Daily News (see `daily_news_models.py`'s own docstring) and between
`src.logic.prior_disclosure_comparison` and the rest of the app. A
Research Case references a trigger (a Radar candidate, a Daily News
story, or a future source) only via a generic, source-agnostic
`(trigger_source_type, trigger_source_id)` pair plus copied-verbatim
display fields — never a live object reference, and never by importing
the triggering system's own types.

Standard-library imports only. Every dataclass is frozen; no mutable
default collections (`tuple`/`None` defaults only)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ResearchCaseStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    SUPERSEDED = "SUPERSEDED"


class RelationshipRole(str, Enum):
    SUPPLIER = "SUPPLIER"
    CUSTOMER = "CUSTOMER"
    COMPETITOR = "COMPETITOR"
    PARTNER = "PARTNER"
    REGULATOR = "REGULATOR"
    INFRASTRUCTURE_PROVIDER = "INFRASTRUCTURE_PROVIDER"
    MANUFACTURER = "MANUFACTURER"
    DISTRIBUTOR = "DISTRIBUTOR"
    FINANCIER = "FINANCIER"
    OTHER = "OTHER"


class BottleneckType(str, Enum):
    MANUFACTURING_CAPACITY = "MANUFACTURING_CAPACITY"
    COMPONENT_SUPPLY = "COMPONENT_SUPPLY"
    MEMORY_SUPPLY = "MEMORY_SUPPLY"
    PACKAGING = "PACKAGING"
    POWER_GRID = "POWER_GRID"
    ENERGY = "ENERGY"
    WATER = "WATER"
    LOGISTICS = "LOGISTICS"
    LABOR = "LABOR"
    PERMITTING = "PERMITTING"
    EXPORT_CONTROL = "EXPORT_CONTROL"
    REGULATION = "REGULATION"
    FINANCING = "FINANCING"
    DEMAND = "DEMAND"
    CUSTOMER_CONCENTRATION = "CUSTOMER_CONCENTRATION"
    PRICING = "PRICING"
    INVENTORY = "INVENTORY"
    QUALIFICATION = "QUALIFICATION"
    OTHER = "OTHER"


class AssertionStatus(str, Enum):
    DIRECTLY_SUPPORTED = "DIRECTLY_SUPPORTED"
    HYPOTHESIS = "HYPOTHESIS"
    CONTRADICTED = "CONTRADICTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class AssertionConfidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class ResearchCase:
    """The root record of one research investigation. `trigger_source_type`/
    `trigger_source_id` are a generic, open reference (e.g. "radar"/a
    CandidateSignal id, or "daily_news"/a NewsStory id) — this module
    never imports the triggering system's own types, so the reference is
    an opaque, caller-supplied pair plus copied display fields
    (`trigger_source_name`, `trigger_summary`), never a live join.
    `version` supports later superseding records (a new `ResearchCase`
    row with a fresh `id`/`created_at`, never an in-place rewrite of this
    one) — see this module's own docstring on immutability."""

    id: str
    trigger_source_type: str
    trigger_source_id: str
    trigger_source_name: str
    trigger_summary: str
    title: str
    research_question: str
    status: ResearchCaseStatus
    created_at: str
    version: int


@dataclass(frozen=True)
class ResearchEvidenceItem:
    """One piece of source evidence attached to a case. Every provenance
    field is required and copied verbatim from the original source at
    the moment this item is added — never re-fetched, never mutated
    afterward. `source_type`/`source_id` use the same generic reference
    shape as `ResearchCase.trigger_source_type`/`trigger_source_id`,
    since one case's evidence can span multiple source systems.
    `excerpt_translated`/`translation_provider` mirror
    `src.models.models.Translation`'s shape without importing it."""

    id: str
    case_id: str
    source_type: str
    source_id: str
    source_url: str
    source_publisher_or_system: str
    source_date: str
    retrieved_at: str
    excerpt_original: str
    original_language: str
    added_at: str
    excerpt_translated: str | None = None
    translation_provider: str | None = None


@dataclass(frozen=True)
class RelationshipAssertion:
    """A directly-supported-or-hypothesized relationship between two
    named entities. `evidence_ids` must reference at least one
    `ResearchEvidenceItem.id` for the assertion to be meaningful — this
    module defines the shape only; the non-empty-evidence and
    hypothesis-requires-reasoning invariants are enforced by a pure
    validation function in a later, separately-approved step, not here.
    `subject_entity`/`object_entity` are plain display-name strings in
    this step, deliberately not a reference into the Issuer Registry
    (`src.models.issuer.Issuer`) — see this module's own docstring on
    why that coupling is deferred."""

    id: str
    case_id: str
    subject_entity: str
    object_entity: str
    role: RelationshipRole
    assertion_status: AssertionStatus
    evidence_ids: tuple[str, ...]
    confidence: AssertionConfidence
    created_at: str
    reasoning: str | None = None
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class DependencyAssertion:
    """A directly-supported-or-hypothesized bottleneck/constraint
    exposure for one entity. `supply_chain_layer` is stored as a plain
    string in this step (no import of or validation against
    `src.config.ontology.SUPPLY_CHAIN_LAYERS` — deferred to a later step
    per this phase's approval). `transmission_path` is an ordered tuple
    of entity-name strings describing a plausible propagation chain;
    `None` means no path has been proposed, distinct from an empty tuple.
    Same evidence/reasoning invariants as `RelationshipAssertion` — see
    its own docstring."""

    id: str
    case_id: str
    affected_entity: str
    bottleneck_type: BottleneckType
    supply_chain_layer: str | None
    transmission_path: tuple[str, ...] | None
    assertion_status: AssertionStatus
    evidence_ids: tuple[str, ...]
    confidence: AssertionConfidence
    created_at: str
    reasoning: str | None = None
    limitations: tuple[str, ...] = ()
