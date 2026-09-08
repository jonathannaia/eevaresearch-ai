# Company Discovery Worker Deployment — Phase 2 (Passive Candidate Ledger)

This document describes how `scripts/company_discovery_worker.py` (the
standalone, continuous Candidate Ledger worker introduced in Phase 2)
is meant to be deployed, and what it deliberately does **not** do. It
is a documentation-only artifact — no hosting platform is provisioned,
configured, or deployed as part of Phase 2. Actually enabling live,
continuous discovery against a deployed worker is a separate, later,
explicitly approved action, exactly like `design/RADAR_WORKER_
DEPLOYMENT.md`'s own posture for the Radar worker.

## What this worker does, and does not do

It reads already-persisted `FilingEvent`, `CandidateSignal`, and
`NewsStory` records — nothing it has not already stored — extracts
organization mentions using deterministic pattern rules, resolves them
against Core companies/Discovery stubs/known Candidate aliases, scores
them, and persists the result as Candidate Ledger rows. It never
fetches anything from the network, never calls a translation provider,
and — critically — **there is no promotion path**: nothing this worker
does ever writes to `TrackedCompany`, `SEED_ISSUERS`, `DISCOVERY_
STUBS`, or any live filing/news-monitoring configuration. A Candidate
stays a Candidate (or moves to Archived/Rejected/Quarantined) until a
later, separately-approved phase adds a real promotion mechanism.

## Why Postgres is required for a real deployed worker

Same reasoning as the Radar and Daily News workers: this worker and the
dashboard/Radar/Daily News workers are separate processes with no
shared memory. For this worker to read their already-persisted Filing/
News data, and for its own Candidate Ledger tables to be inspectable by
the internal admin page, all of these must point at the *same* durable
Postgres database — a deployment-time operational choice (setting this
worker's own `EDGE_COMPANY_DISCOVERY_WORKER_STATE_DB_URL` to the
identical DSN the dashboard's `EDGE_STATE_DB_URL` already uses), never
something this worker's code decides on its own.

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `EDGE_COMPANY_DISCOVERY_LIVE_ENABLED` | Yes (master switch) | `true`/`1`/`yes`/`on` to let the worker do anything at all. Absent or any other value: the worker prints a message and exits `0` immediately. |
| `EDGE_COMPANY_DISCOVERY_WORKER_DB_BACKEND` | Yes | Must be exactly `postgres`. Anything else (including `sqlite`, `json`, blank, or unset) is a fatal, sanitized startup error — stricter than the Radar worker, matching the Daily News worker's own posture. |
| `EDGE_COMPANY_DISCOVERY_WORKER_STATE_DB_URL` | Yes for live mode | This worker's own Postgres DSN — deliberately separate from the dashboard's `EDGE_STATE_DB_URL` and from Radar's/Daily News's own worker DSNs, so a misconfiguration in one can never silently point another at the wrong database. Should be set to the *same physical database* the dashboard/Radar/Daily News already use, so this worker can actually read their data. |
| `EDGE_COMPANY_DISCOVERY_WORKER_STATE_DB_PATH` | Local/test only | SQLite path — never accepted by the worker's own live-mode gate (Postgres only); exists only for a direct local test run or the admin page's own local SQLite read path. |
| `EDGE_COMPANY_DISCOVERY_SCAN_INTERVAL_MINUTES` | No (default `240`) | Minutes between ticks. Floored to a 60-second minimum regardless of the configured value, as defense-in-depth. |
| `EDGE_COMPANY_DISCOVERY_STALE_DAYS` | No (default `180`) | A Candidate with no new evidence for this many days is moved to Archived on the next tick. |
| `EDGE_COMPANY_DISCOVERY_ADMIN_ENABLED` | No (default off) | Gates the hidden, read-only admin page — separate from the worker's own live-scan flag. |

## Ownership split, stated explicitly

The worker's own `EDGE_COMPANY_DISCOVERY_WORKER_*` variables are read
only by `scripts/company_discovery_worker.py` and `scripts/backfill_
company_discovery.py` — never by `get_settings()`'s own ambient use in
`app.py`/the dashboard. The dashboard never reads this worker's master
switch to decide anything, and this worker never reads the dashboard's
own `EDGE_DB_BACKEND`/`EDGE_STATE_DB_URL`.

## Starting the worker

```
export EDGE_COMPANY_DISCOVERY_WORKER_DB_BACKEND=postgres
export EDGE_COMPANY_DISCOVERY_WORKER_STATE_DB_URL=<same DSN the dashboard uses>
export EDGE_COMPANY_DISCOVERY_LIVE_ENABLED=true
.venv/bin/python -m scripts.company_discovery_worker
```

SIGTERM/SIGINT are handled gracefully — an in-progress tick is never
interrupted mid-call, but no new tick starts once a shutdown signal is
received.

## The one-shot backfill

Before (or shortly after) first enabling the worker, an operator may
run the bounded, idempotent backfill once to process existing Filing/
Daily News history rather than only new records going forward:

```
.venv/bin/python -m scripts.backfill_company_discovery
```

Safe to re-run — every evidence row's dedup key is deterministic, so a
repeat run skips everything already recorded. It never runs the
staleness/decay pass and never touches `TrackedCompany`/`SEED_ISSUERS`.

## Safety invariants this deployment preserves

- No network I/O of any kind — proven by `tests/test_company_discovery_
  scope_guard.py`'s AST-based import checks, not just documented.
- No promotion path — proven by the same scope-guard suite: no function
  in this worker or the pipeline it calls ever writes `TrackedCompany`-
  shaped data or any live-monitoring configuration.
- Disabled by default, fails closed unless its own Postgres backend and
  DSN are explicitly supplied — `tests/test_company_discovery_worker.py`.
- A Candidate is never created without its first evidence row in the
  same repository operation — `tests/test_company_discovery_pipeline.py`.
- The admin page is hidden, separately flag-gated, and contains no
  button, form, or state-changing control of any kind — `tests/
  test_company_discovery_admin_page.py`.

## Local testing vs. a deployed Postgres worker — operator checklist

**Local testing (SQLite, single machine, explicitly not production):**

1. `export EDGE_COMPANY_DISCOVERY_WORKER_DB_BACKEND=sqlite`
2. `export EDGE_COMPANY_DISCOVERY_WORKER_STATE_DB_PATH=/tmp/eeva-company-discovery-test.db`
3. `export EDGE_COMPANY_DISCOVERY_LIVE_ENABLED=true` and run
   `python -m scripts.company_discovery_worker` in a foreground
   terminal; confirm it logs a tick, then `Ctrl-C` to stop it cleanly.
4. This SQLite file is disposable test state — never point a real
   deployed worker at it.

**Deployed Postgres worker (the only supported production path):**

1. Provision (separately, later, explicitly approved) a small
   always-on/scheduled compute target — this worker needs no more
   resource than the existing Daily News worker.
2. Set `EDGE_COMPANY_DISCOVERY_WORKER_DB_BACKEND=postgres` and
   `EDGE_COMPANY_DISCOVERY_WORKER_STATE_DB_URL` to the *same* DSN the
   dashboard's own `EDGE_STATE_DB_URL` already points at — this worker
   only ever reads existing Filing/Daily News data, it never needs a
   database of its own.
3. Start `scripts/company_discovery_worker.py` with `EDGE_COMPANY_
   DISCOVERY_LIVE_ENABLED=true`.
4. Optionally run the one-shot backfill once against the same DSN.
5. Enable `EDGE_COMPANY_DISCOVERY_ADMIN_ENABLED=true` on the dashboard
   deployment to review the Candidate Ledger at `/company-discovery-admin`.
