# Migration Notes — Foundation Rebuild

This branch (`foundation-rebuild`) replaces the prior EevaResearch AI product
with a new one: a curated, evidence-first thematic market-intelligence
platform across five fixed themes (AI Buildout, Humanoids, Space, Memory,
Photonics), built foundation-first with mock/demo data only.

This file records what's being retired and the ground rules for this branch,
written before any prior-product file is deleted, moved, or modified.

## Prior product — what this branch retires

The previous product was a different concept: an individual-ticker
research-brief tool with a scoring/guardrails pipeline, plus a separate
always-on autonomous scanner. The modules and integrations behind that are
being retired from this branch:

- **Manual research pipeline**: `src/services/` (research, thesis, notes,
  ticker, watchlist, audit, settings services), `src/scoring/` (component
  scorers, scorecard, context, defaults), `src/guardrails/` (citation
  validator, language filters, source hierarchy).
- **Persistence layer**: `src/database/` (SQLite schema + seed).
- **Live data providers**: `src/providers/` — `edgar_client.py` /
  `live_edgar.py` (SEC EDGAR filings, fundamentals, insider transactions,
  news), `finnhub_client.py` / `live_price.py` (Finnhub price/valuation/
  analyst data), `dart_client.py` / `live_dart.py` (Korea DART filings),
  `registry.py` (provider routing), `mock_providers.py`, `base.py`.
- **Autonomous scanner ("Radar")**: all of `src/radar/` (feed ingestion,
  keyword filtering, LLM tagging, ticker registry, ticker snapshots,
  analytics, webhook notifier, run orchestration), its scheduled GitHub
  Actions workflow (`.github/workflows/radar_scan.yml`), and its data files
  (`data/radar_findings.json`, `data/radar_state.json`,
  `data/ticker_snapshots.json`, `data/tracked_tickers.json`).
- **Smoke-test scripts and workflows** for the retired live providers:
  `scripts/dart_smoke_test.py`, `scripts/finnhub_smoke_test.py`,
  `scripts/run_radar_scan.py`, `.github/workflows/dart_smoke_test.yml`,
  `.github/workflows/finnhub_smoke_test.yml`.
- **Old UI pages** under `src/ui/` built around the prior product (dashboard,
  new brief, compare snapshots, alerts, sources, scoring settings, data
  provider settings, guardrails page, app settings, radar, radar trends,
  capital rotation, watchlist, ticker detail) and their `tests/*` coverage.
- `sample_data/` (superseded by `data/seed/` in the new build).

## What's preserved, untouched

- **`main`** — the prior product remains fully intact and deployed exactly as
  it is today. This branch does not merge into, rebase onto, or otherwise
  affect `main` or the live Streamlit Cloud deployment reading from it.
- **Git history** — every retired module remains fully recoverable from `main`
  and from this branch's own history at the commit prior to any deletion.
- **`.env`, `data/edge_research.db`, live credentials, and provider
  configuration** — not read, not modified, not deleted, not referenced by
  the new build.

## What this foundation build intentionally does NOT include (Phase 1)

- No autonomous scanner or scheduled scan job of any kind.
- No live calls to SEC EDGAR, Finnhub, DART, or any other external market/
  filings API.
- No other external API calls of any kind.
- No LLM wiring (Research Chat uses only canned demo answers in this phase).
- No trading integrations, order execution, or portfolio/broker connectivity.
- No real ticker universe — the only ticker in this phase is a clearly
  labeled fictional demo company (`DEMO` / "Nova Aperture Systems (Demo
  Company — Not Real)").
- No real-time or delayed market-data presentation of any kind.

## Code provenance

No code from `src/radar/`, `src/providers/`, `src/services/`, `src/scoring/`,
`src/guardrails/`, `src/database/`, or the old `src/ui/` is copied into the
new implementation. Everything under `src/` on this branch going forward is
newly written for the new product concept. The only carried-over pattern (not
code) is the *idea* of a single provider-wiring seam, referenced in the plan
as similar in spirit to the old `src/providers/registry.py`'s routing
approach — the new `src/data_access/container.py` is a fresh implementation
of that idea, not a copy.
