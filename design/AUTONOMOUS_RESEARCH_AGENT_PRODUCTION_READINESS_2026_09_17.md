# Autonomous Evidence-First Research Agent — Production-Readiness Review

Status: **read-only review.** No code, package, schema, migration, configuration, test, secret, deployment setting, stash or untracked file was modified. No worker, model session, MCP server, ingestion, scan, polling, backfill or network research job was run. This report is the only file created.

Question under review: can the agent implementation at `244d4d4` be pushed and deployed **with the worker dormant and publication blocked**, and what must be true before it is ever enabled?

Review method: source reading at `HEAD` (agent paths byte-identical to `244d4d4`, verified with `git diff 244d4d4 HEAD` over every agent path), inspection of the installed `claude-agent-sdk==0.2.155` / `mcp==2.2.0` packages and the SDK's bundled CLI binary (read-only string scan), isolated `git worktree` test runs at `244d4d4` and `74a116b` (removed afterwards), an import audit with sockets and process creation blocked, and read-only inspection of local caches.

---

## 0. Verdict in one paragraph

With the worker never started, nothing in the agent can execute: every live toggle defaults off, no import path spawns a process or opens a socket, the MCP server refuses to start without its credential, and the Verified Updates page is flag-gated closed. **Two things must still be fixed before the first push**, because both concern the one part of this change that runs in production immediately — the `candidates.published_by` migration, which applies automatically on the first repository open: (1) its `NOT NULL DEFAULT 'human_reviewer'` is historically inaccurate and unused, and it becomes permanent the moment it runs on Neon; (2) the Postgres half of that change (migration V22 and the Postgres candidate repository's widened INSERT/UPDATE) has never executed against a real Postgres. A defect there would break every candidate write in production, not just the agent's. Separately, the implementation is **not** safe to *enable*: §2 and §5 list eight enablement blockers, none of which affects a dormant deployment.

---

## 1. Schema and migration compatibility

### 1.1 Exact transitions introduced

| Backend | From (deployed `origin/main` = `74a116b`) | To | Statement |
|---|---|---|---|
| SQLite (`src/data_access/state_db/schema.py`) | 19 | 20 | `ALTER TABLE candidates ADD COLUMN published_by TEXT NOT NULL DEFAULT 'human_reviewer'` |
| Postgres (`src/data_access/postgres_state_db/schema.py`) | 21 | 22 | same statement |
| JSON candidate store (`src/data_access/dart/candidate_store.py`) | — | — | `published_by` key written by `asdict()` on the next save; loader defaults a missing key to `"human_reviewer"` |

**No other schema change exists.** Every other agent and public-update store is a local file under `settings.cache_dir`, whatever `EDGE_DB_BACKEND` says: `agent_audit_events.jsonl`, `agent_research_packets.json`, `verified_updates.json`. The design's `agent_audit_events` table (§8.2) was *not* implemented. Research-case bundles go through the existing writer: `research_store` on JSON, or `get_research_case_bundle_writer` on SQLite/Postgres, into existing tables.

### 1.2 Upgrade behavior from deployed main

- **Trigger.** Migrations apply automatically inside `backend_factory._require_postgres_connection` / `_require_sqlite_connection` whenever the dashboard or the Radar worker first opens a repository. The Radar worker does this at startup (`scripts/radar_worker.py:1074`). No standalone migration CLI exists.
- **Step semantics.** Each step runs in its own transaction; on error it rolls back and raises (`schema.py` `migrate()`). The ALTER is additive. On Postgres ≥ 11, `ADD COLUMN … NOT NULL DEFAULT <constant>` is metadata-only: no table rewrite, only a brief `ACCESS EXCLUSIVE` lock.
- **Concurrency hazard (pre-existing, affects all migrations).** `migrate()` takes no advisory lock. If the dashboard and the Radar worker open repositories simultaneously on first deploy, both read version 21 and both issue the ALTER. The loser gets a duplicate-column error, rolls back, and raises, so that one repository open fails closed (empty page section or a failed tick) until the next open. **Mitigation: apply the migration once, deliberately, before restarting services (§7.3).**
- **Untested.** All 43 Postgres tests were skipped in this environment (`tests/test_state_db_postgres_schema.py` 21, `tests/test_state_db_postgres_candidate_repository.py` 22 — "Local disposable Postgres test container not reachable"). The SQLite path is verified: `published_by` round-trips through insert and optimistic-lock update. The Postgres repository edits mirror it, but a placeholder-count or column-list mistake would fail **every** Postgres candidate INSERT/UPDATE. **This is a push blocker until those 43 tests pass against a real Postgres.**

### 1.3 Rollback and read compatibility

- **Pre-agent code (`74a116b`) against a V22/V20 database: safe.** `migrate()` skips every step `≤ current` and never rejects a newer version (a no-op that returns 22). Old `SELECT *` + `row["…"]` reads ignore the extra column. Old INSERT/UPDATE statements name their columns explicitly, so the new column takes its default. The old JSON loader reads keys explicitly and ignores `published_by`.
- **Irreversible element:** the `ADD COLUMN` itself. Migrations are forward-only by convention; no down-migration exists. Leaving the column in place after a code rollback is harmless.
- **Rollback hazard that exists only after enablement:** once the agent persists `CandidateStatus.VERIFIED_DRAFT` or `INSUFFICIENT_EVIDENCE`, pre-agent code cannot parse those values.
  - SQL: `CandidateStatus(row["status"])` raises, so that source's whole candidate read fails closed and renders empty.
  - JSON: the loader skips the entry, and the next `save_candidates` **drops it from disk**.

  With the worker dormant, no such status is ever written, so rollback is clean. After enablement, move those candidates to an existing status first, or roll forward instead of back.

### 1.4 Is `published_by NOT NULL DEFAULT 'human_reviewer'` accurate and necessary?

**Not historically accurate:**
- A non-human publisher already exists. `edgar_pipeline.py:304` sets `PUBLISHED` from the deterministic shadow policy (`SignalRoute.PUBLISH`) whenever `EDGE_EDGAR_AUTO_PUBLISH_ENABLED` is on for the dashboard's manual scan path; the Radar worker forces it off. Every such row — past, and future too, since the EDGAR path never sets the field — would be labelled `human_reviewer`.
- The default also stamps `human_reviewer` onto every row that was never published (`NEEDS_REVIEW`, `NOT_MATERIAL`, `DISMISSED`, …), which is semantically meaningless.
- The three `PUBLISHED` candidates in the local caches do carry "Reviewed: …" transition details (genuinely human). The production mix is unknown from this repository.

**Not necessary:**
- The only reader and writer of the field is the agent's own `request_publication_decision` (`grep` confirms this).
- Agent provenance is already recorded immutably in four places: `VerifiedUpdate.published_by`, the packet decision, the `publication_decision_made` / `candidate_status_written` audit events, and the candidate's own appended transition detail `autonomous_agent:AUTO_PUBLISHED:…`.
- `signal_promotion` is provenance-blind by design.

**Recommendation (smallest fix, before first push):** amend V20 and V22 *in place*, which is safe only because no environment has applied them. The local `data/edge_research.db` is the unrelated legacy DB and has no `schema_version` table; the operator must confirm Neon reads `SELECT version FROM schema_version` = 21. The amendment is `ALTER TABLE candidates ADD COLUMN published_by TEXT` (nullable, no default), with `CandidateSignal.published_by: str | None = None` and the JSON loader defaulting to `None`. `NULL` then truthfully means "provenance not recorded". The agent continues to write `'autonomous_agent'`. Update the two schema-pin tests and `tests/test_models_agent_states.py`. Once V22 has run on Neon, this becomes a V23 migration plus an unrecoverable ambiguity about which historical rows were human-published.

---

## 2. Production authentication and runtime (Render)

### 2.1 What the SDK actually runs

`claude-agent-sdk==0.2.155` is a wrapper around the **Claude Code CLI**:
- The wheel bundles a native `claude` binary (`claude_agent_sdk/_bundled/claude`, 215 MB, CLI 2.1.275).
- The SDK spawns it as a subprocess (`_internal/transport/subprocess_cli.py`) and always speaks its streaming control protocol internally, so `can_use_tool`/hooks work with the string prompt `default_generator` sends.
- Binary resolution order: bundled binary → `PATH` `claude` → `~/node_modules/.bin/claude` → `~/.claude/local/claude`.
- A version probe (`claude -v`) runs before every spawn.
- **The installed wheel is platform-specific** (`Tag: py3-none-macosx_11_0_arm64`). Render needs a Linux x86_64 wheel carrying a Linux binary; this was **not verified** (no network research was permitted). **Prerequisite: confirm it (§7.2) before pushing**, because `requirements.txt` is installed by the *dashboard* build as well.

### 2.2 Dependency on developer-machine state

| Assumption | Present? | Detail |
|---|---|---|
| Claude Code CLI binary | **Yes, required** | Supplied by the wheel's bundled binary; no separate install if the Linux wheel bundles it. |
| Interactive Claude login (OAuth in `~/.claude`) | **No, if a key is supplied** | The CLI reads `ANTHROPIC_API_KEY` (also `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`, or Bedrock/Vertex via `CLAUDE_CODE_USE_BEDROCK`/`_VERTEX`) — all present in the binary. Without one it falls back to interactive login and fails. |
| `~/.claude` config / settings | **Not read** | `setting_sources=[]`, `skills=[]`, `plugins=[]`, `strict_mcp_config=True`. The CLI still **writes** session state to `CLAUDE_CONFIG_DIR` (default `~/.claude`), so that directory must be writable (ephemeral is fine). |
| Project files | **Yes, the repo checkout** | `cwd=PROJECT_ROOT`. The MCP server launches as `python -m src.mcp_agent.server` from the repo root using `sys.executable`; both are present in a Render build. |
| Model choice | **Implicit** | `model=None` means the CLI's default model. Recommend pinning explicitly for cost and behavior stability. |

### 2.3 Enablement blockers found in the runtime path (none affects a dormant deploy)

- **E1 — Secrets on the process command line.** `agent_session.build_options` sets the stdio MCP server's `env` to `{**os.environ, …, EEVA_AGENT_PRESENTED_TOKEN: token}`. The SDK serializes stdio MCP configs verbatim into `--mcp-config <json>` on the CLI's **argv** (`subprocess_cli.py:660-681`). Every secret in the worker environment — DB DSN, DART/EDINET keys, R2 keys, `ANTHROPIC_API_KEY`, the service token — would sit in `/proc/<pid>/cmdline`, visible to `ps` and to any crash or process logging.
  **Smallest correction:** put only the non-secret `EEVA_AGENT_*` scope variables in `mcp_servers[…]["env"]`, and let the MCP child receive credentials through the inherited process environment instead. The CLI already inherits the full `os.environ` (`subprocess_cli.py:812`); verify with a local, non-network test that the stdio child inherits it too. Otherwise, pass a path to a `0600` credentials file.
- **E2 — The service-token check is a configuration guard, not a trust boundary.** The expected and presented values both come from the same environment the CLI child inherits. It prevents accidental starts, not a compromised session. Document it as such.
- **E3 — Credential scope deviates from design §8.3/§8.4.** The MCP server is a child of the model-driving CLI yet holds DB write credentials and performs the publication write itself. The model has no shell or file tools (`tools=[]`, 15 built-ins denied, `dontAsk`, allowlist hook, `can_use_tool` gate), so exfiltration is not directly possible. Still, the design intended the agent identity never to hold write credentials.
  **Smallest correction:** `request_publication_decision` records "decision requested" only; the worker process evaluates `evaluate_publication_eligibility` and performs the candidate and public writes after the session ends.
- **E4 — Excerpt retrieval cannot work on a separate service.** The excerpt tools read each adapter's file cache (`*_document_excerpts.json`) and fetch through `ctx.clients`, which `server.build_context_from_env` never populates (`clients={}`). On a separate Render service the cache is empty, so every call raises inside `guarded()`, and every session ends `FAILED_RETRIEVAL` — fail-closed, but inert.
  **Smallest correction:** read the already-persisted, durable `CandidateSignal.excerpt_original` / `evidence_location` from the candidate repository. No fetch is involved, which also satisfies the design's "no agent-triggered fetch".
- **E5 — Agent stores are ephemeral and unshared.** Packets, decisions, dedup hashes, retry counts, audit events and `VerifiedUpdate`s live in local JSON files. On Render the worker's disk is ephemeral and invisible to the dashboard, so the Verified Updates page could never see a worker-written update. Redeploys also erase dedup state (risk of double publication) and audit evidence. Worse, `_validate_live_settings` accepts `EDGE_DB_BACKEND=json` in live mode.
  **Smallest correction:** move the three stores to Postgres through `backend_factory` (the design's `agent_audit_events`, plus `agent_packets` and `verified_updates` tables) and require `postgres` in live mode, as `daily_news_worker` and `company_discovery_worker` already do.

### 2.4 Process model, egress, startup

- **Process model (future).** One Render *Background Worker*, `python -m scripts.research_agent_worker`, strictly sequential: at most 2 sessions per tick; one CLI subprocess per session, which spawns one `python -m src.mcp_agent.server` stdio child. Per-session limits: `max_turns=24`, `max_budget_usd=1.00`, 180 s tool TTL. Memory is an **estimate to validate**: plan ≥ 1 GB (215 MB native CLI + Python worker + Python MCP child).
- **Egress (future).** `api.anthropic.com:443` (or the Bedrock/Vertex endpoint), plus the Postgres host. With E4 fixed, no SEC/DART/EDINET traffic is needed. Suppress non-essential CLI traffic: `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, `DISABLE_AUTOUPDATER=1`, `DISABLE_TELEMETRY=1`, `DISABLE_ERROR_REPORTING=1` (all present in the binary).
- **Startup behavior (verified).** With `EDGE_RESEARCH_AGENT_LIVE_ENABLED` off, `main()` prints "not enabled — nothing to do. Exiting." and returns 0 before any tick. Live without a token, it prints "refusing to start" and returns 2. Background workers have no HTTP health check; a dormant worker that exits 0 would be restarted in a loop by Render. **Do not create the worker service until enablement.** At enablement, add a startup self-check before the loop: CLI version probe succeeds, DB reachable, schema version ≥ 22.

---

## 3. Deployment-safe defaults (verified)

Checked in a clean environment after `.env` loading (no agent variables present) with `Settings()`:

| Setting | Env var | Default | Effect when default |
|---|---|---|---|
| `research_agent_live_enabled` | `EDGE_RESEARCH_AGENT_LIVE_ENABLED` | `False` | Worker exits 0 before any tick |
| `research_agent_service_token` | `EDGE_RESEARCH_AGENT_SERVICE_TOKEN` | `None` | Worker exits 2 in live mode; MCP server raises `ServerRefused`; `build_options` raises |
| `research_agent_publication_kill_switch_enabled` | `EDGE_RESEARCH_AGENT_PUBLICATION_KILL_SWITCH_ENABLED` | **`False` — the kill switch is NOT engaged by default** | If a session ever ran, AUTO_PUBLISHED would be possible. **Set it to `1` explicitly on every service.** |
| `verified_updates_page_enabled` | `EDGE_VERIFIED_UPDATES_PAGE_ENABLED` | `False` | Hidden route renders "not enabled"; it is also behind the app-wide Google sign-in gate |

- **MCP server refusal (verified by direct call; no server started).** `build_context_from_env` raises `ServerRefused` in all three cases: token not configured, configured but not presented, and presented but mismatched (constant-time comparison). `main()` returns 2 before building the server. **Test gap:** no test asserts `ServerRefused`; only `build_options` and the worker's refusals are tested.
- **No startup side effects (verified).** 15 modules imported with `socket.connect`, `socket.create_connection`, `subprocess.Popen`, `os.system` and `asyncio.create_subprocess_exec` replaced by raising stubs: every `src.mcp_agent` module, the worker, the policy and stores, the Verified Updates page, and the four main pages plus `ui.py`. **Zero blocked calls.** `server.main` and `worker.main` run only under `__main__`. The worker checks the live flag before `run_one_tick`. AST scope guards prove `app.py` and `src/ui` never import `src.mcp_agent`, `claude_agent_sdk`, `mcp`, or the worker. No Procfile, `render.yaml`, Dockerfile or workflow references the agent.

---

## 4. Minimal future trigger

The worker's current selection (`select_candidates`) re-scans every candidate store each tick **and cannot match real data**. It selects only `EXTRACTED`/`TRANSLATED`, but every pipeline moves an extracted candidate to its final status before persisting (`radar_pipeline.py:243-265`, `edgar_pipeline.py:300-311`, `edinet_pipeline.py:283-287`). The local caches hold none in those statuses: DART `{Needs review 13, Not material 1, Processing deferred 9, Published 1}`, EDGAR `{Needs review 1, Monitoring 1, Published 2, Dismissed 1}`. Replace it with an event-driven job queue:

- **Event.** Inside the existing Radar worker tick, right after a provider's `run_pipeline` persists a candidate whose `extraction_state == EXTRACTED` **and** `status == NEEDS_REVIEW`. That is the post-materiality "material, awaiting review" state. `NOT_MATERIAL`, `DISMISSED`, `MONITORING`, `PUBLISHED`, `PROCESSING_DEFERRED` and failed candidates never enqueue. This piggybacks on ingestion that already happens: no new polling, no new ingestion path, and no backfill, since existing candidates are never enqueued and the queue starts empty.
- **Queue.** One durable Postgres table `agent_jobs`:
  - `dedup_key TEXT PRIMARY KEY` = `"{source_name}:{rcept_no}"` — one job per filing, ever;
  - `candidate_id`, `excerpt_sha256` (for audit);
  - `status queued|running|done|quarantined`, `attempts`, `next_attempt_at`, `last_error_code`, `created_at`, `updated_at`.

  Enqueue with `INSERT … ON CONFLICT (dedup_key) DO NOTHING`; a re-extracted document is re-run only by explicit operator requeue.
- **Claim.** `SELECT … FROM agent_jobs WHERE status='queued' AND next_attempt_at <= now() ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED` on one worker instance, plus a session-level advisory lock, gives concurrency 1.
- **Retry.** Only infrastructure and retrieval failures: CLI spawn or version failure, session timeout, `no_structured_output`, `FAILED_RETRIEVAL`. Backoff 60 then 120 minutes, at most 3 attempts. After that the job becomes `quarantined`: never auto-retried, surfaced to admins, and requeued only manually. Every content outcome (`NOT_MATERIAL`, `INSUFFICIENT_EVIDENCE`, `REVIEW_REQUIRED`, `VERIFIED_DRAFT`, `DUPLICATE`, `AUTO_PUBLISHED`) is terminal: `done`, never retried.
- **Cost and concurrency caps.** Per session: `max_budget_usd=1.00`, `max_turns=24`, 20 k excerpt characters, 180 s TTL (existing). Global: at most 2 sessions per tick (existing) and a new configurable daily cap, e.g. `EDGE_RESEARCH_AGENT_MAX_SESSIONS_PER_DAY=20`, so worst-case spend is about $20/day. Hard stop when reached.

---

## 5. Public rendering and rollback

### 5.1 Publication gating (verified in `request_publication_decision.py`)

A `VerifiedUpdate` is appended only when all four conditions hold, in order:
1. The server-side matrix returns `AUTO_PUBLISHED`. The kill switch is row 0; any internal error fails closed to `REVIEW_REQUIRED`.
2. The decision is persisted to the packet store (line 126).
3. The candidate transition to `PUBLISHED` returns `"updated"` (lines 133–135). A missing candidate, version conflict or write error withholds publication.
4. The append itself succeeds (line 141).

**Residual defects (enablement blockers, not push blockers):**
- **E6 — Non-atomic sequence.** A crash after step 3 and before step 4 leaves a `PUBLISHED` candidate with no `VerifiedUpdate`. An idempotent replay returns the stored decision early and never retries the append. Add a reconciliation pass (`PUBLISHED` + `published_by='autonomous_agent'` + no update ⇒ re-append from the stored packet), or write both within one Postgres transaction once E5 is fixed.
- **E7 — Agent publications leak into the analytical Signal path.** `RadarSignalRepository` promotes every `PUBLISHED` candidate via `candidate_to_signal`, which invents `Direction.EMERGING`, a strength derived from confidence, `Horizon.MULTI_WEEK`, and a templated interpretation (`signal_promotion.py:154-161`). That is exactly the "invented judgment fields" design §9.2 forbids.
  **Smallest correction:** exclude `published_by == 'autonomous_agent'` in `RadarSignalRepository`, with a test.
- **Minor.** `_write_candidate_status` treats an `update_candidate` return of `None` as `"updated"` — dead code with today's repositories, but fail-open in principle. Make it fail closed.

### 5.2 Kill switch and rollback operations (none deletes audit evidence)

| Goal | Operation | Takes effect |
|---|---|---|
| Stop all future auto-publication | Set `EDGE_RESEARCH_AGENT_PUBLICATION_KILL_SWITCH_ENABLED=1` on the worker service | Render restarts on env change; the MCP server reads it per session, so every later decision is `REVIEW_REQUIRED` |
| Stop the agent entirely | Set `EDGE_RESEARCH_AGENT_LIVE_ENABLED=0`, or suspend the worker service | Next start exits 0 |
| Hide the public surface | Set `EDGE_VERIFIED_UPDATES_PAGE_ENABLED=0` on the dashboard | Next render |
| Code rollback | Redeploy the prior commit; the schema stays at V22/V20 (compatible, §1.3) | Safe while no `VERIFIED_DRAFT`/`INSUFFICIENT_EVIDENCE` rows exist |

Packets, research-case bundles, audit events and candidate state histories are append-only and untouched by all four operations.

### 5.3 Correcting a bad published update

- **Today:** the candidate can be moved to `DISMISSED` via `review_actions.record_review_decision`. That is script-only — **no UI or script caller exists** — and removes it from the Radar Signal path. **The `VerifiedUpdate` stays visible**, because `verified_updates.py` reads only `verified_update_store` and never consults candidate status, and the store has no retract/supersede API (`load_verified_updates` / `append_verified_updates` only). The only non-destructive lever is hiding the whole page.
- **E8 — Required before enablement:**
  1. The page renders an update only while its candidate is still `PUBLISHED` (a read-only join through `get_candidate_repository`), since status is the single source of truth.
  2. An append-only retraction/supersession record: `retraction_id`, `update_id`, `reason`, `retracted_at`, `retracted_by`, optional `superseded_by_update_id`, plus a `verified_update_retracted` audit event.
  3. The page shows "Corrected" and links to the superseding update rather than deleting anything.

  The original `VerifiedUpdate`, packet and audit trail are never edited.

---

## 6. Tests and baseline

### 6.1 Agent tests at `244d4d4` (isolated worktree)

- Agent, schema and persistence suites: **279 passed, 43 skipped, 1 failed.**
  - Suites: `test_models_agent_states`, `test_publication_policy`, `test_agent_audit_and_packet_store`, `test_mcp_agent_tools`, `test_mcp_agent_tool_scope_guard`, `test_agent_session`, `test_research_agent_worker`, `test_research_agent_worker_scope_guard`, `test_verified_updates_page`, both schema suites, both candidate-repository suites, `test_models`, `test_navigation`, `test_signal_promotion`.
  - The failure is baseline C1 below.
  - All 43 skips are the Postgres suites (see §1.2).
- **The six mandatory regression cases: 13/13 passed** (`-k case1…case6`): Wonik IPS `NOT_MATERIAL` (terminates at row 3, never drafted); CoreWeave publishes only when every direct-fact condition holds; Fabrinet/Coherent blocked by type contradiction *and* by the unavailable registry; Ciena/Marvell blocked on direction and on an unverified edge; Applied Materials/Samsung blocked for superseded/lapsed/historical/disputed/unverified edges while historical evidence is retained; the anonymous yield rumor never reaches a public or draft state.

### 6.2 The 24 baseline failures (identical at `244d4d4` and `HEAD`; root cause captured per test)

| Cluster | Tests | Root cause | Intersects agent schema / persistence / evidence packet / UI? | Blocker? |
|---|---|---|---|---|
| C1 Doc-content guards (3) | `test_backend_factory_phase2b`, `test_candidate_persistence_phase3a`, `test_state_db_candidate_repository::…phase3b0…` | `DECISIONS.md` contains the strings `data/cache` and `.streamlit/secrets.toml` | Candidate-persistence **test files**, but the assertion is about documentation text; no code path | No |
| C2 Evidence-packet import guards (2) | `test_evidence_packet_phase1::…forbidden_dependency…`, `test_evidence_packet_phase2_step2::…forbidden_imports…` | `radar_pipeline.py` and `edinet_pipeline.py` import the Daily News shadow adapters (pre-existing wiring) | Evidence-packet guard; the agent touches neither file | No |
| C3 Registry drift (7) | `test_coverage_page` ×2, `test_issuer_coverage` ×2, `test_radar_service` ×3 | Tracked-company expansion (Tier-1 cohorts) not reflected in fixture counts and seeded corp codes (123≠105, 12≠10, 21≠18; "Hanmi Semiconductor…" unresolved) | No | No |
| C4 Filings-page renders (11) | `test_radar_inbox_page::…sqlite_backed…`, `test_ui_audit_phase_c` ×2, `test_ui_audit_phase_r1` ×5, `test_ui_audit_phase_t1` ×3 | The page renders the not-configured state ("Configuration details", shell-only markdown). Inferred to be the same registry drift as C3: DART readiness reports unresolved companies | UI and SQLite candidate paths, but the failure occurs before any candidate is rendered; the agent's persistence change is verified independently by the passing SQLite repository tests | No, but it means full-render regression coverage of Filings is currently not exercising results |
| C5 Working-tree diff guard (1) | `test_ui_audit_phase_t1::…dependency_other_than_tzdata` | Compares `HEAD:requirements.txt` to the working tree, so it fails on every clean checkout (fails identically at `74a116b`) | Not affected by the agent's committed dependency additions | No |

**No baseline failure is a deployment blocker.** The push blocker is the *skipped* Postgres coverage, not any failure.

---

## 7. Deployment runbook

### 7.1 Required environment variables

**Now (push with the agent disabled) — dashboard and Radar worker services:**

| Variable | Value |
|---|---|
| `EDGE_RESEARCH_AGENT_LIVE_ENABLED` | unset (or `0`) |
| `EDGE_RESEARCH_AGENT_PUBLICATION_KILL_SWITCH_ENABLED` | **`1`** — the default is off; set it explicitly as belt-and-braces |
| `EDGE_VERIFIED_UPDATES_PAGE_ENABLED` | unset (or `0`) |
| `EDGE_RESEARCH_AGENT_SERVICE_TOKEN` | unset — do not create the secret yet |
| `ANTHROPIC_API_KEY` | **must not** be set on these services |
| Existing (`EDGE_DB_BACKEND=postgres`, `EDGE_STATE_DB_URL`, `EDGE_RADAR_WORKER_*`, source keys) | unchanged |

**Future (enablement, after E1–E8) — new `research-agent-worker` Background Worker only:** `EDGE_RESEARCH_AGENT_LIVE_ENABLED=1`; `EDGE_RESEARCH_AGENT_SERVICE_TOKEN=<≥32 random bytes>`; `EDGE_RESEARCH_AGENT_PUBLICATION_KILL_SWITCH_ENABLED=1` initially; `ANTHROPIC_API_KEY=<prod key>` (or the Bedrock/Vertex variables); `EDGE_DB_BACKEND=postgres`; `EDGE_STATE_DB_URL=<dsn>`; `CLAUDE_CONFIG_DIR=/tmp/claude-config`; `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`; `DISABLE_AUTOUPDATER=1`; `DISABLE_TELEMETRY=1`; `DISABLE_ERROR_REPORTING=1`; a writable `HOME`; the daily session cap from §4.

### 7.2 Deployment prerequisites (before push)

1. **Fix F1:** make `published_by` nullable with no default in V20/V22, the dataclass and the JSON loader (§1.4), with tests updated.
2. **Verify F2:** run the Postgres suites against the disposable test Postgres (`127.0.0.1:55432`, db `eevaresearch_test_phase4b`), with **0 skipped, 0 failed**:
   ```bash
   EEVARESEARCH_PG_TEST_PASSWORD=<test-password> .venv/bin/python3 -m pytest tests/test_state_db_postgres_schema.py tests/test_state_db_postgres_candidate_repository.py -q
   ```
3. **Linux wheel check:** the Render build must find a Linux wheel.
   ```bash
   pip download claude-agent-sdk==0.2.155 --no-deps --only-binary=:all: --platform manylinux_2_17_x86_64 --python-version 3.12 -d /tmp/wheelcheck
   ```
   If none exists, or the dashboard build size or time is unacceptable (215 MB binary), move `claude-agent-sdk` and `mcp` into a separate `requirements-agent.txt` (`-r requirements.txt` plus the two pins) used only by the future worker service and CI. The scope guards prove the dashboard never imports them.
4. **Choose the push scope explicitly.** `git push origin main` ships 11 commits: the 6 agent commits plus 4 redesign commits (`60a1652`…`c37745b`) and the F1 fix. The redesign was validated locally but is outside this review. For agent-only, push a branch cut from `244d4d4` plus the F1 commit.
5. Confirm the Neon schema version and snapshot the database (§7.3).

### 7.3 Migration commands (operator, production DSN)

```bash
psql "$EDGE_STATE_DB_URL" -c "SELECT version FROM schema_version;"
```
Expect `21`. Stop if it reads anything else. Take a Neon branch or snapshot, rehearse on the branch first, then apply once, from one process, before restarting services:
```bash
EDGE_STATE_DB_URL="<dsn>" .venv/bin/python3 -c "import os; from src.data_access.postgres_state_db import connection, schema; print(schema.migrate(connection.connect(os.environ['EDGE_STATE_DB_URL'])))"
```
Expect `22`. Then verify the column; after F1, expect `is_nullable=YES` and no default:
```bash
psql "$EDGE_STATE_DB_URL" -c "SELECT column_name, is_nullable, column_default FROM information_schema.columns WHERE table_name='candidates' AND column_name='published_by';"
```

### 7.4 Startup checks after deploy

- Dashboard boots; Filings / Signals / Dashboard / Research Theses render; `/verified-updates` shows "Verified Updates is not enabled in this environment."
- Radar worker completes one tick with no migration error in its logs; `SELECT version FROM schema_version` = 22.
- No `research-agent-worker` service exists. No process named `claude` or `src.mcp_agent.server` is running on any service.
- Candidate writes work: the next Radar tick persists new candidates without errors.

### 7.5 Rollback steps (push-level)

1. Redeploy `74a116b` (or the prior deployed commit) on the dashboard and Radar worker. Schema V22 is read-compatible (§1.3); leave the column in place.
2. Only if a code defect in the Postgres candidate repository is suspected: restore the Neon snapshot taken in §7.3, which discards writes since then — prefer rolling forward with a fix.
3. Agent-level rollback is not needed while dormant. For post-enablement rollback, see §5.2.

---

## 8. Remaining risks

| Risk | Applies to | Mitigation |
|---|---|---|
| Postgres V22 / repository defect breaks all candidate writes | Push | F2 test gate plus rehearsal on a Neon branch |
| Migration race between dashboard and Radar worker on first open | Push | Apply once, deliberately (§7.3) |
| Dashboard build fails or bloats on the 215 MB platform-specific SDK wheel | Push | §7.2 item 3 |
| Redesign commits ship unintentionally with a plain push | Push | §7.2 item 4 |
| Kill switch off by default | Push (latent) | Set `…KILL_SWITCH_ENABLED=1` everywhere |
| E1–E8: argv secrets, trust boundary, credential scope, excerpt path, ephemeral stores, non-atomic publish, Signal leakage, no retraction | Enablement | Fix all before enabling; §2.3 and §5 |
| Worker eligibility can never match persisted candidates | Enablement | §4 trigger |
| No exact-quote verification: a hallucinated but well-formed "direct fact" citing a real evidence id can pass all ten rows (design §14) | Enablement | Keep the kill switch ON (every decision `REVIEW_REQUIRED`) until substring verification against the stored excerpt exists |
| No `ServerRefused` test; no UI caller for `record_review_decision` (`VERIFIED_DRAFT` approval and unpublish are script-only) | Enablement | Add the test; add an admin action |
| New statuses unreadable by pre-agent code | Post-enablement rollback | §1.3 |

---

## 9. Go / no-go checklist

**Push (agent disabled):**
- [ ] F1 — `published_by` nullable, no default, before V20/V22 first run anywhere
- [ ] F2 — Postgres schema and candidate-repository suites pass against a real Postgres (0 skipped)
- [ ] Linux x86_64 wheel for `claude-agent-sdk==0.2.155` confirmed, or agent deps split out of the dashboard requirements
- [ ] Push scope decided (agent-only branch vs. full `main` including the redesign)
- [ ] Neon at schema 21 confirmed, snapshot taken, migration rehearsed on a branch, applied once
- [ ] `EDGE_RESEARCH_AGENT_PUBLICATION_KILL_SWITCH_ENABLED=1` set; `EDGE_RESEARCH_AGENT_LIVE_ENABLED` and `EDGE_VERIFIED_UPDATES_PAGE_ENABLED` unset; no `ANTHROPIC_API_KEY` and no agent token on any service; no agent worker service created
- [x] All toggles default off except the kill switch (§3) — verified
- [x] MCP server refuses without its token — verified by direct call
- [x] No import or startup path spawns a process or opens a socket — verified
- [x] Agent suites and the six regression cases pass at `244d4d4` — verified
- [x] No baseline failure intersects the agent's change in a way that blocks deploy — verified

**Enable (later, separate approval):** E1–E8 fixed; §4 trigger with durable `agent_jobs`; durable Postgres agent stores; page filtered by live candidate status with retraction records; exact-quote verification; kill switch kept ON through a supervised trial.

---

READY_TO_PUSH_AFTER_SPECIFIC_FIXES
