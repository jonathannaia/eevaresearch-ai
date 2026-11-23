# Current Signals Policy and Gap Inventory (2026-09-15)

Read-only inventory. No file under `src/`, no test, no registry,
configuration, worker, database, cache, or production data was
modified to produce this document. Traces `origin/main` @ `b39ac24`
(post PR #59) — confirmed via `git log`/`git status` this session.
Where a currently-checked-out branch carries uncommitted, not-yet-
merged local changes to `source_registry.py` (a separate, already-
approved-but-unmerged Daily News Cohort 1 source-expansion batch),
this document deliberately reports `origin/main`'s own merged truth
(28 issuer-linked / 36 editorial / 64 total sources) rather than the
local working tree, per this task's own "from current origin/main"
scope. `design/SIGNALS_ADMISSION_MATERIALITY_CALIBRATION_2026_09_15.md`
(the prior calibration-design document) is treated as an input only;
every claim below was independently re-verified against the real,
current code this session — including several places where live code
execution (not just reading) surfaced a more precise or more severe
mechanism than that prior document stated. No discovery run, scan,
cache write, or database write occurred; several fixture headlines
below were checked against real, live third-party reporting via
read-only web search (nothing written to any repository file).

---

## 0. Headline findings

1. **There is no "Review needed" tier anywhere in the current
   implementation.** `NewsMaterialityTier` (`src/models/daily_news_
   models.py`) has exactly three members: `HIGH_SIGNAL` ("High
   Signal"), `WATCHLIST` ("Watchlist"), `BACKGROUND` ("Background").
   "Review needed" is a term from the prior calibration-design
   document's own *proposed* vocabulary, not a state that exists
   today — this document uses "Watchlist" throughout when describing
   current behavior, and flags "Review needed" explicitly as
   not-yet-built wherever it matters.
2. **"Recently Updated" (the Dashboard's own compact feed component,
   `src/ui/components/recently_updated.py`) never renders a
   materiality tier at all** — by explicit design ("No claim-type,
   confidence, strength, importance, priority, score... — this is a
   factual, source-attributed feed only"). Every fixture in this
   document showing a "· High Signal" badge is necessarily being
   observed on the full **Signals page** (`src/ui/pages/daily_
   news.py`) or its Dashboard sibling, not Recently Updated.
3. **The UI layer cannot raise an item's apparent importance.**
   `_effective_tier()` (both `daily_news.py` and, implicitly, the
   default parameter shape in `editorial_coverage.render_editorial_
   card()`) only ever reads the already-persisted `materiality_tier`
   field, with exactly one fallback: a legacy record with
   `materiality_tier=None` (persisted before this field existed)
   displays as **Watchlist**, never Background and never High
   Signal — a deliberate, documented, one-directional safety default,
   not an importance-raising mechanism. No other UI code path
   computes, adjusts, or overrides a tier.
4. **Materiality's own taxonomy has zero `space`-sector coverage.**
   `materiality_classification.TAXONOMY_KEYWORDS` has exactly 5
   buckets (semiconductors & equipment; AI compute & data center;
   memory/networking/power/cooling/packaging; wafers/materials/
   manufacturing capacity; capital policy/trade controls/supply
   chain) — none contains a launch/satellite/spacecraft/orbital term.
   This means Gate C (taxonomy-anchored, no number required) can
   **never** fire for a pure space-sector story, regardless of how
   substantive it is — the *only* path to High Signal for a space
   story is Gate B (a real quantified figure) or Gate D (editorial-
   lane fact-attribution language). This is independently confirmed
   by both Fixture C's and Fixture H's traces below.
5. **A code-verified, more severe variant of the "rumor" defect than
   previously documented**: Gate D's `_REPORTING_VERB_KEYWORDS` check
   is a bare substring/word-boundary match with **no negation
   awareness** — the literal text "has **not** officially confirmed"
   satisfies the same `"confirmed"` keyword hit as "the company
   confirmed," live-verified this session (see Fixture A, §3).
6. **A code-verified grammatical-agency gap** in `editorial_
   admission._company_has_nearby_action_language()`: it checks only
   whether *some* company-action keyword appears in the same sentence
   as a company mention, never *who* performs that action — live-
   verified this session with a realistic Fixture B reconstruction,
   where "**New Math Data** today **announced** it has achieved
   Premier Tier Partner status in the **Amazon Web Services** ...
   Partner Network" grants Amazon identity purely because "announced"
   and "Amazon Web Services" share a sentence, even though New Math
   Data — not Amazon — is the one who announced anything.

---

## 1. Complete pipeline trace

### 1.1 Source registry and source-class/lane policy

**File**: `src/data_access/daily_news/source_registry.py` (1764
lines). **Model**: `DailyNewsSourceEntry` (frozen dataclass) — fields:
`source_id`, `category` (`SourceCategory`), `format` (`SourceFormat`),
`canonical_url`, `domains`, `jurisdiction`, `enabled`, `health_state`
(`SourceHealthState`), `attribution_label`, `licensing_
classification`, `priority`, `issuer_name`/`issuer_agnostic`
(mutually exclusive), `last_verified_at`, `allowlisted`, `allowed_
event_filters`, `image_host`, `notes`, `language` (default
`"English"`).

**Two structurally separate lanes**, confirmed by direct code trace:
- **Lane A (issuer-linked)**: `RUNTIME_SOURCE_REGISTRY` — **28
  entries** (`PILOT_SOURCE_REGISTRY` 12 + `EXPANSION_BATCH_1..5` 7+1+
  4+3+1). Derived into `feed_registry.PILOT_FEEDS` via `to_daily_
  news_feed_source()`, filtered to `entry.enabled and entry.format ==
  RSS_ATOM`. Consumed by `daily_news_pipeline.run_discovery()`. Every
  entry's `category` is `OFFICIAL_IR` or `OFFICIAL_NEWSROOM`.
- **Lane B (editorial/issuer-agnostic)**: `EDITORIAL_SOURCE_REGISTRY`
  — **36 entries** (`EDITORIAL_SOURCE_REGISTRY_V1` 12 +
  `_BATCH_2` 23 + `_BATCH_3` 1), always-on, unconditionally polled.
  Consumed by `editorial_pipeline.run_editorial_discovery()`.
  Categories present: `INDEPENDENT_NEWS` (29), `REGULATOR` (4),
  `GOVERNMENT_POLICY` (2), `EXCHANGE` (1).
- **Dormant, registered but never polled**: `GATED_MARKET_NEWS_
  SOURCE_REGISTRY` (1, Light Reading) and `GATED_JP_KR_SOURCE_
  REGISTRY` (3) — gated behind two env-var allow-lists (`EDGE_DAILY_
  NEWS_ENABLED_SOURCES`, `EDGE_DAILY_NEWS_ENABLED_SOURCES_JP_KR`),
  both empty in this environment (confirmed via `.env` this session).
- **Fully inert**: `source_candidates.VALIDATED_SOURCE_CANDIDATES` (5)
  — never imported by any runtime path (confirmed by that module's
  own isolation tests).

**Admission-into-the-registry validation** (distinct from per-item
editorial admission, §1.4): `validate_source_entry()`/`find_registry_
violations()` — required non-empty `source_id`/`canonical_url`
(https)/`domains`/`jurisdiction`/`attribution_label`/`licensing_
classification`; `priority >= 1`; exactly one of `issuer_name`/
`issuer_agnostic`; a 3-name permanent exclusion list (`_EXCLUDED_
SOURCE_NAMES`: SemiAnalysis, Citrini Research, Serenity); a social-
media-domain denylist; `OFFICIAL_*`/`ISSUER_*` categories must resolve
via `feed_registry.tracked_company_for()`; `INDEPENDENT_NEWS` requires
`allowlisted=True`. **Tests**: `tests/test_daily_news_source_
registry.py` (1323 lines, exhaustive per-batch coverage).

**Known gap**: no source-class field distinguishes "primary/first-
party or regulator" from "commentary/opinion/interview format" at the
*registry* level — `SourceCategory` describes the source's
institutional type (who runs the feed), never the *typical content
shape* a given feed produces (e.g. Data Center Frontier's feed mixes
hard news and executive interviews under one `INDEPENDENT_NEWS`
category with no sub-classification).

### 1.2 Feed discovery, parsing, normalization, canonicalization, freshness

**Fetch/parse**: `rss_atom_client.fetch_entries(feed_url)` —
`feedparser`-based, 10s timeout, never raises (`FeedFetchResult(
entries, failure_code, duration_ms, http_status)`; `failure_code` is
`type(exc).__name__` or `"HTTPError:<status>"` or `"MalformedFeed"`).
One feed's failure is isolated per-source in both pipelines (`source_
failures` dict) — never blocks any other source in the same run.

**Date parsing**: `rss_atom_client._parse_published_at()` — UTC-
normalized from `published_parsed`/`updated_parsed`; a source date
with no timezone info is echoed back as literal wall-clock digits
(confirmed live for TheElec's Korea-local `<pubDate>`, a real,
previously-fixed bug); no `published_parsed`/`updated_parsed` at all
→ returns `""` (never fabricated); a date more than 5 minutes in the
future (`_FUTURE_TOLERANCE`) → falls back to `now()` (never trusted,
never displayed as impossible).

**Canonicalization** (the one hard suppression gate):
`canonical_url.validate_canonical_url(url, canonical_domains,
feed_url)` — `https` only; exact-match against the source's own
`canonical_domains` tuple (never wildcard/suffix); rejects the bare
feed URL itself; rejects an empty path (bare homepage); rejects a
`search`/`results` path segment or a `q`/`query`/`s`/`search` query
param. A separate, stricter `validate_image_url()` gates any rendered
image against a per-source `image_host` allowlist (only 3 of 28 Lane
A sources have one set: NVIDIA, SK Hynix, Cisco).

**Freshness — two independently-implemented, differently-sized
windows, confirmed by direct code read of both constants:**
| Lane | File | Constant | Window | Cap |
|---|---|---|---|---|
| A (issuer) | `daily_news_pipeline.py` | `_FRESHNESS_WINDOW_SECONDS` | **7 × 24h (168h)** | 5/source (`_PER_SOURCE_CAP`) |
| B (editorial) | `editorial_pipeline.py` | `_FRESHNESS_WINDOW_HOURS` | **72h** | 5/source, 20 total (`_TOTAL_DISPLAY_CAP`) |

Both use an inclusive `<=` boundary and independently re-implement the
same "elapsed-seconds-since-published" logic — never shared, by
deliberate design (each pipeline stays fully independent). Both also
distinguish `items_missing_published_at` (empty string) from `items_
invalid_published_at` (non-empty, unparsable) as separate counters,
though both are suppressed identically (fail-closed, never fabricated).

**Normalization/summarization**: `summary_grounding.generate_summary()`
(Lane A only) produces a grounded/fallback/original-preserved summary
from the feed's own fields; never fetches the linked page. `dedup.
normalize_title()`/`normalize_title_unicode()` strip case/punctuation
(the latter Unicode-safe, fixing a real production bug where two
distinct Hangul-content titles collapsed to the same Latin-only
fingerprint).

**Tests**: `tests/test_daily_news_rss_atom_client.py`, `tests/test_
daily_news_canonical_url.py` (30 tests), `tests/test_daily_news_
pipeline.py`, `tests/test_daily_news_dedup.py`.

**Known gaps**: (a) no distinction anywhere in the freshness/
canonicalization layer between a hard-news item and an interview/
opinion-format item — both pass identically; (b) the two independent
freshness-window implementations, while each internally correct, mean
a "same story, two lanes" pair can have differently-computed
freshness eligibility with no shared test proving the two windows'
edge behavior is intentionally different rather than accidentally
drifted.

### 1.3 Issuer/company/entity attribution

**File**: `src/data_access/daily_news/editorial_matching.py` (Lane B
only — Lane A never needs this: a Lane A story's `company_name` is
always exactly the one issuer that owns the feed it came from, set
directly at `daily_news_pipeline.py`'s `NewsStory(...,
company_name=source.company_name, ...)` construction site, **never
derived from the item's own text at all**).

**Company matching** (`match_companies(text)`): every company in
`company_aliases.daily_news_company_names()` (every active tracked
company + Daily-News-only `DISCOVERY_STUBS`), matched via two
independent alias sources per company:
1. **Mechanical aliases** (`company_aliases._aliases_for()`): the
   exact legal name, plus that name with its single longest matching
   legal-suffix pattern stripped (case-insensitive, word-boundary
   regex) — e.g. `"L3Harris Technologies, Inc."` → also matches
   `"L3Harris Technologies"`, but **not** bare `"L3Harris"** (confirmed
   this session — `"L3Harris"` alone produces zero matches).
2. **Curated brand aliases** (`_BRAND_ALIAS_OVERLAY`, exactly 3 today:
   `"Amazon Web Services"/"AWS"` → Amazon.com, Inc.; `"Google"` →
   Alphabet Inc.; `"IBM"` → International Business Machines Corp.),
   matched **case-sensitively**.

No fuzzy matching, no ticker matching, no confidence score anywhere in
this step — every match is a deterministic, exact (word-boundary)
substring hit or nothing. **This is a hard architectural fact worth
naming explicitly**: there is no numeric "confidence threshold" in
this codebase at any layer — attribution and materiality are both
100% rule-based pass/fail, never a scored/weighted system.

**Identity refinement, the layer directly downstream** (`editorial_
admission._identified_subject_companies()`): a raw company match is
**not** treated as identity on its own. For each matched company:
- If the company appears in `_RIVAL_ENTITY_EXCLUSION_PHRASES` (TOWA/
  vtuber, CEVA/CEVA-Logistics, "Oracle of Omaha"/"Oracle of Delphi"
  idioms) — excluded outright, unconditionally.
- If the company is in `_AMBIGUOUS_ALIAS_COMPANIES` (10 companies:
  Disco, Oracle, Alphabet, Coherent, Intel Corp., Amazon.com Inc.,
  MKS Inc, TOWA, CEVA, Nokia — common-word/idiom collision risk) —
  identity requires **both** the company being named in the title
  **and** a `_COMPANY_ACTION_KEYWORDS` hit in the *same sentence* as
  that company's own mention.
- Otherwise — identity requires **either** title placement **or** a
  same-sentence action-language hit.
- A `_HISTORICAL_BACKGROUND_PATTERNS` hit ("co-founder," "founder of,"
  "acquired ... in 19xx/20xx") vetoes the action-language route only
  (never the title route) for biographical/historical framing.

**Confirmed defect (§0.6)**: the same-sentence action-language check
has no concept of grammatical subject/agent — any company-action
keyword anywhere in the sentence satisfies it for *every* company
matched in that sentence, regardless of which one actually performed
the action.

**Topic/theme matching** (`match_themes(text)`): a fixed, narrow,
5-theme compound-phrase table (`THEME_KEYWORDS`) — 8 `ai-buildout`
phrases, 8 `memory`, 7 `space`, 6 `photonics`, 5 `humanoids` — never a
bare single word. Used only for **admission** eligibility (a theme
match alone can admit an item with zero company attached), never for
materiality tiering directly (materiality has its own, separate,
larger `TAXONOMY_KEYWORDS` table — see §1.5).

**Tests**: `tests/test_editorial_matching.py`, `tests/test_company_
aliases.py`, `tests/test_editorial_admission.py` (785 lines).

### 1.4 Editorial admission and exclusion rules

**File**: `src/data_access/daily_news/editorial_admission.py`
(Lane B only). Entry point: `assess_admission(title, description,
matched_companies, matched_themes, materiality_reasons) ->
AdmissionDecision(admitted: bool, reason: str)`.

**Called from** `editorial_pipeline.py`'s qualifying loop, but **only
after** two hard-coded, source-scoped bypasses:
```python
if source.source_id == _SPACEFORCE_SOURCE_ID:
    pass  # unconditional admission — no company/theme check, no assess_admission() at all
elif source.source_id == _NIST_SOURCE_ID:
    if not _matches_nist_allow_list(...): continue  # CHIPS/semiconductor allow-list only
elif not matched_companies and not matched_themes:
    continue  # fail-closed pre-check
else:
    admission = assess_admission(...)
    if not admission.admitted: continue
```
The Space Force bypass is total — confirmed by direct code read, not
inference — **every** `spaceforce-news-rss` item is admitted,
regardless of content, including a routine personnel announcement.

**Rule sequence inside `assess_admission()`** (order matters):
1. Compute `identified_companies` (§1.3) — never consults
   `materiality_reasons` while doing so ("identity precedes
   materiality").
2. Check `_matched_consumer_format()` — a curated phrase/regex list
   (`_CONSUMER_FORMAT_PHRASES`, `_CONSUMER_FORMAT_PATTERNS`,
   `_STOCK_ROUNDUP_PHRASES`; 25+ entries spanning deal/gift-guide/
   review framing, retail-purchase-guide/scalper framing added in the
   Dashboard Signals quality fix). A hit here **rejects** unless
   `identified_companies` is non-empty **and** `materiality_reasons`
   contains a hard-evidence prefix (`_HARD_MATERIAL_REASON_PREFIXES`:
   `primary_disclosure`, `formal_earnings_materials:`, `material_
   dividend_action:`, `quantified_change:`, `quantified_capital_
   return:` — deliberately **excludes** Gate C/D reasons).
3. If a company was matched but not identified as subject, and no
   theme matched either → rejected (`company_mention_not_subject_
   worthy`).
4. If a theme matched (with or without a company) → admitted
   (`theme_subject:<theme>`).
5. Otherwise → rejected (`no_qualifying_subject_evidence`).

**No admission gate of any kind exists for Lane A** — confirmed by
direct code read of `daily_news_pipeline.py`: every item that
survives the canonical-URL/freshness/dedup gates is unconditionally
constructed as a `NewsStory` and tiered; there is no reject path.
This is a deliberate architectural choice, not an oversight — a
company's own official feed item is, by construction, always "about"
that company.

**Tests**: `tests/test_editorial_admission.py` (785 lines, the most
heavily-fixtured file in this package — Nintendo/Amazon, Oracle-of-
Omaha, CEVA/CEVA-Logistics, TOWA/VTuber, Asus-RTX-5090-retail-scalper,
and the full consumer-format-phrase-safety-guard suite).

**Known gaps**: (a) no marketing-designation/partner-tier/
certification exclusion category exists at all (confirmed live —
Fixture B); (b) no personnel/leadership/governance-announcement
exclusion category exists at all (confirmed live — Fixture C's
general-purpose trace); (c) no rumor/hedge-language exclusion category
exists at all; (d) no law-firm-solicitation/attorney-advertising
exclusion category exists at all (Fixture G); (e) the `_HARD_
MATERIAL_REASON_PREFIXES` exception, while narrow and well-reasoned
for its one stated purpose, has never been tested against any of
these four new content shapes specifically, since none existed as a
named gap before this session.

### 1.5 Materiality scoring/classification

**File**: `src/data_access/daily_news/materiality_classification.py`
(545 lines — read in full this session, unchanged since the prior
calibration-design document's own read). Pure, deterministic, no LLM,
no numeric score — a fixed set of boolean "gates," any one of which
qualifies for `HIGH_SIGNAL`:

| Gate | Condition | Applies to |
|---|---|---|
| A1 | `SourceClass.REGULATORY_FILING` or `SourceCategory.OFFICIAL_FILING` | Both lanes (never actually produced by either pipeline today — forward-compatible only) |
| A2 | An `_EARNINGS_KEYWORDS` hit not disqualified by `_SCHEDULING_KEYWORDS` (unless an `_UNAMBIGUOUS_ACTUAL_RESULT_KEYWORDS` phrase overrides) | Both |
| A3 | `REGULATOR`/`REGULATOR_EXCHANGE`/`EXCHANGE` category alone, or `GOVERNMENT_POLICY`/`GOVERNMENT_PROCUREMENT` + a `_FORMAL_ACTION_KEYWORDS` hit | Lane B only |
| A4 | A `_DIVIDEND_MATERIAL_ACTION_PATTERNS` hit (initiate/suspend/resume/raise/cut/special) | Both |
| B | A `_MATERIALITY_ANCHOR_KEYWORDS` hit + a `_NUMERIC_MAGNITUDE_PATTERNS` hit, not survey-suppressed | Both |
| B2 | A `_CAPITAL_RETURN_QUANTIFIED_KEYWORDS` hit + a numeric hit | Both |
| C | A `TAXONOMY_KEYWORDS` hit + a `_TAXONOMY_PAIRING_ANCHOR_KEYWORDS` hit (deploy-family excluded), not survey-suppressed, **no number required, no proximity required — the two hits may be anywhere in the combined title+excerpt text** | Both |
| D | `is_independent_news` + a `_REPORTING_VERB_KEYWORDS` hit + ≥200 chars + (taxonomy or anchor hit) | Lane B only |

**Priority order among zero-gate outcomes**: on-taxonomy-no-anchor →
`WATCHLIST`; unqualified capital-return mention → `WATCHLIST`;
scheduling-disqualified earnings → `WATCHLIST`; otherwise →
`BACKGROUND`. **A zero-gate item can never reach `HIGH_SIGNAL`** — a
hard, separately-tested invariant.

**Calibration history already built in** (all confirmed present in
the current code, not proposed): deploy-family exclusion from Gate
C's pairing (a real number is still required via Gate B); earnings-
vs-scheduling disambiguation; survey/research-language suppression of
Gates B/C only; the three-path capital-allocation design (A4/B2/bare
mention); a narrow consumer-deal-price-framing suppression scoped to
exactly 3 ambiguous anchor keywords (`pricing`/`price increase`/
`price cut`).

**Confirmed live this session, not previously documented at this
precision**: Gate D's `_REPORTING_VERB_KEYWORDS` list (`confirmed`,
`disclosed`, `revealed`, `according to a regulatory filing`, `obtained
documents show`, `according to sources`, `filed with`, `reported
exclusively`) is matched with a plain word-boundary regex — **no
negation check of any kind**. "has **not** officially confirmed"
satisfies the same hit as "the company confirmed."

**Tests**: `tests/test_daily_news_materiality_classification.py`.

**Known gaps**: (a) Gate D negation-blindness (§0.5, code-verified);
(b) Gate C has no interview/opinion-format awareness and no proximity
requirement between its two hits (§0, Fixtures D/E, code-verified);
(c) zero `space`-sector taxonomy coverage (§0.4, code-verified,
affects Fixtures C and H identically); (d) no content-shape awareness
for law-firm-solicitation, marketing-designation, or personnel-
announcement text at all — these three rely entirely on the
*admission* layer (§1.4) or on the accident that their typical
boilerplate rarely contains a materiality anchor.

### 1.6 Legal/regulatory/news-source handling

Source *type* materially affects eligibility in exactly three places,
all confirmed by direct code read:
1. **Materiality Gate A3**: `REGULATOR`/`REGULATOR_EXCHANGE`/
   `EXCHANGE` category sources (SEC, Federal Reserve, FTC, Japan FSA,
   JPX) qualify for `HIGH_SIGNAL` by category alone — no keyword check
   at all. `GOVERNMENT_POLICY`/`GOVERNMENT_PROCUREMENT` (Space Force,
   NIST) additionally need a `_FORMAL_ACTION_KEYWORDS` hit.
2. **Materiality Gate D**: only fires for `SourceCategory.
   INDEPENDENT_NEWS` — a regulator/exchange/government item never
   needs Gate D since A3 already covers it; a Lane A (official-
   company) item never reaches Gate D at all (`is_independent_news`
   is hard-coded `False` for that lane).
3. **Admission bypass**: Space Force/NIST's own source-scoped
   exceptions (§1.4) — the only place a source's *identity* (not
   just its category) changes the admission rule entirely.

**No source-type check exists for "primary/first-party" vs.
"litigation/legal-marketing" vs. "opinion/interview" at all** — a
PR-Newswire-syndicated law-firm solicitation (Fixture G) is
classified identically, at the code level, to a PR-Newswire-
syndicated genuine corporate press release; nothing distinguishes
attorney-advertising content from wire-distributed corporate news
except whatever materiality anchors happen to appear in the text.

### 1.7 Duplicate/cross-lane/localization handling

Three independent, non-overlapping mechanisms, confirmed by direct
code read of all three:
1. **`dedup.is_duplicate_title(existing_headlines, company_name,
   title)`** — exact match on `(company_name, normalize_title_
   unicode(title))`. Used within Lane A (against both cross-run
   `existing_headlines` and same-run `queued_headlines_this_source`)
   and within Lane B (`(normalized_title, publisher)` key, per that
   pipeline's own docstring reference — confirmed structurally
   equivalent). Unicode-safe since the SK Hynix production fix (no
   longer collapses distinct Hangul titles to one Latin fingerprint).
2. **`localization_dedup.is_localized_duplicate(...)`** — **Lane A
   only, structurally incapable of reaching Lane B content**
   (requires both sides to be `SourceClass.OFFICIAL_COMPANY`, which
   Lane B never constructs). Four hard gates (same company, both
   first-party, different declared `language`, published within 48h)
   plus a translated-token-containment similarity check (≥0.6 ratio,
   ≥2 shared tokens). Two call sites: `_evaluate_localization_match()`
   (discovery-time, `daily_news_pipeline.run_discovery()`, may call a
   live `TranslationProvider`) and `_compare_using_cached_translation_
   only()` (render-time, `select_canonical_stories()`, structurally
   pure — only ever reads `translation_service.get_cached_
   translation()`, a cache-file read, never a live translation call —
   the render-time-translation-purity invariant this project's own
   Dashboard Signals PR #57 established).
3. **`select_canonical_stories(stories, cache_dir)`** — read-time
   reconciliation, called by both `src/ui/pages/daily_news.py` and
   `src/ui/components/recently_updated.py` (never inside `run_
   discovery()` itself for the general case), collapsing an already-
   persisted cross-language pair to one preferred (English/global)
   record — the superseded record stays fully intact in the store,
   never deleted, never mutated.

**Cross-lane duplication** (the same story appearing in both an
issuer's own Lane A feed and a Lane B wire/trade-press item covering
it) has **no dedicated mechanism at all** — `dedup.is_duplicate_
title()`'s `(company_name, title)` key only ever compares within
whichever list it's given; nothing merges Lane A's `NewsStory` list
and Lane B's `EditorialStory` list before comparing titles. In
practice this is mitigated only by the two lanes rendering as visually
distinct cards (different badge label: "Company news" vs. "Market
news") on the unified Signals feed, not by any actual duplicate-
suppression logic.

**Tests**: `tests/test_daily_news_dedup.py`, `tests/test_
localization_dedup.py`.

**Known gap**: no cross-lane title-duplicate suppression exists.

### 1.8 Dashboard/Signals rendering

Three distinct rendering surfaces, confirmed by direct code read of
all three:

| Surface | File | Shows tier? | Badge/label | Ordering | Default visibility |
|---|---|---|---|---|---|
| Recently Updated (Dashboard) | `recently_updated.py` | **No, never** | Uniform neutral `er-status-tag` showing only `source_label` ("SEC EDGAR"/"Korea DART"/"Japan EDINET"/"Signals") | Newest `sort_key` first, content-derived tie-break | Top 5 (`PREVIEW_COUNT`) across all 3 merged sources |
| Signals page (unified feed) | `daily_news.py` | **Yes** | `Company news` (issuer) or `Market news`/`Public sector`/`Policy` (editorial, per `_badge_label()`) + tier badge (`er-tag-pos`/`er-tag-mix`/`er-tag-neutral` for High/Watchlist/Background) | Reverse-chronological within each tier bucket (never re-sorted by tier internally) | **High Signal section always shown** (or its own empty state); **Watchlist section shown only if non-empty**; **Background hidden behind an `st.expander`, collapsed by default** |
| Editorial card component | `editorial_coverage.py` | Yes (via caller-supplied `tier` param only — never reads `story.materiality_tier` itself) | Same badge system, reused | N/A (caller decides) | N/A |

**Fallbacks**: a `materiality_tier=None` record displays as Watchlist
(`_effective_tier()`, both `daily_news.py` and implicitly wherever
`editorial_coverage.render_editorial_card()`'s `tier=None` default is
used without an explicit override — confirmed the caller in `daily_
news.py` always supplies one, so this default path is Lane B code
existing for robustness, not currently exercised via that one call
site).

**Can UI code raise an item's apparent importance? No** — confirmed
by exhaustive read of `_render_card()`, `render_editorial_card()`,
`_tier_badge_html()` in both files: each is a pure display function
reading an already-computed tier; none contains conditional logic
that could promote a tier based on rendering context, company
identity, or any other signal. The one asymmetric default (None →
Watchlist, never → Background or High Signal) is the *only*
UI-adjacent value judgment in this whole layer, and it moves a
record toward the *middle*, never toward High Signal.

**Tests**: no dedicated `test_daily_news_dashboard_rendering.py`
exists; rendering logic is covered indirectly via `tests/test_daily_
news_page.py`, `tests/apptest_pages/daily_news_page.py`, and
`tests/apptest_pages/daily_news_admin_page.py` (Streamlit `AppTest`-
based).

---

## 2. Rule inventory table

| Layer | Condition | Action | Scope | File / function | Tests | Known gap |
|---|---|---|---|---|---|---|
| Registry admission | `validate_source_entry()` (https, domains, licensing, issuer-resolution, exclusion list, allowlist-for-independent-news) | Reject malformed/unlicensed/excluded source entries | Both lanes | `source_registry.py::validate_source_entry` | `test_daily_news_source_registry.py` | No source-level "content shape" (interview/legal-marketing) classification |
| Fetch isolation | Any `requests`/parse exception | Isolate to `source_failures`, never blocks other sources | Both | `rss_atom_client.py::fetch_entries` | `test_daily_news_rss_atom_client.py` | — |
| Canonical URL | https + exact domain + non-empty path + no search segment/param | Suppress item entirely | Both | `canonical_url.py::validate_canonical_url` | `test_daily_news_canonical_url.py` | — |
| Freshness (Lane A) | `now - published <= 7d` | Suppress stale item | Lane A | `daily_news_pipeline.py::_is_fresh` | `test_daily_news_pipeline.py` | Two independently-implemented windows, no shared regression proof they're intentionally different |
| Freshness (Lane B) | `now - published <= 72h` | Suppress stale item | Lane B | `editorial_pipeline.py::_is_fresh` | `test_editorial_pipeline.py` | Same as above |
| Per-source cap | 5 newest/source/run | Suppress excess | Both | `daily_news_pipeline.py`/`editorial_pipeline.py`, `_PER_SOURCE_CAP` | Both pipeline test files | — |
| Total display cap | 20 across sources | Truncate | Lane B "All companies" view only | `editorial_pipeline.py::select_visible_editorial_stories` | `test_editorial_pipeline.py` | Not applied to per-company view (deliberate) |
| Same-run/cross-run title dedup | `(company_name, normalized_title)` exact match | Suppress duplicate | Both | `dedup.py::is_duplicate_title` | `test_daily_news_dedup.py` | No cross-lane check |
| Localized-duplicate | Same company, both first-party, different language, ≤48h, ≥0.6 token containment | Collapse to one preferred (English/global) record | Lane A only, structurally | `localization_dedup.py::is_localized_duplicate` | `test_localization_dedup.py` | Cannot reach Lane B by construction (may be correct, may be an unstated limitation) |
| Company matching | Mechanical alias (case-insensitive) or curated brand alias (case-sensitive), word-boundary | Populate `matched_companies` | Lane B (Lane A: direct assignment, no matching) | `editorial_matching.py::match_companies` | `test_editorial_matching.py` | No confidence threshold anywhere — binary only |
| Theme matching | 5-theme, 34-phrase compound table | Populate `matched_themes` | Lane B | `editorial_matching.py::match_themes` | `test_editorial_matching.py` | No `space` taxonomy overlap with materiality's own table (separate tables, only partial phrase overlap) |
| Identity refinement | Ambiguous-alias bar / title-or-action-language / rival-entity exclusion / historical-background veto | Decide `identified_companies` (used only for the admission boolean, never persisted) | Lane B | `editorial_admission.py::_identified_subject_companies` | `test_editorial_admission.py` | **Grammatical-agency blind** (§0.6); result discarded, not propagated to the persisted `matched_companies` field |
| Consumer-format exclusion | 25+ curated phrases/patterns | Reject unless hard-anchor-excused | Lane B | `editorial_admission.py::_matched_consumer_format` | `test_editorial_admission.py` | No equivalent for marketing/legal/personnel content shapes |
| Source-scoped admission bypass | `source_id == spaceforce-news-rss` | Skip ALL admission checks unconditionally | Lane B, 1 source | `editorial_pipeline.py` qualifying loop | `test_daily_news_worker.py`, `test_government_editorial_sources.py` | Admits routine personnel/governance content indiscriminately |
| Source-scoped allow-list | `source_id == nist-news-rss` | CHIPS/semiconductor topical filter only | Lane B, 1 source | `editorial_pipeline.py::_matches_nist_allow_list` | `test_government_editorial_sources.py` | — |
| Materiality Gates A/A2/A3/A4/B/B2/C/D | See §1.5 table | Assign `HIGH_SIGNAL`/`WATCHLIST`/`BACKGROUND` + `materiality_reasons` | Both (A1/A3/D scoped as noted) | `materiality_classification.py::_classify_core` | `test_daily_news_materiality_classification.py` | Gate D negation-blind; Gate C no proximity/format-awareness; zero space-taxonomy coverage |
| Tier display fallback | `materiality_tier is None` | Display as Watchlist | Both, render-time only | `daily_news.py::_effective_tier` | Indirect (`test_daily_news_page.py`) | — |
| Tier bucketing/ordering | Partition already-merged list by tier, preserve chronological order within bucket | High Signal shown / Watchlist shown if non-empty / Background collapsed | Signals page only | `daily_news.py::render` | Indirect | Recently Updated shows no tier at all — a different, narrower factual-feed contract |

---

## 3. Fixture-by-fixture trace

For each: exact current rule path (code-verified this session unless
noted), where policy is insufficient, and the narrowest layer for a
later fix.

### A — Tom's Hardware Intel "reportedly cans 12Xe" rumor
**Path**: Lane B, `INDEPENDENT_NEWS`. `match_companies()` matches
"Intel Corp." via bare "Intel" in the title. Intel is an ambiguous-
alias company, so identity needs title placement (satisfied) **and**
same-sentence action language. **Live-verified this session** with a
realistic reconstruction (tipster-attributed cancellation, no Intel
confirmation): no sentence pairs "Intel" with a `_COMPANY_ACTION_
KEYWORDS` hit → `identified_companies=[]` → `matched_themes` also
empty → **`assess_admission` returns `admitted=False`** under this
reconstruction. Materiality (computed regardless, since it's an input
to admission's own hard-anchor exception): **`HIGH_SIGNAL`** via Gate
D, `credible_editorial_reporting:confirmed` — triggered by the literal
substring "confirmed" inside "has **not** officially confirmed,"
Gate D's negation-blind bug (§0.5), independent of admission's
outcome.
**Insufficiency**: (1) Gate D cannot tell confirmed fact from a
negated non-confirmation, or from a tipster/leaker attribution
("according to tipster X," "the leaker says") that reads grammatically
similar to genuine sourcing language; (2) if the real production
excerpt differs from this reconstruction in a way that *does* satisfy
the ambiguous-alias identity bar (e.g. a different, more Intel-
attributive sentence), admission would succeed and the Gate D defect
alone fully explains the observed High Signal outcome.
**Narrowest fix layer**: materiality (Gate D — add negation detection
and a distinct rumor/hedge-language vocabulary, per the calibration
document's Rule 1), independent of and prerequisite to any admission-
layer change.

### B — Math Data AWS Partner Network, attached to Amazon
**Path**: Lane B (PR-Newswire-shaped). `match_companies()` matches
"Amazon.com, Inc." via the curated `"Amazon Web Services"` brand
alias. Amazon is ambiguous-alias, needing title placement (satisfied)
**and** same-sentence action language. **Live-verified this session**:
a realistic reconstruction ("New Math Data today **announced** it has
achieved Premier Tier Partner status in the **Amazon Web Services**
... Partner Network") shares a sentence between "Amazon Web Services"
and "announced" — the code has no grammatical-subject check, so this
satisfies `has_action_language=True` for Amazon even though New Math
Data is the actual announcer. **`assess_admission` returns
`admitted=True, reason='company_subject:Amazon.com, Inc.'`**.
Materiality: **`BACKGROUND`** (`off_taxonomy_no_anchor` — no gate
fires for generic partner-tier boilerplate).
**Insufficiency**: admission is granted to the wrong company via a
confirmed grammatical-agency gap; the story is real and correctly
demoted to Background tier, but is still incorrectly *attributed* to
Amazon at all — it will surface under Amazon's own company-filtered
view (`select_visible_editorial_stories_for_company`) even hidden in
the Background expander.
**Narrowest fix layer**: admission (a new marketing-designation/
partner-tier exclusion category, per the calibration document's Rule
2) **and** a grammatical-agency refinement to `_company_has_nearby_
action_language()` — two separate, composable fixes; the exclusion
category alone would suffice for this specific fixture even without
the deeper agency fix, since a partner-tier-shaped item would be
rejected regardless of which company matched.

### C — DAF space technology portfolio executive
**Path**: near-certainly `spaceforce-news-rss` (Lane B). The Space
Force **source-scoped bypass is total** — confirmed by direct code
read: `assess_admission()` and the company/theme fail-closed pre-check
are both **skipped entirely** for this source_id. **Live-verified this
session**: run through the general-purpose `assess_admission()`
directly (bypassing the special-case), a realistic reconstruction
produces `admitted=False, reason='no_qualifying_subject_evidence'`
(no company alias, no `space` theme phrase like "satellite
constellation"/"spacecraft" present) — confirming that *if* this
source's bypass didn't exist, the general admission rules would
already correctly reject this exact content shape. Materiality: no
`_FORMAL_ACTION_KEYWORDS` hit ("establishes" isn't in that list) and
zero taxonomy-bucket coverage for space topics → `BACKGROUND`
(`off_taxonomy_no_anchor`).
**Insufficiency**: the item is admitted (visible) *purely* because of
the Space Force bypass, not because any rule judged it relevant — the
general-purpose admission rules would already reject it.
**Narrowest fix layer**: source-level policy — add a narrow,
unconditional personnel/leadership/governance-announcement exclusion
that runs even inside the Space Force bypass path (never widen or
remove the bypass itself, which is a separately-approved, deliberate
design choice for genuine program/technology/contract Space Force
news); this exact shape is the calibration document's Rule 3.

### D — Data Center Frontier / Kirkwood IG interview
**Path**: Lane B, `INDEPENDENT_NEWS`, no source bypass. Kirkwood IG is
private, unmatched by any company alias → `matched_companies=()`.
"AI Data Center" (the exact literal phrase, present in the real
headline) matches `THEME_KEYWORDS`'s `ai-buildout` bucket →
`matched_themes=('ai-buildout',)`. Admission: `matched_companies` is
empty, so falls straight to the theme-only path → **`admitted=True,
reason='theme_subject:ai-buildout'`** — correct, by design (a
legitimate theme-only admission, not a defect). Materiality:
**live-verified this session** with a realistic interview-shaped
reconstruction ("describing the capacity investment needed to keep
pace with hyperscale demand") → **`HIGH_SIGNAL`** via Gate C,
`taxonomy_anchored_consequence:ai_compute_and_data_center:capacity` —
"AI data center" (taxonomy) and "capacity" (anchor) anywhere in the
combined text, no proximity or format check.
**Insufficiency**: admission is correct (theme-only, no company); the
defect is purely materiality — Gate C cannot distinguish a hard-news
capacity disclosure from an executive restating industry narrative in
conversation, and has no concept of interview/Q&A format at all.
**Narrowest fix layer**: materiality (Gate C — add interview-format
detection, suppressing Gate C specifically for that shape, per the
calibration document's Rule 4; never suppress Gate B, which a genuine
quantified disclosure inside an interview would still satisfy).

### E — Meta One subscription
**Path**: Lane A (`meta-ir-rss` or `meta-newsroom-rss`), no admission
gate exists for this lane at all (§1.4) — the item is unconditionally
constructed and persisted regardless of content; only its *tier* is
in question. Materiality: **live-verified this session**, twice — with
Meta's own real, boilerplate AI-infrastructure/capex framing present
anywhere in the release text → **`HIGH_SIGNAL`** via Gate C
(`taxonomy_anchored_consequence:ai_compute_and_data_center:capacity`);
with that same boilerplate removed (pure consumer-subscription framing
only) → correctly falls to **`BACKGROUND`**. Confirms the boilerplate
narrative — not the subscription launch itself — is the trigger.
**Insufficiency**: identical root cause to Fixture D — Gate C has no
proximity requirement, so an unrelated capex-narrative paragraph
elsewhere in a first-party release inflates an unrelated event's
materiality.
**Narrowest fix layer**: materiality (Gate C — the same sentence-
proximity requirement proposed for Fixture D covers this fixture too,
since both share the identical mechanism; per the calibration
document's Rule 5a).

### F — NVIDIA AI Factory power/flexibility article (positive control)
**Path**: Lane A, no admission gate. **Live-verified this session**: a
realistic reconstruction reaches `HIGH_SIGNAL` via Gate C
(`ai_compute_and_data_center:capacity`, "data center" + "capacity" —
note this specific reconstruction did not independently trigger Gate
B, since no digit-prefixed `MW`/`GW` figure was present in my
reconstruction; the real article's own independently-verified
"tokens-per-megawatt"/quantified-efficiency framing plausibly also
satisfies Gate B directly if a literal number+unit pairing like "50
megawatts" appears, which this session did not independently confirm
verbatim). Either way, the item reaches `HIGH_SIGNAL` correctly today.
**Insufficiency**: none — this is the positive control. **Directly
relevant regression risk**: any Gate-C sentence-proximity tightening
(Fixtures D/E's fix) must be verified against this fixture, since it
currently relies on the *same* gate, un-tightened; a naive proximity
requirement could inadvertently suppress this fixture too if NVIDIA's
own real article states "capacity" and "data center" in different
sentences from each other.
**Narrowest fix layer**: none required; **mandatory regression
fixture** for any Gate C change.

### G — Rosen Law Firm / PR Newswire ASTS lead-plaintiff solicitation
**Path**: Lane B, `pr-newswire-general-rss` (`INDEPENDENT_NEWS`, wire
service). **Live-verified this session** against the real, confirmed
Rosen Law Firm ASTS release pattern (via live web search — a real,
repeatedly-syndicated PR Newswire/GlobeNewswire release series,
"ROSEN ... Encourages AST SpaceMobile, Inc. Investors to Secure
Counsel Before Important Deadline in Securities Class Action"):
"AST SpaceMobile, Inc." is title-placed (not an ambiguous-alias
company) → identity established via title alone →
**`admitted=True, reason='company_subject:AST SpaceMobile, Inc.'`**.
Materiality: **`BACKGROUND`** (`off_taxonomy_no_anchor` — no gate
fires; typical solicitation boilerplate contains no materiality
anchor).
**Distinguishing the solicitation from the underlying lawsuit**: the
underlying lawsuit's own real allegations (per the actual Rosen filing,
confirmed via live search: BlueBird satellite production delays tied
to two supplier issues) would, if a *company* disclosure or *court
filing* itself independently stated a quantified figure or supply-
chain anchor, be eligible for Gates B/C/D on its own merits — this
document's trace of the *solicitation* specifically shows it does
**not** rely on or require any of that; it is demoted to Background by
the accident that attorney-advertising boilerplate rarely contains a
materiality anchor, not by any deliberate rule recognizing it as
legal-marketing content.
**Insufficiency**: the *current* observed outcome for this fixture
(admitted, Background) is arguably already acceptable per this
project's own stated policy ("Background means relevant research
context with low immediate materiality"), but it is correct **by
accident**, not by design — a differently-worded solicitation that
happens to quote the underlying complaint's own dollar figures (e.g.
"...investors who suffered losses exceeding $50 million...") could
satisfy Gate B/C and incorrectly reach Watchlist or High Signal purely
by reciting allegations, never by disclosing anything new.
**Narrowest fix layer**: admission — a narrow law-firm-solicitation/
attorney-advertising content-shape exclusion (never a bare "lawsuit"
keyword ban, which would also suppress genuine reporting about a real
legal action, a company's own primary-source disclosure of pending
litigation, or an SEC enforcement action — all of which must remain
fully eligible, see §4 conflict matrix).

### H — KSAT / Intuitive Machines / NASA JPL EAGLE-VSWIR (positive control for a commercial space award)
**Path**: **live-verified this session that Intuitive Machines, Inc.
has no Lane A (issuer) Daily News source at all today** (confirmed
against the real, current 28-entry `RUNTIME_SOURCE_REGISTRY`) — the
real underlying announcement (confirmed via live web search: Intuitive
Machines' own investor-relations press release, 2026-08-18, "Intuitive
Machines Selected to Provide Spacecraft Platform and Mission Solutions
for NASA's EAGLE-VSWIR Earth Science Mission," distributed via
GlobeNewswire and `investors.intuitivemachines.com`) is **not
reachable by Daily News at all today**, structurally, regardless of
admission/materiality rules — neither Intuitive Machines' own IR feed
nor GlobeNewswire is a registered source in either lane. The item
could only ever reach Daily News today if independently covered by one
of the 36 existing Lane B sources (e.g. a general item on PR Newswire
or a tech-press outlet); this session found no evidence any of the 36
current sources carried it. A "KSAT selected by Intuitive Machines"
angle specifically would, if covered by an eligible source, match
"Intuitive Machines" (non-ambiguous, `in_title` or same-sentence
"selected" action language both plausible) → `company_subject`
admission, correctly, with KSAT itself never falsely attached to any
tracked company (KSAT is untracked, correctly producing no company
card for it). Materiality: same space-taxonomy-gap constraint as
Fixture C — Gate C cannot fire for a pure mission-selection story;
`HIGH_SIGNAL` eligibility depends entirely on whether the covering
article states a real dollar figure (Gate B) — third-party reporting
independently found the underlying NASA program valued near $600
million, which, if stated in the article with a literal
`$...million`-shaped figure, would satisfy Gate B via the `contract`
anchor.
**Distinguishing a verified award from launch/revenue/mission
completion**: this fixture is explicitly a *selection* announcement
(a contract/award event), not a launch (EAGLE-VSWIR is confirmed
targeted for 2028, years out), not revenue recognition, and not
mission completion — none of those later milestones are conflated
with this one in the real, confirmed press release; a future
detection rule should key on "selected"/"awarded"/"contract" language
specifically, never treat a mission-selection announcement as
equivalent to a launch/completion event.
**Insufficiency**: (1) the primary source-coverage gap (no Intuitive
Machines Lane A feed, no GlobeNewswire Lane B source) means this
concrete, real, positive-control-worthy event is currently invisible
to Daily News regardless of any admission/materiality rule; (2) even
if discovered, the space-taxonomy gap (shared with Fixture C) makes
`HIGH_SIGNAL` eligibility fragile — dependent entirely on a stated
dollar figure rather than any topical space-sector recognition.
**Narrowest fix layer**: two independent layers — (1) source coverage
(a future, separately-scoped Daily News source-expansion decision —
explicitly out of scope for any admission/materiality calibration
work); (2) materiality (adding a `space` bucket to `TAXONOMY_KEYWORDS`
— a narrow, additive taxonomy extension, not a new gate).

---

## 4. Fixture matrix

| Fixture | Current observed outcome | Exact reason | Intended outcome | Recommended future rule/layer |
|---|---|---|---|---|
| A | Admitted (uncertain under reconstruction) / High Signal | Gate D negation-blind `"confirmed"` match on negated text | Reject or cap at Watchlist; High Signal only with primary confirmation | Materiality Gate D — negation + rumor-hedge vocabulary (Rule 1) |
| B | Admitted, attached to Amazon / Background | Grammatical-agency-blind same-sentence action-language check | Reject (marketing-designation content); Amazon never attached | Admission — new marketing-designation category (Rule 2) + agency refinement |
| C | Admitted (bypass) / Background | Unconditional Space Force source bypass | Reject (routine personnel/governance, no disclosed transaction) | Admission — personnel-announcement category running inside the bypass (Rule 3) |
| D | Admitted (correctly, theme-only) / High Signal | Gate C no-proximity, no-format-awareness | Background/Watchlist unless a new, measurable, attributable disclosure | Materiality Gate C — interview-format suppression (Rule 4) |
| E | Admitted (Lane A, no gate) / High Signal | Gate C no-proximity — boilerplate AI-capex framing elsewhere in text | Background/Watchlist by default; High Signal only with concrete, event-specific evidence | Materiality Gate C — sentence-proximity requirement (Rule 5a) |
| F | Admitted (Lane A, no gate) / High Signal | Gate C (and plausibly Gate B) — genuine quantified infrastructure claim | **Unchanged** — positive control | None; mandatory regression fixture |
| G | Admitted, attached to AST SpaceMobile / Background | No gate fires on typical solicitation boilerplate (works by accident, not design) | Background by design, never Watchlist/High Signal even if the solicitation quotes the underlying complaint's own figures | Admission — law-firm-solicitation category (new, narrow) |
| H | **Not currently discoverable** (no source covers it) | Source-coverage gap, not an admission/materiality rule | Admitted, correctly attributed to Intuitive Machines, High-Signal-eligible via a real contract-value figure | Source coverage (separate, out of scope) + materiality taxonomy — add a `space` bucket |

---

## 5. Conflict matrix — potential regressions from tightening

| Tightening under consideration | What it must NOT suppress | Why the design already (or must) preserve it |
|---|---|---|
| Rumor/hedge-language suppression (Gate D/C, Rule 1) | A genuine, company-confirmed disclosure that happens to also reference earlier press speculation ("first reported in June; the company confirmed it today") | `_UNAMBIGUOUS_ACTUAL_RESULT_KEYWORDS`'s existing override pattern (already live for the earnings-vs-scheduling case) is the proven precedent — an unambiguous confirmation phrase must override hedge-language suppression, never the reverse |
| Marketing-designation exclusion (Rule 2) | A genuine, quantified AWS/company contract or deployment announcement that happens to also use award/partner-adjacent language (e.g. "AWS-certified deployment now serving 2 GW of capacity") | The exception must key off `_HARD_MATERIAL_REASON_PREFIXES` (Gates A/A2/A4/B/B2 only — never C/D), exactly mirroring the existing consumer-format exception's own anchor-evidence design |
| Personnel-announcement exclusion (Rule 3) | A leadership change directly tied to a disclosed acquisition, restructuring, or funded program (the Fixture-C-positive-control shape: "Names New Program Executive Officer for $1.2B Resilient GPS Program") | Same `_HARD_MATERIAL_REASON_PREFIXES` anchor-evidence exception; must run identically whether or not the source has its own broader bypass (Space Force) |
| Interview-format Gate C suppression (Rule 4) | A genuine, measurable, attributable disclosure made *during* an interview (a named executive disclosing a real dollar figure or capacity commitment on the record) | Rule 4 suppresses **Gate C only** — Gate B (quantified change) and Gate D (independent fact-attribution) remain fully available to interview-format content; F's own reliance on Gate C specifically is why F must be the anchor regression fixture for this change |
| Gate C sentence-proximity requirement (Rule 5a) | NVIDIA's/any first-party issuer's genuine infrastructure/power/cooling disclosures where the taxonomy phrase and anchor legitimately co-occur in the same sentence or a tightly adjacent one | Fixture F is the explicit, named regression check; the requirement must be validated against F's real (not reconstructed) text before shipping, since this document's own reconstruction did not conclusively rule out a same-sentence pairing already existing in the real article |
| Law-firm-solicitation exclusion (new, Fixture G) | (a) Independent journalism genuinely reporting on a real lawsuit (Gate D-eligible on its own separate merits); (b) a company's own primary-source disclosure of litigation (10-K risk factor, 8-K legal-proceedings item — Gate A/A2-eligible); (c) an SEC enforcement action (Gate A3, `REGULATOR` category, unaffected by any Lane-B-scoped rule) | The exclusion must key on attorney-advertising *content shape* (law-firm self-promotion, "secure counsel," "lead plaintiff deadline," firm-ranking boilerplate) — never a bare "lawsuit"/"class action" keyword, which would indiscriminately catch (a) and (b) too |
| Space taxonomy addition (Fixture H, C) | Nothing — this is purely additive (a new `TAXONOMY_KEYWORDS` bucket), and cannot itself cause a new false positive on its own; the real regression risk is downstream — a `space` bucket could newly let Gate C fire for a *non-substantive* space-adjacent story (e.g. a routine launch-schedule notice) the way the existing 5 buckets already can for their own themes today | Any new bucket should reuse the exact same narrow, multi-word-compound-phrase discipline (`"satellite constellation"`, `"orbital launch"`, etc.) already established for the other 5 buckets — never a bare `"space"`/`"satellite"` single word |

---

## 6. Ranked implementation list

### Must-fix before beta
1. **Gate D negation detection** (materiality) — the single highest-
   confidence, highest-severity, code-verified defect in this entire
   inventory (Fixture A). A plain negation check (a small set of
   negation markers within N words before a reporting-verb keyword)
   is narrow, deterministic, and directly testable against Fixture A
   without touching any other gate.
2. **Grammatical-agency check for `_company_has_nearby_action_
   language()`** (admission) — the second highest-confidence defect
   (Fixture B), code-verified, and the mechanism most likely to also
   silently affect other already-shipped fixtures this session did
   not specifically test.
3. **Marketing-designation/partner-tier exclusion category**
   (admission, Rule 2) — closes Fixture B's remaining gap even
   without #2, and is a wholly new, narrow, well-precedented addition
   (same shape as the existing consumer-format list).
4. **Personnel-announcement exclusion category, wired to run inside
   the Space Force bypass** (admission, Rule 3) — closes Fixture C;
   the "runs inside the bypass" requirement is the one piece of this
   fix that is not a drop-in reuse of an existing pattern and needs
   its own explicit design/test attention.

### Strongly recommended
5. **Gate C sentence-proximity requirement** (materiality, Rule 5a) —
   closes Fixtures D and E via one shared mechanism; requires
   validating Fixture F's real text before shipping (see conflict
   matrix) — the one item on this list with a real, non-trivial
   regression-verification cost attached.
6. **Interview-format detection + Gate C suppression** (materiality,
   Rule 4) — narrows Fixture D further beyond what #5 alone achieves,
   and is independently valuable for any future interview-format
   source.
7. **Law-firm-solicitation exclusion category** (admission, new) —
   Fixture G's current Background outcome is already acceptable in
   practice, but the underlying mechanism is accidental; this closes
   the accidental gap before it produces a real false-positive High
   Signal from a differently-worded solicitation.
8. **`space` taxonomy bucket** (materiality, additive) — closes the
   structural gap shared by Fixtures C and H; narrow, purely additive,
   low regression risk given the existing 5-bucket discipline's own
   track record.

### Optional refinement
9. **Cross-lane title-duplicate suppression** — a real, named gap
   (§1.7) with no fixture in this document specifically exercising it;
   worth scoping once a concrete production duplicate is observed,
   not speculatively.
10. **Source-coverage expansion for Intuitive Machines' own IR feed
    and/or a GlobeNewswire Lane B registration** — the structural
    fix for Fixture H's discoverability gap; explicitly a *source*
    decision, not an admission/materiality one, and out of scope for
    any calibration work following this inventory.
11. **Gate D reporting-verb list refinement beyond negation** (e.g.
    explicitly distinguishing "according to sources" from "confirmed"
    by evidentiary strength, per the original calibration document's
    fuller Rule 1 proposal) — valuable but lower urgency than the
    negation fix alone, since negation is the more severe, more
    surprising failure mode.
12. **A dedicated Dashboard-rendering test file** — no `test_daily_
    news_dashboard_rendering.py` exists today; current coverage is
    indirect via page-level `AppTest` fixtures. Not a policy gap, but
    a testing-infrastructure gap worth noting for whoever implements
    items 1-8, since none of this inventory's UI claims (§1.8) has a
    dedicated, easily-extended test home today.

---

## 7. Confirmation

No file under `src/`, no test, no source registry, configuration,
worker, database, cache, or production data was modified to produce
this document. No discovery run, worker run, scan, cache write, or
database write occurred. The read-only web searches used to ground
Fixtures A, D, G, and H against real, live third-party content wrote
nothing to any repository file. All 10 pre-existing untracked
artifacts (the 9 named in prior sessions plus this document's own
predecessor set) remain untouched; this document itself is untracked,
not staged, not committed. The currently-checked-out branch's own
uncommitted, not-yet-merged Daily News Cohort 1 source-registry
changes were left exactly as they were — not read from, not built
upon, not altered — this inventory instead sourced `source_registry.
py`'s content directly from `origin/main` via `git show` specifically
to guarantee the reported counts (28/36/64) reflect real, merged
truth rather than local, unapproved work.

## Files read / created

**Read this session (all read-only)**: `src/data_access/daily_news/
source_registry.py` (via `git show origin/main:...` for the
canonical, merged version), `feed_registry.py`, `daily_news_
pipeline.py` (full file, 696 lines), `editorial_pipeline.py`,
`editorial_admission.py` (full file, prior session), `editorial_
matching.py` (full file, prior session), `materiality_classification.
py` (full file, prior session), `canonical_url.py`, `rss_atom_
client.py` (partial, function signatures/docstrings), `dedup.py`,
`localization_dedup.py` (signatures), `company_aliases.py` (prior
session), `src/models/daily_news_models.py` (`NewsMaterialityTier`,
`SourceClass` definitions), `src/ui/components/recently_updated.py`
(full file), `src/ui/pages/daily_news.py` (full file), `src/ui/
components/editorial_coverage.py` (full file), `.env` (grepped, no
secret values). Live external verification this session (read-only
web search, nothing written to any repository file): the real Tom's
Hardware Intel/Nova Lake-S article context, the real Rosen Law Firm/
AST SpaceMobile PR Newswire release series, and the real Intuitive
Machines/NASA JPL EAGLE-VSWIR press release. Several fixtures (A, B,
C, D, E, F, G) were additionally **executed against the real, live,
unmodified code this session** (not merely read) via read-only Python
calls to `matched_companies_and_themes()`, `classify_editorial_
story()`/`classify_issuer_story()`, and `assess_admission()` — no
persistent state was written by any of these calls.

**Not touched**: all 9 prior untracked artifacts plus `design/SIGNALS_
ADMISSION_MATERIALITY_CALIBRATION_2026_09_15.md` (read as an input
only, per this task's own instruction, never modified).

**Created**: `design/CURRENT_SIGNALS_POLICY_AND_GAP_INVENTORY_2026_
09_15.md` (this document — untracked, not staged, not committed).
