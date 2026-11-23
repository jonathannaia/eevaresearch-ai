# Daily News High Signals / Watchlist — Implementation Plan

**Status:** Read-only plan. No code, test, model, route, data, source configuration, worker behavior, or existing design document was modified. No mockup created. No worker run, no live discovery, no cache/database access, no staging/commit/push/PR/merge/deploy, no stash operation.

**Builds on:** `design/DAILY_NEWS_HIGH_SIGNALS_WATCHLIST_DISCOVERY_2026_09_16.md` (all file/function names, current behavior, and precedent citations below are taken directly from that discovery's own findings, re-verified where noted).

**Product decision this plan targets** (given, not re-derived):
```
Canonical page: /daily-news
Canonical tier URLs: /daily-news?tier=high_signal, /daily-news?tier=watchlist
Legacy /daily-news (no tier): resolves to High Signals; URL updates to ?tier=high_signal if safe
High Signals: materiality_tier == "High Signal"
Watchlist:    materiality_tier == "Watchlist" OR materiality_tier is None
Background:   secondary, inside Watchlist only, collapsed, never equivalent to a primary view
```

---

## 0. Precedent functions confirmed before specifying implementation

Per the plan constraints, every existing `st.query_params` usage in this repository was re-inspected directly (not merely cited from memory) before writing §1 below:

- **Reading**: `src/ui/pages/themes_research.py:157` (`theme_id = st.query_params.get("theme_id", "").strip()`), `src/ui/pages/research_cases.py:121`, `src/ui/pages/theme_workspace.py:357` — all three use the identical `st.query_params.get("<param>", "").strip()` shape, read once at the top of `render()`.
- **Deep-linking (writing, but only from a *different* page via a link, never from within the target page's own `render()`)**: `st.page_link(page, ..., query_params={"theme_id": theme.id})` — `themes_research.py:279`, `research_cases.py:174`, `theme_workspace.py:408`, and `src/ui/components/empty_state.py:36/42` (`action_query_params`).
- **Testing**: `at.query_params["theme_id"] = theme_id` set before `at.run()` — confirmed live in `tests/test_themes_research_page.py:123`, `tests/test_theme_workspace_page.py:518/560/577/595/606`, `tests/test_research_cases_page.py:164/227`, `tests/test_dashboard_data_integrity.py:405`. Directly re-verified (not merely cited) that `AppTest.query_params` supports item-assignment before `.run()` and that this is the exact mechanism already exercised in this codebase.

**Explicit gap, not precedent-covered:** no existing page in this codebase *writes* to `st.query_params` from within its own `render()` (the legacy-URL-normalization requirement below) or synchronizes a widget's selection to a query parameter both ways. Both are new patterns for this codebase, designed in §1.1–§1.3 using Streamlit's documented, standard mechanisms — not copied from an existing call site, because none exists. This distinction is called out explicitly per the discovery document's own finding (§1.4 of the discovery) and is not glossed over here.

**Exact tier strings, reconfirmed directly from the model and current page code** (`src/models/daily_news_models.py:65-67`, `src/ui/pages/daily_news.py:148-151,156-163`):
```
NewsMaterialityTier.HIGH_SIGNAL.value == "High Signal"
NewsMaterialityTier.WATCHLIST.value   == "Watchlist"
NewsMaterialityTier.BACKGROUND.value  == "Background"
```
These are **preserved exactly, unchanged** — this plan introduces new, separate, URL-facing string constants (`"high_signal"`, `"watchlist"`) for the query parameter's own values (see §1.1), never reusing or aliasing the model's own `.value` strings for the URL, and never changing the enum itself.

---

## 1. Required plan detail

### 1.1 Reading, validating, and setting `st.query_params["tier"]`

**New module-level constants in `src/ui/pages/daily_news.py`:**
```python
_TIER_QUERY_PARAM = "tier"
_HIGH_SIGNALS_QUERY_VALUE = "high_signal"
_WATCHLIST_QUERY_VALUE = "watchlist"
_DEFAULT_TIER_QUERY_VALUE = _HIGH_SIGNALS_QUERY_VALUE
_VALID_TIER_QUERY_VALUES = (_HIGH_SIGNALS_QUERY_VALUE, _WATCHLIST_QUERY_VALUE)
```

**New function `_resolve_active_tier_view() -> str`** (called once, at the top of `render()`, before any story is loaded):
```python
def _resolve_active_tier_view() -> str:
    raw = st.query_params.get(_TIER_QUERY_PARAM, "").strip()
    if raw not in _VALID_TIER_QUERY_VALUES:
        if raw != _DEFAULT_TIER_QUERY_VALUE:
            st.query_params[_TIER_QUERY_PARAM] = _DEFAULT_TIER_QUERY_VALUE
        return _DEFAULT_TIER_QUERY_VALUE
    return raw
```
Returns exactly one of the two canonical query values — never the raw, unvalidated string — so every caller downstream (the tier-switch widget, the section-rendering branch) works with a closed, already-validated two-value set.

### 1.2 Handling missing, invalid, repeated, or unsupported `tier` values

- **Missing** (`?` with no `tier` key, or bare `/daily-news`): `st.query_params.get(_TIER_QUERY_PARAM, "")` returns `""`, which is not in `_VALID_TIER_QUERY_VALUES` → normalizes to `_DEFAULT_TIER_QUERY_VALUE` (High Signals), URL is updated to `?tier=high_signal`.
- **Invalid** (`?tier=xyz`, `?tier=HIGH_SIGNAL`, `?tier=High%20Signal`, case-sensitive mismatches, or any string not exactly `"high_signal"`/`"watchlist"`): same path as missing — normalizes to High Signals. No partial-match/fuzzy handling is introduced (smallest additive change, and avoids ambiguous behavior for an unanticipated future value).
- **Repeated** (`?tier=watchlist&tier=high_signal`): resolved by Streamlit's own `QueryParams.get()`, which returns a single value for a repeated key (the last occurrence, per Streamlit's own documented `QueryParams` behavior) — whatever single string that resolves to is then validated exactly as any other raw value above. This plan does not special-case repetition; it is fully covered by the existing missing/invalid path once `.get()` has already collapsed it to one string.
- **Unsupported** (any value not one of the two current tiers, including a plausible-looking future value like `"background"`): treated identically to invalid — Background is never a directly-addressable primary tier value by design (§0's product decision: Background lives only inside Watchlist).

### 1.3 Preventing rerun loops when normalizing legacy URLs

Streamlit triggers exactly one automatic rerun when `st.query_params` is mutated during normal script execution (the same mechanism as a widget-state change). `_resolve_active_tier_view()`'s own guard — `if raw != _DEFAULT_TIER_QUERY_VALUE: st.query_params[...] = ...` — writes **only when the current raw value differs from the target**. Trace: visit `/daily-news` (raw `""`) → write `"high_signal"` → exactly one rerun → this second execution reads raw `"high_signal"`, which **is** in `_VALID_TIER_QUERY_VALUES`, so the function returns immediately without writing again. At most one extra rerun per genuine legacy/invalid visit, never a loop, because the guard's own condition becomes false the moment the write has happened. The widget-driven write path (§1.4) uses the identical "only write if different" discipline for the same reason, even though a genuine user click can't itself produce a no-op write.

### 1.4 Rendering a two-option High Signals / Watchlist control with URL-synchronized state

**Widget choice:** `st.segmented_control` — confirmed available (Streamlit `1.61.1` installed, pinned `>=1.38,<2.0` in `requirements.txt`; the widget shipped in `1.36`), not yet used elsewhere in this codebase (`st.tabs` is used in `regional_brief.py`/`theme_workspace.py` but has no programmatic index/selection control or `on_change`, so it cannot be URL-synchronized — confirmed by direct inspection, not assumed).

**Critical implementation detail — avoiding stale widget state overriding the URL:** a `st.segmented_control` with a fixed `key` persists its own selection in `st.session_state` across reruns, which would let a manually-edited URL (e.g. typing `?tier=watchlist` into the address bar while the widget's session state still holds "High Signals" from an earlier click) be silently overridden by the stale widget state instead of reflecting the URL — the URL must remain the single source of truth. **Mitigation: key the widget off the currently-resolved tier value itself**, so a genuine change in resolved tier (from either the URL or a click) always produces a fresh widget instance rather than reusing stale state:
```python
_TIER_SWITCH_LABELS = {"high_signal": "High Signals", "watchlist": "Watchlist"}
_TIER_SWITCH_VALUE_BY_LABEL = {v: k for k, v in _TIER_SWITCH_LABELS.items()}

def _handle_tier_switch_change(widget_key: str) -> None:
    selected_label = st.session_state[widget_key]
    new_value = _TIER_SWITCH_VALUE_BY_LABEL[selected_label]
    if st.query_params.get(_TIER_QUERY_PARAM, "") != new_value:
        st.query_params[_TIER_QUERY_PARAM] = new_value

def _render_tier_switch(active_tier_query_value: str) -> None:
    widget_key = f"daily-news-tier-switch-{active_tier_query_value}"
    st.segmented_control(
        "View", options=list(_TIER_SWITCH_LABELS.values()),
        default=_TIER_SWITCH_LABELS[active_tier_query_value],
        key=widget_key, on_change=_handle_tier_switch_change, args=(widget_key,),
        label_visibility="collapsed",
    )
```
A click sets `st.session_state[widget_key]`, Streamlit runs `_handle_tier_switch_change` as part of the same rerun the click already triggers (a callback's own `st.query_params` mutation does not add a *second* rerun — it takes effect before the main script body resumes in that same cycle), and the main script body then re-derives `active_tier_view` from the now-updated URL on its next natural pass. This mirrors the existing on_click/on_change pattern already used in this exact file for the translate toggle (`_toggle_show_translation`, `daily_news.py:306-308`).

### 1.5 Applying existing filters after tier partitioning

Only one filter exists today: the company selector (`st.selectbox("Companies", ...)`, `daily_news.py:409`). Theme, date, source, and search filters do not exist anywhere on this page today (confirmed in the discovery document, §1.4/§2.4) — **none is introduced by this plan**; the acceptance criteria's "filters/search behave correctly within the active tier" is scoped to the one real, existing filter.

**No reordering of the existing pipeline is required.** The current sequence — resolve `selected_company` → build `issuer_items`/`editorial_items` already scoped to that company → `_merge_feed_items()` → bucket into `high_signal_items`/`watchlist_items`/`background_items` via `_effective_tier()` — already produces, for the *currently selected company*, the exact three buckets a "filter, then partition" and a "partition, then filter" order would both yield (company-scoping and tier-bucketing commute: filtering to one company's items and then splitting by tier is set-equal to splitting all items by tier and then filtering each bucket to that company). Re-deriving this as a literal two-stage "filter-after-partition" pipeline would recompute the same result at higher cost and touch more of the existing, already-tested merge/bucket code for no behavioral difference. **The company selector therefore needs zero logic changes** — only the rendering step after it changes (§1.6), never the filtering step itself.

### 1.6 Tier-specific, filter-aware counts

No existing `section_header()` parameter carries a count (`src/ui/components/section.py:10` — `title: str, subtitle: str | None`); only the Background expander shows one today (`f"Show Background ({len(background_items)})"`). **Smallest additive change: fold the count into the existing `title` string**, no change to `section.py` at all:
```python
section_header(f"High Signals ({len(high_signal_items)})")
section_header(f"Watchlist ({len(watchlist_items)})", "Relevant but early, unquantified, or not yet material.")
```
Both counts are already computed today (`len(high_signal_items)`, `len(watchlist_items)`) — this is a rendering-only change, and because they are derived from `merged`, which is already company-filtered, they are automatically filter-aware with no new computation.

### 1.7 Empty states for High Signals and Watchlist

- **High Signals**: unchanged — `_NO_HIGH_SIGNAL_EMPTY_STATE` (`daily_news.py:153`, exact string: `"No material signals right now. Eeva is monitoring new disclosures and developments."`) already exists and is already shown when `high_signal_items` is empty; only the *condition under which this branch is reached* changes (now gated on the active tier view being High Signals, not merely on the list being empty within a combined render).
- **Watchlist**: **new** — the discovery document's own finding (§1.3/§1.6) is that no Watchlist empty-state copy exists today (its section simply doesn't render when empty). New constant:
  ```python
  _NO_WATCHLIST_EMPTY_STATE = "No Watchlist items right now. Eeva is monitoring for emerging developments."
  ```
  (Exact copy is a product-approval item, not fixed by this plan — placeholder text only, following the existing High Signals string's own tone/shape for consistency.) Rendered when the active tier view is Watchlist and `watchlist_items` is empty, using the same `empty_state()` component and call shape already used for High Signals and the page-level empty states (`daily_news.py:488/490`).
- Per §2.4/§2.5 of the discovery, `empty_state()` already supports a bridging second action (`action_label_2`/`action_page_2`) — **not used by default** in this plan (no cross-tier bridge is required by the given acceptance criteria), but available as an optional follow-up (§4).

### 1.8 Background placement, count, and zero-background behavior — inside Watchlist only

The existing Background bucketing (`background_items`, computed via the same unchanged `_effective_tier()` pass) and the existing expander (`st.expander(f"Show Background ({len(background_items)})")`, `daily_news.py:537-543`) are **relocated in the render flow only** — moved inside the `if active_tier_view == _WATCHLIST_QUERY_VALUE:` branch, immediately after the Watchlist section, with **zero change to the expander's own condition** (`if background_items:` — still renders only when non-empty, still collapsed by default, still labeled `f"Show Background ({len(background_items)})"`). When the active tier view is High Signals, this whole block is simply never reached — Background cannot appear there by construction, not by an added exclusion check.

### 1.9 Rendering `materiality_tier=None` as Watchlist consistently

**No new handling needed.** `_effective_tier()` (`daily_news.py:156-163`) already implements `item.materiality_tier or NewsMaterialityTier.WATCHLIST` and is the **only** function that decides bucket membership — it is not modified by this plan (per the "no enum change, no schema migration, no backfill" constraint) and is called unconditionally for every item before tier-view rendering happens. Because the Watchlist bucket is computed once, upstream of the tier-view branch, a `None`-tier item is already correctly present in `watchlist_items` today (confirmed by the existing, unmodified `test_legacy_unclassified_story_defaults_to_watchlist_for_display_only`) and needs no additional logic to keep appearing there once the two-view split is added — it is naturally excluded from `high_signal_items` for the same reason.

### 1.10 Preserving translation controls, card behavior, current filters, Dashboard links, `/daily-news`

- **Translation**: `_do_translate_headline`, `_toggle_show_translation`, `_can_translate_headline`, and the whole translate-button/session-state block inside `_render_card()` (`daily_news.py:372-388`) are **untouched** — they operate purely on `story.id`-keyed session state, orthogonal to which tier section is currently rendering the card.
- **Card behavior**: `_render_card()` and `render_editorial_card()` (`src/ui/components/editorial_coverage.py:129`) are **untouched** — both already accept a `tier` parameter used only for the badge; the calling section simply changes which items it iterates.
- **Company filter**: untouched (§1.5).
- **Dashboard links**: `src/ui/components/recently_updated.py`'s existing `st.page_link(daily_news_page, label="View all Signals →")` (no query params) is **untouched** — visiting it lands on bare `/daily-news`, which resolves to High Signals per §1.1/§1.3, satisfying "Dashboard links continue to work" without any change to `recently_updated.py`.
- **`/daily-news` itself**: no change to `app.py`'s route registration (`_RENDER_FNS`/`_URL_PATHS`, both keyed `"daily_news"`/`"daily-news"`) — the query parameter is additive to the existing single route, never a new `st.Page`.

---

## 2. Acceptance criteria (testable)

| # | Criterion | How it is demonstrated |
|---|---|---|
| 1 | `/daily-news` defaults to High Signals | `AppTest` with no `query_params` set; assert High Signal items render, Watchlist/Background sections do not |
| 2 | `/daily-news?tier=high_signal` directly opens High Signals | `at.query_params["tier"] = "high_signal"` before `.run()`; same assertions as #1 |
| 3 | `/daily-news?tier=watchlist` directly opens Watchlist | `at.query_params["tier"] = "watchlist"` before `.run()`; assert Watchlist items render, High Signal items do not |
| 4 | Invalid or repeated `tier` values safely normalize to High Signals | `at.query_params["tier"] = "bogus"` (and, separately, a value simulating repetition resolved by `.get()`); assert High Signals renders and `st.query_params["tier"] == "high_signal"` after `.run()` |
| 5 | High Signals excludes Watchlist, Background, and `None`-tier cards | Seed one story per tier (`HIGH_SIGNAL`, `WATCHLIST`, `BACKGROUND`, and `materiality_tier=None`) with `tier=high_signal`; assert only the High-Signal-tier headline appears in `at.markdown` text |
| 6 | Watchlist includes Watchlist and `None`-tier cards but excludes High Signal cards from its primary list | Same seed, `tier=watchlist`; assert Watchlist- and `None`-tier headlines appear, High-Signal-tier headline does not appear outside the (separately asserted) Background-adjacent area — High Signal is never placed in Background either, so this reduces to "does not appear at all" |
| 7 | Background is accessible only from Watchlist and remains collapsed by default | With `tier=high_signal` and a Background-tier story seeded: assert zero `at.expander` elements exist. With `tier=watchlist` and the same seed: assert exactly one `at.expander` whose label starts with `"Show Background"` exists, and the Background headline is not present in `at.markdown` outside that expander's own content (mirrors the existing `test_background_story_is_excluded_from_the_default_view_and_only_reachable_via_the_toggle` proof) |
| 8 | Background never appears in High Signals | Covered by #5 and the first half of #7 together |
| 9 | Filters/search behave correctly within the active tier | Seed two companies' stories across tiers; select one company via the existing selector; assert only that company's items appear, correctly bucketed, in both `tier=high_signal` and `tier=watchlist` runs |
| 10 | Switching tiers produces a shareable canonical URL | `at.segmented_control[0].set_value("Watchlist").run()` (starting from `tier=high_signal`); assert `at.query_params["tier"] == "watchlist"` after the interaction |
| 11 | Existing Daily News translation/card controls remain functional | Re-run existing translation tests (`test_french_item_with_no_translation_shows_explicit_language_and_translate_control`, `test_french_item_translate_click_shows_clearly_labeled_title_translation`, `test_english_item_shows_no_language_indicator_or_translate_control`) unmodified against the new two-view code path (both `tier=high_signal` and `tier=watchlist` as applicable to the seeded tier) |
| 12 | Existing Dashboard links to `/daily-news` continue to work | Re-run `tests/test_dashboard_latest_item.py::test_recently_updated_footer_links_use_the_current_signals_label` (source-level assertion, unaffected by this plan) plus a manual confirmation that no query-param requirement was added to that link |

---

## 3. Test strategy — exact files and additions

| Area | File | Addition |
|---|---|---|
| URL state, direct navigation, invalid values | `tests/test_daily_news_page.py` | New tests using `at.query_params["tier"] = ...` before `at.run()` (mirroring `tests/test_themes_research_page.py:123`), covering acceptance criteria #1–#4 |
| State changes (tier switch) | `tests/test_daily_news_page.py` | New test using `at.segmented_control[0].set_value(...).run()` (confirmed working interaction, see §0), covering #10 |
| Tier partitioning / filters / `None` fallback | `tests/test_daily_news_page.py` | New tests extending the existing seed-and-assert pattern already used by `test_high_signal_story_renders_in_the_default_section_with_its_badge` / `test_watchlist_story_renders_in_its_own_secondary_section_with_its_badge` / `test_legacy_unclassified_story_defaults_to_watchlist_for_display_only`, parameterized by `tier=` query value; covers #5, #6, #9 |
| Background visibility | `tests/test_daily_news_page.py` | Update `test_background_story_is_excluded_from_the_default_view_and_only_reachable_via_the_toggle` and `test_no_background_control_rendered_when_there_are_zero_background_items` to run under `tier=watchlist` (their real, intended context now); add a new test proving zero Background exposure under `tier=high_signal`; covers #7, #8 |
| Translations, existing card behavior | `tests/test_daily_news_page.py` | Re-run existing translation tests unmodified (or with only the minimal `tier=` param addition needed to keep seeding the same fixture in the right section) — covers #11 |
| Legacy behavior / Dashboard link | `tests/test_daily_news_page.py`, `tests/test_dashboard_latest_item.py` | New "no query params → High Signals + URL normalizes" test; existing Dashboard-link source-assertion test re-run unmodified — covers #1, #4 (normalization half), #12 |
| Harness | `tests/apptest_pages/daily_news_page.py` | No change expected — already a thin wrapper around `daily_news.render()`; `AppTest.query_params` is set by the *test*, not the harness file itself (confirmed pattern, §0) |

**No new test file is proposed** — every addition lands in the existing, authoritative `tests/test_daily_news_page.py` (already 1,325 lines covering exactly this page), consistent with "favor the smallest additive change."

---

## 4. Explicit separation — required vs. optional

**Required for this implementation:**
- `_resolve_active_tier_view()`, `_render_tier_switch()`, `_handle_tier_switch_change()`, and the associated constants (§1.1–§1.4) in `daily_news.py`.
- Conditional rendering of the High Signals / Watchlist / Background sections based on `active_tier_view` (§1.6–§1.8), reusing the existing bucketing computation unchanged.
- The new `_NO_WATCHLIST_EMPTY_STATE` constant and its render branch (§1.7).
- Test additions/updates listed in §3.

**Optional follow-up refinements (not part of this plan's required scope):**
- A cross-tier empty-state bridge using `empty_state()`'s existing `action_label_2`/`action_page_2` slots (e.g. "No Watchlist items — view High Signals instead").
- Tier-aware counts surfaced anywhere outside the section headers themselves (e.g. a sidebar/nav badge) — no such mechanism exists today (discovery §2.5) and none is required by the given acceptance criteria.
- Any new filter (theme, date, source) or search capability — confirmed not to exist today and explicitly out of scope (discovery §2.4, this plan's §1.5).
- Any card/item-level deep link — confirmed not to exist today (discovery §1.4) and not required by any acceptance criterion here.
- Migrating the default landing tier away from "preserve legacy, then High Signals" if a different phased rollout is later approved (discovery §2.2 already recommends the phased approach this plan implements).

---

## 5. Proposed file-change list

**Production:**
- `src/ui/pages/daily_news.py` — the only production file this plan requires changing.

**Tests:**
- `tests/test_daily_news_page.py` — new and updated tests per §3.

**Not expected to change:** `app.py`, `src/ui/ui.py`, `src/ui/components/editorial_coverage.py`, `src/ui/components/recently_updated.py`, `src/ui/components/section.py`, `src/ui/components/empty_state.py`, `src/models/daily_news_models.py`, any `src/data_access/daily_news/*` file, `tests/apptest_pages/daily_news_page.py`, `tests/test_editorial_coverage_component.py`, `tests/test_dashboard_latest_item.py` (re-run only, not modified), `tests/test_recently_updated_translation.py`.

## 6. Proposed test-command list

```bash
pytest tests/test_daily_news_page.py -q
pytest tests/test_editorial_coverage_component.py -q
pytest tests/test_dashboard_latest_item.py -q
pytest tests/test_recently_updated_translation.py -q
pytest tests/test_daily_news_materiality_classification.py -q
pytest tests/ -k "daily_news or editorial or signals or dashboard" -q
git diff --check
```

## 7. Explicit non-goals

- No schema/migration change to `NewsMaterialityTier` or the persisted `materiality_tier` field.
- No new `st.Page`, no new route, no change to `app.py`'s page registration.
- No data backfill or automatic reclassification of `None`-tier records.
- No new filter (theme/date/source) or search capability.
- No card/item-level deep-link feature.
- No change to `materiality_classification.py`, `editorial_admission.py`, `editorial_pipeline.py`, or `daily_news_pipeline.py`.
- No change to Radar Signals, Radar Inbox, filings, EDGAR/EDINET/DART, source registries, parsers, workers, polling cadence, or deployment configuration.
- No mockup, wireframe, or visual design artifact.

## 8. Implementation risks and mitigations

| Risk | Mitigation |
|---|---|
| Widget session-state desyncs from the URL (address bar edited directly) | Key `st.segmented_control` off the currently-resolved tier value itself (§1.4), forcing a fresh widget instance whenever the resolved tier changes for any reason |
| Legacy-URL normalization causes a rerun loop | Guarded write (`if raw != target`) in `_resolve_active_tier_view()`, verified to terminate after at most one extra rerun (§1.3) |
| A `None`-tier or otherwise legacy record silently disappears from both primary views | No new filtering logic is introduced for tier membership — the existing, already-tested `_effective_tier()` remains the single source of truth (§1.9); regression covered by re-running the existing legacy-tier test under both `tier=` values |
| Company filter interacts incorrectly with the new tier split | No reordering of the filter/partition pipeline (§1.5) — the existing, already-tested company-scoping logic is reused verbatim; new tests explicitly assert filter+tier combinations (#9) |
| Test suite growth makes `test_daily_news_page.py` unwieldy | Accepted as the smallest-additive-change tradeoff; no new test file is proposed to avoid fragmenting the single authoritative source of truth for this page's behavior (§3) |
| Copy for the new Watchlist empty state is not yet product-approved | Placeholder text proposed in §1.7, explicitly flagged as needing approval before implementation, not fixed by this plan |

## 9. Pre-implementation checklist

- [ ] Confirm the exact Watchlist empty-state copy (§1.7 placeholder needs product sign-off).
- [ ] Confirm the exact `st.segmented_control` option labels ("High Signals" / "Watchlist" used here) match whatever copy is finally approved for the visible control.
- [ ] Confirm no other page or test outside this plan's file list assumes `/daily-news` never carries a query parameter (a `grep` for `"daily-news"` and `get_page("daily_news")` across `src/` and `tests/` before implementation, to catch any unanticipated caller).
- [ ] Confirm this plan's understanding of `st.query_params.get()`'s repeated-key resolution against the installed Streamlit version's own release notes/behavior at implementation time (documented here from current knowledge, §1.2, but cheap to re-verify given how central it is to acceptance criterion #4).
- [ ] Re-run the full existing `tests/test_daily_news_page.py` suite once, before any change, to capture a clean baseline to diff against.

---

## Files inspected during this planning pass

```
design/DAILY_NEWS_HIGH_SIGNALS_WATCHLIST_DISCOVERY_2026_09_16.md
src/ui/pages/daily_news.py
src/ui/pages/themes_research.py
src/ui/pages/research_cases.py
src/ui/pages/theme_workspace.py
src/ui/components/section.py
src/ui/components/empty_state.py
src/models/daily_news_models.py
requirements.txt
tests/test_themes_research_page.py
tests/test_theme_workspace_page.py (grep)
tests/test_research_cases_page.py (grep)
tests/test_dashboard_data_integrity.py (grep)
tests/test_daily_news_page.py (cross-referenced against discovery's own inventory)
```
Plus direct, interactive verification (not merely read) of: `AppTest.query_params` item-assignment before `.run()`, and `at.segmented_control[0].set_value(...)` as a working interaction — both confirmed live against the installed Streamlit `1.61.1` before being cited as implementation mechanisms above.

---

*No file other than this new document was created, modified, staged, committed, or pushed during this planning pass. No worker was run; no live discovery, cache/database write, or persistence occurred; no source registry, parser, or deployment configuration was touched; no stash was created, dropped, applied, or modified; no pre-existing untracked file was altered.*
