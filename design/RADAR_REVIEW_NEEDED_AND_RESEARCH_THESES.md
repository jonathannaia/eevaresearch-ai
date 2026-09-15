# "Review Needed" Badge + Research Theses Live Evidence

Two independent, additive features. Neither changes what gets scanned,
admitted, or published — both are display-only.

## Part 1 — "Review needed" badge

### What it is

A small, quiet advisory chip (`er-chip er-chip-uncertainty` — the same
"genuinely incomplete, not wrong" visual treatment already used for
`RETRIEVAL_FAILURE_NOTE`, never the loud `er-tag-neg` pill this app
reserves for genuine failures) shown on a Radar card when
`filing_display.review_needed(filing)` returns `flagged=True`. Tooltip:
"Issuer or role unresolved. Evidence collected, mapping under review."
The filing is still fully published and readable — this is metadata
only, never a gate.

### When it appears, and why — an honest architectural finding

The task asked for three trigger conditions: ambiguous issuer identity
(Simmtech-style parent/subsidiary confusion), unresolved exchange/
ticker identifiers, and cases the discovery/candidate logic already
quarantines. A full audit of this codebase's own identity-resolution
code (`dart/corp_code_resolver.py`, `edinet/edinet_code_resolver.py`,
`edgar/cik_resolver.py`, `company_discovery/entity_resolution.py`)
found that **all three of these cases already result in complete
exclusion, upstream, before any `FilingEvent` or `CandidateSignal` is
ever created**:

- An ambiguous or unresolved company code (DART corp_code, EDINET
  code, EDGAR CIK) is never guessed — the company is simply left out of
  the scan entirely (`missing_krx_codes`/`ambiguous_codes`/
  `missing_tickers`), surfaced today only as a page-level readiness
  banner (`radar_inbox.py`), never per-filing.
- Company Discovery's own `QUARANTINED` status (the general mechanism
  the real Simmtech case triggered) keeps a candidate out of
  `tracked_companies.py` entirely — it never becomes a company Radar
  scans, so it can never produce a card either. Today it's visible only
  on a hidden, admin-only, direct-URL-only page
  (`company_discovery_admin.py`), never on Radar.

Given the guardrails for this task — no change to discovery/admission
logic, no relaxing of what gets scanned — there is no honest way to
make a per-card badge fire for these two specific cases without either
building new identity-resolution/fuzzy-matching logic (out of scope) or
weakening the exclusion that already handles them correctly (which
would make things worse, not better). **This is a property of the
existing fail-closed architecture, not a gap this PR leaves open.**

The one condition that genuinely can vary per already-persisted
`FilingEvent`, using only fields it already stores, with zero new
resolution logic: **its own stored `corp_code` (issuer code) or
`stock_code` (exchange/ticker) is blank.** Every entry in
`tracked_companies.py` has both fields populated by construction, and
every resolver above excludes rather than proceeds on an unresolved
code — so this is a real, currently-dormant safety net against a
future or malformed record, not something that fires against today's
registry. That's consistent with the task's own framing: a *rare*
badge, not a routine one.

### Files

`src/logic/filing_display.py` (`review_needed()`, `ReviewNeededFlag`,
`REVIEW_NEEDED_TOOLTIP`) → `src/ui/components/radar_status.py`
(`review_needed_tag_html()`) → `src/ui/components/radar_card.py`
(rendered next to the title). Tests: pure-function coverage in
`tests/test_filing_display.py`, end-to-end card-rendering proof
(including two synthetic ambiguous fixtures and confirmation that a
normal, fully-resolved filing never shows it) in
`tests/test_radar_card_public_contract.py`.

## Part 2 — Research Theses: theme tags + live evidence

### What already existed

`src/ui/pages/themes_research.py` was **not** a placeholder — it
already had a real, published-only list → detail flow, reading via
`backend_factory.get_theme_repository()`: a list of theme cards
(category/status badges, working thesis, evidence-direction chips,
evidence/company counts) and a rich detail view (question, working
thesis, why it matters, a manually-curated Evidence ledger, a company
map by role, what-could-change-the-view, what-to-watch-next).

### What this PR adds

Two things, both genuinely "live" (read fresh at render time, never
cached in the curated `ResearchTheme` record) and both built by reusing
existing identity conventions rather than any new matching/inference:

1. **Primary theme tags on the list card.** `ResearchTheme` has no
   sector-tag field itself (`ThemeCategory` is "Bottleneck"/"Demand
   shift"/"Second-order effect" — a research-framing category, not a
   sector). Tags are derived instead from the Thesis's own curated
   company map: for each mapped company that is a real tracked company,
   its `TrackedCompany.themes` (the same sector-slug vocabulary — e.g.
   "ai-buildout", "memory" — already used everywhere else in this app)
   is unioned in. `src/logic/theme_evidence.theme_tags_for_companies()`.

2. **A "Live evidence" section on the detail view**, clearly labeled
   and explained as distinct from the existing curated Evidence ledger
   above it: "Recent official filings and news items tagged to this
   thesis's companies — shown for context, not analyzed or scored."
   Pulls a handful (5 each) of the most recent Radar `FilingEvent`s and
   Daily News items (`NewsStory` + `EditorialStory`) whose company
   matches one of the Thesis's mapped companies — an **exact** match
   against the same `corp_name`/`company_name`/`matched_companies`
   identity fields this app already uses everywhere else, never a
   fuzzy lookup or a new scoring system. Shows title + link + date
   only, nothing else — no summary, no materiality tier, no admission
   reasoning. Empty state ("No recent evidence yet.") when a Thesis has
   no mapped companies, or none of them have recent activity.

Copy added at the top of the list page: "A Research Thesis connects
multiple filings and news items into an evidence-backed bottleneck,
demand shift, or second-order effect Eeva is tracking across
companies."

### How it connects Radar and Daily News

`src/logic/theme_evidence.py` is a pure filtering/sorting module — it
never fetches or queries anything itself. The page
(`themes_research.py`) reads through the exact same repository seams
Radar's own page and Daily News's own page already use
(`backend_factory.get_filing_event_repository()` for all three
providers, `daily_news_backend.get_daily_news_repository()` /
`get_editorial_story_repository()`), then hands the results to
`theme_evidence.py` to filter down to the Thesis's own companies and
sort newest-first. A `NewsStory` is only ever included when
`status == PUBLISHED` — the same gate `daily_news.py`'s own page
already applies before showing anything publicly.

### Files

`src/logic/theme_evidence.py` (new), `src/ui/pages/themes_research.py`.
Tests: pure-function coverage in `tests/test_theme_evidence.py`,
end-to-end list/detail rendering (tags present/absent, live evidence
present/excluded/empty) in `tests/test_themes_research_page.py`.
