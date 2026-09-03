"""Source-agnostic Issuer model — Phase A of the autonomous-radar registry
foundation (design/ISSUER_REGISTRY_FOUNDATION.md).

Deliberately separate from `src.config.tracked_companies.TrackedCompany`,
not a replacement for it in this phase: a `TrackedCompany` is a per-source
scan configuration record ("which real company do we scan, from which
source"); an `Issuer` is the company's own identity, independent of any one
source. A single issuer may eventually carry identifiers from several
sources at once (EDGAR CIK *and* an IR-adapter domain, say) — `TrackedCompany`
has no way to express that; `identifiers` here does, via an open
{source_name: native_id} mapping rather than a fixed set of fields, so a
future source needs no schema change to plug in.

Every field beyond the required core is optional/metadata-only in this
phase — nothing here is fetched, resolved, or verified live. See
`src/config/issuer_registry.py` for the two populated collections
(`SEED_ISSUERS`, migrated losslessly from the existing `TrackedCompany`
tuple, and `DISCOVERY_STUBS`, unverified portfolio-map candidates that are
structurally excluded from every existing scan path)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CoverageState(str, Enum):
    """How EevaResearch tracks this issuer today — distinct from
    `LifecycleState`, which describes the issuer's own business state.
    SEED: part of the curated, actively-scanned universe (today, that
    means it round-trips through the existing TrackedCompany-based
    pipelines via the compatibility adapter). DISCOVERED: surfaced as a
    candidate for coverage (at Phase A, only via the static portfolio-
    map stubs; from the Company Discovery Phase 2 workstream onward,
    also the live Candidate Ledger's own tier — see
    src.data_access.company_discovery) but never yet reviewed/promoted
    — must never reach an existing scanner or any live-monitoring
    configuration. REJECTED: considered and explicitly excluded —
    invalid, duplicate, non-corporate, or not relevant (no entries at
    Phase A; populated by Company Discovery Phase 2's deterministic
    reject rules).

    Company Discovery Phase 2 additions (both purely additive, no
    existing member's value or meaning changed):
    ARCHIVED: a Candidate whose evidence has gone stale (no new evidence
    within the configured staleness window) — retained, never deleted,
    never scored, never promotable without new evidence. QUARANTINED: an
    ambiguous identity match — ARCHIVED and QUARANTINED are both terminal
    with respect to scoring (a candidate in either state is never scored
    and never a promotion input) but not deletion; see
    src.data_access.company_discovery.entity_resolution for the exact
    classification rules that produce each."""

    SEED = "Seed"
    DISCOVERED = "Discovered"
    REJECTED = "Rejected"
    ARCHIVED = "Archived"
    QUARANTINED = "Quarantined"


class LifecycleState(str, Enum):
    """The issuer's own real-world business state — orthogonal to
    CoverageState. ACTIVE is the default for every issuer in this phase
    (including discovery stubs, whose business-activity status is assumed
    rather than verified — see each stub's own `normalization_status`)."""

    ACTIVE = "Active"
    MONITORING = "Monitoring"
    DELISTED = "Delisted"
    MERGED = "Merged"


@dataclass(frozen=True)
class Issuer:
    """A source-agnostic issuer identity record. Frozen and tuple-based
    for list-like fields, matching `TrackedCompany`'s existing convention.
    `identifiers` is the one mutable-typed field (a plain dict) — see its
    own docstring below for why a tuple-of-pairs wasn't used instead;
    callers must treat it as read-only by convention, the same trust-
    boundary the rest of this codebase already extends to internal data."""

    issuer_id: str  # stable, source-independent — see issuer_registry.py for the ID scheme
    legal_name: str
    country_or_jurisdiction: str
    coverage_state: CoverageState
    lifecycle_state: LifecycleState = LifecycleState.ACTIVE
    native_name: str = ""
    aliases: tuple[str, ...] = ()
    primary_ticker: str | None = None
    primary_exchange: str | None = None
    # {source_name: native_identifier}, e.g. {"SEC EDGAR": "0000002488"},
    # {"OpenDART / DART": "00164742"}, {"EDINET": "E02778"}. Deliberately
    # an open mapping, not a fixed set of named fields (cik/corp_code/...)
    # — a future source (an IR adapter, a national-registry adapter) adds
    # an entry here without any model change. Empty for an issuer whose
    # identifier hasn't been resolved/confirmed for a given source yet —
    # never a guessed value, same discipline `TrackedCompany.corp_code`
    # already follows.
    identifiers: dict[str, str] = field(default_factory=dict)
    ir_domain: str | None = None  # descriptive only in this phase — never fetched
    themes: tuple[str, ...] = ()  # validated against src.config.ontology.PRIMARY_THEMES where set
    subthemes: tuple[str, ...] = ()
    supply_chain_layers: tuple[str, ...] = ()  # validated against src.config.ontology.SUPPLY_CHAIN_LAYERS where set
    evidence_confidence: str = "Not assessed"
    discovered_via: str = ""
    discovered_at: str | None = None  # ISO 8601 date, optional metadata only
    last_verified_at: str | None = None  # ISO 8601 date, optional metadata only
    # Phase-A-specific status text — required (non-empty) for any issuer
    # whose identity/identifiers are not yet independently verified;
    # empty is only valid for issuers carried forward from the existing,
    # already-verified TrackedCompany registry. See issuer_registry.py.
    normalization_status: str = ""
    notes: str = ""
    # Company Discovery Phase 2 additions — both purely additive, default
    # values chosen so every existing SEED_ISSUERS/DISCOVERY_STUBS
    # construction call (which never passes either) remains correct
    # unchanged: every one of those is a real, independently-known
    # corporate issuer with no parent-subsidiary relationship recorded.
    parent_issuer_id: str | None = None  # another Issuer's issuer_id, when this issuer is a known subsidiary
    entity_kind: str = "corporate"  # "corporate" | "subsidiary" | "fund" | "agency" | "government" | "unknown"
