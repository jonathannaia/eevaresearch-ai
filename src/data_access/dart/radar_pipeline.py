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
from src.data_access.dart import candidate_store, document_service, ownership_materiality, retry_policy, scan_service
from src.data_access.dart.candidate_store import CandidatePersistence
from src.data_access.dart.client import DartClient
from src.data_access.daily_news.dart_filing_candidate_adapter import map_dart_filing_to_candidate
from src.data_access.daily_news.filing_event_models import FilingDerivedNewsCandidate
from src.data_access.dart.document_extractor import assess_excerpt_quality
from src.data_access.translation import translation_service
from src.data_access.translation.interfaces import TranslationProvider
from src.data_access.translation.translation_service import translate_cached_with_outcome
from src.models.models import (
    CandidateSignal,
    CandidateStatus,
    ExtractionState,
    StateTransition,
    build_flag_reason,
    record_excerpt,
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

# Daily News Filing-Event Shadow Adapter, Batch 2b — combined cap across
# both mapping-exception and (for parity with the EDGAR adapter's own
# join-based diagnostics; DART needs no join, so this cap here only ever
# bounds mapping-exception diagnostics) per pipeline run.
_FILING_CANDIDATE_SHADOW_DIAGNOSTICS_CAP = 20


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
    # Daily News Filing-Event Shadow Adapter, Batch 2b — additive,
    # defaulted so every existing construction of this dataclass
    # (positional or keyword) is unaffected. Populated only when
    # run_pipeline's own filing_candidate_shadow_enabled parameter is
    # True; empty otherwise, including for every existing caller/test
    # that omits the parameter. In-memory report data only — never
    # written to any store, never rendered by any UI, never read by
    # scripts/daily_news_worker.py or any Daily News code path.
    filing_candidate_shadow_matches: tuple[FilingDerivedNewsCandidate, ...] = ()
    filing_candidate_shadow_diagnostics: tuple[str, ...] = ()


def _build_dart_filing_candidate_shadow_report(
    scan_result: "scan_service.ScanResult",
) -> tuple[tuple[FilingDerivedNewsCandidate, ...], tuple[str, ...]]:
    """Daily News Filing-Event Shadow Adapter, Batch 2b — pure, in-memory
    only; never called unless run_pipeline's own filing_candidate_shadow_
    enabled parameter is True. Never creates, persists, or displays
    anything; never touches candidate_store, translation, or any UI.

    Unlike EDGAR, DART's map_dart_filing_to_candidate() needs no
    CandidateSignal join at all — dart_rules.evaluate_report_name()
    (called inside the adapter, already committed) takes only
    filing.report_nm, already present on the bare FilingEvent — so this
    is simply one call per filing in scan_result.new_filing_events.

    A per-filing mapping exception is caught, recorded as a sanitized
    "{rcept_no}:{ExceptionClassName}" diagnostic (never a raw message),
    and never stops evaluation of the remaining filings. Diagnostics are
    capped at _FILING_CANDIDATE_SHADOW_DIAGNOSTICS_CAP entries for this
    run."""
    candidates: list[FilingDerivedNewsCandidate] = []
    diagnostics: list[str] = []

    for filing in scan_result.new_filing_events:
        try:
            candidate = map_dart_filing_to_candidate(filing)
        except Exception as exc:  # noqa: BLE001 — one filing's mapping failure must never fail the scan
            if len(diagnostics) < _FILING_CANDIDATE_SHADOW_DIAGNOSTICS_CAP:
                diagnostics.append(f"{filing.rcept_no}:{type(exc).__name__}")
            continue

        if candidate is not None:
            candidates.append(candidate)

    return tuple(candidates), tuple(diagnostics)


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
    is_retry = candidate.status == CandidateStatus.RETRIEVAL_FAILED
    candidate = _transition(candidate, CandidateStatus.QUEUED_FOR_PROCESSING)
    candidate = _transition(candidate, CandidateStatus.RETRIEVAL_IN_PROGRESS)

    doc_result = document_service.get_or_fetch_excerpt(client, candidate.filing.rcept_no, cache_dir, force_refresh=is_retry)
    candidate.extraction_state = doc_result.state
    if doc_result.from_cache:
        counters["cache_hits"] += 1

    if doc_result.state == ExtractionState.EXTRACTED:
        if not doc_result.from_cache:
            counters["documents_retrieved"] += 1
            counters["documents_extracted"] += 1
        # Evidence-packet foundation, Phase 1 (design/DECISIONS.md):
        # excerpt_original is set once and never silently overwritten —
        # see record_excerpt's own docstring. A retry that re-extracts
        # different text is preserved in excerpt_supplemental instead of
        # replacing the original.
        record_excerpt(candidate, doc_result.excerpt_original, doc_result.retrieved_at)
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
    title_attempt = translate_cached_with_outcome(translation_provider, candidate.filing.rcept_no, candidate.filing.report_nm, cache_dir)
    candidate.title_translation = title_attempt.translation
    if title_attempt.translation is not None:
        counters["translations_completed"] += 1

    excerpt_attempt = None
    if candidate.excerpt_original:
        excerpt_attempt = translate_cached_with_outcome(translation_provider, candidate.filing.rcept_no, candidate.excerpt_original, cache_dir)
        candidate.excerpt_translation = excerpt_attempt.translation
        if excerpt_attempt.translation is not None:
            counters["translations_completed"] += 1

    # translation_state (and, on failure, the persisted failure category/
    # reason/retry schedule — translation reliability workstream) is
    # driven by the excerpt's own outcome when an excerpt exists, else by
    # the title's — the same "primary text" convention retry_translation_
    # for_candidate() uses so a later automatic retry re-attempts exactly
    # the text that actually caused UNAVAILABLE.
    primary_attempt = excerpt_attempt if candidate.excerpt_original else title_attempt
    translation_service.record_translation_attempt(candidate, primary_attempt)
    if primary_attempt.translation is None:
        error_counts["translation_unavailable"] = error_counts.get("translation_unavailable", 0) + 1

    transition_detail = ""
    if candidate.extraction_state == ExtractionState.EXTRACTED:
        if _is_ownership_change_candidate(candidate):
            gate_result = ownership_materiality.assess_ownership_materiality(candidate.filing.report_nm, candidate.excerpt_original)
            candidate.materiality_assessment = gate_result.detail
            transition_detail = gate_result.detail
            # Evidence-packet foundation, Phase 1: refresh the normalized
            # why-flagged record with the ownership-materiality gate's own
            # detail once it's known — matched_rules/confidence are
            # unchanged by this gate, only source_detail is enriched.
            candidate.flag_reason = build_flag_reason(candidate.matched_rules, candidate.confidence, source_detail=gate_result.detail)
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
    filing_candidate_shadow_enabled: bool = False,
) -> ScanReport:
    """One bounded, idempotent pipeline run. Re-running with the same
    scope never creates duplicate FilingEvents/CandidateSignals (scan_
    service's own receipt-number dedup) and never re-fetches/re-parses/
    re-translates an already-processed candidate (document_service's and
    translation_service's own per-ID/per-hash caches) — this function's
    only new responsibility is deciding *which* eligible candidates get
    processed this run, bounded by `max_candidates_to_process`.

    A separate, independently-budgeted selection of stale
    RETRIEVAL_FAILED candidates (retry_policy.AUTOMATIC_RETRY_MAX_PER_TICK)
    runs after the new/deferred loop below and never affects
    `max_candidates_to_process`/`candidates_deferred` in any way; a
    candidate not reached by that cap is left completely untouched, never
    relabeled PROCESSING_DEFERRED. PARSE_FAILED is never automatically
    retried (see retry_policy.py's module docstring for why).

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
    cache read/write are never affected by this parameter.

    `filing_candidate_shadow_enabled` (Daily News Filing-Event Shadow
    Adapter, Batch 2b) is additive and optional, defaulting to False —
    every existing call site that omits it is completely unaffected, and
    this whole branch never executes when it's False. When True,
    _build_dart_filing_candidate_shadow_report() is evaluated against
    `scan_result` (this tick's own already-tracked-issuer filings,
    already in memory from the scan_service.scan() call above — no new
    fetch) purely to populate the returned ScanReport's own
    `filing_candidate_shadow_matches`/`filing_candidate_shadow_
    diagnostics` fields. This is a read-only observation only: it never
    touches `detected_now`, `store`, the candidate_repository,
    process_candidate, or translation_provider in any way, and never
    creates or persists a CandidateSignal or NewsStory."""
    scan_id = f"scan-{uuid.uuid4().hex[:12]}"
    started_at = datetime.now(timezone.utc).isoformat()
    max_candidates_to_process = clamp_max_candidates(max_candidates_to_process)

    scan_result = scan_service.scan(client, companies, cache_dir, lookback_days=lookback_days)

    filing_candidate_shadow_matches: tuple[FilingDerivedNewsCandidate, ...] = ()
    filing_candidate_shadow_diagnostics: tuple[str, ...] = ()
    if filing_candidate_shadow_enabled:
        filing_candidate_shadow_matches, filing_candidate_shadow_diagnostics = (
            _build_dart_filing_candidate_shadow_report(scan_result)
        )

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

    # Automatic retry of stale RETRIEVAL_FAILED candidates — separately
    # budgeted, never touches to_process/to_defer above. A candidate not
    # reached by AUTOMATIC_RETRY_MAX_PER_TICK this tick is left completely
    # untouched (not PROCESSING_DEFERRED). PARSE_FAILED is never included.
    stale_failures = sorted(
        (c for c in store.values() if retry_policy.automatic_retry_eligible(c)),
        key=lambda c: (c.filing.rcept_dt, c.filing.rcept_no),
    )[:retry_policy.AUTOMATIC_RETRY_MAX_PER_TICK]

    for candidate in stale_failures:
        retried = process_candidate(client, translation_provider, candidate, cache_dir, counters, error_counts)
        if candidate_repository is None:
            candidate_store.update_candidate(cache_dir, retried)
        else:
            candidate_repository.update_candidate(retried)

    # Automatic, bounded retry of stale translation failures whose cause
    # is retryable (rate limit/timeout/network/transient provider error) —
    # translation reliability workstream. Separately budgeted from every
    # loop above; re-attempts only the translation itself (never
    # re-fetches/re-extracts the document), so a NEEDS_REVIEW candidate
    # with TranslationState.UNAVAILABLE is picked up here without ever
    # re-entering the retrieval/extraction eligibility pool above.
    stale_translation_failures = sorted(
        (c for c in store.values() if translation_service.translation_retry_eligible(c)),
        key=lambda c: (c.filing.rcept_dt, c.filing.rcept_no),
    )[:translation_service.AUTOMATIC_TRANSLATION_RETRY_MAX_PER_TICK]

    for candidate in stale_translation_failures:
        retried = translation_service.retry_translation_for_candidate(translation_provider, candidate, cache_dir)
        if candidate_repository is None:
            candidate_store.update_candidate(cache_dir, retried)
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
        filing_candidate_shadow_matches=filing_candidate_shadow_matches,
        filing_candidate_shadow_diagnostics=filing_candidate_shadow_diagnostics,
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
