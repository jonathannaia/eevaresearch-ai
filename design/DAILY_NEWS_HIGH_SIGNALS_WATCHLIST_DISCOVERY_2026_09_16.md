# Daily News High Signals / Watchlist Navigation — Technical & Product Discovery

**Status:** Read-only discovery. No code, test, model, configuration, UI, database, cache, or deployment file was modified. No mockup was created. No worker run, no live discovery, no persistence, no stash operation, no untracked artifact touched.

**Product direction under discovery** (given, not designed here): Daily News will eventually expose two primary tier views — **High Signals** (default, decision-oriented, highest-confidence/material/action-relevant) and **Watchlist** (monitoring view, Watchlist-tier only, not Background). Background remains secondary research, never presented as equivalent to either primary view.

---

## Part 1 — Implementation map (current state, end to end)

### 1.1 Routes, page/component ownership, navigation entry points

| Layer | File | Detail |
|---|---|---|
| Top-level routing | `app.py` | Uses Streamlit's native `st.navigation(list(pages.values()), position="hidden")` + `st.Page` objects (`_build_pages()`, `@st.cache_resource`). **Not** a custom URL router — no framework-level route params beyond Streamlit's own `url_path` per page and its native `st.query_params`. |
| Route registration for Daily News | `app.py:69-89` | `_RENDER_FNS["daily_news"] = daily_news.render`; `_URL_PATHS["daily_news"] = "daily-news"`. One single `st.Page`, one single URL (`/daily-news`), `visibility="visible"`, linked from `PRIMARY_NAV`. |
| Sidebar nav entry | `src/ui/ui.py:31-46` | `PRIMARY_NAV` includes `("daily_news", "Signals")` — the only sidebar link into this page today. |
| Page ownership | `src/ui/pages/daily_news.py` | Owns the entire route: company selector, unified issuer+editorial feed, tier bucketing (High Signal / Watchlist / Background), Background expander, translation controls. 543 lines, single `render()` entry point. |
| Card rendering (editorial) | `src/ui/components/editorial_coverage.py` | Owns `render_editorial_card()` and the two query functions (`get_visible_editorial_stories`, `get_editorial_stories_for_company`). `daily_news.py` calls into this component; the reverse never happens (explicit isolation discipline in this module's own docstring). |
| Other navigation entry points | `src/ui/components/recently_updated.py` (Dashboard "Recently Updated" footer link, bare `/daily-news`, no query params); `src/ui/components/command_palette.py` (indexes only Radar Signals — `signals.py` — **not** Daily News/editorial content at all). | No other page or component links into Daily News. |
| Admin/status route (separate, unrelated to this discovery) | `src/ui/pages/daily_news_admin.py`, registered hidden at `/daily-news-admin` | Shows scan/fetch/publish **operational** counts per feed — not user-facing tier counts. Out of scope for this product direction. |

**Finding:** there is exactly one route today (`/daily-news`), one render function, no sub-routes, no existing `st.query_params` usage anywhere in this page or its owned components. The unread/"last seen" mechanism (`LAST_SEEN_KEY`/`READ_IDS_KEY`, `src/ui/ui.py:96-97`) is used only by **Radar Signals** (`src/ui/pages/signals.py`) and the Dashboard — **not** by Daily News (`daily_news.py`) at all, despite the similar naming. A future implementer must not assume unread state exists for Daily News tiers; none does today.

### 1.2 Source of truth for editorial tier/materiality

- **Computed once, at persistence time**, by `src/data_access/daily_news/materiality_classification.py` (`classify_editorial_story()` for Lane B, `classify_issuer_story()` for Lane A) — never at render time, never an LLM score, never user-editable (confirmed via `NewsMaterialityTier`'s own docstring, `src/models/daily_news_models.py:43-67`).
- **Enum**: `NewsMaterialityTier(str, Enum)` — exactly three members, exact persisted string values:
  ```
  HIGH_SIGNAL = "High Signal"
  WATCHLIST   = "Watchlist"
  BACKGROUND  = "Background"
  ```
- **Persisted field**: `materiality_tier: NewsMaterialityTier | None = None` on both `NewsStory` and `EditorialStory` (`src/models/daily_news_models.py`). `None` means "persisted before this field existed, never reclassified" — confirmed, real, and currently reachable: `materiality_classification.py`'s own module docstring states records are **never reclassified automatically**.
- **Display-only fallback convention, duplicated in two places**: `daily_news.py::_effective_tier()` (line 156-163) and `recently_updated.py::_effective_editorial_tier()` (added in PR #63) both independently implement `item.materiality_tier or NewsMaterialityTier.WATCHLIST` — `None` defaults to Watchlist for **display purposes only**, the stored record is never written back to. This duplication is a deliberate, existing pattern in this codebase (see `recently_updated.py`'s own comment explaining why it doesn't import from a UI *page* module).

### 1.3 Query, sort, filter, render logic — High Signal / Watchlist / Background

| Step | Function | Behavior |
|---|---|---|
| Load issuer stories | `daily_news.py::_published_stories()` | Loads all `NewsStory`, applies cross-language canonical-story reconciliation (`daily_news_pipeline.select_canonical_stories()`), filters to `status == PUBLISHED`, sorts newest-first by `sources[0].published_at`. **No tier filtering at this step.** |
| Load editorial stories (All companies) | `editorial_coverage.get_visible_editorial_stories()` → `editorial_pipeline.select_visible_editorial_stories()` | 72h freshness, 5-per-source cap, 20-total cap, newest-first. **No tier filtering at this step either** — tier bucketing happens only at render time, in `daily_news.py::render()`. |
| Load editorial stories (one company) | `editorial_coverage.get_editorial_stories_for_company()` → `editorial_pipeline.select_visible_editorial_stories_for_company()` | Same freshness/per-source cap, no cross-company total cap, queries the **full** persisted store (not the shared top-20 list) so a company-matched story ranked outside the global cap still appears here. |
| Merge | `daily_news.py::_merge_feed_items()` | Presentation-only reverse-chronological merge of issuer + editorial lists, by each item's own `published_at`. Never re-derives either lane's inclusion decision. |
| Tier bucket | `daily_news.py::render()`, lines 502-512 | Single pass over the merged, already-ordered list; each item's `_effective_tier()` sorts it into `high_signal_items` / `watchlist_items` / `background_items`, **preserving each bucket's own relative chronological order** (never re-sorted per bucket). |
| Render | `render()`, lines 520-543 | High Signal: always has its own `section_header`, renders inline or shows `_NO_HIGH_SIGNAL_EMPTY_STATE` if empty. Watchlist: `section_header` rendered **only if non-empty** (no explicit empty state for Watchlist today — an important gap for a future dedicated Watchlist view, since "zero items" and "section doesn't exist yet" currently look identical). Background: `st.expander(f"Show Background ({len(background_items)})")`, rendered only if non-empty, collapsed by default. |
| Dashboard "Recently Updated" filter (separate surface, PR #63) | `recently_updated.py::_load_editorial_rows()` | Excludes Background-tier editorial rows entirely from the 5-row Dashboard preview; Watchlist/High Signal pass through unfiltered. **Issuer (Lane A) rows in `_load_daily_news_rows()` are NOT tier-filtered at all** — every published issuer story appears regardless of tier (a separate, already-known, explicitly-deferred gap from the prior live-card audit, unrelated to this discovery but relevant context for anyone touching tier display near this code again). |

### 1.4 URL, query-string, search, filter, translation, deep-link behavior

- **URL**: exactly one path, `/daily-news`. No tier, company, or item-level state is reflected in the URL today.
- **Query-string**: `daily_news.py` never reads or writes `st.query_params`. **Real precedent exists elsewhere in this codebase**, though: `src/ui/pages/themes_research.py` (`theme_id`), `src/ui/pages/research_cases.py` (`case_id`), and `src/ui/pages/theme_workspace.py` (`theme_id`) all use the identical pattern — `st.query_params.get("<param>", "").strip()` read at the top of `render()`, branching to an index view or a detail view within **one** registered `st.Page`, with `st.page_link(page, ..., query_params={...})` for deep links (also used generically by `empty_state()`'s own `action_query_params` parameter). This is the closest, most directly reusable existing convention for a future tier-as-query-parameter design.
- **Search**: the command palette (`command_palette.py`) indexes only Radar Signals — Daily News/editorial content has **no** search/palette entry today.
- **Company filter**: `st.selectbox("Companies", ...)` — plain widget state, **not reflected in the URL**, resets on a fresh page load/navigation. No theme, date, or source filter exists on this page at all today (despite matched themes/sources being tracked internally and shown as chips).
- **Translation**: on-demand, click-triggered, cached in `st.session_state` (never persisted) — `_do_translate_headline()`/`_toggle_show_translation()` for issuer cards (Korean/Japanese/French → English headline only, never the summary). Editorial cards have **no translate control** at all (`editorial_coverage.py`'s own docstring: every currently-registered editorial source is English-language).
- **Card/detail deep links**: **none exist today.** Each card renders inline via `st.container(key=f"card-issuer-{story.id}")` / `f"card-editorial-{story.id}"` — a Streamlit element key (DOM/session identity only), never a URL-addressable identity. The only outbound link per card is `[Read original source →]` to the **external publisher's own URL**, not an internal Eeva detail page. There is no `?story_id=` (or similar) pattern to preserve, unlike the `theme_id`/`case_id` precedent above — this is a genuine gap, not a hidden existing feature, if a future design wants to add one.

### 1.5 Counts

- Computed **fresh, in-process, on every render** — no caching (`@st.cache_data`) anywhere in `daily_news.py`, `editorial_coverage.py`, or the count sites in `render()`. Always current, never stale, never global (always scoped to whatever company/view is currently selected).
- Only **one** count is ever displayed to the user today: `len(background_items)` in the Background expander's own label. High Signal and Watchlist sections show **no** numeric count at all — only their section header (and, for Watchlist, an explanatory subtitle: "Relevant but early, unquantified, or not yet material.").
- `daily_news_admin.py` shows separate, unrelated **operational** counts (fetch/publish/suppress per source) — not a precedent for user-facing tier counts.

### 1.6 Background "Show Background (N)" — current implementation and empty states

- Implementation: `st.expander(f"Show Background ({len(background_items)})")`, `daily_news.py:537-543`. Rendered **only when `background_items` is non-empty** — when zero, no expander element exists at all (confirmed by `test_no_background_control_rendered_when_there_are_zero_background_items`, which asserts `len(at.expander) == 0`).
- No dedicated Background-only empty state exists (none is needed today, since the control itself only appears when non-empty).
- Items inside always render with an explicit `tier=NewsMaterialityTier.BACKGROUND` badge (`_tier_badge_html`), never ambiguous about their own tier once visible.

### 1.7 Dashboard-to-Daily-News links and assumptions

- `src/ui/components/recently_updated.py`'s footer: `st.page_link(daily_news_page, label="View all Signals →")` — a bare link to `/daily-news`, **no query parameters, no tier assumption**. This is the only Dashboard→Daily-News link in the codebase (`dashboard.py` itself contains no direct Daily-News link).
- Because this link carries no state, **it requires no change at all** if a future tier query parameter is added with a safe default (e.g. omission of the parameter means "default landing tier") — but it also means this link currently cannot deep-link a Dashboard visitor straight into, say, Watchlist; that would be a deliberate future enhancement, not a preserved behavior.

### 1.8 Test coverage inventory

| File | Coverage |
|---|---|
| `tests/test_daily_news_page.py` (1,325 lines, ~55 tests) | The authoritative route/tier/UI test file: unified feed merge/ordering, company selector and per-company fallback, tier bucketing (`test_high_signal_story_renders_in_the_default_section_with_its_badge`, `test_watchlist_story_renders_in_its_own_secondary_section_with_its_badge`, `test_background_story_is_excluded_from_the_default_view_and_only_reachable_via_the_toggle`, `test_no_background_control_rendered_when_there_are_zero_background_items`, `test_legacy_unclassified_story_defaults_to_watchlist_for_display_only`, `test_no_high_signal_items_shows_the_exact_approved_empty_state`, `test_mixed_tier_items_each_render_in_their_own_section_in_one_page`), translation (`test_french_item_with_no_translation_shows_explicit_language_and_translate_control`, `test_french_item_translate_click_shows_clearly_labeled_title_translation`, `test_english_item_shows_no_language_indicator_or_translate_control`), historical fallback, canonical-story dedup. Uses the real `AppTest` harness (`tests/apptest_pages/daily_news_page.py`) for end-to-end proofs, plus pure-function unit tests for the merge/freshness helpers. |
| `tests/test_editorial_coverage_component.py` | Card-rendering and the two query functions (`get_visible_editorial_stories`, `get_editorial_stories_for_company`) in isolation. |
| `tests/test_dashboard_latest_item.py` | Dashboard "Recently Updated" ordering/exclusion, including the PR #63 tier filter (Background excluded, Watchlist/High Signal included, Radar/filing rows unaffected). |
| `tests/test_recently_updated_translation.py` | The Dashboard's own separate translate-control coverage (filing rows + issuer rows only — editorial rows have no translate control anywhere). |
| `tests/test_daily_news_materiality_classification.py` | The tier-assignment **logic itself** (Gates A–D), not UI — the true source-of-truth tests. |
| No existing test anywhere exercises `st.query_params` for Daily News (none exists to test), a tier-specific route, or a card/item deep link — confirming §1.4's finding that none of this exists today. |

---

## Part 2 — Product decisions: current behavior, constraints, options, recommendation

### 2.1 Navigation form

**Current behavior:** one route, one page, in-page section-based tier display (§1.1, §1.3).
**Constraints:** `st.navigation`/`st.Page` is Streamlit-native, page-set-cached via `@st.cache_resource` (`app.py:109`, with an explicit prior "sidebar navigation silently broke" bug this caching exists to prevent — any new route-registration approach must respect that caching contract). No existing precedent in this codebase for **nested** `st.Page` sub-routes under one conceptual page — every existing `st.Page` maps to a genuinely separate top-level destination. Real precedent **does** exist for query-parameter-driven index/detail state within one page (§1.4).

| Option | Description | Fit with existing codebase |
|---|---|---|
| A. Nested routes (`/daily-news/high-signals`, `/daily-news/watchlist`) | Two new `st.Page` entries, new `url_path`s, likely both still calling into a shared `daily_news.py` render function parameterized by tier | No precedent for this shape anywhere in the app; would need new sidebar/nav-group UI (this app's sidebar is a flat list, not a tree) and a decision on whether the old `/daily-news` URL becomes a redirect, an alias, or a third live route |
| B. One route, tier query parameter (`/daily-news?tier=high_signal`) | Extends the existing single `/daily-news` `st.Page`; `render()` reads `st.query_params.get("tier", ...)` at the top, same shape as `theme_id`/`case_id` | **Directly matches an existing, tested, three-times-repeated codebase convention** (§1.4) — lowest implementation risk, smallest diff, most consistent with how this app already does index/detail (or here, view-A/view-B) state |
| C. Tab state with a canonical deep-link strategy (in-page tabs, e.g. `st.tabs` or custom pill toggle, synced to a query parameter for deep-linkability) | A visual tab widget whose active tab is driven by (and writes back to) the same query parameter as option B | Combines B's URL mechanism with a different visual affordance than a bare page reload; `st.tabs` itself does not natively sync to `st.query_params` in Streamlit, so this still needs B's own read/write logic underneath — effectively B plus a UI polish layer |

**Recommendation:** **Option B** (or C as a visual refinement of B) — reuses an established, tested, three-times-precedented pattern exactly, requires no new `st.Page`/sidebar-nav-tree work, and keeps the existing single `/daily-news` URL fully intact by construction (the parameter is additive, never a path change).

### 2.2 Default landing state

**Current behavior:** the existing single page's default view is effectively "High Signal prominent, Watchlist visible below it, Background collapsed" — all three always present on one page load, no "landing tier" concept exists yet.
**Constraints:** the stated product direction explicitly names High Signals as "the default decision-oriented view."

| Option | Description |
|---|---|
| A. High Signals only, by default | A bare `/daily-news` (no query parameter) shows only the High Signals view; Watchlist requires either a query parameter or an in-page nav click |
| B. All non-Background editorial items (today's current combined behavior), unchanged | Preserves exact current behavior as the default; the two new "primary views" become additional, more focused query-parameter states layered on top, not a replacement of the default |
| C. Preserve legacy default with an explicit migration path | Same as B short-term, with an explicit, documented plan (and a tracked follow-up ticket) for when the default switches to A |

**Recommendation:** **Option C** — matches the stated product intent (High Signals as the eventual default) while respecting this session's own established discipline of narrow, incremental changes with explicit approval gates; a hard cutover to Option A in the same change that introduces the two-view split would combine a navigation-structure change with a default-behavior change, compounding risk and making a regression harder to isolate.

### 2.3 Background handling

**Current behavior:** in-page `st.expander`, collapsed by default, rendered only when non-empty, inside the same single page (§1.6).
**Constraints:** the product direction explicitly says Background "must not be presented as equivalent to either primary view" but also "remains available."

| Option | Description |
|---|---|
| A. Retained expander, now living inside (or beside) the Watchlist view specifically | Smallest change from today; Background stays visually and structurally subordinate, physically near Watchlist rather than under whichever tier happens to render last |
| B. Separate secondary view/route (e.g. its own query-parameter value or its own hidden-from-nav route, mirroring how Radar Signals/Methodology/About are already "hidden but reachable") | Matches this codebase's own existing "hidden but reachable, not linked from primary nav" pattern (`HIDDEN_FROM_NAV`, §1.1) exactly — Background would never appear in primary navigation but stays a real, directly-linkable page |
| C. Hidden by default, reachable only through a filtered/search state (e.g. a company filter or future search query surfaces Background items when nothing else matches) | Requires new filter/search infrastructure that doesn't exist yet (§1.4); highest implementation cost of the three |

**Recommendation:** **Option A or B**, not C. Given the discovery task's own instruction that "Background stays in an expander or separate secondary view" is itself an open question rather than a settled answer, and that this codebase already has a real, tested precedent for "hidden but reachable" routes (Option B) as well as a real, tested precedent for the current expander (Option A), either is low-risk; C requires building new infrastructure this discovery found no trace of and should not be the first choice.

### 2.4 Filters and search

**Current behavior:** one filter exists today — the company selector — and it is **not** tier-aware; selecting a company shows that company's items across all three tiers on the one current page. No theme, date, or source filter exists. No search exists for Daily News content.
**Constraints:** none of "filters persist across views," "zero-match behavior," or "Background deep link ownership" have any existing implementation to inspect — this section is pure forward design space.

| Open question | Current-state finding | Options (not decided here) |
|---|---|---|
| Do filters persist across High Signals and Watchlist? | No cross-view filter state exists today to persist or not persist | (a) filter state lives in the shared query-parameter/URL and survives a tier switch; (b) filter state is view-local and resets on tier switch; (c) filter state lives in `st.session_state` only (survives tier switch within a session, lost on a fresh URL load) |
| Zero-match behavior | The existing `empty_state()` component already supports a bridging CTA to another page/query-param state (§1.7's own `empty_state.py` inspection: `action_page`/`action_query_params`) | Reuse `empty_state()`'s existing two-action-slot capability (`action_label`/`action_label_2`) to bridge "no results in this tier" to the other tier or to Background |
| Which tier owns a direct link to a specific Background card | No card-level deep link exists at all today (§1.4) — this presumes new infrastructure | Deferred to whichever Background-handling option (§2.3) is chosen; a card-level deep link is a separate feature this discovery did not find any existing precedent for |

**No recommendation given** — this section is intentionally left as discovery-only per the task's own framing (filters/search are explicitly future scope, not part of the two-primary-view navigation change itself).

### 2.5 Counts, headings, and empty states

**Current behavior:** only Background shows a count (§1.5); High Signal has one dedicated approved empty-state string (`_NO_HIGH_SIGNAL_EMPTY_STATE`); Watchlist has **no** empty-state copy at all today (its section header simply doesn't render when empty).

| Option | Description |
|---|---|
| A. Tier-specific counts everywhere (High Signals shows "N items," Watchlist shows "N items," Background keeps its existing count) | Most informative; requires new copy/UI for two sections that currently show none |
| B. Global counts only (e.g. a badge on the nav entry itself, unread-style) | Would be the first tier-aware count anywhere outside the Background expander; note §1.1's finding that Daily News has **no** existing unread/badge mechanism to build on (that infrastructure belongs to Radar Signals only) |
| C. No numeric counts beyond what exists today; rely on empty-state copy alone for the zero case | Smallest change; consistent with the current High-Signal/Watchlist asymmetry, just needs Watchlist's own missing empty-state copy added |

**Recommendation:** **Option C** as the minimal first step (write the missing Watchlist empty-state copy, reuse the existing pattern), with Option A as a natural, separable follow-up once the two-view split itself is stable — bundling new count UI into the same change as the navigation restructure increases the surface area of a single PR without a clear necessity.

### 2.6 Compatibility

| Requirement | Current-state finding | Preservation approach |
|---|---|---|
| Preserving current `/daily-news` URLs | One URL, `app.py:83` (`"daily_news": "daily-news"`) | Under §2.1's recommended Option B, this URL is never renamed or redirected — only an optional query parameter is added |
| Preserving Dashboard links | One link, bare, no query params (§1.7) | Requires zero change — a query-parameter-based tier state with a safe default (§2.2) means the existing bare link continues to land on exactly the current default behavior |
| Preserving external/bookmarked card/detail URLs | **None exist today** (§1.4) — there is nothing to preserve at the card level | Not a compatibility constraint; a future card-deep-link feature is purely additive, not a migration |
| Preserving translation and existing card actions | On-demand, session-scoped, click-triggered (§1.4) | Entirely orthogonal to navigation structure — the translate button/toggle logic lives inside `_render_card()`/`render_editorial_card()`, which a tier-split would call unchanged; no interaction with routing or query parameters at all |

### 2.7 Tier semantics

- **Exact persisted values, reconfirmed**: `"High Signal"`, `"Watchlist"`, `"Background"` (§1.2). The product direction's own phrasing ("High Signals," plural) is a **display-label** difference only, not a data-model difference — no enum-value change is implied or required.
- **Migration requirement**: **not required for the values themselves.** What *is* already true, and must be explicitly decided (not assumed) by any future implementation: `materiality_tier` can be `None` on records persisted before the field existed, and this is **not hypothetical** — it is an existing, tested, currently-live code path (`test_legacy_unclassified_story_defaults_to_watchlist_for_display_only`). A two-primary-view design must explicitly decide whether a `None`-tier record continues to default into Watchlist (today's convention, zero migration needed) or whether a real backfill/reclassification pass is run first (a deliberate, separate, larger decision — `materiality_classification.py`'s own docstring is explicit that automatic reclassification does not happen today and would be a new capability, not a bug fix).

---

## Part 3 — Deliverables

### 3.1 Implementation map — exact files

**Production files that would very likely be touched by a future implementation** (none touched in this discovery):
- `src/ui/pages/daily_news.py` — tier bucketing, section rendering, company selector, render() entry point
- `src/ui/components/editorial_coverage.py` — card rendering, query functions
- `app.py` — route/`st.Page`/`url_path` registration if any new route form is chosen (§2.1 option A) or left untouched (§2.1 option B/C)
- `src/ui/components/recently_updated.py` — only if the Dashboard link should start deep-linking into a specific tier (not required for compatibility, §2.6)
- `src/ui/components/empty_state.py` — only if a new empty-state shape (e.g. a third action slot) is needed beyond the existing two-action support
- `src/models/daily_news_models.py` — only if tier semantics themselves change (§2.7 concludes they should not need to)

**Test files that would very likely need new or updated coverage:**
- `tests/test_daily_news_page.py` — the authoritative existing suite; almost every tier-display test in it would need re-verification under whatever navigation form is chosen
- `tests/apptest_pages/daily_news_page.py` — the AppTest harness; may need a tier-aware variant if query-parameter-driven state is added
- `tests/test_editorial_coverage_component.py` — only if card rendering itself changes
- `tests/test_dashboard_latest_item.py` — only if the Dashboard link's target behavior changes (§2.6 concludes it should not need to)

**Explicitly not expected to change:** `src/data_access/daily_news/materiality_classification.py`, `editorial_admission.py`, `editorial_pipeline.py`, `daily_news_pipeline.py` (materiality/admission logic itself is out of scope for a navigation change), `src/data_access/daily_news/source_registry.py`, any parser/worker/CLI file.

### 3.2 Route/state decision matrix

| Topic | Current state | Recommended direction | Confidence |
|---|---|---|---|
| Navigation form | One route, in-page sections | Query parameter on the existing route (§2.1-B) | High — directly precedented three times already |
| Default landing state | Combined view, no "landing tier" | Preserve current default short-term, explicit migration path to High-Signals-default later (§2.2-C) | Medium — depends on product timing, not a technical constraint |
| Background handling | In-page expander | Either retained expander (near Watchlist) or a "hidden but reachable" route, both precedented (§2.3-A/B) | Medium — genuinely open per the task's own framing |
| Filters/search | Company-only, not tier-aware, no persistence question exists yet | No recommendation — pure forward design space (§2.4) | Low — insufficient existing precedent to anchor a recommendation |
| Counts/headings/empty states | Only Background has a count; Watchlist has no empty-state copy | Add missing Watchlist empty-state copy first; defer new count UI (§2.5-C) | High — smallest necessary change, existing pattern to copy |
| Compatibility | One URL, one bare Dashboard link, no card deep links | Fully preservable under §2.1-B by construction | High |
| Tier semantics | Three enum values, `None` fallback already exists and is tested | No schema/enum change; explicit decision needed on `None`-record handling only | High on "no migration needed"; the `None`-handling decision itself is open |

### 3.3 Recommended information architecture and rationale

A **single route, query-parameter-driven** architecture (`/daily-news` plus an optional `?tier=` parameter) is recommended over nested routes, because:
1. It is the only option with real, repeated, tested precedent in this exact codebase (`theme_id`, `case_id` — three independent existing call sites).
2. It requires zero change to `app.py`'s `st.Page`/`st.cache_resource` registration, the single highest-risk piece of infrastructure this discovery found (its own comments describe a real, previously-fixed navigation-breaking bug tied to how pages are cached).
3. It preserves the existing single URL and the existing bare Dashboard link with zero required changes (§2.6).
4. It keeps Background's "not equivalent to a primary view" requirement satisfiable either way (§2.3) without forcing a route-count decision now.

### 3.4 Required backward-compatibility behavior

- `/daily-news` (no query parameter) must continue to resolve to a real, working page — never a 404 or a forced redirect that breaks an existing bookmark.
- The existing bare `st.page_link(daily_news_page, label="View all Signals →")` in `recently_updated.py` must continue to work unmodified.
- The existing company selector, translation controls, and per-card "Read original source" links must continue to function identically regardless of which tier view is active.
- No existing `NewsStory`/`EditorialStory` persisted record, including one with `materiality_tier=None`, may be silently dropped from all views — its fallback-tier assignment (today: Watchlist) must be an explicit, documented decision if changed, not an incidental side effect of the navigation restructure.
- The existing Background expander/behavior must not regress for any user who has not yet been exposed to the new two-view navigation (relevant if a phased rollout is considered).

### 3.5 Acceptance-criteria draft for a later implementation PR

*(Draft only — not a commitment, not scoped to any specific PR.)*
1. A visitor landing on `/daily-news` with no query parameter sees the same effective default behavior as today (or the explicitly-approved new default, if that migration is separately authorized).
2. `/daily-news?tier=high_signal` (or the chosen parameter shape) shows only High-Signal-tier items, newest first, with the existing empty-state copy when none exist.
3. `/daily-news?tier=watchlist` (or equivalent) shows only Watchlist-tier items, newest first, with new, approved empty-state copy when none exist (§2.5).
4. Background items never appear in either primary view; they remain reachable exactly as approved in §2.3.
5. An invalid/unrecognized `tier` value degrades safely to the default view, never an exception or a blank page.
6. The company selector, translation controls, and card actions behave identically in both tier views.
7. `recently_updated.py`'s existing Dashboard link continues to work with zero required changes.
8. A record with `materiality_tier=None` renders in the tier explicitly decided per §2.7 (today: Watchlist), consistently across both the page and any other surface that reads tier.
9. All existing tests in `tests/test_daily_news_page.py` pass unmodified or are updated with an explicit, reviewed reason tied to the approved design (never silently weakened).

### 3.6 Narrow risk register

| Risk | Description | Mitigation direction |
|---|---|---|
| Routing/deep links | A malformed or stale `?tier=` value in an old bookmark/shared link could error or silently show the wrong content | Explicit default-on-invalid-value handling (acceptance criterion 5) |
| Filter state | Company selector (and any future filter) losing its value across a tier switch could look like a bug even if "intended" per §2.4's open question | Decide and document filter-persistence behavior explicitly before implementation, not implicitly via whatever falls out of the chosen mechanism |
| Translations | Translation state is `st.session_state`-scoped per story id; a tier-view change must not accidentally reset or duplicate this state for a story visible in both a "before" and "after" view during a transition period | Keep translation keys keyed by `story.id` only (already true, §1.4) — no change needed if this convention is preserved |
| Stale records | A `None`-tier or very old record could behave inconsistently between the Signals page and the Dashboard if the two surfaces' fallback conventions ever diverge | Keep `_effective_tier()`-equivalent logic identical (or shared) across every surface that reads tier — already a near-duplication risk today between `daily_news.py` and `recently_updated.py` (§1.2) |
| Performance/query behavior | Splitting one page into two query states, if implemented naively, could double the underlying repository reads (once per tier, on every navigation) instead of reading once and filtering in memory | Preserve the existing "load once, bucket in memory" pattern (§1.3) — never re-query the repository per tier |
| User confusion | A visitor who has bookmarked or muscle-memorized "Daily News = one page with three sections" could be confused by a sudden split, especially if the default view changes at the same time | Favor §2.2's phased approach (preserve legacy default first) to decouple "navigation restructure" from "default behavior change" as two separately-reviewable decisions |

### 3.7 Explicit out-of-scope list

- Any schema/migration change to `NewsMaterialityTier` or the persisted `materiality_tier` field.
- Any change to `materiality_classification.py`, `editorial_admission.py`, `editorial_pipeline.py`, or `daily_news_pipeline.py` — this discovery is navigation/presentation only.
- Any new search, date filter, theme filter, or source filter capability (§2.4 is discovery-only, no recommendation).
- Any card/item-level deep-link feature (no existing precedent; a separate future feature, not implied by this discovery).
- Any unread/badge/notification mechanism for Daily News (none exists; would be new infrastructure, not a preservation concern).
- Any change to Radar Signals, Radar Inbox, filings, EDGAR/EDINET/DART, source registries, parsers, workers, polling cadence, or deployment configuration.
- Any mockup, wireframe, or visual design artifact — none was created for this discovery.

---

## Files inspected during this discovery

```
src/ui/pages/daily_news.py
src/ui/ui.py
app.py
src/ui/components/editorial_coverage.py
src/ui/components/recently_updated.py
src/ui/components/empty_state.py
src/ui/components/command_palette.py (grep only)
src/ui/pages/dashboard.py (grep only)
src/ui/pages/daily_news_admin.py (grep only)
src/ui/pages/themes_research.py (query-param precedent)
src/models/daily_news_models.py
tests/test_daily_news_page.py
tests/apptest_pages/daily_news_page.py
tests/test_editorial_coverage_component.py (listed, cross-referenced)
tests/test_dashboard_latest_item.py (listed, cross-referenced)
tests/test_recently_updated_translation.py (listed, cross-referenced)
tests/test_daily_news_materiality_classification.py (listed, cross-referenced)
```

---

*No file other than this new document was created, modified, staged, committed, or pushed during this discovery. No worker was run; no live discovery, cache/database write, or persistence occurred; no source registry, parser, or deployment configuration was touched; no stash was created, dropped, applied, or modified; no pre-existing untracked file was altered.*
