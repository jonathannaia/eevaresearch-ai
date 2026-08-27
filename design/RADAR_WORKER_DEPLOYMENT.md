# Radar Worker Deployment — Durable-State Phase 4M-0

This document describes how `scripts/radar_worker.py` (the standalone,
continuous autonomous-scan worker introduced in Phase 4M-0) is meant to
be deployed, and what it deliberately does **not** do on its own. It is
a documentation-only artifact for this phase — no hosting platform is
provisioned, configured, or deployed as part of Phase 4M-0. Actually
enabling live, continuous scanning against a deployed worker is a
separate, later, explicitly approved action.

## The dashboard is not the scheduler

Streamlit Community Cloud (or any similar host for `app.py`) only serves
the Radar Inbox dashboard on page requests. It has no persistent
background process, no cron, and no guarantee a given app instance stays
warm between visits. **The Streamlit process must never be the thing
that runs recurring external scans** — and it doesn't: nothing in
`app.py` or any UI page imports or calls `scripts/radar_worker.py`, and
`EDGE_RADAR_LIVE_SCAN_ENABLED` (the worker's own master switch) is read
only by the worker's own `main()`, never by the dashboard.

The continuous worker is a **separate, long-running process**, deployed
independently of the dashboard, on infrastructure that actually supports
an always-on (or reliably-scheduled) process — for example a small
always-on container/VM, a platform's "worker" or "background job"
process type, or a scheduled-job runner invoked on an interval matching
`EDGE_RADAR_SCAN_INTERVAL_MINUTES`. This phase does not choose or
provision one of these; it only requires that whichever platform is
chosen later can run a plain, long-lived Python process (or invoke it on
a schedule) and hold the environment variables below.

## Why Postgres is required for a real deployed pair

The dashboard and the worker are two separate processes with no shared
memory and, on most hosting platforms, no shared local filesystem
either. For the worker's own per-provider scan-status/cursor state
(`provider_scan_status`, Phase 4M-0's new table) to mean anything to the
dashboard's read-only status display, both processes must read and
write the *same* durable store:

- **JSON is not supported for the worker at all.** `scripts/radar_worker.py`
  refuses to start with `EDGE_RADAR_WORKER_DB_BACKEND=json` (or unset) —
  `backend_factory.get_scan_status_repository()` has no JSON
  implementation and raises a sanitized `BackendConfigurationError`
  rather than silently doing nothing useful. This isn't a gap to fill
  later: the existing one-shot `python -m scripts.run_scan` already
  covers the JSON/local-demo path adequately via its own idempotent
  `FilingEvent` dedup, which needs no cursor at all.
- **SQLite is local/test-only.** A SQLite file works for validating the
  worker's own loop behavior on a single machine (see "Local testing"
  below), but it is *not* a safe answer for a deployed dashboard+worker
  pair: a SQLite file on Streamlit Community Cloud's own filesystem is
  ephemeral and is not shared with a separately-deployed worker process
  regardless.
- **Postgres is required for a real deployed pair.** The worker writes
  via its own `EDGE_RADAR_WORKER_STATE_DB_URL`. If the dashboard is to
  read that same status (via its "Continuous worker status only" Radar
  Inbox expander), the *operator* separately points the dashboard's own,
  already-existing `EDGE_DB_BACKEND=postgres`/`EDGE_STATE_DB_URL` (Phase
  4B's own dashboard bridge — a distinct configuration, never the
  worker's env vars) at the same physical database. This is an
  operational choice made outside this phase's code — no code in this
  phase reads the worker's DSN from the dashboard process, or vice
  versa. This is the same isolated `src/data_access/postgres_state_db/`
  backend introduced in earlier Durable-State phases — no new database
  technology.

**Candidate rendering is a separate, unimplemented concern.** Even when
the dashboard and worker are pointed at the same database, Radar
Inbox's own candidate/filing-event list (`_build_items()`) reads only
`"sqlite"` for that purpose — it does not yet recognize `"postgres"` at
all, so it falls through to its JSON branch (empty, in a hosted
deployment) regardless of `EDGE_DB_BACKEND`. **The worker autonomously
ingests and persists candidates and records sanitized provider status.
Rendering worker-persisted candidates in the normal Streamlit dashboard
requires a separately approved dashboard persistence-read bridge**,
which this phase does not build. Only the sanitized per-provider status
counts (never the candidate records themselves) are visible today, via
the status expander described below.

## Environment variables

All of these are read only by `scripts/radar_worker.py`'s own
`main()`/`Settings` construction — none of them are read by `app.py` or
any UI page.

| Variable | Required | Purpose |
|---|---|---|
| `EDGE_RADAR_LIVE_SCAN_ENABLED` | Yes (master switch) | `true`/`1`/`yes`/`on` to let the worker do anything at all. Absent or any other value: the worker prints a message and exits `0` immediately. |
| `EDGE_RADAR_WORKER_DB_BACKEND` | Yes | Must be exactly `sqlite` or `postgres`. Anything else (including `json`, blank, or unset) is a fatal, sanitized startup error. |
| `EDGE_RADAR_WORKER_STATE_DB_URL` | Required if backend is `postgres` | The worker's own Postgres DSN — deliberately separate from the dashboard's own `EDGE_STATE_DB_URL`, so a dashboard secrets misconfiguration can never make the worker point at the wrong database, or vice versa. |
| `EDGE_RADAR_WORKER_STATE_DB_PATH` | Required if backend is `sqlite` | Local file path — local/test-only, see above. |
| `EDGE_RADAR_SCAN_INTERVAL_MINUTES` | No (default `60`) | Minutes between full scan rounds (all configured providers). Floored to a 60-second minimum regardless of the configured value, as defense-in-depth. |
| `EDGE_EDGAR_USER_AGENT` | Same requirement as today's manual EDGAR scans | Read via the worker's own ambient `Settings`, unchanged from today. |
| `EDGE_DART_API_KEY` / `EDGE_TRANSLATION_API_KEY` | Same requirement as today's manual DART scans | Unchanged. |
| `EDGE_EDINET_SUBSCRIPTION_KEY` | Same requirement as today's manual EDINET scans | Unchanged. |

Note: `EDGE_EDGAR_AUTO_PUBLISH_ENABLED`, if set anywhere in the worker's
environment, is **structurally ignored** by the worker — see "Safety
invariants" below.

**Ownership split, stated explicitly**: the five variables above
(`EDGE_RADAR_LIVE_SCAN_ENABLED`, `EDGE_RADAR_SCAN_INTERVAL_MINUTES`,
`EDGE_RADAR_WORKER_DB_BACKEND`, `EDGE_RADAR_WORKER_STATE_DB_URL`,
`EDGE_RADAR_WORKER_STATE_DB_PATH`) are read **only** by
`scripts/radar_worker.py`'s own process. The dashboard's own, separate,
already-existing (Phase 4B) variables — `EDGE_DB_BACKEND`,
`EDGE_STATE_DB_PATH`, `EDGE_STATE_DB_URL` — are unrelated fields read by
`get_settings()` for the dashboard's own signal/candidate/status reads,
and are never set from or coupled to the worker's own values by any code
in this phase. A structural test,
`tests/test_radar_worker_dsn_boundary.py`, proves no file under
`src/ui/`, `app.py`, or `src/data_access/container.py` ever accesses the
three worker-only `Settings` fields
(`radar_worker_db_backend`/`radar_worker_state_db_path`/
`radar_worker_state_db_url`).

## Identifier resolution stays a separate, manual step

The worker never resolves an EDGAR CIK or a DART corp code itself. An
operator runs `scripts/resolve_tracked_identifiers.py` once, manually,
against the *same* backend/DSN the worker will use, before starting the
worker — and again, occasionally, whenever a new tracked issuer is
added to `src/config/tracked_companies.py`. EDINET needs no such step:
its five tracked issuers' identifiers are hardcoded.

```bash
# EDGAR, against a Postgres target:
.venv/bin/python -m scripts.resolve_tracked_identifiers \
    --source edgar --backend postgres --dsn-env-var EDGE_RADAR_WORKER_STATE_DB_URL \
    --edgar-user-agent "EevaResearch AI contact@example.com"

# DART, against a Postgres target:
.venv/bin/python -m scripts.resolve_tracked_identifiers \
    --source dart --backend postgres --dsn-env-var EDGE_RADAR_WORKER_STATE_DB_URL \
    --dart-api-key "<key>"
```

## Starting the worker

```bash
# All configuration via environment variables (or a process manager's
# own env-injection mechanism) — there is no config file and no CLI flag.
export EDGE_RADAR_LIVE_SCAN_ENABLED=true
export EDGE_RADAR_WORKER_DB_BACKEND=postgres
export EDGE_RADAR_WORKER_STATE_DB_URL="<postgres DSN>"
export EDGE_RADAR_SCAN_INTERVAL_MINUTES=60
export EDGE_EDGAR_USER_AGENT="EevaResearch AI contact@example.com"
export EDGE_DART_API_KEY="<key>"
export EDGE_TRANSLATION_API_KEY="<key>"
export EDGE_EDINET_SUBSCRIPTION_KEY="<key>"

.venv/bin/python -m scripts.radar_worker
```

The process runs until it receives `SIGTERM`/`SIGINT` (a normal
container/orchestrator stop signal), at which point it finishes any
provider scan already in progress, then exits cleanly rather than
starting a new one.

## Safety invariants this deployment preserves

These hold regardless of hosting platform, and are proven by
`tests/test_radar_worker.py`, not just documented here:

- The worker never sets `CandidateStatus.PUBLISHED`, `MONITORING`, or
  `DISMISSED`. `record_review_decision()` (human-only) remains the sole
  route to any of those three statuses.
- **The worker always forces `edgar_auto_publish_enabled=False`** for
  every scan it performs, regardless of `EDGE_EDGAR_AUTO_PUBLISH_ENABLED`'s
  real value in its own process environment. This exists because
  `edgar_pipeline.run_pipeline()` has a separate, pre-existing
  `auto_publish_enabled` parameter (driven by that env var) which — if
  left on its ambient value — could let this worker autonomously
  publish a fact-complete 424B5 offering candidate. The worker's own
  `_build_worker_settings()` closes this off structurally, not by
  convention.
- EDGAR, DART, and EDINET are scanned independently, each inside its own
  try/except per tick. A missing credential or a network failure for one
  provider is recorded only in that provider's own scan-status row and
  never prevents the other configured providers from being attempted in
  the same round.
- The worker never resolves an issuer identifier itself (see above) —
  an issuer with no already-resolved identifier is simply skipped for
  that tick, the same behavior `scan_service.scan()` already has today.
- A non-blocking, per-(provider, backend) file lock means an overlapping
  scan attempt for the same provider is skipped for that tick, never
  queued or duplicated; the lock is released automatically by the OS if
  the worker process dies for any reason, so a crash self-heals on the
  very next tick.

## Local testing vs. a deployed Postgres worker — operator checklist

**Local testing (SQLite, single machine, explicitly not production):**

1. `export EDGE_RADAR_WORKER_DB_BACKEND=sqlite`
2. `export EDGE_RADAR_WORKER_STATE_DB_PATH=/tmp/eeva-radar-worker-test.db`
3. Run `scripts/resolve_tracked_identifiers.py` once against
   `--backend sqlite --sqlite-path /tmp/eeva-radar-worker-test.db` for
   each of `--source edgar` / `--source dart`.
4. `export EDGE_RADAR_LIVE_SCAN_ENABLED=true` and run
   `python -m scripts.radar_worker` in a foreground terminal; confirm it
   logs a scan attempt per configured provider, then `Ctrl-C` to stop it
   cleanly.
5. This SQLite file is disposable test state — never point a real
   deployed worker at it, and never treat it as a durable store.

**Deployed Postgres worker (the only supported production path):**

1. Provision (separately, later, explicitly approved) a small
   always-on/scheduled compute target and a Postgres database.
2. Set `EDGE_RADAR_WORKER_DB_BACKEND=postgres` and
   `EDGE_RADAR_WORKER_STATE_DB_URL` to that database's DSN on the
   worker's own deployment. If the dashboard is meant to read the same
   store for its read-only status display, separately set the
   dashboard's own, distinct `EDGE_DB_BACKEND=postgres`/`EDGE_STATE_DB_URL`
   (never the worker's own variable names) to the same DSN on the
   dashboard's deployment — this is two separate configuration actions
   against two separate variable names, not one shared setting.
3. Run `scripts/resolve_tracked_identifiers.py` once against
   `--backend postgres --dsn-env-var <name>` for each source, using the
   deployed database's own DSN.
4. Start `scripts/radar_worker.py` on the deployed compute target with
   `EDGE_RADAR_LIVE_SCAN_ENABLED=true`.
5. Confirm the Radar Inbox's "Continuous worker status only — not a
   candidate feed (read-only)" section (Phase 4M-0) shows each
   configured provider as healthy after its first successful tick. This
   confirms status visibility only — it does not mean worker-detected
   candidates themselves are browsable in Radar Inbox; see "Candidate
   rendering is a separate, unimplemented concern" above.
