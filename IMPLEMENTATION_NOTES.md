# Implementation Notes

What is built, what is partial, and what is known-broken, as of 2026-11-23
(`main` at `127442d`). The authoritative "why" for any decision here is
`design/DECISIONS.md`; this file is the index to current state.

> This document previously described a demo-data "foundation phase" with 66
> tests, no live ingestion and no persistence. That has been wrong for a long
> time and is rewritten here. If you find other docs making foundation-phase
> claims, distrust them and check the code.

## Built and running

**Ingestion.** Three independent regulator pipelines, each with its own client,
scan service, rule module, document extractor and orchestration:

- `src/data_access/edgar/` — SEC EDGAR, incl. discovery rules and CIK resolution
- `src/data_access/dart/` — Korea OpenDART, incl. corp-code resolution,
  ownership/equity-transaction materiality, low-value filing rules, retry policy
- `src/data_access/edinet/` — Japan EDINET, incl. annual-report materiality

Each pipeline is **bounded and idempotent**: only candidates above a confidence
threshold are eligible for retrieval, only a per-run budget are processed, and
everything else is left as `CANDIDATE_DETECTED` or `PROCESSING_DEFERRED` for the
next run. Nothing is dropped or silently overwritten.

**Persistence.** `src/data_access/state_db/` (SQLite, schema v23) and
`src/data_access/postgres_state_db/` (Postgres, schema v25) are *independent*
implementations — deliberately no shared dialect abstraction. Selection is via
`Settings.db_backend` through `src/data_access/backend_factory.py`. The core
tables are `candidates` ⋈ `filing_events` joined on
`(source_name, corp_code, rcept_no)`; `rcept_dt` is the authoritative filing
date on every source (`filed_at` is EDINET-only).

**Workers.** Four standalone entry points, none imported by `app.py` or any UI
page — the dashboard never scans on page render:

- `scripts/radar_worker.py` — continuous filing scans
- `scripts/daily_news_worker.py` — feed discovery, requires Postgres
- `scripts/company_discovery_worker.py` — passive candidate ledger
- `scripts/research_agent_worker.py` — dormant by default

Each builds its own scoped `Settings` via a private `_build_worker_settings()`
that reads worker-specific variables and overrides `db_backend` /
`state_db_url`. **The generic `EDGE_DB_BACKEND` / `EDGE_STATE_DB_URL` are
therefore ignored by the workers for database selection.**

**Daily News.** Curated feed registry, URL quality gate, canonicalisation,
deduplication, extractive-only grounding, materiality tiering, cross-lane
grouping, plus its own admin page.

**Themes.** `config/eevaresearch_theme_registry.yaml` — a typed, fail-closed
taxonomy of 13 themes with matching policy, entity roles, geographies,
materiality keywords and an output contract. Loaded with `yaml.safe_load` only.

**Universe.** `src/config/tracked_companies.py` — 129 issuers (90 EDGAR,
27 EDINET, 12 DART).

**Decision and publication policy** (`src/logic/`, pure and Streamlit-free):
`signal_decision_policy.py` routes an extracted filing to PUBLISH / TIMELINE /
REVIEW / ARCHIVE; `publication_policy.py` is the only thing that can turn a
candidate into a published item; `quote_verification.py` checks that a stored
excerpt actually supports the claim; `research_case_validation.py` validates
case structure; `agent_write_guard.py` confines shadow-mode writes.

**UI.** 19 page modules under `src/ui/pages/` including Dashboard, Signals, Radar
Inbox, Daily News, Research Cases, Coverage, Theme Workspace, Verified Updates,
Agent Review, and four admin pages. `with_chrome()` (in `src/ui/ui.py`) wraps
every page.

**Auth.** Google OAuth through Streamlit's native `st.user` / `st.login`, with
an email allowlist decision layer in `src/ui/beta_gate.py` (pure, no I/O).
`EEVA_ADMIN_EMAILS` gates admin pages. The allowlist is authorization only —
it cannot verify that an email belongs to the caller.

There are now **two** access layers, and only the first is live. The beta gate
above treats an empty allowlist as *allow*, which is what makes the open beta
open. `src/logic/account_access.py` inverts that — a row grants access and
nothing else does — but it is switched off (see the entitlements row below).
Do not read one as a replacement for the other until enforcement is enabled;
today the beta gate is the only thing deciding anything.

## Partial, dormant, or parked

| Thing | State |
|---|---|
| **Account entitlements** (`src/models/account_access.py`, `src/logic/account_access.py`, `src/ui/access_screens.py`) | **Built, deployed and dormant.** Production is at Postgres v28 with `account_entitlements` and `account_access_events` created and **empty**. `EDGE_ACCESS_ENFORCEMENT_ENABLED` is unset, so `evaluate_account_access()` returns `ENFORCEMENT_DISABLED` for everyone and `app.py` never constructs the repository — the open beta's own gate is still the only gate. **Enabling it before backfilling locks out every existing user, because nobody has a row.** Order: migrate (done), backfill at `/admin-users`, verify, enable. Administrators (`EEVA_ADMIN_EMAILS`) are allowed in before enforcement, before the allowlist and before the store is read, so switching it on cannot lock the operator out of the page that switches it off. |
| **Autonomous Research Agent** (`src/mcp_agent/`, `scripts/research_agent_worker.py`) | Fully built, **dormant by default** behind `EDGE_RESEARCH_AGENT_MODE` plus a publication kill switch and a shadow write-guard. Its Render service is **suspended**. Runtime deps isolated in `requirements-agent.txt`; the SDK's Linux wheel has **never been verified** on Render's runtime. |
| **Read-only filing API** | Complete on the unmerged branch `feat/filing-read-api` (4 commits, unpushed): digest-based bearer auth, three projection policies, SQLite + Postgres adapters, gunicorn hosting, rate limiting, readiness gate. Verified locally end-to-end against a disposable Postgres. **Never deployed, and the branch is local-only — never pushed.** Needs a least-privilege DB role and restricted network access first. It is also the only non-Streamlit HTTP surface in the project, so anything needing a webhook endpoint depends on its fate. |
| **LLM filing comparison** | Parked on `feat/llm-filing-comparison`. A synthetic-only smoke test ran; use against real filings is not cleared. |
| **R2 remote cache** (`src/data_access/remote_cache/`) | Code exists, explicitly dormant. Nothing imports it at runtime; `boto3` is only reached through a lazy import that never fires. |
| **Demo backend** (`src/data_access/demo/`, `data/seed/`) | Still the default when `EDGE_DB_BACKEND` is unset. Used by local dev and a large part of the test suite. |
| **Unmerged work** | Three open draft PRs: #108, #111 and #112, plus the `rescue/daily-news-cohort1-sources` branch, which has no PR pending a scope-guard exception. `feat/supply-chain-relationship-seed-v1` holds a validated 12-relationship seed that nothing calls; see PR #118's own discussion for why it was not merged. |

There are **no `TODO`/`FIXME`/`XXX`/`HACK` comments** anywhere in `src/`,
`scripts/` or `app.py`. Incomplete work is tracked in `design/` and in module
docstrings instead — if you want to know what is unfinished, that is where to
look, not a grep.

## Known issues

### Clean-checkout test failures — resolved 2026-11-22

`pytest -q` on a pristine checkout failed **12** tests for a long time, which
trained everyone — human and assistant — to skim past failures. PR #113 fixed
all 12 and added CI, so a clean checkout is now green: **6,647 passed, 312
skipped, 0 failed**.

**Every one of the 12 was an outdated test, not a bug.** No production code
changed. One theme ran through all of them: *point-in-time claims implemented
as permanent assertions* — each was true the day it was written and became
false as the repository moved. Where possible the replacement asserts a
property that cannot drift (derived counts, bounded document slices, field
presence instead of exact prose) rather than a new fixed value.

| Tests | Cause → fix |
|---|---|
| 3 | A `DECISIONS.md` phase section was sliced marker→EOF; the file is append-only, so later sections got swallowed → bound the slice to the phase's own `## ` heading |
| 4 | Registry sizes hardcoded (105 seed, 10 DART…); the registry grew to 129 → derive counts from `SEED_ISSUERS`/`DISCOVERY_STUBS` |
| 2 | Phase-scoped guards forbade `daily_news` imports but read files' *current* contents; the filing-event bridge is deliberate → drop that clause, keep the rest |
| 1 | Diffed the working tree against `HEAD:requirements.txt`, so it could only pass *before* its own commit → assert the dependency as a property |
| 1 | Asserted the original excerpt is never shown; it is now deliberately shown and labelled → assert the honesty invariant instead |
| 1 | Asserted one exact composed sentence; the card was redesigned → assert the fields individually |

Adding CI immediately found three more failure classes that only a clean
environment can expose, and which had been masked locally:

| Tests | Cause → fix |
|---|---|
| 5 modules | Agent suites raised `ModuleNotFoundError` at **collection** time without `requirements-agent.txt`, aborting the whole run → module-level `pytest.importorskip` |
| 6 + 2 | `requirements.txt` allows `streamlit>=1.38,<2.0`; a fresh install gets a newer Streamlit where `AppTest.query_params.get()` returns a scalar, not a one-element list → normalise in the test rather than pin the dependency |
| 1 | A test asserted a live Japan Times fetch returns 403 — true from one IP, 200 from GitHub's runners → gate behind `EEVARESEARCH_RUN_LIVE_NETWORK_TESTS` so the suite has no default network dependency |

The lasting lesson is the second table, not the first: "it passes locally" was
not the same as "it passes," and nothing surfaced the difference until a check
ran somewhere else.

### Diff-scope guards

Many tests run `git diff --name-only HEAD` and fail on any *tracked*
modification outside an approved file set. They clear on commit. Untracked
files do not trip them. Consequence: a dirty working tree produces ~13–20 extra
failures that say nothing about correctness.

### Documentation drift

A recurring pattern, not a one-off. This file and `README.md` were both years
out of date. `signal_decision_policy.py`'s docstring contains a correction of
its own earlier false claim about "shadow mode". Treat prose as needing
verification against code.

### Deployment configuration

- **`render.yaml` is tracked but not authoritative.** It mirrors the live
  dashboard configuration so the deployment is reviewable here; Render ignores
  it, because no service belongs to a Blueprint instance. The dashboard remains
  the source of truth until a Blueprint is deliberately created. No `Procfile`
  or `Dockerfile` is tracked.
- **All five live services now deploy from `main`.** Until 2026-11-22
  `eevaresearch-company-discovery` deployed from `feat/issuer-registry-foundation`
  — fully merged since 2026-09-05, so it ran ~475 commits behind and failed
  every tick against a schema it no longer matched. The `review/ui-3d1e4a870f`
  review service is suspended; its branch is kept only because that suspended
  service still pins it.
- **Two Postgres datastores exist; only one is used.** All live services point
  at the same database; the second is referenced by nothing.
- **Four independently-settable DSN variables point at the same database**, so
  changing one silently repoints only that service. The radar worker also
  carries a generic `EDGE_STATE_DB_URL` that is dead configuration (see above).
- **Three of four services connect over the external database hostname** rather
  than Render's internal one.
- **Secrets are generated at boot** by `scripts/render_start.sh` because
  `app.py` touches `st.user` on every page load — a fragile startup coupling.

## Environment variables

Names only; see `.env.example`. Grouped by the subsystem that reads them.

- **Core** — `EDGE_DB_BACKEND`, `EDGE_STATE_DB_URL`, `EDGE_STATE_DB_PATH`,
  `EDGE_DATA_MODE`, `EDGE_APP_VERSION`
- **Source APIs** — `EDGE_DART_API_KEY`, `EDGE_EDGAR_USER_AGENT`,
  `EDGE_EDGAR_DISCOVERY_ENABLED`, `EDGE_EDINET_SUBSCRIPTION_KEY`,
  `EDGE_TRANSLATION_API_KEY`
- **Auth** — `EDGE_PRIVATE_BETA_AUTH_ENABLED`,
  `EDGE_PRIVATE_BETA_ALLOWED_EMAILS`, `EDGE_AUTH_COOKIE_SECRET`,
  `EDGE_AUTH_REDIRECT_URI`, `EDGE_GOOGLE_CLIENT_ID`,
  `EDGE_GOOGLE_CLIENT_SECRET`, `EDGE_GOOGLE_SERVER_METADATA_URL`,
  `EEVA_ADMIN_EMAILS`
- **Radar worker** — `EDGE_RADAR_WORKER_DB_BACKEND`,
  `EDGE_RADAR_WORKER_STATE_DB_URL`, `EDGE_RADAR_WORKER_STATE_DB_PATH`,
  `EDGE_RADAR_LIVE_SCAN_ENABLED`, `EDGE_RADAR_LIVE_SCAN_PROVIDERS`,
  `EDGE_RADAR_SCAN_INTERVAL_MINUTES`, `EDGE_THEME_CANDIDATE_DETECTION_ENABLED`
- **Daily News worker** — `EDGE_DAILY_NEWS_WORKER_DB_BACKEND`,
  `EDGE_DAILY_NEWS_WORKER_STATE_DB_URL`, `EDGE_DAILY_NEWS_ENABLED_SOURCES`,
  `EDGE_DAILY_NEWS_ENABLED_SOURCES_JP_KR`, `EDGE_DAILY_NEWS_LIVE_SCAN_ENABLED`,
  `EDGE_DAILY_NEWS_SCAN_INTERVAL_MINUTES`, `EDGE_DAILY_NEWS_ADMIN_ENABLED`
- **Company Discovery worker** — `EDGE_COMPANY_DISCOVERY_WORKER_DB_BACKEND`,
  `EDGE_COMPANY_DISCOVERY_WORKER_STATE_DB_URL`,
  `EDGE_COMPANY_DISCOVERY_LIVE_ENABLED`,
  `EDGE_COMPANY_DISCOVERY_ADMIN_ENABLED`,
  `EDGE_COMPANY_DISCOVERY_SCAN_INTERVAL_MINUTES`,
  `EDGE_COMPANY_DISCOVERY_STALE_DAYS`
- **Research agent** — `EDGE_RESEARCH_AGENT_MODE`,
  `EDGE_RESEARCH_AGENT_SERVICE_TOKEN`,
  `EDGE_RESEARCH_AGENT_PUBLICATION_KILL_SWITCH_ENABLED`
- **Dormant** — `EDGE_R2_*` (5), `EDGE_REMOTE_CACHE_ENABLED`,
  `EDGE_MARKET_DATA_API_KEY`, `EDGE_EVIDENCE_SOURCE_API_KEY`
- **Test-only** — `EEVARESEARCH_PG_TEST_PASSWORD`

## Conventions worth knowing

- **The dashboard never performs a recurring external scan on page render.**
  All scanning lives in the standalone workers.
- **Policy logic in `src/logic/` is pure** — no Streamlit import, no I/O, no
  network. That is what makes it unit-testable, and it is enforced by tests.
- **The two database backends are independent by design.** Do not introduce a
  shared dialect abstraction without reading the Phase 4B decision record.
- **Claim typing is load-bearing**, not decoration. Every claim-bearing model
  carries a `claim_type`, and the UI renders it.
