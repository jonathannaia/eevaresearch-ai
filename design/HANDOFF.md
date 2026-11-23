# EevaResearch — Project Handoff

Written 2026-11-19 from the repository at `/Users/naia/edge_research_agent`
(`main` at `e827320`). Everything below
is drawn from the code, config, design docs and git history. Items marked
**(inferred)** are my reading, not something the repo states outright.

> **Read this first:** `README.md` and `IMPLEMENTATION_NOTES.md` are badly out
> of date. They describe a "foundation phase" with demo/mock data, no live
> ingestion, no LLM wiring and 66 tests. The repo actually has ~6,900 tests,
> live filing ingestion from three regulators, a Postgres backend at schema
> version 25, four deployed Render services and a 129-company tracked universe.
> Trust the code and `design/DECISIONS.md` (5,049 lines), not the README.

---

## 1. What the tool is

EevaResearch is an **evidence-first thematic market-intelligence platform** for
frontier-technology sectors. It autonomously ingests regulatory filings from
three jurisdictions (US SEC EDGAR, Korea OpenDART, Japan EDINET) plus curated
news feeds, detects material disclosures against a typed theme taxonomy,
extracts and translates supporting text, and surfaces them as reviewable
signals, research cases and a daily news digest. Its defining constraint is
epistemic discipline: every material claim is labelled **Fact, Interpretation,
Inference or Uncertainty**, the product never issues buy/sell/hold advice or
price targets, and autonomous publication is gated behind deterministic
evidence policies. The audience **(inferred, from the private-beta Google
sign-in, admin pages and allowlist machinery)** is a small set of
analyst/investor users in an invite-only beta, with the operator (the repo
owner) as the primary reviewer.

## 2. The end goal

- A continuously-running research surface where **autonomous workers do the
  discovery** and a human reviews only what survives the policy gates.
- **Full disclosure-to-signal coverage** across US/Korea/Japan for a curated
  universe of frontier-tech issuers, with original-language text preserved and
  machine translation clearly labelled.
- **Auditable claims**: every published item traces to an exact source excerpt,
  verified by quote-matching, with the filing link and retrieval timestamp.
- **Research cases** — multi-filing, evidence-backed theses — assembled and
  validated structurally before a human sees them.
- An **autonomous research agent** that proposes and drafts cases under a
  kill-switch, write-guard and publication policy (built, dormant).
- **(inferred)** A read-only API for partners/pilot users is an emerging goal:
  a complete private-pilot filing API exists on an unmerged branch.

## 3. Tech stack

| Layer | Choice |
|---|---|
| Language | Python 3.12 (`runtime.txt`: `python-3.12`) |
| UI | Streamlit (`>=1.38,<2.0`), 19 page modules via `st.navigation` / `st.Page` |
| Database | PostgreSQL 18 in production; SQLite and JSON backends also implemented |
| DB driver | `psycopg[binary]` 3.x |
| Hosting | Render — 7 services + 2 Postgres datastores |
| Auth | Google OAuth via Streamlit's native `st.user` / `st.login("google")`, backed by Authlib; plus an email allowlist gate (`src/ui/beta_gate.py`) |
| External APIs | SEC EDGAR, OpenDART (Korea), EDINET (Japan), DeepL (translation), RSS/Atom feeds via `feedparser` |
| Object storage | Cloudflare R2 via `boto3` — **dormant**, nothing imports it at runtime |
| Agent runtime | `claude-agent-sdk` + `mcp`, isolated in `requirements-agent.txt`, disabled by default |
| **Payments** | **None.** No Stripe/Paddle/LemonSqueezy or any billing code exists. |

## 4. Architecture

### Folders

| Path | Role |
|---|---|
| `app.py` (350 lines) | Entry point: page registry, Google sign-in, beta gate, chrome |
| `src/models/` | 14 typed dataclass modules — `FilingEvent`, `CandidateSignal`, `Theme`, `EvidenceItem`, research cases, daily news, user accounts |
| `src/config/` | `settings.py` (31 env vars), `tracked_companies.py` (129 issuers) |
| `src/data_access/edgar\|dart\|edinet/` | Per-regulator client, scan service, rules, document extractor, pipeline |
| `src/data_access/daily_news/` | Feed registry, URL quality gate, canonicalisation, dedup, materiality tiering |
| `src/data_access/company_discovery/` | Passive candidate ledger for universe expansion |
| `src/data_access/state_db/` | SQLite backend, schema **v23** |
| `src/data_access/postgres_state_db/` | Postgres backend, schema **v25** (68 DDL statements) — deliberately no shared dialect abstraction |
| `src/data_access/backend_factory.py` | Backend selection seam (`json` / `sqlite` / `postgres`) |
| `src/logic/` | Pure, Streamlit-free policy: signal routing, publication policy, quote verification, research-case validation, agent write-guard |
| `src/ui/pages/` | 19 page modules: Dashboard, Signals, Radar Inbox, Daily News, Research Cases, Coverage, Theme Workspace, admin pages |
| `src/mcp_agent/` | Autonomous research agent session + MCP tool server |
| `scripts/` | 28 scripts, incl. 4 worker entry points and `render_start.sh` |
| `design/` | 18 tracked design docs (+~30 uncommitted) and `DECISIONS.md`, the real architectural record |
| `tests/` | 298 test files |

### Data flow

```
Regulator API (EDGAR / DART / EDINET)
  └─ scan_service      → FilingEvent + CandidateSignal (CANDIDATE_DETECTED)
      └─ *_rules       → keyword/category match → confidence High/Moderate/Low
          └─ pipeline  → budget-bounded: only above-threshold candidates proceed
              ├─ document_service  → fetch + extract excerpt (ExtractionState)
              ├─ translation_svc   → DeepL, labelled (TranslationState)
              ├─ materiality gates → low-value/boilerplate filtering
              └─ signal_decision_policy → PUBLISH / TIMELINE / REVIEW / ARCHIVE
                  └─ state_db / postgres_state_db  (candidates ⋈ filing_events)
                      └─ Streamlit pages: Signals, Radar Inbox, Dashboard
                          └─ Research Cases → validation → publication_policy
```

Key properties: the pipeline is **bounded and idempotent** (a deferred candidate
is picked up next run, never dropped); **the dashboard never scans on page
render** — all scanning is in standalone workers; `rcept_dt` is the
authoritative filing date on every source.

## 5. What's done

- **Three-regulator live ingestion** (EDGAR, DART, EDINET) with per-source
  rules, retry policy and document extraction, running autonomously on Render.
- **Durable state** on Postgres (v25) and SQLite (v23), with a backend factory
  seam and an independently-implemented Postgres repository package.
- **Daily News** pipeline: curated feeds, URL quality gate, canonicalisation,
  dedup, extractive-only grounding, materiality tiering, own worker + admin page.
- **Theme registry**: typed, fail-closed YAML taxonomy, 13 themes, with
  matching policy, entity roles, geographies and an output contract.
- **129 tracked companies** (90 EDGAR, 27 EDINET, 12 DART).
- **Research cases**: structural validation, theme matching, rejection reasons,
  lead factory/orchestration/selection.
- **Publication policy + quote verification**: exact-excerpt support checking
  before anything is published.
- **Google OAuth sign-in**, email allowlist beta gate, admin user management.
- **Company Discovery** passive candidate ledger with its own worker.
- **~6,900 tests** (6,615 passing on a clean checkout), including diff-scope guards that fail if a change
  touches files outside an approved set.
- **Extensive perf work** recently: dashboard staged timing, batched loads,
  cached source reads, deferred materialisation.

## 6. In progress or partial

**There are no `TODO`/`FIXME`/`XXX`/`HACK` comments anywhere in `src/`,
`scripts/` or `app.py` — zero.** Incomplete work is tracked in `design/` docs
and module docstrings instead, and in a large number of unmerged branches.

- **Autonomous Research Agent** — built but **dormant by default**
  (`EDGE_RESEARCH_AGENT_MODE`, kill-switch, shadow write-guard). Its Render
  service is **suspended**. Runtime deps are isolated in
  `requirements-agent.txt`; the SDK's Linux wheel has never been verified on
  Render's runtime.
- **Private-pilot read-only filing API** — complete on the unmerged branch
  `feat/filing-read-api` (worktree `/Users/naia/eeva-wt-readapi`, 4 commits,
  **unpushed**): bearer-token auth by SHA-256 digest, three projection
  policies, SQLite + Postgres adapters, gunicorn hosting layer, rate limiting,
  readiness gate. Verified locally end-to-end; **never deployed**.
- **LLM filing-comparison experiment** — parked on `feat/llm-filing-comparison`
  (worktree `/Users/naia/eeva-wt-llmcompare`). A synthetic-only smoke test ran;
  real-filing use is not cleared.
- **R2 remote cache** — code exists, explicitly dormant, nothing imports it.
- **~12 active worktrees and dozens of unmerged branches** covering daily-news
  source expansion, EDGAR 20-F/6-K foreign issuers, EDINET usability, summary
  quality and product-truthfulness fixes.
- **30 untracked design docs** sitting uncommitted in `design/`.

## 7. Known issues

- **README and IMPLEMENTATION_NOTES are factually wrong** about the product's
  current state — the single most misleading thing in the repo for a newcomer.
- **Documentation drift is a recurring pattern** (not only the README):
  `signal_decision_policy.py`'s docstring carries a correction of its own
  earlier false claim about "shadow mode", and a readiness warning elsewhere
  claimed a feature was unimplemented after it had been implemented.
- **12 tests fail on a clean checkout** of `main` (`e827320`):
  `test_coverage_page` (2), `test_issuer_coverage` (2),
  `test_radar_public_read_only`, `test_ui_audit_phase_r1/t1`,
  `test_evidence_packet_phase1/phase2_step2`, and three
  `*_files_never_reference_real_local_state_*` guards. Long-standing, not new
  regressions. The set is base-dependent — 11 at `f803dca` — so measure rather
  than cite.
- **Diff-scope guard tests fail on any uncommitted tracked change** (~18–20 of
  them). They clear on commit, but they make a dirty working tree look broken.
- **`eevaresearch-company-discovery` deploys from a feature branch**
  (`feat/issuer-registry-foundation`), not `main` — a merge to `main` does not
  update it. `eeva-review-64d82127ad` likewise runs `review/ui-3d1e4a870f`.
- **Two Postgres datastores exist but only one is used.** All four live
  services point at the same database; the second (`eeva-radar-beta-db`) is
  referenced by nothing.
- **Four independently-settable DSN variables all point at the same database**,
  so changing one silently repoints only that service. The radar worker's
  generic `EDGE_DB_BACKEND`/`EDGE_STATE_DB_URL` are **dead configuration** —
  its `_build_worker_settings()` overrides them.
- **Three of four services connect over the external database hostname**
  rather than Render's internal one **(inferred: avoidable latency)**.
- **`render.yaml` is tracked as a record only** — it mirrors the dashboard so
  the deployment is reviewable, but Render does not read it: no service belongs
  to a Blueprint instance, so the dashboard is still the source of truth. No
  `Procfile` or `Dockerfile` is tracked.
- **Streamlit secrets are generated at boot** by `scripts/render_start.sh`
  because `app.py` touches `st.user` on every page load and raises without an
  `[auth]` section — a fragile startup coupling.

## 8. Environment and deploy

### Environment variables (names only)

**Core:** `EDGE_DB_BACKEND`, `EDGE_STATE_DB_URL`, `EDGE_STATE_DB_PATH`,
`EDGE_DATA_MODE`, `EDGE_APP_VERSION`

**Source APIs:** `EDGE_DART_API_KEY`, `EDGE_EDGAR_USER_AGENT`,
`EDGE_EDGAR_DISCOVERY_ENABLED`, `EDGE_EDINET_SUBSCRIPTION_KEY`,
`EDGE_TRANSLATION_API_KEY`

**Auth:** `EDGE_PRIVATE_BETA_AUTH_ENABLED`, `EDGE_PRIVATE_BETA_ALLOWED_EMAILS`,
`EDGE_AUTH_COOKIE_SECRET`, `EDGE_AUTH_REDIRECT_URI`, `EDGE_GOOGLE_CLIENT_ID`,
`EDGE_GOOGLE_CLIENT_SECRET`, `EDGE_GOOGLE_SERVER_METADATA_URL`,
`EEVA_ADMIN_EMAILS`

**Radar worker:** `EDGE_RADAR_WORKER_DB_BACKEND`,
`EDGE_RADAR_WORKER_STATE_DB_URL`, `EDGE_RADAR_WORKER_STATE_DB_PATH`,
`EDGE_RADAR_LIVE_SCAN_ENABLED`, `EDGE_RADAR_LIVE_SCAN_PROVIDERS`,
`EDGE_RADAR_SCAN_INTERVAL_MINUTES`, `EDGE_THEME_CANDIDATE_DETECTION_ENABLED`

**Daily News worker:** `EDGE_DAILY_NEWS_WORKER_DB_BACKEND`,
`EDGE_DAILY_NEWS_WORKER_STATE_DB_URL`, `EDGE_DAILY_NEWS_ENABLED_SOURCES`,
`EDGE_DAILY_NEWS_ENABLED_SOURCES_JP_KR`, `EDGE_DAILY_NEWS_LIVE_SCAN_ENABLED`,
`EDGE_DAILY_NEWS_SCAN_INTERVAL_MINUTES`, `EDGE_DAILY_NEWS_ADMIN_ENABLED`

**Company Discovery worker:** `EDGE_COMPANY_DISCOVERY_WORKER_DB_BACKEND`,
`EDGE_COMPANY_DISCOVERY_WORKER_STATE_DB_URL`,
`EDGE_COMPANY_DISCOVERY_LIVE_ENABLED`, `EDGE_COMPANY_DISCOVERY_ADMIN_ENABLED`,
`EDGE_COMPANY_DISCOVERY_SCAN_INTERVAL_MINUTES`,
`EDGE_COMPANY_DISCOVERY_STALE_DAYS`

**Research agent:** `EDGE_RESEARCH_AGENT_MODE`,
`EDGE_RESEARCH_AGENT_SERVICE_TOKEN`,
`EDGE_RESEARCH_AGENT_PUBLICATION_KILL_SWITCH_ENABLED`

**Dormant:** `EDGE_R2_*` (5), `EDGE_REMOTE_CACHE_ENABLED`,
`EDGE_MARKET_DATA_API_KEY`, `EDGE_EVIDENCE_SOURCE_API_KEY`

**Test-only:** `EEVARESEARCH_PG_TEST_PASSWORD`

### Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py          # http://localhost:8501
pytest -q                     # ~6,900 tests; 12 fail on a clean checkout
```

Postgres-backed tests need a disposable loopback container on `127.0.0.1:55432`
and skip cleanly without one. `scripts/postgres_test_container.sh` wraps this.

### Deploy

Push to `main` → Render auto-deploys. **Seven services, two databases:**

| Service | Type | Branch | Start command |
|---|---|---|---|
| `eevaresearch-ai` | Web | `main` | `bash scripts/render_start.sh` → Streamlit |
| `eevaresearch-radar-worker` | Worker | `main` | `python -m scripts.radar_worker` |
| `eevaresearch-daily-news-worker` | Worker | `main` | `python -m scripts.daily_news_worker` |
| `eevaresearch-company-discovery` | Worker | `feat/issuer-registry-foundation` | `python -u -m scripts.company_discovery_worker` |
| `eevaresearch-research-agent-worker` | Worker | `main` | *(suspended)* |
| `eevaresearch-landing` | Static | `main` | publishes `landing/` |
| `eeva-review-64d82127ad` | Web | `review/ui-3d1e4a870f` | review-mode Streamlit |

Dashboard is `app.eevaresearch.com`; the static site serves `eevaresearch.com`.
All services run Python 3 in Ohio and build with
`pip install -r requirements.txt`.

## 9. Suggested next steps

1. **Rewrite `README.md` and `IMPLEMENTATION_NOTES.md`.** They actively mislead
   anyone new — including future AI assistants — about what exists. Cheapest
   high-value fix in the repo.
2. **Clear the branch backlog.** ~12 worktrees, dozens of unmerged branches and
   30 uncommitted design docs. Two production services deploy from feature
   branches, which is a live correctness risk, not just untidiness. Decide what
   merges, what is abandoned, and get every service onto `main`.
3. **Fix or formally quarantine the 12 clean-checkout test failures.** A suite
   that is never green trains everyone to ignore failures.
4. **Commit deployment configuration to the repo** (`render.yaml` or equivalent)
   and reconcile the database configuration: retire or document the unused
   second Postgres instance, remove the radar worker's dead
   `EDGE_DB_BACKEND`/`EDGE_STATE_DB_URL`, and move services onto the internal
   database hostname.
5. **Decide the fate of the two parked efforts** — the filing API
   (`feat/filing-read-api`, complete and locally verified, needs a
   least-privilege DB role and restricted network access before exposure) and
   the autonomous research agent (suspended; its SDK has never been verified on
   Render's runtime). Both are finished enough to be worth either shipping or
   explicitly shelving.
