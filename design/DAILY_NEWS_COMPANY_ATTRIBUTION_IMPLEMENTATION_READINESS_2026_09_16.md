# Daily News Company Attribution — Implementation Readiness Review

Status: **read-only readiness review**. No production code, test, model, persistence schema, existing report, configuration, source registry, UI, or cache/database record was modified. Builds directly on `design/DAILY_NEWS_IDENTIFIED_VS_MATCHED_COMPANIES_DISCOVERY_2026_09_16.md`'s findings and Option 1 recommendation; this document does not re-litigate that analysis, only sharpens it into exact, verified implementation mechanics — several of them now confirmed by actually running the real pipeline functions read-only against candidate fixture text (no persistence, no cache/database write), not merely inferred from reading code.

## 0. Approved target contract (restated, unchanged)

- `EditorialStory.identified_companies`: additive, raw-recognition, diagnostic-only. Must never by itself create a visible tag or filter match.
- `EditorialStory.matched_companies`: unchanged name and consumer contract — only the *companies that passed attribution* — used by existing cards, Dashboard, filter, and future second-order research.
- No UI redesign. No historical backfill. Old records keep their existing `matched_companies` value untouched; a safe empty default applies to `identified_companies` for any record that predates the field, unless a current deserialization convention on this model already dictates otherwise (§1 confirms it does not — the existing convention *is* the safe-empty-default pattern).

## 1. `EditorialStory` model field mechanics — exact

File: `src/models/daily_news_models.py`. `EditorialStory` is declared `@dataclass(frozen=True)` at line 139-140. Relevant existing fields, verbatim (lines 167-175):

```python
matched_companies: tuple[str, ...]  # zero-to-many real TrackedCompany.name values
matched_themes: tuple[str, ...]  # one-to-many theme slugs
source_feed_id: str  # ...
materiality_tier: NewsMaterialityTier | None = None
materiality_reasons: tuple[str, ...] = ()
```

**Proposed field declaration** — placed after `matched_companies`/`matched_themes` and before `source_feed_id` would break positional-argument compatibility for any caller still using positional construction; placed at the end (after `materiality_reasons`, matching the file's own established pattern of appending new optional fields last, exactly how `materiality_tier`/`materiality_reasons` were themselves added) is the correct, lowest-risk position:

```python
    materiality_tier: NewsMaterialityTier | None = None
    materiality_reasons: tuple[str, ...] = ()
    # Raw company recognition (design/DAILY_NEWS_IDENTIFIED_VS_MATCHED_
    # COMPANIES_DISCOVERY_2026_09_16.md) — every tracked company whose
    # alias appears in the source title/description, BEFORE any
    # attribution/admission check. Diagnostic/audit/future-relationship-
    # intelligence input only — never itself a visible tag or filter
    # match; see matched_companies for the attributed subset that
    # actually drives display. Additive, safe-default: () for every
    # record persisted before this field existed, never backfilled
    # automatically — same contract as materiality_tier above.
    identified_companies: tuple[str, ...] = ()
```

- **Type**: `tuple[str, ...]`, identical shape to `matched_companies` — no new type introduced.
- **Default**: `()` — required, since it's appended after fields that already have defaults (`materiality_tier`, `materiality_reasons`); Python dataclasses require every field after the first defaulted field to also carry a default. This is also exactly the desired backward-compatible behavior per the approved contract.
- **Equality/repr**: no special handling needed. `EditorialStory` uses the dataclass-generated `__eq__`/`__repr__` (no `eq=False`/`repr=False` override present anywhere in the class or its decorator) — adding a defaulted field changes both automatically and safely; no test asserting exact `repr()` output was found (only field-level assertions), so this is not a breaking change to any existing test.
- **Serialization**: `editorial_story_store.save_stories()` uses `dataclasses.asdict(story)` (confirmed at `editorial_story_store.py`, import line 9: `from dataclasses import asdict`) — this walks every dataclass field automatically, so `identified_companies` requires **zero code change** on the write side; it will be included in the JSON output the moment the field exists on the dataclass.
- **Deserialization / backwards compatibility for field-absent records**: `editorial_story_store._story_from_dict()` (lines 31-43) already establishes the exact convention to follow — every optional field is read via `.get(key, default)`:
  ```python
  matched_companies=tuple(data.get("matched_companies", ())),
  ```
  The proposed addition is the direct mirror:
  ```python
  identified_companies=tuple(data.get("identified_companies", ())),
  ```
  For a record written before this field existed, `data.get("identified_companies", ())` returns `()` — **this is the "current model-deserialization convention" the task asks about, and it already exists and already produces exactly the safe-empty-default the approved contract asks for.** No new convention needs inventing.
- **Postgres**: schema at `src/data_access/postgres_state_db/schema.py:583-596`, table `editorial_stories`, existing column `matched_companies_json TEXT NOT NULL DEFAULT '[]'` (line 596). Proposed additive column, same convention: `identified_companies_json TEXT NOT NULL DEFAULT '[]'`. `_row_to_story()` (`editorial_story_repository.py:26-38`) reads `matched_companies_json` via `json.loads(row["matched_companies_json"])` unconditionally (no `.get`, since the DB guarantees `NOT NULL DEFAULT '[]'`) — the proposed `identified_companies_json` read follows the identical unconditional-read pattern, relying on the same DB-level default rather than an application-level `.get()`, consistent with how this file already treats `matched_companies_json`/`matched_themes_json`.

## 2. Exact raw-company recognition construction point

File: `src/data_access/daily_news/editorial_pipeline.py`, inside `run_editorial_discovery()`.

- Line 361: `matched_companies, matched_themes = matched_companies_and_themes(entry.title, classification_summary)` — this is the one and only place raw recognition happens for the production pipeline. This is the value that must be preserved, unchanged, as the future `identified_companies` argument at construction.
- Line 458-465 (current): `EditorialStory(..., matched_companies=matched_companies, matched_themes=matched_themes, ...)` — the same raw tuple from line 361, currently reused verbatim as `matched_companies`.

**Proposed change at construction** (line ~461-462):
```python
story = EditorialStory(
    id=story_id, headline=entry.title, publisher=source.attribution_label, source_url=entry.link,
    published_at=entry.published_at, retrieved_at=retrieved_at,
    excerpt=_extractive_excerpt(entry.summary),
    identified_companies=matched_companies,            # the raw tuple, preserved verbatim
    matched_companies=admission.identified_companies,  # the admission-vetted subset (see §3)
    matched_themes=matched_themes,
    source_feed_id=source.source_id,
    materiality_tier=materiality_tier, materiality_reasons=materiality_reasons,
)
```
This is the single core logic change the entire design hinges on — one call site, two keyword arguments changed.

## 3. Exact admission result/property representing vetted per-company subjects

File: `src/data_access/daily_news/editorial_admission.py`.

`AdmissionDecision` (line 559-562) currently:
```python
@dataclass(frozen=True)
class AdmissionDecision:
    admitted: bool
    reason: str
```

`_identified_subject_companies(text, title, matched_companies) -> list[str]` (lines 565-597) already computes the exact per-company-vetted list the product contract wants — it is called once, at the top of `assess_admission()` (line 633: `identified_companies = _identified_subject_companies(text, title, matched_companies)`), but its result is discarded after being used only to pick `identified_companies[0]` for a diagnostic string.

**Exact count and disposition of every return site** — re-verified directly this session (not estimated): `assess_admission()` contains **13 `return AdmissionDecision(...)` statements** (lines 638, 639, 653, 654, 664, 665, 684, 686, 687, 691, 693, 702, 704). Each falls into one of three mechanical categories:

| Pattern in current code | Count | Proposed `identified_companies` value on the new field |
|---|---|---|
| `AdmissionDecision(True, f"company_subject:{identified_companies[0]}")` | 5 (lines 638, 653, 664, 684, 691) | The full local `identified_companies` list, as `tuple(identified_companies)` |
| `AdmissionDecision(True, f"theme_subject:{matched_themes[0]}")` | 2 (lines 686, 702) | `()` — admitted via theme, not a company subject |
| `AdmissionDecision(False, ...)` (every rejection) | 6 (lines 639, 654, 665, 687, 693, 704) | `()` — rejected, nothing persists regardless (§4) |

**Proposed widened dataclass**:
```python
@dataclass(frozen=True)
class AdmissionDecision:
    admitted: bool
    reason: str  # safe, human-readable diagnostic — never raw feed content beyond the matched phrase itself
    identified_companies: tuple[str, ...] = ()  # the vetted per-company subject list — () when admitted via theme only, or rejected
```
The new field is additive with a default, so the two-positional-argument construction pattern used at all 13 existing call sites remains valid without immediate edits — though the whole point of this change is to *also* pass the third argument at the 5 `company_subject` sites, so all 13 call sites are touched in practice (7 gain an explicit `()`  for clarity/consistency even though the default would supply it, 5 gain the real list; conventions at implementation time may choose to rely on the default for the `()` cases instead of writing it explicitly — either is correct, a style choice, not a correctness one). Confirmed by direct grep this session: **`AdmissionDecision` is constructed nowhere outside `editorial_admission.py` itself** — no external caller or test directly constructs it, so this widening carries zero external-caller breakage risk.

At the pipeline call site (`editorial_pipeline.py:427-430`), `admission.identified_companies` becomes available for use exactly as shown in §2.

## 4. Renaming the admission helper to avoid the naming collision

`_identified_subject_companies()` (module-private, `editorial_admission.py:565`) currently means "the vetted, admission-attributed subject list" — the *opposite* of what the new model-level `EditorialStory.identified_companies` field means (raw, pre-attribution). Left unrenamed, a future reader searching the codebase for "identified" would find two functions/fields with contradictory meanings in the same feature area.

**Proposed rename**: `_identified_subject_companies()` → `_admission_attributed_companies()` (or `_vetted_subject_companies()` — either is acceptable; `_admission_attributed_companies` more directly mirrors the model field's own eventual "attributed" framing used in this document and the prior discovery doc). This is a pure rename — same signature, same body, same call site (line 633) — with its own docstring already accurately describing the "vetted subject" behavior (lines 568-574), needing no rewording, only the name itself and its one call site updated. Grep confirms this function has exactly one call site (`editorial_admission.py:633`) and no external importers — a self-contained, zero-risk rename.

This rename is explicitly permitted by the task's own exclusions ("no materiality/admission-policy change, except an internal helper rename if required purely to clarify semantics") — it changes no behavior, only a private name.

## 5. Ensuring rejected items remain non-persisted with no stored raw metadata

**Already true today, unchanged by this design.** Re-confirmed by direct re-reading of `editorial_pipeline.py:427-434`: on `not admission.admitted`, the loop does `items_not_subject_relevant += 1; ...; continue` — execution never reaches the `EditorialStory(...)` construction at line 458. This proposal does not touch that control flow at all — `identified_companies`/`matched_companies` are only ever set at construction time, and construction is skipped entirely for a rejected item, exactly as before. The only in-memory trace of a rejected item's raw match remains `EditorialScanReport.rejected_examples` (title + reason strings, dry-run-only, never persisted) — unchanged by this design, and out of scope (§9).

**Empirically re-verified this session** (read-only, no persistence) using the real RTX hobbyist-mod fixture already in `tests/test_editorial_admission.py::test_rtx_laptop_power_limit_unofficial_tool_is_rejected` (title: "Developer vibe codes a tool to let Nvidia RTX 50-series laptop owners crank up their power limits..."): running it through the actual `matched_companies_and_themes()` → `classify_editorial_story()` → `assess_admission()` chain confirms `matched_companies=('NVIDIA',)` raw, `admission.admitted=False`, `reason='hobbyist_modding_format:vibe codes'` — the item never reaches construction under either the current or proposed code (this proposal changes what gets written at construction, not whether construction happens).

## 6. Ensuring multi-company stories retain every directly attributable company

`_admission_attributed_companies()` (proposed rename, §4) already iterates every entry in `matched_companies`, not just the first — `identified.append(company)` inside a `for company in matched_companies:` loop (line 585 today). It was always capable of returning more than one company; the current bug is not that it *collapses* multi-company subjects, it's that its full list is discarded and never reaches the persisted model. Once `AdmissionDecision.identified_companies` carries the full list through (§3) and the pipeline uses it verbatim for `matched_companies` (§2), no company present in that list is dropped — the fix is a pass-through, not a re-filter.

**Empirically re-verified this session** with a from-scratch, clearly-labeled synthetic fixture matching the CoreWeave/NVIDIA pattern (title: "CoreWeave deploys new NVIDIA GB200 cluster to expand AI cloud capacity"): raw `matched_companies=('CoreWeave, Inc.', 'NVIDIA')`, and `_admission_attributed_companies()` (current name `_identified_subject_companies`) independently returns **both** `['CoreWeave, Inc.', 'NVIDIA']` — both companies are title-placed, both pass identity checking, neither is dropped. Full trace in §7 fixture 3.

## 7. Ensuring a raw-mention-only item either rejects or persists with a documented empty `matched_companies`

Both outcomes already exist in the current admission model and both are preserved unchanged by this design — this proposal changes *what gets recorded*, not *which of these two outcomes occurs* for a given item:

- **Reject entirely**: when `matched_companies` (raw) is non-empty but none are identified AND no theme matches either — `AdmissionDecision(False, "company_mention_not_subject_worthy")` (line 693). Real, existing example: `tests/test_editorial_admission.py::test_incidental_customer_mention_in_an_unrelated_labor_story_fails_admission` (LS Cable & System labor story incidentally naming SK Hynix/Samsung Electronics as customers — both raw-matched, neither identified, no theme match, whole item rejected).
- **Persist with an intentionally empty `matched_companies`**: when a theme matches independently of any company subject — `AdmissionDecision(True, f"theme_subject:{matched_themes[0]}")` with `identified_companies=()` (line 686 or 702). **This is exactly "where current policy allows"** the task's own required-investigation wording anticipates — theme-only admission is an already-established, already-tested admission path (`assess_admission`'s own final `if matched_themes: return AdmissionDecision(True, ...)` branch, line 695-702, documented in its own docstring as "relevant but not yet material — Watchlist — is for"), not a new policy this design introduces.

**Empirically re-verified this session** with a clearly-labeled synthetic fixture (title: "Startup smartARM unveils new humanoid robot arm for home assistance tasks", description naming a private robotics startup whose product optionally pairs with third-party VR headsets "including headsets from Meta Platforms and other manufacturers"): raw `matched_companies=('Meta Platforms, Inc.',)`, `matched_themes=('humanoids',)`, `admission.admitted=True`, `reason='theme_subject:humanoids'`, and `_admission_attributed_companies()` independently returns `[]` — Meta Platforms is raw-matched but never identified (no title placement, no action language — a passive "including headsets from" listing). **Under today's code this story would persist with `matched_companies=('Meta Platforms, Inc.',)`, producing a misleading Meta chip and Meta-filter membership. Under the proposed design it would persist with `identified_companies=('Meta Platforms, Inc.',)` and `matched_companies=()`.** This is the first *executed*, not merely structurally inferred, confirmation of the exact gap the discovery document described as "not yet empirically observed" — obtained this session by tracing real fixture text through the real, unmodified pipeline functions, with no code change and no persistence.

## 8. Fixture matrix — exact text and verified assertions

All six fixtures below were traced this session by directly calling `matched_companies_and_themes()`, `classify_editorial_story()`, `assess_admission()`, and (for identity) the current `_identified_subject_companies()` against the exact text shown — read-only, in-process, no file/cache/database write. Provenance is stated per fixture; nothing is guessed.

| # | Fixture | Provenance | Raw `matched_companies` (today, = proposed `identified_companies`) | `matched_themes` | Admission outcome (unchanged by this design) | Proposed `matched_companies` (post-fix) |
|---|---|---|---|---|---|---|
| 1 | **Valid single-company official issuer announcement.** Title: `"Oracle Corporation reports strong AI cloud demand"`. Description: `"Oracle Corporation said AI cloud demand drove revenue higher."` | **Real, existing, reused verbatim** — `tests/test_editorial_pipeline.py::test_qualifying_item_is_published` (lines 50-65), which runs the real, full `run_editorial_discovery()` end-to-end, not a hand-set fixture | `("Oracle Corporation",)` | `("ai-buildout",)` | Admitted, `company_subject:Oracle Corporation` | `("Oracle Corporation",)` — **identified == matched == Oracle Corporation, exactly as required**; identical to today's already-correct output for this baseline case, confirmed no regression |
| 2 | **Valid Generac/Amazon-style named supply agreement.** Title: `"Generac Holdings signs multi-year supply agreement with Amazon Web Services to provide backup power systems for data centers"`. Description: `"Generac Holdings announced a new multi-year supply agreement under which Generac Holdings will provide backup power generation systems for Amazon Web Services data center facilities. Amazon Web Services confirmed the agreement as part of its data center power resiliency buildout."` | **Synthetic — labeled as such.** No real published article was used; this is authored text designed to trigger this codebase's own real alias vocabulary. Note: bare "Amazon"/"Generac" alone are deliberately excluded from the alias list as ambiguous single-word brands (`company_aliases.py`, confirmed this session) — the full mechanical aliases "Generac Holdings" and the curated brand alias "Amazon Web Services" were used instead, verified to actually match | `("Amazon.com, Inc.", "Generac Holdings Inc.")` | `()` | Admitted, `company_subject:Amazon.com, Inc.` (materiality: HIGH_SIGNAL, `credible_editorial_reporting:confirmed`) | `("Amazon.com, Inc.", "Generac Holdings Inc.")` — **both companies independently pass identity checking** (both title-placed); **identified == matched == {Generac, Amazon}, exactly as required** |
| 3 | **Valid CoreWeave/NVIDIA-style named infrastructure deployment.** Title: `"CoreWeave deploys new NVIDIA GB200 cluster to expand AI cloud capacity"`. Description: `"CoreWeave announced it has deployed a new cluster of NVIDIA GB200 GPUs, expanding its AI cloud infrastructure capacity. CoreWeave said the NVIDIA-powered cluster will support enterprise AI training workloads."` | **Synthetic — labeled as such.** Authored text; both company names use their real, already-verified aliases | `("CoreWeave, Inc.", "NVIDIA")` | `("ai-buildout",)` | Admitted, `company_subject:CoreWeave, Inc.` | `("CoreWeave, Inc.", "NVIDIA")` — **both companies independently pass identity checking; identified == matched == direct, attributable parties, exactly as required** |
| 4 | **Private third-party/startup story that merely mentions a tracked company.** Title: `"Startup smartARM unveils new humanoid robot arm for home assistance tasks"`. Description: `"smartARM, a private robotics startup, unveiled a new humanoid robot arm this week aimed at home assistance tasks. The arm can be paired over Bluetooth with a variety of third-party VR headsets for remote teleoperation, including headsets from Meta Platforms and other manufacturers."` | **Synthetic — labeled as such.** Modeled on the real smartARM/Meta live-card pattern named in the discovery document and the prior live-card audit, but authored fresh (the real audited item was a different Lane, per the discovery doc's own finding) and deliberately engineered so the story admits via a genuine theme match (`humanoids`) rather than via any company subject, isolating the exact "incidental mention inside an otherwise-admissible story" case | `("Meta Platforms, Inc.",)` | `("humanoids",)` | Admitted, `theme_subject:humanoids` | `()` — **Meta Platforms is excluded from `matched_companies` (no title placement, no action language — a passive listing); it appears only in `identified_companies`, exactly as required.** This is the one fixture in this matrix that demonstrates a real behavior *change* between today's code and the proposed design (today: misleading Meta chip; proposed: none) |
| 5 | **Rejected RTX hobbyist/modding fixture.** Title: `"Developer vibe codes a tool to let Nvidia RTX 50-series laptop owners crank up their power limits — can juice RTX 5090 mobile GPU to 225W"`. Description: (full text as in the real test) | **Real, existing, reused verbatim** — `tests/test_editorial_admission.py::test_rtx_laptop_power_limit_unofficial_tool_is_rejected` (lines 1149-1165) | `("NVIDIA",)` (raw, momentarily computed) | `()` | **Rejected**, `hobbyist_modding_format:vibe codes` | N/A — item never constructed. **Zero persisted stories, zero persisted metadata of any kind (identified or matched), exactly as required.** Unchanged by this design (§5) |
| 6 | **Old serialized record missing `identified_companies`.** A JSON object written directly to a `tmp_path` cache file (bypassing `save_stories()`, to simulate a genuinely pre-migration on-disk record), containing every current field (`id`, `headline`, `publisher`, `source_url`, `published_at`, `retrieved_at`, `excerpt`, `matched_companies: ["Oracle Corporation"]`, `matched_themes: ["ai-buildout"]`, `source_feed_id`) but **no `identified_companies` key at all** | **Synthetic — labeled as such** (a deliberately-constructed legacy-shape fixture; no real production JSON file exists to draw from, per the discovery document's confirmed finding that no `daily_news_editorial_stories.json` exists locally) | N/A (not a matching fixture — a deserialization fixture) | N/A | N/A | Loads successfully via `editorial_story_store.load_stories(tmp_path)`; `story.matched_companies == ("Oracle Corporation",)` (**unchanged, exactly as stored**); `story.identified_companies == ()` (**the safe empty default from `.get("identified_companies", ())`, per §1 — exactly as required**) |

## 9. Explicit exclusions — confirmed still respected by every mechanic above

No historical migration/backfill (§1, §9 of the discovery doc — old records keep their stored `matched_companies` untouched, §8 fixture 6); no UI redesign or card-component change (§2 of the discovery doc's per-surface table — every consumer inherits correctness automatically, confirmed again in this pass by tracing that no UI file needs to change); no Dashboard/route/filter visual change (same reasoning); no materiality/admission-policy change beyond the internal helper rename (§4 — a pure rename, zero behavior change, explicitly permitted); no source/parser/polling/worker/CLI change (nothing in §1-§8 touches any of those); no company additions/ontology/relationship-graph/private-entity-model/second-order-inference (nothing proposed here builds any of that — `identified_companies` is described only as a future input pool, same as the discovery doc); no database/cache writes performed by this review (every trace in §5-§8 ran in-process against pure functions or a pytest `tmp_path`-equivalent read-only investigation — actually the exact commands run this session used plain Python function calls with no filesystem write at all, confirmed by the commands used); no live discovery/worker run/ingestion; no staging/commit/push/PR/merge/deploy/stash/untracked-artifact modification.

## 10. Recommended final implementation design

Unchanged from the discovery document's Option 1, now fully specified at the exact-mechanics level: add `EditorialStory.identified_companies: tuple[str, ...] = ()` (§1); widen `AdmissionDecision` with an additive `identified_companies: tuple[str, ...] = ()` field and thread `_admission_attributed_companies()`'s (renamed, §4) real result through all 13 return sites (§3); at the single `EditorialStory(...)` construction site in `editorial_pipeline.py`, pass the preserved raw tuple as `identified_companies` and `admission.identified_companies` as `matched_companies` (§2); extend both persistence backends' read/write paths additively (§1). Every UI/filter/evidence consumer requires zero code change — correctness is inherited from the single construction point.

### Exact field semantics (final)

| Field | Contains | Populated from | Consumed by |
|---|---|---|---|
| `identified_companies` | Every tracked company whose alias appears in title+description — raw, pre-attribution | The preserved output of `matched_companies_and_themes()` at construction | Nothing yet (diagnostic/audit/future relationship intelligence only, per the approved contract — no consumer wired in this task) |
| `matched_companies` | Only companies independently vetted as the story's genuine subject | `AdmissionDecision.identified_companies` (the renamed `_admission_attributed_companies()`'s real return value) | Card tags (`editorial_coverage.py`), Dashboard rows (`recently_updated.py`), company filter (`editorial_pipeline.select_visible_editorial_stories_for_company`), evidence overlap (`theme_evidence.py`) — unchanged code, corrected data |

## 11. Exact anticipated files and functions

| File | Function(s) | Change |
|---|---|---|
| `src/models/daily_news_models.py` | `EditorialStory` | Add `identified_companies: tuple[str, ...] = ()` field + docstring update (§1) |
| `src/data_access/daily_news/editorial_admission.py` | `AdmissionDecision`, `_identified_subject_companies` → `_admission_attributed_companies`, `assess_admission` | Add field to dataclass; rename helper; thread its result through all 13 `return AdmissionDecision(...)` sites (§3, §4) |
| `src/data_access/daily_news/editorial_pipeline.py` | `run_editorial_discovery` | At the one `EditorialStory(...)` construction call, pass `identified_companies=` (raw, preserved) and `matched_companies=admission.identified_companies` (§2) |
| `src/data_access/daily_news/editorial_story_store.py` | `_story_from_dict` (read side; write side needs no change — `asdict` is automatic) | Add `identified_companies=tuple(data.get("identified_companies", ()))` (§1) |
| `src/data_access/postgres_state_db/editorial_story_repository.py` | `_row_to_story` (read side); the insert/upsert write path | Add `identified_companies_json` column read/write, mirroring `matched_companies_json` (§1) |
| `src/data_access/postgres_state_db/schema.py` | `editorial_stories` table definition | Additive `identified_companies_json TEXT NOT NULL DEFAULT '[]'` (§1) |

Test files:

| File | Change |
|---|---|
| `tests/test_editorial_admission.py` | Extend `AdmissionDecision`-returning assertions to also check `identified_companies`; add fixtures 2/3/4 from §8 (currently-uncovered multi-company and theme-admitted-incidental-mention patterns) run through the real `_assess()` helper |
| `tests/test_editorial_pipeline.py` | Extend `test_qualifying_item_is_published`-style end-to-end assertions to also check `story.identified_companies`; add a new end-to-end multi-company construction test proving `identified_companies` and `matched_companies` can genuinely differ for a real pipeline run (fixture 4's shape) |
| `tests/test_editorial_story_store.py` | Add fixture 6 (§8) — old-record-missing-the-field deserialization; add a normal round-trip test for the new field on a freshly-constructed story |
| `tests/test_state_db_postgres_editorial_story_repository.py` | Same round-trip proof for the Postgres column (confirmed via the discovery pass as one of the 8 existing `matched_companies`-referencing hits in this file — the natural home for the mirrored test) |

**Verified as unnecessary, per the task's own "include them only if demonstrably necessary" instruction**: no `test_daily_news_models.py` exists (confirmed by direct search this session — no such file anywhere in `tests/`), and no other file constructs `EditorialStory(...)` in a way that would break under an additive, defaulted field (`test_daily_news_page.py`, `test_dashboard_latest_item.py`, `test_daily_news_backend.py`, `test_theme_evidence.py`, `test_editorial_coverage_component.py` all construct `EditorialStory` via local `_story()`-style helpers that pass keyword arguments only for the fields they care about — an added field with a default requires zero change to any of them unless a specific new test wants to exercise a non-default `identified_companies` value, which is optional polish, not a requirement).

## 12. Backward-compatible old-record policy (final)

1. A record persisted before this change deserializes with `identified_companies = ()` — via the existing, already-established `.get(key, default)` convention this model already uses for every other optional field (§1). This is not a new convention being invented for this task; it is the codebase's own pre-existing pattern, simply applied to a new field.
2. That record's existing `matched_companies` value is never rewritten, never reinterpreted, never touched.
3. No backfill, migration, or rewrite of historical records is performed or proposed as part of this design.
4. Consequence, stated plainly (unchanged from the discovery document): an old record whose raw match included an incidental/unvalidated company continues to display it until it ages out of the relevant surface's own recency window, or until a future, separately-approved backfill task (not proposed here) recomputes it from the still-preserved `headline`/`excerpt`.

## 13. Exact implementation/validation commands (for a future, separately-approved implementation task — not run here)

```bash
pytest tests/test_editorial_admission.py tests/test_editorial_matching.py tests/test_editorial_pipeline.py -q
pytest tests/test_editorial_story_store.py tests/test_state_db_postgres_editorial_story_repository.py -q
pytest tests/test_daily_news_page.py tests/test_editorial_coverage_component.py tests/test_dashboard_latest_item.py tests/test_theme_evidence.py tests/test_daily_news_backend.py -q
pytest tests/ -k "editorial or daily_news" -q
git diff --check
```

## 14. Recommended atomic commit layout (not created in this task)

1. **Model / backward compatibility** — `src/models/daily_news_models.py` + `src/data_access/daily_news/editorial_story_store.py` + `src/data_access/postgres_state_db/editorial_story_repository.py` + `src/data_access/postgres_state_db/schema.py`, plus `tests/test_editorial_story_store.py` and `tests/test_state_db_postgres_editorial_story_repository.py`. Self-contained: the field and its persistence round-trip can be proven correct (including the old-record-missing-field case, fixture 6) before anything reads or writes it meaningfully elsewhere.
2. **Pipeline construction and admission-helper naming** — `src/data_access/daily_news/editorial_admission.py` (rename + `AdmissionDecision` widening) + `src/data_access/daily_news/editorial_pipeline.py` (the construction-site change). These two are tightly coupled (the pipeline change is meaningless without the admission change) and belong together.
3. **Test fixtures and regression coverage** — the new/extended assertions in `tests/test_editorial_admission.py` and `tests/test_editorial_pipeline.py` for fixtures 2-4 (§8), proving the previously-uncovered multi-company and theme-admitted-incidental-mention patterns now behave as intended.

This ordering lets commit 1 be reviewed and merged (or reverted) independently of behavior change, commit 2 be the single behavior-changing commit reviewed on its own, and commit 3 supply the regression proof — mirroring this session's own established convention of separating "safe additive plumbing" from "the actual logic change" from "the tests that prove it."

## 15. Risk register and mitigations

| Risk | Mitigation |
|---|---|
| Old records carry silently-different `matched_companies` semantics under the same field name | §12 — explicit, documented policy; unchanged from the discovery document |
| Unknown production Postgres row count/state (still unconfirmed — this review did not access production persistence, per its own guardrails) | Recommend a read-only row count as the first step of the actual implementation task, before assuming low practical exposure — carried forward unresolved from the discovery document, not newly introduced |
| The 13-return-site `AdmissionDecision` widening in `assess_admission()` is mechanical but touches every branch | This review has now enumerated all 13 sites exactly, by line number and category (§3) — the implementation task has a checklist, not an open-ended search |
| Renaming `_identified_subject_companies()` could be missed at its one call site, leaving a dangling reference | Confirmed this session: exactly one call site (line 633), no external importers — a single, low-risk rename with no hidden second reference |
| Fixture 4 (§8) is the one case with genuinely different pre/post behavior — if its synthetic text doesn't generalize to real editorial prose, the "fix" could be under- or over-broad in production | Flagged explicitly as synthetic; the implementation task should still validate against a handful of further real historical examples if any become available (e.g., via a future, separately-approved review of real production data, not performed here) before treating fixture 4's exact phrasing as fully representative |
| `dataclasses.asdict()`'s automatic serialization means a forgotten field-name typo in the model declaration would silently produce a differently-named JSON key | Low risk given the direct copy-paste-and-rename pattern already used for every prior additive field on this model; standard code review catches this |

## 16. Pre-implementation checklist

- [ ] Confirm production Postgres's actual `editorial_stories` row count and a sample of real `matched_companies_json` values before assuming the backward-compatibility exposure is small in practice (unresolved, carried from the discovery document).
- [ ] Re-confirm the exact current line numbers in `editorial_admission.py`/`editorial_pipeline.py`/`daily_news_models.py` at implementation time (this review cites them as of 2026-09-16, `main` @ `54360898348e10fe203010bfc20575b1afd2f661` — they may have shifted if any other change lands first).
- [ ] Decide, at implementation time, whether the 7 non-`company_subject` `AdmissionDecision` return sites explicitly pass `identified_companies=()` for readability or rely on the dataclass default — a style choice, not a correctness one (§3).
- [ ] Confirm no other file beyond the six production files and four test files listed in §11 constructs `EditorialStory` or `AdmissionDecision` in a way this review's grep missed — a fresh `grep -rn "EditorialStory(\|AdmissionDecision(" src/ tests/` immediately before implementation is cheap insurance, mirroring the same discipline used in this session's prior readiness reviews.

## 17. Files inspected

- `src/models/daily_news_models.py` (full `EditorialStory` declaration, docstring, field order)
- `src/data_access/daily_news/editorial_admission.py` (full `AdmissionDecision`, `_identified_subject_companies`, `assess_admission` — all 13 return sites individually re-verified by line number)
- `src/data_access/daily_news/editorial_pipeline.py` (the exact construction call and its surrounding control flow, re-confirmed line-for-line)
- `src/data_access/daily_news/editorial_matching.py` (`matched_companies_and_themes`, reused directly for live fixture tracing)
- `src/data_access/daily_news/materiality_classification.py` (`classify_editorial_story`, reused directly for live fixture tracing)
- `src/data_access/daily_news/company_aliases.py` (`build_company_alias_entries` — read to determine exactly which alias strings actually match Amazon/Meta/Generac, since bare "Amazon"/"Meta"/"Generac" are deliberately excluded as ambiguous)
- `src/data_access/daily_news/editorial_story_store.py` (full file — serialization/deserialization convention)
- `src/data_access/postgres_state_db/editorial_story_repository.py` and `src/data_access/postgres_state_db/schema.py` (`editorial_stories` table and row-mapping function)
- `tests/test_editorial_admission.py` (`_assess()` helper; `test_rtx_laptop_power_limit_unofficial_tool_is_rejected`; `test_incidental_customer_mention_in_an_unrelated_labor_story_fails_admission`, both reused verbatim as real fixtures)
- `tests/test_editorial_pipeline.py` (`test_qualifying_item_is_published`, reused verbatim as the real baseline fixture)
- `tests/test_editorial_story_store.py` (existing `_story()` helper and round-trip test conventions, used as the template for the proposed fixture-6 test)
- `design/DAILY_NEWS_IDENTIFIED_VS_MATCHED_COMPANIES_DISCOVERY_2026_09_16.md` (this task's own stated starting point)
- Live, in-process, read-only execution (this session, no file/cache/database write): `matched_companies_and_themes()`, `classify_editorial_story()`, `assess_admission()`, and `_identified_subject_companies()` called directly against the six §8 fixture texts, to obtain verified rather than inferred output

## Confirmations

- **Exact file changed**: one — this document, `design/DAILY_NEWS_COMPANY_ATTRIBUTION_IMPLEMENTATION_READINESS_2026_09_16.md`, newly created and untracked. No other file was created, modified, or deleted.
