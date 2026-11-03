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
    the filings say the same thing. Two filings reporting different
    ownership percentages under the same rule category are
    NO_MATERIAL_CHANGE.
  * `CHANGE_DETECTED` means those sets differ. It is not a finding that
    a materially important business change occurred — a rule category
    can appear or disappear because detection coverage differs.
  * `NOT_COMPARABLE` / `NOT_AVAILABLE` are the only values this module
    treats as dispositive, and only in the refusing direction.

That module also records its own limitations, which this one inherits
rather than papers over: `_PERIOD_LIMITATION` ("Comparable reporting
period is not available in current metadata.") means the two sides are
not known to cover comparable periods, and `_KNOWN_EXCERPT_TRUNCATION_
BOUND = 600` exists only to flag that an excerpt "reached the known
600-character truncation bound and may be incomplete" — a length-derived
suspicion, not a recorded extraction fact.

Consequence for this component: comparability status is an input gate,
never a claim. No status value licenses any sentence about what the
documents say.

--- Why completeness is never inferred from length ---

Both extractors cap excerpts (`MAX_EXCERPT_CHARS = 600`, and EDGAR's
larger 8-K item cap), and `ExtractionResult` carries no truncation
field. An excerpt at the cap is therefore *probably* truncated, and one
under it may still be partial for unrelated reasons. Length is not
evidence of completeness in either direction, so this contract carries
`ExcerptCompleteness` as its own declared field, sourced from the
pipeline's own extraction state rather than measured from the text.
`UNKNOWN` is a first-class value and the safe default, and it is never
read as COMPLETE anywhere in this package.

--- Why there is no "nothing changed" outcome ---

There is deliberately no outcome meaning "this filing is unchanged".
The strongest absence statement representable here is
`NO_DIFFERENCE_IN_COMPARED_FIELDS`, which is valid only when the result
names the fields it compared, each named field is locatable in BOTH
excerpts, and no named field's value sits in the region a truncated
excerpt may have cut. Completeness alone never licenses an absence
claim: COMPLETE describes the excerpt the pipeline produced, not the
filing it came from, so even two COMPLETE excerpts can only support a
claim scoped to the fields actually compared within them.

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

CONTRACT_VERSION = "filing_comparison/contract/v2"

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

# How much of a non-COMPLETE excerpt's tail is treated as possibly cut.
# A named field whose value region falls entirely inside this window
# cannot support a field-scoped absence claim: the value itself may be
# the thing that was truncated. Observed basis: the real DART pair in
# `data/cache` ends mid-number ("802,371,20") at the 600-character cap.
TRUNCATION_TAIL_CHARS = 80

# Numbers of two or more digits, separators stripped. Single digits are
# deliberately out of scope — they collide with list markers, section
# numbers and ordinary prose, and flagging them would make the grounding
# check unusable. A fabricated single-digit value is therefore NOT
# caught; see UNVERIFIABLE_BY_VALIDATION.
_NUMERIC_RUN = re.compile(r"\d[\d,. \s]*\d|\d")
_SEPARATORS = re.compile(r"[,. \s]")

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

# How far either side of a repeated figure a hedge token still counts as
# qualifying that figure.
_HEDGE_WINDOW_CHARS = 120


class ClaimScope(str, Enum):
    """The scope of every statement this component can make.

    One value, on purpose. A filing-level or issuer-level claim is not
    representable, so no code path can emit one and no reviewer has to
    take its absence on trust."""

    EXCERPT = "excerpt"


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
    # Scoped absence: no difference in the fields this result NAMES.
    # Never "the filing did not change" — see the module docstring.
    NO_DIFFERENCE_IN_COMPARED_FIELDS = "no_difference_in_compared_fields"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NOT_COMPARABLE = "not_comparable"
    NO_PRIOR_FILING = "no_prior_filing"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    OUTPUT_REJECTED = "output_rejected"
    BUDGET_EXCEEDED = "budget_exceeded"


# Outcomes that carry claims about the documents and so must pass the
# full content validation.
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
    "Numeric grounding matches digit runs only. A fabricated single-digit value, a "
    "correct figure given the wrong unit, scale or currency, and a correct figure "
    "attributed to the wrong party or period are all undetected.",
    "Grounding admits any figure present in the excerpt, wherever in it the figure sits. "
    "A number planted in the source text — by an injected instruction the scrub did not "
    "remove, or by an upstream extraction error — is therefore admissible, so grounding "
    "bounds invention by the model, not corruption of the evidence.",
    "Conditional-language preservation is checked by keyword proximity. It can miss a "
    "hedge expressed in words it does not list, and it cannot judge whether retained "
    "hedging was applied to the right subject.",
    "Semantic entailment is not checked anywhere in this package. A result that passes "
    "validation is well-formed and in-scope; it is not thereby correct.",
)


class ClaimSupport(str, Enum):
    """How a statement relates to the two documents.

    SOURCE_STATED is the only level that may carry a figure, a date or a
    named party; INTERPRETATION is the model's reading and is rendered
    and validated as such."""

    SOURCE_STATED = "source_stated"
    EVIDENCED_CHANGE = "evidenced_change"
    INTERPRETATION = "interpretation"


def numeric_tokens(text: str) -> frozenset[str]:
    """Digit runs of two or more digits, separators removed. The unit of
    the grounding check — see `_NUMERIC_RUN` for what is out of scope."""
    out: set[str] = set()
    for match in _NUMERIC_RUN.finditer(text or ""):
        digits = _SEPARATORS.sub("", match.group(0))
        if len(digits) >= 2:
            out.add(digits)
    return frozenset(out)


def _has_hedge(text: str) -> bool:
    low = (text or "").lower()
    return any(token in low for token in _HEDGE_TOKENS)


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

    @property
    def tail_begins_at(self) -> int:
        """Index after which content may have been cut. For a COMPLETE
        excerpt there is no such region, so the whole text is readable."""
        if self.completeness is ExcerptCompleteness.COMPLETE:
            return len(self.excerpt)
        return max(0, len(self.excerpt) - TRUNCATION_TAIL_CHARS)

    def locates_field(self, name: str) -> bool:
        """Whether a named field can be read out of this excerpt with
        its value intact.

        Requires an occurrence of the name whose following value region
        is not inside the possibly-truncated tail. This is the only
        mechanism in the package that licenses a scoped absence claim,
        and it is a locator check, not a parse: it does not verify that
        what follows the name is a well-formed value."""
        needle = (name or "").strip().casefold()
        if not needle:
            return False
        haystack = self.excerpt.casefold()
        limit = self.tail_begins_at
        start = haystack.find(needle)
        while start != -1:
            if start + len(needle) <= limit:
                return True
            start = haystack.find(needle, start + 1)
        return False

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
class ComparisonRequest:
    """Everything the component is allowed to reason over. There is no
    other input channel: no retrieval, no tool use, no browsing."""

    request_id: str
    issuer_id: str
    issuer_name: str
    current: ComparisonEvidence
    prior: ComparisonEvidence | None
    comparability_status: ComparisonStatus
    comparison_basis: str = ""
    added_categories: tuple[str, ...] = ()
    removed_categories: tuple[str, ...] = ()

    def violations(self) -> tuple[str, ...]:
        out: list[str] = []
        for name in ("request_id", "issuer_id", "issuer_name"):
            if not str(getattr(self, name) or "").strip():
                out.append(f"blank_{name}")
        if not isinstance(self.comparability_status, ComparisonStatus):
            out.append("invalid_comparability_status")
        out.extend(f"current.{v}" for v in self.current.violations())
        if self.prior is not None:
            out.extend(f"prior.{v}" for v in self.prior.violations())
        return tuple(out)

    def evidence_numeric_tokens(self) -> frozenset[str]:
        """Every figure the two excerpts contain, plus the filing dates
        the request itself carries — the admissible universe for any
        number appearing anywhere in the output."""
        tokens: set[str] = set()
        for side in (self.current, self.prior):
            if side is None:
                continue
            tokens |= numeric_tokens(side.excerpt)
            tokens |= numeric_tokens(side.filed_at)
        return frozenset(tokens)


@dataclass(frozen=True)
class SourceReference:
    """A pointer a reader can follow to re-check a statement. Minted from
    the request's own evidence — a reference to a document the request
    never carried is a validation failure, not a citation."""

    document_id: str
    source_url: str
    filed_at: str
    locator: str = ""


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
    # deliberately did not. `compared_fields` is required for a scoped
    # absence outcome and is what bounds that outcome's meaning.
    compared_fields: tuple[str, ...] = ()
    fields_not_compared: tuple[str, ...] = ()
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
                "references": [
                    {"document_id": r.document_id, "source_url": r.source_url,
                     "filed_at": r.filed_at, "locator": r.locator}
                    for r in s.references
                ],
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
    """Any figure in a claim-bearing field must appear in the evidence.

    Applied to EVERY field that carries a claim, not only to the typed
    statement lists: a fabricated number is no less fabricated for
    sitting in the summary or the interpretation, where neither a
    support level nor a reference would have been required of it."""
    unknown = sorted(numeric_tokens(text) - admissible)
    return [f"{prefix}.ungrounded_number:{token}" for token in unknown]


def _hedge_violations(prefix: str, statement: ComparisonStatement, excerpts: dict[str, str]) -> list[str]:
    """Flags a figure whose source context hedges it while the statement
    does not.

    A keyword-proximity heuristic, and documented as such in
    UNVERIFIABLE_BY_VALIDATION: it fires on omission or reversal of a
    listed hedge near a repeated figure, and is silent about hedging
    phrased some other way or attached to some other subject."""
    if _has_hedge(statement.text):
        return []
    out: list[str] = []
    for token in sorted(numeric_tokens(statement.text)):
        for ref in statement.references:
            excerpt = excerpts.get(ref.document_id)
            if not excerpt:
                continue
            for match in _NUMERIC_RUN.finditer(excerpt):
                if _SEPARATORS.sub("", match.group(0)) != token:
                    continue
                start = max(0, match.start() - _HEDGE_WINDOW_CHARS)
                end = min(len(excerpt), match.end() + _HEDGE_WINDOW_CHARS)
                if _has_hedge(excerpt[start:end]):
                    out.append(f"{prefix}.conditional_language_not_preserved:{token}")
                    return out
    return out


def _scoped_absence_violations(result: FilingComparison, request: ComparisonRequest) -> list[str]:
    """What a `NO_DIFFERENCE_IN_COMPARED_FIELDS` result must establish.

    The scope must be named, and every named field must be locatable
    with its value intact on BOTH sides. Completeness is not sufficient
    and is not consulted directly here — `locates_field` consults it, to
    exclude a field whose value may sit in a truncated tail."""
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
        if not request.current.locates_field(name):
            out.append(f"compared_fields[{index}].not_locatable_in_current_excerpt")
        if not request.prior.locates_field(name):
            out.append(f"compared_fields[{index}].not_locatable_in_prior_excerpt")
    return out


def validate_comparison(result: FilingComparison, request: ComparisonRequest) -> tuple[str, ...]:
    """Every violation, never a raise — the same exhaustive style
    `validate_claim_proposal` uses. A non-empty result means the output
    is rejected outright; it is never retried against a looser schema.

    What this validates is MECHANICAL: shape, caps, support levels, that
    every reference resolves to a document the request actually carried,
    that every figure in every claim-bearing field appears in the
    evidence, that a named compared field is locatable on both sides,
    and that a hedged figure is not restated as settled.

    It does not and cannot establish that a statement is a true reading
    of the document. A correctly-formed citation proves the source
    exists and was in scope, not that the sentence beside it follows
    from it; grounding proves a figure occurs in the evidence, not that
    it was used correctly. The checks this validator CANNOT perform are
    enumerated in `UNVERIFIABLE_BY_VALIDATION` and are attached to every
    claim-bearing result, so a passing validation is never reported as
    semantic correctness."""
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

    # Grounding over EVERY claim-bearing field, including the two free
    # text fields and the two string lists.
    admissible = request.evidence_numeric_tokens()
    violations.extend(_grounding_violations("summary", result.summary, admissible))
    violations.extend(_grounding_violations("interpretation", result.interpretation, admissible))
    for index, text in enumerate(result.qualifications):
        violations.extend(_grounding_violations(f"qualifications[{index}]", text, admissible))
    for index, text in enumerate(result.uncertainties):
        violations.extend(_grounding_violations(f"uncertainties[{index}]", text, admissible))

    excerpts = {
        side.document_id: side.excerpt
        for side in (request.current, request.prior) if side is not None
    }
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
            violations.extend(_grounding_violations(prefix, statement.text, admissible))
            violations.extend(_hedge_violations(prefix, statement, excerpts))

    # An evidenced CHANGE needs a prior document to have changed from.
    if result.evidenced_changes and request.prior is None:
        violations.append("evidenced_change_without_prior_evidence")

    return tuple(violations)
