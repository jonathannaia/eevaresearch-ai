# Daily News Worker Deployment

This document describes how `scripts/daily_news_worker.py` (the
standalone, continuous autonomous-discovery worker for Daily News) is
meant to be deployed, and what it deliberately does **not** do on its
own. It is a documentation-only artifact — no hosting platform is
provisioned, configured, or deployed by writing this file. Actually
enabling live, continuous scanning against a deployed worker is a
separate, later, explicitly approved action (see "Proposed deployment
plan" at the bottom).

## The dashboard is not the scheduler

The Daily News page (`src/ui/pages/daily_news.py`) only reads whatever
is already stored — it never fetches, discovers, or writes anything on
page load. `scripts/daily_news_worker.py` is a **separate, long-running
process**, deployed independently of the dashboard, on infrastructure
that actually supports an always-on (or reliably-scheduled) process —
the same requirement `design/RADAR_WORKER_DEPLOYMENT.md` describes for
`scripts/radar_worker.py`. `EDGE_DAILY_NEWS_LIVE_SCAN_ENABLED` (the
worker's own master switch) is read only by the worker's own `main()`,
never by the dashboard.

## Why Postgres is required — stricter than the Radar worker

`scripts/daily_news_worker.py` requires `EDGE_DAILY_NEWS_WORKER_DB_BACKEND`
to be exactly `"postgres"`. Unlike `scripts/radar_worker.py`, **SQLite is
not an accepted live-mode backend at all** — `_build_worker_settings()`
rejects `"json"`, `"sqlite"`, unset, or blank with a fatal, sanitized
startup error. A local SQLite file exists only so a direct, local test
can construct its own `Settings` and call `run_one_tick()` without going
through `main()`'s live-mode gate; that gate itself never accepts it.

The reasoning otherwise matches Radar's own deployment doc exactly: the
dashboard and the worker are two separate processes with no shared
memory or filesystem, so for the worker's own per-feed scan-status state
(`DailyNewsFeedScanStatus`/`DailyNewsWorkerStatus`) to mean anything
anywhere else, both sides must read/write the same durable Postgres
database. These are deliberately separate settings from the dashboard's
own `EDGE_DB_BACKEND`/`EDGE_STATE_DB_URL`, so a dashboard secrets
misconfiguration can never make this worker (or vice versa) silently
point at the wrong database.

**No worker status or controls are exposed anywhere in the public UI,
the sidebar, or the hidden `daily_news_admin.py` page.** Every status
field the worker writes is read back only by tests and any future,
separately-approved internal tooling — there is currently no dashboard
surface analogous to Radar Inbox's "Continuous worker status only"
expander for Daily News.

## No third-party credentials required

Unlike the Radar worker (which needs `EDGE_EDGAR_USER_AGENT`,
`EDGE_DART_API_KEY`, `EDGE_TRANSLATION_API_KEY`, and
`EDGE_EDINET_SUBSCRIPTION_KEY`), the Daily News pipeline reads no
third-party API credential at all — `daily_news_pipeline.py` and the
registered `PILOT_FEEDS` (public RSS/Atom feeds only) use no
`os.getenv`/`os.environ` call anywhere. The only configuration this
worker needs is its own scheduling/backend variables, listed below.

## Environment variables

All of these are read only by `scripts/daily_news_worker.py`'s own
`main()`/`Settings` construction — none of them are read by `app.py` or
any UI page. **Names only — no value is written or exposed by this
document.**

| Variable | Required | Purpose |
|---|---|---|
| `EDGE_DAILY_NEWS_LIVE_SCAN_ENABLED` | Yes (master switch) | `true`/`1`/`yes`/`on` to let the worker do anything at all. Absent or any other value: the worker prints a message and exits `0` immediately. |
| `EDGE_DAILY_NEWS_WORKER_DB_BACKEND` | Yes | Must be exactly `postgres`. `json`, `sqlite`, blank, or unset is a fatal, sanitized startup error — stricter than the Radar worker. |
| `EDGE_DAILY_NEWS_WORKER_STATE_DB_URL` | Yes (backend is always `postgres` in live mode) | The worker's own Postgres DSN — deliberately separate from the dashboard's own `EDGE_STATE_DB_URL`. |
| `EDGE_DAILY_NEWS_WORKER_STATE_DB_PATH` | No — local/direct-test-only | Never accepted by the live-mode gate (`main()`); exists only for a test to construct `Settings` and call `run_one_tick()` directly. |
| `EDGE_DAILY_NEWS_SCAN_INTERVAL_MINUTES` | No (default `30`) | Minutes between full ticks (all registered feeds). Floored to a 60-second minimum regardless of the configured value, as defense-in-depth. |
| `EDGE_DAILY_NEWS_RECONCILIATION_INTERVAL_HOURS` | No (default `24`) | How often the separate feed-health reconciliation pass runs. |
| `EDGE_DAILY_NEWS_RECONCILIATION_STALENESS_HOURS` | No (default `72`) | A feed is flagged in reconciliation only if it has had no *successful fetch* in this window — a healthy feed that simply published nothing new is never flagged. |

## No identifier resolution step

Unlike the Radar worker (which needs `scripts/resolve_tracked_identifiers.py`
run once against EDGAR/DART before starting), the Daily News worker needs
no separate resolution step — `feed_registry.PILOT_FEEDS` is a static
list of company name → feed URL entries; nothing needs a runtime-resolved
external identifier.

## Starting the worker

```bash
# All configuration via environment variables (or a process manager's
# own env-injection mechanism) — there is no config file and no CLI flag.
export EDGE_DAILY_NEWS_LIVE_SCAN_ENABLED=true
export EDGE_DAILY_NEWS_WORKER_DB_BACKEND=postgres
export EDGE_DAILY_NEWS_WORKER_STATE_DB_URL="<postgres DSN>"
export EDGE_DAILY_NEWS_SCAN_INTERVAL_MINUTES=30

.venv/bin/python -m scripts.daily_news_worker
```

The process runs until it receives `SIGTERM`/`SIGINT` (a normal
container/orchestrator stop signal), at which point it finishes any
in-progress tick, then exits cleanly rather than starting a new one.

## Safety invariants this deployment preserves

These hold regardless of hosting platform, and are proven by
`tests/test_daily_news_worker.py`, not just documented here:

- Each registered feed is discovered inside its own try/except per tick
  — one feed's exception is recorded only in that feed's own
  `DailyNewsFeedScanStatus.last_failure_code` (sanitized to
  `type(exc).__name__` only, never a raw message) and never prevents the
  other feeds from being attempted in the same tick. A tick-level
  failure (e.g. the shared repository connection itself) is caught one
  level up in `main()`'s own loop, so it can never kill future ticks.
- Concurrency safety uses a single, session-level Postgres advisory lock
  (`pg_try_advisory_lock`/`pg_advisory_unlock`), acquired immediately
  before a tick and released in a `finally` block. If another instance
  already holds it, that tick is skipped entirely (no mutation) — a
  crashed or disconnected holder's lock is released automatically by
  Postgres, so a failed run self-heals on the very next tick with no
  separate staleness-timeout logic needed.
- The reconciliation pass never re-queries feeds for older history (RSS/
  Atom exposes no lookback/date-range query) — it only checks each
  feed's own `last_fetch_success_at` against the configured staleness
  threshold and prints a warning; it never reads
  `last_story_published_at`, so a healthy-but-quiet feed is never
  flagged.
- Graceful shutdown: `SIGTERM`/`SIGINT` set a flag checked between ticks
  — an in-progress tick is never interrupted mid-call, but no new tick
  starts once the flag is set.

## Local testing vs. a deployed Postgres worker — operator checklist

**Local testing (SQLite, single machine, explicitly not production):**

1. Construct a `Settings` object directly with
   `daily_news_worker_db_backend="sqlite"` and
   `daily_news_worker_state_db_path=<local path>` (this is a direct,
   test-only construction — `main()`'s own live-mode gate never accepts
   this combination).
2. Call `run_one_tick(worker_settings, scan_status_repository)` directly
   and confirm it records a per-feed status row for each configured
   feed.
3. This SQLite file is disposable test state — never point a real
   deployed worker at it, and never treat it as a durable store.

**Deployed Postgres worker (the only supported production path):**

1. Provision (separately, later, explicitly approved) a small
   always-on/scheduled compute target and a Postgres database.
2. Set `EDGE_DAILY_NEWS_WORKER_DB_BACKEND=postgres` and
   `EDGE_DAILY_NEWS_WORKER_STATE_DB_URL` to that database's DSN on the
   worker's own deployment.
3. Start `scripts/daily_news_worker.py` on the deployed compute target
   with `EDGE_DAILY_NEWS_LIVE_SCAN_ENABLED=true`.
4. There is currently no dashboard-facing status surface to confirm
   ticks are succeeding — status would need to be read directly from the
   worker's own Postgres tables (`DailyNewsFeedScanStatus`/
   `DailyNewsWorkerStatus`) or from the worker process's own stdout,
   until a future, separately-approved change adds a read-only status
   view analogous to Radar Inbox's own.

## Proposed deployment plan (not executed — requires separate, explicit approval)

1. Provision a Postgres database (or reuse an existing one already
   provisioned for the Radar worker, using a distinct DSN/credential if
   sharing the same physical instance).
2. Provision a small always-on or scheduled compute target capable of
   running a long-lived Python process (the same class of target
   `design/RADAR_WORKER_DEPLOYMENT.md` describes for the Radar worker).
3. Set, on that target only:
   - `EDGE_DAILY_NEWS_LIVE_SCAN_ENABLED=true`
   - `EDGE_DAILY_NEWS_WORKER_DB_BACKEND=postgres`
   - `EDGE_DAILY_NEWS_WORKER_STATE_DB_URL=<postgres DSN>`
   - `EDGE_DAILY_NEWS_SCAN_INTERVAL_MINUTES=30` (or another value; 30 is
     the coded default)
4. Start `.venv/bin/python -m scripts.daily_news_worker` as a persistent
   process (container/service/orchestrator-managed, matching whatever
   pattern is chosen for the Radar worker).
5. Confirm the worker is ticking by inspecting its own Postgres status
   tables or stdout — no dashboard UI reflects this yet.

None of the above has been performed as part of this documentation
change. No Render configuration was accessed or modified, no live
scanning was enabled, and no persistent worker process was started.
