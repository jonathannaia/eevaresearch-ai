"""Radar Inbox — automated primary-filing discovery. Korea DART (Samsung
Electronics + SK Hynix), SEC EDGAR (NVIDIA, Micron Technology, Coherent
Corp, Rockwell Automation, Rocket Lab), and EDINET (Japan) — SoftBank
Group, Kioxia Holdings, Furukawa Electric, FANUC, ispace, all tracked as
of Gate 7 — but EDINET has never had a live scan run against it, so its
"Scan EDINET now" action is wired and its five companies are configured,
while its FilingEvent/CandidateSignal counts stay at zero until a
separately authorized live-scan gate. The only page in this app backed
by real, live data; every other page reads from the demo AppContext (see
src/data_access/dart/radar_service.py's module docstring for why sources
are kept separate).

Deliberately visually and lexically distinct from Signals (the curated,
published Signal Board): a Radar item is a filing-driven research lead
under review, never a completed market read. See
src/ui/components/radar_status.py for the separate status vocabulary.

Each source keeps its own explicit, separately-bounded "Scan ... now"
action, its own readiness check, and its own on-disk candidate store
(dart_candidates.json / edgar_candidates.json) — never merged into one
"scan everything" action. A candidate's source is always distinguished
via `filing.source_name`, never blended into one undifferentiated
"live" label.
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
from src.logic import review_actions
from src.logic.radar_freshness import categorize_source_status, compute_radar_freshness, effective_interval_minutes
from src.models.models import CandidateSignal, CandidateStatus
from src.ui.components.empty_state import empty_state
from src.ui.components.radar_card import candidate_row
from src.ui.components.radar_status import RadarItem, status_label

_DART_SCOPE_LINE = "OpenDART / DART · Samsung Electronics + SK Hynix · Memory + AI Buildout"
_EDGAR_SCOPE_LINE = "SEC EDGAR · NVIDIA, Micron, Coherent, Rockwell Automation, Rocket Lab · AI Buildout, Memory, Photonics, Humanoids, Space"

# View-mode + pagination (usability/navigation-stability follow-up — see
# design/DECISIONS.md): the raw "every FilingEvent, every source, no cap"
# list could grow to hundreds of cards, each with several nested
# containers/expanders/buttons — rendering all of them at once was found
# live to make the app's own sidebar navigation unreliable. "Latest"
# (candidates only — renamed from "Needs your decision" in Phase R1,
# design/DECISIONS.md, itself renamed from "Signals & review queue" in
# the Phase C editorial-simplicity pass — same underlying view/filter/
# ordering logic every time: `candidate is not None`, never gated on
# review status, so an already-Published or -Dismissed item still shows
# here exactly as before) is the default, useful view; "Captured filings"
# (temporarily relabeled from "All filings" in Phase F1, design/
# DECISIONS.md — only candidate-linked filings are durably persisted
# under Postgres/SQLite today, so "All filings" could read as a stronger
# completeness claim than this view can back up; the underlying query/
# filter/ordering logic is unchanged) is opt-in and always paginated,
# same as the default view if it ever grows past one page. No page ever
# renders more than PAGE_SIZE cards' worth of widgets, regardless of view
# or filters.
_SIGNALS_VIEW = "Latest"
_ALL_FILINGS_VIEW = "Captured filings"
PAGE_SIZE = 20

_FILTER_KEYS = (
    "radar-filter-search",
    "radar-filter-source",
    "radar-filter-company",
    "radar-filter-theme",
    "radar-filter-status",
    "radar-filter-dates",
    "radar-filter-language",
    "radar-filter-confidence",
)


def _switch_to_all_filings() -> None:
    st.session_state["radar-view-mode"] = _ALL_FILINGS_VIEW
    st.session_state["radar-page"] = 1


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
    `_build_items`/`_render_worker_status`."""
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
    # Phase B (UI audit): the top-level state is now one calm sentence —
    # the full env-var/resolver/unresolved-company dump moves into a
    # collapsed expander below, unchanged in content. Readiness logic and
    # the `lines` construction above are untouched.
    empty_state(
        "Radar Inbox is not configured",
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

    # Deterministic newest-first ordering with a stable secondary
    # tie-break (Durable-State Phase 4M-1): a Postgres read has no
    # guaranteed row order of its own (no ORDER BY in load_filing_events),
    # so two same-date filings could otherwise render in a different
    # relative order on different page loads. Sorting ascending by
    # (source_name, rcept_no) first, then stably by rcept_dt descending,
    # produces the same order on every render — Python's sort is stable,
    # so equal-date items retain their relative order from the first pass
    # rather than depending on repository/dict iteration order.
    items = sorted(items, key=lambda i: (i.filing.source_name, i.filing.rcept_no))
    return sorted(items, key=lambda i: i.filing.rcept_dt, reverse=True)


def _scan_summary_line(report) -> str:
    parts = [
        f"{report.filings_discovered} filings discovered", f"{report.new_filing_events} new",
        f"{report.candidates_detected} candidates detected", f"{report.candidates_processed} processed",
        f"{report.candidates_deferred} deferred", f"{report.documents_extracted} extracted",
    ]
    # Only DART's ScanReport has a translation step — EDGAR's report has
    # no such field at all (see edgar_pipeline.ScanReport's own docstring).
    translations = getattr(report, "translations_completed", None)
    if translations is not None:
        parts.append(f"{translations} translated")
    return " · ".join(parts)


def _render_scan_result(session_key_report: str, session_key_error: str) -> None:
    if st.session_state.get(session_key_report) is not None:
        report = st.session_state[session_key_report]
        with st.container(border=True):
            st.markdown(f'<div class="er-muted">Last scan: {report.scan_id} · {report.started_at}</div>', unsafe_allow_html=True)
            st.markdown(f'<div style="margin-top:0.2rem;">{_scan_summary_line(report)}</div>', unsafe_allow_html=True)
            if report.no_data_count:
                st.markdown(f'<div class="er-muted" style="margin-top:0.2rem;">{report.no_data_count} company(s) had no new disclosures in this window.</div>', unsafe_allow_html=True)
            if report.errors_by_category:
                categories = ", ".join(f"{k} ({v})" for k, v in report.errors_by_category.items())
                st.markdown(f'<div class="er-muted" style="margin-top:0.2rem;">Warnings: {categories}</div>', unsafe_allow_html=True)
    if st.session_state.get(session_key_error):
        st.markdown(f'<div class="er-muted" style="margin-top:0.4rem;">{st.session_state[session_key_error]}</div>', unsafe_allow_html=True)


_WORKER_TRACKED_PROVIDERS = ("SEC EDGAR", "OpenDART / DART", "EDINET")

_WORKER_STATUS_SCOPE_NOTE = (
    "Status only — not a candidate feed. The worker persists candidates "
    "directly to its own configured store; rendering those candidates in "
    "this dashboard requires a separately approved dashboard "
    "persistence-read bridge, which does not exist yet."
)


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


def _render_worker_status(
    state: str, statuses: dict | None, readiness_by_provider: dict[str, bool], interval_minutes: int | None,
) -> None:
    """Renders the required states without ever showing an API key,
    DSN, EDGE_* environment-variable name, raw exception, stack trace,
    internal resolver command, or unresolved-issuer list — only provider
    display names, counts, and timestamps already established elsewhere
    in this codebase as safe to print (see ScanReport's own docstring).
    Read-only and sanitized by construction: this function itself makes
    no repository call at all — `(state, statuses)` come from
    `_worker_scan_status_snapshot()`, called once per
    `_load_dashboard_snapshot()` refresh (Durable-State Phase 4M-2)
    rather than once per render — never a write method, and never
    displays failure_code's own raw value.

    Phase C (editorial-simplicity pass): no longer its own top-level
    expander — Streamlit can't nest expanders, and this is now called
    from inside render()'s single merged "Ingestion status" disclosure
    alongside the scan controls, so it renders a plain sub-heading
    instead. Content/wording below is otherwise unchanged.

    Phase F1 (design/DECISIONS.md): each tracked provider now renders one
    of four distinct, plain-text states — disabled/not configured for
    this deployment (per `readiness_by_provider`, so EDINET is never
    implied to be enabled just because its tracked companies exist in
    code — see src/config/tracked_companies.py), configured but never
    successfully scanned, recently successful, or stale (per
    `src.logic.radar_freshness.categorize_source_status`, the same
    threshold rule the tester-facing freshness line uses) — distinguished
    by wording alone, not color. A source whose most recent attempt
    failed but has never once succeeded still reads as "no completed
    scan yet," not a distinct failure state — `failure_code` stays
    unexposed here, unchanged from before this phase."""
    st.markdown(
        '<div class="er-muted" style="margin-top:0.6rem;"><strong>Continuous worker status</strong> — '
        'not a candidate feed (read-only)</div>',
        unsafe_allow_html=True,
    )
    st.markdown(f'<div class="er-muted">{_WORKER_STATUS_SCOPE_NOTE}</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="er-muted" style="margin-top:0.4rem;">Refresh mode: Automatic worker when configured; '
        "manual scan controls remain available.</div>",
        unsafe_allow_html=True,
    )
    if state == "not_configured":
        st.markdown(
            '<div class="er-muted" style="margin-top:0.4rem;">Automatic worker status is not configured. '
            "This dashboard is not configured with a sqlite/postgres backend, so no worker status can be "
            "shown here. See design/RADAR_WORKER_DEPLOYMENT.md for the separate continuous-worker "
            "deployment path.</div>",
            unsafe_allow_html=True,
        )
        return
    if state == "unreachable":
        st.markdown(
            '<div class="er-muted" style="margin-top:0.4rem;">This dashboard\'s own configured store could not '
            'be reached right now.</div>',
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        f'<div class="er-muted" style="margin-top:0.4rem;">Expected scan interval: '
        f'{effective_interval_minutes(interval_minutes)} minutes</div>',
        unsafe_allow_html=True,
    )
    for provider in _WORKER_TRACKED_PROVIDERS:
        if not readiness_by_provider.get(provider, False):
            st.markdown(
                f'<div class="er-muted" style="margin-top:0.4rem;">{provider}: not configured for this '
                f"deployment.</div>",
                unsafe_allow_html=True,
            )
            continue
        status = statuses.get(provider)
        category = categorize_source_status(status, interval_minutes)
        if category == "never_scanned":
            st.markdown(
                f'<div class="er-muted" style="margin-top:0.4rem;">{provider}: configured — no completed '
                f"scan yet.</div>",
                unsafe_allow_html=True,
            )
        elif category == "stale":
            st.markdown(
                f'<div class="er-muted" style="margin-top:0.4rem;">{provider}: stale — last successful scan '
                f'{status.last_successful_at} · {status.candidates_created} candidate(s) created, '
                f'{status.skipped_unresolved_count} skipped (unresolved identifier).</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div class="er-muted" style="margin-top:0.4rem;">{provider}: recently successful — last scan '
                f'{status.last_successful_at} · {status.candidates_created} candidate(s) created, '
                f'{status.skipped_unresolved_count} skipped (unresolved identifier).</div>',
                unsafe_allow_html=True,
            )


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
    # tester-facing freshness line, the operator panel's per-source
    # disabled/never-scanned/recent/stale distinction, and the existing
    # candidate "Process" button gating below — same three readiness
    # booleans as before, just named/shared in one place instead of
    # redefined later in this function.
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
    st.markdown('<div class="er-page-title">Radar Inbox</div>', unsafe_allow_html=True)
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

    # Ingestion status (Phase C, editorial-simplicity pass) — one calm,
    # default-collapsed disclosure combining scan controls (previously its
    # own "Data controls (local/admin)" expander) and continuous-worker
    # status (previously its own separate expander — Streamlit can't nest
    # expanders, so _render_worker_status no longer opens one of its own;
    # see that function's docstring). Every underlying control, action,
    # and piece of operational detail is unchanged, just gathered under
    # one header instead of two.
    with st.expander("Ingestion status"):
        # Phase R1: relocated verbatim from the default header — same
        # scope-line content/computation (_DART_SCOPE_LINE,
        # _EDGAR_SCOPE_LINE, snapshot.edinet_scope_line are all untouched),
        # only shown here now instead of by default.
        if dart_readiness.ready:
            st.markdown(f'<div class="er-muted">{_DART_SCOPE_LINE}</div>', unsafe_allow_html=True)
        if edgar_readiness.ready:
            st.markdown(f'<div class="er-muted" style="margin-top:0.2rem;">{_EDGAR_SCOPE_LINE}</div>', unsafe_allow_html=True)
        if edinet_readiness.ready:
            st.markdown(f'<div class="er-muted" style="margin-top:0.2rem;">{snapshot.edinet_scope_line}</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="er-muted" style="margin-top:0.2rem;">Korea DART + SEC EDGAR pilots configured '
            '(EDINET seam present, not yet a live pilot)</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="er-muted" style="margin-top:0.4rem;">Candidate signals are rule-based filing flags, '
            'not published market interpretations.</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="er-muted" style="margin-top:0.6rem;">Source scans can take time and are intended for local/admin use.</div>',
            unsafe_allow_html=True,
        )
        scan_cols = st.columns([2, 2, 2, 2])
        with scan_cols[0]:
            dart_scan_clicked = dart_readiness.ready and st.button(
                "Scan DART now", type="primary", use_container_width=True, key="scan-dart-btn",
            )
        with scan_cols[1]:
            edgar_scan_clicked = edgar_readiness.ready and st.button(
                "Scan EDGAR now", type="primary", use_container_width=True, key="scan-edgar-btn",
            )
        with scan_cols[2]:
            edinet_scan_clicked = edinet_readiness.ready and st.button(
                "Scan EDINET now", type="primary", use_container_width=True, key="scan-edinet-btn",
            )

        if dart_scan_clicked:
            with st.spinner("Scanning DART — bounded to the configured lookback window and candidate budget..."):
                try:
                    st.session_state["radar_last_scan_report"] = radar_service.run_scan(settings)
                except Exception:  # noqa: BLE001 — surfaced as a safe, generic message only
                    st.session_state["radar_last_scan_error"] = "DART scan failed — see server logs for detail."
        if edgar_scan_clicked:
            with st.spinner("Scanning EDGAR — bounded to the configured lookback window, candidate budget, and rate limit..."):
                try:
                    st.session_state["edgar_last_scan_report"] = edgar_service.run_scan(settings)
                except Exception:  # noqa: BLE001 — surfaced as a safe, generic message only
                    st.session_state["edgar_last_scan_error"] = "EDGAR scan failed — see server logs for detail."
        if edinet_scan_clicked:
            with st.spinner("Scanning EDINET — no tracked companies configured yet, so this will report zero filings this gate..."):
                try:
                    st.session_state["edinet_last_scan_report"] = edinet_service.run_scan(settings)
                except Exception:  # noqa: BLE001 — surfaced as a safe, generic message only
                    st.session_state["edinet_last_scan_error"] = "EDINET scan failed — see server logs for detail."

        _render_scan_result("radar_last_scan_report", "radar_last_scan_error")
        _render_scan_result("edgar_last_scan_report", "edgar_last_scan_error")
        _render_scan_result("edinet_last_scan_report", "edinet_last_scan_error")

        if dart_scan_clicked or edgar_scan_clicked or edinet_scan_clicked:
            # A manual "Scan ... now" click just wrote fresh data (JSON/SQLite)
            # this same rerun — invalidate the cached snapshot and re-fetch so
            # the admin who clicked it sees their own scan's results
            # immediately, exactly like before this phase's caching was added,
            # rather than waiting up to the 60-second TTL.
            _load_dashboard_snapshot.clear()
            snapshot = _load_dashboard_snapshot(settings.cache_dir, config_fingerprint, settings)

        _render_worker_status(
            snapshot.worker_status_state, snapshot.worker_status_statuses,
            source_readiness_by_provider, settings.radar_scan_interval_minutes,
        )

    items = snapshot.items

    if not items:
        empty_state(
            "No filings scanned yet",
            "Run a scan above to pull the latest tracked-company disclosures.",
        )
        return

    # View selector — the top-level content control (Requirement 1):
    # "Latest" (candidate-only, the useful default) vs. "Captured filings"
    # (the fuller inventory, opt-in) — a compact segmented control right
    # beside the default view. Read-only: switching views never creates a
    # signal, mutates a status, or invokes processing.
    view_mode = st.radio(
        "View", [_SIGNALS_VIEW, _ALL_FILINGS_VIEW], key="radar-view-mode", horizontal=True,
    )

    view_items = [i for i in items if i.candidate is not None] if view_mode == _SIGNALS_VIEW else items

    if view_mode == _SIGNALS_VIEW and not view_items:
        empty_state(
            "No candidate signals yet",
            "No filing currently meets the configured candidate rules.",
            action_label="Show captured filings",
            on_click=_switch_to_all_filings,
            key="radar-no-signals",
        )
        return

    companies = sorted({i.filing.corp_name for i in view_items})
    sources = sorted({i.filing.source_name for i in view_items})
    themes = sorted({i.filing.theme_slug for i in view_items if i.filing.theme_slug})
    statuses = sorted({status_label(i) for i in view_items})
    parsed_dates = sorted(d for d in (_parse_rcept_date(i.filing.rcept_dt) for i in view_items) if d is not None)
    min_date = parsed_dates[0] if parsed_dates else date.today()
    max_date = parsed_dates[-1] if parsed_dates else date.today()

    # Phase R1 (design/DECISIONS.md): Status moved into Advanced filters
    # — a research feed's default viewport doesn't need a human-review-
    # workflow filter front and center. Same key ("radar-filter-status"),
    # same options computation, same filtering logic below — only the
    # widget's container moved; _clear_filters()'s own _FILTER_KEYS tuple
    # is untouched, since it clears by session-state key, not position.
    search_col, source_col, theme_col, date_col = st.columns([2, 2, 2, 3])
    search_query = search_col.text_input("Search", key="radar-filter-search", placeholder="Company or filing title…")
    source_filter = source_col.multiselect("Source", sources, key="radar-filter-source")
    theme_filter = theme_col.multiselect("Theme", themes, key="radar-filter-theme")
    date_range = date_col.date_input(
        "Filed between", value=(min_date, max_date), min_value=min_date, max_value=max_date, key="radar-filter-dates",
    )

    adv_col, clear_col = st.columns([6, 2])
    with adv_col:
        with st.expander("Advanced filters"):
            adv_cols = st.columns(4)
            status_filter = adv_cols[0].multiselect("Status", statuses, key="radar-filter-status")
            company_filter = adv_cols[1].multiselect("Company", companies, key="radar-filter-company")
            language_filter = adv_cols[2].selectbox(
                "Language", ["All", "Korean original", "Korean + English translation", "Translation unavailable"],
                key="radar-filter-language",
            )
            confidence_filter = adv_cols[3].multiselect("Detection confidence", ["Moderate", "High"], key="radar-filter-confidence")
    with clear_col:
        # Phase C: a quiet inline text action rather than a separate
        # full-width button — same cta-tertiary-* ghost-link treatment
        # used for every other low-emphasis action in the app. Same key,
        # on_click, and behavior as before; only the surrounding
        # container/width changed.
        st.markdown('<div style="margin-top:1.6rem;"></div>', unsafe_allow_html=True)
        with st.container(key="cta-tertiary-clear-radar-filters"):
            st.button("Clear all filters", key="radar-clear-filters-btn", on_click=_clear_filters)

    filtered = view_items
    if search_query and search_query.strip():
        query = search_query.strip().lower()
        filtered = [i for i in filtered if query in i.filing.report_nm.lower() or query in i.filing.corp_name.lower()]
    if source_filter:
        filtered = [i for i in filtered if i.filing.source_name in source_filter]
    if company_filter:
        filtered = [i for i in filtered if i.filing.corp_name in company_filter]
    if theme_filter:
        filtered = [i for i in filtered if i.filing.theme_slug in theme_filter]
    if status_filter:
        filtered = [i for i in filtered if status_label(i) in status_filter]
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start, end = date_range
        filtered = [i for i in filtered if (d := _parse_rcept_date(i.filing.rcept_dt)) is not None and start <= d <= end]
    if language_filter == "Korean + English translation":
        filtered = [i for i in filtered if i.candidate is not None and i.candidate.excerpt_translation is not None]
    elif language_filter == "Translation unavailable":
        filtered = [i for i in filtered if i.candidate is not None and i.candidate.translation_state.value == "Translation unavailable"]
    elif language_filter == "Korean original":
        filtered = [i for i in filtered if i.candidate is None or i.candidate.excerpt_translation is None]
    if confidence_filter:
        filtered = [i for i in filtered if i.candidate is not None and i.candidate.confidence in confidence_filter]

    def _on_process(candidate_id: str) -> None:
        # Routed by the candidate's own id prefix ("edgar-cand-",
        # "edinet-cand-", vs "cand-") — reliable since each source's
        # scan_service mints its own ids, and each source's action must
        # stay independently bounded, never a merged "process anything"
        # seam.
        if candidate_id.startswith("edgar-cand-"):
            edgar_service.process_candidate_now(settings, candidate_id)
        elif candidate_id.startswith("edinet-cand-"):
            edinet_service.process_candidate_now(settings, candidate_id)
        else:
            radar_service.process_candidate_now(settings, candidate_id)
        # Invalidate the cached dashboard snapshot (Durable-State
        # Phase 4M-2) so the st.rerun() below re-renders from this
        # candidate's just-updated status, not a stale cached copy.
        _load_dashboard_snapshot.clear()
        st.rerun()

    def _on_review_decision(candidate_id: str, status: CandidateStatus, note: str) -> CandidateSignal | None:
        # Same id-prefix routing as _on_process, kept independent of it —
        # this picks the on-disk store filename only; the actual decision
        # logic lives entirely in review_actions.record_review_decision,
        # which has no source awareness of its own. The caller
        # (radar_card.py) is responsible for rerunning on success and for
        # showing an error (never silently proceeding) on a None result.
        if candidate_id.startswith("edgar-cand-"):
            filename = edgar_pipeline.CANDIDATE_STORE_FILENAME
        elif candidate_id.startswith("edinet-cand-"):
            filename = edinet_pipeline.CANDIDATE_STORE_FILENAME
        else:
            filename = candidate_store._CACHE_FILENAME
        result = review_actions.record_review_decision(settings.cache_dir, candidate_id, filename, status, note, settings=settings)
        # Invalidate the cached dashboard snapshot (Durable-State
        # Phase 4M-2) so the caller's own st.rerun() (radar_card.py, on
        # success) re-renders from this decision's just-persisted
        # status, not a stale cached copy. Harmless to clear even when
        # `result` is None (the decision failed and nothing changed).
        _load_dashboard_snapshot.clear()
        return result

    # Reuses the exact same readiness dict computed at the top of
    # render() for the Scan buttons (Phase F1: renamed/shared as
    # source_readiness_by_provider, same three booleans as before) — a
    # candidate's "Prepare analyst view"/"Retry analyst view preparation"
    # action needs its own source's live credentials just as much as a
    # scan does, and must be disabled (with an honest reason) rather than
    # silently attempted when they're absent.
    process_readiness_by_source = source_readiness_by_provider

    if not filtered:
        empty_state(
            "No items match these filters", "Clear a filter to see more results.",
            action_label="Clear all filters", on_click=_clear_filters, key="radar-no-filter-matches",
        )
        return

    # Pagination — never render more than PAGE_SIZE cards' worth of
    # containers/expanders/buttons in one script run, in either view.
    # Reset to page 1 whenever the view mode or any filter value changes.
    filter_signature = (
        view_mode, search_query, tuple(sorted(source_filter)), tuple(sorted(company_filter)),
        tuple(sorted(theme_filter)), tuple(sorted(status_filter)), date_range, language_filter,
        tuple(sorted(confidence_filter)),
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

    for item in page_items:
        candidate_row(
            item, on_process=_on_process,
            process_ready=process_readiness_by_source.get(item.filing.source_name, False),
            on_review_decision=_on_review_decision,
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
            f'<div class="er-muted" style="margin-top:0.5rem;">{total_items} of {len(view_items)} items · '
            f'Page {current_page} of {total_pages} (showing {range_start}–{range_end})</div>',
            unsafe_allow_html=True,
        )
