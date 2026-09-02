"""Typed data model for EevaResearch AI's foundation build.

Every model that carries a claim about the world (a Ticker's thesis, a
Signal's interpretation, a ResearchClaim, a ChatAnswer) is either a
ClaimType-tagged claim itself or backed by EvidenceItem(s) — the same
"every claim ties to something" principle the app is built around, applied
structurally rather than left to convention.

Phase 1 is demo-data-only: every EvidenceItem in this build has
is_demo=True, source_name="EevaResearch Demo Data", and no source_url. The
fields exist now (source_type, retrieved_at, freshness_label, ticker_symbol,
theme_slug) so a real evidence pipeline can populate them later without a
schema migration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class ClaimType(str, Enum):
    """The four categories EevaResearch distinguishes for every claim it
    surfaces — see Methodology page. Not optional decoration: every UI
    surface showing a claim must show which of these it is."""

    FACT = "Fact"
    INTERPRETATION = "Interpretation"
    INFERENCE = "Inference"
    UNCERTAINTY = "Uncertainty"


class Exposure(str, Enum):
    PRIMARY = "Primary"
    SECONDARY = "Secondary"


class Direction(str, Enum):
    IMPROVING = "Improving"
    WEAKENING = "Weakening"
    EMERGING = "Emerging"
    MIXED = "Mixed"


class Strength(str, Enum):
    WEAK = "Weak"
    MODERATE = "Moderate"
    STRONG = "Strong"


class Horizon(str, Enum):
    INTRADAY = "Intraday"
    SWING = "Swing"
    MULTI_WEEK = "Multi-week"
    MULTI_QUARTER = "Multi-quarter"


FRESHNESS_FRESH_DAYS = 3
FRESHNESS_AGING_DAYS = 10


@dataclass
class EvidenceItem:
    """One piece of evidence backing a claim. In this phase, always demo
    data: source_name is always "EevaResearch Demo Data", source_url is
    always None (no external link presented as real), is_demo is always
    True. freshness_label is derived from retrieved_at, not stored, so it's
    always correct relative to "now" rather than going stale itself."""

    id: str
    title: str
    source_name: str
    source_type: str  # e.g. "Demo Dataset" in phase 1
    published_at: str  # ISO 8601
    retrieved_at: str  # ISO 8601
    excerpt: str
    claim_type: ClaimType
    source_url: str | None = None
    is_demo: bool = True
    ticker_symbol: str | None = None
    theme_slug: str | None = None
    # Original-language text for non-English source excerpts, rendered
    # above the (English) `excerpt` translation per brief §7. None for
    # English-language sources. document_location is the in-document
    # pointer (e.g. "p.7") shown in the excerpt's <cite> line.
    excerpt_original: str | None = None
    document_location: str = ""

    @property
    def freshness_label(self) -> str:
        try:
            retrieved = datetime.fromisoformat(self.retrieved_at)
            if retrieved.tzinfo is None:
                retrieved = retrieved.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return "Unknown"
        age_days = (datetime.now(timezone.utc) - retrieved).total_seconds() / 86400
        if age_days <= FRESHNESS_FRESH_DAYS:
            return "Fresh"
        if age_days <= FRESHNESS_AGING_DAYS:
            return "Aging"
        return "Stale"


@dataclass
class ResearchClaim:
    text: str
    claim_type: ClaimType
    evidence: list[EvidenceItem] = field(default_factory=list)


@dataclass
class Subtheme:
    slug: str
    name: str
    description: str
    theme_slug: str


@dataclass
class Theme:
    slug: str
    name: str
    description: str
    subthemes: list[Subtheme] = field(default_factory=list)


@dataclass
class Ticker:
    """A ticker record. In phase 1 the only instance is the fictional DEMO
    company — theme pages otherwise show an empty state rather than any
    populated ticker table. is_demo=True on every record in this phase."""

    symbol: str
    company_name: str
    theme_slug: str
    subtheme_slug: str | None
    exposure: Exposure
    market_cap_bucket: str  # e.g. "Large", "Mid", "Small" — placeholder buckets
    liquidity_bucket: str  # e.g. "High", "Medium", "Low"
    technical_strength: str  # e.g. "Strong", "Neutral", "Weak" — placeholder, not computed
    risk_level: str  # e.g. "Low", "Medium", "High"
    thesis: str
    market_expectation: str = ""
    underappreciated: str = ""
    bull_factors: list[str] = field(default_factory=list)
    base_factors: list[str] = field(default_factory=list)
    bear_factors: list[str] = field(default_factory=list)
    key_risks: list[str] = field(default_factory=list)
    what_would_change_thesis: str = ""
    is_demo: bool = True


@dataclass
class Catalyst:
    id: str
    title: str
    date: str  # ISO 8601 date
    catalyst_type: str  # e.g. "Earnings", "Product Launch", "Regulatory", "Industry Event"
    description: str
    theme_slug: str
    ticker_symbol: str | None = None
    is_demo: bool = True


@dataclass
class Signal:
    id: str
    title: str
    theme_slug: str
    subtheme_slug: str | None
    direction: Direction
    strength: Strength
    horizon: Horizon
    evidence_count: int
    interpretation: str
    contrary_evidence: str
    validation_criteria: str
    invalidation_criteria: str
    related_tickers: list[str]
    last_updated: str  # ISO 8601
    is_demo: bool = True
    # Additive — real Radar promotion (src/logic/signal_promotion.py) only.
    # Empty/None for every demo signal (the seed file never sets these).
    # Never inferred or translated here: issuer/source_url/excerpt are
    # copied verbatim from the underlying FilingEvent/CandidateSignal;
    # excerpt is the original-language text only, same bounded-excerpt
    # rule as CandidateSignal.excerpt_original.
    issuer: str = ""
    source_name: str = ""
    source_url: str = ""
    excerpt: str | None = None
    # Bilingual display — additive, real Radar promotion only. `title`/
    # `excerpt` above stay exactly as-is (collapsed native-or-translated)
    # for existing callers (the drawer's dialog title bar, Fact-chip
    # gating); these six fields are the explicit bilingual pair a
    # card/drawer can render both halves from. title_native/
    # original_language are always populated for a real signal;
    # title_translated/excerpt_translated are None unless
    # CandidateSignal.translation_state was TRANSLATED — never guessed,
    # never locally translated. translation_state carries the exact
    # TranslationState enum value string verbatim (e.g. "Translation
    # pending"), no new vocabulary invented. exchange_symbol is set only
    # on an exact (source, stock_code) match against
    # src/config/tracked_companies.py's static registry — None otherwise,
    # never a guessed exchange or ticker.
    title_native: str = ""
    title_translated: str | None = None
    excerpt_translated: str | None = None
    original_language: str = ""
    translation_state: str = ""
    exchange_symbol: str | None = None


@dataclass
class CapitalRotationMetric:
    theme_slug: str
    relative_performance_pct: float  # vs. broad-market placeholder benchmark
    breadth_pct: float  # % of theme's tickers showing positive momentum, placeholder
    leaders: list[str]
    laggards: list[str]
    as_of: str  # ISO 8601
    is_demo: bool = True


@dataclass
class ChatMessage:
    role: str  # "user" | "assistant"
    content: str
    created_at: str


@dataclass
class ChatAnswer:
    question: str
    what_happened: str
    why_it_matters: str
    underappreciated: str
    risks: str
    what_to_watch: str
    sources: list[EvidenceItem]
    confidence: Strength
    freshness: str
    claim_type: ClaimType = ClaimType.INTERPRETATION
    is_demo: bool = True
    # Per-claim breakdown for the evidence-spine rendering (brief §7) — each
    # claim carries its own ClaimType, distinct from the single summary
    # claim_type above. Empty for answers that predate this (e.g. the
    # generic fallback), which render as plain text instead.
    claims: list[ResearchClaim] = field(default_factory=list)


@dataclass
class WatchlistEntry:
    list_name: str
    ticker_symbol: str
    added_at: str
    note: str = ""
    invalidates_if: str = ""


class CandidateStatus(str, Enum):
    """Radar Inbox processing lifecycle (Korea DART pilot, milestone 4
    orchestration) — one authoritative "where is this candidate right
    now" progression from first detection through document retrieval,
    extraction, and translation to human review. Detection confidence,
    extraction state, and translation state are tracked *separately* on
    CandidateSignal (see their own fields) — this status is the single
    summary value a Radar Inbox would primarily filter/display by, not a
    replacement for those more detailed sub-states."""

    NEW_FILING_EVENT = "New filing event"
    CANDIDATE_DETECTED = "Candidate detected"
    QUEUED_FOR_PROCESSING = "Queued for document processing"
    RETRIEVAL_IN_PROGRESS = "Document retrieval in progress"
    EXTRACTION_PENDING = "Extraction pending"
    EXTRACTED = "Extracted"
    TRANSLATION_PENDING = "Translation pending"
    TRANSLATED = "Translated"
    NEEDS_REVIEW = "Needs review"
    PROCESSING_DEFERRED = "Processing deferred"
    PARSE_FAILED = "Parse failed"
    RETRIEVAL_FAILED = "Retrieval failed"
    TRANSLATION_UNAVAILABLE = "Translation unavailable"
    PUBLISHED = "Published"
    DISMISSED = "Dismissed"
    NOT_MATERIAL = "Not material"
    # Human-reviewed, deliberately deferred — distinct from NEEDS_REVIEW
    # (not yet reviewed at all) and from PROCESSING_DEFERRED (a scan-time
    # processing-budget concept, unrelated to human review). Never
    # eligible for Signal promotion — see signal_promotion.py.
    MONITORING = "Monitoring"


class TranslationState(str, Enum):
    """Tracks the *excerpt's* translation specifically (title translation
    is attempted independently — see CandidateSignal.title_translation —
    and doesn't have its own state enum to keep this milestone's model
    additions bounded). Every failure mode (missing key, network error,
    rate limit, timeout, malformed response) collapses to UNAVAILABLE, per
    the explicit "retain source text and transition to Translation
    unavailable" behavior — there is no separate terminal "failed" state
    distinct from "unavailable", since the user-facing handling is
    identical either way."""

    NOT_REQUESTED = "Not requested"
    PENDING = "Translation pending"
    TRANSLATED = "Translated"
    UNAVAILABLE = "Translation unavailable"


class ExcerptQuality(str, Enum):
    """Descriptive metadata about the *shape* of an extracted excerpt —
    never a materiality signal. Grounded in the real document pattern
    verified in milestone 3 (numbered, table-heavy major-event reports),
    not a general-purpose text-quality classifier."""

    VERY_SHORT_OR_EMPTY = "Very short or empty"
    LIKELY_BOILERPLATE = "Likely boilerplate"
    TABLE_HEAVY = "Table-heavy"
    USABLE_TEXT = "Usable text"
    UNKNOWN = "Unknown"


@dataclass(frozen=True)
class StateTransition:
    """One entry in a CandidateSignal's audit trail. Transitions are
    appended, never overwritten or replaced — a failed attempt stays
    visible in the history even after a later retry succeeds."""

    status: CandidateStatus
    at: str  # ISO 8601
    detail: str = ""  # safe, human-readable — never a raw exception/stack trace or a secret


@dataclass
class FilingEvent:
    """A deduplicated primary-source disclosure — the radar pipeline's
    "filing event" tier (raw primary disclosure -> filing event). Always
    real data when it exists; there is no demo FilingEvent.

    Shared across both Korea DART and SEC EDGAR pilots via the existing
    `source_name` field (already present, already defaulting to
    "OpenDART / DART" — no new field was added for this). The identifier
    fields carry source-dependent meaning, documented here rather than
    renamed, to keep DART's existing field names/behavior completely
    unchanged (see design/DECISIONS.md, milestone 8):

        source_name == "OpenDART / DART":
            corp_code   = DART issuer identifier (internal 8-digit corp_code)
            stock_code  = public KRX stock code
            rcept_no    = DART receipt number (dedup key)

        source_name == "SEC EDGAR":
            corp_code   = normalized 10-digit SEC CIK (zero-padded string)
            stock_code  = ticker
            rcept_no    = canonical dashed SEC accession number, e.g.
                          "0000320193-25-000079" (dedup key; a no-dash
                          form is derived only at archive-URL-construction
                          time, never stored)

    Deduplicated by (source_name, corp_code, rcept_no) — never by title,
    ticker, name, date, or form type alone, since none of those are
    guaranteed unique or stable across a source's own filing history."""

    rcept_no: str
    corp_code: str
    corp_name: str
    stock_code: str
    report_nm: str  # DART: Korean disclosure title, verbatim. EDGAR: primary document description/title.
    rcept_dt: str  # ISO 8601 date
    flr_nm: str  # filer name, as returned by the source
    # DART's disclosure-list response does NOT echo back a type code per
    # row (pblntf_ty/pblntf_detail_ty are search *filters*, not response
    # fields — confirmed against a live pull, not assumed). Both stay
    # empty for DART filings pulled via an unfiltered scan.
    # EDGAR reuses this same slot for its own, real, per-filing type code:
    # pblntf_ty holds the SEC form type (e.g. "8-K", "10-Q", "SC 13D"),
    # which — unlike DART's pblntf_ty — genuinely is present per filing in
    # EDGAR's submissions data. pblntf_detail_ty stays unused for EDGAR
    # (8-K item-number detail lives on the resulting CandidateSignal's
    # matched_rules instead, same place DART puts its own rule-match
    # detail).
    #
    # EDINET (Gate 8.1) reuses BOTH slots for its own two real per-filing
    # codes, confirmed live (Gate 4/6/8): pblntf_ty holds EDINET's
    # `formCode` (its closest analog to "form type" — unchanged since
    # Gate 1), and pblntf_detail_ty — unused by DART/EDGAR — holds
    # EDINET's `docTypeCode`. EDINET's third real code, `ordinanceCode`
    # (the government ordinance/regulation a filing was made under, e.g.
    # "010" for the Financial Instruments and Exchange Act), has no
    # natural DART/EDGAR analog and no free slot to reuse — see
    # `ordinance_code` below.
    pblntf_ty: str = ""
    pblntf_detail_ty: str = ""
    theme_slug: str = ""
    subtheme_slug: str | None = None
    source_url: str = ""  # real DART viewer link built from rcept_no, or EDGAR filing-index URL
    retrieved_at: str = ""  # ISO 8601 — when EevaResearch fetched this
    source_name: str = "OpenDART / DART"
    original_language: str = "Korean"
    is_demo: bool = False
    # Additive, EDGAR-only (milestone 8) — SEC's own `primaryDocument`
    # filename for this filing, needed to fetch the actual document later
    # (EdgarClient.fetch_document requires cik + accession_no + filename).
    # Stays empty for DART, which has no equivalent concept (DART's
    # document.xml endpoint needs only rcept_no).
    primary_document: str = ""
    # Additive, EDINET-only (Gate 8.1) — see pblntf_ty/pblntf_detail_ty's
    # docstring above for why this is a new field rather than a reused
    # slot: EDINET's ordinanceCode has no DART/EDGAR analog. Stays empty
    # for DART/EDGAR.
    ordinance_code: str = ""
    # Evidence-packet foundation, Phase 1 (design/DECISIONS.md) — the
    # best-available FULL filed/published timestamp (date + time, and a
    # UTC offset only when the source itself supplied one), distinct from
    # `rcept_dt` above (which stays date-only — an existing UI date-parsing
    # helper already depends on that exact shape, so it is never changed).
    # None whenever the source's own currently-used endpoint doesn't
    # expose a time component, or the value it returned wasn't parseable —
    # never fabricated, never backfilled from rcept_dt. Populated today
    # only for EDINET (from its real submitDateTime field); EDGAR's and
    # DART's currently-used endpoints carry no time component at all, so
    # this stays None for both without a demonstrated serialization defect.
    filed_at: str | None = None


@dataclass
class Translation:
    """A machine translation of one bounded excerpt or title — never
    itself a Fact, and never substituted for the Korean original. Cached
    by (document id, excerpt hash) so the same text is never re-sent to
    the translation provider twice."""

    translated_text: str
    provider: str  # e.g. "DeepL"
    source_lang: str  # "ko"
    target_lang: str  # "en"
    translated_at: str  # ISO 8601
    model: str | None = None


class ExtractionState(str, Enum):
    """Where a CandidateSignal's document-evidence extraction stands —
    always shown explicitly rather than left implicit in whether
    `excerpt_original` happens to be populated (milestone 3, Korea DART
    pilot document retrieval)."""

    NOT_FETCHED = "Not fetched"
    PENDING = "Document available; extraction pending"
    EXTRACTED = "Extracted"
    UNSUPPORTED_FORMAT = "Unsupported format"
    PARSE_FAILED = "Parse failed"
    RETRIEVAL_FAILED = "Retrieval failed"


@dataclass(frozen=True)
class FlagReason:
    """A normalized, source-neutral explanation of why Radar flagged a
    candidate — additive alongside `CandidateSignal.matched_rules`, never
    replacing it or losing any per-source rationale detail (EDGAR's own
    typed `SignalDecision` reason/rule_ids, DART's `materiality_assessment`,
    EDINET's code-triplet detail all still exist unchanged; this is a
    normalized *view* built from them, not a competing source of truth).

    This explains why the pipeline selected an item for review — never a
    market/investment thesis, and never a speculative causal claim about
    what the flagged item means. `category`/`matched_terms`/`score_inputs`
    are drawn only from data the pipeline already produces (matched_rules,
    confidence); nothing here is inferred or generated."""

    category: str  # the matched rule/category, e.g. "financing_or_debt", "ownership_change"
    matched_terms: tuple[str, ...] = ()  # the raw matched_rules entries this reason summarizes, verbatim
    score_inputs: tuple[str, ...] = ()  # descriptive detection inputs available today, e.g. "confidence=High" — never a new score
    human_readable_reason: str = ""  # a plain-language sentence describing what matched
    source_detail: str = ""  # source-specific raw detail (EDGAR's shadow-policy reason, EDINET's code triplet, etc.) — preserved verbatim


def build_flag_reason(matched_rules: list[str] | tuple[str, ...], confidence: str, source_detail: str = "") -> FlagReason:
    """The one shared, source-neutral way to build a FlagReason from the
    matched_rules/confidence every source already produces — used by all
    three sources' scan_service.py so the resulting shape is identical
    regardless of which pipeline built it. Never parses source-specific
    meaning out of a matched_rules string beyond the category prefix
    every source already writes before its first ':' — DART's keyword
    lexicon, EDGAR's form/item categories, and EDINET's code-triplet
    categories all share that one convention already."""
    rules = tuple(matched_rules)
    category = rules[0].split(":", 1)[0] if rules else ""
    distinct_categories = len({r.split(":", 1)[0] for r in rules})
    human_readable_reason = (
        f"Matched {len(rules)} detection rule(s) in category '{category}'." if rules
        else "No detection rule matched."
    )
    return FlagReason(
        category=category,
        matched_terms=rules,
        score_inputs=(f"confidence={confidence}", f"category_matches={distinct_categories}"),
        human_readable_reason=human_readable_reason,
        source_detail=source_detail,
    )


class LocationKind(str, Enum):
    """Where, if anywhere, an evidence excerpt's source-aware location
    contract points. UNAVAILABLE is a fully valid, honest value — never
    fabricated — and is the only kind ever produced for metadata-only
    events (no document ever fetched/parsed) or for a source/format that
    doesn't expose finer-grained location today."""

    UNAVAILABLE = "Unavailable"
    PAGE = "Page"  # a real page number — meaningful for PDF-sourced documents only
    SECTION = "Section"  # a section heading or element/item identifier (e.g. "Item 2.03")
    TABLE = "Table"  # a table label
    PARAGRAPH = "Paragraph"  # a paragraph index


@dataclass(frozen=True)
class EvidenceLocation:
    """Source-aware pointer to where within a document an excerpt was
    found. Never claims a page number for an HTML/XML/XBRL or
    metadata-only event — HTML has no true pages, so PAGE is only ever
    set from a real PDF page-extraction loop. Every field beyond `kind`
    is optional and only set when the pipeline already produced that
    exact value while building the excerpt — this contract never triggers
    a new document fetch, parser, or heuristic to populate itself."""

    kind: LocationKind = LocationKind.UNAVAILABLE
    page: int | None = None
    section: str | None = None
    table: str | None = None
    paragraph_index: int | None = None


def record_excerpt(
    candidate: "CandidateSignal",
    excerpt_text: str | None,
    retrieved_at: str,
    evidence_source_member: str | None = None,
) -> bool:
    """The one safe way any pipeline attaches newly extracted excerpt
    text to a CandidateSignal — evidence-packet-foundation Phase 1's
    integrity fix (design/DECISIONS.md). `excerpt_original` is set once,
    on the first successful extraction, and is never overwritten
    afterward. A later extraction/reprocessing attempt that produces
    *different* text is preserved in `excerpt_supplemental` instead of
    silently replacing the original — the first/source excerpt and its
    provenance (`excerpt_retrieved_at`) are never lost. A repeat
    extraction that reproduces the exact same text is a no-op either way.

    `evidence_source_member` (Phase 2, Step 2) is additive and optional,
    defaulting to None — every existing call site that omits it (DART,
    EDGAR, and EDINET's own non-ZIP bare-PDF/HTML/text extraction) is
    completely unaffected and this field stays None for them, exactly as
    before. When supplied (EDINET's bounded ZIP-member extraction only),
    it is recorded ATOMICALLY with `excerpt_original`, at the exact same
    first-write moment and never independently — there is no separate
    "supplemental" slot for it, since it is a single provenance fact
    about the persisted `excerpt_original`, not content that can
    meaningfully have its own later revision. If `excerpt_original` was
    already set by a prior extraction, this call is a no-op with respect
    to both fields (a later/different extraction's member path is simply
    never recorded), which guarantees `evidence_source_member`, whenever
    populated, always names the true origin of the excerpt actually
    persisted — never a different extraction's member.

    Returns True only when this call performed the first-ever assignment
    of `excerpt_original` (useful for a caller that wants to know whether
    this was the candidate's first successful extraction); False for a
    no-op, an unchanged-text repeat, or a supplemental recording.
    Does nothing at all if `excerpt_text` is None (a failed/unsupported
    extraction has no text to record)."""
    if excerpt_text is None:
        return False
    if candidate.excerpt_original is None:
        candidate.excerpt_original = excerpt_text
        candidate.excerpt_retrieved_at = retrieved_at
        if evidence_source_member is not None:
            candidate.evidence_source_member = evidence_source_member
        return True
    if excerpt_text != candidate.excerpt_original:
        candidate.excerpt_supplemental = excerpt_text
    return False


@dataclass
class CandidateSignal:
    """The radar pipeline's "candidate signal" tier: a transparent,
    rule-based flag on top of a FilingEvent explaining *why* it might
    matter — never a market interpretation or a confident conclusion on
    its own. `matched_rules` names every rule that fired (see
    src/data_access/dart/dart_rules.py); an empty list means this stays a
    plain FilingEvent and should not be surfaced as a candidate at all.
    Promotion to the curated Signal Board (the Signal model above) is a
    separate, human review step tracked via `status`, not automatic.

    `confidence` here is a pilot-stage *detection* confidence only — how
    confidently the keyword rules classified the filing's title, not a
    judgment about business materiality. It should not be read as
    "High confidence this matters"; a later milestone that adds real
    document context is expected to separate detection confidence,
    evidence completeness, and materiality into distinct fields rather
    than overload this one value further."""

    id: str
    filing: FilingEvent
    matched_rules: list[str]
    confidence: str  # "Low" / "Moderate" / "High" — keyword-match-count only; see class docstring
    status: CandidateStatus
    extraction_state: ExtractionState = ExtractionState.NOT_FETCHED
    translation_state: TranslationState = TranslationState.NOT_REQUESTED
    excerpt_quality: ExcerptQuality = ExcerptQuality.UNKNOWN
    # None means document parsing hasn't succeeded (or hasn't run) yet —
    # the UI shows extraction_state's PENDING message in that case rather
    # than an empty-looking field. Bounded to a short excerpt, never a
    # full-document summary.
    excerpt_original: str | None = None
    # Machine translations of the *title* and the *excerpt* are tracked
    # separately since they're different source texts with independent
    # cache keys/failure states — either can succeed while the other
    # fails or hasn't run yet.
    title_translation: Translation | None = None
    excerpt_translation: Translation | None = None
    reviewed_at: str | None = None
    reviewed_note: str = ""
    # Audit trail — see StateTransition. Every status change during
    # orchestration appends here rather than silently overwriting.
    state_history: list[StateTransition] = field(default_factory=list)
    # Radar Calibration milestone: a narrow, deliberately non-numeric
    # note from the ownership-change materiality gate
    # (src/data_access/dart/ownership_materiality.py) only — never a
    # general materiality score, and never set for any non-ownership
    # candidate (stays "Not assessed" for earnings/capex/financing/
    # rumor-response/etc). Distinct from `confidence`, which measures
    # keyword-detection confidence, not materiality.
    materiality_assessment: str = "Not assessed"
    # Evidence-packet foundation, Phase 1 (design/DECISIONS.md). Set only
    # via record_excerpt() above — never assigned directly by a pipeline —
    # so the "set once, never overwritten" guarantee actually holds.
    # excerpt_supplemental holds a later extraction's text only when it
    # differs from excerpt_original; excerpt_retrieved_at is the timestamp
    # of that first, immutable extraction (distinct from
    # FilingEvent.retrieved_at, which is scan-time filing discovery, not
    # document-extraction time).
    excerpt_supplemental: str | None = None
    excerpt_retrieved_at: str | None = None
    # Normalized, source-neutral "why flagged" record — additive alongside
    # matched_rules above (never replacing it, never losing any per-source
    # rationale). See FlagReason's own docstring.
    flag_reason: FlagReason | None = None
    # Source-aware evidence-location contract — see EvidenceLocation's own
    # docstring. None/UNAVAILABLE is a valid, honest default; Phase 1 only
    # ever sets a more specific kind from data a pipeline already produced
    # while building the excerpt, never from a new fetch/parser/heuristic
    # added solely to populate this field.
    evidence_location: EvidenceLocation | None = None
    # Evidence-packet foundation, Phase 2, Step 2 (design/DECISIONS.md).
    # A descriptive, safe archive-relative member path/name — e.g.
    # "PublicDoc/0101.pdf" — recording which member inside an official
    # EDINET ZIP package the persisted excerpt_original came from. Never
    # a URL, never independently fetchable/clickable/resolvable; purely a
    # provenance label about container structure, distinct from
    # `evidence_location` (which describes a position *within* a
    # document's own content, e.g. a section or page). Set only via
    # record_excerpt() above, atomically with excerpt_original's own
    # first write — never assigned directly by a pipeline. Currently
    # populated only by EDINET's bounded ZIP-member extraction path;
    # stays None for EDGAR, DART, EDINET's own bare-PDF/HTML/text
    # extraction, and every pre-Phase-2-Step-2 record.
    evidence_source_member: str | None = None
    # Translation reliability (Radar simplicity + translation reliability
    # workstream) — persisted separately from `translation_state` so a
    # failure's cause and retry schedule survive a process restart, not
    # just the coarse UNAVAILABLE bucket. `translation_failure_category`
    # is one of translation_service.py's own category strings (e.g.
    # "rate_limit", "timeout", "network", "provider_error" — retryable;
    # "config_missing_key", "parse_error" — terminal); None whenever
    # translation_state is not UNAVAILABLE. `translation_next_retry_at`
    # is set only while a bounded, backed-off automatic retry is actually
    # still scheduled (see translation_service.record_translation_attempt)
    # — None means no further retry will happen (either the failure was
    # terminal, or the retry cap was reached), which is exactly the signal
    # the public card uses to distinguish "being prepared" from a silent,
    # honest fallback to the original text.
    translation_failure_category: str | None = None
    translation_failure_reason: str | None = None
    translation_failure_at: str | None = None
    translation_retry_count: int = 0
    translation_next_retry_at: str | None = None
