"""Bounded, idempotent EDINET Radar orchestration (Japan radar pilot,
planning Gate 1) — the one reusable entry point connecting scan_service
(document-list scan + FilingEvent/CandidateSignal creation),
document_service (retrieval + extraction), and edinet_rules into a single
pipeline run. Mirrors src/data_access/edgar/edgar_pipeline.py's shape,
with the deliberate differences the approved plan calls for:

1. No translation call of any kind this gate. The candidate lifecycle's
   translation_state IS exercised (PENDING once a Japanese excerpt is
   successfully extracted — the existing lifecycle seam DART/EDGAR both
   use), but this module never imports or calls
   src/data_access/translation/translation_service.py's translate_cached
   (or anything else in that package) — per the explicit Gate 1
   instruction "no actual translation/provider call may occur in Gate
   1." A later gate wires the real DeepL call the same way DART's own
   radar_pipeline.py does; until then a PENDING candidate simply stays
   PENDING.
2. No ownership/large-shareholding materiality gate — explicitly
   forbidden for this pilot (unlike DART's ownership_materiality.py,
   which is specific to the two Korean DART document shapes it was
   calibrated against).

Uses its own separate persisted candidate store (edinet_candidates.json,
via candidate_store.py's existing additive `filename` parameter) so DART,
EDGAR, and EDINET candidates never mix on disk.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.config.tracked_companies import TrackedCompany
from src.data_access.dart import candidate_store
from src.data_access.dart.candidate_store import CandidatePersistence
from src.data_access.edinet import document_service, edinet_rules, scan_service
from src.data_access.edinet.client import EdinetClient
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, StateTransition, TranslationState

_PROCESSABLE_CONFIDENCE_LEVELS = frozenset({"Moderate", "High"})

DEFAULT_MAX_CANDIDATES_PER_SCAN = 5
MAX_CANDIDATES_PER_SCAN_CEILING = 10

_ELIGIBLE_STATUSES = frozenset({CandidateStatus.CANDIDATE_DETECTED, CandidateStatus.PROCESSING_DEFERRED})

CANDIDATE_STORE_FILENAME = "edinet_candidates.json"


def clamp_max_candidates(n: int) -> int:
    return max(1, min(n, MAX_CANDIDATES_PER_SCAN_CEILING))


@dataclass(frozen=True)
class ScanReport:
    """Structured, JSON-serializable summary of one EDINET pipeline run —
    counts and safe strings only, never raw provider responses, document
    contents, or the configured Subscription-Key. Same shape as EDGAR's
    edgar_pipeline.ScanReport, minus documents_extracted-drives-
    translation semantics (see module docstring — translation is not
    called this gate)."""

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
    client: EdinetClient,
    candidate: CandidateSignal,
    cache_dir: Path,
    counters: dict[str, int],
    error_counts: dict[str, int],
) -> CandidateSignal:
    """The per-candidate retrieval/extraction state machine — a single
    explicit candidate, never a loop. Called both from run_pipeline's
    budgeted loop and from process_single_candidate's on-demand manual
    entry point."""
    candidate = _transition(candidate, CandidateStatus.QUEUED_FOR_PROCESSING)
    candidate = _transition(candidate, CandidateStatus.RETRIEVAL_IN_PROGRESS)

    doc_id = candidate.filing.rcept_no

    doc_result = document_service.get_or_fetch_excerpt(client, doc_id, cache_dir)
    candidate.extraction_state = doc_result.state
    if doc_result.from_cache:
        counters["cache_hits"] += 1

    if doc_result.state == ExtractionState.EXTRACTED:
        if not doc_result.from_cache:
            counters["documents_retrieved"] += 1
            counters["documents_extracted"] += 1
        candidate.excerpt_original = doc_result.excerpt_original
        # Translation lifecycle seam only — no provider call this gate
        # (see module docstring). A successfully extracted Japanese
        # excerpt is marked PENDING, the same state DART uses before its
        # own translate_cached() call; a later gate wires that call in
        # for EDINET exactly as DART already does for itself.
        candidate.translation_state = TranslationState.PENDING
        candidate = _transition(candidate, CandidateStatus.EXTRACTED)
    elif doc_result.state == ExtractionState.RETRIEVAL_FAILED:
        error_counts["retrieval_failed"] = error_counts.get("retrieval_failed", 0) + 1
        candidate = _transition(candidate, CandidateStatus.RETRIEVAL_FAILED, doc_result.detail)
    else:  # PARSE_FAILED / UNSUPPORTED_FORMAT
        if not doc_result.from_cache:
            counters["documents_retrieved"] += 1
        error_counts["parse_failed"] = error_counts.get("parse_failed", 0) + 1
        candidate = _transition(candidate, CandidateStatus.PARSE_FAILED, doc_result.detail)

    if candidate.extraction_state == ExtractionState.EXTRACTED:
        final_status = CandidateStatus.NEEDS_REVIEW
    elif candidate.extraction_state == ExtractionState.RETRIEVAL_FAILED:
        final_status = CandidateStatus.RETRIEVAL_FAILED
    else:
        final_status = CandidateStatus.PARSE_FAILED
    candidate = _transition(candidate, final_status)

    return candidate


@dataclass(frozen=True)
class CandidateBackfillResult:
    """Outcome of backfill_candidate_from_existing_event — never raises;
    exactly one of `created`/`already_existed`/`error` explains what
    happened, so a caller (or Gate 10's own report) can distinguish "a
    new candidate now exists" from "nothing changed" without inspecting
    internals."""

    candidate_id: str | None = None
    created: bool = False
    already_existed: bool = False
    error: str | None = None


def backfill_candidate_from_existing_event(
    cache_dir: Path,
    doc_id: str,
    code_category_map: dict[str, str] = edinet_rules.DEFAULT_CODE_CATEGORY_MAP,
    candidate_repository: CandidatePersistence | None = None,
) -> CandidateBackfillResult:
    """No network call of any kind — purely local. Re-evaluates an
    already-persisted FilingEvent's own already-recorded
    ordinance_code/pblntf_ty/pblntf_detail_ty (populated by
    scan_service.backfill_form_codes in Gate 9, or by a live scan going
    forward) against `code_category_map`, and creates exactly one
    CandidateSignal referencing the EXISTING FilingEvent if and only if
    the rule matches AND no CandidateSignal for this docID already
    exists in the real candidate_store (edinet_candidates.json) — never
    scan_service's own internal cache bookkeeping, which nothing reads
    back for display.

    Never creates a new FilingEvent, never modifies any existing
    FilingEvent field, never touches document_service/translation.
    Idempotent: a second call with an already-existing candidate is a
    no-op, reported via `already_existed=True`.

    `candidate_repository` (Durable-State Phase 3A) is additive and
    optional. Omitted, every candidate-store touch below is exactly
    today's JSON behavior via candidate_store.py. Supplied, both the
    existence check and the write route through the collaborator instead
    — see candidate_store.CandidatePersistence's own docstring for why
    both, not just the write, move together. FilingEvent reads
    (scan_service.load_filing_events) are never affected either way."""
    filings = {f.rcept_no: f for f in scan_service.load_filing_events(cache_dir)}
    filing = filings.get(doc_id)
    if filing is None:
        return CandidateBackfillResult(error=f"{doc_id}: no existing FilingEvent found.")

    candidate_id = f"edinet-cand-{doc_id}"
    if candidate_repository is None:
        existing_candidates = candidate_store.load_candidates(cache_dir, CANDIDATE_STORE_FILENAME)
    else:
        existing_candidates = candidate_repository.load_candidates()
    if candidate_id in existing_candidates:
        return CandidateBackfillResult(candidate_id=candidate_id, already_existed=True)

    evaluation = edinet_rules.evaluate_document(filing.ordinance_code, filing.pblntf_ty, filing.pblntf_detail_ty, code_category_map)
    if evaluation.confidence is None:
        return CandidateBackfillResult(error=f"{doc_id}: form tuple ({filing.ordinance_code}:{filing.pblntf_ty}:{filing.pblntf_detail_ty}) does not match any configured category — no candidate created.")

    category = evaluation.matched_rules[0].split(":", 1)[0]
    label = category.replace("_", " ").title()
    detail = (
        f"{label} · ordinanceCode={filing.ordinance_code} · formCode={filing.pblntf_ty} · "
        f"docTypeCode={filing.pblntf_detail_ty} — form-metadata routing only; no extracted document evidence yet."
    )
    now = datetime.now(timezone.utc).isoformat()
    candidate = CandidateSignal(
        id=candidate_id,
        filing=filing,
        matched_rules=list(evaluation.matched_rules),
        confidence=evaluation.confidence,
        status=CandidateStatus.CANDIDATE_DETECTED,
        state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at=now, detail=detail)],
    )
    if candidate_repository is None:
        candidate_store.upsert_new_candidates(cache_dir, [candidate], CANDIDATE_STORE_FILENAME)
    else:
        candidate_repository.upsert_new_candidates([candidate])
    return CandidateBackfillResult(candidate_id=candidate_id, created=True)


def process_single_candidate(
    client: EdinetClient,
    candidate_id: str,
    cache_dir: Path,
    candidate_repository: CandidatePersistence | None = None,
) -> CandidateSignal | None:
    """On-demand processing of exactly one named candidate — the seam
    Radar Inbox's manual "Process now"/"Retry processing" actions would
    call for an EDINET candidate. Returns None if the id isn't found.

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
    processed = process_candidate(client, candidate, cache_dir, counters, error_counts)
    if candidate_repository is None:
        candidate_store.update_candidate(cache_dir, processed, CANDIDATE_STORE_FILENAME)
    else:
        candidate_repository.update_candidate(processed)
    return processed


def run_pipeline(
    client: EdinetClient,
    companies: list[TrackedCompany],
    cache_dir: Path,
    lookback_days: int = scan_service.DEFAULT_LOOKBACK_DAYS,
    max_candidates_to_process: int = DEFAULT_MAX_CANDIDATES_PER_SCAN,
    candidate_repository: CandidatePersistence | None = None,
) -> ScanReport:
    """One bounded, idempotent pipeline run. Re-running with the same
    scope never creates duplicate FilingEvents/CandidateSignals
    (scan_service's own dedup) and never re-fetches/re-parses an
    already-processed candidate (document_service's own cache) — this
    function's only new responsibility is deciding *which* eligible
    candidates get processed this run, bounded by
    `max_candidates_to_process`. Gate 1 note: has zero real callers yet
    (see edinet_service.py) — `companies` will be empty in real use until
    a later gate adds tracked EDINET companies; fixtures exercise this
    function directly with explicit fictional company data.

    `candidate_repository` (Durable-State Phase 3A) is additive and
    optional. Omitted (the only path any real service entry point uses
    this phase), every candidate-store touch below is exactly today's
    JSON behavior via candidate_store.py. Supplied — synthetic tests
    only this phase — every candidate-store touch in this one call
    (the post-scan upsert, the eligibility-selection read, and both
    processing-loop writes) routes through the same collaborator, so
    candidates this call just detected are visible to its own
    eligibility selection and processing loops. scan_service.scan()'s own
    filing-event read/write is never affected by this parameter."""
    scan_id = f"edinet-scan-{uuid.uuid4().hex[:12]}"
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
        processed = process_candidate(client, candidate, cache_dir, counters, error_counts)
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
