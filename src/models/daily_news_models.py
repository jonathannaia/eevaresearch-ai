"""Daily News Slice 1 — an independent product surface from Radar Inbox.
Deliberately its own module with zero imports from src.models.models: no
CandidateSignal, FilingEvent, CandidateStatus, or StateTransition is
reused here, even though NewsStory's own state_history is structurally
similar to CandidateSignal's — the two systems must stay decoupled at
the type level, not just the storage level (see design/DECISIONS.md and
the Radar-vs-Daily-News product clarification it records).

Every NewsStory that reaches PUBLISHED has a non-empty, validated,
canonical `sources[i].url` — see
src/data_access/daily_news/canonical_url.py — and an `eeva_summary` that
is either an extractive excerpt of a permitted feed description, the
fixed fallback sentence, or None (original-language-preserved, no
translation available). Never fabricated, never a full article
reproduction (see summary_grounding.py's own docstring for exactly what
"grounded" means this slice).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SourceClass(str, Enum):
    """Only OFFICIAL_COMPANY is ever produced in Slice 1 — the other
    three values exist so the type doesn't need to change shape when a
    later, separately-approved slice adds regulatory filings or
    third-party sources."""

    REGULATORY_FILING = "Regulatory filing"
    OFFICIAL_COMPANY = "Official company source"
    PRESS_RELEASE_WIRE = "Press-release wire"
    INDEPENDENT_JOURNALISM = "Independent journalism"


class NewsStoryStatus(str, Enum):
    DISCOVERED = "Discovered"
    SUMMARIZED = "Summarized"
    PUBLISHED = "Published"
    SUPPRESSED = "Suppressed"  # no valid canonical URL — never shown, never partially rendered


class NewsMaterialityTier(str, Enum):
    """Signals materiality classification (product-naming separation +
    deterministic classification, design/DECISIONS.md) — a Daily-News-
    domain type, deliberately never SignalTier/Signal or any Radar-owned
    type (see this module's own docstring on why Daily News stays
    decoupled from Radar's domain types at the type level). Computed
    once, deterministically, by src/data_access/daily_news/
    materiality_classification.py at story-construction time; never an
    LLM score, never user-editable.

    HIGH_SIGNAL: material and evidence-backed — the default Signals feed.
    WATCHLIST: relevant but early, unquantified, or insufficiently
      material — retained, shown in a secondary section.
    BACKGROUND: real but not currently decision-relevant — retained for
      search/audit, excluded from the default feed.

    None (the field's own default on NewsStory/EditorialStory, not a
    member of this enum) means "not yet classified" — every record
    persisted before this field existed. Never reclassified automatically
    (see materiality_classification.py's own module docstring); such a
    record must still render safely wherever a tier is displayed."""

    HIGH_SIGNAL = "High Signal"
    WATCHLIST = "Watchlist"
    BACKGROUND = "Background"


@dataclass(frozen=True)
class NewsSourceReference:
    publisher: str
    source_class: SourceClass
    url: str  # real, direct, canonical — validated before a story is ever PUBLISHED
    title: str  # publisher's own headline, verbatim
    published_at: str  # ISO 8601, publisher-claimed
    retrieved_at: str  # ISO 8601, when EevaResearch fetched it
    original_language: str
    excerpt_original: str | None = None  # bounded, from the feed's own description/summary field only
    # Both optional, source-provided-only (never fetched from a linked
    # article page, never generated) — see
    # src/data_access/daily_news/rss_atom_client.py's extraction and
    # canonical_url.validate_image_url()'s separate, per-source
    # exact-hostname gate applied before either is ever populated here.
    image_url: str | None = None
    image_alt: str | None = None  # source alt text if present, else the item's own title — accessibility text, never a factual caption
    # Daily News worker observability, Part A (design/DECISIONS.md): the
    # UTC time at which EevaResearch first persisted this deterministic
    # story ID during Daily News discovery. Distinct from published_at
    # (the source's own claimed publication time), retrieved_at (the
    # timestamp of THIS particular fetch — identical to
    # first_discovered_at only on the run that first discovered the
    # story), and the underlying real-world event time (never captured
    # anywhere in this app). Set once, at construction, in
    # daily_news_pipeline.run_discovery()'s new-item branch only —
    # never touched again: an already-known story_id short-circuits
    # before a new NewsSourceReference is ever constructed for it (see
    # run_discovery()'s own `if story_id in store: ... continue`), so
    # this field can only ever be written once per story, by
    # construction of the pipeline's own control flow, not by a
    # separate runtime guard. None for every pre-migration/pre-this-
    # field record — never backfilled.
    first_discovered_at: str | None = None


@dataclass
class NewsStateTransition:
    """Own type, not src.models.models.StateTransition — see module
    docstring. Same append-only audit-trail shape."""

    status: NewsStoryStatus
    at: str  # ISO 8601
    detail: str = ""  # safe, human-readable — never a raw exception or secret


@dataclass
class NewsStory:
    id: str
    company_name: str  # matches a real TrackedCompany.name from tracked_companies.py
    ticker: str | None  # TrackedCompany.krx_code, reused for identity only, never re-derived
    theme_slug: str
    headline: str  # the source's own title, used as-is — Slice 1 does not rewrite headlines
    eeva_summary: str | None  # extractive excerpt, fallback sentence, or None — see summary_grounding.py
    is_fallback_summary: bool
    translation_unavailable: bool
    original_title: str | None  # populated only when translation_unavailable is True
    sources: tuple[NewsSourceReference, ...]
    status: NewsStoryStatus
    state_history: list[NewsStateTransition] = field(default_factory=list)
    # Materiality classification (design/DECISIONS.md) — additive, safe-
    # default fields. None for every record persisted before this field
    # existed (never backfilled automatically — see
    # materiality_classification.py's own docstring); set once, at
    # construction, for every story discovered from this point forward.
    materiality_tier: NewsMaterialityTier | None = None
    materiality_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class EditorialStory:
    """Editorial Daily News v1 (design/DECISIONS.md) — a parallel,
    additive model for issuer-agnostic editorial feed items (CNBC,
    Korea Herald). Deliberately NOT a NewsStory: NewsStory's own
    `company_name: str` is a single required field (exactly one issuer,
    always present) — editorial coverage genuinely supports zero, one,
    or many matched companies and one-or-many matched themes, a
    different shape this project's own established "never force an
    existing type to loosely fit a new shape" discipline says deserves
    its own type rather than a NewsStory with invented/optional fields.
    Zero change to NewsStory/NewsSourceReference/NewsStoryStatus/
    SourceClass above.

    Every instance already passed src.data_access.daily_news.
    editorial_matching's fail-closed gate before construction — at least
    one of matched_companies/matched_themes is always non-empty; there is
    no "unmatched" or "suppressed" EditorialStory, mirroring this
    project's established "don't construct what can't be shown" pattern
    (see e.g. the Federal Register Policy Monitor Pilot)."""

    id: str  # deterministic, from (canonical source_url) or (normalized headline, publisher) — see editorial_pipeline.py
    headline: str  # the source's own title, verbatim — never rewritten
    publisher: str  # e.g. "CNBC", "The Korea Herald"
    source_url: str  # canonical https link, validated before construction
    published_at: str  # ISO 8601, source-claimed
    retrieved_at: str  # ISO 8601, when EevaResearch fetched it
    excerpt: str | None  # bounded extractive excerpt from the feed's own description field only; None means omit entirely — never a fallback sentence, never invented
    matched_companies: tuple[str, ...]  # zero-to-many real TrackedCompany.name values
    matched_themes: tuple[str, ...]  # one-to-many theme slugs
    source_feed_id: str  # the DailyNewsSourceEntry.source_id this item came from — for per-source cap/failure-isolation bookkeeping
    # Materiality classification (design/DECISIONS.md) — same additive,
    # safe-default fields as NewsStory above; see that field's own
    # comment for the "None means not yet classified, never backfilled"
    # contract.
    materiality_tier: NewsMaterialityTier | None = None
    materiality_reasons: tuple[str, ...] = ()
