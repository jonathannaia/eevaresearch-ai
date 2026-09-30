"""EevaResearch Phase 4, Step 4B-1 (design/DECISIONS.md) — a pure(-ish)
batch orchestration helper coordinating the existing selector/factory/
validator into one bounded, deterministic sweep over a caller-supplied
candidate set, for a future worker-only autonomous Research Case
creation step (Step 4B-2, not implemented here).

This module performs no I/O of its own accord: it never loads
candidates itself (the caller supplies them), never constructs a
repository or connection, never imports `backend_factory` or any data-
access/store/repository module, and never persists anything — the one
externally-visible side effect it can have is calling the caller-
injected `existing_case_ids` callable **exactly once** per nonempty
survivor batch (never once per candidate) to ask "which of these
already exist," treated as fail-closed if it raises or returns
something malformed. `prepare_research_case_bundles()` never reads the
system clock, never generates a random value, and never mutates its
`candidates`/`config`/`existing_case_ids` inputs.

`select_research_lead()` is called exactly once per evaluated
candidate, always with an empty `frozenset()` as its own
`already_triggered_case_ids` argument — deliberately, since this
module performs the *actual* dedup decision itself, externally, using
the one bulk `existing_case_ids` read, after the selector has already
told it each survivor's deterministic case id. This avoids either
calling the selector twice per candidate or re-implementing its
case-ID derivation a third time (Step 4A-1's `research_lead_selection.
_build_case_id_v1` and Step 4A-2's `research_lead_factory.
_build_case_id` are each already documented, tested, standalone
duplicates of the one real algorithm — a third copy here would be
unnecessary given the selector already computes and returns it).

Zero relationship/dependency assertions are ever produced — every
returned bundle comes unchanged from `build_research_case_bundle_from_
lead()`, which hard-codes `assertions=()` by its own design. This
module never persists a bundle, never calls a scan/pipeline/source
client, and never wires into any runtime entry point — nothing in this
step is reachable from `scripts/radar_worker.py`, `scripts/run_scan.py`,
any UI page, or app startup."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Collection, Sequence

from src.logic.research_case_validation import ResearchCaseBundle, validate_research_case_bundle
from src.logic.research_lead_factory import build_research_case_bundle_from_lead
from src.logic.research_lead_selection import (
    REJECTION_REASONS,
    LeadPriority,
    LeadSelectionResult,
    ResearchLeadSelectionConfig,
    select_research_lead,
)
from src.models.models import CandidateSignal, CandidateStatus

_MIN_MAX_CANDIDATES = 1
_MAX_MAX_CANDIDATES = 10

# Observability-only reason tokens this module adds on top of the
# selector's own closed vocabulary. Both are fixed literals, same
# safety property as every member of REJECTION_REASONS.
#
# `other` is the allowlist's fallback: any reason value this module
# does not recognize -- a future selector token, a malformed entry, a
# non-string -- is counted as `other` rather than passed through, so no
# caller-supplied or source-controlled text can reach a log line even
# if the selector's vocabulary changes later.
_UNRECOGNIZED_REASON = "other"
# This module's own rejection, not the selector's: the selector said
# QUALIFIED/HIGH_SIGNAL but returned a blank case_id. That selection's
# `reasons` are success-path tokens (which include a source-derived
# `category:<slug>` entry) and must never be counted as qualification
# rejection reasons, so this fixed token stands in for them.
_QUALIFIED_WITHOUT_CASE_ID = "qualified_without_case_id"

_HISTOGRAM_VOCABULARY: frozenset[str] = REJECTION_REASONS | {
    _UNRECOGNIZED_REASON, _QUALIFIED_WITHOUT_CASE_ID,
}

# Canonicalization, not merely validation. Membership in a frozenset is
# decided by __hash__/__eq__, so a `str` SUBCLASS whose value equals an
# allowlisted token passes `x in vocabulary` while still carrying its
# own __str__/__format__ -- which is what an f-string would then call,
# putting arbitrary text in a log line despite the allowlist. Resolving
# through a dict instead returns the VALUE stored here, a plain `str`
# literal this module owns, and discards the caller's object entirely.
# `.get()` never invokes the key's formatting, only its hash/eq.
_CANONICAL_REJECTION_REASONS: dict[str, str] = {reason: reason for reason in REJECTION_REASONS}
_CANONICAL_HISTOGRAM_REASONS: dict[str, str] = {reason: reason for reason in _HISTOGRAM_VOCABULARY}

_HISTOGRAM_NONE = "none"
_HISTOGRAM_UNAVAILABLE = "unavailable"


def _canonical_reason(reason: object, canonical: dict[str, str]) -> str:
    """The one way a reason becomes output text: an exact plain `str`
    owned by this module, or the fixed `other` token.

    Fails closed on anything that is not a plain-comparable string --
    including a non-string, an unhashable value, and a subclass whose
    own __hash__/__eq__ misbehaves or raises."""
    if not isinstance(reason, str):
        return _UNRECOGNIZED_REASON
    try:
        return canonical.get(reason, _UNRECOGNIZED_REASON)
    except Exception:  # noqa: BLE001 — a hostile __hash__/__eq__ must not escape
        return _UNRECOGNIZED_REASON


def _is_not_qualified(priority: object) -> bool:
    """Equality, deliberately, not identity — matching the selection
    call site in prepare_research_case_bundles exactly.

    `LeadPriority` subclasses `str`, so the raw string "NOT_QUALIFIED"
    compares equal to the enum member. The call site treats such a
    selection as a rejection; this aggregator must classify it the same
    way, or the histogram would disagree with the decision it reports
    on. Fails closed to False for an object whose __eq__ raises."""
    try:
        return bool(priority == LeadPriority.NOT_QUALIFIED)
    except Exception:  # noqa: BLE001 — a hostile __eq__ must not escape
        return False


@dataclass(frozen=True)
class ResearchLeadOrchestrationConfig:
    as_of_date: str
    lookback_days: int
    max_candidates: int
    allowed_source_names: tuple[str, ...] = ("SEC EDGAR",)


@dataclass(frozen=True)
class ResearchLeadOrchestrationResult:
    bundles: tuple[ResearchCaseBundle, ...]
    evaluated_count: int
    # Wrong-shaped candidates, and candidates filtered out before ever
    # reaching select_research_lead (unrecognized source, status other
    # than NEEDS_REVIEW) — deliberately NOT folded into
    # `not_qualified_count` (see this module's own test file for why:
    # that count means "the selector itself returned NOT_QUALIFIED,"
    # never "this record never reached the selector at all").
    skipped_count: int
    not_qualified_count: int
    already_existing_count: int
    # Distinct from `already_existing_count` on purpose — a broken/
    # malformed membership check is a different failure mode than a
    # confirmed duplicate, and must never be reported as one.
    membership_check_failed_count: int
    factory_rejected_count: int
    validation_rejected_count: int
    # False only when `config` itself was invalid — every other field
    # is then forced to its zero/empty value. A caller must not infer
    # "config was invalid" from an all-zero result alone (a valid config
    # with zero eligible candidates looks identical otherwise), hence
    # this explicit, separate signal.
    config_valid: bool
    # Observability only — never read by any selection decision, and
    # additive: defaulted so every existing construction of this
    # dataclass keeps working unchanged.
    #
    # Occurrence counts of the safe, closed-vocabulary reasons behind
    # `not_qualified_count`, already sorted by reason for deterministic
    # output. Three distinct states:
    #   ()    — no candidate was rejected (renders as "none")
    #   None  — aggregation itself failed (renders as "unavailable");
    #           every other field on this result is still exact
    #   pairs — the histogram
    #
    # These are reason OCCURRENCES, not candidates: one candidate
    # failing three gates contributes three counts, so the sum may
    # exceed `not_qualified_count`. That is deliberate — a candidate
    # blocked by several gates needs all of them visible to choose a
    # repair.
    rejection_reason_counts: tuple[tuple[str, int], ...] | None = ()


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _validate_orchestration_config(config: object) -> bool:
    if not isinstance(config, ResearchLeadOrchestrationConfig):
        return False
    if not _nonblank(config.as_of_date):
        return False

    lookback_days = config.lookback_days
    if isinstance(lookback_days, bool) or not isinstance(lookback_days, int) or lookback_days < 0:
        return False

    max_candidates = config.max_candidates
    if (
        isinstance(max_candidates, bool)
        or not isinstance(max_candidates, int)
        or not (_MIN_MAX_CANDIDATES <= max_candidates <= _MAX_MAX_CANDIDATES)
    ):
        return False

    allowed = config.allowed_source_names
    if not isinstance(allowed, (tuple, list)) or not allowed or not all(_nonblank(name) for name in allowed):
        return False

    return True


def _empty_result(config_valid: bool, evaluated_count: int = 0, skipped_count: int = 0) -> ResearchLeadOrchestrationResult:
    return ResearchLeadOrchestrationResult(
        bundles=(), evaluated_count=evaluated_count, skipped_count=skipped_count,
        not_qualified_count=0, already_existing_count=0, membership_check_failed_count=0,
        factory_rejected_count=0, validation_rejected_count=0, config_valid=config_valid,
    )


def _record_rejection_reasons(counts: dict[str, int], selection: object) -> None:
    """Folds one rejected selection's reasons into `counts`.

    Allowlist, never denylist, and canonicalizing rather than merely
    validating: every key written here is a plain `str` owned by this
    module (see `_canonical_reason`), so this can only ever emit fixed
    literals regardless of what the selector returns. Mutates `counts`
    in place and returns nothing — it never inspects or alters the
    selection itself, and no caller's decision depends on it.

    Three cases, in the order they are distinguished:

      1. priority EQUALS NOT_QUALIFIED (the real selector's own
         rejection, and equally a raw string-equivalent) — count its
         reasons, which is the whole point of the histogram;
      2. priority is a real LeadPriority but some other member — the
         selector qualified it and prepare_research_case_bundles
         rejected it on its own blank-case_id guard;
      3. anything else — a malformed or hand-constructed object with no
         usable priority at all. That is not a qualified selection and
         must not be labelled as one, so it counts as `other`."""
    priority = getattr(selection, "priority", None)

    if not _is_not_qualified(priority):
        if isinstance(priority, LeadPriority):
            # Case 2 — see _QUALIFIED_WITHOUT_CASE_ID's own comment for
            # why this selection's own reasons must not be counted.
            counts[_QUALIFIED_WITHOUT_CASE_ID] = counts.get(_QUALIFIED_WITHOUT_CASE_ID, 0) + 1
        else:
            # Case 3 — malformed. `other` is the truthful bucket.
            counts[_UNRECOGNIZED_REASON] = counts.get(_UNRECOGNIZED_REASON, 0) + 1
        return

    reasons = getattr(selection, "reasons", None)
    if not isinstance(reasons, (tuple, list)) or not reasons:
        counts[_UNRECOGNIZED_REASON] = counts.get(_UNRECOGNIZED_REASON, 0) + 1
        return

    for reason in reasons:
        key = _canonical_reason(reason, _CANONICAL_REJECTION_REASONS)
        counts[key] = counts.get(key, 0) + 1


def rejection_reason_histogram(result: object) -> str:
    """The one log-safe rendering of a batch's qualification-rejection
    reasons, for a worker summary line.

    Returns `none` when nothing was rejected, `unavailable` when
    aggregation failed or the value is malformed, and otherwise
    `reason:count` pairs joined by commas in ascending reason order, so
    the same batch always renders byte-identically.

    Re-canonicalizes rather than trusting the stored value:
    `ResearchLeadOrchestrationResult` is a public dataclass a caller can
    construct by hand, so every reason is resolved back to a plain `str`
    this module owns before it reaches the output — an equal-valued
    `str` subclass cannot smuggle its own __format__ into the log line.
    Never raises."""
    try:
        counts = getattr(result, "rejection_reason_counts", None)
        if counts is None:
            return _HISTOGRAM_UNAVAILABLE
        if not isinstance(counts, (tuple, list)):
            return _HISTOGRAM_UNAVAILABLE

        merged: dict[str, int] = {}
        for entry in counts:
            if not isinstance(entry, (tuple, list)) or len(entry) != 2:
                return _HISTOGRAM_UNAVAILABLE
            reason, count = entry
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                return _HISTOGRAM_UNAVAILABLE
            key = _canonical_reason(reason, _CANONICAL_HISTOGRAM_REASONS)
            # int() for the same reason the reason is canonicalized: an
            # `int` subclass can override __format__, and relying on
            # `0 + count` to launder it back to a plain int is not safe
            # (a subclass __radd__ takes priority and may return self).
            merged[key] = merged.get(key, 0) + int(count)

        if not merged:
            return _HISTOGRAM_NONE
        return ",".join(f"{reason}:{count}" for reason, count in sorted(merged.items()))
    except Exception:  # noqa: BLE001 — observability must never break a provider tick
        return _HISTOGRAM_UNAVAILABLE


def _source_recognized(candidate: CandidateSignal, allowed_source_names: Sequence[str]) -> bool:
    filing = getattr(candidate, "filing", None)
    source_name = getattr(filing, "source_name", None)
    if not _nonblank(source_name):
        return False
    allowed_stripped = {name.strip() for name in allowed_source_names if _nonblank(name)}
    return source_name.strip() in allowed_stripped


def _status_needs_review(candidate: CandidateSignal) -> bool:
    status = getattr(candidate, "status", None)
    return isinstance(status, CandidateStatus) and status is CandidateStatus.NEEDS_REVIEW


def _sort_key(candidate: CandidateSignal) -> tuple[str, str, str]:
    """Deterministic ascending sort key with safe fixed fallbacks — a
    malformed (non-string) rcept_dt/rcept_no/id never raises a
    comparison TypeError; it simply sorts as if that field were blank."""
    filing = getattr(candidate, "filing", None)
    rcept_dt = getattr(filing, "rcept_dt", None)
    rcept_no = getattr(filing, "rcept_no", None)
    candidate_id = getattr(candidate, "id", None)
    return (
        rcept_dt if isinstance(rcept_dt, str) else "",
        rcept_no if isinstance(rcept_no, str) else "",
        candidate_id if isinstance(candidate_id, str) else "",
    )


def _normalized_membership(raw: object) -> frozenset[str] | None:
    """None signals a malformed membership result — not a collection at
    all, or one containing a non-string entry. The caller treats this
    identically to the membership callable raising."""
    if not isinstance(raw, (set, frozenset, list, tuple)):
        return None
    if not all(isinstance(item, str) for item in raw):
        return None
    return frozenset(raw)


def prepare_research_case_bundles(
    candidates: Collection[CandidateSignal],
    existing_case_ids: Callable[[Sequence[str]], Collection[str]],
    config: ResearchLeadOrchestrationConfig,
) -> ResearchLeadOrchestrationResult:
    """Pure aside from the one injected `existing_case_ids` call. Never
    raises for malformed `candidates`/`config` input. See module
    docstring for the full non-goal list — this never persists a
    bundle, never calls a scan/pipeline/source client, and never wires
    into any runtime entry point."""
    if not _validate_orchestration_config(config):
        return _empty_result(config_valid=False)

    try:
        candidate_list = list(candidates)
    except TypeError:
        candidate_list = []

    skipped_count = 0
    eligible: list[CandidateSignal] = []
    for candidate in candidate_list:
        if not isinstance(candidate, CandidateSignal):
            skipped_count += 1
            continue
        if not _source_recognized(candidate, config.allowed_source_names):
            skipped_count += 1
            continue
        if not _status_needs_review(candidate):
            skipped_count += 1
            continue
        eligible.append(candidate)

    eligible.sort(key=_sort_key)
    capped = eligible[: config.max_candidates]
    evaluated_count = len(capped)

    selector_config = ResearchLeadSelectionConfig(
        as_of_date=config.as_of_date, lookback_days=config.lookback_days,
        recognized_source_names=tuple(config.allowed_source_names),
    )

    not_qualified_count = 0
    survivors: list[tuple[CandidateSignal, LeadSelectionResult]] = []
    # Observability only. `None` means aggregation failed and the
    # histogram renders "unavailable" — the selection loop's own
    # decisions are unaffected either way.
    reason_counts: dict[str, int] | None = {}
    for candidate in capped:
        selection = select_research_lead(candidate, frozenset(), selector_config)
        if selection.priority == LeadPriority.NOT_QUALIFIED or not _nonblank(selection.case_id):
            not_qualified_count += 1
            if reason_counts is not None:
                try:
                    _record_rejection_reasons(reason_counts, selection)
                except Exception:  # noqa: BLE001 — a logging aggregate must never change a selection outcome
                    reason_counts = None
            continue
        survivors.append((candidate, selection))

    rejection_reason_counts = None if reason_counts is None else tuple(sorted(reason_counts.items()))

    if not survivors:
        return ResearchLeadOrchestrationResult(
            bundles=(), evaluated_count=evaluated_count, skipped_count=skipped_count,
            not_qualified_count=not_qualified_count, already_existing_count=0,
            membership_check_failed_count=0, factory_rejected_count=0, validation_rejected_count=0,
            config_valid=True, rejection_reason_counts=rejection_reason_counts,
        )

    candidate_case_ids = [selection.case_id for _candidate, selection in survivors]
    try:
        existing_raw = existing_case_ids(candidate_case_ids)
    except Exception:  # noqa: BLE001 — any membership-callable failure fails closed for the whole survivor batch
        existing_raw = None
        membership_ok = False
    else:
        membership_ok = True

    existing_ids: frozenset[str] | None = _normalized_membership(existing_raw) if membership_ok else None
    if existing_ids is None:
        return ResearchLeadOrchestrationResult(
            bundles=(), evaluated_count=evaluated_count, skipped_count=skipped_count,
            not_qualified_count=not_qualified_count, already_existing_count=0,
            membership_check_failed_count=len(survivors), factory_rejected_count=0,
            validation_rejected_count=0, config_valid=True,
            rejection_reason_counts=rejection_reason_counts,
        )

    already_existing_count = 0
    factory_rejected_count = 0
    validation_rejected_count = 0
    bundles: list[ResearchCaseBundle] = []
    for candidate, selection in survivors:
        if selection.case_id in existing_ids:
            already_existing_count += 1
            continue
        bundle = build_research_case_bundle_from_lead(candidate, selection)
        if bundle is None:
            factory_rejected_count += 1
            continue
        issues = validate_research_case_bundle(bundle)
        if issues:
            validation_rejected_count += 1
            continue
        bundles.append(bundle)

    return ResearchLeadOrchestrationResult(
        bundles=tuple(bundles), evaluated_count=evaluated_count, skipped_count=skipped_count,
        not_qualified_count=not_qualified_count, already_existing_count=already_existing_count,
        membership_check_failed_count=0, factory_rejected_count=factory_rejected_count,
        validation_rejected_count=validation_rejected_count, config_valid=True,
        rejection_reason_counts=rejection_reason_counts,
    )
