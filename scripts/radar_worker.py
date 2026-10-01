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

Provider scoping (design/DECISIONS.md): EDGE_RADAR_LIVE_SCAN_PROVIDERS
(Settings.radar_live_scan_providers), optional, a comma-separated subset
of edgar/dart/edinet — see _resolve_active_providers()'s own docstring
for the exact parsing/validation rules. Genuinely absent scans all three
in canonical order, unchanged from before this control existed; an
explicitly blank or invalid value is a fail-closed WorkerConfigurationError
at startup, before the scan loop ever begins — never silently "all
providers" and never a guessed subset. This is layered on top of, and
does not change, EDGE_RADAR_LIVE_SCAN_ENABLED above.

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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from src.config.settings import Settings, get_settings
from src.data_access import backend_factory
from src.data_access.dart import radar_service as dart_radar_service
from src.data_access.edgar import edgar_service
from src.data_access.edinet import edinet_service
from src.data_access.state_db.scan_status_repository import ProviderScanStatus
from src.data_access.state_db.coverage_status_repository import CoverageEvent, InstrumentLaneCoverage
from src.data_access.theme_store import build_theme_company_map_id, build_theme_id, build_theme_research_note_id
from src.logic.research_case_theme_matching import evaluate_theme_match
from src.logic.research_lead_orchestration import (
    ResearchLeadOrchestrationConfig,
    prepare_research_case_bundles,
    rejection_reason_histogram,
)
from src.logic.theme_auto_publish import evaluate_auto_publish_gates
from src.logic.theme_candidate_detection import detect_theme_candidates_with_diagnostics
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
_VALID_PROVIDERS = frozenset(_PROVIDERS)
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


def _resolve_active_providers(raw: str | None) -> tuple[str, ...]:
    """Provider-scoping safety control (design/DECISIONS.md) —
    EDGE_RADAR_LIVE_SCAN_PROVIDERS. `raw` is `Settings.
    radar_live_scan_providers` — None only when the environment variable
    is genuinely absent (see that field's own docstring for why it
    deliberately does not fold an explicit blank into None the way every
    other optional string Settings field does).

    - Absent (raw is None): today's exact behavior, unchanged —
      every provider, in canonical order.
    - Present but blank/whitespace-only: a real, fail-closed
      configuration error, never treated as "all providers" — an
      operator who explicitly set this to nothing almost certainly
      meant to restrict scanning.
    - Present with real content: split on comma, trim, lowercase; a
      purely-whitespace segment between commas is dropped as harmless
      formatting slack, but a result that resolves to zero names after
      that (e.g. "," or " , ") is the same fail-closed error as an
      explicit blank, not silently "all providers." Every remaining
      name must be exactly one of edgar/dart/edinet, and no name may
      repeat. Never fuzzy-matched, never partially accepted.
    - Valid, non-empty, deduplicated input: returned in
      _PROVIDERS' own canonical order (edgar, dart, edinet) —
      the caller-provided order is never trusted.

    Every error message is a WorkerConfigurationError naming only the
    offending provider token(s) — never a secret, DSN, or any other
    environment variable's value."""
    if raw is None:
        return _PROVIDERS

    if not raw.strip():
        raise WorkerConfigurationError(
            "EDGE_RADAR_LIVE_SCAN_PROVIDERS is set but blank. Unset the variable "
            "entirely to scan all providers, or list at least one of: edgar, dart, edinet."
        )

    tokens = [token.strip().lower() for token in raw.split(",")]
    tokens = [token for token in tokens if token]

    if not tokens:
        raise WorkerConfigurationError(
            "EDGE_RADAR_LIVE_SCAN_PROVIDERS is set but resolves to an empty provider list. "
            "Unset the variable entirely to scan all providers, or list at least one of: edgar, dart, edinet."
        )

    unknown = sorted({token for token in tokens if token not in _VALID_PROVIDERS})
    if unknown:
        raise WorkerConfigurationError(
            "EDGE_RADAR_LIVE_SCAN_PROVIDERS contains unrecognized provider name(s): "
            f"{', '.join(unknown)!r}. Allowed values: edgar, dart, edinet."
        )

    seen: set[str] = set()
    duplicates: list[str] = []
    for token in tokens:
        if token in seen and token not in duplicates:
            duplicates.append(token)
        seen.add(token)
    if duplicates:
        raise WorkerConfigurationError(
            "EDGE_RADAR_LIVE_SCAN_PROVIDERS contains duplicate provider name(s): "
            f"{', '.join(sorted(duplicates))!r}. Each provider may be listed at most once."
        )

    requested = set(tokens)
    return tuple(provider for provider in _PROVIDERS if provider in requested)


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
        f"write_rejected={write_rejected} "
        f"rejection_reasons={rejection_reason_histogram(result)}"
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
        f"write_rejected={write_rejected} "
        f"rejection_reasons={rejection_reason_histogram(result)}"
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
    # EDGAR's own category names (8-K items 1.01 / 2.03 and the
    # registration-and-prospectus forms / 8-K item 8.01). This tuple was
    # written in EDGAR's vocabulary, so DART's and EDINET's category
    # vocabularies intersected it at exactly zero: DART names its
    # capital-raise category `financing`, not `financing_or_debt`, and
    # EDINET's only real code-mapped categories are
    # annual_securities_report / share_buyback_status /
    # extraordinary_report. Nothing those two markets emit could clear
    # this gate at all.
    "material_agreement", "financing_or_debt", "other_material_event",
    # DART's statutory new-facility/facility-investment disclosure
    # (신규시설투자 / 시설투자, dart_rules.KOREAN_KEYWORD_LEXICON) — the
    # single most constraint-specific category in any of the three
    # lexicons, since facility investment IS capacity formation, and the
    # one the DART module itself records as observed repeatedly in a real
    # pull rather than carried as a "standard, not observed" entry.
    #
    # Deliberately one category, not a vocabulary alignment: admitting
    # the category is necessary, never sufficient. The keyword gate still
    # runs after it, `min_distinct_companies` still requires a cluster to
    # span two issuers, and autonomous publication stays off — so this
    # widens what can be EXAMINED for relevance, and promises no Theme
    # candidate. No EDGAR or EDINET rule can emit this slug, so the
    # change is market-scoped by construction and cannot alter either of
    # those markets' detection behavior.
    #
    # Known limitation this does NOT address: the keyword gate reads
    # `excerpt_original` + `report_nm` — original-language text — against
    # an English keyword list, so an admitted DART pair is still likely
    # to be rejected at the keyword gate instead. That is a deliberate
    # outcome to MEASURE via the category_rejected/keyword_rejected
    # split, not a reason to broaden keywords here.
    "capex_or_facility_investment",
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


_DETECTION_METRICS_UNAVAILABLE = (
    "pairs_examined=unavailable pairs_malformed=unavailable category_rejected=unavailable "
    "keyword_rejected=unavailable constraint_relevant=unavailable clusters_formed=unavailable "
    "clusters_scope_suppressed=unavailable clusters_below_threshold=unavailable"
)


def _format_detection_metrics(diagnostics: object) -> str:
    """The theme-candidate funnel counts, rendered for the worker
    summary. Integers only -- `DetectionDiagnostics` carries no issuer,
    slug, excerpt, identifier or source text, so nothing unsafe can
    reach a log line through here.

    Observability must never change a tick's outcome, so a malformed or
    missing diagnostics object degrades to a fixed `unavailable` marker
    for every field rather than raising: detection has already
    completed and its candidates are already persisted by the time this
    runs. Never raises."""
    try:
        parts = []
        # Fixed order, and the only names this function will ever
        # emit: the field list is a literal here, so a diagnostics
        # object cannot introduce a name of its own.
        for name in ("pairs_examined", "pairs_malformed", "category_rejected", "keyword_rejected",
                     "constraint_relevant", "clusters_formed", "clusters_scope_suppressed",
                     "clusters_below_threshold"):
            value = getattr(diagnostics, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return _DETECTION_METRICS_UNAVAILABLE
            parts.append(f"{name}={int(value)}")
        return " ".join(parts)
    except Exception:  # noqa: BLE001 — a logging aggregate must never affect a provider tick
        return _DETECTION_METRICS_UNAVAILABLE


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
    detected, detection_diagnostics = detect_theme_candidates_with_diagnostics(
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
        f"creation_errors={creation_errors} "
        f"pairs_gathered={len(pairs)} {_format_detection_metrics(detection_diagnostics)}"
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

# --- Measured coverage per (issuer, lane) — Coverage Control Plane, M1 ---
#
# Before this, a scan's own `no_data_companies` set was reduced to a
# count in each pipeline and discarded, so which instruments a lane
# actually failed to cover was unrecoverable once the tick ended, and
# the Coverage page could only assert coverage from configuration.
#
# The ordered rules below are the whole point of this block. A no-data
# issuer is in BOTH `no_data_companies` and `observed_companies` by
# construction (a trustworthy "nothing new" IS a successful
# observation), so without an explicit order it could be persisted as
# Covered and lose the distinction. Identity is checked FIRST because an
# unresolved issuer is never fetched at all: on EDGAR/DART it lands in
# neither set and would otherwise read as an untrusted outcome, and on
# EDINET — whose no-data is derived by complement — it would otherwise
# read as healthy no-data, which is exactly the misreport this milestone
# exists to prevent.
#
# Duck-typed on the repository, exactly as ProviderScanStatus already
# is: the SQLite and Postgres records have identical field shapes and
# neither package imports the other.

_COVERED = "Covered"
_NO_DATA = "NoData"
_FAILING = "Failing"
_UNMAPPED = "Unmapped"
_NOT_EXPECTED = "NotExpected"

# Covered and NoData are ONE health class for audit purposes. They stay
# separate current states with their own timestamps and counters, but a
# move between them is not a change worth a history row — recording
# every oscillation would turn a bounded table into a per-tick log.
_HEALTHY_STATES = frozenset({_COVERED, _NO_DATA})

_LANE_HEALTHY = "lane_healthy"
_LANE_FAILING = "lane_failing"

_REASON_IDENTITY = "identity_unresolved"
_REASON_UNTRUSTED = "untrusted_outcome"
_REASON_PROVIDER_ERROR = "provider_error"
_REASON_LANE_UNTRUSTED = "lane_untrusted"
_REASON_INTEGRITY = "unresolvable_scan_name"

# Its own scope: an integrity condition is a fact about the scan, not
# about one instrument (there is no instrument to name) and not a lane
# incident (the lane itself is healthy). Overloading either would make
# `scope` useless for filtering.
_SCOPE_INTEGRITY = "integrity"
_INTEGRITY_PRESENT = "integrity_present"
_INTEGRITY_CLEAR = "integrity_clear"

_COMPANY_LISTERS = {
    "edgar": lambda settings: edgar_service.get_edgar_companies(settings.cache_dir, settings),
    "dart": lambda settings: dart_radar_service.get_radar_companies(settings.cache_dir, settings),
    "edinet": lambda settings: edinet_service.get_edinet_companies(settings.cache_dir),
}


@contextmanager
def _coverage_transaction(coverage_repo):
    """One top-level transaction per lane per tick, so state rows and
    history rows can never disagree. Never nested — both connection
    packages document that nesting is unsupported. A repository without
    a `conn` (the in-memory fake the tests use) simply yields."""
    conn = getattr(coverage_repo, "conn", None)
    if conn is None:
        yield
        return
    if hasattr(conn, "rollback") and hasattr(conn, "commit"):
        try:
            yield
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return
    yield


def _is_healthy(state: str | None) -> bool:
    return state in _HEALTHY_STATES


@dataclass(frozen=True)
class _LaneInstrument:
    """One registry issuer's standing on one lane for this tick."""

    company_name: str
    active: bool           # still in the lane service's active company list
    identity_resolved: bool


def _lane_instruments(lane: str, worker_settings: Settings) -> dict[str, _LaneInstrument]:
    """{issuer_id: _LaneInstrument} for EVERY registry issuer on this
    lane — active and inactive alike.

    The registry is the authoritative universe. Inactive issuers are
    returned too, flagged, rather than filtered out: a company that goes
    inactive silently stops appearing in scan output
    (`get_tracked_companies_for_source` is active-only), so if it were
    dropped here its last row would simply freeze on whatever state it
    held and then age into Stale — reported forever as a coverage gap
    for an instrument nobody expects to cover. Returning it lets the
    caller record NotExpected instead.

    Also the only source available on a provider-wide failure, where
    there is no ScanResult at all yet every expected instrument still
    has to be marked."""
    from src.config.issuer_registry import SEED_ISSUERS, source_name_for_seed_issuer

    display_source = _SOURCE_DISPLAY_NAMES[lane]
    resolved_by_name = {c.name: bool(c.corp_code) for c in _COMPANY_LISTERS[lane](worker_settings)}
    instruments: dict[str, _LaneInstrument] = {}
    for issuer in SEED_ISSUERS:
        if source_name_for_seed_issuer(issuer) != display_source:
            continue
        name = issuer.legal_name
        active = name in resolved_by_name
        instruments[issuer.issuer_id] = _LaneInstrument(
            company_name=name, active=active,
            identity_resolved=resolved_by_name.get(name, False),
        )
    return instruments


def _expected_instruments(lane: str, worker_settings: Settings) -> dict[str, tuple[str, bool]]:
    """The ACTIVE subset, in the shape the outcome rules consume."""
    return {
        issuer_id: (instrument.company_name, instrument.identity_resolved)
        for issuer_id, instrument in _lane_instruments(lane, worker_settings).items()
        if instrument.active
    }


def _apply_not_expected(coverage_repo, lane: str, instruments: dict, existing: dict, now: str) -> None:
    """Records every inactive registry issuer as NotExpected.

    Timestamps and counters are carried forward untouched — an issuer
    leaving the scanned universe is not an observation, so neither
    `last_attempt_at` nor `last_success_at` may move. An event is
    written only on the crossing itself, so a permanently inactive
    issuer costs one row once, not one per tick."""
    for issuer_id, instrument in instruments.items():
        if instrument.active:
            continue
        previous = existing.get((issuer_id, lane))
        if previous is not None and previous.coverage_state == _NOT_EXPECTED:
            coverage_repo.upsert_coverage_status(
                _next_status(previous, issuer_id, lane, _NOT_EXPECTED, now)
            )
            continue  # already not expected — event-silent
        coverage_repo.upsert_coverage_status(
            _next_status(previous, issuer_id, lane, _NOT_EXPECTED, now)
        )
        if previous is not None:
            coverage_repo.record_coverage_event(CoverageEvent(
                scope="instrument", issuer_id=issuer_id, lane=lane,
                from_state=previous.coverage_state, to_state=_NOT_EXPECTED,
                failure_class=None, blocking_reason="", detail="", at=now,
            ))


def _blank_status(issuer_id: str, lane: str, now: str) -> InstrumentLaneCoverage:
    return InstrumentLaneCoverage(
        issuer_id=issuer_id, lane=lane, expected=True, coverage_state=_FAILING,
        last_attempt_at=None, last_success_at=None, last_no_data_at=None,
        last_item_at=None, last_material_item_at=None,
        consecutive_empty_runs=0, consecutive_failures=0,
        failure_class=None, blocking_reason="", updated_at=now,
    )


def _next_status(
    previous: InstrumentLaneCoverage | None, issuer_id: str, lane: str, state: str,
    now: str, *, blocking_reason: str = "", failure_class: str | None = None,
    has_new_item: bool = False, has_new_material_item: bool = False,
) -> InstrumentLaneCoverage:
    """Applies one outcome to one instrument's row.

    The single invariant this function exists to enforce: only Covered
    and NoData may advance `last_success_at`. Every other state carries
    the previous value forward untouched, so a failing scan can never be
    read later as a recent successful observation."""
    base = previous or _blank_status(issuer_id, lane, now)
    advances_success = state in _HEALTHY_STATES
    return InstrumentLaneCoverage(
        issuer_id=issuer_id,
        lane=lane,
        expected=state != _NOT_EXPECTED,
        coverage_state=state,
        last_attempt_at=now if state != _NOT_EXPECTED else base.last_attempt_at,
        last_success_at=now if advances_success else base.last_success_at,
        last_no_data_at=now if state == _NO_DATA else base.last_no_data_at,
        last_item_at=now if has_new_item else base.last_item_at,
        last_material_item_at=now if has_new_material_item else base.last_material_item_at,
        consecutive_empty_runs=(base.consecutive_empty_runs + 1) if state == _NO_DATA else (
            0 if state == _COVERED else base.consecutive_empty_runs
        ),
        consecutive_failures=(base.consecutive_failures + 1) if state == _FAILING else (
            0 if advances_success else base.consecutive_failures
        ),
        failure_class=failure_class if state == _FAILING else None,
        blocking_reason=blocking_reason,
        updated_at=now,
    )


def _classify(
    issuer_id: str, name: str, identity_resolved: bool, report, previous, lane: str, now: str,
) -> InstrumentLaneCoverage:
    """The ordered rules. First match wins — see this section's header
    for why the order is load-bearing rather than cosmetic."""
    if not identity_resolved:
        return _next_status(previous, issuer_id, lane, _UNMAPPED, now, blocking_reason=_REASON_IDENTITY)
    if name in set(getattr(report, "no_data_companies", ())):
        return _next_status(previous, issuer_id, lane, _NO_DATA, now)
    if name in set(getattr(report, "observed_companies", ())):
        return _next_status(
            previous, issuer_id, lane, _COVERED, now,
            has_new_item=name in set(getattr(report, "companies_with_new_items", ())),
            has_new_material_item=name in set(getattr(report, "companies_with_new_material_items", ())),
        )
    return _next_status(previous, issuer_id, lane, _FAILING, now, blocking_reason=_REASON_UNTRUSTED)


def _lane_was_failing(existing: dict, expected: dict, lane: str) -> bool:
    """A lane counts as already in a lane-wide incident when every
    expected instrument is Failing for a lane-scoped reason. Used only
    to decide whether a lane event is a new transition."""
    rows = [existing.get((iid, lane)) for iid in expected]
    if not rows or any(r is None for r in rows):
        return False
    return all(
        r.coverage_state == _FAILING and r.blocking_reason in (_REASON_PROVIDER_ERROR, _REASON_LANE_UNTRUSTED)
        for r in rows
    )


def _apply_lane_failure(
    coverage_repo, lane: str, worker_settings: Settings, blocking_reason: str,
    failure_class: str | None, now: str,
) -> None:
    """Every expected instrument on the lane becomes Failing, and no
    instrument's `last_success_at` advances. Exactly ONE lane-scoped
    event is written — fanning this out per instrument would record one
    copy per instrument of a single fact about the lane, and is what
    would make the history table unbounded under a flapping source."""
    instruments = _lane_instruments(lane, worker_settings)
    expected = {i: v for i, v in instruments.items() if v.active}
    existing = coverage_repo.get_all_coverage_statuses()
    was_failing = _lane_was_failing(existing, expected, lane)
    for issuer_id in expected:
        coverage_repo.upsert_coverage_status(_next_status(
            existing.get((issuer_id, lane)), issuer_id, lane, _FAILING, now,
            blocking_reason=blocking_reason, failure_class=failure_class,
        ))
    _apply_not_expected(coverage_repo, lane, instruments, existing, now)
    if not was_failing:
        coverage_repo.record_coverage_event(CoverageEvent(
            scope="lane", issuer_id=None, lane=lane,
            from_state=_LANE_HEALTHY, to_state=_LANE_FAILING,
            failure_class=failure_class, blocking_reason=blocking_reason,
            detail="", at=now,
        ))


def _unknown_name_digest(names) -> str:
    """A deterministic fingerprint of the unknown-name SET, used only to
    detect change between ticks.

    A digest rather than the names themselves: an unrecognized name came
    from provider output, so storing it verbatim would put untrusted
    source text in a history row that the Coverage page and any future
    export read. The digest is a SHA-256 prefix over the sorted names —
    stable across ticks, order-independent, and not reversible to a
    name. Only the digest and a count ever leave this function."""
    import hashlib

    ordered = sorted(set(names))
    if not ordered:
        return ""
    fingerprint = hashlib.sha256("\n".join(ordered).encode("utf-8")).hexdigest()[:16]
    return f"count={len(ordered)} digest={fingerprint}"


def _last_integrity_detail(coverage_repo, lane: str) -> str:
    """The most recent integrity condition recorded for this lane, or ""
    when the condition is not currently present.

    Read back from the events table rather than stored in a new column:
    the condition is already fully described by the last integrity event,
    so a schema change would add a second place for the same fact to live
    and a second place for it to drift."""
    try:
        events = coverage_repo.get_coverage_events(lane)
    except Exception:  # noqa: BLE001 — change detection must never fail a tick
        return ""
    for event in reversed(list(events)):
        if event.scope == _SCOPE_INTEGRITY:
            return "" if event.to_state == _INTEGRITY_CLEAR else (event.detail or "")
    return ""


def _apply_integrity_condition(coverage_repo, lane: str, unknown_names, now: str) -> None:
    """Records unrecognized scan names as their own condition.

    Three properties this needs and the previous version did not have:
    it is its own `scope`, so it never masquerades as an instrument
    event with no instrument; it writes only when the condition first
    appears, changes, or clears, so a persistent unknown name costs one
    row rather than one per tick; and an exact replay of the same tick
    writes nothing, because the digest is unchanged."""
    current = _unknown_name_digest(unknown_names)
    previous = _last_integrity_detail(coverage_repo, lane)
    if current == previous:
        return
    coverage_repo.record_coverage_event(CoverageEvent(
        scope=_SCOPE_INTEGRITY, issuer_id=None, lane=lane,
        from_state=_INTEGRITY_PRESENT if previous else _INTEGRITY_CLEAR,
        to_state=_INTEGRITY_PRESENT if current else _INTEGRITY_CLEAR,
        failure_class=None, blocking_reason=_REASON_INTEGRITY,
        detail=current, at=now,
    ))


def _apply_coverage_outcomes(coverage_repo, lane: str, worker_settings: Settings, report, now: str) -> None:
    """One trustworthy scan's outcomes, applied to every registry
    instrument on the lane.

    EDINET's empty `observed_companies` is the lane-untrusted signal:
    its no-data set is derived by complement over day rows, so an
    untrustworthy day would otherwise name every company as healthy
    no-data."""
    if lane == "edinet" and not getattr(report, "observed_companies", ()):
        _apply_lane_failure(coverage_repo, lane, worker_settings, _REASON_LANE_UNTRUSTED, None, now)
        return

    instruments = _lane_instruments(lane, worker_settings)
    expected = {i: (v.company_name, v.identity_resolved) for i, v in instruments.items() if v.active}
    existing = coverage_repo.get_all_coverage_statuses()
    lane_recovering = _lane_was_failing(existing, expected, lane)

    for issuer_id, (name, identity_resolved) in expected.items():
        previous = existing.get((issuer_id, lane))
        status = _classify(issuer_id, name, identity_resolved, report, previous, lane, now)
        coverage_repo.upsert_coverage_status(status)

        previous_state = previous.coverage_state if previous else None
        previous_reason = previous.blocking_reason if previous else ""
        if previous_state is None:
            continue  # first sighting is the row itself, not a transition

        # During a lane recovery, only the recovery OF the lane-wide
        # failure is attributable to the lane and covered by the single
        # lane event below. An issuer that independently fails on the
        # same tick is its own fact and still gets its own event —
        # otherwise that failure would never appear in history at all,
        # because the next tick sees no change.
        recovered_from_lane_incident = (
            lane_recovering
            and previous_reason in (_REASON_PROVIDER_ERROR, _REASON_LANE_UNTRUSTED)
            and _is_healthy(status.coverage_state)
        )
        if recovered_from_lane_incident:
            continue

        changed_class = _is_healthy(status.coverage_state) != _is_healthy(previous_state)
        changed_reason = previous_reason != status.blocking_reason
        if changed_class or changed_reason:
            coverage_repo.record_coverage_event(CoverageEvent(
                scope="instrument", issuer_id=issuer_id, lane=lane,
                from_state=previous_state, to_state=status.coverage_state,
                failure_class=status.failure_class, blocking_reason=status.blocking_reason,
                detail="", at=now,
            ))

    _apply_not_expected(coverage_repo, lane, instruments, existing, now)

    if lane_recovering:
        coverage_repo.record_coverage_event(CoverageEvent(
            scope="lane", issuer_id=None, lane=lane,
            from_state=_LANE_FAILING, to_state=_LANE_HEALTHY,
            failure_class=None, blocking_reason="", detail="", at=now,
        ))

    # An unrecognized scan name is an integrity problem, never a silent
    # drop and never healthy no-data: the scan reported a company this
    # worker cannot tie to any registry instrument.
    known_names = {value.company_name for value in instruments.values()}
    unknown = [n for n in (getattr(report, "observed_companies", ()) or ()) if n not in known_names]
    _apply_integrity_condition(coverage_repo, lane, unknown, now)


def _run_provider_tick(
    provider_key: str, worker_settings: Settings, scan_status_repo, coverage_repo=None,
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
            # Every expected instrument on this lane becomes Failing, with
            # one lane-scoped event. Guarded separately so a coverage-write
            # problem can never change how a scan failure is reported.
            if coverage_repo is not None:
                try:
                    with _coverage_transaction(coverage_repo):
                        _apply_lane_failure(
                            coverage_repo, provider_key, worker_settings,
                            _REASON_PROVIDER_ERROR, type(exc).__name__,
                            datetime.now(timezone.utc).isoformat(),
                        )
                except Exception:  # noqa: BLE001 — coverage is observability, never the scan's outcome
                    print(f"{provider_key.upper()}: coverage write failed — scan outcome unaffected.")
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
            # Both of these used to hold a value its column name denied.
            #
            # items_discovered held candidates_detected -- a count taken
            # AFTER matching, dedupe and rules, under a name that reads
            # as raw discovery. It made a quiet source and a source whose
            # rows we discarded look identical, which is exactly the
            # question a throughput investigation needs answered.
            # filings_discovered is the earliest truthful count all three
            # providers share: in-window filings for tracked companies,
            # before dedupe, rules and candidate creation.
            #
            # candidates_created held candidates_processed, which counts
            # retrieval/extraction/translation work done this tick, not
            # candidates created. It sits at 0 on a healthy tick with
            # nothing left to process, and has already been misread as
            # "no writes happened". candidates_detected is the number of
            # candidate signals newly created this run -- see the field's
            # own comment in dart/radar_pipeline.py.
            items_discovered=report.filings_discovered,
            candidates_created=report.candidates_detected,
            skipped_unresolved_count=skipped_unresolved,
            failure_code=None,
            updated_at=completed_at,
        ))
        # Measured coverage, written immediately after the provider-level
        # scan status it complements. In its own guard: a successful scan
        # must never be reported as failed because an observability write
        # did not land.
        if coverage_repo is not None:
            try:
                with _coverage_transaction(coverage_repo):
                    _apply_coverage_outcomes(
                        coverage_repo, provider_key, worker_settings, report, completed_at,
                    )
            except Exception:  # noqa: BLE001 — coverage is observability, never the scan's outcome
                print(f"{provider_key.upper()}: coverage write failed — scan outcome unaffected.")
        # One line per provider carrying each funnel stage separately, so
        # a drop can be located rather than guessed at. already_seen
        # comes from the on-disk dedupe cache, which is ephemeral on a
        # container with no disk attached -- a cold container reports 0
        # and re-detects, so read it alongside container age, not alone.
        stages = (
            f"filings_discovered={report.filings_discovered} "
            f"new_filing_events={report.new_filing_events} "
            f"already_seen={report.already_seen_count} "
        )
        # EDINET alone queries a whole day's document list and matches
        # tracked companies afterwards, so it alone has counts upstream
        # of the matcher. getattr keeps EDGAR and DART honest: they have
        # no equivalent stage, so they print no equivalent number.
        normalized_rows = getattr(report, "normalized_rows_fetched", None)
        if normalized_rows is not None:
            stages = f"normalized_rows_fetched={normalized_rows} " + stages
        deferred_status = getattr(report, "deferred_status_count", None)
        if deferred_status is not None:
            stages += f"deferred_status={deferred_status} "
        print(
            f"{provider_key.upper()}: ok — {stages}"
            f"candidates_detected={report.candidates_detected} "
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


def run_one_tick(
    worker_settings: Settings, scan_status_repo, providers: tuple[str, ...] = _PROVIDERS,
    coverage_repo=None,
) -> None:
    """Runs exactly one scan attempt per provider, in order, each fully
    isolated from the others' exceptions. Never loops, never sleeps,
    never checks the shutdown flag itself — the only function tests
    should call directly; main()'s own while-loop is not meant to be
    unit-tested as a whole.

    `providers` (design/DECISIONS.md, provider-scoping safety control)
    is additive and optional, defaulting to `_PROVIDERS` — every
    existing caller that omits it (including every test written before
    this control existed) scans exactly the same three providers in the
    same order as before. `main()` is the only real caller that ever
    passes a narrowed value, and only after `_resolve_active_providers()`
    has already validated it at startup — this function itself performs
    no validation of `providers` and trusts the caller completely.

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
    for provider_key in providers:
        candidates, newly_created_cases = _run_provider_tick(
            provider_key, worker_settings, scan_status_repo, coverage_repo,
        )
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
        active_providers = _resolve_active_providers(ambient.radar_live_scan_providers)
    except WorkerConfigurationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    try:
        scan_status_repo = backend_factory.get_scan_status_repository(worker_settings)
        # Optional by construction: a backend that cannot provide measured
        # coverage must not stop the worker from scanning.
        try:
            coverage_repo = backend_factory.get_coverage_status_repository(worker_settings)
        except Exception:  # noqa: BLE001 — coverage is observability, never a scan precondition
            coverage_repo = None
            print("coverage: measured-coverage persistence unavailable — scans continue.")
    except Exception as exc:  # noqa: BLE001 — never leak a raw connection/config error
        print(f"ERROR: could not construct the scan-status repository ({type(exc).__name__}).", file=sys.stderr)
        return 1

    interval_seconds = max(_MIN_INTERVAL_SECONDS, ambient.radar_scan_interval_minutes * 60)
    print(
        f"Radar worker starting — backend={worker_settings.db_backend} "
        f"interval_minutes={ambient.radar_scan_interval_minutes} "
        f"providers={','.join(active_providers)}"
    )

    while not _shutdown_requested:
        run_one_tick(worker_settings, scan_status_repo, providers=active_providers, coverage_repo=coverage_repo)
        if _shutdown_requested:
            break
        _sleep_in_chunks(interval_seconds)

    print("Radar worker shutting down (signal received).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
