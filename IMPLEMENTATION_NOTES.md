# Implementation Notes — Foundation Phase

## What is complete

- **Data model** (`src/models/models.py`): typed dataclasses for Theme,
  Subtheme, Ticker, EvidenceItem, ResearchClaim, Catalyst, Signal,
  CapitalRotationMetric, ChatMessage/ChatAnswer, WatchlistEntry, plus the
  ClaimType/Direction/Strength/Horizon/Exposure enums. Every claim-bearing
  model carries a `claim_type`; `EvidenceItem` carries a full provenance set
  (id, source_name, source_type, published_at, retrieved_at, a derived
  `freshness_label`, `is_demo`, optional `ticker_symbol`/`theme_slug`) built
  to need zero migration when real evidence replaces demo evidence.
- **Repository interfaces + AppContext container** (`src/data_access/`):
  seven ABCs (`ThemeRepository`, `TickerRepository`, `EvidenceRepository`,
  `CatalystRepository`, `SignalRepository`, `MarketDataProvider`,
  `ResearchAnswerProvider`) with one demo implementation each, wired up in
  `container.py`'s `get_repositories()` — the single place Phase 2 changes.
  Every page depends on these interfaces, never on a seed filename.
- **Logic layer** (`src/logic/`): pure, Streamlit-free helpers for
  formatting, theme/signal aggregation (leaders/laggards, breadth), and
  claim-type/freshness badge mapping — independently unit-tested.
- **UI system**: a thin CSS layer over the existing `.streamlit/config.toml`
  dark theme, a global chrome wrapper (status banner + footer applied to
  every page via `with_chrome`), and a reusable component library (cards,
  badges, section headers, filterable tables, empty states, the Market Brief
  module) built on Streamlit's native widgets (`st.container(border=True)`,
  `st.badge`, `st.dataframe`, `st.bar_chart`) rather than custom HTML/CSS
  where a native widget already does the job.
- **Navigation**: `st.navigation` + function-based `st.Page` objects, seven
  visible primary pages plus one hidden ticker-detail template
  (`visibility="hidden"`), reachable only via `st.page_link`/query params
  from Themes and the Market Brief — never a nav-menu entry, never a
  typed-only URL. Validated against the installed Streamlit version
  (1.61.1) before writing any routing code.
- **All seven primary pages** (Overview with Market Brief, Themes, Research
  Chat, Capital Rotation, Signal Board, Watchlists, Methodology) plus the
  ticker-detail template, all reading from `data/seed/` through the
  repository interfaces.
- **Tests**: 66 passing — data-model validation, repository/loader
  behavior (including a missing/malformed seed file degrading to empty
  results rather than raising), pure logic helpers, and 18
  `st.testing.v1.AppTest`-based smoke tests covering every registered page
  (including the hidden ticker-detail page's query-param handling) for
  exceptions, the global chrome, and demo-evidence labeling.

## What is intentionally mocked

- Every number, timestamp, signal, catalyst, and evidence item in
  `data/seed/` (see `data/seed/README.md` for the full inventory and the
  no-fabricated-realism conventions followed there).
- Research Chat's answers — canned responses to the five suggested
  questions plus a generic demo-mode fallback for anything else. No LLM
  call anywhere in this build.
- Rotation/RS-vs-benchmark figures, positioning/short-interest (explicitly
  marked "not built" rather than faked), and the ticker-detail template's
  fundamental/technical snapshot fields (rendered as literal `—`
  placeholders, never an invented number).
- Watchlist add/remove — session-state only, not persisted across a reload.

## Two real bugs found and fixed during manual verification

Both were caught by actually clicking through the running app in a browser,
not by reading the code:

1. `ticker_filter_bar`'s `st.multiselect` widgets had no explicit `key`.
   Since `st.tabs` renders every tab's content in the same script run (not
   lazily), calling the filter bar once per theme tab produced five
   identical widget IDs and a `StreamlitDuplicateElementId` crash. Fixed by
   threading a `key_prefix` (the theme slug) through every widget key.
2. Research Chat's typed `st.chat_input` sits below the messages
   read/render block in script order (the fixed-composer convention), so
   appending the new message to `st.session_state` there didn't show up
   until a second rerun. Fixed with an explicit `st.rerun()` on that path
   specifically — the suggested-question buttons above don't need it, since
   their append happens before that same read.

## Phase 2 — Data integration plan

Real sources slot in entirely behind the existing interfaces, with no
page-rendering code changes:

1. Add a `src/data_access/live/` package alongside `demo/`, one
   implementation per interface, backed by whatever real source is chosen
   per domain (e.g. SEC EDGAR/filings for `EvidenceRepository`, a licensed
   market-data provider for `MarketDataProvider`, a retrieval-augmented LLM
   call for `ResearchAnswerProvider`).
2. Repoint `container.py`'s `get_repositories()` to the new implementations
   — behind a `Settings.data_mode` switch (already a field on `Settings`,
   currently always `"demo"`), so demo mode stays available for local dev
   and tests.
3. Extend `EvidenceItem`/`Ticker` population to real values — the fields
   already exist (`source_url`, `published_at`, real financials), so this is
   data population, not a schema change.
4. Re-run the existing test suite against a `live` fixture in addition to
   `demo` — the interface-based design means the same tests in
   `test_data_access.py` (adapted to assert real-shape invariants instead
   of demo-specific ones) should mostly carry over.
5. Only after Phase 2's evidence pipeline exists does it make sense to wire
   Research Chat to a real LLM call — answering research questions well
   depends on having real evidence to retrieve against, not the other way
   around.

## Phase 3 — Curated ticker-universe plan

1. Replace `data/seed/tickers_demo.json`'s single `DEMO` row with a real,
   curated ticker list — populated through the same `TickerRepository`
   interface (a `live/ticker_repository.py`, or a CSV/DB-backed one; the
   interface doesn't care).
2. Re-populate `EvidenceRepository`, `CatalystRepository`, and
   `SignalRepository` per real ticker, using the same shapes already
   validated in Phase 1 — no `Ticker`/`Signal`/`Catalyst` field changes
   anticipated, since Phase 1 modeled these deliberately generically.
3. `ticker_filter_bar`'s filters (market cap, liquidity, subcategory,
   exposure, technical strength, catalyst type, risk level) already derive
   their options from whatever data is loaded — they'll start actually
   narrowing results once there's more than one ticker to filter, with no
   code change required.
4. Capital Rotation's `leaders`/`laggards` fields on `CapitalRotationMetric`
   already accept ticker lists — Phase 1 deliberately left these empty
   rather than fabricate plausible-looking ticker symbols; Phase 3 populates
   them for real.
5. Revisit `EDGE_RADAR_MAX_SNAPSHOT_TICKERS_PER_RUN`-style cost controls
   (a pattern from the retired product, see `MIGRATION_NOTES.md`) if/when
   any per-ticker refresh job is reintroduced — not needed while the
   universe is manually curated and small.
