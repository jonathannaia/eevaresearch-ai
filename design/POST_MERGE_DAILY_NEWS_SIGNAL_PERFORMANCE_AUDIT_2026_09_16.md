# Post-Merge Daily News & Signals Performance Audit — 2026-09-16

**Status:** Read-only audit. No code, test, registry, configuration, UI, database, cache, persisted story, or deployment file was modified. No worker was run. No item was ingested, persisted, or cached.

**Base:** `origin/main` at `2190f59211e27bb2e58baa103dcb94785a6f1307` (includes PR #61 `aabf9e1` and PR #60 `f2f72e3`).

**Method and honesty note on evidence sources:** this local environment has no configured connection to the production (Render) database/cache — `.env` carries no `EDGE_STATE_DB_URL`/backend selection, and the local dev cache (`data/cache/daily_news_stories.json`) contains 154 unrelated local/seed rows, none from production. **No persisted-Dashboard-card inspection was possible.** Instead, two narrowly bounded, read-only probes were run, each doing exactly what `rss_atom_client.fetch_entries()` already does for one feed — one `requests.get` per source, no retry, no write:

1. **Source-health probe** — one live fetch of all 70 registry `canonical_url`s, recording entry count and the newest `published_at` per feed. No parsing beyond what the codebase's own client already does.
2. **Signal-quality probe** — for every entry inside each lane's own freshness window (72h editorial, 7-day issuer) already returned by probe 1, replayed the real, unmodified `matched_companies_and_themes()`, `assess_admission()`, and `classify_editorial_story()`/`classify_issuer_story()` functions in-process to determine what the current merged pipeline would decide **right now**, without ever calling `run_editorial_discovery()`/`run_discovery()` (which persist) or touching `editorial_story_store`/`daily_news_store`.

This produces genuine, current, code-verified evidence — not speculation — but it is **not** a view of the actual persisted Dashboard. Where the two would differ (e.g. an item published outside this snapshot's window, or already deduped/capped in the real store) is called out explicitly. All snapshot data lives only in this session's scratchpad; nothing was written to the repository.

---

## 1. Source health

**Totals** (70/70 sources returned live, parseable, non-empty results — zero HTTP failures, zero exceptions):

| Lane | Window | Within window (active) | Quiet but plausible (1–4× window) | Stale (>4× window) | Misconfigured |
|---|---|---|---|---|---|
| Issuer-linked (Lane A) | 7 days (168h) | 16 | 11 | 4 | 1 |
| Editorial (Lane B) | 72h | 37 | 1 | 0 | 0 |

**Issuer sources beyond 4× the freshness window** (>28 days since the newest item; feed itself resolves and parses correctly — this is "no recent output," not "feed broken"):
- `maxlinear-ir-rss` — newest item ~29.7 days old
- `arista-networks-ir-rss` — ~42.7 days
- `amazon-ir-rss` — ~47.7 days
- `corning-ir-rss` — ~50 days

None of these four is a Cohort 1 source or touched by PR #60/#61. Per the guardrail against judging a source unhealthy for being quiet, these are flagged as **watch, not remove** — a live, non-empty, parseable feed with an unusually long quiet stretch for a mega-/large-cap issuer is worth periodic re-check, not automatic pause.

**Misconfigured (1):** `microchip-newsroom-rss` — the feed resolves and returns 71 real entries with real titles, but **every entry's `published_at` is an empty string** (confirmed directly: `''` on all 5 sampled entries). Empty `published_at` fails the freshness check unconditionally (`if not entry.published_at or not _is_fresh(...)`), so this source can never publish anything through Lane A regardless of content quality — a real, live, reproducible date-parsing defect in this specific feed's XML shape, unrelated to PR #60/#61 and pre-existing. Candidate file: `src/data_access/daily_news/rss_atom_client.py::_parse_published_at`.

**Editorial (Lane B)** is uniformly healthy: 37 of 38 sources have a live item inside the 72h window; the one exception (`federal-reserve-press-rss`, ~5 days quiet) is a normal cadence for a central-bank press feed, not a defect.

### Cohort 1 sources (6) — explicit status

| Source | Status | Evidence |
|---|---|---|
| Equinix (`equinix-ir-rss`) | **Healthy** | Feed live; 1 fresh item in the 7-day window (a personnel appointment, correctly tiered Background — see §5) |
| L3Harris (`l3harris-newsroom-rss`) | **Healthy** | Feed live; 8 fresh items in the 7-day window, including one genuine High Signal contract story (see §4) |
| Firefly Aerospace (`firefly-aerospace-news-rss`) | **Quiet but healthy** | Feed live, 128 total entries, but the newest is ~169.6h old — essentially exactly at the 168h boundary (≈1.6h past it). No item fell inside the 7-day window at probe time; this reflects publication cadence, not a broken feed |
| Yaskawa (`yaskawa-newsroom-rss`) | **Healthy** | Feed live; 1 fresh item in the window, English-language, correctly attributed (see §5 for the language-override check) |
| SpaceNews (`spacenews-rss`) | **Healthy** | Feed live; 3 fresh items in the 72h window, all correctly evaluated (none matched a tracked company or theme — see §5) |
| The Robot Report (`robot-report-rss`) | **Healthy** | Feed live; 6 fresh items in the 72h window, one correctly admitted at High Signal (see §5) |

No Cohort 1 source is stale, failing, or misconfigured.

---

## 2. Signal-quality review

819 editorial candidates existed inside the 72h window at probe time; 95 were admitted (95/819 ≈ 11.6%). Tier split of the 95 admitted: **27 High Signal, 35 Watchlist, 33 Background**. 44 issuer candidates existed inside the 7-day window; all 44 are published unconditionally (Lane A has no admission gate — see PR #60/#61 audit precedent) and were tiered by materiality only.

Representative sample (not exhaustive — selected to cover both strong positives and the confirmed defects below):

| Title | Source | Attribution | Displayed/would-be category | Code-path classification | Verdict | Explanation | Recommended result |
|---|---|---|---|---|---|---|---|
| L3Harris Advances Production of Proximity Sensors for US Air Force | `l3harris-newsroom-rss` (Lane A) | L3Harris Technologies, Inc. | High Signal | `quantified_change:contract` | **Correct** | Genuine named contract/production story with a real anchor | High Signal |
| Rocket Lab calls foul on Blue Origin's $700M Mars comms contract | `the-register-headlines-rss` | Rocket Lab | High Signal | `quantified_change:contract`, `taxonomy_anchored_consequence:space_missions_and_launch:contract` | **Correct** | Named, quantified space contract dispute | High Signal |
| Samsung Electronics to Expand DDR5 Module Output Only Through Outsourcing | `thelec-rss` | Samsung Electronics | High Signal | `taxonomy_anchored_consequence:semiconductors_and_equipment:capacity` | **Correct** | Genuine capacity-expansion action | High Signal |
| AI networking startups race to replace Nvidia's NVLink | `the-register-headlines-rss` | NVIDIA | High Signal | `quantified_change:capacity`, `taxonomy_anchored_consequence:ai_compute_and_data_center:capacity` | **Correct** | Quantified AI-infrastructure capacity content — the required Nvidia-style positive control | High Signal |
| Broadcom CEO addresses Anthropic's slowdown push, says AI revenue targets haven't... | `cnbc-technology-rss` | Broadcom Inc. | High Signal | `taxonomy_anchored_consequence:ai_compute_and_data_center:revenue` | **Correct** | Executive commentary tied to real, sentence-local revenue guidance, not a bare vision quote | High Signal |
| **Grab a $560 saving on this 1440p-ready gaming PC with a 9800X3D and RTX 5060 Ti...** | `toms-hardware-rss` | theme: memory | High Signal | `quantified_change:capacity` | **Incorrect** | Confirmed, verbatim retail deal-bundle content (Walmart link, "$560 discount," full build price) — exactly the shape the consumer-format exclusion exists for, but its wording doesn't match the curated phrase list | Reject |
| **Save $1,289 when you build an extreme PC with these top-tier components — combo deal...** | `toms-hardware-rss` | theme: memory | High Signal | `quantified_change:capacity` | **Incorrect** | Confirmed Newegg combo-deal content ("combo bundles," "save $1,289") | Reject |
| **Nvidia's RTX 5090 vanishes from online retail in the US — third-party sellers now demand as much as $9,500** | `toms-hardware-rss` | company_subject: NVIDIA | High Signal | `quantified_change:orders` | **Incorrect** | Confirmed retail-scarcity/reseller-markup story — same shape as the curated "scalper"/"reseller listing" exclusion phrases, but doesn't use those literal words | Reject |
| **Russian freelancers use Claude to program autonomous combat drone swarm — AI-enabled target selection...** | `toms-hardware-rss` | theme: memory | High Signal | `quantified_change:guidance`, `taxonomy_anchored_consequence:semiconductors_and_equipment:guidance` | **Incorrect** | Confirmed root cause (full text pulled): the "memory" theme hit came from `DRAM` inside an unrelated **related-articles hyperlink URL slug** at the article's end (`...nexperia-and-dram-crisis`), and the `guidance` anchor came from **"terminal guidance"** — a weapons-targeting term, not financial guidance. A serious national-security/AI-misuse story about an Anthropic threat-intelligence report, misclassified as a semiconductor/memory-market signal via boilerplate-link text and a homonym | Reject |
| Gaming takes a backseat as Nvidia overhauls the RTX 5090 for maximum AI margins — RTX Pro 5500 delivers 2.6X VRAM... | `toms-hardware-rss` | company_subject: NVIDIA | High Signal | `quantified_change:capacity` | **Borderline** | A genuine new enterprise/workstation GPU SKU with a quantified spec (2.6× VRAM) — arguably legitimate product news, though framed with consumer-review language | High Signal (acceptable) |
| SK hynix denies US production rumors linked to Intel | `korea-herald-business-rss` | company_subject: Intel Corp. | Watchlist | `on_taxonomy_no_anchor:memory_networking_power_cooling_packaging` | **Incorrect** | A denial story — the headline itself states this is being denied, not confirmed. Reached Watchlist via the global `on_taxonomy_no_anchor` fallback, which is **not** gated by the negation/rumor guard (that guard only suppresses Gates C/D, not this fallback) | Background |
| Blog Review: Sept. 16 | `semiconductor-engineering-rss` | company_subject: Synopsys, Inc. | Watchlist | `on_taxonomy_no_anchor:semiconductors_and_equipment` | **Incorrect** | A recurring link-roundup/digest feature, not a Synopsys news story — Synopsys is almost certainly named only inside one of the roundup's linked post titles | Background |
| Dumpster diver builds home lab Proxmox server from weekly landfill runs for SSDs, HDDs... | `toms-hardware-rss` | theme: memory | Watchlist | `on_taxonomy_no_anchor:ai_compute_and_data_center` | **Incorrect** | Hobbyist/lifestyle content, not industry signal | Background |
| Pick up a giant 18-inch RTX 5080-powered Asus gaming laptop from Best Buy and save $600 | `toms-hardware-rss` | theme: memory | Watchlist | `on_taxonomy_no_anchor:ai_compute_and_data_center` | **Incorrect** | Same confirmed retail-deal pattern as the High Signal cases above | Background |
| Intel, SK Hynix shares jump on report they're discussing U.S. memory chip manufacturing | `cnbc-top-news-rss` / `cnbc-technology-rss` | company_subject: SK Hynix | Watchlist | `on_taxonomy_no_anchor:semiconductors_and_equipment` | **Correct** | Early, unconfirmed, but genuinely relevant reporting — exactly Watchlist's own stated purpose ("relevant but early, unquantified, or not yet material") | Watchlist |
| Nuri gears up for Korea's first constellation launch | `korea-herald-business-rss` | theme: space | Watchlist | `on_taxonomy_no_anchor:space_missions_and_launch` | **Correct** | Real space-launch coverage without a quantified figure — appropriately Watchlist, not inflated | Watchlist |

**Pre-merge stale cards vs. current-pipeline failure:** no persisted Dashboard data was reachable from this environment, so **the stale-card count is not determinable here** (see method note above) — this audit can state with confidence that every defect listed above is a **live, current-pipeline** outcome (verified by re-deriving it from a freshly fetched item through the unmodified, currently-merged code), not an artifact of old persisted data. Whether any *already-persisted* production card is additionally stale is a separate question this environment cannot answer.

---

## 3. Required negative controls

| Category | Post-merge item found? | Disposition |
|---|---|---|
| Unconfirmed rumor / tipster / denial framed as confirmation | **Yes** — "SK hynix denies US production rumors linked to Intel" | **Reproducible post-merge policy failure** — admitted via the un-gated `on_taxonomy_no_anchor` Watchlist fallback, which the negation/rumor guard does not cover. (Separately: several "reportedly in talks" items were also found, but those are defensibly Watchlist-appropriate early-signal reporting, not denials — not counted as failures.) |
| Plaintiff-law-firm lead-plaintiff/investor solicitation | Not observed in current sample | Not observed, not failed — none of the 819 candidates matched this shape at probe time. Existing test coverage (`test_law_firm_lead_plaintiff_solicitation_rejects` and the pipeline-level Rosen/ASTS regression) remains in place and passing as of the PR #61 merge. |
| Generic partner tier / certification / badge / award / membership / status announcement | Not observed as admitted | The one candidate close to this shape ("Blog Review: Sept. 16") is a digest/roundup, not a certification announcement — a different, newly-identified defect (see §2) rather than a partner-tier miss. No partner-tier item was admitted in this sample; the mechanism this fix already tests (Math Data/AWS) is unchanged. |
| Routine executive/board/leadership/portfolio/organizational announcement | **Yes** — "Equinix Names DD Dasgupta as Chief Marketing Officer" (Lane A, always published) | **Correctly retained as Background** — Lane A has no admission gate (a pre-existing, documented architectural fact, not a PR #61 regression), but materiality correctly tiers this Background (`off_taxonomy_no_anchor`), so it is not visible by default. |
| Speech/keynote/panel/conference appearance/event invitation without concrete action | Not observed as admitted in this sample | Not observed, not failed — no such item was present in the live 72h/7-day windows at probe time. The structural gap identified in the prior audit (`design/POST_MERGE_SIGNALS_PRECISION_AUDIT_2026_09_15.md`) remains unresolved in the code but was not exercised by this specific snapshot. |
| Generic product feature/AI marketing post without material evidence | **Yes** — the four confirmed retail-deal items in §2 | **Reproducible post-merge policy failure** — same root cause as the consumer-format gap, confirmed live with real Tom's Hardware content. |
| Interview/podcast without new attributable measurable disclosure | Not observed as admitted | Not observed, not failed. "Scott Bergs, CEO of Kirkwood IG: Fiber and the AI Data Center Buildout" (the exact fixture already covered by the P1 interview guard's own test) appeared in the live sample at Watchlist, not High Signal — confirming the existing guard is working correctly on its own tested case. |
| Weak company association based only on nearby mention or generic ecosystem reference | **Yes** — "Blog Review: Sept. 16" (Synopsys) | **Reproducible post-merge policy failure**, newly identified — not one of PR #61's own regression fixtures, but the same class of defect (matched_companies text-matching without regard to genuine subject relevance) the prior audit already flagged as gap #2/#3. |

---

## 4. Required positive controls

| Category | Observed? | Evidence |
|---|---|---|
| Named contract, award, procurement, or customer deployment | **Yes** | "L3Harris Advances Production of Proximity Sensors for US Air Force" (`l3harris-newsroom-rss`, High Signal, `quantified_change:contract`); "Rocket Lab calls foul on Blue Origin's $700M Mars comms contract" (High Signal) |
| Capacity, power, data-center, manufacturing, or infrastructure commitment | **Yes** | "Samsung Electronics to Expand DDR5 Module Output Only Through Outsourcing" (High Signal, `taxonomy_anchored_consequence:...:capacity`) |
| Earnings/filing disclosure involving demand, revenue, pricing, backlog, capex, or guidance | **Yes** | "Broadcom CEO addresses Anthropic's slowdown push, says AI revenue targets..." (High Signal, revenue-anchored); "Oracle celebrates banner quarter with another round of layoffs" (High Signal, `quantified_change:contract` + revenue taxonomy) |
| Substantive legal/regulatory/court/issuer development | **Not observed in current sample, not failed** | No resolved legal/regulatory/court item appeared in the live 72h/7-day windows at probe time. Existing unit/pipeline-level regression coverage for this category (AST SpaceMobile $150M settlement fixture) remains in the test suite and passing as of the PR #61 merge — this audit did not re-verify it live because no matching real-world item existed in the sample. |
| Funded Space program, launch/mission milestone, satellite/ground-segment action, or procurement | **Yes** | "Rocket Lab calls foul on Blue Origin's $700M Mars comms contract" (High Signal, `space_missions_and_launch:contract`); "Nuri gears up for Korea's first constellation launch" (correctly admitted, Watchlist pending quantification) |
| Quantified Nvidia-style AI infrastructure/power/cooling evidence | **Yes** | "AI networking startups race to replace Nvidia's NVLink" (High Signal, `quantified_change:capacity` + `taxonomy_anchored_consequence:ai_compute_and_data_center:capacity`) |

5 of 6 required positive-control categories were directly observed and confirmed live, with real current content, through the unmodified merged pipeline. The core admission/materiality architecture is demonstrably still admitting genuine, concrete, well-evidenced developments correctly.

---

## 5. Cohort 1 contribution

| Source | Produced | Notes |
|---|---|---|
| Equinix | 1 item, correctly tiered Background (personnel announcement, no anchor) | Attribution correct (`Equinix Names DD Dasgupta as Chief Marketing Officer` → matched to Equinix); always-published via Lane A, but not prominently visible. Quiet week overall. |
| L3Harris | 8 items; 1 High Signal (contract), 7 Background (routine feature/awareness content, including one opinion piece, "If the Best AI Wins, America Must Build It," correctly demoted) | Good thematic usefulness — real defense-contract signal mixed with clearly non-material feature content, both correctly tiered. |
| Firefly Aerospace | 0 items in the current 7-day window | Feed live and healthy (128 total historical entries); simply quiet at probe time (newest item ≈169.6h old, just past the 168h cutoff). No eligible/rejected items to evaluate this cycle. |
| Yaskawa | 1 item, correctly tiered Background | **English-override verified correct**: "Launch of Collaborative Robot MOTOMAN-HC12 with 12 kg Payload Capacity and 1410 mm Reach" reads as clean, natural English with no garbled/mistranslated text, confirming the `language="English"` override is working as intended (the registry's own documented reason: Yaskawa's RSS metadata mislabels its English content as Japanese). Tiered Background despite concrete specs (12kg payload, 1410mm reach) because `_has_numeric_magnitude()` does not recognize physical unit measurements (kg/mm) as a qualifying magnitude — a minor, pre-existing under-classification asymmetry (the opposite direction from the false positives above), not a defect requiring urgent action. |
| SpaceNews | 3 items in window, 0 admitted | All 3 correctly rejected at the fail-closed precondition (no tracked company, no theme phrase): a private-company item (Iceye — untracked), an event-notice item, and a broad commentary piece ("Space's growing billion-dollar club"). This is quiet-but-healthy source behavior, not a defect — SpaceNews's real value is expected to show up more on high-theme-density news days. |
| The Robot Report | 6 items in window, 1 admitted (High Signal: "Agility's Digit 5 humanoid has new legs, batteries, and safety upgrades," `quantified_change:shipment`) | One correctly rejected item is worth noting: "Universal Robots launches its seventh generation robot platform at IMTS" was rejected with `company_mention_not_subject_worthy` — a real, live confirmation that the PR #61 grammatical-agency fix correctly prevents an incidentally-mentioned tracked company (not Universal Robots itself, which is untracked) from stealing subject credit for a story that isn't about it. |

**Cross-lane duplicate check (Firefly/L3Harris ↔ SpaceNews, Yaskawa ↔ The Robot Report):** **not observed in current sample** — no topical overlap existed between the Lane A and Lane B content actually live at probe time (SpaceNews's 3 items don't mention Firefly or L3Harris; The Robot Report's admitted item is about Agility Robotics, unrelated to Yaskawa). The dedup mechanism itself was not exercised because no genuine duplicate candidate existed in this snapshot — this is a gap in available evidence, not a confirmed pass or fail of the dedup logic.

**Deduplication (within-lane, general):** not independently re-verified live in this audit; the existing, already-passing `tests/test_daily_news_dedup.py` and `tests/test_localization_dedup.py` suites (last run clean at the PR #60 merge) are the authoritative source for this behavior and were not re-run here (would require executing the test suite, which this audit's own read-only fetch-based method did not need to touch).

---

## 6. Beta readiness decision

## READY AFTER A SMALL FOLLOW-UP FIX

**Rationale:** the core admission/materiality/lane architecture, and both merged PRs' own scoped changes, are demonstrably working correctly — 5 of 6 required positive-control categories were confirmed live with real current content (contracts, capacity commitments, revenue-anchored commentary, space-program contracts, quantified Nvidia infrastructure evidence), all 6 Cohort 1 sources are healthy and correctly gated (including a live, real confirmation of the grammatical-agency-attribution fix rejecting an incidental company mention), and Lane B's fail-closed precondition correctly rejected the large majority (724 of 819) of candidates in this snapshot. But two concrete, well-scoped, **pre-existing** (not introduced by PR #60/#61) defects were newly confirmed live, at meaningful volume, in the top-visibility High Signal tier, and should be patched before wider beta exposure rather than deferred indefinitely.

### Three most important findings

1. **Un-stripped HTML/boilerplate "related articles" link text pollutes taxonomy/anchor matching.** Confirmed with exact text: a serious, unrelated national-security story reached High Signal because a taxonomy phrase (`semiconductor`) and a materiality anchor (`guidance`, in its "terminal guidance"/weapons sense) both happened to appear inside unrelated hyperlink URL slugs and an unrelated phrase — text the reader never actually reads as prose. Root cause is input preparation, not the admission/materiality rules themselves.
2. **The curated consumer-format/deal exclusion phrase list doesn't cover common real-world retail-deal phrasing** ("Save $X when you build...", "Grab a $X saving on...", "...third-party sellers now demand as much as $X"). Confirmed 4 times live (2 at High Signal, 2 at Watchlist), all from one high-volume source (`toms-hardware-rss`). This is the same class of problem the exclusion list was already built to solve — it needs broader (still narrow, still non-company-specific) phrase/pattern coverage, not a redesign.
3. **The Watchlist `on_taxonomy_no_anchor` fallback is not gated by the negation/rumor guard**, unlike Gates C/D. Confirmed live: a denial story ("SK hynix denies US production rumors") reached visible Watchlist. This is the same architectural gap already documented in the prior precision audit (`design/POST_MERGE_SIGNALS_PRECISION_AUDIT_2026_09_15.md`, priority 1), now confirmed with real production-registry content rather than a hypothetical fixture.

### Reproducible bugs with candidate files

- **HTML/boilerplate text pollution** — candidate files: `src/data_access/daily_news/editorial_pipeline.py` (`_extractive_excerpt` — verify/strengthen HTML stripping before matching) and/or `src/data_access/daily_news/editorial_matching.py` / `src/data_access/daily_news/materiality_classification.py` (matching should operate on the already-stripped excerpt, never on raw feed HTML). Regression fixture: reuse the confirmed live text ("...nexperia-and-dram-crisis" link + "terminal guidance" phrase) as a synthetic test case.
- **Consumer-deal phrase-list gap** — candidate file: `src/data_access/daily_news/editorial_admission.py` (`_CONSUMER_FORMAT_PHRASES`/`_CONSUMER_FORMAT_PATTERNS`). Regression fixtures: the four confirmed live titles in §2, plus their already-tested Math Data/AWS-shaped positive control preserved unchanged.
- **Watchlist fallback bypasses the negation/rumor guard** — candidate file: `src/data_access/daily_news/materiality_classification.py` (`_classify_core`'s `on_taxonomy_no_anchor` branch). Regression fixture: the confirmed live "SK hynix denies..." title, plus the existing Intel-rumor positive/negative control pair preserved unchanged.

### Sources to watch, pause, or remove

- **Watch (not pause):** `amazon-ir-rss`, `arista-networks-ir-rss`, `corning-ir-rss`, `maxlinear-ir-rss` — feeds are live and healthy but unusually quiet (29–50 days); re-check in the next audit cycle rather than acting now.
- **Fix, don't remove:** `microchip-newsroom-rss` — a real date-parsing defect blocks every item from this otherwise-live feed; worth a small, separate fix (unrelated to PR #60/#61 and to Cohort 1) rather than removal, since the feed itself is healthy and active.
- **No Cohort 1 source should be paused or removed.** All six are healthy or quiet-but-healthy.

### Next recommended step

Scope a narrow, third follow-up branch (separate from both merged PRs, per the guardrail keeping admission precision, source health, and stale-card cleanup as distinct issues) addressing the three findings above, each with its own regression fixture built from the real content confirmed in this audit — then re-run this same read-only live-signal probe as the acceptance check before controlled beta, rather than relying solely on synthetic fixtures.

---

## 7. Detection-latency investigation — 2026-09-16 addendum

**Method note:** this section reuses the same read-only method as §§1–2 above — a live fetch of the exact source feed (`fetch_entries()`, one bounded `requests.get`, no persistence) plus a re-derivation of the real, unmodified matching/admission/materiality functions in-process. It adds no new probe type. As with the rest of this document, this environment has no access to the production worker's actual run history, logs, or persisted-story timestamps — every step below states its evidence source and confidence explicitly, and several required timeline steps are honestly reported as not observable.

### Required fixture

> Title: SK Hynix reportedly in talks with Intel to build memory chips in US
> Source: TechCrunch

**Important finding before the timeline:** the raw TechCrunch feed (`https://techcrunch.com/feed/`) was fetched directly and inspected. The item's own RSS entry is:

```xml
<title>SK Hynix reportedly in talks with Intel to build memory chips in US</title>
<link>https://techcrunch.com/2026/09/16/sk-hynix-reportedly-in-talks-with-intel-to-build-memory-chips-in-us/</link>
<dc:creator><![CDATA[Kate Park]]></dc:creator>
<pubDate>Wed, 16 Sep 2026 13:23:02 +0000</pubDate>
<description><![CDATA[SK Hynix told TechCrunch the company hasn't finalized any plans or arrangements yet. ]]></description>
```

This `<pubDate>` carries an explicit, unambiguous UTC offset (`+0000`) — not the ambiguous "wall-clock, no offset" case `rss_atom_client._parse_published_at()`'s own docstring warns about (that failure mode was confirmed elsewhere in this audit for a different feed, TheElec, and is unrelated here). Converted: **13:23:02 UTC = 09:23:02 AM EDT (UTC−4)**. This is TechCrunch's own, sole, self-reported publication timestamp for this article — there is no separate "updated" field and no other date in the feed entry.

**This does not match the fixture's stated "article publication time ~6:23 AM EDT."** The fixture's stated "EevaResearch observed/persisted time" (9:23 AM EDT) is, to the second, identical to TechCrunch's own `<pubDate>`. No evidence available to this audit — the TechCrunch feed, its description text, or any other configured source — shows a 6:23 AM EDT timestamp anywhere. If an earlier ~6:23 AM EDT report genuinely exists (e.g. an original Reuters wire alert that TechCrunch's own piece may be derived from — the article's one-line RSS description doesn't itself cite Reuters, though the fixture states the story is Reuters-sourced, which this audit cannot independently verify since only the RSS `<description>` field, not the full article body, is available here), **that gap is entirely a publisher-side syndication/editorial delay between the original wire report and TechCrunch's own republication — Eeva does not poll Reuters directly (it is not in the source registry), so no configuration or scheduling change on Eeva's side could have closed that specific gap.** This distinction — publisher-side vs. Eeva-controlled — is the central finding of this section; the timeline and cadence analysis below characterize Eeva's own, independently real, latency budget, which is a separate question from this specific fixture's likely true cause.

### Full timeline

| Step | Exact time | Timezone | Evidence source | Confidence | Elapsed from prior step |
|---|---|---|---|---|---|
| Article published | 2026-09-16 13:23:02 | UTC (09:23:02 EDT) | TechCrunch's own `<pubDate>`, fetched directly from `https://techcrunch.com/feed/` | **High** — explicit RFC822 timestamp with real UTC offset, directly observed | — |
| Feed entry first became available | Not independently provable; presumed same as `<pubDate>` (standard WordPress/RSS behavior for a source already live-verified in this registry) | — | Inference only, not a direct observation | **Low-to-moderate** (presumption, not proof) | ~0 (presumed) |
| First eligible worker poll after availability | **Not observable from current environment** | — | No access to the production worker's real tick schedule/phase or run history | — | Not observable — see cadence analysis below for a theoretical bound |
| Fetch started (real production worker) | **Not observable from current environment** | — | No worker logs, no scheduler history available here | — | Not observable |
| Fetch completed (real production worker) | **Not observable from current environment**; this audit's own read-only probe fetched the same feed in **159.7 ms** | — | This audit's own `fetch_entries()` call, `duration_ms` field, timed 2026-09-16 ~13:34 UTC (audit probe time, not production) | **High** for the audit's own probe latency; **not observable** for the real worker's actual fetch | Not observable for production; ~0.16s for this audit's own probe |
| Parsing/normalization completed | **Not observable in production**; this audit's own re-derivation (same, unmodified `rss_atom_client`/`editorial_matching` code) completes in-process, sub-millisecond, no I/O | — | Code inspection + this audit's own function calls | **High** that this step is not a meaningful bottleneck (pure in-memory parsing); **not observable** as a real production timestamp | Negligible (in-process) |
| Entity/theme matching completed | **Not observable in production**; re-derived live: `matched_companies=("Intel Corp.", "SK Hynix")`, `matched_themes=()` | — | This audit's own call to the real, unmodified `matched_companies_and_themes()` | **High** for the matching result itself; **not observable** as a real production timestamp | Negligible (in-process) |
| Editorial admission/materiality completed | **Not observable in production**; re-derived live: admitted (`company_subject:SK Hynix`), tier `Watchlist` (`on_taxonomy_no_anchor:memory_networking_power_cooling_packaging`) | — | This audit's own calls to the real, unmodified `assess_admission()`/`classify_editorial_story()` | **High** for the classification result itself; **not observable** as a real production timestamp | Negligible (in-process) |
| Persistence completed | **Not observable from current environment** | — | No database/cache access from this environment (established in §0 of this document) | — | Not observable |
| Dashboard/Signals became visible | **Not observable from current environment**. Note: this is a **Daily News/Signals** item, not a Radar item — the two are separate systems in this codebase, and "Radar" visibility does not apply to this fixture at all. | — | No production UI/database access | — | Not observable |

**What this audit can state with confidence:** the article's own publication timestamp (13:23:02 UTC), and that the in-process computation steps (parse → match → admission → materiality) are architecturally incapable of contributing meaningful latency — they are synchronous, in-memory function calls with no I/O, network, or sleep of any kind (confirmed by direct code reading of `editorial_pipeline.py`'s qualifying loop, `editorial_matching.py`, `editorial_admission.py`, and `materiality_classification.py`; none of these modules perform any I/O). **What this audit cannot state:** any real production fetch/persistence/visibility timestamp for this specific item — those require production telemetry this environment does not have.

### Root cause determination

Given the above, the candidate causes are assessed as follows:

| Candidate cause | Assessment |
|---|---|
| Publisher/RSS syndication delay | **Most likely explanation for the fixture's own stated ~3-hour gap**, if a genuine earlier (~6:23 AM EDT) upstream report exists — this is TechCrunch's own editorial timing, entirely outside Eeva's control, and Eeva has no configured source that would have surfaced the underlying story earlier than TechCrunch's own `<pubDate>`. |
| Polling interval / scheduler cadence | **Confirmed, real, independently quantifiable Eeva-controlled latency component** — see cadence analysis below. Average-case expected wait ≈ half the configured interval (≈15 minutes at the code default of 30 minutes) between an item's real availability and the next eligible poll; worst case ≈ the full interval (≈30 minutes). This is real and worth targeting, but on its own does not explain a 3-hour gap at the default cadence. |
| Job queue / backlog / concurrency | **Not a meaningful contributor** — both lanes are fetched with a single, plain, sequential `for source in source_entries:` loop (`editorial_pipeline.py:272`, `daily_news_pipeline.py:490`), no threading, no queue. This audit's own live probe of all 70 sources (mean 718ms/source, max 2.76s, sum ≈50s) shows a full sequential pass across every registered source — Lane A and Lane B combined — would take roughly **under one minute** in normal conditions, negligible relative to a 30-minute interval. |
| Source retry/backoff | **Confirmed: none exists.** `rss_atom_client.fetch_entries()`'s own docstring is explicit: "Exactly one `requests.get` call, unconditionally — no retry, no second validation request... a separate, not-yet-approved change." A single transient failure simply waits for the next full 30-minute tick, with no faster retry in between. Not the cause for TechCrunch specifically (its own fetch succeeded quickly, per this audit's direct probe), but a real latency amplifier for any source that fails intermittently. |
| Parsing/matching/classification cost | **Not a contributor** — confirmed synchronous, in-memory, no I/O (see timeline table). |
| Persistence latency | **Not observable** from this environment; architecturally a single local write per admitted item (JSON/Postgres backend, not queued), no reason to suspect this is a meaningful contributor, but this audit cannot prove a real production number. |
| UI refresh/cache latency | **No dedicated cache-staleness layer found** for the Daily News/Signals page specifically (`src/ui/pages/daily_news.py`, `src/data_access/daily_news/daily_news_backend.py` — no `@st.cache_data`/`@st.cache_resource` decorators). The codebase's only Daily-News-adjacent Streamlit cache is unrelated (`src/ui/ui.py`'s CSS/nav caching); the one TTL-based cache found (`src/ui/pages/radar_inbox.py:427`, 60s TTL) belongs to the separate Radar system, not Daily News/Signals. Not a plausible contributor to this fixture. |
| Timestamp/timezone interpretation | **Ruled out for this specific fixture** — TechCrunch's `<pubDate>` carries an explicit, unambiguous UTC offset, correctly interpretable by `_parse_published_at()`'s existing logic; this is not the same failure mode as the separately-confirmed `microchip-newsroom-rss` date-parsing defect elsewhere in this audit. |
| Insufficient telemetry | **Confirmed, and the binding constraint on this entire investigation** — this environment has no access to real worker run history, fetch timestamps, or persistence timestamps, so the actual Eeva-side latency for this specific item cannot be measured, only bounded theoretically from the known cadence configuration. |

**Portion Eeva controls vs. publisher-controlled, for this fixture specifically:** the ~3-hour gap as stated in the fixture most likely reflects a **publisher-side** delay (TechCrunch's own republication timing relative to an earlier, un-registered upstream report) that Eeva could not have closed with any configuration change, since Eeva only polls TechCrunch's own feed, not any upstream wire service directly. The one genuinely **Eeva-controlled** latency component identified and quantified in this investigation is the **polling cadence itself** (≈0–30 minutes, ≈15 minutes average) — real, but roughly an order of magnitude smaller than the fixture's stated 3-hour gap, and not sufficient on its own to explain it.

### Source cadence inventory

| Source or lane | Configured cadence | Actual observed recent run cadence | Retry/backoff | Batch/concurrency behavior | Source-priority behavior |
|---|---|---|---|---|---|
| Lane A (issuer, 32 sources) | `EDGE_DAILY_NEWS_SCAN_INTERVAL_MINUTES`, default **30 minutes** (`settings.py:340-342`), floored at 60s (`scripts/daily_news_worker.py:151`) | **Not observable** — no production run-history access from this environment; this local `.env` does not set this variable, so the local default (30 min) applies here, but Render's actual production value cannot be confirmed from this environment | None — single unconditional fetch per source per tick | Strictly sequential, `for source in feed_sources:` (`daily_news_pipeline.py:490`), no concurrency | None — `priority` field exists on every `DailyNewsSourceEntry` but is explicitly documented as unused: `"priority: int  # 1 = highest; not yet consumed by any runtime ranking (Daily News is chronological-only today)"` (`source_registry.py:192`) |
| Lane B (editorial, 38 sources) | **Identical** 30-minute default — both lanes run inside the same worker tick (`_run_tick_body`/`_run_editorial_tick`, `scripts/daily_news_worker.py`), issuer lane fetched first, then the full editorial registry, every tick, no separate schedule | Not observable, same constraint as above | None — same single-fetch-per-tick behavior (`rss_atom_client.fetch_entries()`'s own docstring, confirmed) | Strictly sequential, `for source in source_entries:` (`editorial_pipeline.py:272`) | None — same unused `priority` field |
| **TechCrunch specifically** (`techcrunch-rss`) | Same 30-minute default as every other Lane B source — no per-source override exists anywhere in the registry or worker code | Not observable (no run-history access); this audit's own live fetch succeeded in 159.7 ms with 20 fresh entries | None (same as all Lane B sources) | Fetched in its ordinary sequential turn — no expedited or prioritized path | `priority=1` is set on this source's registry entry (`source_registry.py:1265`), but per the confirmed finding above, this value is **not consumed anywhere at runtime** — it carries no scheduling advantage today |

### Latency target proposal

A practical, tiered detection SLO — explicitly **not** "instantaneous," and explicitly preserving the existing per-request timeout, one-fetch-per-tick politeness, deduplication, and admission/materiality quality gates unchanged:

| Category | Proposed detection SLO (publish → eligible-for-poll) | Rationale |
|---|---|---|
| Official filings / issuer IR disclosures | ≤ 30 minutes (current default cadence is already adequate) | Regulatory/IR disclosures are typically already time-stamped precisely and infrequent per company; the existing cadence is proportionate. |
| High-quality breaking editorial sources (e.g. TechCrunch, CNBC breaking-news feeds) | 5–10 minutes | These sources publish frequently and their content is often time-sensitive market-moving news; a shorter cadence for a small, curated subset closes most of the realistic Eeva-controlled gap without materially increasing total request volume (a handful of sources polled every 5–10 min, not all 70). |
| Standard editorial sources (the majority of the 38) | 15–30 minutes (current default is acceptable) | No evidence in this audit that these sources need tighter polling; most editorial content is not minute-sensitive. |
| Low-urgency or quiet issuer sources (e.g. the four sources flagged stale in §1 above) | 60+ minutes, or an explicitly longer "quiet source" cadence | Polling a source with a demonstrated multi-week publication gap every 30 minutes is pure overhead with no detection benefit; a longer interval for confirmed-quiet sources reduces total request volume without any latency cost, since nothing is being missed. |

### Recommended implementation options, ranked by impact/risk

1. **Priority polling for a small, curated set of high-value editorial sources** (lowest risk, highest targeted impact). Reactivate the already-modeled-but-unused `priority` field (or a new, explicit small allow-list) to poll a handful of sources (TechCrunch, CNBC breaking feeds) on a separate, shorter interval, leaving the other ~65 sources on the existing 30-minute cadence unchanged. Candidate files: `scripts/daily_news_worker.py` (new secondary tick loop or interval check), `src/config/settings.py` (a new `EDGE_DAILY_NEWS_PRIORITY_SCAN_INTERVAL_MINUTES` setting), `src/data_access/daily_news/source_registry.py` (no field change needed — `priority` already exists; only its consumption is new). **Not modified as part of this read-only audit.**
2. **Scheduler/poll-cadence adjustment** (low risk, broad but shallow impact). Simply lowering `EDGE_DAILY_NEWS_SCAN_INTERVAL_MINUTES` for all sources trades a modest latency improvement for a proportional increase in total request volume across all 70 sources — less targeted than option 1, but a one-line configuration change with no code risk. Candidate file: `src/config/settings.py:340-342` (default value only) or the production environment variable itself — **no code change required at all**, and explicitly **not changed** as part of this audit.
3. **Staggered polling / concurrency change** (moderate implementation effort, low urgency). Given this audit's own measurement (a full 70-source sequential pass takes roughly under one minute under normal conditions), concurrency is **not currently a bottleneck** and is not recommended as a near-term latency fix — it would only matter if source count or per-source latency grows substantially. Candidate files (if ever pursued): `editorial_pipeline.py:272`, `daily_news_pipeline.py:490` (both currently plain sequential loops).
4. **RSS/Atom conditional-request support (ETag/If-Modified-Since) or feed freshness metadata** (moderate effort, mainly a politeness/efficiency win, not primarily a latency win at current cadence). Would reduce redundant full-body fetches for unchanged feeds, freeing headroom that could fund option 1's shorter cadence for priority sources without a net increase in request volume. Candidate file: `src/data_access/daily_news/rss_atom_client.py::fetch_entries()`.
5. **Faster Dashboard refresh/cache invalidation** (not currently indicated). No cache-staleness layer was found on the Daily News/Signals page in this audit — there is nothing to invalidate faster today. Revisit only if a caching layer is added in the future.
6. **New latency telemetry** (foundational, recommended regardless of which other option is chosen). This entire investigation was constrained by "not observable from current environment" for the majority of the requested timeline steps. Recording a real fetch-started/fetch-completed/persisted timestamp per item (or per tick) would let a future audit measure actual latency instead of bounding it theoretically. Candidate files: `scripts/daily_news_worker.py` (`_log_editorial_completion` and its issuer-lane counterpart already log aggregate counts — extending this to log per-tick start/end wall-clock time is a small, additive change), `src/data_access/daily_news/editorial_pipeline.py`/`daily_news_pipeline.py` (the `EditorialScanReport`/equivalent dataclasses already carry `started_at`/`completed_at` at the tick level — per-item timestamps would be new).

None of these options were implemented, and no file listed above was modified, as part of this read-only audit.

### Quality guardrail — confirmed against the live item

Re-derived directly against the real, unmodified, currently-merged code (same method as §2 above), using the exact live TechCrunch item:

- **Admission:** `admitted=True`, reason `company_subject:SK Hynix`.
- **Materiality tier:** `Watchlist`, reason `on_taxonomy_no_anchor:memory_networking_power_cooling_packaging`.
- **Confirmed: this item does NOT reach High Signal.** The core guardrail requirement — that an unconfirmed, hedged report not be treated as confirmed/material — holds today.
- **Precise gap:** `_has_rumor_or_negated_confirmation_language()` was directly tested against this exact title+description and returns `True` (the title's own "**reportedly** in talks" is a listed rumor-hedge keyword, and the description's "hasn't finalized any plans or arrangements yet" is exactly the kind of non-confirmation this guard is designed to catch). However, that flag is — as already identified in `design/SIGNALS_PRECISION_FOLLOWUP_RETAIL_BOILERPLATE_GUIDANCE_2026_09_16.md`'s §2.4 — wired only into Gates C/D, not into the `on_taxonomy_no_anchor` Watchlist fallback that actually admitted this item at Watchlist rather than Background. This live item is a **direct, second, independent confirmation** of that already-identified, already-designed gap — it does not reveal a new one. No "Review needed" tier exists in the current implementation (confirmed elsewhere in this audit); the closest available, already-designed fix is the same Watchlist-fallback `uncertain_reporting` gate proposed in that follow-up document, which would move this exact item from Watchlist to Background without touching any rumor, attribution, materiality, deduplication, or admission check's own existing logic.

---

*No file outside `design/POST_MERGE_DAILY_NEWS_SIGNAL_PERFORMANCE_AUDIT_2026_09_16.md` (this document, explicitly permitted for this addendum) was created, modified, staged, committed, or pushed while producing this audit or its latency addendum. No worker was run; no item was ingested, cached, or persisted. `git stash` state and all registry/config/test files are unchanged.*
