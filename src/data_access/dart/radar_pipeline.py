"""Bounded, idempotent Radar orchestration (Korea DART pilot) — the one
reusable entry point that connects scan_service (disclosure scan +
FilingEvent/CandidateSignal creation), document_service (retrieval +
extraction), and translation_service (DeepL) into a single pipeline run.

Deliberately does NOT retrieve/extract/translate every candidate by
default: only candidates meeting the confidence threshold are eligible,
and only up to a strict per-run processing budget are actually
processed. Everything else is left as CANDIDATE_DETECTED or explicitly
marked PROCESSING_DEFERRED — nothing is dropped, overwritten, or
silently ignored; a deferred candidate is picked up by the *next*
pipeline run via candidate_store.py, without needing to be rediscovered
by a fresh scan.

No Radar Inbox UI, no Scan button, and no scheduling live here — this is
the backend seam a later milestone's UI calls into.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.config.tracked_companies import TrackedCompany
from src.data_access.dart import candidate_store, document_service, ownership_materiality, scan_service
from src.data_access.dart.candidate_store import CandidatePersistence
from src.data_access.dart.client import DartClient
from src.data_access.dart.document_extractor import assess_excerpt_quality
from src.data_access.translation.interfaces import TranslationProvider
from src.data_access.translation.translation_service import translate_cached
from src.models.models import (
    CandidateSignal,
    CandidateStatus,
    ExtractionState,
    StateTransition,
    TranslationState,
)

# Only candidates at these detection-confidence levels enter the
# document/extraction/translation pipeline. "Low" is never produced by
# the current rule engine (dart_rules.py stays a plain FilingEvent below
# that bar) — kept explicit here for forward-compatibility rather than
# assuming that will always be true.
_PROCESSABLE_CONFIDENCE_LEVELS = frozenset({"Moderate", "High"})

DEFAULT_MAX_CANDIDATES_PER_SCAN = 5
MAX_CANDIDATES_PER_SCAN_CEILING = 10

_ELIGIBLE_STATUSES = frozenset({CandidateStatus.CANDIDATE_DETECTED, CandidateStatus.PROCESSING_DEFERRED})


def clamp_max_candidates(n: int) -> int:
    """Never returns more than MAX_CANDIDATES_PER_SCAN_CEILING regardless
    of input — the one place a future UI's budget input must pass
    through, same pattern as scan_service.clamp_lookback_days."""
    return max(1, min(n, MAX_CANDIDATES_PER_SCAN_CEILING))


@dataclass(frozen=True)
class ScanReport:
    """A structured, JSON-serializable summary of one pipeline run —
    counts and safe strings only, never raw provider responses, document
    contents, or secrets. Suitable for a future Radar Inbox to render
    directly."""

    scan_id: str
    started_at: str
    completed_at: str
    companies: tuple[str, ...]
    source: str
    bgn_de: str
    end_de: str
    filings_discovered: int  # new + already-seen this run, across all companies
    new_filing_events: int
    candidates_detected: int  # newly created this run
    candidates_processed: int  # went through retrieval/extraction/translation this run
    candidates_deferred: int  # eligible but skipped this run due to the processing budget
    documents_retrieved: int  # successful, non-cached document fetches this run
    documents_extracted: int  # successful, non-cached extractions this run
    # Successful translations (title + excerpt combined) confirmed this
    # run — includes cache hits, since "a translation exists and is
    # ready" is the meaningful signal, not "a fresh API call happened."
    translations_completed: int
    already_seen_count: int
    no_data_count: int  # companies whose search legitimately found zero disclosures this window
    errors_by_category: dict[str, int]
    cache_hits: int  # document-retrieval cache hits this run
    warnings: tuple[str, ...]  # safe, human-readable — never a raw exception or secret


def _transition(candidate: CandidateSignal, status: CandidateStatus, detail: str = "") -> CandidateSignal:
    candidate.status = status
    candidate.state_history.append(StateTransition(status=status, at=datetime.now(timezone.utc).isoformat(), detail=detail))
    return candidate


def process_candidate(
    client: DartClient,
    translation_provider: TranslationProvider,
    candidate: CandidateSignal,
    cache_dir: Path,
    counters: dict[str, int],
    error_counts: dict[str, int],
) -> CandidateSignal:
    """The per-candidate retrieval/extraction/translation state machine —
    a single explicit candidate, never a loop. Called both from
    run_pipeline's budgeted loop and from process_single_candidate's
    on-demand manual entry point (Radar Inbox's Process now/Retry
    actions)."""
    candidate = _transition(candidate, CandidateStatus.QUEUED_FOR_PROCESSING)
    candidate = _transition(candidate, CandidateStatus.RETRIEVAL_IN_PROGRESS)

    doc_result = document_service.get_or_fetch_excerpt(client, candidate.filing.rcept_no, cache_dir)
    candidate.extraction_state = doc_result.state
    if doc_result.from_cache:
        counters["cache_hits"] += 1

    if doc_result.state == ExtractionState.EXTRACTED:
        if not doc_result.from_cache:
            counters["documents_retrieved"] += 1
            counters["documents_extracted"] += 1
        candidate.excerpt_original = doc_result.excerpt_original
        candidate.excerpt_quality = assess_excerpt_quality(doc_result.excerpt_original)
        candidate = _transition(candidate, CandidateStatus.EXTRACTED)
    elif doc_result.state == ExtractionState.RETRIEVAL_FAILED:
        error_counts["retrieval_failed"] = error_counts.get("retrieval_failed", 0) + 1
        candidate = _transition(candidate, CandidateStatus.RETRIEVAL_FAILED, doc_result.detail)
    else:  # PARSE_FAILED / UNSUPPORTED_FORMAT
        if not doc_result.from_cache:
            counters["documents_retrieved"] += 1
        error_counts["parse_failed"] = error_counts.get("parse_failed", 0) + 1
        candidate = _transition(candidate, CandidateStatus.PARSE_FAILED, doc_result.detail)

    # Title translation is attempted regardless of extraction outcome —
    # the title comes from FilingEvent, not the document body, so a
    # failed/unsupported document doesn't block it.
    candidate = _transition(candidate, CandidateStatus.TRANSLATION_PENDING)
    title_translation = translate_cached(translation_provider, candidate.filing.rcept_no, candidate.filing.report_nm, cache_dir)
    candidate.title_translation = title_translation
    if title_translation is not None:
        counters["translations_completed"] += 1

    excerpt_translation = None
    if candidate.excerpt_original:
        excerpt_translation = translate_cached(translation_provider, candidate.filing.rcept_no, candidate.excerpt_original, cache_dir)
        candidate.excerpt_translation = excerpt_translation
        if excerpt_translation is not None:
            counters["translations_completed"] += 1

    if candidate.excerpt_original:
        if excerpt_translation is not None:
            candidate.translation_state = TranslationState.TRANSLATED
        else:
            candidate.translation_state = TranslationState.UNAVAILABLE
            error_counts["translation_unavailable"] = error_counts.get("translation_unavailable", 0) + 1
    else:
        candidate.translation_state = TranslationState.TRANSLATED if title_translation is not None else TranslationState.UNAVAILABLE

    transition_detail = ""
    if candidate.extraction_state == ExtractionState.EXTRACTED:
        if _is_ownership_change_candidate(candidate):
            gate_result = ownership_materiality.assess_ownership_materiality(candidate.filing.report_nm, candidate.excerpt_original)
            candidate.materiality_assessment = gate_result.detail
            transition_detail = gate_result.detail
            final_status = CandidateStatus.NOT_MATERIAL if gate_result.outcome == "not_material" else CandidateStatus.NEEDS_REVIEW
        else:
            final_status = CandidateStatus.NEEDS_REVIEW
    elif candidate.extraction_state == ExtractionState.RETRIEVAL_FAILED:
        final_status = CandidateStatus.RETRIEVAL_FAILED
    else:
        final_status = CandidateStatus.PARSE_FAILED
    candidate = _transition(candidate, final_status, transition_detail)

    return candidate


def _is_ownership_change_candidate(candidate: CandidateSignal) -> bool:
    return any(rule.startswith("ownership_change:") for rule in candidate.matched_rules)


def run_pipeline(
    client: DartClient,
    translation_provider: TranslationProvider,
    companies: list[TrackedCompany],
    cache_dir: Path,
    lookback_days: int = scan_service.DEFAULT_LOOKBACK_DAYS,
    max_candidates_to_process: int = DEFAULT_MAX_CANDIDATES_PER_SCAN,
    candidate_repository: CandidatePersistence | None = None,
) -> ScanReport:
    """One bounded, idempotent pipeline run. Re-running with the same
    scope never creates duplicate FilingEvents/CandidateSignals (scan_
    service's own receipt-number dedup) and never re-fetches/re-parses/
    re-translates an already-processed candidate (document_service's and
    translation_service's own per-ID/per-hash caches) — this function's
    only new responsibility is deciding *which* eligible candidates get
    processed this run, bounded by `max_candidates_to_process`.

    `candidate_repository` (Durable-State Phase 3A) is additive and
    optional. Omitted (the only path any real service entry point uses
    this phase), every candidate-store touch below is exactly today's
    JSON behavior via candidate_store.py. Supplied — synthetic tests
    only this phase — every candidate-store touch in this one call (the
    post-scan upsert, the eligibility-selection read, and both
    processing-loop writes) routes through the same collaborator, so
    candidates this call just detected are visible to its own
    eligibility selection and processing loops. scan_service.scan()'s own
    filing-event read/write and translate_cached()'s own translation-
    cache read/write are never affected by this parameter."""
    scan_id = f"scan-{uuid.uuid4().hex[:12]}"
    started_at = datetime.now(timezone.utc).isoformat()
    max_candidates_to_process = clamp_max_candidates(max_candidates_to_process)

    scan_result = scan_service.scan(client, companies, cache_dir, lookback_days=lookback_days)

    detected_now = list(scan_result.new_candidate_signals)
    if candidate_repository is None:
        candidate_store.upsert_new_candidates(cache_dir, detected_now)
        store = candidate_store.load_candidates(cache_dir)
    else:
        candidate_repository.upsert_new_candidates(detected_now)
        store = candidate_repository.load_candidates()

    eligible = sorted(
        (
            c for c in store.values()
            if c.status in _ELIGIBLE_STATUSES and c.confidence in _PROCESSABLE_CONFIDENCE_LEVELS
        ),
        key=lambda c: (c.filing.rcept_dt, c.filing.rcept_no),
    )
    to_process = eligible[:max_candidates_to_process]
    to_defer = eligible[max_candidates_to_process:]

    counters = {"documents_retrieved": 0, "documents_extracted": 0, "translations_completed": 0, "cache_hits": 0}
    error_counts: dict[str, int] = {}

    for candidate in to_process:
        processed = process_candidate(client, translation_provider, candidate, cache_dir, counters, error_counts)
        if candidate_repository is None:
            candidate_store.update_candidate(cache_dir, processed)
        else:
            candidate_repository.update_candidate(processed)

    for candidate in to_defer:
        deferred = _transition(candidate, CandidateStatus.PROCESSING_DEFERRED, "Scan processing budget reached.")
        if candidate_repository is None:
            candidate_store.update_candidate(cache_dir, deferred)
        else:
            candidate_repository.update_candidate(deferred)

    warnings: list[str] = []
    for message in scan_result.errors:
        error_counts["scan_error"] = error_counts.get("scan_error", 0) + 1
        warnings.append(message)

    completed_at = datetime.now(timezone.utc).isoformat()
    scope = scan_result.scope
    return ScanReport(
        scan_id=scan_id, started_at=started_at, completed_at=completed_at,
        companies=scope.companies, source=scope.source, bgn_de=scope.bgn_de, end_de=scope.end_de,
        filings_discovered=len(scan_result.new_filing_events) + scan_result.already_seen_count,
        new_filing_events=len(scan_result.new_filing_events),
        candidates_detected=len(detected_now),
        candidates_processed=len(to_process),
        candidates_deferred=len(to_defer),
        documents_retrieved=counters["documents_retrieved"],
        documents_extracted=counters["documents_extracted"],
        translations_completed=counters["translations_completed"],
        already_seen_count=scan_result.already_seen_count,
        no_data_count=len(scan_result.no_data_companies),
        errors_by_category=error_counts,
        cache_hits=counters["cache_hits"],
        warnings=tuple(warnings),
    )


def process_single_candidate(
    client: DartClient,
    translation_provider: TranslationProvider,
    candidate_id: str,
    cache_dir: Path,
    candidate_repository: CandidatePersistence | None = None,
) -> CandidateSignal | None:
    """On-demand processing of exactly one named candidate — the seam
    Radar Inbox's manual "Process now" (a PROCESSING_DEFERRED candidate)
    and "Retry processing" (a RETRIEVAL_FAILED/PARSE_FAILED/translation-
    unavailable candidate) actions call. Deliberately bypasses
    _ELIGIBLE_STATUSES and the confidence filter — those govern automatic
    pickup by run_pipeline's budgeted loop, not an explicit single-item
    user click, which is its own bounded action. Returns None if the id
    isn't found rather than raising, so a caller can show a clear message.

    `candidate_repository` (Durable-State Phase 3A) is additive and
    optional — see run_pipeline's own docstring above for the shared
    reasoning. process_candidate's own network/extraction/translation
    calls are never affected either way."""
    if candidate_repository is None:
        store = candidate_store.load_candidates(cache_dir)
    else:
        store = candidate_repository.load_candidates()
    candidate = store.get(candidate_id)
    if candidate is None:
        return None
    counters = {"documents_retrieved": 0, "documents_extracted": 0, "translations_completed": 0, "cache_hits": 0}
    error_counts: dict[str, int] = {}
    processed = process_candidate(client, translation_provider, candidate, cache_dir, counters, error_counts)
    if candidate_repository is None:
        candidate_store.update_candidate(cache_dir, processed)
    else:
        candidate_repository.update_candidate(processed)
    return processed
