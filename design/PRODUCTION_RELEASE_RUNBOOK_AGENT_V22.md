# Production Release Runbook — Agent-Disabled Push with Postgres V22

Scope: releasing `main` from the deployed `74a116b` to `20f7c34` (or a
later commit that contains the same agent code), with the autonomous
research agent **disabled**. The only production-affecting change in
that range besides UI is Postgres schema step **V22**:

```sql
ALTER TABLE candidates ADD COLUMN published_by TEXT   -- nullable, no default
```

Nothing in this runbook is performed by an agent session. Every step
marked **(operator)** needs a named human, and every step marked
**(approval)** needs explicit sign-off before it starts. Credentials are
never pasted into chat, commit messages, tickets, or logs.

---

## 1. Established facts (verified 2026-09-18)

| Fact | Evidence |
|---|---|
| All three main services — `eevaresearch-ai` (web), `eevaresearch-radar-worker`, `eevaresearch-daily-news-worker` — use the **same** Render PostgreSQL database: service `eevaresearch-radar-db`, database `eevaresearch`, port 5432. The Radar worker uses the internal hostname; web and Daily News use the external one. | Operator comparison of parsed host/port/database only (no credential exposure). |
| `eevaresearch-radar-db` lives in Render "My project → Production", PostgreSQL 18, Ohio. It offers 3-day point-in-time recovery and on-demand logical export. | Render dashboard (read-only). |
| `eeva-radar-beta-db` is a **different**, non-production instance. None of the three main services writes to it. | Its logs showed no application connections and 0-buffer checkpoints across Radar and Daily News ticks. |
| All three main services track branch `main` with Auto-Deploy "On Commit" and **no Pre-Deploy command**. One push redeploys all three **concurrently**. | Render service settings (read-only). |
| `eevaresearch-company-discovery` tracks a different branch (`feat/issuer-registry-foundation`) and is not redeployed by a push to `main`. | Render service settings. |
| Each service applies migrations implicitly: every Postgres repository open calls `postgres_schema.migrate(conn)` — `backend_factory._require_postgres_connection` (web and Radar worker; the Radar worker at startup and every tick) and `daily_news_backend._require_postgres_connection` (Daily News worker). | Source: `src/data_access/backend_factory.py`, `src/data_access/daily_news/daily_news_backend.py`. |
| `migrate()` takes **no advisory lock** and sets **no lock timeout**. Concurrent first opens race: the loser gets a duplicate-column error, rolls back, and fails that repository open. | Source: `src/data_access/postgres_state_db/schema.py`. |
| Deployed code (`74a116b`) leaves read connections **idle in transaction** until they are garbage-collected. Such sessions hold `AccessShareLock` and block `ALTER TABLE candidates`; while the ALTER waits, every new candidate query queues behind it. Production's active-connection graph shows a matching sawtooth (≈5 → ≈19, then sharp drops). | Reproduced locally against disposable Postgres; production metric graph. |
| `74a116b` is **read/write compatible with a V22 schema**: `migrate()` is a no-op at 22, reads ignore the extra column, and its INSERT/UPDATE statements name columns explicitly (new rows get NULL). | Cross-version test against disposable Postgres (74a116b@v21 → HEAD@v22 → 74a116b → HEAD). |
| V22 statement is nullable with no default, so it is a metadata-only change on Postgres: no rewrite, no backfill. Every historical row reads `published_by = NULL`. | `src/data_access/postgres_state_db/schema.py` `_V22_STATEMENTS`; tests in `tests/test_state_db_postgres_schema.py`. |

## 2. Rules

1. **V22 is applied exactly once**, deliberately, **schema-first** — before
   any service runs code whose `CURRENT_SCHEMA_VERSION` is 22. Never let the
   auto-deploy race apply it.
2. **Workers are suspended and database connections drained** before V22.
3. The migration session uses a **bounded lock timeout** (`lock_timeout = 5s`).
   On timeout: stop, re-drain, re-check. Never retry in a blind loop.
4. A **recovery point or logical export** exists before V22 is applied.
5. Post-migration: `schema_version = 22` and `candidates.published_by` is
   `is_nullable = YES` with `column_default IS NULL`.
6. `EDGE_RESEARCH_AGENT_PUBLICATION_KILL_SWITCH_ENABLED=1` is set on **all three**
   main services **before** any deployment containing agent code.
7. These stay **unset or disabled** on every service:
   `EDGE_RESEARCH_AGENT_LIVE_ENABLED`, `EDGE_VERIFIED_UPDATES_PAGE_ENABLED`,
   `EDGE_RESEARCH_AGENT_SERVICE_TOKEN`, and any Anthropic/Claude credential
   (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`, Bedrock/
   Vertex agent credentials). No research-agent worker service exists.
8. The dashboard and workers install `requirements.txt` only; the agent runtime
   (`claude-agent-sdk`, `mcp`) lives in `requirements-agent.txt` and is never added
   to a Render build.

## 2a. Status as of 2026-09-18 (read-only observation)

- `EDGE_RESEARCH_AGENT_PUBLICATION_KILL_SWITCH_ENABLED` is **not set** on any of
  the three main services — §4.1 is still required.
- `EDGE_RESEARCH_AGENT_LIVE_ENABLED`, `EDGE_VERIFIED_UPDATES_PAGE_ENABLED`,
  `EDGE_RESEARCH_AGENT_SERVICE_TOKEN`, and every Anthropic/Claude credential are
  absent from all three main services; none uses an environment group or
  secret file.
- The production read-only SQL pre-flight (§4.0 step 3) has **not** been run:
  `schema_version`, the absence of `candidates.published_by`, and the live
  session/lock picture are unobserved.

## 3. Open items to close before the release window

- [ ] Confirm whether `eevaresearch-company-discovery` points at the same
      production database (`EDGE_COMPANY_DISCOVERY_WORKER_STATE_DB_URL`). If it
      does, include it in the suspend/resume steps below.
- [ ] Decide the push scope: `main` ships the agent commits **and** the four
      redesign-v2 commits (`60a1652`…`c37745b`).
- [ ] Pick a low-traffic window; name the operator and the approver.

## 4. Procedure

### 4.0 Pre-flight (operator, read-only)
1. Local: `git status` clean at the release commit; `git diff --check` clean;
   agent-isolation, schema, and repository suites pass
   (`tests/test_agent_dependency_isolation.py`, `tests/test_state_db_schema.py`,
   `tests/test_state_db_postgres_schema.py`, `tests/test_state_db_postgres_candidate_repository.py`
   against a disposable Postgres with **0 skipped**).
2. Render: all three main services Live on `74a116b`; env keys match §2 rule 7.
3. Production database, read-only session (`BEGIN READ ONLY`,
   `statement_timeout = 5s`), never through application code:
   ```sql
   SELECT version FROM schema_version;                         -- must be 21
   SELECT table_schema, column_name, is_nullable, column_default
     FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'candidates'
      AND column_name = 'published_by';                        -- must be 0 rows
   SELECT pid, usename, client_addr, state, wait_event_type,
          now() - xact_start AS xact_age, now() - state_change AS state_age,
          left(query, 60) AS query
     FROM pg_stat_activity
    WHERE datname = current_database() AND pid <> pg_backend_pid()
    ORDER BY xact_start NULLS LAST;
   SELECT l.pid, l.mode, l.granted
     FROM pg_locks l JOIN pg_class c ON c.oid = l.relation
    WHERE c.relname = 'candidates';
   ```
   **Stop** if the version is not 21 or the column already exists.

### 4.1 Kill switch first (operator, approval)
Set `EDGE_RESEARCH_AGENT_PUBLICATION_KILL_SWITCH_ENABLED=1` on web, Radar worker,
and Daily News worker. Saving an env var redeploys the service on `74a116b`,
which ignores the variable — harmless. Wait until all three are Live again.

### 4.2 Recovery point (operator)
Record the UTC timestamp for point-in-time recovery **and** run
Recovery → "Create export" on `eevaresearch-radar-db`; wait for the export to
complete.

### 4.3 Quiesce (operator, approval)
1. Suspend `eevaresearch-radar-worker` and `eevaresearch-daily-news-worker`
   (and `eevaresearch-company-discovery` if §3 says it shares the database).
   Wait until no tick is in progress (worker logs show the last tick completed).
2. Drain the web service's leaked sessions: restart `eevaresearch-ai`
   (optionally with Maintenance Mode on for the window).
3. Re-run the `pg_stat_activity` / `pg_locks` queries. Proceed only when no
   application session holds a lock on `candidates` and none is
   `idle in transaction`. Terminating a straggler with `pg_terminate_backend`
   is a separate, explicitly approved action.

### 4.4 Apply V22 exactly once (operator, approval)
From one process, using the release commit's code, with a bounded lock
timeout and a hard version guard:
```bash
PGOPTIONS='-c lock_timeout=5s -c statement_timeout=60s' \
  EEVA_PROD_DB_URL="<from the operator's secret store; never echoed>" \
  .venv/bin/python3 -c "
import os
from src.data_access.postgres_state_db import connection, schema
conn = connection.connect(os.environ['EEVA_PROD_DB_URL'])
before = schema.get_schema_version(conn)
assert before == 21, f'expected 21, found {before}'
print('migrated to', schema.migrate(conn))
"
```
Expected output: `migrated to 22`. On `lock_timeout`, the step rolls back
cleanly — return to §4.3 step 3.

### 4.5 Verify (operator, read-only)
```sql
SELECT version FROM schema_version;                            -- 22
SELECT is_nullable, column_default
  FROM information_schema.columns
 WHERE table_schema = 'public' AND table_name = 'candidates'
   AND column_name = 'published_by';                           -- YES, NULL
SELECT count(*) FILTER (WHERE published_by IS NOT NULL) FROM candidates;  -- 0
```

### 4.6 Deploy (operator, approval)
1. Push the release commit to `main` (normal push, no force). The web service
   auto-deploys; its first repository open finds version 22 and `migrate()` is
   a no-op.
2. Resume the suspended workers. Confirm each is Live on the release commit;
   if a worker still shows `74a116b`, trigger a manual deploy of the latest
   commit.

### 4.7 Post-deploy checks
- Web: Dashboard, Filings, Signals, Research Theses render; `/verified-updates`
  shows "not enabled".
- Radar and Daily News workers: one full tick each with no migration or
  connection errors.
- Database: `schema_version = 22`; new candidates persist with
  `published_by = NULL`.
- No `research-agent-worker` service, no `claude` / `src.mcp_agent.server`
  process, no agent credential on any service.

## 5. Rollback

- **Code:** redeploy `74a116b` on the affected services. V22 stays in place —
  `74a116b` is compatible with it. Do not attempt a down-migration.
- **Data:** only for a suspected data-corrupting defect: restore from the
  §4.2 recovery point into a new instance and repoint services — this discards
  writes made since the recovery point. Prefer rolling forward.
- **Migration race observed anyway** (a service logs a duplicate-column
  error): harmless after V22 exists; restart the affected service.
