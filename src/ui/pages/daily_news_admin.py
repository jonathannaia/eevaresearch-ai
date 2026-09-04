"""Daily News admin/status view — hidden (not linked in the sidebar,
same `visibility="hidden"` pattern app.py already uses for `company`/
`disclaimer`), reachable by direct URL only, for controlled pilot
verification. Runs one manual discovery pass on demand and shows the
full DailyNewsScanReport, including per-item suppression reasons and
sanitized per-source failures — the detail the public page in
daily_news.py deliberately never shows.

Worker health section (Daily News operational-fix workstream, design/
DECISIONS.md) — read-only. Reads the SAME `DailyNewsWorkerStatus`/
`DailyNewsFeedScanStatus` Postgres/SQLite state `scripts/
daily_news_worker.py` already writes, via the dashboard's own AMBIENT
`Settings` (`EDGE_DB_BACKEND`/`EDGE_STATE_DB_URL`) — deliberately never
the worker's own separate `EDGE_DAILY_NEWS_WORKER_DB_BACKEND`/
`EDGE_DAILY_NEWS_WORKER_STATE_DB_URL`. This is intentional: the whole
point of this section is to show whether the dashboard is even pointed
at the same physical database the worker writes to (design/
DAILY_NEWS_WORKER_DEPLOYMENT.md's "operational choice made outside this
phase's code") — reading via the worker's own DSN would defeat that
purpose by always finding data regardless of whether the dashboard's own
configuration is correct. Never triggers a scan, never writes anything,
never a DSN/credential in any rendered string."""
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from src.config.settings import get_settings
from src.data_access.backend_factory import BackendConfigurationError
from src.data_access.daily_news import daily_news_backend, daily_news_pipeline
from src.data_access.daily_news.feed_registry import PILOT_FEEDS
from src.ui.components.section import section_header
from src.ui.pages.daily_news import _FRESHNESS_WINDOW_DAYS, _published_stories, _recent_stories


def _elapsed_hours(timestamp: str | None, now: datetime) -> float | None:
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (now - parsed).total_seconds() / 3600


def _render_worker_health(settings) -> None:
    """Read-only. Every failure path here is sanitized (exception class
    name only, never a raw message/DSN) and degrades to an informative
    `st.info`/`st.warning` rather than a crash — the rest of the admin
    page (manual "Run discovery now") must keep working even if the
    dashboard's own backend isn't pointed at Postgres/SQLite at all."""
    section_header("Autonomous worker health", "Read-only — reads the dashboard's own configured backend, never triggers a scan.")

    backend = (settings.db_backend or "json").strip().lower()
    st.write(f"Dashboard store backend: `{backend}`")

    if backend not in ("postgres", "sqlite"):
        st.info(
            "The dashboard is reading the JSON backend (EDGE_DB_BACKEND is unset or not "
            "postgres/sqlite) — the autonomous worker's status lives only in Postgres/SQLite "
            "and is not visible from this backend. To see live worker health here, point the "
            "dashboard's own EDGE_DB_BACKEND/EDGE_STATE_DB_URL at the same database the worker's "
            "EDGE_DAILY_NEWS_WORKER_STATE_DB_URL writes to (see design/"
            "DAILY_NEWS_WORKER_DEPLOYMENT.md)."
        )
        return

    try:
        scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(settings)
        worker_status = scan_status_repository.get_worker_status()
        feed_statuses = scan_status_repository.get_all_feed_statuses()
    except BackendConfigurationError as exc:
        st.warning(f"Could not read worker status: {exc}")
        return
    except Exception as exc:  # noqa: BLE001 — never leak a raw connection/driver error
        st.warning(f"Could not read worker status ({type(exc).__name__}).")
        return

    now = datetime.now(timezone.utc)

    if worker_status is None:
        st.info("No worker status recorded yet — the autonomous worker has not completed a tick against this database.")
    else:
        st.write(f"Last completed tick: {worker_status.last_tick_completed_at or '—'}")
        st.write(f"Last tick started: {worker_status.last_tick_started_at or '—'}")
        st.write(f"Last reconciliation pass: {worker_status.last_reconciliation_at or '—'}")

    polled = len(PILOT_FEEDS)
    succeeded = sum(1 for s in feed_statuses.values() if s.last_failure_code is None)
    failed = sum(1 for s in feed_statuses.values() if s.last_failure_code is not None)
    not_yet_attempted = polled - len(feed_statuses)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Feeds registered", polled)
    col2.metric("Fetched successfully", succeeded)
    col3.metric("Failed", failed)
    col4.metric("Never attempted", max(not_yet_attempted, 0))

    section_header("Latest scan item counts")
    st.write(
        f"Items discovered: **{sum(s.items_discovered_last_run for s in feed_statuses.values())}**  ·  "
        f"Newly published: **{sum(s.stories_published_last_run for s in feed_statuses.values())}**"
    )
    st.caption(
        "Already-seen, title-deduplicated, and invalid/no-URL-suppressed counts are not "
        "currently persisted per feed by the worker (only the two totals above and a per-feed "
        "publish/failure outcome are) — those three breakdowns are only available for a manual "
        "\"Run discovery now\" run within this session, shown below."
    )

    # Resilience fix (Daily News final-integration-review workstream,
    # design/DECISIONS.md): the story-repository read is a SEPARATE
    # connection-construction path from the scan-status repository above
    # (daily_news_backend.get_daily_news_repository() vs. get_daily_news_
    # scan_status_repository()) — a successful scan-status read does not
    # guarantee this one also succeeds (a transient blip, pool exhaustion,
    # etc.). Wrapped with the exact same two-tier sanitization policy as
    # the scan-status read above: BackendConfigurationError's own
    # (already-sanitized-at-its-raise-site) message, or the bare
    # exception class name for anything else — never a DSN, path, or raw
    # message. On failure, the worker/feed status already rendered above
    # stays on the page; only this and the freshness-window section
    # below degrade to a concise "unavailable" notice instead of crashing
    # the whole admin page.
    published_stories = None
    story_status_error: str | None = None
    try:
        published_stories = _published_stories(settings)
    except BackendConfigurationError as exc:
        story_status_error = str(exc)
    except Exception as exc:  # noqa: BLE001 — never leak a raw connection/driver error
        story_status_error = f"{type(exc).__name__}"

    section_header("Persisted story freshness")
    if story_status_error is not None:
        st.warning(f"Latest story status unavailable: {story_status_error}")
    else:
        latest_published_at = None
        if published_stories:
            latest_published_at = max(s.sources[0].published_at for s in published_stories if s.sources)
        st.write(f"Latest persisted story `published_at`: {latest_published_at or '—'}")

    staleness_threshold_hours = settings.daily_news_reconciliation_staleness_hours
    most_recent_fetch_success = None
    for status in feed_statuses.values():
        if status.last_fetch_success_at and (most_recent_fetch_success is None or status.last_fetch_success_at > most_recent_fetch_success):
            most_recent_fetch_success = status.last_fetch_success_at
    elapsed_since_success = _elapsed_hours(most_recent_fetch_success, now)
    if elapsed_since_success is None or elapsed_since_success >= staleness_threshold_hours:
        st.warning(
            f"No successful feed fetch recorded in the last {staleness_threshold_hours} hours "
            f"(EDGE_DAILY_NEWS_RECONCILIATION_STALENESS_HOURS) — the worker may not be running."
        )

    if story_status_error is None and not _recent_stories(published_stories):
        st.warning(f"No persisted story falls within the public page's {_FRESHNESS_WINDOW_DAYS}-day freshness window.")

    if feed_statuses:
        section_header("Per-feed status (sanitized)")
        for company_name in sorted(feed_statuses):
            status = feed_statuses[company_name]
            result = f"`{status.last_failure_code}`" if status.last_failure_code else "ok"
            st.write(
                f"- **{company_name}** — last success: {status.last_fetch_success_at or '—'}  ·  "
                f"last attempt: {status.last_attempt_at or '—'}  ·  result: {result}"
            )


def render() -> None:
    st.markdown('<div class="er-page-title">Daily News — Admin / Status</div>', unsafe_allow_html=True)

    settings = get_settings()
    if not settings.daily_news_admin_enabled:
        st.info("Daily News — Admin is not enabled on this deployment.")
        return

    st.markdown(
        '<div class="er-muted">Internal ingestion status — not linked from the public sidebar.</div>',
        unsafe_allow_html=True,
    )

    _render_worker_health(settings)
    section_header("Manual discovery run")

    if st.button("Run discovery now"):
        with st.spinner("Polling pilot feeds..."):
            # Daily News durability workstream: storage only — this button
            # still does exactly one on-demand discovery pass, reporting
            # the same DailyNewsScanReport shape as before. Which backend
            # it reads/writes against now follows the same EDGE_DB_BACKEND
            # setting every other repository in this app already honors,
            # instead of being hardcoded to the JSON file.
            repository = daily_news_backend.get_daily_news_repository(settings)
            report = daily_news_pipeline.run_discovery(settings.cache_dir, daily_news_repository=repository)
        st.session_state["_daily_news_last_report"] = report

    report = st.session_state.get("_daily_news_last_report")
    if report is None:
        st.write("No discovery run yet this session. Click “Run discovery now”.")
        return

    section_header("Last run")
    st.write(f"Scan ID: `{report.scan_id}`")
    st.write(f"Started: {report.started_at}  ·  Completed: {report.completed_at}")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Sources polled", report.sources_polled)
    col2.metric("Items discovered", report.items_discovered)
    col3.metric("Stories published", report.stories_published)
    col4.metric("Deduplicated", report.items_deduplicated)

    if report.source_failures:
        section_header("Source failures (sanitized)")
        for company_name, failure_code in report.source_failures.items():
            st.write(f"- **{company_name}**: `{failure_code}`")

    if report.warnings:
        section_header("Warnings")
        for warning in report.warnings:
            st.write(f"- {warning}")

    if report.suppressed_items:
        section_header("Suppressed items")
        for company_name, title, reason in report.suppressed_items:
            st.write(f"- **[{company_name}]** {title} — _{reason}_")
