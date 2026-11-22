# Implementation Notes

What is built, what is partial, and what is known-broken, as of 2026-11-19
(`main` at `e827320`). The authoritative "why" for any decision here is
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

## Partial, dormant, or parked

| Thing | State |
|---|---|
| **Autonomous Research Agent** (`src/mcp_agent/`, `scripts/research_agent_worker.py`) | Fully built, **dormant by default** behind `EDGE_RESEARCH_AGENT_MODE` plus a publication kill switch and a shadow write-guard. Its Render service is **suspended**. Runtime deps isolated in `requirements-agent.txt`; the SDK's Linux wheel has **never been verified** on Render's runtime. |
| **Read-only filing API** | Complete on the unmerged branch `feat/filing-read-api` (4 commits, unpushed): digest-based bearer auth, three projection policies, SQLite + Postgres adapters, gunicorn hosting, rate limiting, readiness gate. Verified locally end-to-end against a disposable Postgres. **Never deployed.** Needs a least-privilege DB role and restricted network access first. |
| **LLM filing comparison** | Parked on `feat/llm-filing-comparison`. A synthetic-only smoke test ran; use against real filings is not cleared. |
| **R2 remote cache** (`src/data_access/remote_cache/`) | Code exists, explicitly dormant. Nothing imports it at runtime; `boto3` is only reached through a lazy import that never fires. |
| **Demo backend** (`src/data_access/demo/`, `data/seed/`) | Still the default when `EDGE_DB_BACKEND` is unset. Used by local dev and a large part of the test suite. |
| **Unmerged work** | ~12 active worktrees and dozens of branches; 30 uncommitted design documents in `design/`. |

There are **no `TODO`/`FIXME`/`XXX`/`HACK` comments** anywhere in `src/`,
`scripts/` or `app.py`. Incomplete work is tracked in `design/` and in module
docstrings instead — if you want to know what is unfinished, that is where to
look, not a grep.

## Known issues

### Clean-checkout test failures (12 on `main` at `e827320`)

`pytest -q` on a pristine checkout: **12 failed, 6615 passed, 287 skipped**
out of 6,914 collected.

- `test_backend_factory_phase2b.py::test_phase2b_files_never_reference_real_local_state_or_real_signal_ids`
- `test_candidate_persistence_phase3a.py::test_phase3a_files_never_reference_real_local_state_or_real_signal_ids`
- `test_state_db_candidate_repository.py::test_phase3b0_files_never_reference_real_local_state_or_real_signal_ids`
- `test_coverage_page.py::test_coverage_page_shows_both_seed_and_discovery_tables_with_expected_row_counts`
- `test_coverage_page.py::test_coverage_page_summary_metrics_match_registry_counts`
- `test_evidence_packet_phase1.py::test_no_new_document_fetch_or_forbidden_dependency_introduced`
- `test_evidence_packet_phase2_step2.py::test_no_forbidden_imports_introduced_in_phase2_step2_files`
- `test_issuer_coverage.py::test_country_filter_returns_expected_subset`
- `test_issuer_coverage.py::test_source_filter_returns_expected_subset`
- `test_radar_public_read_only.py::test_radar_card_shows_only_the_approved_public_fields`
- `test_ui_audit_phase_r1.py::test_card_shows_original_and_english_translation`
- `test_ui_audit_phase_t1.py::test_phase_t1_does_not_add_a_dependency_other_than_tzdata`

These are long-standing, not new regressions. Diagnosis per test is pending.

The exact set is base-dependent: at `f803dca` (24 commits earlier) it is 11,
without `test_radar_public_read_only`. Measure before citing a number.

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

- **No tracked `render.yaml`, `Procfile` or `Dockerfile`.** Every service
  setting exists only in the Render dashboard.
- **`eevaresearch-company-discovery` deploys from `feat/issuer-registry-foundation`**,
  not `main` — merging to `main` does not update it. A review web service runs
  from `review/ui-3d1e4a870f` similarly.
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
