"""Reader-facing Daily News ingestion-status line — the read + render
half of src/logic/daily_news_freshness.py (which holds every rule and
stays Streamlit-free, matching this codebase's src/logic convention).

Why this exists: the Signals page could already show an empty feed, and
a reader had no way to tell a genuinely quiet day from ingestion that
had stopped. Both looked identical. This line states what the worker
actually recorded, and says "unknown" when those rows cannot support any
claim at all.

Deliberately NOT shown here, keeping src/ui/pages/daily_news.py's own
long-standing scope (technical status lives in daily_news_admin.py):
failure codes, HTTP statuses, request durations, per-feed names, item
counts, cursor values, or the worker's own configuration. A reader gets
one plain sentence about what was ingested and when; an operator still
gets the full picture on the admin page.

What this line describes, precisely: the status rows in the database
THIS process is configured to read (EDGE_DB_BACKEND / EDGE_STATE_DB_URL
or EDGE_STATE_DB_PATH). The Daily News worker writes its status through
its own dedicated EDGE_DAILY_NEWS_WORKER_DB_BACKEND /
EDGE_DAILY_NEWS_WORKER_STATE_DB_URL configuration — the same split
daily_news_admin.py already documents — so the two can be pointed at
different databases.

A mismatch does not itself produce "unknown". It produces whatever the
configured database happens to contain: another database carrying these
tables yields that database's rows, which may be current, stale, or
absent, and absent rows alone are what classify as unknown. The line is
therefore always a true statement about the database it read, and never
a claim about a database it did not. "unknown" specifically covers no
row, no completed run ever recorded, and a read that could not be
performed at all — including the JSON backend (the default), where these
tables do not exist for this process and the accessor raises rather than
returning an empty result. Every failure path resolves there: never a
crash, never a sanitized-error string shown to a reader, and never a
healthy-looking line inferred from the absence of a failure.

--- Cache contract ---

What the repository accessor actually depends on, traced through
daily_news_backend.get_daily_news_scan_status_repository():

  * `settings.db_backend` — selects the branch, and raises for anything
    other than sqlite/postgres;
  * `settings.state_db_path` — the sqlite branch's database file;
  * `settings.state_db_url` — the postgres branch's DSN, which carries
    credentials;
  * nothing else. `settings.cache_dir` is NOT read on this path (there
    is no JSON branch), and no field of Settings varies by user or
    session: `get_settings()` builds Settings from environment variables
    alone, with no session_state involvement, and the row being read is
    one deployment-wide worker row rather than anything per-viewer. A
    cross-session cache is therefore correct for this data; were it
    user-scoped, `st.cache_data`'s own cross-session sharing would be
    wrong for it.

So the cache is keyed on the backend plus a digest identifying whichever
database that backend would open — every dependency that can vary, and
nothing that cannot. The digest is a truncated SHA-256 of the path or
DSN, so two deployments pointed at different databases can never share
an entry while the DSN itself never reaches a cache key, a log line, or
the rendered output. `_settings` is underscore-prefixed so Streamlit
never hashes it.

What is cached is the persisted rows, NOT the classification. Age is
recomputed against the caller's current time on every render, so a
cached entry cannot hold a state past its own staleness boundary; the
only thing the TTL bounds is how old the underlying rows may be (at most
_STATUS_CACHE_TTL_SECONDS), against a staleness threshold of 3 × the
locally configured ingestion interval — 90 minutes at the default 30, so
1/90th of it. A worker tick that lands mid-TTL is reflected within a
minute.
"""
from __future__ import annotations

import hashlib
import html
from dataclasses import dataclass
from datetime import datetime

import streamlit as st

from src.config.settings import Settings
from src.data_access.backend_factory import BackendConfigurationError
from src.data_access.daily_news import daily_news_backend
from src.data_access.state_db.daily_news_scan_status_repository import DailyNewsWorkerStatus
from src.logic.daily_news_freshness import (
    EDITORIAL_COVERAGE_RECORDED,
    MARKET_NEWS_COVERAGE_UNKNOWN_NOTE,
    DailyNewsIngestionStatus,
    classify_ingestion,
    failed_feed_count,
)

# One muted line, same er-muted treatment as the "Showing tracked
# coverage from the past 7 days." line it sits beside — not a banner, not
# a colored alert. There is deliberately no separate state label: each
# message from daily_news_freshness is already a complete sentence with
# its own distinct leading phrase, so the state is legible to a screen
# reader and in monochrome without color, and without the line restating
# itself.
#
# Every message is about the RUN, not the content: a recorded run means
# the worker ran and deliberately not that anything new arrived, which
# this page cannot know, and a reported failure is the exact stored fact
# — a failure code — not a claim that the attempt failed to complete,
# which nothing stored establishes. None of them claims coverage.

_STATUS_CACHE_TTL_SECONDS = 60


@dataclass(frozen=True)
class PersistedIngestionFacts:
    """Exactly the persisted facts the classification needs — small,
    picklable, and carrying no connection object, no DSN, and no
    per-feed detail."""

    worker_status: DailyNewsWorkerStatus | None
    failed_feeds: int


def _database_identity(settings: Settings) -> tuple[str, str]:
    """(backend, digest) — every dependency of the repository accessor
    that can vary between deployments, with the credential-bearing part
    reduced to a non-reversible digest. Never returns the DSN itself."""
    backend = (settings.db_backend or "json").strip().lower()
    if backend == "postgres":
        target = str(settings.state_db_url or "")
    elif backend == "sqlite":
        target = str(settings.state_db_path or "")
    else:
        target = ""
    digest = hashlib.sha256(target.encode("utf-8")).hexdigest()[:16] if target else "unconfigured"
    return backend, digest


def read_persisted_facts(settings: Settings) -> PersistedIngestionFacts:
    """One read of the worker's own status tables. Any unreadable backend
    resolves to "no rows", which classifies as the "unknown" state — see
    this module's own docstring for why that is the only honest
    fallback."""
    try:
        repository = daily_news_backend.get_daily_news_scan_status_repository(settings)
        worker_status = repository.get_worker_status()
        feed_statuses = repository.get_all_feed_statuses()
    except BackendConfigurationError:
        # The dashboard is not pointed at a backend that holds these
        # tables (JSON by default). Genuinely unknown from here, not
        # healthy and not broken.
        return PersistedIngestionFacts(worker_status=None, failed_feeds=0)
    except Exception:  # noqa: BLE001 — never leak a raw connection/driver error to a reader
        return PersistedIngestionFacts(worker_status=None, failed_feeds=0)

    return PersistedIngestionFacts(
        worker_status=worker_status, failed_feeds=failed_feed_count(feed_statuses),
    )


@st.cache_data(ttl=_STATUS_CACHE_TTL_SECONDS, show_spinner=False)
def _persisted_facts_cached(backend: str, database_digest: str, _settings: Settings) -> PersistedIngestionFacts:
    # `backend`/`database_digest` are the complete cache identity (see the
    # cache contract in this module's docstring); `_settings` is
    # underscore-prefixed so Streamlit never hashes it, because it can
    # carry a DSN.
    return read_persisted_facts(_settings)


def current_ingestion_status(settings: Settings, now: datetime | None = None) -> DailyNewsIngestionStatus:
    """The entry point every renderer should use: cached rows, classified
    against the caller's current time. Falls back to an uncached read if
    the cache itself is unavailable (no Streamlit runtime), so this is
    never the thing that breaks a render."""
    backend, database_digest = _database_identity(settings)
    try:
        facts = _persisted_facts_cached(backend, database_digest, settings)
    except Exception:  # noqa: BLE001 — a cache failure must never cost a reader the line
        facts = read_persisted_facts(settings)

    return classify_ingestion(
        facts.worker_status,
        settings.daily_news_scan_interval_minutes,
        failed_feeds=facts.failed_feeds,
        now=now,
        editorial_coverage_recorded=EDITORIAL_COVERAGE_RECORDED,
    )


def ingestion_status_html(status: DailyNewsIngestionStatus) -> str:
    """The one muted line. The unrecorded-coverage caveat is appended for
    every state that reports a recent run, so a reader is never left to
    read "last recorded run completed" as "every source was reached"."""
    line = status.message
    if status.state in ("current", "partial") and not status.editorial_coverage_recorded:
        line = f"{line} {MARKET_NEWS_COVERAGE_UNKNOWN_NOTE}"
    return f'<div class="er-muted">{html.escape(line)}</div>'


def render_ingestion_status(settings: Settings) -> DailyNewsIngestionStatus:
    """Renders the line and returns the status, so the caller can also
    use it to explain an empty feed without reading the backend twice."""
    status = current_ingestion_status(settings)
    st.markdown(ingestion_status_html(status), unsafe_allow_html=True)
    return status
