# Signals Precision Follow-Up — Retail/Deal, Boilerplate, Financial-Guidance — 2026-09-16

**Status:** Read-only design. No code, test, registry, configuration, UI, database, cache, persisted story, or deployment file was modified. No worker run, no live discovery, no write.

**Base:** `origin/main` at `2190f59211e27bb2e58baa103dcb94785a6f1307`. Builds on `design/POST_MERGE_DAILY_NEWS_SIGNAL_PERFORMANCE_AUDIT_2026_09_16.md`'s confirmed findings — every root cause below was re-derived directly against current `main` (not merely re-cited from that audit), using the same real, live fixture text captured there.

---

## 0. What text actually reaches classification today (traced once, applies to every fixture below)

**Editorial lane** (`editorial_pipeline.py:309-318`):
```python
matched_companies, matched_themes = matched_companies_and_themes(entry.title, entry.summary)
materiality_tier, materiality_reasons = classify_editorial_story(entry.title, entry.summary, source.category)
```
Both calls receive **`entry.title`** and **raw `entry.summary`** — the unprocessed RSS `<description>`/`<content:encoded>` field, HTML tags and all. The one function that *does* clean this text, `_extractive_excerpt()` (`editorial_pipeline.py:173-200` — strips HTML via `_strip_html()`, trims to a 400-char sentence boundary), is called **only afterward**, at `EditorialStory` construction (`editorial_pipeline.py:409`), purely to build the **display** `excerpt` field. Classification never sees its output.

**Issuer lane** (`daily_news_pipeline.py:609,652-653`): the same pattern — `generate_summary()` (which internally calls its own `_strip_html()`, `summary_grounding.py:72-90`) produces the cleaned `summary_result.eeva_summary` used for **display**, but `classify_issuer_story(entry.title, entry.summary, ...)` at line 653 is called with the **raw** `entry.summary`, not `summary_result.eeva_summary`. Structurally identical defect, not confirmed live in the audit (no issuer-lane fixture triggered it), but the same root cause.

`matched_companies_and_themes()` and both `_combined_text()` implementations (`editorial_admission.py:415-418`, `materiality_classification.py:602-608`) do no cleaning of their own — they only concatenate `title` and `description` with `\n`.

**Conclusion up front:** there is exactly one content-boundary gap, in exactly two call sites (`editorial_pipeline.py:309-318`, `daily_news_pipeline.py:652-653`), and it is already half-fixed by code that exists and is tested for a different purpose (the display excerpt). This shapes the whole design below.

---

## 1. Fixture-by-fixture root cause

### Fixture 1 & 2 — two retail-deal bundles (High Signal)
> "Grab a $560 saving on this 1440p-ready gaming PC with a 9800X3D and RTX 5060 Ti 16GB, now $1,859..." (`toms-hardware-rss`)
> "Save $1,289 when you build an extreme PC with these top-tier components — combo deal..." (`toms-hardware-rss`)

**Exact code path (re-verified live against current `main`):**
- `_is_consumer_deal_price_framing(text)` (`materiality_classification.py:256-257`) **correctly returns `True`** for both — the existing `_CONSUMER_DEAL_PRICE_PATTERNS` (`materiality_classification.py:248-252`, includes `\bsave\b.{0,20}\$\d`) already detects this exact "Save $X" shape.
- But `_strip_consumer_pricing_anchors_if_deal_framed()` (`materiality_classification.py:260-270`) only strips `"pricing"`/`"price increase"`/`"price cut"` from `anchor_hits` when deal-framing fires — by explicit design (`_CONSUMER_PRICING_ANCHOR_KEYWORDS = frozenset({"pricing", "price increase", "price cut"})`), confirmed by its own docstring: "every other anchor keyword always survives unchanged."
- Both fixtures independently matched **`"capacity"`** (a *different*, non-pricing anchor) via genuine product-spec text — "storage **capacity**" (fixture 1) and "capacity and price" describing an SSD (fixture 2) — confirmed by direct re-fetch: `raw anchor_hits: ('capacity',)` / `('capacity', 'pricing')`, `after strip: ('capacity',)` in both cases, with `numeric_hit: True` (the article contains numbers — $, GB, TB — regardless of where). Gate B (`quantified_change:capacity`) fires purely because "capacity" is untouched by the deal-price strip.
- **This is not the same bug as boilerplate pollution** — both matches are in genuine article prose, correctly HTML-clean; the defect is that deal-framing detection is real but its suppression is scoped to only 3 of the many anchor keywords, and "capacity" is exactly the kind of word that appears in nearly every PC-hardware deal write-up (RAM/SSD specs).

**Current vs. desired:** Currently `HIGH_SIGNAL` (reason `quantified_change:capacity`). Desired: rejected at admission (never published), same disposition as the existing "which GPU should you buy" consumer-format exclusion.

### Fixture 3 — scalper/retail-scarcity story (High Signal)
> "Nvidia's RTX 5090 vanishes from online retail in the US — third-party sellers now demand as much as $9,500..." (`toms-hardware-rss`)

**Exact code path:** `_is_consumer_deal_price_framing(text)` returns **`False`** — there is no discount/"save $X"/"% off" pattern here at all; this is a price-*spike*/scarcity story, a structurally different shape the deal-price detector was never meant to catch. `raw anchor_hits: ('orders',)`, matched from a genuine but off-topic sentence — "most recent reviews are a string of one-star reviews about **orders** never being fulfilled" (a customer-complaint sense of "orders," not a corporate order-backlog signal) — combined with `numeric_hit: True` ($9,500 elsewhere in the piece) → `quantified_change:orders`.
Separately, admission's own `_matched_consumer_format()` (`editorial_admission.py`, phrase list includes `"scalper"`, `"scalpers"`, `"reseller listing"`, `"retail scarcity"`) does **not** fire either — this specific article's real wording ("third-party sellers," "vanishes from online retail," "demand as much as") never literally matches any of those four curated phrases.

**Current vs. desired:** Currently `HIGH_SIGNAL` (reason `quantified_change:orders`). Desired: rejected at admission — same disposition class as the existing scalper/reseller exclusion, just extended to cover the actual live phrasing.

### Fixture 4 — the denial story (Watchlist)
> "SK hynix denies US production rumors linked to Intel" (`korea-herald-business-rss`)

**Exact code path:** admitted as `company_subject:Intel Corp.` (Intel named in title, non-ambiguous-alias-style identification unaffected by content). Materiality: no hard gate (A/A2/A4/B/B2) fires — no quantified figure — so `_classify_core` falls to its Watchlist fallback chain (`materiality_classification.py:679-688`): `if reasons: HIGH_SIGNAL` → false; `if taxonomy_hits: WATCHLIST (on_taxonomy_no_anchor)` (`materiality_classification.py:681-682`) → **fires**, tagged `on_taxonomy_no_anchor:memory_networking_power_cooling_packaging`. `_has_rumor_or_negated_confirmation_language()` (`materiality_classification.py:461-488`) correctly detects this text as uncertain/denial-shaped, but that flag is wired **only** into Gate C's and Gate D's own qualifying conditions (`uncertain_reporting` guards at `materiality_classification.py:666` and `:673-676`) — never into the `on_taxonomy_no_anchor` Watchlist fallback three lines later, which has no awareness of the flag at all.

**Current vs. desired:** Currently `WATCHLIST` (visible by default). Desired: `BACKGROUND` when the item has no hard-gate evidence — but genuine hard-gate-qualifying denial coverage (a denial tied to a real regulatory/legal/financial disclosure, which independently satisfies Gates A/A2/A4/B) must remain completely unaffected, since those gates are already untouched by `uncertain_reporting`.

### Fixture 5 — the blog digest (Watchlist)
> "Blog Review: Sept. 16" (`semiconductor-engineering-rss`, attributed to Synopsys, Inc.)

**Exact code path:** `matched_companies_and_themes()` performs pure alias-substring matching with zero awareness of document *format* — a recurring link-roundup post is textually indistinguishable from a real news article once a tracked company's alias appears anywhere in its (raw, un-stripped) text, almost certainly inside one of the roundup's own linked-post *titles*, not this digest's own reported content. Admitted via a theme/company match with no hard evidence → same `on_taxonomy_no_anchor` Watchlist fallback as Fixture 4.

**Current vs. desired:** Currently `WATCHLIST`. Desired: rejected at admission (a recurring aggregator/roundup format is not a reported story about its incidentally-linked companies at all), unless it independently carries real anchor evidence.

### Fixture 6 — hobbyist/curiosity Watchlist items
> "Dumpster diver builds home lab Proxmox server from weekly landfill runs for SSDs, HDDs..." (`toms-hardware-rss`)
> "'Defeated' GPT-6 Astra model spent several hours just farming potatoes after being bl..." (`toms-hardware-rss`)

**Exact code path:** same `on_taxonomy_no_anchor` Watchlist fallback as Fixtures 4/5 — a bare taxonomy-bucket phrase (e.g. a storage-hardware term, or a bare "AI" taxonomy hit) appearing anywhere in the raw text, with zero anchor and zero content-shape relevance, is sufficient on its own.

**Current vs. desired:** Currently `WATCHLIST`. Desired: `BACKGROUND` by default absent any anchor evidence — same fix as Fixture 4/5, no separate mechanism needed. Note: these two titles do **not** share a common curated phrase the way the retail-deal fixtures do (no shared "hobbyist"/"curiosity" vocabulary), so **this is not solvable by a curated phrase list without drifting toward the broad-ban guardrail** — the correct fix is the same structural `on_taxonomy_no_anchor` gate as Fixture 4/5, not a new content-shape exclusion.

### Fixture 3b — the "terminal guidance" collision, isolated (part of Fixture 3's own High-Signal article, "Russian freelancers use Claude to program autonomous combat drone swarm...")

**Exact code path, fully traced with the real article text:**
- After HTML-stripping (`_strip_html`), the article's genuine body legitimately contains the phrase **"terminal guidance"** — real prose: "Claude helped develop swarm coordination, computer vision, **terminal guidance**, and other software that enabled drones to select targets." This is a weapons-targeting term, correctly present in real, non-boilerplate article text.
- `_MATERIALITY_ANCHOR_KEYWORDS` (`materiality_classification.py:222-227`) includes the bare word `"guidance"` with no sense-disambiguation at all — `_contains_any(text, _MATERIALITY_ANCHOR_KEYWORDS)` matches on "terminal **guidance**" alone.
- `numeric_hit = _has_numeric_magnitude(text)` (Gate B, `materiality_classification.py:637`) is a **whole-document** boolean with **no proximity requirement to the anchor at all** — it is satisfied by any number anywhere in the ~4,100-character stripped article (dates, counts of organizations, etc.), unrelated to "guidance." `anchor_hits and numeric_hit` → `quantified_change:guidance`.
- The same "semiconductor"/"semiconductors" taxonomy phrase that paired locally with "guidance" for Gate C (`taxonomy_anchored_consequence:semiconductors_and_equipment:guidance`) was independently confirmed to originate from an unrelated "related articles" hyperlink title at the article's end (see §1's own "DRAM" finding below) — meaning Gate C's sentence-locality check, applied to un-stripped HTML, can pair a genuine body-text anchor with a boilerplate-sourced taxonomy phrase that happen to land in adjacent "sentences" purely because the boilerplate block runs together with real prose once minimal splitting is applied.

**This confirms two independent, separable defects converging on one fixture:**
1. **Gate B has no locality requirement between anchor and number at all** (broader than previously documented — not just Gate D) — a structural gap, not specific to "guidance."
2. **"Guidance" itself is a genuine homonym** — its financial-forecast sense and its instructional/technical sense (targeting, navigation, travel, setup, etc.) are indistinguishable to a bare keyword match.

### The "DRAM"/boilerplate theme match, isolated (same article as Fixture 3b)

**Exact code path, fully traced:** the stripped article's tail (still present after `_strip_html`, since HTML-stripping only removes tag markup, not the human-readable text between tags) contains a "Go deeper with TH Premium" related-reading block — a genuine Tom's Hardware template feature, syntactically indistinguishable from real prose once tags are removed: *"...Chip scarcity assaults auto industry amid the worsening Nexperia and **DRAM** crisis..."* — this is the *title of a different, unrelated article*, embedded as a "related reading" link inside this article's own RSS `<description>`/`content:encoded` field. `THEME_KEYWORDS["memory"]` (`editorial_matching.py`) includes the literal phrase `"DRAM"`, so `matched_companies_and_themes()` reports `matched_themes=("memory",)` for a story that never actually discusses memory chips. **HTML-tag stripping alone does not fix this** — the offending text survives as plain, readable words; the actual problem is that a template "related articles" block is a different *kind* of content than the article's own reported prose, and nothing in the pipeline distinguishes them.

---

## 2. Design

### 2.1 Content-boundary fix (boilerplate/link exclusion) — addresses the DRAM/related-articles finding, partially mitigates Fixture 6

**Two layers, both narrow and both reusing what already exists:**

1. **Route classification through the same cleaning the display excerpt already uses**, instead of raw `entry.summary`. Concretely: in `editorial_pipeline.py`, compute `cleaned_summary = _strip_html(entry.summary) if entry.summary else None` once, before the matching/materiality calls, and pass `cleaned_summary` (not `entry.summary`) into `matched_companies_and_themes()` and `classify_editorial_story()`. (`_strip_html`, not the sentence-truncated `_extractive_excerpt`, since truncation is a display concern and would silently change which facts are eligible for classification — the goal here is tag removal, not length reduction.) Mirror the same change in `daily_news_pipeline.py`, passing `_strip_html(entry.summary)` (reuse `summary_grounding._strip_html`, already imported via `generate_summary`'s own module) into `classify_issuer_story()`.
2. **Add a narrow, curated "related-content block" trim**, applied after HTML-stripping and before classification: split the cleaned text at the first occurrence of a small, curated set of known related-reading/navigation markers (e.g. `"Go deeper with"`, `"Related:"`, `"Read more:"`, `"Continue reading:"`) and keep only the text *before* the first match. This is not a keyword ban — it never rejects or excludes an item; it only trims trailing boilerplate that these specific publishers append after their own real reporting, mirroring the same "narrow, curated marker list" discipline already used for the interview/podcast format guard. Scope this list conservatively (a handful of literal, tested markers) and expect to extend it as new publishers' conventions are confirmed live, exactly like the consumer-format/personnel-announcement phrase lists already are.

**Why not rely on truncation alone:** the existing 400-char `_extractive_excerpt` happened to cut before both the "Go deeper" block and the "terminal guidance" sentence in this fixture, but that is incidental to article length, not a structural guarantee — a shorter genuine lead paragraph or an earlier-appearing related-links block would defeat it. The marker-based trim is deterministic regardless of length.

**Positive control that must survive:** any article whose own real, reportable facts happen to appear *after* the 400-character mark, or after a REAL body paragraph that itself contains a false-positive marker string (low risk given the markers are chosen to be genuine navigational conventions, not ordinary words) — needs a regression fixture proving a **long, genuine article** (headline-relevant fact stated in paragraph 3, well past the lead) still classifies correctly once cleaned. `test_nvidia_quantified_ai_factory_power_flexibility_remains_high_signal` and its pipeline-level counterpart must be re-run to confirm the content-boundary change doesn't accidentally trim genuine NVIDIA capacity language.

### 2.2 Retail/deal/scarcity rule — addresses Fixtures 1, 2, 3

**Promote deal/scarcity detection from a narrow materiality-side anchor-strip into a fourth admission-layer content-shape exclusion**, structurally identical to the three that already exist in `editorial_admission.py` (`_matched_consumer_format`, `_matched_law_firm_solicitation`, `_matched_personnel_announcement`) — narrow phrase/pattern match, checked in `assess_admission()`, with the **same** anchor-evidence-required rescue (`if identified_companies and _has_anchor_evidence(materiality_reasons): admit as company_subject`, else reject). Concretely:

- Add `_CONSUMER_DEAL_PRICE_PATTERNS`' existing, already-correct patterns (`\bsave\b.{0,20}\$\d`, `\$\d[\d,.]*\s*(off|discount)\b`, `\d+(\.\d+)?%\s*off\b`) to `editorial_admission.py`'s own `_matched_consumer_format()` check (or a clearly-named sibling function reusing the same shape) — these already exist and are tested in `materiality_classification.py`; this is additive reuse across the two files' existing conventions, not new detection logic.
- Add one new, narrow pattern for the scarcity/markup shape confirmed in Fixture 3: `\bthird-party sellers?\b.{0,80}\b(demand|charging|asking|selling for)\b` — generalizes the existing curated "scalper"/"reseller listing"/"retail scarcity" phrases to the real, live phrasing that slipped past them, without naming any product or company.
- **Do not** touch `_strip_consumer_pricing_anchors_if_deal_framed()`'s own narrow 3-keyword scope in `materiality_classification.py` — it is correct and already has its own tested positive control (`test_quantified_capacity_infrastructure_event_is_unaffected_by_the_consumer_deal_fix`, which uses a non-deal-framed fixture and is unaffected by an admission-layer change). Rejecting the item at admission makes the materiality-layer question moot for these specific fixtures; leaving the existing materiality mechanism untouched avoids any risk of regressing that already-tested behavior.

**Why this is the narrowest fix:** it reuses two already-written, already-tested pattern sets (the deal-price patterns from `materiality_classification.py`, the exclusion-with-rescue architecture from `editorial_admission.py`) rather than inventing new detection or a new mechanism, and the rescue clause already guarantees a genuine issuer-level pricing/inventory/channel action (identified company + real anchor evidence, e.g. `quantified_change:pricing` from a company's own announced price cut) remains admitted exactly as today.

**Positive controls that must survive:** `test_named_material_aws_contract_remains_eligible_with_correct_attribution`, `test_regression_named_material_aws_contract_is_eligible_with_correct_attribution` (contract, no deal framing at all — unaffected); `test_quantified_capacity_infrastructure_event_is_unaffected_by_the_consumer_deal_fix` (no deal framing — unaffected); a **new** fixture required by this task's own guardrail — a genuine issuer price action phrased with deal-adjacent language, e.g. "AWS cuts S3 storage pricing, saving customers an estimated $40 million annually" with AWS grammatically identified as actor and a real anchor (`quantified_change:pricing`) — must remain admitted via the rescue clause.

### 2.3 Financial-guidance disambiguation — addresses the "terminal guidance" collision

**Contextual, narrow, negative-modifier design** (not a positive phrase-list, and not a ban): "guidance" remains a valid anchor by default everywhere it already is today, **except** when it is immediately preceded (within a short, bounded lookback window — reuse the exact `_NEGATION_LOOKBACK_CHARS`-style proximity mechanism already established for the P0 negation-aware confirmation fix, e.g. 20–25 characters) by one of a small, curated set of disqualifying modifiers that name a non-financial sense of the word: `"terminal"`, `"navigation"`, `"travel"`, `"safety"`, `"operational"`, `"installation"`, `"setup"`, `"user"`, `"parental"`, `"style"`. A "guidance" occurrence with a disqualifying modifier in its immediate lookback is excluded from `anchor_hits` for that occurrence only — every other "guidance" occurrence in the same text (e.g. a genuine "the company narrowed its full-year guidance" elsewhere in the same article) is unaffected. This directly satisfies "do not broadly ban the word guidance" — the word remains a valid, unqualified anchor in the overwhelming majority of real financial contexts; only a small, closed, curated set of local disqualifying phrases is excluded, exactly mirroring the existing `_NEGATION_MARKER_PATTERN` convention already in the same file.

**Positive control that must survive:** every existing earnings-shaped fixture that doesn't use the literal word "guidance" is untouched by definition. A **new** regression fixture is required (none currently exists in the suite): "Company X narrows full-year guidance, citing stronger-than-expected demand" (or similar) — a genuine financial-guidance sentence with **no** disqualifying modifier nearby — must still qualify as an anchor after this change.

### 2.4 Denial, digest, and curiosity content — addresses Fixtures 4, 5, 6

**Single structural fix, shared across all three:** gate the `on_taxonomy_no_anchor` Watchlist fallback (`materiality_classification.py:681-682`) behind the same `uncertain_reporting` flag already computed earlier in `_classify_core` (`materiality_classification.py:662`) and already used to guard Gates C/D — i.e. change `if taxonomy_hits: return WATCHLIST, (...)` to `if taxonomy_hits and not uncertain_reporting: return WATCHLIST, (...)`; when the taxonomy-only path is suppressed this way, the item falls through to `BACKGROUND` (`off_taxonomy_no_anchor`), the existing final fallback — never a full rejection, since Lane B admission has already independently decided the item is on-topic (company/theme matched); this only affects the *display tier*, not whether the item is admitted at all.

This single change:
- Fixes **Fixture 4** directly (`uncertain_reporting` is already `True` for "SK hynix denies... rumors").
- Fixes **Fixture 5** only if the digest is *also* flagged uncertain — it generally will not be (a "Blog Review" isn't rumor-shaped), so Fixture 5 needs its **own**, separate, narrow admission-layer fix: a curated recurring-feature/digest title-pattern check (new phrase/pattern list in `editorial_admission.py`, same three-part shape as the other exclusions — e.g. `^(Blog|Weekly|Daily) (Review|Roundup|Digest|Brief|Wrap)\b`, `:\s*(Jan(uary)?|Feb(ruary)?|...)\s+\d{1,2}$` date-suffixed title patterns), with the same anchor-evidence rescue.
- **Partially** mitigates **Fixture 6** — both curiosity items reach Watchlist via the identical `on_taxonomy_no_anchor` path, but neither is rumor/denial-shaped, so the `uncertain_reporting` guard alone does not touch them. §2.1's content-boundary cleaning may reduce (not eliminate) incidental taxonomy hits by shortening/cleaning the matched text, but this is not a guaranteed fix. **No further curated-phrase mechanism is recommended for Fixture 6** specifically — "dumpster diver" and "farming potatoes" share no common, narrow, non-company-specific vocabulary a curated list could safely target without drifting toward a broad ban. This item type is recommended for **monitoring**, not a required pre-beta fix (see §4).

**Positive control that must survive:** `test_denied_report_does_not_reach_high_signal` (unaffected — Gates C/D were already suppressed by `uncertain_reporting`, this change only extends the same flag one gate further); a **new** fixture required — a denial tied to a genuine hard-gate anchor (e.g. "Company X's regulatory filing formally denies allegations, resolving the matter with a $50 million settlement disclosed in its 8-K") must still reach `HIGH_SIGNAL` via Gate A/A2/A4/B, which `uncertain_reporting` never touches.

### 2.5 Source health — `microchip-newsroom-rss` (kept separate, per instruction)

**Exact root cause, confirmed by fetching the raw feed directly:** every `<pubDate>` in this feed is formatted `M/D/YYYY H:MM:SS AM/PM` (e.g. `9/3/2026 7:00:00 AM`) — neither RFC822 nor RFC3339, and with no timezone/offset marker at all. `feedparser`'s built-in date parser does not recognize this format, so `entry.get("published_parsed")`/`entry.get("updated_parsed")` are both `None`, and `_parse_published_at()` (`rss_atom_client.py:67-98`) correctly, deliberately falls back to `""` per its own documented "never guess/fabricate a date" discipline — this is not a bug in that function's own logic, it is a real, unrecognized source format.

**This is entirely separate from every fix in §2.1–2.4** — different file (`rss_atom_client.py`), different function (`_parse_published_at`, not `editorial_pipeline.py`'s matching/materiality call sites), no shared code path with admission or materiality classification at all. Per the instruction, it is **not** bundled into the same follow-up.

**Recommended narrow fix (separate, optional, low-priority):** extend `_parse_published_at`'s fallback path to try a short, explicit list of common non-standard date-string formats via `datetime.strptime` against the entry's raw `published`/`updated` string (which `feedparser` preserves as plain text even when it fails to produce a struct), before falling back to `""`. A single additional format string (`"%m/%d/%Y %I:%M:%S %p"`) would resolve this specific feed. Keep the existing "never fabricate, empty string if truly unparseable" behavior as the final fallback.
**Alternative, even smaller option:** do nothing to the parser and instead add this feed to a source-status watch list (a monitoring/documentation change only, no code change) — since the blast radius is a single company's coverage going silent, not a false signal.

---

## 3. Minimal regression-test matrix

| Area | New negative fixture (must reject / demote) | Existing positive control (must survive unchanged) | New positive fixture (must remain eligible) |
|---|---|---|---|
| Retail deal bundle | Fixture 1 & 2 text (exact live titles) | `test_named_material_aws_contract_remains_eligible_with_correct_attribution`, `test_quantified_capacity_infrastructure_event_is_unaffected_by_the_consumer_deal_fix` | A genuine issuer price-cut with deal-adjacent phrasing (new: "AWS cuts S3 pricing, saving customers $40M annually") |
| Scalper/scarcity | Fixture 3 text | (same as above — no scalper-shaped positive control currently exists) | — |
| DRAM/boilerplate theme match | Fixture 3's own "Go deeper with TH Premium" block, as an isolated synthetic fixture | `test_nvidia_quantified_ai_factory_power_flexibility_remains_high_signal`, its pipeline-level counterpart | A long, genuine article with its material fact in paragraph 3+ (new synthetic fixture) |
| "Terminal guidance" collision | The exact live "terminal guidance" sentence | — (no existing "guidance" fixture) | New: "Company X narrows full-year guidance, citing stronger-than-expected demand" |
| Denial (Watchlist) | Fixture 4 text | `test_denied_report_does_not_reach_high_signal` | New: a denial tied to a real settlement/filing (hard-gate anchor) |
| Digest/roundup | Fixture 5 text | — (no existing digest fixture) | A genuine named-column article whose title happens to contain a date (must not collide with the new title-pattern check) |
| Curiosity (monitor only) | Fixture 6 — no fix required this pass; document as a known, accepted residual gap | — | — |

---

## 4. Recommended PR split

**PR A — Signals admission/materiality precision follow-up** (`editorial_pipeline.py`, `daily_news_pipeline.py`, `editorial_admission.py`, `materiality_classification.py`, plus `tests/test_editorial_admission.py`, `tests/test_daily_news_materiality_classification.py`, `tests/test_editorial_pipeline.py`): §2.1 (content-boundary), §2.2 (retail/deal/scarcity), §2.3 (guidance disambiguation), §2.4 (denial/digest Watchlist gate + digest-format exclusion). All four areas touch the same admission/materiality call chain and are natural to validate together against the same live fixtures.

**PR B — `microchip-newsroom-rss` date-extraction fix** (`src/data_access/daily_news/rss_atom_client.py`, `tests/test_daily_news_rss_atom_client.py`): §2.5 only. Fully independent file and function; no shared code path with PR A. Recommend landing separately (or deferring — see priority below) so a source-specific parser fix never blocks or gets blocked by the precision follow-up.

---

## 5. Priority recommendation

**Must fix before controlled beta validation:**
- §2.2 (retail/deal/scarcity) — confirmed, reproducible, **High Signal**-tier pollution (the most visible, most trusted tier) from a single high-volume source, four separate confirmed occurrences.
- §2.1 (content-boundary/boilerplate) — directly enables the most severe single finding (a national-security story reaching High Signal); also reduces exposure for any future publisher using similar related-content templates.
- §2.3 (guidance disambiguation) — same fixture as above, second independent cause; narrow and low-risk to land alongside §2.1.
- §2.4's denial gate (Fixture 4) — a confirmed, live, reproducible Watchlist-tier defect with a one-line, well-scoped fix reusing an existing flag.

**Can be monitored rather than required before beta:**
- §2.4's digest-format exclusion (Fixture 5) — confirmed but lower-severity (Watchlist, not High Signal) and affects a narrower content type; reasonable to include in PR A if time allows, but not a beta blocker on its own.
- Fixture 6 (curiosity content) — explicitly no safe, narrow fix identified this pass; document as accepted residual risk and monitor via the same live-probe method used in the performance audit.
- §2.5 (`microchip-newsroom-rss`) — zero false-signal risk (the failure mode is silence, not incorrect output), affects one source's coverage only; fix opportunistically, not gating.

---

*No file other than this new document was created, modified, staged, committed, or pushed while producing this design. No worker was run; no item was ingested, cached, or persisted.*
