"""Radar evidence-packet foundation, Phase 3, Step 1 (design/DECISIONS.md)
— pure, deterministic "what changed versus the prior comparable
disclosure" logic. This is a detection-category comparison only: it
compares the normalized set of detection-rule categories already
attached to a CandidateSignal's `matched_rules` between the current
candidate and a strictly earlier, strictly comparable prior candidate.
It never claims a material business, financial, supply-chain, or
semantic change — CHANGE_DETECTED means only "the deterministic
rule-category set differs," nothing more.

No I/O of any kind: every input (the current candidate, the pool of
candidate prior context to choose from, and `computed_at`) is supplied
by the caller. This module never reads wall-clock time, never queries
JSON/SQLite/Postgres/any repository, never fetches a document, and never
mutates any input it's given. Persistence, scheduling, and UI exposure
are explicitly out of scope for this step — see design/DECISIONS.md's
Phase 3 approval for the full staged plan.

Deliberately does NOT import src.data_access.edgar.edgar_rules (or the
DART/EDINET equivalents): this module's import surface is limited to
src.models.models (for CandidateSignal/ExtractionState, the shared
types this comparison operates over) plus the standard library only.
Two small pieces of EDGAR-specific normalization that would otherwise
naturally reuse edgar_rules.py (SEC form-type alias resolution and
8-K item-number extraction from matched_rules) are therefore mirrored
locally below rather than imported — see _EDGAR_FORM_ALIASES and
_edgar_item_numbers's own docstrings for the exact correspondence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Sequence

from src.models.models import CandidateSignal, ExtractionState

_SEC_EDGAR = "SEC EDGAR"
_OPENDART = "OpenDART / DART"
_EDINET = "EDINET"

# Versioned, so a later change to the comparison method is distinguishable
# from this one in any stored/displayed result.
COMPARISON_BASIS = "matched_rules_set_diff:v1"

_PERIOD_LIMITATION = "Comparable reporting period is not available in current metadata."

# Mirrors each source's own independently-defined MAX_EXCERPT_CHARS
# constant (dart/document_extractor.py, edgar/document_extractor.py,
# edinet/document_extractor.py all independently set this to 600) —
# not imported, since those are extraction-pipeline modules, out of this
# module's import scope. Used only to flag a possibly-incomplete excerpt
# as a limitation, never to re-truncate or otherwise touch the text.
_KNOWN_EXCERPT_TRUNCATION_BOUND = 600

_EDGAR_AMENDMENT_LIMITATION = (
    "Filing form type indicates an amendment/correction (SEC '/A' suffix); "
    "no structural link to a specific original filing is available."
)
_DART_AMENDMENT_LIMITATION = (
    "Filing carries DART's amendment/correction marker ([기재정정]); "
    "no structural link to a specific original filing is available."
)

# Mirrors src.data_access.edgar.edgar_rules._FORM_ALIASES exactly (not
# imported — see module docstring). SEC's real data returns the
# spelled-out form in some cases, per that module's own documented
# live-pull finding; without this, "SC 13D" and "SCHEDULE 13D" would be
# wrongly treated as different families.
_EDGAR_FORM_ALIASES: dict[str, str] = {
    "SCHEDULE 13D": "SC 13D",
    "SCHEDULE 13D/A": "SC 13D/A",
    "SCHEDULE 13G": "SC 13G",
    "SCHEDULE 13G/A": "SC 13G/A",
}

_EDGAR_ITEM_NUMBER_PATTERN = re.compile(r"8-K item (\d{1,2}\.\d{2})")

# DART's own title-prefix amendment marker — see
# src.data_access.dart.dart_rules.AMENDMENT_MARKER / evaluate_report_name,
# which appends this literal token (never a category-prefixed one) to
# matched_rules. Duplicated as a literal here (not imported) for the same
# import-isolation reason as the EDGAR mirrors above.
_DART_AMENDMENT_MARKER_TOKEN = "amendment_or_correction"


class ComparisonStatus(str, Enum):
    NOT_AVAILABLE = "NOT_AVAILABLE"
    NOT_COMPARABLE = "NOT_COMPARABLE"
    NO_MATERIAL_CHANGE = "NO_MATERIAL_CHANGE"
    CHANGE_DETECTED = "CHANGE_DETECTED"


@dataclass(frozen=True)
class ComparisonResult:
    """Source-agnostic, additive, in-memory-only result of one comparison
    attempt. Every field beyond the three always-present ones defaults to
    an honest empty/None value — never fabricated — so a NOT_AVAILABLE or
    NOT_COMPARABLE result can be constructed without a prior candidate at
    all. Field order here groups the three fields every result always
    carries first (a dataclass constraint: fields without defaults must
    precede fields with defaults), followed by the rest in the same
    grouping the approved design used."""

    comparison_status: str  # one of ComparisonStatus's values
    comparison_basis: str
    computed_at: str
    prior_document_id: str | None = None
    prior_filed_at: str | None = None
    added_categories: tuple[str, ...] = ()
    removed_categories: tuple[str, ...] = ()
    prior_excerpt: str | None = None
    current_excerpt: str | None = None
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuleSetDiff:
    """Pure output of compare_matched_rules() — deduplicated, normalized,
    deterministically sorted category tokens only. Never the raw
    matched_rules strings, never a numeric score."""

    added: tuple[str, ...]
    removed: tuple[str, ...]


@dataclass(frozen=True)
class PriorSelection:
    """Internal result of select_prior_candidate(): either a selected
    prior candidate (with any source-specific limitations discovered
    while establishing comparability, e.g. an amendment marker), or a
    terminal status explaining why none was selected. `terminal_status`
    is None exactly when `candidate` is not None."""

    candidate: CandidateSignal | None
    terminal_status: str | None
    limitations: tuple[str, ...] = ()


def _rule_categories(matched_rules: Sequence[str]) -> frozenset[str]:
    """Extracts the normalized category token from each matched-rule
    string, using the existing 'category:...' convention shared by all
    three sources' matched_rules shapes (the same parsing
    src.models.models.build_flag_reason already applies to its own first
    matched rule). DART's literal amendment-marker token (which carries
    no category prefix) is excluded here — it is surfaced separately as
    a limitation, never treated as a comparison category. Case-
    insensitive and deduplicated via a set, so duplicate/mixed-case
    tokens can never manufacture a false category difference."""
    categories: set[str] = set()
    for rule in matched_rules:
        if rule == _DART_AMENDMENT_MARKER_TOKEN:
            continue
        category = rule.split(":", 1)[0].strip().lower()
        if category:
            categories.add(category)
    return frozenset(categories)


def _normalize_edgar_form_type(form_type: str) -> str:
    normalized = (form_type or "").strip().upper()
    return _EDGAR_FORM_ALIASES.get(normalized, normalized)


def _edgar_item_numbers(matched_rules: Sequence[str]) -> frozenset[str]:
    """Mirrors src.data_access.edgar.edgar_rules.items_from_matched_rules
    (not imported — see module docstring): extracts 8-K item numbers back
    out of matched_rules entries shaped 'category:8-K item X.XX'."""
    found: set[str] = set()
    for rule in matched_rules:
        match = _EDGAR_ITEM_NUMBER_PATTERN.search(rule)
        if match:
            found.add(match.group(1))
    return frozenset(found)


def _same_filing_identity(a: CandidateSignal, b: CandidateSignal) -> bool:
    return (
        a.filing.source_name == b.filing.source_name
        and a.filing.corp_code == b.filing.corp_code
        and a.filing.rcept_no == b.filing.rcept_no
    )


def _parse_full_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _parse_receipt_date(raw: str | None) -> date | None:
    """Mirrors the existing FilingEvent.rcept_dt parsing convention used
    elsewhere in this codebase (DART's unconverted "YYYYMMDD", EDGAR/
    EDINET's dashed ISO "YYYY-MM-DD") — stripping dashes before parsing
    as %Y%m%d handles both without needing to know which source a given
    candidate came from. A local, standard-library-only reimplementation
    (not imported from src.ui.pages.radar_inbox, which is out of this
    module's import scope) of the same safe, already-proven logic."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw.replace("-", ""), "%Y%m%d").date()
    except ValueError:
        return None


def _official_time_pair(current: CandidateSignal, other: CandidateSignal) -> tuple[object, object] | None:
    """Resolves a comparable (current_time, other_time) pair for exactly
    this pair of candidates: prefers each side's full `filed_at`
    timestamp when BOTH sides have one that parses (and the resulting
    datetimes are actually comparable — an aware/naive mismatch falls
    through rather than raising); otherwise falls back to `rcept_dt` when
    both sides have a valid date. Returns None when neither basis is
    usable for this pair — never a guessed/partial comparison."""
    current_ts = _parse_full_timestamp(current.filing.filed_at)
    other_ts = _parse_full_timestamp(other.filing.filed_at)
    if current_ts is not None and other_ts is not None:
        try:
            current_ts < other_ts
        except TypeError:
            pass
        else:
            return current_ts, other_ts

    current_date = _parse_receipt_date(current.filing.rcept_dt)
    other_date = _parse_receipt_date(other.filing.rcept_dt)
    if current_date is not None and other_date is not None:
        return current_date, other_date

    return None


def _comparable_date(value: object) -> date:
    """Normalizes a resolved time value (date or datetime) down to a
    plain date, used only for ranking multiple eligible priors against
    each other — the pairwise "strictly earlier" gate itself always uses
    the full-precision value _official_time_pair actually resolved."""
    return value.date() if isinstance(value, datetime) else value  # type: ignore[return-value]


def _baseline_eligible(current: CandidateSignal, candidate: CandidateSignal) -> bool:
    """General hard gates, source-agnostic: same source, same issuer,
    not the current candidate (by id or by filing identity), a usable
    evidence state, and a strictly earlier official time by the pairwise
    filed_at-else-rcept_dt rule."""
    if candidate.id == current.id or _same_filing_identity(candidate, current):
        return False
    if candidate.filing.source_name != current.filing.source_name:
        return False
    if candidate.filing.corp_code != current.filing.corp_code:
        return False
    if candidate.extraction_state != ExtractionState.EXTRACTED:
        return False
    if not candidate.excerpt_original:
        return False
    pair = _official_time_pair(current, candidate)
    if pair is None:
        return False
    current_time, prior_time = pair
    return prior_time < current_time


def _edgar_family_eligible(current: CandidateSignal, candidate: CandidateSignal) -> tuple[bool, tuple[str, ...]]:
    current_form = _normalize_edgar_form_type(current.filing.pblntf_ty)
    prior_form = _normalize_edgar_form_type(candidate.filing.pblntf_ty)
    if not current_form or current_form != prior_form:
        return False, ()

    if current_form == "8-K":
        current_items = _edgar_item_numbers(current.matched_rules)
        prior_items = _edgar_item_numbers(candidate.matched_rules)
        if not (current_items & prior_items):
            return False, ()

    limitations: list[str] = []
    if "/A" in current_form or "/A" in prior_form:
        limitations.append(_EDGAR_AMENDMENT_LIMITATION)
    return True, tuple(limitations)


def _dart_family_eligible(current: CandidateSignal, candidate: CandidateSignal) -> tuple[bool, tuple[str, ...]]:
    current_categories = _rule_categories(current.matched_rules)
    prior_categories = _rule_categories(candidate.matched_rules)
    if not current_categories or current_categories != prior_categories:
        return False, ()

    limitations: list[str] = []
    if (
        _DART_AMENDMENT_MARKER_TOKEN in current.matched_rules
        or _DART_AMENDMENT_MARKER_TOKEN in candidate.matched_rules
    ):
        limitations.append(_DART_AMENDMENT_LIMITATION)
    return True, tuple(limitations)


def _edinet_family_eligible(current: CandidateSignal, candidate: CandidateSignal) -> tuple[bool, tuple[str, ...]]:
    current_triple = (current.filing.ordinance_code, current.filing.pblntf_ty, current.filing.pblntf_detail_ty)
    prior_triple = (candidate.filing.ordinance_code, candidate.filing.pblntf_ty, candidate.filing.pblntf_detail_ty)
    if not all(current_triple) or not all(prior_triple):
        return False, ()
    if current_triple != prior_triple:
        return False, ()
    return True, ()


def _family_eligible(current: CandidateSignal, candidate: CandidateSignal) -> tuple[bool, tuple[str, ...]]:
    """Source-specific family/comparability gate, dispatched on the
    current candidate's own source_name. An unrecognized source_name
    (never expected in practice — EDGAR/DART/EDINET are the only three
    this pilot has) fails closed rather than guessing a family rule."""
    source = current.filing.source_name
    if source == _SEC_EDGAR:
        return _edgar_family_eligible(current, candidate)
    if source == _OPENDART:
        return _dart_family_eligible(current, candidate)
    if source == _EDINET:
        return _edinet_family_eligible(current, candidate)
    return False, ()


def select_prior_candidate(
    current: CandidateSignal,
    prior_candidates: Sequence[CandidateSignal],
) -> PriorSelection:
    """Selects the single strictly-comparable prior candidate for
    `current` out of the explicitly supplied `prior_candidates` pool, or
    explains why none qualifies. Pure — never reads any state beyond its
    two arguments, never mutates either.

    Two-tier funnel:
      1. Baseline eligibility (source-agnostic: issuer, source, identity,
         evidence state, strict time ordering) — if NO supplied candidate
         passes this tier, the result is NOT_AVAILABLE.
      2. Family eligibility (source-specific: form/category/routing-
         triple equality) applied only to baseline-eligible candidates —
         if at least one candidate passed tier 1 but none also pass tier
         2, the result is NOT_COMPARABLE.

    Among candidates passing both tiers, the most recent by official time
    is selected; if the top two share the identical resolved date with no
    other deterministic way to order them, the result is NOT_COMPARABLE
    rather than an arbitrary pick."""
    baseline_eligible = [c for c in prior_candidates if _baseline_eligible(current, c)]
    if not baseline_eligible:
        return PriorSelection(candidate=None, terminal_status=ComparisonStatus.NOT_AVAILABLE.value)

    fully_eligible: list[tuple[CandidateSignal, tuple[str, ...]]] = []
    for candidate in baseline_eligible:
        ok, limitations = _family_eligible(current, candidate)
        if ok:
            fully_eligible.append((candidate, limitations))

    if not fully_eligible:
        return PriorSelection(candidate=None, terminal_status=ComparisonStatus.NOT_COMPARABLE.value)

    def _rank_time(item: tuple[CandidateSignal, tuple[str, ...]]) -> date:
        pair = _official_time_pair(current, item[0])
        assert pair is not None  # guaranteed by baseline eligibility above
        return _comparable_date(pair[1])

    ranked = sorted(fully_eligible, key=_rank_time, reverse=True)
    if len(ranked) > 1 and _rank_time(ranked[0]) == _rank_time(ranked[1]):
        return PriorSelection(candidate=None, terminal_status=ComparisonStatus.NOT_COMPARABLE.value)

    selected, limitations = ranked[0]
    return PriorSelection(candidate=selected, terminal_status=None, limitations=limitations)


def compare_matched_rules(
    current_matched_rules: Sequence[str],
    prior_matched_rules: Sequence[str],
) -> RuleSetDiff:
    """Pure set-difference over normalized detection-rule categories
    only — never the raw matched_rules strings, never excerpt text,
    never a translation, never a timestamp. Deduplicated and
    deterministically sorted so repeated/reordered/mixed-case input
    tokens never manufacture a false difference."""
    current_categories = _rule_categories(current_matched_rules)
    prior_categories = _rule_categories(prior_matched_rules)
    added = tuple(sorted(current_categories - prior_categories))
    removed = tuple(sorted(prior_categories - current_categories))
    return RuleSetDiff(added=added, removed=removed)


def _excerpt_limitations(excerpt: str | None, label: str) -> list[str]:
    if not excerpt:
        return [f"{label} excerpt is missing or empty."]
    if len(excerpt) >= _KNOWN_EXCERPT_TRUNCATION_BOUND:
        return [f"{label} excerpt reached the known {_KNOWN_EXCERPT_TRUNCATION_BOUND}-character truncation bound and may be incomplete."]
    return []


def build_comparison_result(
    current: CandidateSignal,
    prior_candidates: Sequence[CandidateSignal],
    computed_at: str,
) -> ComparisonResult:
    """Orchestrates select_prior_candidate() + compare_matched_rules()
    into the final, source-agnostic ComparisonResult. `computed_at` is
    entirely caller-supplied — this function never reads wall-clock time.
    Never mutates `current` or any entry in `prior_candidates`."""
    selection = select_prior_candidate(current, prior_candidates)
    if selection.terminal_status is not None:
        return ComparisonResult(
            comparison_status=selection.terminal_status,
            comparison_basis=COMPARISON_BASIS,
            computed_at=computed_at,
        )

    prior = selection.candidate
    assert prior is not None  # guaranteed by terminal_status being None
    diff = compare_matched_rules(current.matched_rules, prior.matched_rules)
    status = (
        ComparisonStatus.CHANGE_DETECTED.value
        if (diff.added or diff.removed)
        else ComparisonStatus.NO_MATERIAL_CHANGE.value
    )

    limitations = list(selection.limitations)
    limitations.append(_PERIOD_LIMITATION)
    limitations.extend(_excerpt_limitations(current.excerpt_original, "Current"))
    limitations.extend(_excerpt_limitations(prior.excerpt_original, "Prior"))

    return ComparisonResult(
        comparison_status=status,
        comparison_basis=COMPARISON_BASIS,
        computed_at=computed_at,
        prior_document_id=prior.filing.rcept_no,
        prior_filed_at=prior.filing.filed_at,
        added_categories=diff.added,
        removed_categories=diff.removed,
        prior_excerpt=prior.excerpt_original,
        current_excerpt=current.excerpt_original,
        limitations=tuple(limitations),
    )
