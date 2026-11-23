# Post-Merge PR #63 Live Validation — 2026-09-16

**Status:** Read-only validation. No code, test, configuration, registry, worker, UI, documentation other than this report, database, cache, persisted record, or deployment file was modified. No worker run, no live discovery, no cache/database write, no stash operation.

**Confirmed pre-conditions:**
```
PR #63 state:      MERGED
merge commit:       79a8401c5272a6ec511eb8a1e538f9124e54190e
included commit:    cd6677aedb792d191ee111416c8bd8ee10bb6c93
```
Local `main` was fast-forwarded (read-only pull) to `79a8401c` before any check below was run.

**Method and honesty note (unchanged from every prior audit this session):** this environment has no configured connection to the production (Render) database/cache. The local dev cache contains exactly one Daily News file, `data/cache/daily_news_stories.json` (154 rows, unchanged since a much earlier session, Lane A only) — searched for all five fixture titles; **zero matches**. There is no local editorial (Lane B) store at all. **This environment cannot observe any actual persisted or production Dashboard record, pre- or post-merge.** Every result below is either (a) a live, narrowly-bounded, read-only re-fetch of the real, currently-registered source feeds (one bounded `requests.get` per source, via the codebase's own `fetch_entries()`, no persistence — the same method used in every prior audit this session) re-derived through the real, current, post-merge admission/materiality functions, or (b) a direct, in-process exercise of the real, current, post-merge `recently_updated._select_recently_updated_rows()` against representative records written only to a Python `tempfile.TemporaryDirectory()` that was deleted before this report was written — never the application's own `data/cache/` directory.

---

## 1. Post-merge hobbyist-filter re-derivation

The RTX 50-series laptop power-limit article was re-fetched live from `toms-hardware-rss` (`https://www.tomshardware.com/...`) at 2026-09-16T19:34:45Z — still present, byte-identical title and `published_at` (`2026-09-16T15:19:42+00:00`) to every prior check this session. Re-derived against the real, current, merged `assess_admission()`/`classify_editorial_story()`:

```
admitted:         False
reason:            hobbyist_modding_format:vibe codes
materiality_tier:  Watchlist (on_taxonomy_no_anchor:ai_compute_and_data_center) — computed but irrelevant,
                    since admission=False means this item is never constructed or persisted at all
matched_companies: (Intel Corp., NVIDIA)
```

**Confirmed: the merged exclusion is active and correctly rejects this exact live article on `main` as it stands right now.** No newly-admitted editorial item containing a hobbyist/modding/overclocking/consumer-performance-tweak pattern was found in this sample — the one confirmed live example of this content shape is rejected, not admitted.

---

## 2. Dashboard tier-filter re-derivation

`recently_updated._select_recently_updated_rows()` (the real, current, post-merge function) was exercised directly against representative in-memory records — one Background editorial story, one Watchlist editorial story, one High Signal editorial story, and one Radar candidate — written to a throwaway `tempfile.TemporaryDirectory()`, never the app's real cache:

| Record | Included in default preview? |
|---|---|
| Background editorial item | **No** — excluded |
| Watchlist editorial item | **Yes** — included |
| High Signal editorial item | **Yes** — included |
| Radar candidate row | **Yes** — included, unaffected |

This matches PR #63's own regression suite (`tests/test_dashboard_latest_item.py`), re-run fresh against the merged `main` for this report: `pytest tests/test_editorial_admission.py tests/test_editorial_pipeline.py tests/test_dashboard_latest_item.py tests/test_recently_updated_translation.py -q` → **219 passed**. Filing rows (`_load_filing_rows`) were not separately re-exercised in-memory here since the merged diff never touches that function at all (confirmed by direct code inspection of the merge commit) — the existing, unmodified, still-passing filing-row test coverage in the same file is the authoritative source for that behavior.

**No Background editorial record can enter the default result set; Watchlist and High Signal are not inadvertently excluded; Radar/filing rows are unaffected.**

---

## 3. Fixture-by-fixture evidence

| Field | RTX power-limit mod | smartARM / Meta | Samsung One UI 9 | L3Harris "Invisible War" |
|---|---|---|---|---|
| Title | "Developer vibe codes a tool to let Nvidia RTX 50-series laptop owners crank up their power limits — can juice RTX 5090 mobile GPU to 225W" | "Canadian Start-up smartARM Uses AI to Create Intuitive Bionic Prosthetics" | "Samsung Begins Official Rollout of One UI 9, Bringing the Latest Galaxy Experiences to More Devices" | "The Invisible War Has a New Architecture" |
| Source / type | `toms-hardware-rss`, Lane B `INDEPENDENT_NEWS` | `meta-newsroom-rss`, Lane A `OFFICIAL_NEWSROOM` | `samsung-newsroom-rss`, Lane A `OFFICIAL_NEWSROOM` | `l3harris-newsroom-rss`, Lane A `OFFICIAL_NEWSROOM` |
| URL | `tomshardware.com/pc-components/overclocking/developer-vibe-codes-a-tool-...` | `about.fb.com/news/2026/09/canadian-start-up-smartarm-...` | `news.samsung.com/global/samsung-begins-official-rollout-of-one-ui-9-...` | `l3harris.com/newsroom/editorial/2026/09/invisible-war-has-new-architecture` |
| Publication time | 2026-09-16T15:19:42Z | 2026-09-16T16:00:19Z | Real `<pubDate>` is `21:00:00Z` (future relative to every check made this session); `_parse_published_at()`'s own existing, unmodified future-discard guard substitutes fetch time instead — reconfirmed again at 19:34:54Z, consistent with every prior fetch | 2026-09-16T15:12:34Z |
| Persistence/creation time | **Not observable** — no persistence store reachable | Not observable | Not observable | Not observable |
| Materiality tier (re-derived, current code) | Watchlist (moot — never admitted) | Background | Background | Background |
| Company tags | `(Intel Corp., NVIDIA)` matched; only NVIDIA identified as subject | Meta Platforms, Inc. (Lane A, single-issuer tag) | Samsung Electronics (Lane A) | L3Harris Technologies, Inc. (Lane A) |
| Predates or postdates merge (`79a8401c`, merged 19:29:59Z) | **Predates** — article timestamp (15:19:42Z) is ~4h10m before the merge; this is the same article found in the prior live-card audit, before PR #63 even opened | **Predates** — 16:00:19Z, ~3h30m before merge | Effectively concurrent with/after this report's own probe time (future-pubDate discard), itself well after the merge — but this reflects when the *article* became live, not any Eeva persistence event | **Predates** — 15:12:34Z, ~4h17m before merge |
| Admission/rendering path and outcome | **Rejected at admission** (`hobbyist_modding_format`) — never constructed, never enters any Dashboard/Signals surface | Lane A, always published; Background → excluded from Dashboard "Recently Updated" by the merged filter; remains available in the Signals page's own "Show Background" expander (unchanged) | Same as smartARM | Same as smartARM |
| **Verdict** | **Correct — confirmed live false positive from the prior audit is now confirmed fixed** | **Historical-stale relative to any hypothetical earlier admission, but the current, live re-derivation is correct/unchanged from the prior audit's own finding** | Same | Same |

**On "whether the RTX item remains visible as a historical persisted item":** this cannot be answered from this environment — there is no reachable persisted editorial store, local or production, containing this or any other Lane B story. This is reported as an absence of evidence, not as evidence of absence. What **is** confirmed: if this exact article were fetched by a real worker right now, under the current merged code, it would not be admitted, so it could not become a *new* persisted card. Whether an *old* card from before PR #61 (the original admission-precision fix, merged 2026-09-16 morning) or before PR #63 still exists in the real production store, and whether it is still rendered somewhere, is **not determinable from this environment** — this is a genuinely open question that only production database/cache access (which this session has never had) could resolve, distinct from the question of whether *today's* code would newly admit it (confirmed: no).

---

## 4. Presence or absence of post-merge live records, and resulting confidence level

**No post-merge *persisted* editorial records exist that this environment can observe**, because no worker has run (by design — this validation is read-only and explicitly prohibited from running one) and no production database/cache is reachable. This is stated plainly rather than inferred.

**Confidence level:** the underlying **code-path behavior** (admission logic, materiality logic, Dashboard filter logic) is verified with **high confidence** — re-derived directly, multiple times, against the real, current, merged `main`, using real live article text for the hobbyist-filter check and representative in-memory records for the Dashboard-filter check, plus a fresh full run of the existing regression suite (219/219 passing). What remains **unverifiable from this environment**, and should be treated as **low/no confidence pending a real post-merge worker run**, is whether the *actual production Dashboard*, right now, reflects this behavior — that depends on production's own deploy timing and worker cadence, neither of which this session has visibility into.

---

## 5. Future Daily News tier navigation: High Signals and Watchlist

*(Documentation only — no design, schema, routing, or UI change is proposed or implemented here.)*

**Product intent, as given:** Daily News will eventually expose two primary tier views — **High Signals** (the highest-confidence, most material, action-oriented events) and **Watchlist** (credible emerging developments that merit monitoring but are not yet High Signals). **Background** remains available as secondary research and should not be promoted as equivalent to either primary view.

**Discovery questions that must be answered before any implementation work begins:**

1. **Current Daily News route/component structure** — is the existing single "Signals" page (`src/ui/pages/daily_news.py`) split into two routes, or does one route gain a tier selector? How does this interact with the existing per-company filter view?
2. **Direct URL and query-parameter strategy** — should "High Signals" and "Watchlist" be independently linkable/bookmarkable (e.g. distinct query parameters or paths), and how does that interact with Streamlit's own page/routing model?
3. **Default landing tier** — does a bare visit to the Daily News area land on High Signals, Watchlist, or the current combined view? Does this differ for a first-time visitor versus a returning one?
4. **Empty-state behavior** — the existing High Signal section already has its own dedicated empty-state copy (`_NO_HIGH_SIGNAL_EMPTY_STATE`); does a genuinely separate Watchlist route need its own distinct empty state, and does "no High Signals right now, but N Watchlist items exist" need to say so explicitly across a route boundary the way it currently can within one page?
5. **Counts and tier definitions** — are the existing three `NewsMaterialityTier` values (`HIGH_SIGNAL`/`WATCHLIST`/`BACKGROUND`) and their existing gate logic left completely unchanged, with only *navigation/presentation* restructured — or does this product direction imply any change to what qualifies for each tier?
6. **Whether Background stays in an expander or becomes a separate secondary view** — the stated intent ("available as secondary research... not promoted as equivalent") is compatible with either the current in-page expander or a genuinely separate, clearly-secondary route; this needs an explicit decision, since it changes how discoverable Background is relative to the two primary tiers.
7. **Preservation of deep links, translation controls, filters, and card behavior** — the existing per-card translate toggle, the per-company filter, cross-lane merge/ordering, and dedup/cap behavior all currently operate over one unified list; splitting into separate primary views needs an explicit decision on whether each of these continues to operate per-tier, globally, or needs its own redesign.

No answer to any of these seven questions is proposed here; they are listed only as the discovery work a future implementation task would need to resolve first.

---

## Summary

- **Post-merge hobbyist-filter outcome:** Confirmed working — the exact live RTX fixture is rejected at admission (`hobbyist_modding_format:vibe codes`) against the current, merged `main`. No newly-admitted hobbyist/modding item was found in this sample.
- **Dashboard tier-filter outcome:** Confirmed working — Background editorial records are excluded from the default "Recently Updated" preview; Watchlist and High Signal records remain included; Radar rows are unaffected. Verified both via direct in-process re-derivation and a fresh 219/219 regression-suite run.
- **Status of historical pre-merge cards:** All four confirmed-live source articles (RTX, smartARM, Samsung, L3Harris) predate the merge by 3.5–4+ hours (Samsung's true availability time is effectively concurrent with this report's own probe, due to a future-pubDate discard that is pre-existing, correct behavior, not a new issue). Whether any of them exist as an already-persisted production card, and if so under what code version, is **not observable** from this environment.
- **Presence/absence of post-merge live records:** **Absent** — no worker has run, and no production store is reachable. Code-path confidence is high (directly re-verified); production-state confidence is not determinable from this environment.
- **Radar/filing preservation outcome:** Confirmed unaffected, both by direct code inspection of the merge diff (neither `_load_filing_rows` nor any Radar-related code path is touched) and by exercising a representative Radar record through the real, current filter function.
- **Future Daily News tier-navigation discovery questions:** documented above (7 questions); no design, schema, or routing change proposed.
- **Exact files changed:** only this new document, `design/POST_MERGE_PR63_LIVE_VALIDATION_2026_09_16.md`.

*No code, test, configuration, registry, worker, UI, database, cache, persisted record, or deployment file was modified. No worker was run; no item was ingested, cached, or persisted; no source registry or parser was touched; no stash was created, dropped, applied, or modified; no pre-existing untracked file was altered.*
