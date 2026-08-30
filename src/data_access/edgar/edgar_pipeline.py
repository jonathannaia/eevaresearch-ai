"""Bounded, idempotent SEC EDGAR Radar orchestration (milestone 8 pilot)
— the one reusable entry point connecting scan_service (filing scan +
FilingEvent/CandidateSignal creation), document_service (retrieval +
extraction), and edgar_rules (post-extraction 8-K item refinement) into
a single pipeline run. Mirrors src/data_access/dart/radar_pipeline.py's
shape exactly, with two deliberate differences:

1. No translation step at all — EDGAR filings are native English, so
   every candidate's translation_state stays NOT_REQUESTED throughout,
   never TRANSLATED/UNAVAILABLE/PENDING.
2. No ownership-materiality gate — that gate is specific to the two
   Korean DART document shapes it was built against (see
   ownership_materiality.py). A `SC 13D`/`SC 13G` EDGAR candidate is
   detected/routed normally but always lands at NEEDS_REVIEW like any
   other category, per the milestone-8 brief's explicit instruction not
   to introduce a U.S. ownership threshold in this pilot.

Uses its own separate persisted candidate store (edgar_candidates.json,
via candidate_store.py's additive `filename` parameter — see that
module's docstring) so DART and EDGAR candidates never mix on disk.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.config.tracked_companies import TrackedCompany
from src.data_access.dart import candidate_store, retry_policy
from src.data_access.dart.candidate_store import CandidatePersistence
from src.data_access.edgar import document_service, edgar_rules, scan_service
from src.logic.signal_decision_policy import SignalRoute, decide_signal_route
from src.data_access.edgar.client import EdgarClient
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, StateTransition, TranslationState

_PROCESSABLE_CONFIDENCE_LEVELS = frozenset({"Moderate", "High"})

DEFAULT_MAX_CANDIDATES_PER_SCAN = 5
MAX_CANDIDATES_PER_SCAN_CEILING = 10

_ELIGIBLE_STATUSES = frozenset({CandidateStatus.CANDIDATE_DETECTED, CandidateStatus.PROCESSING_DEFERRED})

CANDIDATE_STORE_FILENAME = "edgar_candidates.json"


def clamp_max_candidates(n: int) -> int:
    return max(1, min(n, MAX_CANDIDATES_PER_SCAN_CEILING))


@dataclass(frozen=True)
class ScanReport:
    """Structured, JSON-serializable summary of one EDGAR pipeline run —
    counts and safe strings only, never raw provider responses, document
    contents, or the configured User-Agent. Same shape as DART's
    radar_pipeline.ScanReport, minus the translations_completed field
    (EDGAR never translates)."""

    scan_id: str
    started_at: str
    completed_at: str
    companies: tuple[str, ...]
    source: str
    bgn_date: str
    end_date: str
    filings_discovered: int
    new_filing_events: int
    candidates_detected: int
    candidates_processed: int
    candidates_deferred: int
    documents_retrieved: int
    documents_extracted: int
    already_seen_count: int
    no_data_count: int
    errors_by_category: dict[str, int]
    cache_hits: int
    warnings: tuple[str, ...]


def _transition(candidate: CandidateSignal, status: CandidateStatus, detail: str = "") -> CandidateSignal:
    candidate.status = status
    candidate.state_history.append(StateTransition(status=status, at=datetime.now(timezone.utc).isoformat(), detail=detail))
    return candidate


def process_candidate(
    client: EdgarClient,
    candidate: CandidateSignal,
    cache_dir: Path,
    counters: dict[str, int],
    error_counts: dict[str, int],
    auto_publish_enabled: bool = False,
) -> CandidateSignal:
    """The per-candidate retrieval/extraction state machine — a single
    explicit candidate, never a loop. Called both from run_pipeline's
    budgeted loop and from process_single_candidate's on-demand manual
    entry point."""
    is_retry = candidate.status in (CandidateStatus.RETRIEVAL_FAILED, CandidateStatus.PARSE_FAILED)
    candidate = _transition(candidate, CandidateStatus.QUEUED_FOR_PROCESSING)
    candidate = _transition(candidate, CandidateStatus.RETRIEVAL_IN_PROGRESS)

    cik = candidate.filing.corp_code
    accession_no = candidate.filing.rcept_no
    filename = candidate.filing.primary_document

    # Known item numbers, if any, from a prior scan-time/document-time
    # classification — lets document_extractor anchor the excerpt at the
    # actual Item header instead of the document start (see
    # document_extractor.py's module docstring). Empty for a candidate
    # still at the coarse "8-K filed, items pending" classification.
    expected_items = ()
    if candidate.filing.pblntf_ty.strip().upper() == "8-K":
        expected_items = edgar_rules.items_from_matched_rules(candidate.matched_rules)

    doc_result = document_service.get_or_fetch_excerpt(client, cik, accession_no, filename, cache_dir, expected_items, force_refresh=is_retry)
    candidate.extraction_state = doc_result.state
    if doc_result.from_cache:
        counters["cache_hits"] += 1

    final_status_detail = ""
    if doc_result.state == ExtractionState.EXTRACTED:
        if not doc_result.from_cache:
            counters["documents_retrieved"] += 1
            counters["documents_extracted"] += 1
        candidate.excerpt_original = doc_result.excerpt_original
        # Post-extraction 8-K item refinement (see module docstring and
        # edgar_rules.py) — only 8-K candidates need this; every other
        # form type already got its full category+confidence at scan
        # time from form type alone. Monotonic merge (Gate 8 fix): SEC
        # scan-time item metadata (already reflected in
        # candidate.matched_rules entering this function) is
        # authoritative and is only ever added to by the document
        # excerpt, never replaced/shrunk by it.
        if candidate.filing.pblntf_ty.strip().upper() == "8-K":
            scan_time_items = edgar_rules.items_from_matched_rules(candidate.matched_rules)
            excerpt_items = edgar_rules.extract_item_numbers(doc_result.excerpt_original or "")
            merged, newly_added = edgar_rules.merge_8k_item_evaluation(scan_time_items, excerpt_items)
            if merged.confidence is not None:
                candidate.matched_rules = list(merged.matched_rules)
                candidate.confidence = merged.confidence
                if newly_added:
                    final_status_detail = (
                        f"Document excerpt confirmed additional item(s) {', '.join(newly_added)} "
                        f"beyond scan-time SEC metadata ({', '.join(scan_time_items) or 'none'})."
                    )
                elif scan_time_items:
                    final_status_detail = (
                        f"Scan-time SEC item metadata ({', '.join(scan_time_items)}) retained as "
                        f"authoritative; bounded document excerpt reached item(s) "
                        f"({', '.join(excerpt_items) or 'none'}) only."
                    )
                else:
                    final_status_detail = f"Classified from document-excerpt item header(s) {', '.join(excerpt_items)} (no scan-time SEC item metadata was available)."
        candidate = _transition(candidate, CandidateStatus.EXTRACTED)
        shadow_decision = decide_signal_route(candidate)
        candidate.state_history.append(
            StateTransition(
                status=CandidateStatus.EXTRACTED,
                at=datetime.now(timezone.utc).isoformat(),
                detail=(
                    f"Shadow policy: {shadow_decision.route.value} — "
                    f"{shadow_decision.reason} "
                    f"[rules: {', '.join(shadow_decision.rule_ids)}]"
                ),
            )
        )
    elif doc_result.state == ExtractionState.RETRIEVAL_FAILED:
        error_counts["retrieval_failed"] = error_counts.get("retrieval_failed", 0) + 1
        candidate = _transition(candidate, CandidateStatus.RETRIEVAL_FAILED, doc_result.detail)
    else:  # PARSE_FAILED / UNSUPPORTED_FORMAT
        if not doc_result.from_cache:
            counters["documents_retrieved"] += 1
        error_counts["parse_failed"] = error_counts.get("parse_failed", 0) + 1
        candidate = _transition(candidate, CandidateStatus.PARSE_FAILED, doc_result.detail)

    # EDGAR filings are native English — no translation step exists.
    candidate.translation_state = TranslationState.NOT_REQUESTED

    if candidate.extraction_state == ExtractionState.EXTRACTED:
        if shadow_decision.route == SignalRoute.TIMELINE:
            final_status = CandidateStatus.MONITORING
        elif shadow_decision.route == SignalRoute.ARCHIVE:
            final_status = CandidateStatus.DISMISSED
        elif shadow_decision.route == SignalRoute.PUBLISH and auto_publish_enabled:
            final_status = CandidateStatus.PUBLISHED
        else:
            # REVIEW and disabled PUBLISH remain human-gated.
            final_status = CandidateStatus.NEEDS_REVIEW
    elif candidate.extraction_state == ExtractionState.RETRIEVAL_FAILED:
        final_status = CandidateStatus.RETRIEVAL_FAILED
    else:
        final_status = CandidateStatus.PARSE_FAILED
    candidate = _transition(candidate, final_status, final_status_detail)

    return candidate


def process_single_candidate(
    client: EdgarClient,
    candidate_id: str,
    cache_dir: Path,
    candidate_repository: CandidatePersistence | None = None,
    auto_publish_enabled: bool = False,
) -> CandidateSignal | None:
    """On-demand processing of exactly one named candidate — the seam
    Radar Inbox's manual "Process now"/"Retry processing" actions call
    for an EDGAR candidate. Returns None if the id isn't found.

    `candidate_repository` (Durable-State Phase 3A) is additive and
    optional — see run_pipeline's own docstring below for the shared
    reasoning. process_candidate's own network/extraction call is never
    affected either way."""
    if candidate_repository is None:
        store = candidate_store.load_candidates(cache_dir, CANDIDATE_STORE_FILENAME)
    else:
        store = candidate_repository.load_candidates()
    candidate = store.get(candidate_id)
    if candidate is None:
        return None
    counters = {"documents_retrieved": 0, "documents_extracted": 0, "cache_hits": 0}
    error_counts: dict[str, int] = {}
    processed = process_candidate(
        client,
        candidate,
        cache_dir,
        counters,
        error_counts,
        auto_publish_enabled=auto_publish_enabled,
    )
    if candidate_repository is None:
        candidate_store.update_candidate(cache_dir, processed, CANDIDATE_STORE_FILENAME)
    else:
        candidate_repository.update_candidate(processed)
    return processed


def run_pipeline(
    client: EdgarClient,
    companies: list[TrackedCompany],
    cache_dir: Path,
    lookback_days: int = scan_service.DEFAULT_LOOKBACK_DAYS,
    max_candidates_to_process: int = DEFAULT_MAX_CANDIDATES_PER_SCAN,
    candidate_repository: CandidatePersistence | None = None,
    auto_publish_enabled: bool = False,
    scan_interval_minutes: int = 60,
) -> ScanReport:
    """One bounded, idempotent pipeline run. Re-running with the same
    scope never creates duplicate FilingEvents/CandidateSignals
    (scan_service's own dedup) and never re-fetches/re-parses an
    already-processed candidate (document_service's own cache) — this
    function's only new responsibility is deciding *which* eligible
    candidates get processed this run, bounded by
    `max_candidates_to_process`.

    `scan_interval_minutes` (Radar reliability fix) feeds only
    retry_policy.automatic_retry_eligible()'s escalating backoff below —
    see radar_pipeline.run_pipeline's own docstring (DART) for the full
    shared reasoning; this mirrors it exactly, including that the
    separately-budgeted stale-failure retry below never touches
    `max_candidates_to_process`/`candidates_deferred` and never relabels
    an unreached candidate PROCESSING_DEFERRED.

    `candidate_repository` (Durable-State Phase 3A) is additive and
    optional. Omitted (the only path any real service entry point uses
    this phase), every candidate-store touch below is exactly today's
    JSON behavior via candidate_store.py. Supplied — synthetic tests
    only this phase — every candidate-store touch in this one call (the
    post-scan upsert, the eligibility-selection read, and both
    processing-loop writes) routes through the same collaborator, so
    candidates this call just detected are visible to its own
    eligibility selection and processing loops. scan_service.scan()'s own
    filing-event read/write is never affected by this parameter."""
    scan_id = f"edgar-scan-{uuid.uuid4().hex[:12]}"
    started_at = datetime.now(timezone.utc).isoformat()
    max_candidates_to_process = clamp_max_candidates(max_candidates_to_process)

    scan_result = scan_service.scan(client, companies, cache_dir, lookback_days=lookback_days)

    detected_now = list(scan_result.new_candidate_signals)
    if candidate_repository is None:
        candidate_store.upsert_new_candidates(cache_dir, detected_now, CANDIDATE_STORE_FILENAME)
        store = candidate_store.load_candidates(cache_dir, CANDIDATE_STORE_FILENAME)
    else:
        candidate_repository.upsert_new_candidates(detected_now)
        store = candidate_repository.load_candidates()

    eligible = sorted(
        (c for c in store.values() if c.status in _ELIGIBLE_STATUSES and c.confidence in _PROCESSABLE_CONFIDENCE_LEVELS),
        key=lambda c: (c.filing.rcept_dt, c.filing.rcept_no),
    )
    to_process = eligible[:max_candidates_to_process]
    to_defer = eligible[max_candidates_to_process:]

    counters = {"documents_retrieved": 0, "documents_extracted": 0, "cache_hits": 0}
    error_counts: dict[str, int] = {}

    for candidate in to_process:
        processed = process_candidate(
            client,
            candidate,
            cache_dir,
            counters,
            error_counts,
            auto_publish_enabled=auto_publish_enabled,
        )
        if candidate_repository is None:
            candidate_store.update_candidate(cache_dir, processed, CANDIDATE_STORE_FILENAME)
        else:
            candidate_repository.update_candidate(processed)

    for candidate in to_defer:
        deferred = _transition(candidate, CandidateStatus.PROCESSING_DEFERRED, "Scan processing budget reached.")
        if candidate_repository is None:
            candidate_store.update_candidate(cache_dir, deferred, CANDIDATE_STORE_FILENAME)
        else:
            candidate_repository.update_candidate(deferred)

    # Automatic retry of stale RETRIEVAL_FAILED/PARSE_FAILED candidates —
    # separately budgeted, never touches to_process/to_defer above. A
    # candidate not reached by AUTOMATIC_RETRY_MAX_PER_TICK_PER_SOURCE this
    # tick is left completely untouched (not PROCESSING_DEFERRED).
    stale_failures = sorted(
        (c for c in store.values() if retry_policy.automatic_retry_eligible(c, scan_interval_minutes)),
        key=lambda c: (c.filing.rcept_dt, c.filing.rcept_no),
    )[:retry_policy.AUTOMATIC_RETRY_MAX_PER_TICK_PER_SOURCE]

    for candidate in stale_failures:
        retried = process_candidate(client, candidate, cache_dir, counters, error_counts, auto_publish_enabled=auto_publish_enabled)
        if candidate_repository is None:
            candidate_store.update_candidate(cache_dir, retried, CANDIDATE_STORE_FILENAME)
        else:
            candidate_repository.update_candidate(retried)

    warnings: list[str] = []
    for message in scan_result.errors:
        error_counts["scan_error"] = error_counts.get("scan_error", 0) + 1
        warnings.append(message)

    completed_at = datetime.now(timezone.utc).isoformat()
    scope = scan_result.scope
    return ScanReport(
        scan_id=scan_id, started_at=started_at, completed_at=completed_at,
        companies=scope.companies, source=scope.source, bgn_date=scope.bgn_date, end_date=scope.end_date,
        filings_discovered=len(scan_result.new_filing_events) + scan_result.already_seen_count,
        new_filing_events=len(scan_result.new_filing_events),
        candidates_detected=len(detected_now),
        candidates_processed=len(to_process),
        candidates_deferred=len(to_defer),
        documents_retrieved=counters["documents_retrieved"],
        documents_extracted=counters["documents_extracted"],
        already_seen_count=scan_result.already_seen_count,
        no_data_count=len(scan_result.no_data_companies),
        errors_by_category=error_counts,
        cache_hits=counters["cache_hits"],
        warnings=tuple(warnings),
    )
