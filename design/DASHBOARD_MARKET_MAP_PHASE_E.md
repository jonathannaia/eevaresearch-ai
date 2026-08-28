# Phase E — Dashboard Market Map: Discovery & Design (no implementation yet)

> **Implementation status update:** Phase E1 (the MVP spec recommended
> below) was subsequently approved and implemented — see
> `design/DECISIONS.md`'s "Phase E1 — Dashboard Market Map (Verified
> Capabilities Only)" entry for exactly what shipped. Every discovery
> finding below (sections A–E) remains an accurate historical record of
> what was verified at the time this report was written and was not
> re-litigated; it is preserved unchanged.

Status: **discovery/design only — no product behavior changed, nothing committed.**
This document is the required output of the Phase E discovery pass. It answers
the five discovery questions (A–E), proposes an MVP spec, and lays out a
phased plan for review. Every claim below is grounded in a specific file —
nothing here is assumed or extrapolated beyond what the repository actually
contains as of this writing.

---

## A. Existing market-price capabilities

**There is no quote, price, intraday, market-cap, volume, exchange, currency,
market-open/closed, or price-history data source anywhere in this
repository — real or provider-backed.** The only thing resembling "market
data" is `CapitalRotationMetric` (`relative_performance_pct`, `breadth_pct`),
and that is 100% static seed JSON (see section D).

Evidence:
- `src/data_access/interfaces.py:77-83` — `MarketDataProvider` is the *only*
  price-adjacent interface in the app, and it exposes exactly two methods:
  `get_rotation_metrics()` and `get_rotation_metric_for_theme()`. No
  `get_quote`, `get_price_history`, `get_market_cap`, or similar exists.
- `src/data_access/demo/market_data_provider.py` is the *only* implementation
  of that interface. It reads `data/seed/rotation_metrics.json` via
  `src/data_access/loaders.py` — a local file read, not a network call.
- `src/data_access/container.py:60` hardcodes
  `market_data_provider=DemoMarketDataProvider(settings)` unconditionally —
  unlike `signal_repository` (line 57), which goes through
  `backend_factory.get_signal_repository(settings)` and has a real,
  live-wired alternative (`RadarSignalRepository`). No equivalent live
  alternative exists for market data at all.
- `requirements.txt` has no market-data/quote vendor library. Its only HTTP
  clients (`requests`, `httpx`) back the DART/EDGAR/EDINET *filing* APIs,
  not a price feed. `boto3` is dormant R2 remote-cache infra; `psycopg` is
  Postgres; none are price-related.
- `src/models/models.py:129-151` (`Ticker`) — the model backing Company
  pages/theme "Companies" tabs — has **no** exchange, currency, country,
  market cap (real number), volume, or price field at all. It carries
  `market_cap_bucket`/`liquidity_bucket`/`technical_strength`/`risk_level`
  as **placeholder category strings** (e.g. `"Mid — placeholder"`), explicitly
  documented as "not computed." `data/seed/tickers_demo.json` contains
  exactly **one** record: the fictional `DEMO` / "Nova Aperture Systems
  (Demo Company — Not Real)."
- Grep across `src/` and `requirements.txt` for `yfinance`, `alpha vantage`,
  `polygon.io`, `iex cloud`, `finnhub`, `marketstack`, `twelvedata`,
  `tiingo`, and common exchange-suffix literals (`.KS`, `.KQ`, `.T`) found
  nothing wired to any live call. The only two exchange-suffix-style
  strings in the whole repo (`011070.KS`, `3103.T` in
  `src/config/issuer_registry.py:184,313`) are **unverified discovery-stub**
  tickers (inferred from format only, not part of the active tracked
  universe, not fetched anywhere).

**Country coverage:** N/A — there is no price coverage to report by market,
because there is no price data at all, for any market.

**Refresh cadence / cache / rate limits / secrets / licensing:** N/A for the
same reason. The only rate-limit-aware code in the repo
(`DartRateLimitError`, `EdgarRateLimitError`, `EdinetRateLimitError` in each
source's `client.py`/`errors.py`) governs the three *filing* APIs, not price
data. No market-data provider credential, API key, or ToS reference exists
anywhere in `src/`, `.env`, `.streamlit/config.toml`, or `design/*.md`.

**Ticker-format readiness for a future quote lookup**, if one is ever added
(informational only — not proposed for this phase):
- US (SEC EDGAR): `TrackedCompany.krx_code` already holds the plain US
  ticker (e.g. `"NVDA"`, `"AMD"`) — directly usable by most US quote
  providers with no transformation.
- South Korea (DART): `krx_code` holds the bare 6-digit KRX code (e.g.
  `"005930"`). No exchange suffix (`.KS`/`.KQ`) is stored or computed
  anywhere — a future integration would need to add that mapping.
- Japan (EDINET): `krx_code` holds EDINET's own 5-character securities code
  (e.g. `"99840"`), which `tracked_companies.py`'s own module docstring
  (lines 32-34) explicitly warns is **not** the bare 4-character TSE code
  most quote providers expect (e.g. Yahoo Finance's `9984.T` for SoftBank).
  A future integration would need a documented, verified transformation —
  not a guess — before this code is usable as a quote-lookup key.
- China: no ticker format exists at all (see section C).

---

## B. Existing news and regional-summary capabilities

**There is no news ingestion, issuer-news feed, earnings/IR-update feed, or
region-level market-summary source anywhere in the repository.**

Evidence:
- `find src/ -iname "*brief*" -o -iname "*summary*" -o -iname "*heatmap*" -o
  -iname "*news*"` returns nothing except a stale compiled
  `market_brief.cpython-312.pyc` with **no corresponding `.py` source** —
  confirming `market_brief.py` was already deleted in an earlier UI-audit
  pass (Phase A) as dead/unrouted code, not a working feature.
- `src/ui/pages/about.py:38-42` — the app's own "Data sources" copy already
  states this honestly today: *"SEC EDGAR, TDnet (Japan), DART (Korea),
  CNINFO (China), and HKEXnews, plus market pricing for breadth and
  rotation calculations. Filing coverage varies by venue; pricing is
  delayed. In this foundation phase every figure and filing shown is demo
  data — no live source is connected yet."* TDnet, CNINFO, and HKEXnews are
  named as **aspirational** future sources; none has any adapter, client,
  or seed data behind it anywhere in `src/data_access/`.
- The closest things to "real, dated, sourced content" that exist today are:
  1. **Real filings** via `src/data_access/live/radar_signal_repository.py`
     — genuine DART/EDGAR/EDINET filing excerpts with real `source_url`,
     `retrieved_at`, and (for DART) machine translation. This is
     **filing-triggered evidence**, not market news/headlines, and is
     cache-backed (JSON files under `settings.cache_dir`, populated by a
     manual "Scan … now" click or the separate `scripts/radar_worker.py`
     process) rather than a continuously-polled feed by default.
  2. **Canned Research answers** (`src/data_access/demo/
     research_answer_provider.py`, `data/seed/chat_demo_answers.json`) —
     100% static demo Q&A, explicitly labeled as such
     (`FALLBACK_ANSWER_TEMPLATE` even says so for any unrecognized
     question).
  3. Nothing else.

**Country coverage:** DART → South Korea, SEC EDGAR → United States, EDINET
→ Japan, all filing-only. **China has zero source coverage** — no CNINFO or
HKEX adapter exists, and (see section C) no China-domiciled company is even
in the tracked-company registry, active or discovery-stub.

**Can Eeva honestly create sourced regional briefs for all four regions
today?** No, not with current inputs:
- **United States** — partially: real, dated SEC EDGAR filing excerpts exist
  for tracked US companies (when a scan has found something), which is a
  legitimate basis for a filing-grounded note, but not a market-wide "US
  markets today" narrative — there's no broad-market or index data of any
  kind.
- **South Korea** — same shape, DART-only, narrower (2 tracked companies).
- **Japan** — same shape, EDINET-only, narrower still (5 tracked companies,
  and EDINET "has never had a live scan run against it" per
  `radar_inbox.py`'s own module docstring — its FilingEvent/CandidateSignal
  counts are documented as staying at zero until a separately authorized
  live-scan gate).
- **China** — no honest brief is possible at all. There is no source, no
  tracked company, no cached data, nothing to cite.

---

## C. Existing theme/company/ticker map

**Single authoritative source of truth, confirmed non-duplicated:**
`src/config/tracked_companies.py::TRACKED_COMPANIES` (a tuple of 32 frozen
`TrackedCompany` records). Everything else that looks like a company
registry is a *generated, read-only view* of this same tuple:

- `src/config/issuer_registry.py:80-84` — `SEED_ISSUERS` (used by the
  Coverage page) is built by `tuple(_issuer_from_tracked_company(tc) for tc
  in get_tracked_companies(active_only=False))` — the module's own
  docstring (lines 3-9) states it is "Generated programmatically from the
  live tuple (never hand-transcribed) so it can never drift out of sync
  with… the registry every existing pipeline already depends on." Confirmed
  by direct inspection, not assumed.
- `src/models/models.py::Ticker` / `data/seed/tickers_demo.json` is a
  **separate, unrelated concept** — a single fictional demo record used
  only to exercise the Company-page template layout. It is not a second
  source of truth for real companies and should not be treated as one.

**Fields available per company (`TrackedCompany`,
`src/config/tracked_companies.py:47-67`):** `name` (curated display name),
`native_name` (source-native legal name, EDINET only), `exchange` (display
label, e.g. `"NASDAQ"`, `"KRX"`, `"TSE"` — never used for any API call),
`krx_code` (ticker/stock-code/securities-code, meaning depends on `source` —
see section A), `source` (`"OpenDART / DART"` / `"SEC EDGAR"` / `"EDINET"`),
`corp_code` (resolved CIK/corp-code, `None` until a resolver has run),
`themes` (tuple, **can hold more than one theme**), `subthemes`, `active`,
`notes`. There is no explicit country field on `TrackedCompany` itself —
`issuer_registry.py`'s `_LISTING_JURISDICTION_BY_EXCHANGE` dict (lines
40-47) derives a **listing-jurisdiction** label from `exchange` for display
purposes only, and explicitly documents that a handful of entries (Arm,
Nokia, Nebius, Tower Semiconductor) are foreign private issuers whose true
legal domicile differs from their listing exchange's country — this is
already flagged in each entry's own `notes`, not silently glossed over.

**Coverage by market** (verified by direct enumeration of
`TRACKED_COMPANIES`, 32 total):

| Market | Source | Count | Companies |
|---|---|---|---|
| United States | SEC EDGAR | 25 | NVIDIA, Micron, Coherent, Rockwell Automation, Rocket Lab, AMD, SanDisk, Lumentum, Intel, Arm Holdings, Applied Optoelectronics, Corning, Nebius, Penguin Solutions, Marvell, MaxLinear, Nokia, Tower Semiconductor, Aehr Test Systems, Trio-Tech, Navitas, Bloom Energy, indie Semiconductor, Arteris, CEVA |
| South Korea | OpenDART / DART | 2 | Samsung Electronics, SK Hynix |
| Japan | EDINET | 5 | SoftBank Group, Kioxia Holdings, Furukawa Electric, FANUC, ispace |
| **China** | *(none)* | **0** | *(no adapter, no tracked company, no discovery stub)* |

**Multi-theme membership exists and is real, not an edge case to special-case
away:** Samsung Electronics and SK Hynix are each tagged
`themes=("memory", "ai-buildout")`. A theme-grouped Market Map needs to
either show a company under each of its themes (duplicated tile) or pick one
"primary" theme per company for map purposes — this is a real design
decision the MVP spec below makes explicitly (see "Multi-theme handling").

**Gaps / ambiguities already known and self-documented in the repo** (not
new findings — surfaced again here because they matter for the Market Map's
grouping):
- Four companies are foreign-domiciled despite a US/Japan listing exchange
  (Arm, Nokia, Nebius, Tower Semiconductor) — see each entry's own `notes`.
- `Coverage`'s "Known category conflicts" section (`src/config/
  ontology.py::KNOWN_CATEGORY_CONFLICTS`, surfaced on the Coverage page)
  already documents two real, unresolved theme-classification disagreements
  (MRVL, TSEM) between this registry and an external portfolio-map seed
  list — worth being aware of if the Market Map ever needs a second-opinion
  classification source, but out of scope to resolve here.
- `DISCOVERY_STUBS` (21 unverified candidates, Coverage page) include five
  Taiwan-listed names with no active source adapter — not part of the
  active tracked universe, not eligible for a Market Map tile.

---

## D. Capital Rotation — exact current status

**Capital Rotation is 100% static demo data. It has never been live, is not
cached from a real fetch, and has no refresh mechanism of any kind.**

- Data source: `data/seed/rotation_metrics.json` — five fixed records (one
  per theme: `ai-buildout`, `humanoids`, `space`, `memory`, `photonics`),
  each with a hardcoded `relative_performance_pct`, `breadth_pct`,
  `leaders`/`laggards` (four of five have *empty* leaders/laggards lists —
  only `photonics` has `["DEMO"]`), a single fixed `as_of` timestamp
  (`"2026-08-15T00:00:00+00:00"` on every record, not "today" in any sense),
  and `is_demo: true` explicit on every record.
- `data/seed/README.md` (the repo's own seed-data index) labels this file
  in plain language: *"Demo Capital Rotation theme-level metrics."*
- Read path: `DemoMarketDataProvider._all()` →
  `src/data_access/loaders.load(settings, "rotation_metrics.json",
  default=[])` — a local file read, `st.cache_data`-cached like every other
  demo loader, with no network call, no external dependency, no refresh
  trigger.
- Wiring: `container.py:60` hardcodes this provider unconditionally — there
  is no `EDGE_*` setting, backend flag, or code path that would ever select
  a different implementation today, unlike `signal_repository`'s
  demo/live split.
- Computation on top of the raw records
  (`src/logic/theme_metrics.py`) is pure, honest aggregation with **no
  further data invention**: `rank_by_performance` (sort), `leaders_and_
  laggards` (top/bottom-N slice), `average_breadth` (mean). Every number a
  user sees is traceable straight back to the five hardcoded JSON records.
- "Live" cannot truthfully describe this module in its current form under
  any definition — not real-time, not delayed, not end-of-day-computed;
  it is a fixed illustrative snapshot dated in the past relative to "today."

**Smallest safe next step, if this is ever prioritized (not proposed for
this phase):** two independent, non-exclusive options, in order of effort:
1. **Honest relabeling only (near-zero engineering risk):** replace the
   current unlabeled/implied-current framing everywhere Capital Rotation
   renders (Dashboard's "Capital Rotation" panel, each theme's Rotation
   tab) with an explicit freshness line reusing the *already-built*
   `src/ui/components/freshness.py::freshness_chip("demo", timestamp=...)`
   component — sourcing `timestamp` from the JSON's own `as_of` field
   instead of "now," so the UI stops implying today's date without
   changing a single number. This is a pure display fix, reuses an existing
   shared component, and requires no new data source.
2. **Genuinely live (real engineering, out of scope for this phase):**
   would require selecting and contracting a market-data provider with
   verified coverage for all five themes' mapped companies across all
   three real markets (US/KR/JP — see section A's ticker-format caveats),
   adding real credentials/settings, a fetch+cache path analogous to the
   existing DART/EDGAR/EDINET scan-and-cache pattern, and a decision on
   whether `relative_performance_pct`/`breadth_pct` get redefined against
   a real benchmark or continue as an internally-consistent illustrative
   metric computed from real prices. This is Phase E2/E4 work, not this
   discovery pass.

No change to Capital Rotation's calculations or data is made in this phase.

---

## E. UI architecture — what a Market Map touches

**Current Dashboard structure** (`src/ui/pages/dashboard.py`, post Phase C):
`render()` calls, in order: `_render_todays_read`, `_render_theme_health`,
`_render_priority_signals`, `_render_rotation_snapshot`, `_render_catalysts`,
`_render_watchlist_changes`. No `st.divider()` between them (removed in
Phase C); each section carries its own spacing via `section_header()`'s
built-in margin.

**Components that already exist and are directly reusable, with zero new
pattern needed:**
- **Tile grid layout:** `st.columns(...)` + `st.container(border=True,
  key=...)` — the exact mechanism `_render_theme_health` already uses for
  its 5-card row (`dashboard.py:126-171`) and Themes' subtheme cards
  (`themes.py::_render_map_tab`). A theme-grouped Market Map is
  structurally the same pattern repeated per theme group, nothing new.
- **Movement/status display without color alone:** `direction_dot_html()`
  (`src/ui/components/badges.py`) already renders a glyph (▲/▼/●/◆) *plus*
  text label *plus* a restrained color accent — exactly the "clear movement
  labels, not color alone" requirement, already shared across Dashboard,
  Themes, Signals, Company.
- **Click-to-detail panel:** `st.dialog` via `src/ui/components/
  signal_drawer.py::open_signal_drawer()` is the established, working
  pattern for "click a compact item → modal with full detail, evidence
  clearly labeled by claim type, and handoff actions (Save to watchlist /
  Open filing / Ask about this)." A tile-detail dialog is the same
  primitive with different content and footer actions (Open company /
  Related filings·Radar Inbox / Ask Research) — no new interaction
  paradigm.
- **Evidence-type distinction (Verified fact / Interpretation /
  Uncertainty):** `src/ui/components/evidence_chips.py` already enforces
  this exact three(+one)-way distinction, including a fail-loud guard
  (`UnlinkedFactChipError`) against ever showing a "Fact" claim without
  real source attribution — directly reusable for "what may explain this
  move," including the explicit "No verified catalyst linked yet" state
  (render as `evidence_chip(ClaimType.UNCERTAINTY, ...)` or a plain muted
  line when no evidence exists at all — no new component needed).
- **Freshness line:** `src/ui/components/freshness.py::freshness_chip()`
  already renders exactly the "Live · …" / "Stale · …" / "Demo · …" pattern
  requested — a fourth state ("Delayed") or a "Price coverage not
  connected" variant would be a small, additive change to this one shared
  component, not a new one.
- **Regional brief as a compact tabbed view:** `st.tabs(["United States",
  "South Korea", "China", "Japan"])` is the exact mechanism Themes already
  uses for its outer theme selector and inner Map/Rotation/Companies/
  Catalysts selector — no new interaction pattern.
- **Capital Rotation relegated to secondary disclosure:** `st.expander(...)`
  is already the established pattern for exactly this kind of "important
  but not primary" content (Radar Inbox's "Ingestion status", Coverage's
  "Discovery queue"/"Known category conflicts"). Reusing it here is
  consistent, not a new idiom.

**What is explicitly *not* recommended, per the "avoid fragile CSS or
internal Streamlit DOM hacks" constraint:**
- An Altair/`st.altair_chart` heatmap (the dormant `src/ui/components/
  charts.py::rotation_bar_chart`, confirmed called from nowhere in the app
  today, is the one precedent for a charting library in this codebase).
  Charts can show hover tooltips reasonably well, but wiring a *click* on a
  chart mark back into a Streamlit detail dialog needs `st.altair_chart`'s
  newer `on_select` callback API, which is a materially different, less-
  battle-tested interaction model in this codebase than the plain
  `st.button` → `st.dialog` pattern already used everywhere else. A native
  grid of bordered containers/buttons is simpler, consistent with the rest
  of the app, and avoids introducing a second click-handling paradigm for
  one module.
- Any hand-rolled HTML/JS "heatmap" — the codebase's one JS-reliant trick
  (`src/ui/ui.py::_correct_sidebar_state_for_width`, an `st.iframe` used
  once for a specific sidebar-width correction) is explicitly documented as
  a narrow, single-purpose workaround, not a pattern to extend.

**Files/tests that would be touched by implementation** (not changed in
this pass — see "Files likely to change" below for the phase breakdown).

---

## Recommended MVP specification

### Market Map
- **Grouping:** by Eeva theme first, exactly as Dashboard's existing Theme
  Health row and Themes' own tab structure already group things — never a
  generic cross-theme sector heatmap.
- **Tiles:** one tile per `TrackedCompany`, grouped under its theme
  section(s). **Multi-theme handling:** show the company's tile under
  every theme it belongs to (Samsung/SK Hynix appear under both Memory and
  AI Buildout) rather than arbitrarily picking one "primary" theme —
  duplicating a tile is a smaller honesty compromise than silently hiding a
  real theme membership, and `TrackedCompany.themes` already models this as
  an ordered tuple ("primary first") if a future iteration wants to
  de-duplicate by primary theme instead.
- **Time window:** 1D/session only for v1, per the brief. No 5D/1M selector
  until real historical price history is verified and available — there is
  none today (section A), so this is a hard blocker for anything beyond 1D
  regardless of preference.
- **Sizing:** equal-size tiles within each theme section. No authoritative
  market-cap/liquidity metric exists today (`Ticker.market_cap_bucket` is a
  placeholder string, not a real number — section A), so any size-by-cap
  encoding would be fabricated. Equal sizing is the only honest MVP choice.
- **Movement value:** every tile shows a text value (`+2.4%` / `−1.8%`) —
  this is **not implementable today** without a real price source (section
  A). The MVP therefore cannot show real movement values; see Phase E1
  scope below for what *can* ship honestly in the meantime.
- **Freshness line:** given no price source exists, the only honest freshness
  states available today are **"Price coverage not connected"** (always,
  for now) — never "Live," "Delayed," or "Last close," since none of those
  are true yet.
- **Overflow/discoverability:** five theme sections × up to ~25 tiles
  (AI Buildout alone has the most names) risks a crowded page. Recommend
  each theme section show a small fixed number of tiles (e.g. its first
  4-6 by existing `TrackedCompany` order) plus a quiet "+N more — view all
  in Themes" link into that theme's existing Companies tab — reusing
  existing navigation, not a new "show all" control.

### Tile detail (dialog, reusing `st.dialog`)
Fields per the brief, mapped to what's honestly available today:
company (`TrackedCompany.name`), theme(s), market/exchange
(`TrackedCompany.exchange`, labeled "listing exchange" per
`issuer_registry.py`'s own convention), observed move + window (**not
available — omit or show "Price coverage not connected"**), freshness
(same), "what may explain this move" (query `SignalRepository
.get_all_signals()` / `EvidenceRepository` for this company's real,
date-stamped filings when they exist; render via the existing evidence-chip
distinction; show the exact copy **"No verified catalyst linked yet"**
when none exist — this is the common case today given real-signal coverage
is narrow), handoff actions (Open company → existing Company page
route with `?symbol=`; Related filings/Radar Inbox → existing Radar Inbox
page, optionally filtered by company via its existing Search filter;
Ask Research → existing Research page, pre-filling nothing that isn't
already user-typed, consistent with Research's current single-input
design from Phase C).

### Regional Market Brief
- Compact tabbed design (`st.tabs`), one tab per region, smaller footprint
  than the Market Map, per the brief.
- **Honest state per region given today's inputs:** United States, South
  Korea, and Japan can each show a real "unavailable" state that is at
  least *specific* (e.g. "No market-wide summary available — N tracked
  companies, filing coverage only" with a link to that region's filings via
  Radar Inbox's existing Source filter), because each has a real filing
  source and real tracked companies. **China cannot show anything beyond a
  flat "No current coverage" state** — there is no source, no tracked
  company, nothing to cite, and inventing content for it would violate the
  brief's own explicit prohibition. Do not fabricate parity across the four
  tabs.

### Capital Rotation placement
**Recommendation: a collapsed "Capital Rotation" `st.expander` below the
Market Map**, not a link-out to Themes' Rotation tab and not a separate
Dashboard section below the regional brief. Reasoning: it's genuinely
Dashboard-relevant cross-theme content (unlike a single theme's own
Rotation tab), the `st.expander` pattern is already established and
low-complexity (Radar Inbox, Coverage), and demoting it to "collapsed
disclosure" rather than relocating it to a different page preserves exactly
what the brief asks ("must remain available as secondary detail, not
deleted") without touching its calculations, data, or the per-theme
Rotation tab at all.

---

## Phased implementation plan

### Phase E1 — safe UI/data-model integration, verified capabilities only
Scope: theme-grouped Market Map **using only what's real today** — company
identity, theme membership, listing exchange, and real Signals/Radar
evidence for tile detail. No price data anywhere; every tile and the map's
own freshness line honestly say "Price coverage not connected." Capital
Rotation moves into a collapsed expander below the map, calculations
untouched. Regional Brief ships with the honest per-region states above
(US/KR/JP: filing-coverage-only note; China: no-coverage state) — no
invented summaries. Today's Read gets the two new CTA labels ("Explore
Market Map" / "Open Radar Inbox") pointing at the new map section and the
existing Radar Inbox page respectively.

Likely files:
- `src/ui/pages/dashboard.py` — new `_render_market_map()` /
  `_render_regional_brief()` functions; `_render_rotation_snapshot()`
  wrapped in `st.expander`; CTA label changes in `_render_todays_read()`.
- `src/ui/components/cards.py` or a new `src/ui/components/market_map.py`
  — tile rendering + detail-dialog helper (new component file preferred
  over growing `cards.py` further, consistent with this codebase's
  one-component-per-concern convention).
- `src/config/tracked_companies.py` — read-only; no changes needed, but
  worth confirming `get_tracked_companies_for_source`/theme-grouping
  helpers are sufficient or need one small pure grouping function in
  `src/logic/` (e.g. `theme_metrics.py` or a new `market_map.py` under
  `src/logic/`).
- `assets/styles.css` — at most a small addition if the existing
  `[class*="st-key-card-"]` treatment needs a grid-specific tweak; no new
  tokens.
- `tests/apptest_pages/dashboard_page.py` (harness, likely unchanged) and
  a new `tests/test_dashboard_market_map.py` or an addition to
  `tests/test_ui_audit_phase_c.py`/a new `test_ui_audit_phase_e.py`.
- `design/DECISIONS.md` — a short entry recording this phase's approval
  and scope, matching this repo's existing convention.

### Phase E2 — approved live quote integration (only if you approve going live)
Requires an explicit decision on: which provider, budget/licensing terms,
which markets it actually covers reliably (this discovery found *no*
existing evidence about any provider's Korea/Japan/China reliability — that
would need to be verified against the chosen provider's own documentation
before committing to it), the KR/JP ticker-suffix mapping work flagged in
section A, new settings/secrets (`EDGE_*` convention, added to `.env`/
`.streamlit/secrets.toml` generation exactly like DART/EDGAR/EDINET keys
already are), a cache/refresh strategy mirroring the existing scan-and-cache
pattern, and rate-limit handling mirroring the existing
`*RateLimitError` pattern. Not started in this phase.

### Phase E3 — approved regional-news integration (only if you approve going live)
Requires an explicit decision on a news/summary provider (or a rule for
composing regional briefs purely from real filing evidence, which is
already possible today for US/KR/JP without any new provider — see Phase
E1). If a genuine news provider is chosen, needs the same settings/secrets/
cache treatment as Phase E2, plus a decision on how to handle China's total
lack of a source (a provider might cover China market news even without a
filing-source adapter — that would need to be evaluated separately from the
CNINFO/HKEX filing-source gap). Not started in this phase.

### Phase E4 — make Capital Rotation live, or lock in its honest freshness model
If Phases E2's price integration lands and covers all five themes' mapped
companies broadly enough to compute a real relative-performance/breadth
read, recompute `CapitalRotationMetric` from real prices against a defined
benchmark (a new decision point — what benchmark, over what window) and
retire `rotation_metrics.json`. If live coverage turns out to be partial or
unreliable for some themes, the fallback is the "honest relabeling" option
from section D — keep the existing static values but never call them
current, always showing their real `as_of` date via the existing
`freshness_chip` component. Not started in this phase.

---

## Risks, licensing, coverage gaps, and open questions for you

- **No market-data provider has been evaluated or selected.** Any specific
  vendor recommendation would be a guess without your input on budget,
  latency needs, and which markets matter most — flagging this as a
  decision point rather than picking one.
- **China has zero source coverage today** — not a data-quality gap but a
  complete absence of adapter, tracked company, or cached data. Building
  real China coverage (CNINFO/HKEX filings and/or China market pricing) is
  a materially larger scope than the other three markets and would need
  its own explicit approval, likely its own phase.
- **Japan (EDINET) ticker-format mismatch** (section A) is a concrete,
  specific gap: the 5-character EDINET securities code stored today is not
  directly usable as a quote-lookup key for any provider I'm aware of
  without a verified transformation — this needs to be solved with real,
  checked data (the same "never hardcode without verification" discipline
  `tracked_companies.py`'s own docstring already applies to corp codes/CIKs),
  not assumed.
- **Multi-theme companies (Samsung, SK Hynix)** — this report recommends
  showing them under every theme they belong to; if you'd rather show each
  company under one "primary" theme only, that's a one-line policy choice
  in Phase E1's grouping function, not a data change.
- **Rate limits/ToS for any future quote or news provider** are unknown
  until one is chosen — no assumption is made here about what's
  permissible.
- **Open question:** should Phase E1's Market Map ship with *no* movement
  value shown at all (pure identity/theme/exchange tiles, price line
  omitted entirely), or should every tile explicitly render the string
  "Price coverage not connected" in the value slot so the UI's shape
  matches the eventual live version from day one? This report leans toward
  the latter (matches the brief's own suggested phrasing and previews the
  final shape), but it's a real design choice you may want to weigh in on.
- **Open question:** for the Regional Brief's US/KR/JP "filing coverage
  only" state, should it show real recent filing titles/dates directly
  (pulled from the same real Signals/Radar data the tile detail uses), or
  stay at the more conservative flat "no market-wide summary, N companies
  tracked" note this report defaulted to? The former is more useful but
  slightly more surface area to get right (translation state, freshness
  per item) for an MVP.

---

## Commit status

**No commit was made.** This document is the only file added in this pass
(`design/DASHBOARD_MARKET_MAP_PHASE_E.md`). No source, test, config,
dependency, or seed-data file was modified. `git status` at the time of
writing this report shows only this new, untracked file.
