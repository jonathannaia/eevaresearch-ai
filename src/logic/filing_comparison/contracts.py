"""Typed input/output contract for one LLM-assisted filing comparison —
pure dataclasses and enums, no I/O, no Streamlit, no provider import.

What this is for: helping a reader understand what changed between two
comparable disclosures, with the evidence, the qualifications and the
uncertainty preserved rather than smoothed away.

Reuse, not reinvention. This deliberately does NOT introduce a second
agent framework. It borrows the vocabularies and the validation
discipline this codebase already has:

  * `SourceTier` and the "every violation reported, never raised"
    validation style from src/mcp_agent/contracts.py;
  * `ExtractionState`, `ExcerptQuality` and `EvidenceLocation` from
    src/models/models.py as the real completeness/provenance vocabulary;
  * `ComparisonStatus` from src/logic/prior_disclosure_comparison.py as
    the deterministic comparability fact.

--- What ComparisonStatus actually establishes (measured, not assumed) ---

`prior_disclosure_comparison.compare_matched_rules` is, in its own
docstring, "Pure set-difference over normalized detection-rule
categories only — never the raw matched_rules strings, never excerpt
text, never a translation, never a timestamp." Status is then assigned
as `CHANGE_DETECTED if (diff.added or diff.removed) else
NO_MATERIAL_CHANGE`. Therefore, precisely:

  * `NO_MATERIAL_CHANGE` means the two filings' detection-rule category
    SETS are identical. It is not a statement about document content,
    figures, parties or dates, and it is emphatically not a finding that
    the filings say the same thing.
  * `CHANGE_DETECTED` means those sets differ. It is not a finding that
    a materially important business change occurred.
  * `NOT_COMPARABLE` / `NOT_AVAILABLE` are the only values this module
    treats as dispositive, and only in the refusing direction.

That module also records its own limitations, which this one inherits
rather than papers over: `_PERIOD_LIMITATION` ("Comparable reporting
period is not available in current metadata.") means the two sides are
not known to cover comparable periods.

Consequence: comparability status is an input gate, never a claim. No
status value licenses any sentence about what the documents say, and in
particular no status value establishes that one filing REVISES another.

--- Why a relationship must be evidenced, not inferred ---

`ComparisonRelationship` is the second gate, and it exists because the
sharpest observed failure mode is narrating two unrelated disclosures
as one revised figure. Two filings can share an issuer, a filing
family, a rule category and an ordered pair of dates and still announce
two entirely separate events — the local SK hynix facility-investment
pair does exactly that. So none of those facts, alone or together,
establishes a revision here: `EVIDENCED_REVISION` requires a
`RelationshipEvidence` carrying a verbatim span from one of the two
excerpts, and that span may not be merely the issuer name, the form
type, a date or a rule category.

Without that evidence the comparison is `SIDE_BY_SIDE`: differences
between the documents may be described, but nothing may be called an
increase, a decrease, a revision or a restatement, and
`evidenced_changes` must be empty. `UNKNOWN_OR_INCOMPATIBLE` is refused
before the provider is called.

This is deliberately NOT a general event-resolution engine. It resolves
nothing, mints no project or event identifiers, and infers no linkage.
It only refuses to speak in revision language that its input has not
evidenced.

--- Why completeness is never inferred from length ---

Both extractors cap excerpts (`MAX_EXCERPT_CHARS = 600`), and
`ExtractionResult` carries no truncation field. An excerpt at the cap
is therefore *probably* truncated, and one under it may still be
partial for unrelated reasons. Length is not evidence of completeness
in either direction, so this contract carries `ExcerptCompleteness` as
its own declared field, sourced from the pipeline's own extraction
state rather than measured from the text. `UNKNOWN` is a first-class
value and the safe default, and it is never read as COMPLETE.

--- Why there is no "nothing changed" outcome ---

There is deliberately no outcome meaning "this filing is unchanged".
The strongest absence statement representable here is
`NO_DIFFERENCE_IN_COMPARED_FIELDS`, and it is licensed by explicit
per-side `FieldEvidence` — field identity, extracted value, the exact
supporting span, and any qualifications — not by any property of the
excerpt as a whole. An earlier version of this module authorized that
outcome when a field name sat more than 80 characters from the end of
the excerpt. That heuristic is gone: distance from the end is not
evidence that a value is whole.

Every claim this component can emit is excerpt-scoped by construction:
`ClaimScope` has exactly one value, so a whole-filing claim is not
representable in this contract.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from src.logic.prior_disclosure_comparison import ComparisonStatus
from src.mcp_agent.contracts import SourceTier
from src.models.models import EvidenceLocation, ExcerptQuality, ExtractionState

CONTRACT_VERSION = "filing_comparison/contract/v3"

# Bounded output. Every cap is a product decision, not a model setting:
# a longer statement is a refusal, never a truncation, so nothing is ever
# silently cut mid-claim.
MAX_SOURCE_STATED_FACTS = 8
MAX_EVIDENCED_CHANGES = 8
MAX_QUALIFICATIONS = 8
MAX_UNCERTAINTIES = 8
MAX_COMPARED_FIELDS = 12
STATEMENT_MAX_CHARS = 400
INTERPRETATION_MAX_CHARS = 600
SUMMARY_MAX_CHARS = 300

# A relationship span must be substantive. Below this it cannot be more
# than a label, a date or a name.
MIN_RELATIONSHIP_SPAN_CHARS = 12


class ClaimScope(str, Enum):
    """The scope of every statement this component can make.

    One value, on purpose. A filing-level or issuer-level claim is not
    representable, so no code path can emit one and no reviewer has to
    take its absence on trust."""

    EXCERPT = "excerpt"


class ComparisonRelationship(str, Enum):
    """How the two documents stand to each other.

    The distinction that keeps unrelated disclosures from being narrated
    as revisions of one another. See the module docstring for why a
    shared issuer, family, rule category or date order is not enough."""

    # One document revises, amends, restates or follows up the other on
    # the same subject, and source text says so.
    EVIDENCED_REVISION = "evidenced_revision"
    # Two disclosures presented together with no established revision
    # relationship. Differences may be described; nothing may be called
    # an increase, a decrease or a revision.
    SIDE_BY_SIDE = "side_by_side"
    # Not established, or established as incompatible. Refused.
    UNKNOWN_OR_INCOMPATIBLE = "unknown_or_incompatible"


class ExcerptCompleteness(str, Enum):
    """Whether the excerpt in hand is the whole of what was extracted.

    Declared by the caller from the pipeline's own state — never
    measured from the text. Note the bound: COMPLETE describes the
    extracted excerpt, not the source filing."""

    COMPLETE = "complete"
    TRUNCATED = "truncated"
    UNKNOWN = "unknown"


# The only state that can support a COMPLETE or TRUNCATED declaration at
# all; anything else means the pipeline never produced usable text, so
# completeness is not merely unknown but moot.
_USABLE_EXTRACTION_STATES: frozenset[ExtractionState] = frozenset({ExtractionState.EXTRACTED})

# Excerpt shapes that cannot support a factual comparison even when the
# extraction itself succeeded.
_UNUSABLE_EXCERPT_QUALITIES: frozenset[ExcerptQuality] = frozenset({
    ExcerptQuality.VERY_SHORT_OR_EMPTY, ExcerptQuality.LIKELY_BOILERPLATE,
})


class ComparisonOutcome(str, Enum):
    """What the component concluded. Every value except COMPARISON and
    NO_DIFFERENCE_IN_COMPARED_FIELDS is a refusal that names its
    reason."""

    COMPARISON = "comparison"
    # Scoped absence: no difference in the fields this result NAMES, each
    # backed by per-side field evidence. Never "the filing did not
    # change" — see the module docstring.
    NO_DIFFERENCE_IN_COMPARED_FIELDS = "no_difference_in_compared_fields"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NOT_COMPARABLE = "not_comparable"
    NO_PRIOR_FILING = "no_prior_filing"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    OUTPUT_REJECTED = "output_rejected"
    BUDGET_EXCEEDED = "budget_exceeded"


CLAIM_BEARING_OUTCOMES: frozenset[ComparisonOutcome] = frozenset({
    ComparisonOutcome.COMPARISON,
    ComparisonOutcome.NO_DIFFERENCE_IN_COMPARED_FIELDS,
})

REFUSAL_OUTCOMES: frozenset[ComparisonOutcome] = frozenset(
    o for o in ComparisonOutcome if o not in CLAIM_BEARING_OUTCOMES
)


# What a passing `validate_comparison` does NOT establish. Carried on
# every claim-bearing result rather than left in a docstring, because
# the consumer of the output is the party that needs to know. Each entry
# corresponds to a demonstrated test case.
UNVERIFIABLE_BY_VALIDATION: tuple[str, ...] = (
    "A statement may cite a real, in-scope document and still not follow from it: "
    "reference checking proves the source was provided, never that the sentence is a "
    "true reading of it.",
    "A genuine quotation attached to the wrong claim passes every check here, because "
    "nothing mechanically ties a quoted span to the assertion it is offered as support for.",
    "A difference in a field outside `compared_fields` is invisible to this component; "
    "a scoped no-difference result is silent about, not evidence against, such a change.",
    "Measurement grounding compares a number with its currency, scale and percent marker "
    "as written. A figure attributed to the wrong party or period, and a unit or scale "
    "expressed in words the lists do not carry, are undetected.",
    "Grounding admits any figure present in the excerpt, wherever in it the figure sits. "
    "A number planted in the source text — by an injected instruction the scrub did not "
    "remove, or by an upstream extraction error — is therefore admissible, so grounding "
    "bounds invention by the model, not corruption of the evidence.",
    "Conditional-language preservation is checked by keyword proximity. It can miss a "
    "hedge expressed in words it does not list, and it cannot judge whether retained "
    "hedging was applied to the right subject.",
    "A relationship span is checked for presence, substance and verbatim match against "
    "the excerpt. Whether it actually asserts a revision of the same subject is NOT "
    "machine-verified: a caller that supplies an unrelated sentence as revision evidence "
    "will be believed about its meaning, though not about its existence.",
    "Field evidence is supplied by the caller and checked for internal consistency "
    "against the excerpt. This component does not independently re-extract a field, so "
    "it cannot detect an upstream extraction error that is self-consistent.",
    "Revision language is refused by keyword match when no revision relationship is "
    "established. A revision asserted in words the list does not carry, or by "
    "implication alone, is not caught — and the match also OVER-refuses: a faithful "
    "quotation of a revision word that occurs inside one document is rejected along "
    "with an invented revision narrative, so some true information is lost. The local "
    "SK hynix facility-investment excerpt contains one such word (증가).",
    "Grounding is only as discriminating as the evidence is sparse. A long excerpt "
    "containing many section numbers and dates already supplies most single-digit "
    "values, so for such documents a single-digit check rejects little. It remains "
    "worth running because a percentage or ratio stated nowhere in the evidence is "
    "still caught.",
    "Semantic entailment is not checked anywhere in this package. A result that passes "
    "validation is well-formed and in-scope; it is not thereby correct.",
)


class ClaimSupport(str, Enum):
    """How a statement relates to the two documents.

    SOURCE_STATED is the only level that may carry a figure, a date or a
    named party; INTERPRETATION is the model's reading and is rendered
    and validated as such. EVIDENCED_CHANGE additionally requires an
    established revision relationship."""

    SOURCE_STATED = "source_stated"
    EVIDENCED_CHANGE = "evidenced_change"
    INTERPRETATION = "interpretation"


# --- measurement extraction ----------------------------------------------
#
# A bare digit run is not enough to ground a claim. "1%" and "9%" differ
# by a single character and by a factor of nine; "$500 million" and
# "€500 billion" share every digit. So the unit of grounding is a
# MEASUREMENT: the signed, canonicalized value together with the
# currency, scale word and percent marker written around it.

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_SIGN_PRECEDERS = frozenset(" \t\n\r([{=:,;/")

# Longest first, so "dollars" wins over "dollar".
_CURRENCY_TOKENS: tuple[str, ...] = (
    "dollars", "dollar", "euros", "euro", "pounds", "pound",
    "usd", "eur", "gbp", "jpy", "krw", "cny", "won", "yen",
    "$", "€", "£", "¥", "₩", "원", "달러",
)
_SCALE_TOKENS: tuple[str, ...] = (
    "trillion", "billion", "million", "thousand", "bn", "tn",
    "조", "억", "만",
)
_CURRENCY_LOOKBACK = 12
_UNIT_LOOKAHEAD = 18


def _canonical_value(raw: str, negative: bool) -> str:
    """Canonicalizes a digit run so that only real differences differ.

    Thousands separators are dropped and leading/trailing zeros
    normalized, so "5,846,278,608" and "5846278608" agree and "0.10"
    and "0.1" agree. A minus sign is preserved, because it is the whole
    difference between a gain and a loss."""
    s = raw.replace(",", "")
    if "." in s:
        whole, _, frac = s.partition(".")
        frac = frac.rstrip("0")
        whole = whole.lstrip("0") or "0"
        s = f"{whole}.{frac}" if frac else whole
    else:
        s = s.lstrip("0") or "0"
    if negative and s != "0":
        s = f"-{s}"
    return s


def _is_negative(text: str, start: int) -> bool:
    """A "-" counts as a sign only where a sign can legally appear.

    This is what keeps "2026-08-17" from reading as 2026, -8, -17."""
    if start == 0 or text[start - 1] != "-":
        return False
    return start - 2 < 0 or text[start - 2] in _SIGN_PRECEDERS


def _leading_token(window: str, tokens: tuple[str, ...]) -> str:
    stripped = window.lstrip()
    for token in tokens:
        if stripped.startswith(token):
            return token
    return ""


def _trailing_token(window: str, tokens: tuple[str, ...]) -> str:
    stripped = window.rstrip()
    for token in tokens:
        if stripped.endswith(token):
            return token
    return ""


@dataclass(frozen=True)
class Measurement:
    """A number as a claim actually uses it."""

    value: str
    currency: str = ""
    scale: str = ""
    percent: bool = False

    def key(self) -> str:
        return f"{self.value}|{self.currency}|{self.scale}|{'%' if self.percent else ''}"

    def describe(self) -> str:
        parts = [self.currency, self.value, self.scale, "%" if self.percent else ""]
        return " ".join(p for p in parts if p).strip()


def measurements(text: str) -> frozenset[str]:
    """Every measurement key in `text`.

    Single digits are INCLUDED. They were excluded in an earlier
    version as prose noise, which was wrong: a percentage, a share
    count and a ratio are routinely single digits, and "1%" versus "9%"
    is exactly the alteration that most needs catching."""
    out: set[str] = set()
    low = (text or "").lower()
    for match in _NUMBER.finditer(low):
        start, end = match.span()
        negative = _is_negative(low, start)
        back = low[max(0, start - 1 - _CURRENCY_LOOKBACK):start - 1 if negative else start]
        forward = low[end:end + _UNIT_LOOKAHEAD]

        percent = forward.lstrip().startswith("%") or forward.lstrip().startswith("퍼센트")
        scale = _leading_token(forward, _SCALE_TOKENS)
        after_scale = forward.lstrip()[len(scale):] if scale else forward
        currency = (
            _trailing_token(back, _CURRENCY_TOKENS)
            or _leading_token(after_scale, _CURRENCY_TOKENS)
        )
        out.add(Measurement(
            value=_canonical_value(match.group(0), negative),
            currency=currency, scale=scale, percent=percent,
        ).key())
    return frozenset(out)


# Hedging language whose loss or reversal changes a claim's meaning.
_HEDGE_TOKENS: tuple[str, ...] = (
    "expect", "expected", "expects", "anticipate", "anticipated", "anticipates",
    "intend", "intends", "plan", "plans", "may", "might", "could", "would",
    "approximately", "approximate", "about", "around", "estimated", "estimate",
    "subject to", "preliminary", "unaudited", "up to", "at least", "as of",
    "if ", "pending", "proposed", "scheduled", "target",
    # Korean forms observed in the local DART evidence — "변동될 수 있음"
    # ("may change") and "예정일" ("scheduled date") both appear in the
    # SK hynix facility-investment pair's qualifying footnotes.
    "예정", "예상", "추정", "약 ", "잠정", "수 있음", "변동될 수 있",
)
_HEDGE_WINDOW_CHARS = 120

# Language that asserts one document revised another. Refused outright
# unless the relationship is EVIDENCED_REVISION. Comparative description
# ("larger than", "higher than") is deliberately NOT here: comparing two
# side-by-side disclosures is legitimate; narrating a revision is not.
# Stems only, so one phrase is not reported twice ("increase" already
# covers "increased"). Comparative words are deliberately absent, and
# the inflected forms that are NOT stems of a comparative are kept in
# full: "lowered" is here but "lower" is not, because "lower than"
# describes two documents rather than asserting a revision.
_REVISION_LANGUAGE: tuple[str, ...] = (
    "increase", "decrease", "rose", "fell", "grew", "growth",
    "decline", "reduced", "reduction", "raised", "lowered",
    "revised", "revision", "restate", "amend",
    "updated from", "changed from", "change from", "up from", "down from",
    "was previously", "previously reported", "follow-up to", "supersede",
    "인상", "인하", "증가", "감소", "정정", "변경되었",
)


def _has_hedge(text: str) -> bool:
    low = (text or "").lower()
    return any(token in low for token in _HEDGE_TOKENS)


def revision_language_in(text: str) -> tuple[str, ...]:
    """Which revision phrases appear in `text`, if any."""
    low = (text or "").lower()
    return tuple(sorted({token for token in _REVISION_LANGUAGE if token in low}))


# --- per-side field evidence ---------------------------------------------

@dataclass(frozen=True)
class FieldEvidence:
    """What one side of the comparison establishes about ONE named field.

    This is the whole basis for a scoped absence claim, and it replaces
    the discarded tail heuristic. The caller supplies it from its own
    extraction; this module checks it for internal consistency against
    the excerpt and refuses the absence claim when it cannot be
    established.

    `source_span` must be the exact text the value was read out of, and
    must occur verbatim in that side's excerpt. `value_qualifications`
    carries the hedging attached to the value where the source has any
    — a value qualified as preliminary in one filing and final in the
    other has not been shown to be unchanged."""

    field_name: str
    extracted_value: str
    source_span: str
    value_qualifications: tuple[str, ...] = ()

    def violations(self, excerpt: str, completeness: ExcerptCompleteness) -> tuple[str, ...]:
        """Why this evidence cannot establish the field's value.

        Deliberately strict, because the only thing it licenses is a
        statement that something did NOT change."""
        out: list[str] = []
        if not self.field_name.strip():
            out.append("blank_field_name")
        if not self.extracted_value.strip():
            out.append("blank_extracted_value")
        if not self.source_span.strip():
            out.append("blank_source_span")
            return tuple(out)

        index = excerpt.find(self.source_span)
        if index == -1:
            out.append("source_span_not_found_in_excerpt")
            return tuple(out)
        if self.field_name.strip() and self.field_name not in self.source_span:
            out.append("field_name_not_in_source_span")
        if self.extracted_value.strip() and self.extracted_value not in self.source_span:
            out.append("extracted_value_not_in_source_span")

        # The span must be shown to have ENDED inside what was
        # extracted. A span running to the last character of an excerpt
        # that is not declared COMPLETE may be the exact place the text
        # was cut, so the value may continue past it — which is how a
        # share count reading "802,371,20" happens.
        end = index + len(self.source_span)
        if end >= len(excerpt) and completeness is not ExcerptCompleteness.COMPLETE:
            out.append("source_span_may_be_truncated")
        return tuple(out)


@dataclass(frozen=True)
class FieldComparisonRecord:
    """The audit trail for one compared field: what each side said, the
    span it was read from, and whether the values agreed."""

    field_name: str
    current_value: str
    prior_value: str
    current_span: str
    prior_span: str
    current_qualifications: tuple[str, ...] = ()
    prior_qualifications: tuple[str, ...] = ()

    @property
    def values_match(self) -> bool:
        return self.current_value.strip() == self.prior_value.strip()

    @property
    def qualifications_match(self) -> bool:
        return tuple(self.current_qualifications) == tuple(self.prior_qualifications)

    def to_dict(self) -> dict:
        return {
            "field_name": self.field_name,
            "current_value": self.current_value,
            "prior_value": self.prior_value,
            "current_span": self.current_span,
            "prior_span": self.prior_span,
            "current_qualifications": list(self.current_qualifications),
            "prior_qualifications": list(self.prior_qualifications),
            "values_match": self.values_match,
            "qualifications_match": self.qualifications_match,
        }


@dataclass(frozen=True)
class ComparisonEvidence:
    """One side of the comparison. Mirrors what the filing pipeline
    already persists — nothing here requires a new fetch, parser or
    heuristic."""

    document_id: str
    source_name: str
    source_tier: SourceTier
    source_url: str
    filed_at: str
    excerpt: str
    extraction_state: ExtractionState
    excerpt_quality: ExcerptQuality
    completeness: ExcerptCompleteness
    retrieved_at: str
    evidence_location: EvidenceLocation | None = None
    form_type: str = ""
    matched_rules: tuple[str, ...] = ()
    field_evidence: tuple[FieldEvidence, ...] = ()

    @property
    def is_usable(self) -> bool:
        """Usable text to compare at all — extraction succeeded, the
        shape is not boilerplate, and there is something there.

        Usability permits excerpt-scoped facts and differences at any
        completeness, UNKNOWN included: a partial excerpt can still show
        what it does contain. It never permits an absence claim."""
        return (
            self.extraction_state in _USABLE_EXTRACTION_STATES
            and self.excerpt_quality not in _UNUSABLE_EXCERPT_QUALITIES
            and bool(self.excerpt.strip())
        )

    def field_named(self, name: str) -> FieldEvidence | None:
        wanted = (name or "").strip()
        for evidence in self.field_evidence:
            if evidence.field_name.strip() == wanted:
                return evidence
        return None

    def field_gap(self, name: str) -> str | None:
        """Why this side cannot establish `name`, or None when it can."""
        evidence = self.field_named(name)
        if evidence is None:
            return "no field evidence was supplied for this field"
        problems = evidence.violations(self.excerpt, self.completeness)
        if problems:
            return ", ".join(problems)
        return None

    def measurements(self) -> frozenset[str]:
        """Everything this document can ground: its excerpt and its own
        filing date."""
        return measurements(self.excerpt) | measurements(self.filed_at)

    def violations(self) -> tuple[str, ...]:
        out: list[str] = []
        for name in ("document_id", "source_name", "source_url", "filed_at", "retrieved_at"):
            if not str(getattr(self, name) or "").strip():
                out.append(f"blank_{name}")
        if not isinstance(self.source_tier, SourceTier):
            out.append("invalid_source_tier")
        if not isinstance(self.extraction_state, ExtractionState):
            out.append("invalid_extraction_state")
        if not isinstance(self.excerpt_quality, ExcerptQuality):
            out.append("invalid_excerpt_quality")
        if not isinstance(self.completeness, ExcerptCompleteness):
            out.append("invalid_completeness")
        if self.completeness is not ExcerptCompleteness.UNKNOWN and self.extraction_state not in _USABLE_EXTRACTION_STATES:
            # Declaring a document complete or truncated presupposes that
            # extraction produced something to judge.
            out.append("completeness_declared_without_extraction")
        return tuple(out)


@dataclass(frozen=True)
class SourceReference:
    """A pointer a reader can follow to re-check a statement. Minted from
    the request's own evidence — a reference to a document the request
    never carried is a validation failure, not a citation."""

    document_id: str
    source_url: str
    filed_at: str
    locator: str = ""

    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id, "source_url": self.source_url,
            "filed_at": self.filed_at, "locator": self.locator,
        }


@dataclass(frozen=True)
class RelationshipEvidence:
    """The source text that establishes a revision relationship, and the
    document it came from.

    Retained on the output together with its source reference, so a
    reader can check the linkage rather than take it on trust."""

    document_id: str
    source_span: str
    basis: str = ""
    reference: SourceReference | None = None

    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "source_span": self.source_span,
            "basis": self.basis,
            "reference": self.reference.to_dict() if self.reference else None,
        }


@dataclass(frozen=True)
class ComparisonStatement:
    """One statement, with its support level and its references.

    A SOURCE_STATED or EVIDENCED_CHANGE statement must carry at least one
    reference. An INTERPRETATION statement may carry references too, and
    citing them does NOT make the interpretation a fact — see
    `validate_comparison`'s own note."""

    text: str
    support: ClaimSupport
    references: tuple[SourceReference, ...] = ()


@dataclass(frozen=True)
class ComparisonRequest:
    """Everything the component is allowed to reason over. There is no
    other input channel: no retrieval, no tool use, no browsing.

    `relationship` is an INPUT, declared by the caller from source
    evidence. The model is never asked for it and cannot change it."""

    request_id: str
    issuer_id: str
    issuer_name: str
    current: ComparisonEvidence
    prior: ComparisonEvidence | None
    comparability_status: ComparisonStatus
    comparison_basis: str = ""
    added_categories: tuple[str, ...] = ()
    removed_categories: tuple[str, ...] = ()
    # Safe default: describing two disclosures side by side asserts no
    # revision, so it needs no evidence.
    relationship: ComparisonRelationship = ComparisonRelationship.SIDE_BY_SIDE
    relationship_evidence: RelationshipEvidence | None = None

    def violations(self) -> tuple[str, ...]:
        out: list[str] = []
        for name in ("request_id", "issuer_id", "issuer_name"):
            if not str(getattr(self, name) or "").strip():
                out.append(f"blank_{name}")
        if not isinstance(self.comparability_status, ComparisonStatus):
            out.append("invalid_comparability_status")
        if not isinstance(self.relationship, ComparisonRelationship):
            out.append("invalid_relationship")
        out.extend(f"current.{v}" for v in self.current.violations())
        if self.prior is not None:
            out.extend(f"prior.{v}" for v in self.prior.violations())
        out.extend(relationship_evidence_violations(self))
        return tuple(out)

    def documents(self) -> dict[str, ComparisonEvidence]:
        return {
            side.document_id: side
            for side in (self.current, self.prior) if side is not None
        }

    def admissible_measurements(self, document_ids: tuple[str, ...] = ()) -> frozenset[str]:
        """What a claim may ground against.

        With `document_ids`, only those documents — which is how a
        figure supported only by the OTHER filing is caught. Without,
        the union, for fields that carry no references of their own."""
        docs = self.documents()
        chosen = [docs[d] for d in document_ids if d in docs] if document_ids else list(docs.values())
        out: set[str] = set()
        for side in chosen:
            out |= side.measurements()
        return frozenset(out)


def relationship_evidence_violations(request: ComparisonRequest) -> tuple[str, ...]:
    """Whether a declared revision relationship is actually evidenced.

    A matching issuer, filing family, rule category or date sequence
    cannot establish a revision, so a span that is only one of those is
    rejected explicitly rather than accepted as a formality."""
    if request.relationship is not ComparisonRelationship.EVIDENCED_REVISION:
        if request.relationship_evidence is not None:
            return ("relationship_evidence_supplied_without_evidenced_revision",)
        return ()

    evidence = request.relationship_evidence
    if evidence is None:
        return ("evidenced_revision_without_relationship_evidence",)

    out: list[str] = []
    documents = request.documents()
    side = documents.get(evidence.document_id)
    if side is None:
        out.append("relationship_evidence_cites_unknown_document")
        return tuple(out)

    span = evidence.source_span.strip()
    if not span:
        out.append("relationship_evidence_has_blank_span")
        return tuple(out)
    if evidence.source_span not in side.excerpt:
        out.append("relationship_evidence_span_not_found_in_excerpt")
    if len(span) < MIN_RELATIONSHIP_SPAN_CHARS:
        out.append("relationship_evidence_span_too_short_to_establish_a_revision")

    # The facts that explicitly do NOT establish a revision.
    inadmissible = [request.issuer_name, side.form_type, side.filed_at, request.comparison_basis]
    inadmissible.extend(request.added_categories)
    inadmissible.extend(request.removed_categories)
    folded = span.casefold()
    for fact in inadmissible:
        fact = str(fact or "").strip().casefold()
        if fact and (folded == fact or folded in fact):
            out.append("relationship_evidence_span_is_only_issuer_family_category_or_date")
            break
    return tuple(out)


@dataclass(frozen=True)
class FilingComparison:
    """The serializable result. Streamlit-free and JSON-ready via
    `to_dict()` — this is the shape a later customer-facing API would
    render, which is why nothing here is a UI object."""

    request_id: str
    outcome: ComparisonOutcome
    summary: str = ""
    source_stated_facts: tuple[ComparisonStatement, ...] = ()
    evidenced_changes: tuple[ComparisonStatement, ...] = ()
    interpretation: str = ""
    qualifications: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()
    # Named fields this result actually compared, and named fields it
    # deliberately did not.
    compared_fields: tuple[str, ...] = ()
    fields_not_compared: tuple[str, ...] = ()
    # The per-field audit trail behind a scoped absence claim.
    field_comparisons: tuple[FieldComparisonRecord, ...] = ()
    # Carried from the request, never from the model.
    relationship: ComparisonRelationship = ComparisonRelationship.SIDE_BY_SIDE
    relationship_evidence: RelationshipEvidence | None = None
    claim_scope: ClaimScope = ClaimScope.EXCERPT
    unverified_limitations: tuple[str, ...] = ()
    refusal_reason: str = ""
    provider_model: str = ""
    validation_violations: tuple[str, ...] = ()
    usage: dict = field(default_factory=dict)

    @property
    def is_refusal(self) -> bool:
        return self.outcome in REFUSAL_OUTCOMES

    def to_dict(self) -> dict:
        def statement(s: ComparisonStatement) -> dict:
            return {
                "text": s.text,
                "support": s.support.value,
                "references": [r.to_dict() for r in s.references],
            }

        return {
            "request_id": self.request_id,
            "outcome": self.outcome.value,
            "summary": self.summary,
            "source_stated_facts": [statement(s) for s in self.source_stated_facts],
            "evidenced_changes": [statement(s) for s in self.evidenced_changes],
            "interpretation": self.interpretation,
            "qualifications": list(self.qualifications),
            "uncertainties": list(self.uncertainties),
            "compared_fields": list(self.compared_fields),
            "fields_not_compared": list(self.fields_not_compared),
            "field_comparisons": [f.to_dict() for f in self.field_comparisons],
            "relationship": self.relationship.value,
            "relationship_evidence": (
                self.relationship_evidence.to_dict() if self.relationship_evidence else None
            ),
            "claim_scope": self.claim_scope.value,
            "unverified_limitations": list(self.unverified_limitations),
            "refusal_reason": self.refusal_reason,
            "provider_model": self.provider_model,
            "validation_violations": list(self.validation_violations),
            "usage": dict(self.usage),
        }


def refusal(
    request_id: str, outcome: ComparisonOutcome, reason: str,
    violations: tuple[str, ...] = (), usage: dict | None = None, provider_model: str = "",
) -> FilingComparison:
    """Every refusal path returns a result, never raises — the caller
    always has something to render and to log."""
    return FilingComparison(
        request_id=request_id, outcome=outcome, refusal_reason=reason,
        validation_violations=violations, usage=dict(usage or {}), provider_model=provider_model,
    )


# --- validation -----------------------------------------------------------

def _reference_violations(prefix: str, statement: ComparisonStatement, allowed: dict[str, SourceReference]) -> list[str]:
    out: list[str] = []
    if statement.support in (ClaimSupport.SOURCE_STATED, ClaimSupport.EVIDENCED_CHANGE) and not statement.references:
        out.append(f"{prefix}.missing_reference")
    for ref in statement.references:
        known = allowed.get(ref.document_id)
        if known is None:
            out.append(f"{prefix}.reference_to_unknown_document")
            continue
        if ref.source_url != known.source_url:
            out.append(f"{prefix}.reference_url_does_not_match_request")
        if ref.filed_at != known.filed_at:
            out.append(f"{prefix}.reference_date_does_not_match_request")
    return out


def allowed_references(request: ComparisonRequest) -> dict[str, SourceReference]:
    """The complete set of documents a statement may cite: exactly the
    one or two the request carried."""
    refs: dict[str, SourceReference] = {}
    for side in (request.current, request.prior):
        if side is not None:
            refs[side.document_id] = SourceReference(
                document_id=side.document_id, source_url=side.source_url, filed_at=side.filed_at,
            )
    return refs


def _grounding_violations(prefix: str, text: str, admissible: frozenset[str]) -> list[str]:
    """Any measurement in a claim-bearing field must appear in the
    evidence it is allowed to draw on.

    Applied to EVERY field that carries a claim, not only to the typed
    statement lists: a fabricated figure is no less fabricated for
    sitting in the summary or the interpretation, where neither a
    support level nor a reference would have been required of it."""
    unknown = sorted(measurements(text) - admissible)
    return [f"{prefix}.ungrounded_measurement:{key}" for key in unknown]


def _hedge_violations(prefix: str, statement: ComparisonStatement, documents: dict[str, ComparisonEvidence]) -> list[str]:
    """Flags a figure whose source context hedges it while the statement
    does not.

    A keyword-proximity heuristic, and documented as such in
    UNVERIFIABLE_BY_VALIDATION."""
    if _has_hedge(statement.text):
        return []
    for ref in statement.references:
        side = documents.get(ref.document_id)
        if side is None:
            continue
        excerpt = side.excerpt
        low = excerpt.lower()
        for match in _NUMBER.finditer(low):
            key = Measurement(value=_canonical_value(match.group(0), _is_negative(low, match.start()))).value
            if key not in {m.split("|")[0] for m in measurements(statement.text)}:
                continue
            start = max(0, match.start() - _HEDGE_WINDOW_CHARS)
            end = min(len(excerpt), match.end() + _HEDGE_WINDOW_CHARS)
            if _has_hedge(excerpt[start:end]):
                return [f"{prefix}.conditional_language_not_preserved:{key}"]
    return []


def _relationship_violations(result: FilingComparison, request: ComparisonRequest) -> list[str]:
    """Nothing may be narrated as a revision unless the relationship is
    established by source evidence."""
    out: list[str] = []
    if result.relationship is not request.relationship:
        out.append("relationship_does_not_match_request")
    if request.relationship is ComparisonRelationship.EVIDENCED_REVISION:
        if result.relationship_evidence is None:
            out.append("evidenced_revision_result_without_retained_relationship_evidence")
        elif result.relationship_evidence.reference is None:
            out.append("retained_relationship_evidence_without_source_reference")
        return out

    # Not an evidenced revision: no revision language anywhere, and no
    # evidenced_changes at all.
    if result.evidenced_changes:
        out.append("evidenced_change_without_established_revision_relationship")
    fields: tuple[tuple[str, str], ...] = (
        ("summary", result.summary),
        ("interpretation", result.interpretation),
        *((f"qualifications[{i}]", t) for i, t in enumerate(result.qualifications)),
        *((f"uncertainties[{i}]", t) for i, t in enumerate(result.uncertainties)),
        *((f"source_stated_facts[{i}]", s.text) for i, s in enumerate(result.source_stated_facts)),
        *((f"evidenced_changes[{i}]", s.text) for i, s in enumerate(result.evidenced_changes)),
    )
    for label, text in fields:
        found = revision_language_in(text)
        if found:
            out.append(f"{label}.revision_language_without_established_relationship:{found[0]}")
    return out


def _scoped_absence_violations(result: FilingComparison, request: ComparisonRequest) -> list[str]:
    """What a `NO_DIFFERENCE_IN_COMPARED_FIELDS` result must establish.

    The scope must be named, every named field must be backed by valid
    per-side `FieldEvidence`, and the two extracted values must actually
    agree. Nothing about the excerpt as a whole is consulted."""
    out: list[str] = []
    if not result.compared_fields:
        out.append("scoped_absence_without_compared_fields")
        return out
    if request.prior is None:
        out.append("scoped_absence_without_prior_evidence")
        return out
    for index, name in enumerate(result.compared_fields):
        if not str(name or "").strip():
            out.append(f"compared_fields[{index}].blank_name")
            continue
        for label, side in (("current", request.current), ("prior", request.prior)):
            gap = side.field_gap(name)
            if gap is not None:
                out.append(f"compared_fields[{index}].{label}_field_evidence_unusable")
        current = request.current.field_named(name)
        prior = request.prior.field_named(name)
        if current is None or prior is None:
            continue
        if current.extracted_value.strip() != prior.extracted_value.strip():
            out.append(f"compared_fields[{index}].values_differ_so_this_is_not_an_absence")
        if tuple(current.value_qualifications) != tuple(prior.value_qualifications):
            out.append(f"compared_fields[{index}].qualifications_differ_between_sides")
    if not result.field_comparisons:
        out.append("scoped_absence_without_field_comparison_record")
    elif {r.field_name for r in result.field_comparisons} != {n for n in result.compared_fields if n.strip()}:
        out.append("field_comparison_record_does_not_cover_the_compared_fields")
    return out


def validate_comparison(result: FilingComparison, request: ComparisonRequest) -> tuple[str, ...]:
    """Every violation, never a raise — the same exhaustive style
    `validate_claim_proposal` uses. A non-empty result means the output
    is rejected outright; it is never retried against a looser schema.

    What this validates is MECHANICAL: shape, caps, support levels, that
    every reference resolves to a document the request actually carried,
    that every measurement in every claim-bearing field appears in the
    evidence that field is allowed to draw on, that a scoped absence
    claim is backed by per-side field evidence whose values agree, that
    revision language appears only under an evidenced revision
    relationship, and that a hedged figure is not restated as settled.

    It does not and cannot establish that a statement is a true reading
    of the document. A correctly-formed citation proves the source
    exists and was in scope, not that the sentence beside it follows
    from it; grounding proves a measurement occurs in the evidence, not
    that it was used correctly; and a relationship span is checked for
    existence, not for meaning. The checks this validator CANNOT perform
    are enumerated in `UNVERIFIABLE_BY_VALIDATION` and are attached to
    every claim-bearing result, so a passing validation is never
    reported as semantic correctness."""
    violations: list[str] = []
    if result.request_id != request.request_id:
        violations.append("request_id_mismatch")
    if not isinstance(result.outcome, ComparisonOutcome):
        violations.append("invalid_outcome")
        return tuple(violations)

    if result.outcome in REFUSAL_OUTCOMES:
        if not result.refusal_reason.strip():
            violations.append("blank_refusal_reason")
        return tuple(violations)

    if result.claim_scope is not ClaimScope.EXCERPT:
        violations.append("claim_scope_is_not_excerpt_scoped")
    if not result.unverified_limitations:
        violations.append("missing_unverified_limitations")

    if len(result.summary) > SUMMARY_MAX_CHARS:
        violations.append("summary_too_long")
    if len(result.interpretation) > INTERPRETATION_MAX_CHARS:
        violations.append("interpretation_too_long")
    if len(result.source_stated_facts) > MAX_SOURCE_STATED_FACTS:
        violations.append("too_many_source_stated_facts")
    if len(result.evidenced_changes) > MAX_EVIDENCED_CHANGES:
        violations.append("too_many_evidenced_changes")
    if len(result.qualifications) > MAX_QUALIFICATIONS:
        violations.append("too_many_qualifications")
    if len(result.uncertainties) > MAX_UNCERTAINTIES:
        violations.append("too_many_uncertainties")
    if len(result.compared_fields) > MAX_COMPARED_FIELDS:
        violations.append("too_many_compared_fields")

    if result.outcome is ComparisonOutcome.COMPARISON and not (
        result.source_stated_facts or result.evidenced_changes
    ):
        violations.append("comparison_without_any_evidenced_statement")

    if result.outcome is ComparisonOutcome.NO_DIFFERENCE_IN_COMPARED_FIELDS:
        violations.extend(_scoped_absence_violations(result, request))
        if result.evidenced_changes:
            violations.append("scoped_absence_with_evidenced_changes")

    violations.extend(_relationship_violations(result, request))

    # Grounding over EVERY claim-bearing field. The free-text fields
    # carry no references, so they may draw on either document.
    everything = request.admissible_measurements()
    violations.extend(_grounding_violations("summary", result.summary, everything))
    violations.extend(_grounding_violations("interpretation", result.interpretation, everything))
    for index, text in enumerate(result.qualifications):
        violations.extend(_grounding_violations(f"qualifications[{index}]", text, everything))
    for index, text in enumerate(result.uncertainties):
        violations.extend(_grounding_violations(f"uncertainties[{index}]", text, everything))

    documents = request.documents()
    allowed = allowed_references(request)
    for group, expected_support, label in (
        (result.source_stated_facts, ClaimSupport.SOURCE_STATED, "source_stated_facts"),
        (result.evidenced_changes, ClaimSupport.EVIDENCED_CHANGE, "evidenced_changes"),
    ):
        for index, statement in enumerate(group):
            prefix = f"{label}[{index}]"
            if not statement.text.strip():
                violations.append(f"{prefix}.blank_text")
            elif len(statement.text) > STATEMENT_MAX_CHARS:
                violations.append(f"{prefix}.text_too_long")
            if statement.support is not expected_support:
                violations.append(f"{prefix}.wrong_support_level")
            violations.extend(_reference_violations(prefix, statement, allowed))
            # A statement may only ground against the documents it
            # cites, so a figure that exists only in the other filing is
            # caught here.
            cited = tuple(r.document_id for r in statement.references)
            scope = request.admissible_measurements(cited) if cited else everything
            violations.extend(_grounding_violations(prefix, statement.text, scope))
            violations.extend(_hedge_violations(prefix, statement, documents))

    # An evidenced CHANGE needs a prior document to have changed from.
    if result.evidenced_changes and request.prior is None:
        violations.append("evidenced_change_without_prior_evidence")

    return tuple(violations)
