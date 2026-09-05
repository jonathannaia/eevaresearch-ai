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
from src.data_access.dart import candidate_store
from src.data_access.dart.candidate_store import CandidatePersistence
from src.data_access.daily_news.edgar_filing_candidate_adapter import map_edgar_filing_to_candidate
from src.data_access.daily_news.filing_event_models import FilingDerivedNewsCandidate
from src.data_access.edgar import document_service, edgar_rules, scan_service
from src.logic.signal_decision_policy import SignalRoute, decide_signal_route
from src.data_access.edgar.client import EdgarClient
from src.models.models import (
    CandidateSignal,
    CandidateStatus,
    EvidenceLocation,
    ExtractionState,
    LocationKind,
    StateTransition,
    TranslationState,
    build_flag_reason,
    record_excerpt,
)

_PROCESSABLE_CONFIDENCE_LEVELS = frozenset({"Moderate", "High"})

DEFAULT_MAX_CANDIDATES_PER_SCAN = 5
MAX_CANDIDATES_PER_SCAN_CEILING = 10

_ELIGIBLE_STATUSES = frozenset({CandidateStatus.CANDIDATE_DETECTED, CandidateStatus.PROCESSING_DEFERRED})

CANDIDATE_STORE_FILENAME = "edgar_candidates.json"

# Daily News Filing-Event Shadow Adapter, Batch 2b — see module-level
# docstring addition below _build_edgar_filing_candidate_shadow_report()
# for the full rationale. Combined cap across both mapping-exception and
# duplicate-join-key diagnostics, per pipeline run.
_FILING_CANDIDATE_SHADOW_DIAGNOSTICS_CAP = 20


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
    # Daily News Filing-Event Shadow Adapter, Batch 2b — additive,
    # defaulted so every existing construction of this dataclass
    # (positional or keyword) is unaffected. Populated only when
    # run_pipeline's own filing_candidate_shadow_enabled parameter is
    # True; empty otherwise, including for every existing caller/test
    # that omits the parameter. In-memory report data only — never
    # written to any store, never rendered by any UI, never read by
    # scripts/daily_news_worker.py or any Daily News code path. See
    # _build_edgar_filing_candidate_shadow_report()'s own docstring for
    # the full read-only guarantee and the join/diagnostic rules.
    filing_candidate_shadow_matches: tuple[FilingDerivedNewsCandidate, ...] = ()
    filing_candidate_shadow_diagnostics: tuple[str, ...] = ()


def _build_edgar_filing_candidate_shadow_report(
    scan_result: "scan_service.ScanResult",
) -> tuple[tuple[FilingDerivedNewsCandidate, ...], tuple[str, ...]]:
    """Daily News Filing-Event Shadow Adapter, Batch 2b — pure, in-memory
    only; never called unless run_pipeline's own filing_candidate_shadow_
    enabled parameter is True. Never creates, persists, or displays
    anything; never touches candidate_store, translation, or any UI.

    Builds a (source_name, corp_code, rcept_no) -> matched_rules lookup
    from scan_result.new_candidate_signals — FilingEvent's own documented
    natural identity key, never list position, since new_filing_events
    and new_candidate_signals are NOT parallel/same-length (scan_service.
    scan()'s own loop appends to new_candidate_signals only when a rule
    matched, a strict subset of new_filing_events). Never imports
    CandidateSignal — matched_rules is read off the already-constructed
    signal.filing/signal.matched_rules here, in this pipeline module, and
    handed to map_edgar_filing_to_candidate() as a plain
    tuple[str, ...] | None.

    A duplicate (source_name, corp_code, rcept_no) key across more than
    one CandidateSignal (should never happen given scan_service's own
    per-tick dedup, but never assumed) is treated as "no matched_rules
    available" for that filing — never last-write-wins, never an
    arbitrary pick — plus one sanitized diagnostic recording the
    collision.

    A per-filing mapping exception is caught, recorded as a sanitized
    "{rcept_no}:{ExceptionClassName}" diagnostic (never a raw message),
    and never stops evaluation of the remaining filings. Diagnostics
    (mapping failures and duplicate-key collisions combined) are capped
    at _FILING_CANDIDATE_SHADOW_DIAGNOSTICS_CAP entries for this run."""
    signals_by_key: dict[tuple[str, str, str], tuple[str, ...]] = {}
    duplicate_keys: set[tuple[str, str, str]] = set()
    for signal in scan_result.new_candidate_signals:
        key = (signal.filing.source_name, signal.filing.corp_code, signal.filing.rcept_no)
        if key in signals_by_key:
            duplicate_keys.add(key)
            continue
        signals_by_key[key] = tuple(signal.matched_rules)

    candidates: list[FilingDerivedNewsCandidate] = []
    diagnostics: list[str] = []

    for filing in scan_result.new_filing_events:
        key = (filing.source_name, filing.corp_code, filing.rcept_no)
        if key in duplicate_keys:
            if len(diagnostics) < _FILING_CANDIDATE_SHADOW_DIAGNOSTICS_CAP:
                diagnostics.append(f"{filing.rcept_no}:DuplicateCandidateSignalKey")
            matched_rules = None
        else:
            matched_rules = signals_by_key.get(key) or None

        try:
            candidate = map_edgar_filing_to_candidate(filing, matched_rules=matched_rules)
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

    doc_result = document_service.get_or_fetch_excerpt(client, cik, accession_no, filename, cache_dir, expected_items)
    candidate.extraction_state = doc_result.state
    if doc_result.from_cache:
        counters["cache_hits"] += 1

    final_status_detail = ""
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
        # Evidence-packet foundation, Phase 1 — source-aware location
        # contract: the Item header the excerpt was actually anchored on
        # (already computed while building the excerpt above; never a new
        # parse). UNAVAILABLE for every non-8-K candidate and for an 8-K
        # excerpt that had no Item header to anchor on at all — never a
        # fabricated page/section.
        candidate.evidence_location = (
            EvidenceLocation(kind=LocationKind.SECTION, section=doc_result.location_section)
            if doc_result.location_section else EvidenceLocation(kind=LocationKind.UNAVAILABLE)
        )
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
        # Evidence-packet foundation, Phase 1: refresh the normalized
        # why-flagged record with the (possibly-refined) matched_rules/
        # confidence and EDGAR's own typed shadow-policy reason — this
        # ADDS the existing typed rationale into source_detail, it never
        # weakens or replaces it (the shadow-policy StateTransition note
        # below is unchanged and still the full audit-trail record).
        candidate.flag_reason = build_flag_reason(
            candidate.matched_rules, candidate.confidence,
            source_detail=f"{shadow_decision.route.value}: {shadow_decision.reason} [rules: {', '.join(shadow_decision.rule_ids)}]",
        )
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
    filing_candidate_shadow_enabled: bool = False,
) -> ScanReport:
    """One bounded, idempotent pipeline run. Re-running with the same
    scope never creates duplicate FilingEvents/CandidateSignals
    (scan_service's own dedup) and never re-fetches/re-parses an
    already-processed candidate (document_service's own cache) — this
    function's only new responsibility is deciding *which* eligible
    candidates get processed this run, bounded by
    `max_candidates_to_process`.

    `candidate_repository` (Durable-State Phase 3A) is additive and
    optional. Omitted (the only path any real service entry point uses
    this phase), every candidate-store touch below is exactly today's
    JSON behavior via candidate_store.py. Supplied — synthetic tests
    only this phase — every candidate-store touch in this one call (the
    post-scan upsert, the eligibility-selection read, and both
    processing-loop writes) routes through the same collaborator, so
    candidates this call just detected are visible to its own
    eligibility selection and processing loops. scan_service.scan()'s own
    filing-event read/write is never affected by this parameter.

    `filing_candidate_shadow_enabled` (Daily News Filing-Event Shadow
    Adapter, Batch 2b) is additive and optional, defaulting to False —
    every existing call site that omits it is completely unaffected, and
    this whole branch never executes when it's False. When True,
    _build_edgar_filing_candidate_shadow_report() is evaluated against
    `scan_result` (this tick's own already-tracked-issuer filings and
    candidate signals, already in memory from the scan_service.scan()
    call above — no new fetch) purely to populate the returned
    ScanReport's own `filing_candidate_shadow_matches`/
    `filing_candidate_shadow_diagnostics` fields. This is a read-only
    observation only: it never touches `detected_now`, `store`, the
    candidate_repository, process_candidate, or translation in any way,
    and never creates or persists a CandidateSignal or NewsStory."""
    scan_id = f"edgar-scan-{uuid.uuid4().hex[:12]}"
    started_at = datetime.now(timezone.utc).isoformat()
    max_candidates_to_process = clamp_max_candidates(max_candidates_to_process)

    scan_result = scan_service.scan(client, companies, cache_dir, lookback_days=lookback_days)

    filing_candidate_shadow_matches: tuple[FilingDerivedNewsCandidate, ...] = ()
    filing_candidate_shadow_diagnostics: tuple[str, ...] = ()
    if filing_candidate_shadow_enabled:
        filing_candidate_shadow_matches, filing_candidate_shadow_diagnostics = (
            _build_edgar_filing_candidate_shadow_report(scan_result)
        )

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
        filing_candidate_shadow_matches=filing_candidate_shadow_matches,
        filing_candidate_shadow_diagnostics=filing_candidate_shadow_diagnostics,
    )
