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
from src.models.models import CandidateSignal, CandidateStatus
from src.ui.components.empty_state import empty_state
from src.ui.components.freshness import freshness_chip
from src.ui.components.radar_card import candidate_row
from src.ui.components.radar_status import RadarItem, status_label

_DART_SCOPE_LINE = "OpenDART / DART · Samsung Electronics + SK Hynix · Memory + AI Buildout"
_EDGAR_SCOPE_LINE = "SEC EDGAR · NVIDIA, Micron, Coherent, Rockwell Automation, Rocket Lab · AI Buildout, Memory, Photonics, Humanoids, Space"

# View-mode + pagination (usability/navigation-stability follow-up — see
# design/DECISIONS.md): the raw "every FilingEvent, every source, no cap"
# list could grow to hundreds of cards, each with several nested
# containers/expanders/buttons — rendering all of them at once was found
# live to make the app's own sidebar navigation unreliable. Signals &
# review queue (candidates only) is the default, useful view; All filing
# events is opt-in and always paginated, same as Signals if it ever grows
# past one page. No page ever renders more than PAGE_SIZE cards' worth of
# widgets, regardless of view or filters.
_SIGNALS_VIEW = "Signals & review queue"
_ALL_FILINGS_VIEW = "All filing events"
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
    empty_state(
        "Radar Inbox is not configured",
        " ".join(lines) + " Add the missing configuration to your local .env and restart the app.",
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


def _render_worker_status(settings: Settings) -> None:
    """Renders the required states without ever showing an API key,
    DSN, EDGE_* environment-variable name, raw exception, stack trace,
    internal resolver command, or unresolved-issuer list — only provider
    display names, counts, and timestamps already established elsewhere
    in this codebase as safe to print (see ScanReport's own docstring).
    Read-only and sanitized: calls only get_all_scan_statuses(), never a
    write method, and never displays failure_code's own raw value."""
    state, statuses = _worker_scan_status_snapshot(settings)
    with st.expander("Continuous worker status only — not a candidate feed (read-only)"):
        st.markdown(f'<div class="er-muted">{_WORKER_STATUS_SCOPE_NOTE}</div>', unsafe_allow_html=True)
        if state == "not_configured":
            st.markdown(
                '<div class="er-muted" style="margin-top:0.4rem;">This dashboard is not configured with a '
                'sqlite/postgres backend, so no worker status can be shown here. See '
                'design/RADAR_WORKER_DEPLOYMENT.md for the separate continuous-worker deployment path.</div>',
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
        for provider in _WORKER_TRACKED_PROVIDERS:
            status = statuses.get(provider)
            if status is None:
                st.markdown(
                    f'<div class="er-muted" style="margin-top:0.4rem;">{provider}: no continuous scan has run yet.</div>',
                    unsafe_allow_html=True,
                )
            elif status.failure_code:
                st.markdown(
                    f'<div class="er-muted" style="margin-top:0.4rem;">{provider}: last continuous scan attempt did not complete '
                    f'(last success: {status.last_successful_at or "never"}).</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f'<div class="er-muted" style="margin-top:0.4rem;">{provider}: healthy · last successful scan {status.last_successful_at} · '
                    f'{status.candidates_created} candidate(s) created, {status.skipped_unresolved_count} skipped '
                    f'(unresolved identifier).</div>',
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


def render() -> None:
    settings = get_settings()
    dart_readiness = _dart_readiness_or_unavailable(settings)
    edgar_readiness = _edgar_readiness_or_unavailable(settings)
    edinet_readiness = edinet_service.edinet_readiness(settings)

    header_cols = st.columns([4, 2])
    with header_cols[0]:
        st.markdown('<div class="er-page-title">Radar Inbox</div>', unsafe_allow_html=True)
        st.markdown('<div class="er-muted">Automated primary-filing discovery</div>', unsafe_allow_html=True)
    with header_cols[1]:
        st.markdown('<div style="text-align:right; margin-top:0.8rem;">', unsafe_allow_html=True)
        freshness_chip("live" if (dart_readiness.ready or edgar_readiness.ready or edinet_readiness.ready) else "demo", key="fresh-radar-head")
        st.markdown("</div>", unsafe_allow_html=True)

    if dart_readiness.ready:
        st.markdown(f'<div class="er-muted">{_DART_SCOPE_LINE}</div>', unsafe_allow_html=True)
    if edgar_readiness.ready:
        st.markdown(f'<div class="er-muted" style="margin-top:0.2rem;">{_EDGAR_SCOPE_LINE}</div>', unsafe_allow_html=True)
    if edinet_readiness.ready:
        st.markdown(f'<div class="er-muted" style="margin-top:0.2rem;">{_edinet_scope_line(settings.cache_dir, settings)}</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="er-muted" style="margin-top:0.2rem;">Live primary filings · Korea DART + SEC EDGAR pilots '
        '(EDINET seam present, not yet a live pilot)</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="er-muted" style="margin-top:0.4rem;">Candidate signals are rule-based filing flags, '
        'not published market interpretations.</div>',
        unsafe_allow_html=True,
    )

    if not dart_readiness.ready and not edgar_readiness.ready and not edinet_readiness.ready:
        _render_missing_configuration(dart_readiness, edgar_readiness, edinet_readiness)
        return

    # Data controls moved into a collapsed, explicitly-labeled expander —
    # scanning is a local/admin action, not something every visit needs
    # front-and-center. Behavior/code paths below are unchanged from
    # before this move; only the surrounding container changed.
    with st.expander("Data controls (local/admin)"):
        st.markdown(
            '<div class="er-muted">Source scans can take time and are intended for local/admin use.</div>',
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

    _render_worker_status(settings)

    items = _build_items(settings.cache_dir, settings)

    if not items:
        empty_state(
            "No filings scanned yet",
            "Run a scan above to pull the latest tracked-company disclosures.",
        )
        return

    # View selector — the top-level content control (Requirement 1):
    # Signals & review queue (candidate-only, the useful default) vs. All
    # filing events (the full raw inventory, opt-in). Read-only: switching
    # views never creates a signal, mutates a status, or invokes processing.
    view_mode = st.radio(
        "View", [_SIGNALS_VIEW, _ALL_FILINGS_VIEW], key="radar-view-mode", horizontal=True,
    )

    view_items = [i for i in items if i.candidate is not None] if view_mode == _SIGNALS_VIEW else items

    if view_mode == _SIGNALS_VIEW and not view_items:
        empty_state(
            "No candidate signals yet",
            "No filing currently meets the configured candidate rules.",
            action_label="Show all filing events",
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

    search_col, source_col, theme_col, status_col, date_col = st.columns([2, 2, 2, 2, 3])
    search_query = search_col.text_input("Search", key="radar-filter-search", placeholder="Company or filing title…")
    source_filter = source_col.multiselect("Source", sources, key="radar-filter-source")
    theme_filter = theme_col.multiselect("Theme", themes, key="radar-filter-theme")
    status_filter = status_col.multiselect("Status", statuses, key="radar-filter-status")
    date_range = date_col.date_input(
        "Filed between", value=(min_date, max_date), min_value=min_date, max_value=max_date, key="radar-filter-dates",
    )

    adv_col, clear_col = st.columns([6, 2])
    with adv_col:
        with st.expander("Advanced filters"):
            adv_cols = st.columns(3)
            company_filter = adv_cols[0].multiselect("Company", companies, key="radar-filter-company")
            language_filter = adv_cols[1].selectbox(
                "Language", ["All", "Korean original", "Korean + English translation", "Translation unavailable"],
                key="radar-filter-language",
            )
            confidence_filter = adv_cols[2].multiselect("Detection confidence", ["Moderate", "High"], key="radar-filter-confidence")
    with clear_col:
        st.markdown('<div style="margin-top:1.6rem;"></div>', unsafe_allow_html=True)
        st.button("Clear all filters", key="radar-clear-filters-btn", on_click=_clear_filters, use_container_width=True)

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
        return review_actions.record_review_decision(settings.cache_dir, candidate_id, filename, status, note, settings=settings)

    # Reuses the exact same readiness objects computed at the top of
    # render() for the Scan buttons — a candidate's "Prepare analyst
    # view"/"Retry analyst view preparation" action needs its own
    # source's live credentials just as much as a scan does, and must be
    # disabled (with an honest reason) rather than silently attempted
    # when they're absent.
    process_readiness_by_source = {
        "OpenDART / DART": dart_readiness.ready,
        "SEC EDGAR": edgar_readiness.ready,
        "EDINET": edinet_readiness.ready,
    }

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

    for item in page_items:
        candidate_row(
            item, on_process=_on_process,
            process_ready=process_readiness_by_source.get(item.filing.source_name, False),
            on_review_decision=_on_review_decision,
        )
