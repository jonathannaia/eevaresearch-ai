# Daily News: `identified_companies` vs. `matched_companies` — Discovery and Design

Status: **read-only discovery and design document**. No code, model, schema, migration, registry, source, admission rule, materiality rule, UI, cache/database record, or existing document was modified. No worker, discovery, live ingestion, or polling was run. This document maps the current pipeline exactly as it exists today, evaluates it against the target product contract, and proposes (without implementing) a path to close the gap.

## 0. The core finding, stated up front

**Today's `EditorialStory.matched_companies` field already holds what the target product contract calls `identified_companies` — raw, unfiltered text recognition — not what the field's own name implies.** The admission pipeline (`editorial_admission.assess_admission()`) internally computes a properly-vetted, per-company "is this really the subject" list (`_identified_subject_companies()`), but that list is discarded after being used only to decide a whole-item admit/reject boolean and to log one company's name in a diagnostic string. The raw, pre-admission tuple is what actually gets written into the persisted model, read by every card renderer, every Dashboard row, and every company filter. This is a genuine, real, currently-live semantic gap — confirmed by direct reading of the exact call sites, not inferred — though it has not yet been *observed* as a live defect in any existing fixture, because no current test constructs a realistic multi-company-raw-match item and pushes it through the full pipeline to check whether an unverified secondary company rides along (see §5 and §7 for the honest distinction between "structurally proven" and "empirically observed").

## 1. Exact current data-flow map

```
raw source item (RSS/Atom entry: title, summary/description, link, published_at)
        │
        ▼
editorial_pipeline.run_editorial_discovery()  [src/data_access/daily_news/editorial_pipeline.py]
  1. URL validity / staleness / dedup gates                              (lines 330-351)
  2. classification_summary = _classification_text(entry.summary)        (line 354)
  3. matched_companies, matched_themes =
       editorial_matching.matched_companies_and_themes(title, summary)   (line 361)  ── RAW TEXT RECOGNITION
  4. materiality_tier, materiality_reasons =
       materiality_classification.classify_editorial_story(...)          (lines 368-370)  ── independent pass, not derived from matched_companies
  5. fail-closed precondition: reject if NEITHER company NOR theme        (lines 419-426)
  6. admission = editorial_admission.assess_admission(
       title, summary, matched_companies, matched_themes,
       materiality_reasons)                                              (lines 427-430)
       │
       ├─ internally: identified_companies =
       │    _identified_subject_companies(text, title, matched_companies) ── THE REAL, VETTED SUBJECT LIST
       │  (title-placement / action-language / rival-entity-exclusion / ambiguous-alias rules)
       │  ... used ONLY to pick identified_companies[0] for the `reason` string, then DISCARDED
       │
       └─ returns AdmissionDecision(admitted: bool, reason: str)          ── whole-item boolean only
  7. if not admitted: continue (item dropped, nothing persisted)          (lines 431-434)
  8. if admitted: EditorialStory(..., matched_companies=matched_companies, ...)  (line 458-465)
                                                    ▲
                                     the SAME RAW tuple from step 3, unchanged by step 6
        │
        ▼
persistence (upsert_new_stories)
  ├─ JSON file cache: editorial_story_store.py → daily_news_editorial_stories.json
  └─ Postgres: postgres_state_db/editorial_story_repository.py → editorial_stories table
        │
        ▼
query/read layer
  ├─ get_visible_editorial_stories() / select_visible_editorial_stories_for_company()
  │    (editorial_pipeline.py:539 — filters `company_name in s.matched_companies`)
  └─ theme_evidence.recent_daily_news_evidence()
       (src/logic/theme_evidence.py:97 — `set(story.matched_companies) & company_names`)
        │
        ▼
rendering
  ├─ Daily News card: editorial_coverage._tag_chips_html(story.matched_companies, ...) → visible chip per entry
  └─ Dashboard Recently Updated: recently_updated._load_editorial_rows()
       → company_name=", ".join(story.matched_companies)
```

## 2. Exact files, functions, models, and test locations involved

| Concern | File | Function/field |
|---|---|---|
| Model | `src/models/daily_news_models.py:139-176` | `EditorialStory` — fields: `id, headline, publisher, source_url, published_at, retrieved_at, excerpt, matched_companies: tuple[str,...], matched_themes: tuple[str,...], source_feed_id, materiality_tier, materiality_reasons`. **No `identified_companies` field exists today.** |
| Raw recognition | `src/data_access/daily_news/editorial_matching.py:91-150` | `match_companies(text)`, `match_themes(text)`, `matched_companies_and_themes(title, description)` — pure regex/alias matching, zero admission logic |
| Admission | `src/data_access/daily_news/editorial_admission.py:565-703` | `_identified_subject_companies(text, title, matched_companies)` (the real per-company vetting, discarded after use); `assess_admission(...)` → `AdmissionDecision(admitted: bool, reason: str)` |
| Materiality | `src/data_access/daily_news/materiality_classification.py:781-802` | `classify_editorial_story(headline, excerpt, source_category)` — independent of `matched_companies` |
| Construction | `src/data_access/daily_news/editorial_pipeline.py:328-465` | `run_editorial_discovery()` — the sole `EditorialStory(...)` construction site in production code |
| JSON persistence | `src/data_access/daily_news/editorial_story_store.py:29-62` | `_story_from_dict()` / `save_stories()` — `matched_companies=tuple(data.get("matched_companies", ()))` |
| Postgres persistence | `src/data_access/postgres_state_db/editorial_story_repository.py:26-38`; schema at `src/data_access/postgres_state_db/schema.py:583-596` | `_row_to_story()`; column `matched_companies_json TEXT NOT NULL DEFAULT '[]'` |
| Daily News card | `src/ui/components/editorial_coverage.py:91-99,164` | `_tag_chips_html()`, `render_editorial_card()` — reads `story.matched_companies` directly, no filtering |
| Dashboard | `src/ui/components/recently_updated.py:356-402` | `_load_editorial_rows()` — `company_name=", ".join(story.matched_companies)` |
| Company filter | `src/data_access/daily_news/editorial_pipeline.py:539`; `src/ui/pages/daily_news.py:483` | `select_visible_editorial_stories_for_company()` — `company_name in s.matched_companies` |
| Evidence linking | `src/logic/theme_evidence.py:97` | `recent_daily_news_evidence()` — `set(story.matched_companies) & company_names` |
| Tests (12 files) | see §7 | `test_editorial_admission.py` (23 hits), `test_editorial_pipeline.py` (19), `test_daily_news_page.py` (13), `test_daily_news_historical_signals_audit.py` (13), `test_editorial_coverage_component.py` (8), `test_state_db_postgres_editorial_story_repository.py` (8), `test_editorial_matching.py` (8), `test_theme_evidence.py` (3), `test_editorial_story_store.py` (2), `test_dashboard_latest_item.py` (2), `test_daily_news_backend.py` (2), `test_government_editorial_sources.py` (1) |
| Offline review harness | `scripts/daily_news_quality_review.py` | Reruns matching+admission against pasted JSON for manual review; no persistence, not a production export |

No CSV export, API endpoint, or other export surface reads `matched_companies` — confirmed absent by grep.

## 3. Field-semantic decision table

| Question | Current answer |
|---|---|
| What does `matched_companies` currently contain? | **Raw** output of `matched_companies_and_themes()` — every tracked company whose alias appears in title+description, with no per-company subject-identity check applied. It is *not* raw-matches-mixed-with-admitted; it is consistently raw for every construction path (there is only one), so the current behavior is at least internally consistent — just misnamed relative to the target contract. |
| Does admission ever narrow it per company? | No. `assess_admission()` is a whole-item gate. Its internal `_identified_subject_companies()` computes the real per-company subject list but that list is never returned to the caller in a form the pipeline uses — only `identified_companies[0]` leaks out, embedded in the `reason` diagnostic string (e.g. `"company_subject:NVIDIA"`). |
| Does a rejected item retain any company metadata anywhere? | No durable trace. A rejected item never reaches `EditorialStory(...)` construction — the pipeline `continue`s. The only place any trace exists is `EditorialScanReport.rejected_examples` (title + reason strings, bounded to 5 entries), and only when `dry_run=True`; in normal production runs this stays empty. Confirmed structurally (the `continue` before construction) and by the `test_rtx_laptop_power_limit_unofficial_tool_is_rejected` / `test_unofficial_game_port_mentioning_the_owning_company_historically_fails_admission` fixtures, both of which prove rejection happens with zero `EditorialStory` ever created. |
| Where does company recognition occur relative to admission? | Before — always. Raw matching (`matched_companies_and_themes`) runs first (pipeline line 361); admission runs second (line 427), consuming the raw result as an input but never feeding a narrowed result back out into what gets persisted. |
| Current model fields / schema / serialization | See §2. Two backends (local JSON file, Postgres), both flat, both already treat `matched_companies` as required-with-safe-default-on-read (`.get(..., ())` / `NOT NULL DEFAULT '[]'`). |
| Backwards compatibility precedent | Already established twice on this exact model: `materiality_tier`/`materiality_reasons` were added later as optional/defaulted fields, with an explicit "never backfilled automatically" policy documented in `NewsMaterialityTier`'s own docstring. This is the direct precedent a new field should follow. |
| Is any real persisted `EditorialStory` data inspectable locally? | **No — confirmed absent, not assumed.** `data/cache/daily_news_editorial_stories.json` does not exist anywhere in this repository/environment. Only the unrelated issuer-lane cache (`data/cache/daily_news_stories.json`, `NewsStory` records with a singular `company_name: str`, structurally different) exists locally. **Whether production Postgres already holds real `EditorialStory` rows written under today's raw semantics is unknown from this read-only, local investigation** — this must be treated as an open question, not assumed either way, by any implementation plan. |
| Per-surface filter recommendation | See table below. |

### Per-surface field recommendation

| Surface | Field to use | Why | Code change needed? |
|---|---|---|---|
| Daily News card company tags | `matched_companies` (post-fix semantics) | Display rule is explicit: visible tags must derive from attributed companies only | Yes — indirectly, by fixing what gets *written* to `matched_companies` at construction (§8); the renderer itself needs no change |
| Dashboard Recently Updated company column | `matched_companies` (post-fix semantics) | Same display rule | No direct change — inherits correctness once construction is fixed |
| Company filter/selectbox (`_stories_for_company`) | `matched_companies` (post-fix semantics) | A company filter exists to answer "show me stories *about* this company" — filtering on raw/`identified_companies` would surface incidental-mention noise, defeating the fix's purpose | No direct change — inherits correctness once construction is fixed |
| Research Theses evidence overlap (`theme_evidence.py`) | `matched_companies` (post-fix semantics) | Evidence-linking should reflect validated attribution, not incidental co-mention | No direct change — inherits correctness once construction is fixed |
| High Signals / Watchlist / Background tiers | N/A — orthogonal | Materiality tiering is a fully independent pass (§1 step 4), not derived from `matched_companies` at all; this fix applies uniformly across all three tiers with no special-casing | None |
| Translations | N/A — unaffected | Translation operates on headline/excerpt text; no coupling to `matched_companies` found anywhere in either investigation pass | None |
| Search | N/A — no free-text search exists for editorial stories beyond the company selectbox (already covered above) | — | None |
| Exports/APIs | N/A | None exist that carry editorial company data (confirmed absent) | None |
| Internal diagnostics / future relationship research | `identified_companies` (new field, once added) | This is exactly the product contract's stated purpose for the raw list — a superset pool for future candidate discovery, never itself a display/filter source | New field, additive |

## 4. Naming collision to watch during implementation

The admission module's existing internal helper is named `_identified_subject_companies()` and computes the **admitted/attributed** subset — the opposite of what the product contract's new `identified_companies` *model field* would mean (the raw, pre-admission superset). This is a real naming trap: implementing the recommended design (§8) without renaming that internal helper would leave a codebase where `_identified_subject_companies()` feeds `matched_companies` while a differently-scoped, newly-added `EditorialStory.identified_companies` field means something else entirely. Flagged here explicitly so a future implementation renames the internal helper (e.g. to `_admission_attributed_companies()`) rather than colliding two "identified" concepts with opposite meanings.

## 5. Fixture-by-fixture expected behavior table

For each pattern: what the existing pipeline does *today* (verified against real code/fixtures where they exist), and what the target contract wants for `identified_companies` / `matched_companies` / visible tags / filter membership / persistence, once the recommended design (§8) is implemented.

| # | Pattern | Today (verified) | Target `identified_companies` | Target `matched_companies` | Visible tags | Filter membership | Persistence |
|---|---|---|---|---|---|---|---|
| 1 | RTX hobbyist/modding item naming NVIDIA (and, hypothetically, Intel) | **Confirmed by `tests/test_editorial_admission.py::test_rtx_laptop_power_limit_unofficial_tool_is_rejected`**: NVIDIA is title-placed (would pass the identity check) but the item is **rejected outright** (`hobbyist_modding_format`, no anchor evidence to rescue it) — no `EditorialStory` is ever constructed. Zero durable trace of NVIDIA anywhere. | N/A — never persisted | N/A — never persisted | None (story doesn't exist) | None | **None at all** — this is unchanged by the recommended design; rejection already behaves correctly today. If a second company (Intel) were also named, the same whole-item rejection logic still applies — both vanish together, since rejection is item-level, not per-company. |
| 2a | Private-company story mentioning a tracked company (e.g. Meta) only in passing, with no real corporate action by Meta | Today: if the item admits (via a theme match or a different genuine subject), Meta rides along in `matched_companies` unfiltered — a visible, misleading Meta tag/filter entry. **Not yet empirically observed in any existing fixture** — the real smartARM/Meta audit case was actually a Lane A (`NewsStory`, issuer-published) item, not a Lane B admission-gate case, so no current test exercises this exact Lane B scenario; this row is a structural inference from the code path (§0), not a confirmed live defect. | `{"Meta Platforms, Inc."}` — genuinely recognized in text | `{}` — Meta excluded; it is not a validated subject (incidental mention only) | No Meta chip | Does **not** appear under a "Meta" company filter | Persists (if admissible on another basis) with Meta present only in `identified_companies`, absent from `matched_companies` — available for diagnostics/future relationship research, never as a misleading display |
| 2b | Legitimate direct product integration — Meta officially/materially adopts or certifies a private company's product, with Meta as a genuine corporate actor (title-placed or clear action language) | Today: identical mechanism to any single/multi-company admitted story — if Meta is title-placed or has action language, `_identified_subject_companies()` would already include it internally, but that result is discarded and the raw match is what persists regardless — so today's *outcome* happens to be correct here (Meta shows), but only by coincidence of the raw match equaling the true subject, not because the pipeline verified it. | `{"Meta Platforms, Inc."}` | `{"Meta Platforms, Inc."}` — validated | Meta chip shown | Appears under "Meta" filter | Both fields equal; identical to current visible behavior for this case (no regression) |
| 3 | Genuine named issuer-to-issuer relationship (Generac/Amazon-style supply agreement, both companies genuinely named corporate actors) | **No existing fixture traces a real two-named-issuer supply-agreement headline through the actual `assess_admission()`/`matched_companies_and_themes()` path.** The multi-company fixtures that do exist (`test_editorial_pipeline.py::test_for_company_multi_company_story_appears_for_every_matched_company`, `test_daily_news_page.py::test_multi_company_story_appears_on_both_matched_companies_pages`) hand-set `matched_companies=("Amazon.com, Inc.", "Alphabet Inc.")` directly on the fixture rather than deriving it from realistic prose — they prove display/filter behavior for a two-company tuple, not that admission would correctly identify both as subjects from real text. This is a genuine coverage gap independent of this design task. | `{"Generac Holdings Inc.", "Amazon.com, Inc."}` | `{"Generac Holdings Inc.", "Amazon.com, Inc."}` — both validated (both title-placed or action-language present in a genuine commercial-relationship story) | Both chips shown | Appears under both companies' filters | Both fields equal |
| 4 | Multi-company official infrastructure/technical deployment (CoreWeave/NVIDIA-style joint announcement) | Same gap as #3 — `test_editorial_coverage_component.py::test_multi_company_multi_theme_tags_all_render` (Oracle + Corning) is display-layer only, `matched_companies` hand-set directly, not derived from realistic joint-announcement prose. No fixture traces this pattern through real admission logic. | `{"CoreWeave, Inc.", "NVIDIA"}` | `{"CoreWeave, Inc.", "NVIDIA"}` — both validated | Both chips shown | Appears under both companies' filters | Both fields equal |
| 5 | Normal single-company official issuer announcement (baseline) | **Confirmed end-to-end by `tests/test_editorial_pipeline.py::test_qualifying_item_is_published`** — the one fixture in the whole suite that runs the real, full pipeline (not a hand-set fixture). "Oracle Corporation reports strong AI cloud demand" → `story.matched_companies == ("Oracle Corporation",)` after real construction and persistence. Raw match and true subject trivially coincide here (only one company to begin with). | `{"Oracle Corporation"}` | `{"Oracle Corporation"}` | One chip | Appears under Oracle's filter | Both fields equal — **identical to current, already-correct behavior; no regression for the dominant, common case** |

**Honest caveat, stated per instructions rather than glossed over**: rows 3 and 4 (genuine multi-issuer relationships) are exactly the pattern the recommended design (§8) is built to eventually support well, but they are *also* the pattern with the thinnest existing test coverage — the recommended implementation's own test plan (§10) must add real, from-scratch-text fixtures for these two patterns, not just re-verify already-hand-set ones, or the fix's correctness for exactly the multi-company case it's meant to matter most for would remain unproven.

## 6. Implementation options

### Option 1 (Recommended): Additive `identified_companies` field + repurpose `matched_companies` construction to hold the admission-vetted subset only

- **Model/schema changes**: add `identified_companies: tuple[str, ...] = ()` to `EditorialStory` (additive, defaulted — zero impact on any existing construction call site that doesn't pass it explicitly, once default-handling is added everywhere it's constructed). `matched_companies` itself keeps its existing name/type/position — only *what gets written into it at construction* changes.
- **Serialization/persistence impact**: JSON — add `"identified_companies": list(story.identified_companies)` to whatever `asdict()`-based write path exists; read side needs one new `.get("identified_companies", ())` default, exact same pattern as every other optional field. Postgres — one additive `ALTER TABLE editorial_stories ADD COLUMN identified_companies_json TEXT NOT NULL DEFAULT '[]'`, mirroring the existing `matched_companies_json`/`matched_themes_json` column convention exactly; zero-downtime, standard additive migration.
- **Backwards compatibility / old-record behavior**: old records (wherever they exist, local or production) deserialize with `identified_companies=()` (field didn't exist when written) — **explicitly, not silently, empty**; their existing `matched_companies` value is left completely untouched (no rewrite, no reinterpretation) and continues to mean whatever it meant when written (today: raw). This is the one place genuine semantic drift exists across the change — documented explicitly in §9, not hidden.
- **Query/filter changes**: none required at any consumer call site (§3's per-surface table) — every reader already reads `matched_companies`, and correctness is inherited automatically once construction is fixed at the source. This is the strongest argument for this option: the blast radius is one function (`editorial_pipeline.run_editorial_discovery()`'s construction call) plus the model/schema addition, not a sweep across every UI/filter file.
- **UI impact**: none required — `editorial_coverage.py`, `recently_updated.py`, `daily_news.py` all already read `matched_companies` and will display/filter correctly for every *new* record without any UI code change.
- **Migration/backfill requirement**: **none required, and explicitly none proposed** — old records keep their pre-existing `matched_companies` value (possibly over-inclusive) until they age out of the pipeline's own recency-bounded display windows (Daily News' 7-day window, Dashboard's freshness window) naturally. A future, separately-approved backfill (re-running `matched_companies_and_themes()` + the admission logic against each old record's still-preserved `headline`/`excerpt` to recompute both fields) is *possible* later, since the source text is preserved — but is not part of this design and not needed for correctness of new records.
- **Test impact**: `test_editorial_pipeline.py` needs new end-to-end fixtures for the multi-company patterns (§5 rows 3-4, currently uncovered); `test_editorial_admission.py` needs `AdmissionDecision` widened to expose the identified-companies list (see below) with tests for that new field; `test_editorial_story_store.py` and `test_state_db_postgres_editorial_story_repository.py` need round-trip tests for the new field including old-record-missing-field deserialization; `test_editorial_coverage_component.py`/`test_daily_news_page.py`/`test_dashboard_latest_item.py` need no change (they already assert against `matched_companies`, whose contract at the consumer layer doesn't change).
- **Necessary supporting change**: `AdmissionDecision` (`editorial_admission.py:560-562`) needs a third, additive field — e.g. `identified_companies: tuple[str, ...] = ()` — so `assess_admission()`'s already-computed, currently-discarded `_identified_subject_companies()` result can actually flow back to the caller. This is additive to the dataclass (existing two-positional-arg construction, `AdmissionDecision(True, "reason")`, remains valid via the new field's default) but does require touching every `return AdmissionDecision(...)` call inside `assess_admission()` to also pass the list through. **Naming collision risk (§4)**: recommend renaming the internal `_identified_subject_companies()` helper during this change, to avoid two different "identified" concepts colliding in the same module.
- **Risks**: (a) the exact old-record semantic-drift caveat above — must be documented, not hidden, wherever this ships; (b) whether production Postgres already has real rows is unknown from this investigation — an implementation must explicitly check this (a simple read-only row count) before assuming the "no backfill needed" framing is low-risk in practice, since a production system with many existing rows carries more old-record exposure than an empty one; (c) `AdmissionDecision`'s widened return shape touches five `return` statements inside `assess_admission()` — a mechanical but real diff, worth careful review for a missed branch.

### Option 2: Additive `identified_companies` field only; leave `matched_companies` construction unchanged (still raw); fix display/filter at the read layer instead

- **Model/schema changes**: same additive field as Option 1.
- **Serialization/persistence impact**: same as Option 1 for the new field; `matched_companies` itself is never repurposed, so there is zero semantic drift risk for old vs. new records — `matched_companies` always means raw, at every point in time, for every record.
- **Backwards compatibility**: strictly stronger than Option 1 on this one axis — no old-record caveat needed at all for `matched_companies`, since its meaning never changes.
- **Query/filter changes**: **required at every consumer** — a new pure function (e.g. `attributed_companies_for(story)`) would need to re-run admission-equivalent identity logic against the stored `headline`/`excerpt` at render/filter time, and every one of §3's consumer call sites would need to call it instead of reading `matched_companies` directly.
- **UI impact**: every card renderer, Dashboard row, and filter predicate needs a code change (larger diff surface than Option 1).
- **Migration/backfill requirement**: none, same as Option 1.
- **Test impact**: larger — every consumer-layer test needs to account for the new derived-at-read-time function, in addition to the same admission/pipeline test additions Option 1 needs.
- **Risks**: the admission-identity logic now effectively runs twice per story over its lifetime — once at admission time (to decide whole-item eligibility) and again at every render/filter call (to derive the attributed subset) — a real duplication risk where the two logic paths could drift out of sync over time as one is edited without the other. Also strictly more files touched than Option 1 for equivalent end-state correctness, with no compensating benefit beyond the (real, but narrow) old-record semantic-purity advantage above.

### Option 3: Full model rename — deprecate `matched_companies`'s current meaning entirely, introduce both target fields together with a mandatory backfill pass

- **Model/schema changes**: add `identified_companies` (raw) and treat `matched_companies` as an entirely new column semantically from day one — requires either a genuine schema rename (breaking) or a parallel field and an explicit migration flag distinguishing pre/post-change rows.
- **Serialization/persistence impact**: larger — a migration script must re-run `matched_companies_and_themes()` + admission-equivalent identity logic against every existing row's stored `headline`/`excerpt` to populate both fields correctly for historical data, converting an additive change into a data migration.
- **Backwards compatibility**: cleanest end state (no record, old or new, ever has ambiguous field semantics) but only *after* the backfill completes — during rollout there is a window where correctness depends on migration completion, not just code deployment.
- **Query/filter changes**: same as Option 1 (none needed once the model is correct) — but only after backfill.
- **UI impact**: same as Option 1 — none needed at the consumer layer.
- **Migration/backfill requirement**: **mandatory, immediate** — this is the option the task's own stated recommendation criteria explicitly rule out ("avoids immediate historical backfill").
- **Test impact**: largest — needs the same test additions as Option 1, plus a full migration-script test suite (idempotency, partial-failure handling, dry-run mode).
- **Risks**: violates two of the recommendation's own explicit requirements (avoid immediate backfill; preserve current user-visible behavior without requiring a migration to land first). Presented for completeness, not recommended.

## 7. Recommended design and rationale

**Option 1.** It is the only one of the three that satisfies every stated requirement simultaneously:
- *Additive and backwards compatible*: one new optional model field, one new optional column, no rename, no breaking change to any existing reader.
- *Avoids immediate historical backfill*: old records keep their existing `matched_companies` value untouched; `identified_companies` is simply empty for them, honestly reflecting that it was never computed for that record.
- *Preserves current user-visible behavior for correctly attributed stories*: proven by fixture row 5 (§5) — the dominant, single-company baseline case is already correct today and remains byte-identical after this change, for both old and new records.
- *Removes invalid card-company tags for new records*: proven by fixture rows 2a/3/4 (§5) — new records get `matched_companies` populated from the actually-vetted `_identified_subject_companies()` result, not the raw match.
- *Makes historical-record behavior explicit*: §5's honest-caveat paragraph and §9 below state plainly that old records may continue showing over-inclusive tags until they age out of the recency window — a documented, understood tradeoff, not a silent gap.
- *Creates a trustworthy foundation for future relationship intelligence*: `identified_companies` becomes exactly the broad, diagnostic, "every company recognized" pool the product contract describes, cleanly separated from `matched_companies`'s narrower, display-safe, "validated subject" meaning — the two-field shape a future relationship-graph candidate-generation process would actually want to consume from.

It also has the smallest actual blast radius of the three (one construction function, one dataclass widening, one model/schema addition) precisely because every consumer already reads the field named `matched_companies` — fixing what flows into that name at its single construction point is enough to correct every downstream reader without touching them.

## 8. Proposed implementation file list (not applied — for a future, separately-approved implementation task)

| File | Change |
|---|---|
| `src/models/daily_news_models.py` | Add `identified_companies: tuple[str, ...] = ()` to `EditorialStory`; update its docstring to state the two fields' distinct contracts explicitly |
| `src/data_access/daily_news/editorial_admission.py` | Widen `AdmissionDecision` with an additive `identified_companies: tuple[str, ...] = ()` field; thread `_identified_subject_companies()`'s real result through every `return AdmissionDecision(...)` inside `assess_admission()`; rename `_identified_subject_companies()` to avoid the §4 naming collision |
| `src/data_access/daily_news/editorial_pipeline.py` | At construction (~line 458), pass `identified_companies=matched_companies` (the raw tuple, preserved) and `matched_companies=admission.identified_companies` (the vetted subset) — the one core logic change this whole design hinges on |
| `src/data_access/daily_news/editorial_story_store.py` | Add `identified_companies` to the JSON write path (`asdict`-based, likely automatic) and read path (`_story_from_dict`, one new `.get("identified_companies", ())`) |
| `src/data_access/postgres_state_db/editorial_story_repository.py` | Add `identified_companies_json` read/write, mirroring `matched_companies_json` exactly |
| `src/data_access/postgres_state_db/schema.py` | Additive `ALTER TABLE editorial_stories ADD COLUMN identified_companies_json TEXT NOT NULL DEFAULT '[]'` (or equivalent CREATE-time column for a fresh schema) |

No change anticipated in `editorial_coverage.py`, `recently_updated.py`, `daily_news.py`, or `theme_evidence.py` (§3's per-surface table — all inherit correctness automatically).

## 9. Old-record / backwards-compatibility policy

1. A record persisted before this change deserializes with `identified_companies = ()` — explicitly empty, never inferred or reconstructed automatically.
2. That same old record's existing `matched_companies` value is left completely untouched — no rewrite, no reinterpretation, no silent "upgrade."
3. Consequence, stated plainly: an old record whose raw match included an incidental/unvalidated company will continue to display that company as a card tag and appear under that company's filter until either (a) it ages out of the relevant surface's own recency window naturally, or (b) a future, separately-approved backfill task recomputes it from its still-preserved `headline`/`excerpt`.
4. This policy is a deliberate, documented tradeoff — not a gap discovered later. It mirrors the exact precedent already set by `materiality_tier`'s own "None means not yet classified, never backfilled automatically" contract on this same model.
5. Before implementation, the open question flagged in §3/§6 (whether production Postgres already holds real `EditorialStory` rows) should be answered with a simple read-only row count — this changes the *practical* exposure of point 3 above, though not the recommended policy itself.

## 10. Proposed test plan and commands (for a future implementation task — not run here)

New/changed test coverage, organized by the existing file each belongs in:
- `tests/test_editorial_admission.py`: extend `AdmissionDecision`-returning assertions to also check the new `identified_companies` field; add the two currently-missing real-text fixtures for §5 rows 3 and 4 (a genuine two-issuer supply-relationship headline, a genuine multi-company joint-deployment headline) run through the real `assess_admission()` path, not hand-set tuples.
- `tests/test_editorial_pipeline.py`: end-to-end construction test proving `identified_companies` (raw) and `matched_companies` (vetted) can differ for a real multi-company-raw-match item where only one company is a genuine subject — this is the test that would have caught today's gap and doesn't exist yet.
- `tests/test_editorial_story_store.py`: round-trip test for `identified_companies` including an old-record-missing-the-field deserialization case (defaults to `()`).
- `tests/test_state_db_postgres_editorial_story_repository.py`: same round-trip proof for the Postgres column.
- No changes anticipated in `test_editorial_coverage_component.py`, `test_daily_news_page.py`, `test_dashboard_latest_item.py`, `test_theme_evidence.py`, `test_daily_news_backend.py`, `test_government_editorial_sources.py` — their existing assertions against `matched_companies` remain valid under the new semantics without modification, since the *contract* at their consumption point (matched_companies = display-safe) doesn't change, only what correctly satisfies it.

Proposed validation commands for that future task (not run in this discovery pass):
```bash
pytest tests/test_editorial_admission.py tests/test_editorial_matching.py tests/test_editorial_pipeline.py -q
pytest tests/test_editorial_story_store.py tests/test_state_db_postgres_editorial_story_repository.py -q
pytest tests/test_daily_news_page.py tests/test_editorial_coverage_component.py tests/test_dashboard_latest_item.py tests/test_theme_evidence.py -q
pytest tests/ -k "editorial or daily_news" -q
git diff --check
```

## 11. Explicit non-goals

- No model, schema, migration, registry, source, admission rule, materiality rule, UI, cache/database record, or existing document was modified by this task.
- No worker, discovery, live ingestion, or polling was run.
- No production persistence was accessed or written.
- No historical backfill is proposed as part of the recommended design — explicitly deferred to a future, separately-approved task, if ever undertaken.
- No relationship-graph, second-order inference, or automated Signal-generation logic is proposed or built — `identified_companies` is described only as a *future input pool* for that eventual, separately-scoped work, consistent with `[[COMPANY_UNIVERSE_AND_SUPPLY_CHAIN_AUDIT_PLAN_2026_09_16]]`'s own relationship-model proposal, which this document does not re-litigate or extend.
- No Dashboard, Market Map, Themes UI, worker/CLI, polling, deployment, or source-adapter change is proposed.
- No resolution of the internal-helper naming collision (§4) is performed here — flagged for the implementation task to handle, not resolved in this discovery pass.

## 12. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Old records carry silently-different `matched_companies` semantics than new records under the same field name | §9's explicit, documented policy — never hidden; a future UI could optionally surface a "classified before this date" marker if the ambiguity proves user-visible in practice, though that is not proposed here |
| Unknown production Postgres row count/state | Recommend a read-only row count as the very first step of any future implementation task, before assuming low practical exposure |
| Naming collision between the admission module's internal `_identified_subject_companies()` and the new model-level `identified_companies` (opposite meanings) | §4/§8 — rename the internal helper during implementation |
| `AdmissionDecision`'s widened return shape touches five `return` statements inside `assess_admission()` | Careful line-by-line review at implementation time; the dataclass widening itself is additive and safe, the risk is a missed branch, not a breaking change |
| The two multi-company fixture patterns (§5 rows 3-4) most central to this fix's real-world value have the thinnest existing test coverage | §10's test plan explicitly calls for new, from-real-text fixtures for exactly these two patterns, not just re-verifying already-hand-set ones |
| A future reader could mistake "no fixture currently proves the raw/admitted gap causes a live user-visible defect" for "no gap exists" | §0 and §5 state this distinction explicitly and repeatedly — the gap is structurally real and directly traceable in code, even though it hasn't yet been empirically caught by an existing test |

## 13. Files inspected

- `src/models/daily_news_models.py` (full `EditorialStory`/`NewsStory` definitions)
- `src/data_access/daily_news/editorial_matching.py` (full file, 150 lines)
- `src/data_access/daily_news/editorial_admission.py` (full file, 704 lines, particularly `_identified_subject_companies`/`assess_admission`)
- `src/data_access/daily_news/editorial_pipeline.py` (the full `run_editorial_discovery()` function and its construction site)
- `src/data_access/daily_news/materiality_classification.py` (`classify_editorial_story` and its call-site placement)
- `src/data_access/daily_news/editorial_story_store.py` (full file)
- `src/data_access/postgres_state_db/editorial_story_repository.py` and `src/data_access/postgres_state_db/schema.py` (`editorial_stories` table)
- `src/ui/components/editorial_coverage.py`, `src/ui/components/recently_updated.py`, `src/ui/pages/daily_news.py` (card rendering, Dashboard rendering, company filter)
- `src/logic/theme_evidence.py` (evidence-overlap consumer)
- `scripts/daily_news_quality_review.py` (confirmed as the only script-level surface, non-production, no persistence)
- `data/cache/` (checked directly for a persisted `daily_news_editorial_stories.json` — confirmed absent; `data/cache/daily_news_stories.json`, the unrelated issuer-lane cache, read for one sample record to confirm its different shape)
- Tests: `tests/test_editorial_admission.py`, `tests/test_editorial_matching.py`, `tests/test_editorial_pipeline.py`, `tests/test_daily_news_page.py`, `tests/test_editorial_coverage_component.py`, `tests/test_dashboard_latest_item.py`, `tests/test_editorial_story_store.py`, `tests/test_state_db_postgres_editorial_story_repository.py`, `tests/test_theme_evidence.py`, `tests/test_daily_news_backend.py`, `tests/test_government_editorial_sources.py`, `tests/test_daily_news_historical_signals_audit.py` (all grepped for `matched_companies`; the RTX/multi-company/baseline fixtures read in full)
- `design/POST_MERGE_SIGNALS_LIVE_CARD_AUDIT_2026_09_16.md` (cross-referenced for the original smartARM/Meta live-card finding this task's fixture #2 is drawn from)

## Confirmations

- **Exact file changed**: one — this document, `design/DAILY_NEWS_IDENTIFIED_VS_MATCHED_COMPANIES_DISCOVERY_2026_09_16.md`, newly created and untracked. No other file was created, modified, or deleted.
