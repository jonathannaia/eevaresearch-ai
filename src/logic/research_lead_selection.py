"""EevaResearch Phase 4, Step 4A-1 (design/DECISIONS.md) — a pure,
deterministic selector deciding whether an already-persisted Radar
`CandidateSignal` should become an automatic Research Case lead.

This module performs no I/O of any kind: no file/JSON/SQLite/Postgres
access, no persistence call, no network/HTTP/source fetch, no scan, no
LLM/model/translation/entity-resolution call, no logging, no random
value, no subprocess, no environment-variable read, and no system-clock
read — `ResearchLeadSelectionConfig.as_of_date` is the only notion of
"now" this module ever has, always supplied by the caller. It never
constructs a `ResearchCase`/`ResearchEvidenceItem`, never mutates its
inputs, and never queries anything — `already_triggered_case_ids` is a
plain caller-supplied membership collection, not a lookup this module
performs itself.

Case-ID compatibility (Phase 4 Step 4A audit, design/DECISIONS.md,
Section 6): the real, canonical deterministic case-ID factory is
`src.data_access.research_store.build_case_id()`. This module
deliberately does NOT import it — `research_store` lives in
`src.data_access` (a data-access/persistence package) and this step's
approval explicitly forbids importing any data-access/repository/store
module here, to keep this module free of any I/O-capable package
coupling regardless of whether the specific imported function happens
to be pure. Instead, `_build_case_id_v1` below is a small, deliberately
minimal, byte-for-byte-compatible duplicate of that exact algorithm
(sha256 hex digest of `f"{trigger_source_type}|{trigger_source_id}|
{created_at}"`, truncated to the first 24 hex characters, prefixed
`"case-"`) — verified directly against the real factory in
tests/test_research_lead_selection.py
(test_case_id_matches_real_research_case_factory_exactly). If the real
factory's algorithm ever changes, that test fails and this copy must be
updated to match — it is not an independent ID scheme.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Collection

from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent

_ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Case-ID compatibility constant — must stay identical to
# src.data_access.research_store._ID_DIGEST_CHARS. See module docstring.
_ID_DIGEST_CHARS = 24

_QUALIFYING_CONFIDENCE_LEVELS = frozenset({"Moderate", "High"})
_AMENDMENT_MARKER = "amendment_or_correction"

_POSITIVE_REASONS: tuple[str, ...] = (
    "source_recognized",
    "candidate_identity_present",
    "issuer_provenance_present",
    "source_url_present",
    "excerpt_extracted",
    "rule_categories_present",
    "confidence_qualified",
    "status_needs_review",
    "receipt_date_within_lookback",
    "case_not_previously_triggered",
)


class LeadPriority(str, Enum):
    HIGH_SIGNAL = "HIGH_SIGNAL"
    QUALIFIED = "QUALIFIED"
    NOT_QUALIFIED = "NOT_QUALIFIED"


@dataclass(frozen=True)
class ResearchLeadSelectionConfig:
    as_of_date: str
    lookback_days: int
    recognized_source_names: tuple[str, ...] = (
        "SEC EDGAR",
        "OpenDART / DART",
        "EDINET",
    )


@dataclass(frozen=True)
class LeadSelectionResult:
    priority: LeadPriority
    reasons: tuple[str, ...]
    normalized_categories: tuple[str, ...]
    case_id: str | None


def _rejected(reasons: tuple[str, ...], normalized_categories: tuple[str, ...] = ()) -> LeadSelectionResult:
    return LeadSelectionResult(
        priority=LeadPriority.NOT_QUALIFIED, reasons=reasons,
        normalized_categories=normalized_categories, case_id=None,
    )


def _nonblank_str(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _parse_iso_date(value: object) -> date | None:
    """Strict `YYYY-MM-DD` only — never the broader set of shapes a bare
    `date.fromisoformat()` call accepts on newer Python runtimes (e.g. a
    compact `YYYYMMDD` form), and never a datetime string."""
    if not isinstance(value, str) or not _ISO_DATE_PATTERN.match(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _build_case_id_v1(trigger_source_type: str, trigger_source_id: str, created_at: str) -> str:
    """Byte-for-byte compatible with research_store.build_case_id() — see
    module docstring for why this is a deliberate, tested duplicate
    rather than an import."""
    digest = hashlib.sha256(f"{trigger_source_type}|{trigger_source_id}|{created_at}".encode("utf-8")).hexdigest()
    return f"case-{digest[:_ID_DIGEST_CHARS]}"


def _validate_config(config: ResearchLeadSelectionConfig) -> tuple[str, ...]:
    reasons: list[str] = []
    if not isinstance(config, ResearchLeadSelectionConfig):
        return ("invalid_config",)

    if _parse_iso_date(config.as_of_date) is None:
        reasons.append("invalid_as_of_date")

    lookback_days = config.lookback_days
    if isinstance(lookback_days, bool) or not isinstance(lookback_days, int) or lookback_days < 0:
        reasons.append("invalid_lookback_days")

    recognized = config.recognized_source_names
    if (
        not isinstance(recognized, (tuple, list))
        or not any(_nonblank_str(name) for name in recognized)
    ):
        reasons.append("invalid_recognized_source_names")

    return tuple(reasons)


def _normalize_categories(matched_rules: object) -> tuple[tuple[str, ...], bool, bool]:
    """Returns (normalized_categories, container_valid, amendment_present).

    `container_valid` is False when `matched_rules` isn't a list/tuple of
    strings at all — the caller must then report `invalid_matched_rules`
    and must not treat `amendment_present` as meaningful (it is always
    False in that case; the caller never iterates the raw container for
    the amendment marker either — see this module's own docstring on the
    "after an invalid matched_rules container, do not perform category/
    amendment iteration beyond reporting that one issue" contract)."""
    if not isinstance(matched_rules, (list, tuple)) or not all(isinstance(rule, str) for rule in matched_rules):
        return (), False, False

    categories: set[str] = set()
    amendment_present = False
    for raw_rule in matched_rules:
        token = raw_rule.strip()
        if not token:
            continue
        if token.casefold() == _AMENDMENT_MARKER:
            amendment_present = True
            continue
        if ":" not in token:
            # Every real matched_rules token carrying a category uses a
            # "category:..." shape on all three sources (see Phase 4
            # Step 4A audit, Section 2) — a colon-free, non-amendment
            # token is not a recognized category-bearing shape and is
            # silently discarded, never guessed at.
            continue
        category = token.split(":", 1)[0].strip().lower()
        if category:
            categories.add(category)

    return tuple(sorted(categories)), True, amendment_present


def select_research_lead(
    candidate: CandidateSignal,
    already_triggered_case_ids: Collection[str],
    config: ResearchLeadSelectionConfig,
) -> LeadSelectionResult:
    """Pure, deterministic, no I/O. See module docstring for the full
    non-goal list. Same `(candidate, already_triggered_case_ids, config)`
    always returns an equality-identical result — reasons/categories are
    always built as tuples in a fixed order, never from raw set/dict
    iteration."""
    config_reasons = _validate_config(config)
    if config_reasons:
        return _rejected(config_reasons)

    if not isinstance(candidate, CandidateSignal):
        return _rejected(("invalid_candidate",))

    reasons: list[str] = []

    candidate_id = getattr(candidate, "id", None)
    candidate_id_ok = _nonblank_str(candidate_id)
    if not candidate_id_ok:
        reasons.append("blank_candidate_id")

    filing = getattr(candidate, "filing", None)
    filing_ok = isinstance(filing, FilingEvent)
    if not filing_ok:
        reasons.append("invalid_filing")

    if filing_ok:
        source_name = filing.source_name
        if not _nonblank_str(source_name):
            reasons.append("blank_source_name")
        else:
            recognized_stripped = {name.strip() for name in config.recognized_source_names if _nonblank_str(name)}
            if source_name.strip() not in recognized_stripped:
                # A fixed, safe reason — never the caller-supplied
                # source_name text itself (see this step's explicit
                # "reason strings must be fixed/safe" instruction).
                reasons.append("source_not_recognized")

        if not _nonblank_str(getattr(filing, "rcept_no", None)):
            reasons.append("blank_document_id")

        if not _nonblank_str(getattr(filing, "corp_code", None)):
            reasons.append("blank_issuer_id")

        if not _nonblank_str(getattr(filing, "corp_name", None)):
            reasons.append("blank_issuer_name")

        if not _nonblank_str(getattr(filing, "source_url", None)):
            reasons.append("blank_source_url")

    extraction_state = getattr(candidate, "extraction_state", None)
    if not isinstance(extraction_state, ExtractionState):
        reasons.append("invalid_extraction_state")
    elif extraction_state is not ExtractionState.EXTRACTED:
        reasons.append("excerpt_not_extracted")

    if not _nonblank_str(getattr(candidate, "excerpt_original", None)):
        reasons.append("blank_original_excerpt")

    normalized_categories, matched_rules_container_valid, amendment_present = _normalize_categories(
        getattr(candidate, "matched_rules", None),
    )
    if not matched_rules_container_valid:
        reasons.append("invalid_matched_rules")
    elif not normalized_categories:
        reasons.append("no_rule_categories")

    if matched_rules_container_valid and amendment_present:
        reasons.append(_AMENDMENT_MARKER)

    confidence = getattr(candidate, "confidence", None)
    if not _nonblank_str(confidence):
        reasons.append("invalid_confidence")
    elif confidence not in _QUALIFYING_CONFIDENCE_LEVELS:
        reasons.append("confidence_not_qualified")

    status = getattr(candidate, "status", None)
    if not isinstance(status, CandidateStatus):
        reasons.append("invalid_candidate_status")
    elif status is not CandidateStatus.NEEDS_REVIEW:
        reasons.append("status_not_needs_review")

    receipt_date: date | None = None
    if filing_ok:
        receipt_date = _parse_iso_date(getattr(filing, "rcept_dt", None))
        if receipt_date is None:
            reasons.append("invalid_receipt_date")
        else:
            as_of_date = _parse_iso_date(config.as_of_date)
            days_since_receipt = (as_of_date - receipt_date).days
            if days_since_receipt < 0:
                reasons.append("receipt_date_in_future")
            elif days_since_receipt > config.lookback_days:
                reasons.append("receipt_date_outside_lookback")

    # Case-ID derivation — attempted whenever `candidate` itself is a
    # real CandidateSignal (the one prerequisite this needs), regardless
    # of any other gate's outcome, per this step's "return all
    # applicable candidate-gate reasons" contract. Never attempted for a
    # non-CandidateSignal input (already returned above).
    detected_at: str | None = None
    state_history = getattr(candidate, "state_history", None)
    if isinstance(state_history, (list, tuple)):
        for transition in state_history:
            transition_status = getattr(transition, "status", None)
            if isinstance(transition_status, CandidateStatus) and transition_status is CandidateStatus.CANDIDATE_DETECTED:
                candidate_at = getattr(transition, "at", None)
                if _nonblank_str(candidate_at):
                    detected_at = candidate_at
                break

    candidate_case_id: str | None = None
    if detected_at is None:
        reasons.append("missing_candidate_detected_timestamp")
    elif candidate_id_ok:
        candidate_case_id = _build_case_id_v1("radar", candidate_id, detected_at)

        try:
            already_triggered = candidate_case_id in already_triggered_case_ids
        except Exception:  # noqa: BLE001 — any malformed/broken membership collection fails closed
            reasons.append("invalid_already_triggered_case_ids")
        else:
            if already_triggered:
                reasons.append("case_already_triggered")

    if reasons:
        return _rejected(tuple(reasons), normalized_categories)

    is_high_signal = confidence == "High" and len(normalized_categories) >= 2
    priority = LeadPriority.HIGH_SIGNAL if is_high_signal else LeadPriority.QUALIFIED
    priority_marker = "priority_high_signal" if is_high_signal else "priority_qualified"

    final_reasons = _POSITIVE_REASONS + tuple(f"category:{category}" for category in normalized_categories) + (priority_marker,)
    return LeadSelectionResult(
        priority=priority, reasons=final_reasons,
        normalized_categories=normalized_categories, case_id=candidate_case_id,
    )
