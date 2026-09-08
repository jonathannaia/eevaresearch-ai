"""EevaResearch — Evidence-First Themes MVP (design/DECISIONS.md).
Immutable public domain model types for the curated, cross-company
Themes product surface: a small number of testable research narratives
built from official-source evidence, distinct from both the internal
Research Case workflow objects (src.models.research_case) and the
legacy demo Theme/Subtheme ticker-browser models (src.models.models).

This module defines types only — no I/O, no persistence, no wall-clock
reads, no LLM/automatic-generation, no scoring. Every timestamp
(`created_at`, `updated_at`, evidence `date`) is a plain caller-supplied
`str`; nothing here ever calls `datetime.now()`.

Deliberately decoupled from every other model in this codebase: no
import of `src.models.models`, `src.models.research_case`,
`src.models.daily_news_models`, any source client, or any UI type. A
ResearchTheme is a wholly independent, manually curated record — it
never references a Research Case, a CandidateSignal, or any other
internal backend object, by id or otherwise. Standard-library imports
only. Every dataclass is frozen; no mutable default collections."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ThemeCategory(str, Enum):
    BOTTLENECK = "Bottleneck"
    DEMAND_SHIFT = "Demand shift"
    SECOND_ORDER_EFFECT = "Second-order effect"


class ThemeStatus(str, Enum):
    NEW = "New"
    MONITORING = "Monitoring"
    UPDATED = "Updated"
    RESOLVED = "Resolved"


class ThemeVisibility(str, Enum):
    """The one gate between curated content and the public UI —
    `src.data_access.backend_factory.ThemeRepositoryProtocol` (the only
    seam the web UI is allowed to use) exposes reads filtered to
    PUBLISHED only, server-side, never a broader read filtered in the
    UI layer. INTERNAL -> READY_TO_PUBLISH -> PUBLISHED -> ARCHIVED is
    the only intended transition order, though this module itself
    enforces no state machine — that discipline lives at the curator
    seam/script, never here."""

    INTERNAL = "internal"
    READY_TO_PUBLISH = "ready_to_publish"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class EvidenceDirection(str, Enum):
    SUPPORTS = "Supports"
    CONTRADICTS = "Contradicts"
    MIXED = "Mixed"
    # Phase A0 (design/DECISIONS.md) — the one direction a purely
    # rule-based automatic match is ever allowed to assert (see
    # src.logic.research_case_theme_matching.evaluate_theme_match).
    # SUPPORTS/CONTRADICTS/MIXED remain reserved for a human reviewer's
    # own judgment; a keyword/category match alone is never sufficient
    # basis to claim a filing supports or contradicts a thesis.
    CONTEXT = "Context"


class CompanyRole(str, Enum):
    DEMAND_DRIVER = "Demand driver"
    CONSTRAINT_OWNER = "Constraint owner"
    ENABLER = "Enabler"
    EXPOSED = "Exposed company"
    DISCONFIRMING = "Disconfirming force"


@dataclass(frozen=True)
class ResearchTheme:
    """The root record of one curated, public-facing research theme.
    Deliberately low-volume and manually authored — see
    scripts/create_theme.py, the only intended write path — never
    produced by an automatic pipeline, an LLM, or a Research Case
    publish step."""

    id: str
    category: ThemeCategory
    status: ThemeStatus
    visibility: ThemeVisibility
    title: str
    key_question: str
    hypothesis: str
    working_thesis: str
    why_it_matters: str
    what_could_change_the_view: str
    what_to_watch_next: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ThemeEvidenceItem:
    """One official-source evidence entry in a theme's evidence ledger.
    `source_url` must be a real, working official-source link — this
    module performs no validation of that itself (see the curator
    script and the UI's own safe-link rendering for the two places that
    actually enforce/check it)."""

    id: str
    theme_id: str
    date: str
    company: str
    source_name: str
    source_url: str
    fact: str
    relevance: str
    direction: EvidenceDirection


@dataclass(frozen=True)
class ThemeCompanyMapEntry:
    """One company's role within a theme's company map. `note` is a
    short, optional justification — never a rating, a price target, or
    a buy/sell label."""

    id: str
    theme_id: str
    company_name: str
    role: CompanyRole
    note: str | None = None


class HypothesisConfidence(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class ThemeNoteType(str, Enum):
    """A single, append-only research log covers three distinct kinds
    of internal curator entry — see ThemeResearchNote's own docstring
    for why these are unified into one record family rather than three
    separate models/tables."""

    HYPOTHESIS = "Hypothesis"
    DECISION = "Decision"
    WATCH_ITEM = "Watch item"


@dataclass(frozen=True)
class ThemeResearchNote:
    """One entry in a theme's internal research log — a hypothesis (with
    its own confidence and disconfirming condition), a curator decision,
    or a watch item. Insert-only and append-only, exactly like
    ThemeEvidenceItem/ThemeCompanyMapEntry: reassessing a hypothesis
    means authoring a NEW note, never editing an old one — the log is a
    chronological record of how the team's thinking evolved, not a
    mutable current-state snapshot. `confidence`/`disconfirming_condition`
    are only ever populated for note_type == HYPOTHESIS; both are None
    for DECISION/WATCH_ITEM notes. Never shown to any public user — see
    ThemeRepositoryProtocol.research_notes_for_theme's own docstring."""

    id: str
    theme_id: str
    note_type: ThemeNoteType
    content: str
    confidence: HypothesisConfidence | None
    disconfirming_condition: str | None
    created_at: str
