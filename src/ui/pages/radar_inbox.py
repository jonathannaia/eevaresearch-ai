"""Radar Inbox — automated primary-filing discovery. Korea DART (Samsung
Electronics + SK Hynix), SEC EDGAR (NVIDIA, Micron Technology, Coherent
Corp, Rockwell Automation, Rocket Lab), and EDINET (Japan) — SoftBank
Group, Kioxia Holdings, Furukawa Electric, FANUC, ispace, all tracked as
of Gate 7. The only page in this app backed by real, live data.

Deliberately visually and lexically distinct from Signals (the curated,
published Signal Board): a Radar item is a filing-driven research lead,
never a completed market read. See src/ui/components/radar_status.py
for the separate status vocabulary.

Reader-facing data-integrity pass (design/DECISIONS.md): this page is
now strictly read-only. The manual "Scan ... now" buttons, the
Publish/Monitor/Exclude review decision, and the "Prepare/Retry analyst
view" trigger are all removed — none of that is appropriate on a public
page, and scanning/processing/review-decision write paths are worker-
internal concerns now (see src.ui.components.radar_card's own module
docstring for the equivalent removals at the per-card level, and design/
DECISIONS.md's Section 5 report for what this means for autonomous
processing going forward: this deployment currently has no other
scheduled trigger for a scan, since scripts/radar_worker.py must be
deployed as its own separate process — see design/
RADAR_WORKER_DEPLOYMENT.md — and that gap is a deployment decision, not
something this UI change alone resolves). Each source's own on-disk
candidate store (dart_candidates.json / edgar_candidates.json) and
`filing.source_name` distinction are otherwise unchanged.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime

import streamlit as st

from src.config.settings import Settings, get_settings
from src.data_access import backend_factory
from src.data_access.dart import candidate_store, radar_service
from src.data_access.dart import scan_service as dart_scan_service
from src.data_access.edgar import edgar_pipeline, edgar_service
from src.data_access.edgar import scan_service as edgar_scan_service
from src.data_access.edinet import edinet_pipeline, edinet_service
from src.data_access.edinet import scan_service as edinet_scan_service
from src.logic.formatting import today_local
from src.logic.radar_freshness import compute_radar_freshness
from src.ui.components.empty_state import empty_state
from src.ui.components.radar_card import candidate_row
from src.ui.components.radar_status import RadarItem

_DART_SCOPE_LINE = "OpenDART / DART · Samsung Electronics + SK Hynix · Memory + AI Buildout"
_EDGAR_SCOPE_LINE = "SEC EDGAR · NVIDIA, Micron, Coherent, Rockwell Automation, Rocket Lab · AI Buildout, Memory, Photonics, Humanoids, Space"

# Pagination (usability/navigation-stability follow-up — see
# design/DECISIONS.md): the raw "every FilingEvent, every source, no cap"
# list could grow to hundreds of cards, each with several nested
# containers/expanders/buttons — rendering all of them at once was found
# live to make the app's own sidebar navigation unreliable. Unify-Radar-
# into-Latest-Filings pass: the earlier "Latest" (candidate-linked only)
# vs. "Captured filings" (every captured filing) view selector is removed
# — there is now exactly one public feed, always the full captured list,
# always paginated. No page ever renders more than PAGE_SIZE cards' worth
# of widgets, regardless of filters.
PAGE_SIZE = 20

_FILTER_KEYS = (
    "radar-filter-search",
    "radar-filter-source",
    "radar-filter-theme",
    "radar-filter-dates",
)

# Radar layout correction (design/DECISIONS.md): the Source multiselect
# must always offer every canonical source as an option, even when no
# filing from that source has been loaded yet in the current session —
# previously it was derived purely from `{i.filing.source_name for i in
# view_items}`, so a source with zero currently-loaded filings (most
# often EDINET) silently never appeared as a selectable option at all.
_ALL_RADAR_SOURCES = ("OpenDART / DART", "SEC EDGAR", "EDINET")


def _clear_filters() -> None:
    for key in _FILTER_KEYS:
        st.session_state.pop(key, None)
    st.session_state["radar-page"] = 1


def _edinet_scope_line(cache_dir, settings: Settings | None = None) -> str:
    """Truthful configured-but-not-scanned wording (Gate 7.1) — replaces
    the earlier, now-stale "no tracked companies yet" line from before
    the five-company registry addition (Gate 7). Deliberately does not
    claim EDINET is calibrated, actively monitored, current, autonomous,
    or producing live signals — it isn't; zero live scans have run.

    `settings` is additive and optional (Durable-State Phase 2B; extended
    to Postgres in Phase 4M-1) — see backend_factory.py's module
    docstring. Omitted, behavior is unchanged. `get_edinet_companies` is
    untouched either way — EDINET's five tracked companies have their
    identifiers hardcoded directly in tracked_companies.py, never
    resolved from a runtime cache (see that module's own docstring), so
    there is no identifier-cache read here to wire in the first place.

    Durable-State Phase 4M-1: `"postgres"` is now recognized alongside
    `"sqlite"` — a repository-construction or read failure is caught and
    degrades to a count of zero for this line only, never a raw
    exception or crash; the same fail-closed discipline as
    `_build_items`."""
    company_count = len(edinet_service.get_edinet_companies(cache_dir))
    backend = (settings.db_backend or "json").strip().lower() if settings is not None else "json"
    use_repository_backend = backend in ("sqlite", "postgres")
    if use_repository_backend:
        try:
            filing_count = len(backend_factory.get_filing_event_repository(settings, "EDINET").load_filing_events())
            candidate_count = len(backend_factory.get_candidate_repository(settings, "EDINET").load_candidates())
        except Exception:  # noqa: BLE001 — fail closed; never leak a raw connection/config error into the UI
            filing_count = 0
            candidate_count = 0
    else:
        filing_count = len(edinet_scan_service.load_filing_events(cache_dir))
        candidate_count = len(candidate_store.load_candidates(cache_dir, edinet_pipeline.CANDIDATE_STORE_FILENAME))
    return (
        f"EDINET (Japan) · {company_count} tracked companies configured; no live scan completed yet · "
        f"FilingEvents: {filing_count} · CandidateSignals: {candidate_count} · last scan: none"
    )


def _render_missing_configuration(
    dart_readiness: radar_service.RadarReadiness,
    edgar_readiness: edgar_service.EdgarReadiness,
    edinet_readiness: edinet_service.EdinetReadiness,
) -> None:
    lines: list[str] = []
    if not dart_readiness.dart_key_configured:
        lines.append("EDGE_DART_API_KEY is not configured.")
    if not dart_readiness.translation_key_configured:
        lines.append("EDGE_TRANSLATION_API_KEY is not configured.")
    if dart_readiness.unresolved_companies:
        lines.append(
            "DART corp code not resolved for: " + ", ".join(dart_readiness.unresolved_companies)
            + " — run the corp_code_resolver once against a configured DART key."
        )
    if not edgar_readiness.user_agent_configured:
        lines.append("EDGE_EDGAR_USER_AGENT is not configured.")
    if edgar_readiness.unresolved_companies:
        lines.append(
            "SEC CIK not resolved for: " + ", ".join(edgar_readiness.unresolved_companies)
            + " — run the cik_resolver once against a configured User-Agent."
        )
    if not edinet_readiness.subscription_key_configured:
        lines.append("EDGE_EDINET_SUBSCRIPTION_KEY is not configured.")
    if not edinet_readiness.translation_key_configured:
        lines.append("EDGE_TRANSLATION_API_KEY is not configured.")
    # Translation reliability workstream: DART and EDINET share one
    # EDGE_TRANSLATION_API_KEY, so a missing key can append the identical
    # sentence twice above (once per source's own readiness check) —
    # de-duplicated here, order-preserving, rather than teaching either
    # readiness check about the other.
    lines = list(dict.fromkeys(lines))
    # Phase B (UI audit): the top-level state is now one calm sentence —
    # the full env-var/resolver/unresolved-company dump moves into a
    # collapsed expander below, unchanged in content. Readiness logic and
    # the `lines` construction above are untouched.
    empty_state(
        "Latest Filings is not configured",
        "None of the live filing sources (DART, EDGAR, EDINET) are configured in this environment. "
        "See configuration details below for exactly what's missing.",
    )
    with st.expander("Configuration details", expanded=False):
        st.markdown(
            f'<div class="er-muted">{" ".join(lines)} Add the missing configuration to your local .env and restart the app.</div>',
            unsafe_allow_html=True,
        )


def _parse_rcept_date(raw: str) -> date | None:
    """FilingEvent.rcept_dt is each source's own raw date string — DART's
    unconverted "YYYYMMDD", EDGAR's dashed ISO "YYYY-MM-DD" — never
    assume a single format. Stripping dashes before parsing as %Y%m%d
    handles both without needing to know which source a given item came
    from (confirmed against real data for both — see scan_service.py in
    each source's module)."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw.replace("-", ""), "%Y%m%d").date()
    except ValueError:
        return None


def _official_filed_at(filing) -> datetime | None:
    """Radar ordering correction (design/DECISIONS.md): the best-available
    *official* filed moment — never FilingEvent.retrieved_at (a wholly
    separate capture/retrieval timestamp). Prefers `filing.filed_at` (a
    full date+time, populated today only for EDINET) when it parses;
    otherwise falls back to `filing.rcept_dt` (date-only, all three
    sources — see `_parse_rcept_date`) treated as midnight that day.
    Always returns a naive datetime (any offset on `filed_at` is
    stripped) so every filing's value is mutually comparable regardless
    of source — this module never needs true cross-timezone precision,
    only a single global newest-first ordering. None when neither field
    yields a parseable value — never a fabricated or capture-time
    substitute."""
    if filing.filed_at:
        try:
            parsed = datetime.fromisoformat(filing.filed_at)
        except ValueError:
            parsed = None
        else:
            return parsed.replace(tzinfo=None) if parsed.tzinfo is not None else parsed
    parsed_date = _parse_rcept_date(filing.rcept_dt)
    if parsed_date is not None:
        return datetime(parsed_date.year, parsed_date.month, parsed_date.day)
    return None


def _radar_sort_key(item: RadarItem):
    """Global newest-first ordering across every source (Radar ordering
    correction, design/DECISIONS.md) — replaces the earlier per-source-
    string comparison (`rcept_dt` sorted as raw text), which silently
    broke cross-source ordering: DART's dash-free "20260828" sorted above
    EDGAR's dashed "2026-09-02" as plain strings even though the EDGAR
    filing is chronologically later. A single ascending sort (no
    `reverse=True`) on this tuple achieves every required property in one
    pass: filings with a parseable official filed moment (rank 0) always
    sort before those without one (rank 1) — never the reverse; within
    rank 0, negating the seconds-since-datetime.min value turns ascending
    order into newest-first; and `(source_name, rcept_no)` is a
    deterministic, always-present tie-breaker for equal timestamps or for
    every rank-1 (undated) item alike, so ordering never depends on
    source/repository iteration order and never jumps around on refresh
    (Python's sort is stable, but this tuple makes the order fully
    deterministic even before stability would matter)."""
    filed_at = _official_filed_at(item.filing)
    if filed_at is not None:
        negated_seconds = -(filed_at - datetime.min).total_seconds()
        return (0, negated_seconds, item.filing.source_name, item.filing.rcept_no)
    return (1, 0.0, item.filing.source_name, item.filing.rcept_no)


def _load_source_items(source: str, cache_dir, settings: Settings | None, json_filings, json_candidates):
    """Durable-State Phase 2B (sqlite) / 4M-1 (postgres) — the one place
    `_build_items` decides, per source, whether to read through a
    repository (sqlite/postgres) or the existing JSON path. `json_filings`/
    `json_candidates` are zero-arg thunks so each call site supplies its
    own already-correct JSON loader/filename without this function
    needing to know DART/EDGAR/EDINET's differing JSON conventions.

    A repository-construction or read failure (misconfigured or
    unreachable Postgres, most realistically) is caught here and
    degrades to an empty result for *this source only* — mirroring
    scripts/radar_worker.py's own per-provider isolation: one source's
    read problem never prevents another source's items from rendering,
    and never raises past this page's own render() call. Read-only
    either way — this function never calls a repository's write
    methods."""
    backend = (settings.db_backend or "json").strip().lower() if settings is not None else "json"
    if backend in ("sqlite", "postgres"):
        try:
            filings = backend_factory.get_filing_event_repository(settings, source).load_filing_events()
            candidates = backend_factory.get_candidate_repository(settings, source).load_candidates()
        except Exception:  # noqa: BLE001 — fail closed per source; never leak a raw connection/config error
            return (), {}
        return filings, candidates
    return json_filings(), json_candidates()


def _build_items(cache_dir, settings: Settings | None = None) -> list[RadarItem]:
    """`settings` is additive and optional (Durable-State Phase 2B;
    extended to Postgres in Phase 4M-1) — see backend_factory.py's
    module docstring. Omitted, every read goes through
    scan_service.py/candidate_store.py directly, exactly as before this
    phase. Supplied with `settings.db_backend` of `"sqlite"` or
    `"postgres"`, every read instead goes through that backend's
    filing-event/candidate repositories via `_load_source_items` above —
    this is a read-only display path in every case; no write happens
    here regardless of backend, and a Postgres-backed candidate keeps
    whatever `CandidateStatus` it already has (rendering never mutates
    it — only an explicit, separately-clicked review action does, via
    the existing `_on_process`/`_on_review_decision` callbacks below,
    unchanged by this phase)."""
    items: list[RadarItem] = []

    dart_filings, dart_candidates = _load_source_items(
        "OpenDART / DART", cache_dir, settings,
        lambda: dart_scan_service.load_filing_events(cache_dir),
        lambda: candidate_store.load_candidates(cache_dir),
    )
    dart_by_rcept_no = {c.filing.rcept_no: c for c in dart_candidates.values()}
    items += [RadarItem(filing=f, candidate=dart_by_rcept_no.get(f.rcept_no)) for f in dart_filings]

    edgar_filings, edgar_candidates = _load_source_items(
        "SEC EDGAR", cache_dir, settings,
        lambda: edgar_scan_service.load_filing_events(cache_dir),
        lambda: candidate_store.load_candidates(cache_dir, edgar_pipeline.CANDIDATE_STORE_FILENAME),
    )
    edgar_by_accession_no = {c.filing.rcept_no: c for c in edgar_candidates.values()}
    items += [RadarItem(filing=f, candidate=edgar_by_accession_no.get(f.rcept_no)) for f in edgar_filings]

    edinet_filings, edinet_candidates = _load_source_items(
        "EDINET", cache_dir, settings,
        lambda: edinet_scan_service.load_filing_events(cache_dir),
        lambda: candidate_store.load_candidates(cache_dir, edinet_pipeline.CANDIDATE_STORE_FILENAME),
    )
    edinet_by_doc_id = {c.filing.rcept_no: c for c in edinet_candidates.values()}
    items += [RadarItem(filing=f, candidate=edinet_by_doc_id.get(f.rcept_no)) for f in edinet_filings]

    return sorted(items, key=_radar_sort_key)


def _worker_scan_status_snapshot(settings: Settings):
    """Durable-State Phase 4M-0 (corrected) — read-only lookup of the
    standalone worker's (scripts/radar_worker.py) own per-provider scan
    status. Deliberately reads only this page's own, already-existing
    `settings.db_backend`/`settings.state_db_url` — the same dashboard
    Postgres/SQLite bridge every other read in this module already uses
    (get_signal_repository, get_candidate_repository via _build_items'
    own `use_sqlite` check) — never
    `radar_worker_db_backend`/`radar_worker_state_db_path`/
    `radar_worker_state_db_url`. Those three fields are worker-only:
    scripts/radar_worker.py reads them from its own separate process
    environment; this dashboard must never read or require them (see
    design/DECISIONS.md's Phase 4M-0 correction record for why). If an
    operator wants this expander to show real data, they point this
    dashboard's own EDGE_DB_BACKEND/EDGE_STATE_DB_URL at the same
    physical database the worker's own EDGE_RADAR_WORKER_DB_BACKEND/
    EDGE_RADAR_WORKER_STATE_DB_URL targets — an operational choice made
    entirely outside this function, which never bridges the two itself.

    This function never scans, never writes, never resolves an
    identifier, and never runs on a page load unless a page load happens
    to occur — matching the existing pattern already used by every other
    read in this module (_build_items, _edinet_scope_line).

    Returns ("not_configured", None) when this dashboard's own backend
    isn't sqlite/postgres (the default, "json", today) — the expected
    state for this phase, since Phase 4M-0 documents but does not
    provision worker hosting or a dashboard candidate-read bridge (see
    design/RADAR_WORKER_DEPLOYMENT.md). Returns ("unreachable", None) if
    a sqlite/postgres backend is configured but the status store can't
    be reached right now — never raising, never surfacing the underlying
    exception. Returns ("ok", statuses) otherwise."""
    backend = (settings.db_backend or "json").strip().lower()
    if backend not in ("sqlite", "postgres"):
        return "not_configured", None
    try:
        statuses = backend_factory.get_scan_status_repository(settings).get_all_scan_statuses()
    except Exception:  # noqa: BLE001 — never leak a raw connection/config error into the UI
        return "unreachable", None
    return "ok", statuses


def _edgar_readiness_or_unavailable(settings: Settings) -> edgar_service.EdgarReadiness:
    """Durable-State Phase 4M-1 — necessary correction discovered while
    implementing this phase's own fail-closed requirement for the
    candidate-read bridge. `edgar_readiness()` calls `get_edgar_companies()`,
    which (since Phase 4M-0 wired `"postgres"` into it) raises
    `BackendConfigurationError` when `settings.db_backend == "postgres"`
    but `state_db_url` is missing, blank, or unreachable — uncaught,
    this crashed the whole page before `_build_items` ever ran, which
    would have violated this phase's own requirement that an incomplete/
    unreachable Postgres configuration degrade the page safely rather
    than crash it. Fixed here, in this already-in-scope file, rather
    than in edgar_service.py itself — narrowly scoped to exactly this
    phase's own fail-closed requirement. `user_agent_configured` stays
    honest (a plain boolean derived from settings, never a secret);
    `unresolved_companies` is forced non-empty only so `.ready` is
    correctly `False` when the underlying check itself couldn't run —
    never a raw exception, DSN, or backend detail."""
    try:
        return edgar_service.edgar_readiness(settings)
    except Exception:  # noqa: BLE001 — fail closed; never leak a raw connection/config error into the UI
        return edgar_service.EdgarReadiness(
            user_agent_configured=bool(settings.edgar_user_agent), unresolved_companies=("status unavailable",),
        )


def _dart_readiness_or_unavailable(settings: Settings) -> radar_service.RadarReadiness:
    """Same rationale as `_edgar_readiness_or_unavailable` above, for
    DART's own analogous `get_radar_companies()`/`radar_readiness()`
    pair. EDINET's own `edinet_readiness()` needs no equivalent wrapper:
    `get_edinet_companies()` takes no `settings` argument at all and
    never touches a backend/repository (its five companies' identifiers
    are hardcoded), so it cannot raise this way."""
    try:
        return radar_service.radar_readiness(settings)
    except Exception:  # noqa: BLE001 — fail closed; never leak a raw connection/config error into the UI
        return radar_service.RadarReadiness(
            dart_key_configured=bool(settings.dart_api_key),
            translation_key_configured=bool(settings.translation_api_key),
            unresolved_companies=("status unavailable",),
        )


@dataclass(frozen=True)
class _DashboardSnapshot:
    """Durable-State Phase 4M-2 — every piece of data `render()` reads
    on a rerun, bundled so it can be fetched (and cached) as one unit
    instead of once per field. Nothing here is a secret or a credential
    — readiness booleans/placeholder strings, a display string, a
    worker-status snapshot, and the already-public-shaped `RadarItem`
    list `_build_items` already produces today."""

    dart_readiness: radar_service.RadarReadiness
    edgar_readiness: edgar_service.EdgarReadiness
    edinet_readiness: edinet_service.EdinetReadiness
    edinet_scope_line: str
    worker_status_state: str
    worker_status_statuses: dict | None
    items: list[RadarItem]


def _dashboard_config_fingerprint(settings: Settings) -> tuple[str, bool]:
    """The only input that determines `_load_dashboard_snapshot`'s cache
    identity below — deliberately never the `Settings` object itself,
    a DSN, or any credential. Just enough to know when the underlying
    data source has actually changed: the backend name, and whether a
    state DB URL is configured *at all* (never its value). A real
    settings change (e.g. `EDGE_DB_BACKEND` flipped from `"json"` to
    `"postgres"`) changes this tuple and therefore invalidates the
    cache; the DSN's own value changing while the backend name and
    presence stay the same would not — an accepted tradeoff, since that
    combination (same backend, same "URL present" bit, different actual
    URL) only happens via a live redeploy, which restarts the process
    and clears every in-memory cache anyway."""
    return (settings.db_backend or "json").strip().lower(), bool(settings.state_db_url)


@st.cache_data(ttl=60, show_spinner=False)
def _load_dashboard_snapshot(cache_dir, config_fingerprint: tuple[str, bool], _settings: Settings) -> _DashboardSnapshot:
    """Durable-State Phase 4M-2 — the one place `render()` reads
    readiness/worker-status/candidate data from per rerun, cached for 60
    seconds so a browser refresh reuses the already-fetched result
    instead of re-opening every repository connection from scratch (see
    design/DECISIONS.md's Phase 4M-2 entry for the full performance-
    diagnosis rationale this responds to).

    `_settings` is deliberately underscore-prefixed: Streamlit's own
    convention for "pass this argument through, but never hash it into
    the cache key" (https://docs.streamlit.io — cache_data: "any
    parameter whose name begins with an underscore ... won't be
    hashed"). A `Settings` object can carry a DSN and must never become
    part of a cache key, a log line, or Streamlit's own cache-inspection
    tooling. `config_fingerprint` (from `_dashboard_config_fingerprint`
    above) is the only argument that actually determines cache
    identity; `cache_dir` is a local path, never secret, and is included
    so a JSON-backend cache_dir change is also respected.

    Every call this function makes was already read-only before this
    phase (see each callee's own docstring) — this function performs no
    write, no scan, and no external HTTP call of its own; it only
    changes *how often* those existing reads run, not *what* they do.

    Durable-State Phase 4M-3 — minimal, non-sensitive timing
    instrumentation: every `print()` below only ever includes elapsed
    milliseconds, an item count, and `config_fingerprint` (already a
    non-secret two-tuple of the backend name and a presence boolean —
    never a DSN, credential, SQL statement, query parameter, or filing
    content). The mere presence of the first "cache MISS" line in a
    given request's logs is itself the diagnostic signal this phase
    needs: on an `st.cache_data` cache HIT, this function body — and
    every print() in it — never executes at all for that rerun; seeing
    no lines for a request proves the cache was reused, seeing them
    proves this was a genuine miss."""
    _snapshot_started_at = time.monotonic()
    print(f"[radar_inbox] dashboard snapshot cache MISS — executing (config={config_fingerprint})")

    _step_started_at = time.monotonic()
    dart_readiness = _dart_readiness_or_unavailable(_settings)
    print(f"[radar_inbox]   dart_readiness: {(time.monotonic() - _step_started_at) * 1000:.1f}ms")

    _step_started_at = time.monotonic()
    edgar_readiness = _edgar_readiness_or_unavailable(_settings)
    print(f"[radar_inbox]   edgar_readiness: {(time.monotonic() - _step_started_at) * 1000:.1f}ms")

    _step_started_at = time.monotonic()
    edinet_readiness = edinet_service.edinet_readiness(_settings)
    print(f"[radar_inbox]   edinet_readiness: {(time.monotonic() - _step_started_at) * 1000:.1f}ms")

    _step_started_at = time.monotonic()
    # Matches render()'s own pre-existing condition exactly (line below,
    # in render() itself) — only computed when EDINET is ready, so an
    # unconfigured/not-ready EDINET costs nothing extra here either.
    edinet_scope_line = _edinet_scope_line(cache_dir, _settings) if edinet_readiness.ready else ""
    print(f"[radar_inbox]   edinet_scope_line: {(time.monotonic() - _step_started_at) * 1000:.1f}ms")

    _step_started_at = time.monotonic()
    worker_status_state, worker_status_statuses = _worker_scan_status_snapshot(_settings)
    print(f"[radar_inbox]   worker_status: {(time.monotonic() - _step_started_at) * 1000:.1f}ms")

    _step_started_at = time.monotonic()
    items = _build_items(cache_dir, _settings)
    print(f"[radar_inbox]   build_items: {(time.monotonic() - _step_started_at) * 1000:.1f}ms ({len(items)} items)")

    total_ms = (time.monotonic() - _snapshot_started_at) * 1000
    print(f"[radar_inbox] dashboard snapshot TOTAL: {total_ms:.1f}ms (config={config_fingerprint})")

    return _DashboardSnapshot(
        dart_readiness=dart_readiness,
        edgar_readiness=edgar_readiness,
        edinet_readiness=edinet_readiness,
        edinet_scope_line=edinet_scope_line,
        worker_status_state=worker_status_state,
        worker_status_statuses=worker_status_statuses,
        items=items,
    )


def render() -> None:
    settings = get_settings()
    config_fingerprint = _dashboard_config_fingerprint(settings)
    snapshot = _load_dashboard_snapshot(settings.cache_dir, config_fingerprint, settings)
    dart_readiness = snapshot.dart_readiness
    edgar_readiness = snapshot.edgar_readiness
    edinet_readiness = snapshot.edinet_readiness
    # Phase F1 (design/DECISIONS.md): computed once, reused by the
    # tester-facing freshness line below.
    source_readiness_by_provider = {
        "SEC EDGAR": edgar_readiness.ready,
        "OpenDART / DART": dart_readiness.ready,
        "EDINET": edinet_readiness.ready,
    }

    # Phase R1 (design/DECISIONS.md): the default header is now title +
    # exactly one subtitle — no credential-driven "Live" chip, no tracked-
    # company/theme scope dump, no source-setup/resolver/pilot language,
    # no pipeline-mechanics sentence. None of that information is deleted
    # — every line below moved into the existing collapsed "Ingestion
    # status" disclosure, unchanged in content, just no longer part of
    # the first thing a normal user sees. The Phase F1 freshness line
    # (below, after the readiness gate) is the only default-view
    # statement about source status, and it already derives strictly
    # from durable provider_scan_status — untouched by this phase.
    st.markdown('<div class="er-page-title">Latest Filings</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="er-muted">Radar watches tracked companies for material filings, theme developments, '
        'and high-confidence signals.</div>',
        unsafe_allow_html=True,
    )

    if not dart_readiness.ready and not edgar_readiness.ready and not edinet_readiness.ready:
        _render_missing_configuration(dart_readiness, edgar_readiness, edinet_readiness)
        return

    # Durable, tester-facing freshness line (Phase F1, design/DECISIONS.md)
    # — derived only from the same durable provider_scan_status snapshot
    # the operator panel below reads, never from browser/session time,
    # source code, seed data, page-render time, file mtimes, or an
    # unpersisted in-session manual-scan report. Visually secondary
    # (muted, same treatment as the scope lines above it), immediately
    # above the results below.
    enabled_sources = tuple(p for p, ready in source_readiness_by_provider.items() if ready)
    freshness = compute_radar_freshness(
        snapshot.worker_status_state, snapshot.worker_status_statuses,
        enabled_sources, settings.radar_scan_interval_minutes,
    )
    st.markdown(f'<div class="er-muted" style="margin-top:0.4rem;">{freshness.message}</div>', unsafe_allow_html=True)

    items = snapshot.items

    if not items:
        empty_state(
            "No filings captured yet",
            "Official filings will appear here automatically once ingestion has run.",
        )
        return

    # Unify-Radar-into-Latest-Filings pass: one public feed only — every
    # captured filing from every source, never gated on whether it has an
    # internal candidate/signal/theme classification. The earlier "Latest"
    # (candidate-linked only) vs. "Captured filings" (everything) view
    # selector is removed entirely.
    sources = sorted(set(_ALL_RADAR_SOURCES) | {i.filing.source_name for i in items})
    themes = sorted({i.filing.theme_slug for i in items if i.filing.theme_slug})
    parsed_dates = sorted(d for d in (_parse_rcept_date(i.filing.rcept_dt) for i in items) if d is not None)
    # Radar Inbox date-filter fix (design/DECISIONS.md): max_date must
    # always be today in the app's own display timezone — never derived
    # from the latest stored filing's own date, which previously capped
    # the picker at whatever the newest captured filing happened to be
    # (observed: picker stuck at Aug 5 on Sep 5 once ingestion lagged).
    # min_date is unaffected — still the earliest parsed filing date when
    # one exists.
    min_date = parsed_dates[0] if parsed_dates else today_local()
    max_date = today_local()

    search_col, source_col, theme_col, date_col = st.columns([2, 2, 2, 3])
    search_query = search_col.text_input("Search", key="radar-filter-search", placeholder="Company or filing title…")
    source_filter = source_col.multiselect("Source", sources, key="radar-filter-source")
    theme_filter = theme_col.multiselect("Theme", themes, key="radar-filter-theme")
    date_range = date_col.date_input(
        "Filed between", value=(min_date, max_date), min_value=min_date, max_value=max_date, key="radar-filter-dates",
    )

    # Radar layout correction (design/DECISIONS.md): the Advanced filters
    # expander (Status/Company/Language/Detection confidence) is removed
    # entirely, not relocated — Search/Source/Theme/Filed between above
    # are the complete filter set now. "Clear all filters" is preserved
    # (it still clears every remaining filter via _FILTER_KEYS) in the
    # same trailing-column position it already occupied.
    clear_col = st.columns([6, 2])[1]
    with clear_col:
        st.markdown('<div style="margin-top:1.6rem;"></div>', unsafe_allow_html=True)
        with st.container(key="cta-tertiary-clear-radar-filters"):
            st.button("Clear all filters", key="radar-clear-filters-btn", on_click=_clear_filters)

    filtered = items
    if search_query and search_query.strip():
        query = search_query.strip().lower()
        filtered = [i for i in filtered if query in i.filing.report_nm.lower() or query in i.filing.corp_name.lower()]
    if source_filter:
        filtered = [i for i in filtered if i.filing.source_name in source_filter]
    if theme_filter:
        filtered = [i for i in filtered if i.filing.theme_slug in theme_filter]
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start, end = date_range
        filtered = [i for i in filtered if (d := _parse_rcept_date(i.filing.rcept_dt)) is not None and start <= d <= end]

    if not filtered:
        empty_state(
            "No items match these filters", "Clear a filter to see more results.",
            action_label="Clear all filters", on_click=_clear_filters, key="radar-no-filter-matches",
        )
        return

    # Pagination — never render more than PAGE_SIZE cards' worth of
    # containers/expanders/buttons in one script run. Reset to page 1
    # whenever any filter value changes.
    filter_signature = (
        search_query, tuple(sorted(source_filter)), tuple(sorted(theme_filter)), date_range,
    )
    if st.session_state.get("radar-filter-signature") != filter_signature:
        st.session_state["radar-page"] = 1
        st.session_state["radar-filter-signature"] = filter_signature

    total_items = len(filtered)
    total_pages = max(1, -(-total_items // PAGE_SIZE))
    current_page = max(1, min(st.session_state.get("radar-page", 1), total_pages))
    st.session_state["radar-page"] = current_page
    start_idx = (current_page - 1) * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    page_items = filtered[start_idx:end_idx]
    range_start = start_idx + 1 if total_items else 0
    range_end = min(end_idx, total_items)

    # Radar evidence-packet foundation, Phase 3, Step 3B (design/
    # DECISIONS.md) — at most one bulk comparison-record read for this
    # whole render, bounded to exactly the candidate ids on THIS page
    # (never the full filtered/loaded set, never another page) — the
    # same "one bulk call, never per-card" discipline _build_items above
    # already uses for filings/candidates. A repository construction/read
    # failure is caught here and degrades to no comparison data for this
    # render (every card still renders; only the optional Comparison row
    # is absent) — mirroring _load_source_items' own per-source fail-
    # closed pattern, never raising into render() and never logging/
    # surfacing raw exception text. Read-only: only
    # ComparisonRepositoryProtocol.latest_for_candidate_ids() is called —
    # never a comparison computation, never a write.
    page_candidate_ids = [item.candidate.id for item in page_items if item.candidate is not None and item.candidate.id]
    latest_comparisons_by_candidate_id = {}
    if page_candidate_ids:
        try:
            latest_comparisons_by_candidate_id = backend_factory.get_comparison_repository(settings).latest_for_candidate_ids(page_candidate_ids)
        except Exception:  # noqa: BLE001 — fail closed; never block card rendering on a comparison-repository problem
            latest_comparisons_by_candidate_id = {}

    for item in page_items:
        candidate_row(
            item,
            comparison_record=(latest_comparisons_by_candidate_id.get(item.candidate.id) if item.candidate is not None else None),
        )

    # Pagination controls render after the results, not above them (Phase
    # C) — the first result should be reachable without scrolling past
    # page-navigation chrome first. Page-index computation above this
    # loop is unchanged; only where these controls are drawn moved.
    summary_cols = st.columns([1, 1, 6])
    with summary_cols[0]:
        if st.button("← Previous", key="radar-page-prev", disabled=current_page <= 1, use_container_width=True):
            st.session_state["radar-page"] = current_page - 1
            st.rerun()
    with summary_cols[1]:
        if st.button("Next →", key="radar-page-next", disabled=current_page >= total_pages, use_container_width=True):
            st.session_state["radar-page"] = current_page + 1
            st.rerun()
    with summary_cols[2]:
        st.markdown(
            f'<div class="er-muted" style="margin-top:0.5rem;">{total_items} of {len(items)} items · '
            f'Page {current_page} of {total_pages} (showing {range_start}–{range_end})</div>',
            unsafe_allow_html=True,
        )
