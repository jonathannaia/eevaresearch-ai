"""Durable-State Phase 4M-0 — the standalone, continuous autonomous
Radar worker.

STANDALONE ENTRY POINT ONLY. Not imported by app.py, any UI page, or
any normal scan entry point — the Streamlit dashboard never performs a
recurring external scan on page render, with or without this file
existing. This process is meant to run separately from the dashboard,
on its own always-on worker host or scheduled-job platform (see
design/RADAR_WORKER_DEPLOYMENT.md) — Streamlit Community Cloud hosts
the dashboard, never this loop.

Run (later, once separately approved) as:
    .venv/bin/python -m scripts.radar_worker

Master switch: EDGE_RADAR_LIVE_SCAN_ENABLED (Settings.radar_live_scan_enabled),
default false. If unset/false, main() prints a clear, safe message and
exits 0 immediately — a no-op, not an error — so accidentally starting
this process without the flag is inert.

Backend: EDGE_RADAR_WORKER_DB_BACKEND (Settings.radar_worker_db_backend)
must be exactly "sqlite" or "postgres" — "json" (or unset/blank/
anything else) is a hard, sanitized startup failure. SQLite
(EDGE_RADAR_WORKER_STATE_DB_PATH) is local/test-only; a real deployed
worker running separately from the Streamlit dashboard must use
Postgres (EDGE_RADAR_WORKER_STATE_DB_URL) — see
design/RADAR_WORKER_DEPLOYMENT.md. These are deliberately separate
settings from the ordinary EDGE_DB_BACKEND/EDGE_STATE_DB_URL pair the
dashboard may also have configured, so a dashboard secrets
misconfiguration can never make this worker (or vice versa) silently
point at the wrong database.

This worker NEVER resolves an issuer identifier itself — see
scripts/resolve_tracked_identifiers.py, the one explicit, manual
bootstrap step an operator runs separately, before starting this
process (and again, occasionally, whenever a new tracked issuer is
added). An issuer with no already-resolved identifier is simply skipped
for that tick — the exact behavior scan_service.scan() already has
("... CIK not resolved — run cik_resolver first.", recorded as a
warning, not an error) — and counted in that provider's own
ProviderScanStatus.skipped_unresolved_count.

Provider isolation: each of EDGAR/DART/EDINET is scanned inside its own
try/except per tick — one provider's exception (a missing credential, a
network failure, anything) is caught, recorded in that provider's own
ProviderScanStatus.failure_code (only `type(exc).__name__` — never a raw
exception message, DSN, or credential), and never prevents the other
configured providers from scanning in the same tick.

PUBLISHED-safety: this worker's own settings object always forces
edgar_auto_publish_enabled=False, structurally, regardless of what
EDGE_EDGAR_AUTO_PUBLISH_ENABLED is set to in this process's own
environment — see _build_worker_settings()'s own docstring for why.
This worker never imports review_actions or signal_promotion, and never
constructs a SignalRepository — Signals stays strictly PUBLISHED-only,
entirely gated by the existing human review action, unaffected by
anything in this file. It also never calls
scripts.resolve_tracked_identifiers or any source client/resolver
directly — only each source's own existing, unmodified `run_scan()`.

Locking: a per-(provider, backend) advisory file lock (stdlib `fcntl` —
POSIX only, matching this project's Streamlit Community Cloud + Unix
worker deployment target) held for the duration of that provider's own
scan attempt. A non-blocking lock attempt means an overlapping scan
attempt is *skipped* for that tick, never queued or duplicated. flock()
is released automatically by the OS if the holding process dies for any
reason (crash, kill -9, ...), so a failed run self-heals on the very
next tick with no separate staleness-timeout logic needed.

Graceful shutdown: SIGTERM/SIGINT set a flag checked between providers
and between ticks — an in-progress provider's own scan call is never
interrupted mid-call (that could leave a worse partial-write state than
letting it finish), but the loop will not start a new provider or a new
tick once the flag is set.

Durable-State Phase 4B-2 (design/DECISIONS.md) — autonomous Research
Case creation, EDGAR only. After EDGAR's own scan-status persistence
has already completed successfully (see _run_provider_tick), and only
for provider_key == "edgar", _run_edgar_research_case_step() reads the
already-persisted EDGAR candidate set, runs the existing pure
select_research_lead()/build_research_case_bundle_from_lead()/
validate_research_case_bundle() pipeline via prepare_research_case_
bundles() (bounded to 5 candidates, source "SEC EDGAR" only), and
atomically persists any resulting bundle through the existing
worker-only get_research_case_bundle_writer() seam — never a new
persistence algorithm. This is strictly best-effort: the entire step is
wrapped in one narrow try/except Exception at its call site, and any
exception is swallowed and reported only as a sanitized
"research-case step skipped (<ExceptionType>)" line — it can never
alter ProviderScanStatus, candidate status, retry/backoff state, the
scan report, or any other provider's own tick. DART and EDINET are
completely untouched by this addition; their scan behavior is
byte-for-byte identical to before this phase. `_current_utc_date()` is
the one, deliberately isolated place this file reads the system clock
for this feature — never the pure selector/orchestration/factory
modules themselves, which remain caller-supplied-date-only by their own
design.

Phase A2 (design/DECISIONS.md) — EDGAR-only, post-Research-Case
deterministic Theme matching. After `_run_edgar_research_case_step()`
has already printed its own summary, `_run_theme_matching_step()` runs
in a second, entirely separate try/except in `_run_provider_tick()`:
any exception there is caught and reported only as a sanitized
"theme-matching step skipped (<ExceptionType>)" line, and can never
alter ProviderScanStatus, candidate state, Research Case creation, the
research-case step's own summary/counters, or DART/EDINET. Matching
uses the existing pure `evaluate_theme_match()`
(src.logic.research_case_theme_matching) against every active
`ThemeMatchingScope` loaded once per step via the existing private
`get_theme_matching_repository()` seam, and considers two case
sources: (1) every Research Case bundle inserted this same tick, and
(2) a bounded recent-case catch-up window
(`ResearchCaseRepositoryProtocol.list_recent_cases(_THEME_MATCHING_BACKLOG_MAX_CASES)`)
filtered to `trigger_source_type == "radar"` cases whose
`trigger_source_id` resolves in the already-loaded EDGAR candidate
mapping. This is a bounded, recency-ordered catch-up window — not a
complete historical reconciliation, and not a guarantee that every
past unmatched case will eventually be examined; a case that ages out
of the most-recent-N window before ever receiving a scope is not
retried by this hook. Every stored match is an insert-only, internal
`ResearchCaseThemeMatch` with `direction=EvidenceDirection.CONTEXT`;
this step never creates a Theme, evidence item, company-map entry,
review decision, or visibility change, and never calls an LLM or any
external/network service.

Autonomous Theme candidate detection (design/DECISIONS.md) —
`_run_theme_candidate_detection_step()`, gated by
`settings.theme_candidate_detection_enabled` (default disabled), runs
after the theme-matching step above, in its own separate try/except.
Uses the pure, general `src.logic.theme_candidate_detection` engine to
cluster already-case-linked EDGAR candidates by
(theme_slug, subtheme_slug) and, when independent official-source
evidence for one cluster crosses a configurable threshold within a
configurable window, auto-creates one INTERNAL candidate
`ResearchTheme` plus its `ThemeMatchingScope`, bootstrap CONTEXT-only
`ResearchCaseThemeMatch` rows for the contributing cases, `role=EXPOSED`
`ThemeCompanyMapEntry` rows for every contributing company, and two
`ThemeResearchNote`s (a HYPOTHESIS with confidence/disconfirming
condition, and a DECISION explaining exactly why the candidate fired).
Never auto-creates a `ThemeEvidenceItem` or a review decision, never
promotes CONTEXT to SUPPORTS/CONTRADICTS/MIXED, and never changes
visibility — every one of those stays a human action through the
existing src/ui/pages/theme_workspace.py workspace. The constraint
keyword/rule-category vocabulary and the threshold/window are plain
module-level constants here, not hardcoded inside the detection engine
itself, which takes them as parameters — the engine is general-purpose,
this file's own constants are just today's semiconductor/AI-
infrastructure starting seed.
"""
from __future__ import annotations

import dataclasses
import fcntl
import signal
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from src.config.settings import Settings, get_settings
from src.data_access import backend_factory
from src.data_access.dart import radar_service as dart_radar_service
from src.data_access.edgar import edgar_service
from src.data_access.edinet import edinet_service
from src.data_access.state_db.scan_status_repository import ProviderScanStatus
from src.data_access.theme_store import build_theme_company_map_id, build_theme_id, build_theme_research_note_id
from src.logic.research_case_theme_matching import evaluate_theme_match
from src.logic.research_lead_orchestration import ResearchLeadOrchestrationConfig, prepare_research_case_bundles
from src.logic.theme_auto_publish import evaluate_auto_publish_gates
from src.logic.theme_candidate_detection import detect_theme_candidates
from src.models.research_case import ResearchCase
from src.models.theme_matching import ThemeMatchingScope
from src.models.theme_research import (
    CompanyRole,
    HypothesisConfidence,
    ResearchTheme,
    ThemeCategory,
    ThemeCompanyMapEntry,
    ThemeNoteType,
    ThemeResearchNote,
    ThemeStatus,
    ThemeVisibility,
)

# CandidateSignal (src.models.models) is deliberately never imported here,
# even just for a type hint — see tests/test_radar_worker_safety_invariants.py's
# own structural proof that this worker never imports that module at all
# (it's where CandidateStatus.PUBLISHED/MONITORING/DISMISSED live). This
# file's own `from __future__ import annotations` (PEP 563) means every
# annotation below is a string, never evaluated at runtime, so the bare
# name "CandidateSignal" in a type hint works without importing it.

# Duck-typed deliberately: postgres_state_db.scan_status_repository.ProviderScanStatus
# has an identical field shape, and each repository's own upsert_scan_status()
# reads attributes by name, not by isinstance check — so this one dataclass
# is used uniformly for both backends. See scan_status_repository.py's own
# docstring for the shared shape.

_PROVIDERS: tuple[str, ...] = ("edgar", "dart", "edinet")
_SOURCE_DISPLAY_NAMES = {"edgar": "SEC EDGAR", "dart": "OpenDART / DART", "edinet": "EDINET"}
_SERVICE_MODULES = {"edgar": edgar_service, "dart": dart_radar_service, "edinet": edinet_service}

_LOCK_DIR = Path(tempfile.gettempdir()) / "eevaresearch-radar-worker-locks"
_MIN_INTERVAL_SECONDS = 60  # defense-in-depth floor, regardless of a misconfigured tiny interval value

# EDINET Extraordinary Report shadow-observation workstream (design/
# DECISIONS.md) — small, fixed cap on the per-tick shadow-match log
# list, independent of _MIN_INTERVAL_SECONDS above.
_SHADOW_MATERIAL_EVENT_LOG_CAP = 5


class WorkerConfigurationError(Exception):
    """Raised at startup for a sanitized, fatal configuration problem —
    never a raw exception, DSN, or credential."""


_shutdown_requested = False


def _handle_shutdown_signal(signum: int, frame: object) -> None:
    global _shutdown_requested
    _shutdown_requested = True


def _install_signal_handlers() -> None:
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)


@contextmanager
def _provider_lock(lock_key: str) -> Iterator[bool]:
    """Yields True if the lock was acquired (caller should proceed),
    False if another process already holds it (caller should skip this
    tick for this provider) — never blocks waiting for it."""
    _LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = _LOCK_DIR / f"{lock_key}.lock"
    fh = open(lock_path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        yield False
        return
    try:
        yield True
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def _build_worker_settings(ambient: Settings) -> Settings:
    """Constructs the ONE explicit Settings object every scan this
    worker performs actually uses — never the ambient `ambient` object
    directly, for two structural safety reasons: (1) db_backend/
    state_db_path/state_db_url come from the dedicated
    EDGE_RADAR_WORKER_* fields, never the ordinary EDGE_DB_BACKEND/
    EDGE_STATE_DB_URL pair the dashboard might also have set;
    (2) edgar_auto_publish_enabled is forced False here, structurally,
    regardless of EDGE_EDGAR_AUTO_PUBLISH_ENABLED's real value in this
    process's environment — this worker must never be able to
    autonomously set a candidate to PUBLISHED, and that pre-existing,
    separate feature flag is the one code path in this codebase that
    could otherwise do so (see edgar_pipeline.run_pipeline's own
    auto_publish_enabled parameter and design/DECISIONS.md's own record
    of this exact finding)."""
    backend = ambient.radar_worker_db_backend
    if backend not in ("sqlite", "postgres"):
        raise WorkerConfigurationError(
            'EDGE_RADAR_WORKER_DB_BACKEND must be exactly "sqlite" or "postgres" for continuous '
            f"worker mode (got {backend!r}). JSON is not supported for a separate dashboard+worker pair."
        )
    if backend == "sqlite" and not ambient.radar_worker_state_db_path:
        raise WorkerConfigurationError(
            "EDGE_RADAR_WORKER_STATE_DB_PATH is required when EDGE_RADAR_WORKER_DB_BACKEND=sqlite."
        )
    if backend == "postgres" and not ambient.radar_worker_state_db_url:
        raise WorkerConfigurationError(
            "EDGE_RADAR_WORKER_STATE_DB_URL is required when EDGE_RADAR_WORKER_DB_BACKEND=postgres."
        )

    return dataclasses.replace(
        ambient,
        db_backend=backend,
        state_db_path=ambient.radar_worker_state_db_path,
        state_db_url=ambient.radar_worker_state_db_url,
        edgar_auto_publish_enabled=False,
    )


def _record_failure(scan_status_repo, display_source: str, previous, started_at: str, failure_code: str) -> None:
    """Preserves the last known-good cursor/last_successful_at/counts
    from `previous` (if any) — a failed tick must never erase the
    provider's prior progress, only record that this attempt failed."""
    now = datetime.now(timezone.utc).isoformat()
    scan_status_repo.upsert_scan_status(ProviderScanStatus(
        provider=display_source,
        cursor_value=previous.cursor_value if previous else None,
        started_at=started_at,
        completed_at=now,
        last_successful_at=previous.last_successful_at if previous else None,
        items_discovered=previous.items_discovered if previous else 0,
        candidates_created=previous.candidates_created if previous else 0,
        skipped_unresolved_count=previous.skipped_unresolved_count if previous else 0,
        failure_code=failure_code,
        updated_at=now,
    ))


def _current_utc_date() -> str:
    """The one, deliberately isolated system-clock read for the
    autonomous Research Case step — a standalone function so tests can
    monkeypatch it directly for deterministic behavior. The pure
    selector/orchestration/factory modules this feeds into never read
    the clock themselves; this is the single caller-supplied boundary
    value they require."""
    return datetime.now(timezone.utc).date().isoformat()


# EDGAR's own existing, already-configured scan lookback default — not
# a new environment variable or configuration surface. Reused exactly
# as edgar_service.run_scan()'s own default already is at this file's
# scan call site above.
_EDGAR_RESEARCH_CASE_MAX_CANDIDATES = 5
_EDGAR_ALLOWED_SOURCE_NAMES = ("SEC EDGAR",)

# Phase A2 (design/DECISIONS.md). A bounded recent-case *catch-up*
# window, not a complete historical reconciliation: list_recent_cases()
# always returns the globally most-recent N ResearchCase rows, so a
# case that ages out of this window before ever receiving a scope is
# not retried by this hook. That is a deliberate, accepted tradeoff —
# guaranteeing eventual coverage of arbitrarily old history is a
# separate, not-yet-approved one-time backfill concern, not this tick-
# level hook's job.
_THEME_MATCHING_BACKLOG_MAX_CASES = 25


def _run_edgar_research_case_step(
    worker_settings: Settings, candidate_repository,
) -> tuple[str, dict[str, CandidateSignal], tuple[ResearchCase, ...]]:
    """Best-effort, EDGAR-only autonomous Research Case creation — see
    module docstring. Never called for dart/edinet. Raises on any
    unexpected failure in repository construction, the candidate load,
    or the orchestration call itself; the caller (_run_provider_tick)
    is the one place that catches and sanitizes that exception, since
    this function's own job is only to do the work and build the one
    summary line, not to decide how a failure is reported.

    A single bundle's `writer.insert_bundle()` call is individually
    isolated (its own try/except) so one unexpected write failure never
    prevents the remaining prepared bundles in the same tick from being
    attempted — each bundle's atomic insert is already fully
    self-contained (its own transaction/validation), so per-bundle
    isolation costs nothing extra here.

    Also returns the already-loaded EDGAR `candidates` mapping and the
    `ResearchCase` objects for every bundle actually inserted this tick
    — Phase A2's `_run_theme_matching_step()` reuses both directly so
    it never re-loads the same candidate table a second time."""
    research_case_repository = backend_factory.get_research_case_repository(worker_settings)
    writer = backend_factory.get_research_case_bundle_writer(worker_settings)

    candidates = candidate_repository.load_candidates()
    config = ResearchLeadOrchestrationConfig(
        as_of_date=_current_utc_date(),
        lookback_days=edgar_service.scan_service.DEFAULT_LOOKBACK_DAYS,
        max_candidates=_EDGAR_RESEARCH_CASE_MAX_CANDIDATES,
        allowed_source_names=_EDGAR_ALLOWED_SOURCE_NAMES,
    )
    result = prepare_research_case_bundles(candidates.values(), research_case_repository.existing_case_ids, config)

    created = 0
    write_rejected = 0
    newly_created_cases: list[ResearchCase] = []
    for bundle in result.bundles:
        try:
            inserted = writer.insert_bundle(bundle)
        except Exception:  # noqa: BLE001 — one bundle's write failure must never block the rest of this batch
            write_rejected += 1
            continue
        if inserted:
            created += 1
            newly_created_cases.append(bundle.case)
        else:
            write_rejected += 1

    summary = (
        f"EDGAR: research cases — evaluated={result.evaluated_count} created={created} "
        f"existing={result.already_existing_count} not_qualified={result.not_qualified_count} "
        f"factory_rejected={result.factory_rejected_count} validation_rejected={result.validation_rejected_count} "
        f"write_rejected={write_rejected}"
    )
    if result.membership_check_failed_count:
        summary += f" membership_check_failed={result.membership_check_failed_count}"
    return summary, candidates, tuple(newly_created_cases)


# Autonomous Theme candidate detection, Phase 2 (design/DECISIONS.md) —
# a bounded per-tick cap for DART/EDINET research-case creation,
# mirroring _EDGAR_RESEARCH_CASE_MAX_CANDIDATES's own value exactly
# (same conservative default, not a new policy decision).
_SOURCE_RESEARCH_CASE_MAX_CANDIDATES = 5

# The directly-imported, never-monkeypatched real service modules —
# deliberately NOT `_SERVICE_MODULES[provider_key]`, which tests
# legitimately replace with a fake `run_scan`-only namespace for the
# scan call itself. Mirrors _run_edgar_research_case_step's own
# pattern of reading `edgar_service.scan_service.DEFAULT_LOOKBACK_DAYS`
# from the real, directly-imported module rather than the mutable
# dispatch dict.
_LOOKBACK_SERVICE_MODULES = {"dart": dart_radar_service, "edinet": edinet_service}


def _run_source_research_case_step(
    provider_key: str, worker_settings: Settings, candidate_repository,
) -> tuple[str, dict[str, CandidateSignal], tuple[ResearchCase, ...]]:
    """Best-effort, DART/EDINET autonomous Research Case creation —
    Phase 2's generic counterpart to _run_edgar_research_case_step
    above, which stays completely untouched (never refactored to share
    this function, to avoid any risk to its own already-verified
    behavior). `research_lead_orchestration`/`research_lead_selection`/
    `research_lead_factory` were already fully source-agnostic before
    this phase — the only thing that changes per provider is
    `allowed_source_names` and the lookback default, both already
    exposed per-source exactly like EDGAR's own. Never called for
    provider_key == 'edgar'. Same isolation contract as the EDGAR
    step: the caller (_run_provider_tick) is the one place that
    catches and sanitizes any exception raised here."""
    display_source = _SOURCE_DISPLAY_NAMES[provider_key]
    lookback_service_module = _LOOKBACK_SERVICE_MODULES[provider_key]

    research_case_repository = backend_factory.get_research_case_repository(worker_settings)
    writer = backend_factory.get_research_case_bundle_writer(worker_settings)

    candidates = candidate_repository.load_candidates()
    config = ResearchLeadOrchestrationConfig(
        as_of_date=_current_utc_date(),
        lookback_days=lookback_service_module.scan_service.DEFAULT_LOOKBACK_DAYS,
        max_candidates=_SOURCE_RESEARCH_CASE_MAX_CANDIDATES,
        allowed_source_names=(display_source,),
    )
    result = prepare_research_case_bundles(candidates.values(), research_case_repository.existing_case_ids, config)

    created = 0
    write_rejected = 0
    newly_created_cases: list[ResearchCase] = []
    for bundle in result.bundles:
        try:
            inserted = writer.insert_bundle(bundle)
        except Exception:  # noqa: BLE001 — one bundle's write failure must never block the rest of this batch
            write_rejected += 1
            continue
        if inserted:
            created += 1
            newly_created_cases.append(bundle.case)
        else:
            write_rejected += 1

    summary = (
        f"{provider_key.upper()}: research cases — evaluated={result.evaluated_count} created={created} "
        f"existing={result.already_existing_count} not_qualified={result.not_qualified_count} "
        f"factory_rejected={result.factory_rejected_count} validation_rejected={result.validation_rejected_count} "
        f"write_rejected={write_rejected}"
    )
    if result.membership_check_failed_count:
        summary += f" membership_check_failed={result.membership_check_failed_count}"
    return summary, candidates, tuple(newly_created_cases)


_ZERO_SCOPE_THEME_MATCHING_SUMMARY = (
    "EDGAR: theme matching — scopes_loaded=0 cases_considered=0 "
    "matches_created=0 matches_existing=0 no_match=0 matching_errors=0"
)


def _run_theme_matching_step(
    worker_settings: Settings,
    candidates: dict[str, CandidateSignal],
    newly_created_cases: tuple[ResearchCase, ...],
) -> str:
    """Phase A2 (design/DECISIONS.md) — best-effort, EDGAR-only,
    post-Research-Case deterministic Theme matching. Called only after
    `_run_edgar_research_case_step()` has already succeeded and printed
    its own summary; the caller (`_run_provider_tick`) is the one place
    that catches and sanitizes any exception raised here, in a try/
    except entirely separate from the research-case step's own — a
    failure in this function can never affect ProviderScanStatus,
    candidate state, Research Case creation, or the research-case
    step's own summary/counters.

    Considers two case sources: every Research Case bundle inserted
    this same tick (`newly_created_cases`, evaluated first and never
    duplicated), plus a bounded recent-case catch-up window (see
    `_THEME_MATCHING_BACKLOG_MAX_CASES`'s own comment for why this is
    not a complete historical reconciliation). Repository construction,
    `list_active_scopes()`, `list_recent_cases()`, and the bulk
    existing-match lookup are all unguarded here — a failure in any of
    them aborts this whole function and propagates to the caller's own
    try/except, exactly like `_run_edgar_research_case_step`'s own
    repository-construction failures do today. Per-`(case, scope)`
    evaluation/insert failures, and a defensive missing-candidate case,
    are each isolated so one bad pair never blocks the rest of the
    bounded batch."""
    matching_repository = backend_factory.get_theme_matching_repository(worker_settings)
    scopes = matching_repository.list_active_scopes()
    if not scopes:
        return _ZERO_SCOPE_THEME_MATCHING_SUMMARY

    research_case_repository = backend_factory.get_research_case_repository(worker_settings)
    recent_cases = research_case_repository.list_recent_cases(_THEME_MATCHING_BACKLOG_MAX_CASES)
    backlog_eligible = [
        case for case in recent_cases
        if case.trigger_source_type == "radar" and case.trigger_source_id in candidates
    ]
    newly_created_ids = {case.id for case in newly_created_cases}
    combined_cases = list(newly_created_cases) + [case for case in backlog_eligible if case.id not in newly_created_ids]

    case_ids = tuple(dict.fromkeys(case.id for case in combined_cases))
    existing_match_ids = matching_repository.existing_match_ids_for_case_ids(case_ids)

    cases_considered = 0
    matches_created = 0
    matches_existing = 0
    no_match = 0
    matching_errors = 0

    for case in combined_cases:
        candidate = candidates.get(case.trigger_source_id)
        if candidate is None:
            matching_errors += 1
            continue
        cases_considered += 1
        for scope in scopes:
            try:
                match = evaluate_theme_match(candidate, case.id, scope, case.created_at)
            except Exception:  # noqa: BLE001 — one bad (case, scope) pair must never block the rest of the batch
                matching_errors += 1
                continue
            if match is None:
                no_match += 1
                continue
            if match.id in existing_match_ids:
                matches_existing += 1
                continue
            try:
                inserted = matching_repository.insert_match(match)
            except Exception:  # noqa: BLE001 — same isolation as the evaluation call above
                matching_errors += 1
                continue
            if inserted:
                matches_created += 1
            else:
                matches_existing += 1

    return (
        f"EDGAR: theme matching — scopes_loaded={len(scopes)} cases_considered={cases_considered} "
        f"matches_created={matches_created} matches_existing={matches_existing} "
        f"no_match={no_match} matching_errors={matching_errors}"
    )


# Autonomous Theme candidate detection — plain worker-level tunables,
# not hardcoded inside src.logic.theme_candidate_detection itself (see
# that module's own docstring for why). Today's starting seed is
# semiconductor/AI-infrastructure-flavored, matching the same values an
# operator would otherwise type into scripts/create_theme_matching_scope.py
# by hand — the engine itself has no opinion about sector.
_THEME_CANDIDATE_DETECTION_WINDOW_DAYS = 90
_THEME_CANDIDATE_DETECTION_MIN_COMPANIES = 2
_THEME_CANDIDATE_DETECTION_RULE_CATEGORIES: tuple[str, ...] = (
    "material_agreement", "financing_or_debt", "other_material_event",
)
_THEME_CANDIDATE_DETECTION_KEYWORDS: tuple[str, ...] = (
    "capacity", "wafer", "fab", "foundry", "packaging", "hbm", "dram", "allocation",
    "lead time", "yield", "node", "supply agreement", "capacity expansion", "shortage", "backlog",
)
_THEME_CANDIDATE_DETECTION_EXCLUDED_KEYWORDS: tuple[str, ...] = (
    "share repurchase", "stock buyback", "dividend declaration",
    "annual meeting of stockholders", "proxy statement", "executive compensation",
)


def _gather_case_candidate_pairs_for_detection(
    worker_settings: Settings, candidates: dict[str, CandidateSignal], newly_created_cases: tuple[ResearchCase, ...],
) -> list[tuple[ResearchCase, CandidateSignal]]:
    """The same bounded reachable-case logic _run_theme_matching_step
    already computes internally (Phase A2) — reimplemented here rather
    than refactored out of that already-verified function, to avoid any
    risk of altering its tested behavior. Bounded to `newly_created_cases`
    (this tick) plus the same `_THEME_MATCHING_BACKLOG_MAX_CASES`-sized
    recent-case window — not a full historical scan."""
    research_case_repository = backend_factory.get_research_case_repository(worker_settings)
    recent_cases = research_case_repository.list_recent_cases(_THEME_MATCHING_BACKLOG_MAX_CASES)
    backlog_eligible = [
        case for case in recent_cases
        if case.trigger_source_type == "radar" and case.trigger_source_id in candidates
    ]
    newly_created_ids = {case.id for case in newly_created_cases}
    combined_cases = list(newly_created_cases) + [case for case in backlog_eligible if case.id not in newly_created_ids]
    pairs: list[tuple[ResearchCase, CandidateSignal]] = []
    for case in combined_cases:
        candidate = candidates.get(case.trigger_source_id)
        if candidate is not None:
            pairs.append((case, candidate))
    return pairs


def _run_theme_candidate_detection_step(
    worker_settings: Settings, candidates: dict[str, CandidateSignal], newly_created_cases: tuple[ResearchCase, ...],
) -> str:
    """Best-effort, EDGAR-only autonomous Theme CANDIDATE detection —
    see module docstring. Called only after `_run_theme_matching_step()`
    has already returned, in its own separate try/except at the call
    site — a failure here can never affect ProviderScanStatus, candidate
    state, Research Case creation, or either theme-matching step's own
    summary/counters. Repository construction and the pure
    `detect_theme_candidates()` call are unguarded (a failure there
    aborts this whole function, exactly like the sibling steps' own
    repository-construction failures do); each detected candidate's own
    persistence sequence is wrapped so one bad candidate never blocks
    the rest of the batch."""
    matching_repository = backend_factory.get_theme_matching_repository(worker_settings)
    curator = backend_factory.get_theme_curator_repository(worker_settings)

    active_scopes = matching_repository.list_active_scopes()
    already_covered: set[tuple[str, str | None]] = set()
    for scope in active_scopes:
        for tag in scope.sector_tags:
            already_covered.add((tag, None))
            for subtag in scope.sector_subtags:
                already_covered.add((tag, subtag))

    pairs = _gather_case_candidate_pairs_for_detection(worker_settings, candidates, newly_created_cases)
    detected = detect_theme_candidates(
        pairs, as_of_date=_current_utc_date(), window_days=_THEME_CANDIDATE_DETECTION_WINDOW_DAYS,
        min_distinct_companies=_THEME_CANDIDATE_DETECTION_MIN_COMPANIES,
        constraint_keywords=_THEME_CANDIDATE_DETECTION_KEYWORDS,
        constraint_rule_categories=_THEME_CANDIDATE_DETECTION_RULE_CATEGORIES,
        already_covered=frozenset(already_covered),
    )

    case_by_id = {case.id: case for case, _candidate in pairs}
    candidate_by_case_id = {case.id: candidate for case, candidate in pairs}

    clusters_detected = len(detected)
    themes_created = 0
    matches_created = 0
    company_roles_created = 0
    notes_created = 0
    creation_errors = 0

    for theme_candidate in detected:
        try:
            created_at = datetime.now(timezone.utc).isoformat()
            title = theme_candidate.research_question
            theme_id = build_theme_id(title, created_at)
            theme = ResearchTheme(
                id=theme_id, category=ThemeCategory.BOTTLENECK, status=ThemeStatus.NEW, visibility=ThemeVisibility.INTERNAL,
                title=title, key_question=theme_candidate.research_question, hypothesis=theme_candidate.hypothesis_statement,
                working_thesis=theme_candidate.working_thesis, why_it_matters=theme_candidate.why_it_matters,
                what_could_change_the_view=theme_candidate.what_could_change_the_view,
                what_to_watch_next=theme_candidate.what_to_watch_next, created_at=created_at, updated_at=created_at,
            )
            if not curator.insert_theme(theme):
                creation_errors += 1
                continue
            themes_created += 1

            scope = ThemeMatchingScope(
                theme_id=theme_id, sector_tags=(theme_candidate.theme_slug,),
                sector_subtags=(theme_candidate.subtheme_slug,) if theme_candidate.subtheme_slug else (),
                allowed_matched_rule_categories=theme_candidate.matched_rule_categories,
                required_keywords=theme_candidate.matched_keywords,
                excluded_keywords=_THEME_CANDIDATE_DETECTION_EXCLUDED_KEYWORDS,
            )
            matching_repository.insert_scope(scope)

            for case_id in theme_candidate.member_case_ids:
                member_case = case_by_id.get(case_id)
                member_candidate = candidate_by_case_id.get(case_id)
                if member_case is None or member_candidate is None:
                    continue
                try:
                    match = evaluate_theme_match(member_candidate, case_id, scope, member_case.created_at)
                except Exception:  # noqa: BLE001 — one bad bootstrap match must never block the rest
                    continue
                if match is None:
                    continue
                try:
                    if matching_repository.insert_match(match):
                        matches_created += 1
                except Exception:  # noqa: BLE001 — same isolation as above
                    continue

            for company_name in theme_candidate.company_names:
                entry = ThemeCompanyMapEntry(
                    id=build_theme_company_map_id(theme_id, company_name, CompanyRole.EXPOSED),
                    theme_id=theme_id, company_name=company_name, role=CompanyRole.EXPOSED,
                    note="Auto-detected: filed a constraint-relevant disclosure contributing to this candidate.",
                )
                try:
                    if curator.insert_company_map_entry(entry):
                        company_roles_created += 1
                except Exception:  # noqa: BLE001 — one bad company-map insert must never block the rest
                    continue

            hypothesis_note = ThemeResearchNote(
                id=build_theme_research_note_id(theme_id, ThemeNoteType.HYPOTHESIS, theme_candidate.hypothesis_statement, created_at),
                theme_id=theme_id, note_type=ThemeNoteType.HYPOTHESIS, content=theme_candidate.hypothesis_statement,
                confidence=HypothesisConfidence.MEDIUM, disconfirming_condition=theme_candidate.disconfirming_condition,
                created_at=created_at,
            )
            decision_note = ThemeResearchNote(
                id=build_theme_research_note_id(theme_id, ThemeNoteType.DECISION, theme_candidate.rationale_summary, created_at),
                theme_id=theme_id, note_type=ThemeNoteType.DECISION, content=theme_candidate.rationale_summary,
                confidence=None, disconfirming_condition=None, created_at=created_at,
            )
            for note in (hypothesis_note, decision_note):
                try:
                    if curator.insert_research_note(note):
                        notes_created += 1
                except Exception:  # noqa: BLE001 — one bad note insert must never block the rest
                    continue
        except Exception:  # noqa: BLE001 — one bad candidate must never block the rest of the batch
            creation_errors += 1
            continue

    return (
        f"EDGAR: theme candidate detection — clusters_detected={clusters_detected} themes_created={themes_created} "
        f"matches_created={matches_created} company_roles_created={company_roles_created} notes_created={notes_created} "
        f"creation_errors={creation_errors}"
    )


def _run_theme_auto_publish_step(worker_settings: Settings) -> str:
    """Autonomous Theme candidate detection, Phase 2 (design/
    DECISIONS.md) — best-effort, cross-market autonomous publication.
    Gated by `worker_settings.theme_auto_publish_enabled` (default
    disabled) at the call site in `run_one_tick()`; this function
    itself does not re-check the flag. Only ever evaluates themes
    currently at `visibility == internal` — once a theme leaves that
    state (published or archived), it is never reconsidered here again,
    which is what makes this step naturally idempotent across ticks.
    Uses the pure, shared `src.logic.theme_auto_publish.
    evaluate_auto_publish_gates` — the exact same function
    src/ui/pages/theme_workspace.py's own live eligibility display
    calls — so the worker and the UI can never disagree about whether a
    theme is eligible. On a successful publish, inserts exactly one
    immutable DECISION `ThemeResearchNote` recording every gate's
    outcome and the evidence ids that satisfied it — never written for
    a failed evaluation, so this step never spams the research log.
    Never creates evidence, never changes a company-map entry, never
    touches a theme that is not currently internal. One bad theme's
    evaluation/publish failure never blocks the rest of the batch."""
    curator = backend_factory.get_theme_curator_repository(worker_settings)
    themes = curator.list_themes()
    internal_themes = [t for t in themes if t.visibility is ThemeVisibility.INTERNAL]

    themes_considered = len(internal_themes)
    themes_published = 0
    themes_ineligible = 0
    evaluation_errors = 0

    for theme in internal_themes:
        try:
            evidence = curator.evidence_for_theme(theme.id)
            notes = curator.research_notes_for_theme(theme.id)
            evaluation = evaluate_auto_publish_gates(theme, evidence, notes)
            if not evaluation.eligible:
                themes_ineligible += 1
                continue

            updated_at = datetime.now(timezone.utc).isoformat()
            updated = curator.set_visibility(theme.id, ThemeVisibility.PUBLISHED, updated_at)
            if updated is None:
                evaluation_errors += 1
                continue
            themes_published += 1

            audit_note = ThemeResearchNote(
                id=build_theme_research_note_id(theme.id, ThemeNoteType.DECISION, evaluation.audit_summary, updated_at),
                theme_id=theme.id, note_type=ThemeNoteType.DECISION, content=evaluation.audit_summary,
                confidence=None, disconfirming_condition=None, created_at=updated_at,
            )
            try:
                curator.insert_research_note(audit_note)
            except Exception:  # noqa: BLE001 — the publish itself already succeeded; a note-insert failure must not be reported as a publish failure
                pass
        except Exception:  # noqa: BLE001 — one bad theme's evaluation must never block the rest of the batch
            evaluation_errors += 1
            continue

    return (
        f"EDGAR: theme auto-publish — themes_considered={themes_considered} themes_published={themes_published} "
        f"themes_ineligible={themes_ineligible} evaluation_errors={evaluation_errors}"
    )


_NO_CASES_GATHERED: tuple[dict[str, CandidateSignal], tuple[ResearchCase, ...]] = ({}, ())


def _run_provider_tick(
    provider_key: str, worker_settings: Settings, scan_status_repo,
) -> tuple[dict[str, CandidateSignal], tuple[ResearchCase, ...]]:
    """Phase 2 (design/DECISIONS.md): now returns this tick's own
    `(candidates, newly_created_cases)` for EVERY provider — EDGAR,
    DART, and EDINET alike — so `run_one_tick()` can merge all three
    into one cross-market pool for the theme-matching/detection/auto-
    publish steps, which no longer run nested inside this function at
    all (see `run_one_tick()`'s own docstring for why: providers are
    processed in a fixed order, so a step nested inside one provider's
    own branch could never see a later provider's same-tick output).
    Returns `_NO_CASES_GATHERED` (empty dict, empty tuple) on every
    early-return path (lock not acquired, scan failure, research-case
    step failure) — never `None`, so the caller never needs a None
    check."""
    display_source = _SOURCE_DISPLAY_NAMES[provider_key]
    service_module = _SERVICE_MODULES[provider_key]
    started_at = datetime.now(timezone.utc).isoformat()

    with _provider_lock(f"{provider_key}-{worker_settings.db_backend}") as acquired:
        if not acquired:
            print(f"{provider_key.upper()}: skipped this tick — another scan for this provider is already in progress.")
            return _NO_CASES_GATHERED

        previous_status = scan_status_repo.get_scan_status(display_source)

        try:
            candidate_repository = backend_factory.get_candidate_repository(worker_settings, display_source)
            report = service_module.run_scan(worker_settings, candidate_repository=candidate_repository)
        except Exception as exc:  # noqa: BLE001 — one provider's failure must never stop the others
            _record_failure(scan_status_repo, display_source, previous_status, started_at, type(exc).__name__)
            print(f"{provider_key.upper()}: scan failed ({type(exc).__name__}) — skipped this tick.")
            return _NO_CASES_GATHERED

        completed_at = datetime.now(timezone.utc).isoformat()
        cursor_value = getattr(report, "end_de", None) or getattr(report, "end_date", None)
        skipped_unresolved = sum(1 for w in getattr(report, "warnings", ()) if "not resolved" in w)

        scan_status_repo.upsert_scan_status(ProviderScanStatus(
            provider=display_source,
            cursor_value=cursor_value,
            started_at=started_at,
            completed_at=completed_at,
            last_successful_at=completed_at,
            items_discovered=report.candidates_detected,
            candidates_created=report.candidates_processed,
            skipped_unresolved_count=skipped_unresolved,
            failure_code=None,
            updated_at=completed_at,
        ))
        print(
            f"{provider_key.upper()}: ok — candidates_detected={report.candidates_detected} "
            f"candidates_processed={report.candidates_processed} skipped_unresolved={skipped_unresolved}"
        )

        # EDINET Extraordinary Report shadow-observation workstream
        # (design/DECISIONS.md) — bounded, EDINET-only, flag-gated log
        # line. `worker_settings.edinet_material_event_lexicon_enabled`
        # is the sole gate (disabled by default): when False, this block
        # never executes and prints nothing at all — silence is itself
        # the flag-off proof. `getattr(..., ())` mirrors this same
        # function's own existing cross-provider-safe attribute access
        # (see `cursor_value`/`skipped_unresolved` above) — EDGAR/DART's
        # own ScanReport classes never carry this field, and a fake
        # `report` object in a test may not either.
        if provider_key == "edinet" and worker_settings.edinet_material_event_lexicon_enabled:
            shadow_matches = getattr(report, "shadow_material_event_matches", ())
            print(f"EDINET: edinet_material_event_shadow_matches={len(shadow_matches)}")
            for match in shadow_matches[:_SHADOW_MATERIAL_EVENT_LOG_CAP]:
                print(
                    f"EDINET:   shadow match — docID={match.doc_id} issuer={match.issuer_name} "
                    f"title={match.title} triplet={match.triplet}"
                )

        if provider_key == "edgar":
            try:
                research_case_summary, candidates, newly_created_cases = _run_edgar_research_case_step(
                    worker_settings, candidate_repository,
                )
            except Exception as exc:  # noqa: BLE001 — best-effort only; must never affect scan/candidate state or other providers
                print(f"{provider_key.upper()}: research-case step skipped ({type(exc).__name__}).")
                return _NO_CASES_GATHERED
            print(research_case_summary)
            return candidates, newly_created_cases

        if provider_key in ("dart", "edinet"):
            try:
                research_case_summary, candidates, newly_created_cases = _run_source_research_case_step(
                    provider_key, worker_settings, candidate_repository,
                )
            except Exception as exc:  # noqa: BLE001 — best-effort only; must never affect scan/candidate state or other providers
                print(f"{provider_key.upper()}: research-case step skipped ({type(exc).__name__}).")
                return _NO_CASES_GATHERED
            print(research_case_summary)
            return candidates, newly_created_cases

        return _NO_CASES_GATHERED


def run_one_tick(worker_settings: Settings, scan_status_repo) -> None:
    """Runs exactly one scan attempt per provider, in order, each fully
    isolated from the others' exceptions. Never loops, never sleeps,
    never checks the shutdown flag itself — the only function tests
    should call directly; main()'s own while-loop is not meant to be
    unit-tested as a whole.

    Phase 2 (design/DECISIONS.md): after every provider's own scan +
    research-case step has run, this function merges all three
    providers' `(candidates, newly_created_cases)` into one cross-
    market pool and calls the theme-matching, theme-candidate-
    detection, and theme-auto-publish steps exactly once per tick —
    never nested inside any single provider's own branch, and never
    holding any provider's own lock. Each of the three is isolated in
    its own try/except here: a failure in one can never affect
    ProviderScanStatus, candidate state, Research Case creation, or
    either of the other two steps. `_run_theme_matching_step`/
    `_run_theme_candidate_detection_step` are entirely unchanged from
    Phase A2/the prior autonomous-detection phase — only their call
    site moved; their own summary lines still read "EDGAR: ..." as a
    legacy label predating cross-market support, not a claim they are
    EDGAR-only."""
    all_candidates: dict[str, CandidateSignal] = {}
    all_newly_created_cases: list[ResearchCase] = []
    for provider_key in _PROVIDERS:
        candidates, newly_created_cases = _run_provider_tick(provider_key, worker_settings, scan_status_repo)
        all_candidates.update(candidates)
        all_newly_created_cases.extend(newly_created_cases)

    try:
        matching_summary = _run_theme_matching_step(worker_settings, all_candidates, tuple(all_newly_created_cases))
    except Exception as exc:  # noqa: BLE001 — best-effort only; must never affect scan/candidate/research-case state
        print(f"EDGAR: theme-matching step skipped ({type(exc).__name__}).")
    else:
        print(matching_summary)

    if worker_settings.theme_candidate_detection_enabled:
        try:
            detection_summary = _run_theme_candidate_detection_step(worker_settings, all_candidates, tuple(all_newly_created_cases))
        except Exception as exc:  # noqa: BLE001 — best-effort only; must never affect scan/candidate/research-case/matching state
            print(f"EDGAR: theme-candidate-detection step skipped ({type(exc).__name__}).")
        else:
            print(detection_summary)

    if worker_settings.theme_auto_publish_enabled:
        try:
            auto_publish_summary = _run_theme_auto_publish_step(worker_settings)
        except Exception as exc:  # noqa: BLE001 — best-effort only; must never affect any other step's own state
            print(f"EDGAR: theme-auto-publish step skipped ({type(exc).__name__}).")
        else:
            print(auto_publish_summary)


def _sleep_in_chunks(total_seconds: int, chunk_seconds: int = 5) -> None:
    """Sleeps in small increments so a shutdown signal is noticed
    promptly rather than only after the full interval elapses."""
    elapsed = 0
    while elapsed < total_seconds and not _shutdown_requested:
        time.sleep(min(chunk_seconds, total_seconds - elapsed))
        elapsed += chunk_seconds


def main(argv: list[str] | None = None) -> int:
    _install_signal_handlers()
    ambient = get_settings()

    if not ambient.radar_live_scan_enabled:
        print("EDGE_RADAR_LIVE_SCAN_ENABLED is not enabled — nothing to do. Exiting.")
        return 0

    try:
        worker_settings = _build_worker_settings(ambient)
    except WorkerConfigurationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    try:
        scan_status_repo = backend_factory.get_scan_status_repository(worker_settings)
    except Exception as exc:  # noqa: BLE001 — never leak a raw connection/config error
        print(f"ERROR: could not construct the scan-status repository ({type(exc).__name__}).", file=sys.stderr)
        return 1

    interval_seconds = max(_MIN_INTERVAL_SECONDS, ambient.radar_scan_interval_minutes * 60)
    print(
        f"Radar worker starting — backend={worker_settings.db_backend} "
        f"interval_minutes={ambient.radar_scan_interval_minutes}"
    )

    while not _shutdown_requested:
        run_one_tick(worker_settings, scan_status_repo)
        if _shutdown_requested:
            break
        _sleep_in_chunks(interval_seconds)

    print("Radar worker shutting down (signal received).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
