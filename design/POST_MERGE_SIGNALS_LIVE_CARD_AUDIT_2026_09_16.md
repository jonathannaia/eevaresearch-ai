# Post-Merge Signals Live-Card Audit — 2026-09-16

**Status:** Read-only audit. No code, test, configuration, registry, worker, UI, database, cache, persisted story, or deployment file was modified. No worker run, no live discovery, no write.

**Base:** `origin/main` at `c2536268689488d7e44e93b1ce6463c26030a271` (merged 2026-09-16T17:15:52Z — PR #62, "fix(signals): filter retail noise and content contamination").

**Method and honesty note:** as with every prior audit this session, this environment has no access to the production (Render) database/cache — there is no way to inspect an actually-persisted Dashboard card or a real worker's ingestion timestamp. Four of the five required fixtures were located **live, verbatim**, in their real, currently-registered RSS source, via the same narrowly-bounded, read-only `fetch_entries()` probe used in prior audits (one bounded HTTP GET per source, no persistence), then re-derived through the real, current, merged `matched_companies_and_themes()` / `assess_admission()` / `classify_editorial_story()` / `classify_issuer_story()` functions. The fifth (CoreWeave) could not be located in any of the 70 currently-registered sources' live feeds at probe time; it is analyzed on a policy basis only, with that limitation stated explicitly rather than guessed. Where "before/after merge" is reported, it compares the **source's own claimed publication time** to the merge timestamp — this environment cannot determine when (or whether) Eeva's own production worker actually ingested or persisted any item, since no production telemetry is reachable here.

---

## Fixture 1 — CoreWeave / NVIDIA Vera Rubin NVL72

**Not observable live in the current sample.** A full scan of all 32 issuer-linked and 38 editorial sources' live RSS content found no item with "CoreWeave" in its title. Two related, genuinely live NVIDIA newsroom items about the same Vera Rubin NVL72 platform were found and are used as the closest available reference (see the "Required controls" section below).

| Field | Value |
|---|---|
| Source and type | Not found in any of the 70 registered sources at probe time |
| Raw input | Not available |
| Publication / persisted time | Not observable |
| Source lane | Unknown — plausibly Lane A (`nvidia-newsroom-rss`, since NVIDIA's own newsroom is actively publishing Vera Rubin NVL72 content this exact week) or a Lane B editorial pickup |
| Matched companies/themes | Not observable |
| Admission result | Not observable |
| Materiality tier | Not observable |
| Dashboard visibility path | Not observable |
| Stale vs. post-merge | **Cannot be determined** — most RSS feeds retain only their most recent 10–50 items; if this story genuinely existed, it may have already rolled off its source's live window by probe time. This does not distinguish "was correctly published, then aged out of the feed" from "was persisted from a stale, pre-merge pipeline run." |
| Verdict | **Not verifiable — insufficient live evidence, not a confirmed false positive or a confirmed correct result** |
| Narrowest responsible code layer | Cannot be assigned without the real item |

**Policy-only analysis (title/tags as given):** "CoreWeave brings up multi-rack NVIDIA Vera Rubin NVL72 cluster" names a real customer (CoreWeave), a real named platform (Vera Rubin NVL72), and an operational milestone ("brings up" — a concrete, if not yet fully financial, operating event) with an implied scale ("multi-rack"). If the real article states a genuine rack/cluster count or a comparable quantified figure locally near this language, it should reach at least Watchlist (via Gate C's local taxonomy+anchor pairing on the `ai_compute_and_data_center`/deploy-family logic) or High Signal (via Gate B, if a real number is present). **A real risk exists independent of any defect in the merged fix**: the two confirmed-live NVIDIA newsroom items below show that NVIDIA's own RSS feed sometimes provides only a short, abstract teaser paragraph with no quantified figure at all — if this item's real RSS summary is similarly abstract, it would legitimately land at Background purely from feed-content brevity, not from a policy gap.

---

## Fixture 2 — smartARM / Meta Platforms, Inc.

| Field | Value |
|---|---|
| Source and type | `meta-newsroom-rss` — Lane A, `OFFICIAL_NEWSROOM`, Meta's own official press domain (`about.fb.com`) |
| Raw title | "Canadian Start-up smartARM Uses AI to Create Intuitive Bionic Prosthetics" |
| Raw body (verbatim, key excerpt) | *"...smartARM, a Toronto-based startup developing a vision-first bionic arm prototype that uses open-source software, with Meta AI glasses as an optional add-on... smartARM uses Meta AI Glasses and the Meta Wearables Device Access Toolkit to extend and improve object recognition and understanding..."* |
| Link | `https://about.fb.com/news/2026/09/canadian-start-up-smartarm-uses-ai-to-create-intuitive-bionic-prosthetics/` |
| Publication time | `2026-09-16T16:00:19+00:00` (real, explicit UTC-offset RSS `<pubDate>`, confirmed against raw XML) |
| Eeva persisted time | Not observable (no production database access) |
| Source lane | **Lane A (issuer)** — this is Meta's own official newsroom publishing about a third party, not a Lane B editorial pickup |
| Matched companies/themes | N/A for Lane A — issuer stories are tagged with the single feed-owner company (`Meta Platforms, Inc.`) directly, never text-matched |
| Admission result | N/A — Lane A has no admission gate (confirmed architectural fact, unchanged by this or the prior branch); every fresh, valid, non-duplicate item is unconditionally published |
| Materiality tier and reason | **Background**, `off_taxonomy_no_anchor` (re-derived live against the real, current `classify_issuer_story()`) |
| Dashboard visibility path | Not default-visible on the Signals page (Background → hidden behind `st.expander("Show Background (N)")`); **is** exposed to Dashboard "Recently Updated" if among the 5 most recent items merged across sources, since `recently_updated.py`'s `_load_daily_news_rows()` filters only on `NewsStoryStatus.PUBLISHED`, never on `materiality_tier` (confirmed, unchanged, pre-existing gap — see the prior turn's clarification) |
| Stale vs. post-merge | Article's own publish time (16:00:19 UTC) is **before** the merge (17:15:52 UTC) by about 75 minutes; whether Eeva's own production pipeline had already ingested it before or after the merge deployed cannot be determined from this environment |
| **Verdict** | **Correct** — this is not a spurious third-party mention: Meta's own newsroom is the publisher, and the article discloses a genuine, named product-integration relationship (Meta AI Glasses hardware and the Meta Wearables Device Access Toolkit SDK), exactly matching the fixture's own stated "expected" carve-out ("product integration... disclosed commercial relationship"). Background is the proportionate tier — no revenue, scale, or adoption figure is disclosed. |
| Narrowest responsible code layer | None — this is not a defect |

---

## Fixture 3 — RTX 50-series laptop power-limit mod

| Field | Value |
|---|---|
| Source and type | `toms-hardware-rss` — Lane B, `INDEPENDENT_NEWS` |
| Raw title | "Developer vibe codes a tool to let Nvidia RTX 50-series laptop owners crank up their power limits — can juice RTX 5090 mobile GPU to 225W" |
| Raw body (verbatim, key excerpt) | *"...a new tool called NvpwrControl to unlock additional performance from their assuredly power-limited mobile GPU... spotted by VideoCardz, is available for download on GitHub, and it is labeled as 'experimental'... it's clearly AI-generated in the largest part. The developer is called 'LevinAI'..."* Intel appears exactly once, in a passing three-way vendor-naming-convention comparison: *"...or Intel naming integrated graphics like a discrete GPU..."* — never as a subject of any action. |
| Link | `https://www.tomshardware.com/pc-components/overclocking/developer-vibe-codes-a-tool-to-let-nvidia-rtx-50-series-laptop-owners-crank-up-their-power-limits-can-juice-rtx-5090-mobile-gpu-to-225w` |
| Publication time | `2026-09-16T15:19:42+00:00` |
| Eeva persisted time | Not observable |
| Source lane | Lane B (editorial) |
| Matched companies/themes | `matched_companies=('Intel Corp.', 'NVIDIA')`, `matched_themes=()` (re-derived live) |
| Admission result and reason | **Admitted**, `company_subject:NVIDIA` — `_identified_subject_companies()` correctly resolves `identified_companies=['NVIDIA']` only; Intel is matched (its alias literally appears) but correctly **not** identified as a subject (title placement fails, and the grammatical-agency check correctly finds no action language attached to it) |
| Materiality tier and reason | **Watchlist**, `on_taxonomy_no_anchor:ai_compute_and_data_center` |
| Dashboard visibility path | **Visible by default on the Signals page itself**, under the "Watchlist" section header — no click/expander required, unlike the four Background items in this audit. Also exposed to Dashboard "Recently Updated" under the same untiered-merge gap described above. |
| Stale vs. post-merge | Article published 15:19:42 UTC, **before** the merge (17:15:52 UTC) by about 116 minutes; Eeva's own ingestion timing not observable |
| **Verdict** | **False positive** — this is unambiguous consumer/hobbyist/developer-modding content (an experimental, AI-generated, community GitHub tool for voiding a laptop's own power-delivery limits), with zero corporate action, disclosure, or material consequence. NVIDIA's admission is technically correct per the *existing* identity rules (its name is genuinely in the title), but no existing content-shape exclusion targets this format at all. **This is the single most severe finding in this audit**, since it is both a confirmed live false positive *and* the only one of the five fixtures currently visible without any click at all. |
| Narrowest responsible code layer | `src/data_access/daily_news/editorial_admission.py::assess_admission()` — none of the four existing content-shape exclusions (consumer-format/retail-deal, law-firm solicitation, personnel-announcement, and the newly-added retail-deal/scarcity patterns from PR #62) recognize a "hobbyist mod / overclocking / developer tool" shape at all |

Separately confirmed: `EditorialStory.matched_companies` stores the raw `('Intel Corp.', 'NVIDIA')` tuple, so **the Dashboard would show "Intel Corp." as a company tag** on this card even though Intel was correctly excluded from the admission decision itself — a live, concrete re-confirmation of the display-attribution gap already identified (but not yet fixed) in `design/POST_MERGE_SIGNALS_PRECISION_AUDIT_2026_09_15.md`.

---

## Fixture 4 — Samsung One UI 9 rollout

| Field | Value |
|---|---|
| Source and type | `samsung-newsroom-rss` — Lane A, `OFFICIAL_NEWSROOM` (`news.samsung.com`) |
| Raw title | "Samsung Begins Official Rollout of One UI 9, Bringing the Latest Galaxy Experiences to More Devices" |
| Raw body (verbatim, key excerpt) | *"Samsung Electronics today announced the expansion of One UI 9 to more Galaxy devices, with the rollout beginning today starting with the Galaxy S26, S26+, and S26 Ultra... One UI 9 brings an easier, more seamless mobile experience across form factors, as well as enhanced Galaxy AI that is richer, more personal, and more helpful."* — a feature list follows; no revenue, unit, demand, or pricing figure appears anywhere in the fetched content. |
| Link | `https://news.samsung.com/global/samsung-begins-official-rollout-of-one-ui-9-bringing-the-latest-galaxy-experiences-to-more-devices` |
| Publication time | **Notable finding**: the raw feed's own `<pubDate>` is `Wed, 16 Sep 2026 21:00:00 +0000` — a timestamp in the future relative to this audit's probe time (~17:20–17:30 UTC). `rss_atom_client._parse_published_at()`'s own, already-documented "never trust a future timestamp" guard (`_FUTURE_TOLERANCE = 5 minutes`) correctly discards this and substitutes the fetch-time "now" instead — this is confirmed, intended, correct existing behavior (verified against the function's own docstring and by observing the substituted timestamp change across three separate fetches, exactly as the future-discard logic predicts), not a new defect. The article's *true* availability time is therefore effectively "now" — **after** the merge — even though Samsung's own CMS scheduled/labeled it for a later hour. |
| Eeva persisted time | Not observable |
| Source lane | Lane A (issuer) |
| Matched companies/themes | N/A — Lane A, single-company tagging (`Samsung Electronics`) |
| Admission result | N/A — Lane A has no admission gate |
| Materiality tier and reason | **Background**, `off_taxonomy_no_anchor` |
| Dashboard visibility path | Not default-visible on the Signals page (Background expander); exposed to Dashboard "Recently Updated" under the same untiered-merge gap |
| Stale vs. post-merge | Effectively **post-merge** (see the future-timestamp note above — the item's real availability is at/after probe time, well after the 17:15:52 UTC merge) |
| **Verdict** | **Correct** — a routine consumer OS/UI feature rollout with no disclosed adoption, pricing, revenue, AI-monetization, capacity, or supply-chain evidence anywhere in the available content lands exactly at Background, matching the fixture's own stated default expectation precisely. |
| Narrowest responsible code layer | None — this is not a defect. (The future-pubDate handling is a separate, correctly-functioning existing safeguard, noted here for completeness only.) |

---

## Fixture 5 — L3Harris "The Invisible War Has a New Architecture"

| Field | Value |
|---|---|
| Source and type | `l3harris-newsroom-rss` — Lane A, `OFFICIAL_NEWSROOM` |
| Raw title | "The Invisible War Has a New Architecture" |
| Raw body (verbatim, full) | *"Transforming electronic warfare into a distributed, adaptive and collaborative force multiplier."* — a one-line strategic/thought-leadership standfirst; no named program, contract, award, or budget figure anywhere in the fetched content |
| Link | `https://www.l3harris.com/newsroom/editorial/2026/09/invisible-war-has-new-architecture` |
| Publication time | `2026-09-16T15:12:34+00:00` |
| Eeva persisted time | Not observable |
| Source lane | Lane A (issuer) |
| Matched companies/themes | N/A — Lane A, single-company tagging (`L3Harris Technologies, Inc.`) |
| Admission result | N/A — Lane A has no admission gate |
| Materiality tier and reason | **Background**, `off_taxonomy_no_anchor` |
| Dashboard visibility path | Not default-visible on the Signals page (Background expander); exposed to Dashboard "Recently Updated" under the same untiered-merge gap |
| Stale vs. post-merge | Article published 15:12:34 UTC, **before** the merge (17:15:52 UTC) by about 123 minutes; Eeva's own ingestion timing not observable |
| **Verdict** | **Correct** — this is the same strategic-commentary/thought-leadership content shape already confirmed and correctly handled in the prior performance audit (L3Harris's own "If the Best AI Wins, America Must Build It" piece, also correctly Background). No named contract, award, procurement, budget, or measurable operating change is present, so Background — not a rejection this branch's scope never proposed for Lane A anyway — is the correct, expected outcome. |
| Narrowest responsible code layer | None — this is not a defect |

---

## Root-cause category assignment

| Fixture | Category |
|---|---|
| 1. CoreWeave | Not determinable (insufficient live evidence) |
| 2. smartARM/Meta | None — correct classification; if anything, an instance of legitimate "official product integration" the existing policy is explicitly designed to preserve |
| 3. RTX power-limit mod | **Hobbyist/developer/modding format** — no existing content-shape exclusion covers this shape; secondarily, **Dashboard Recently Updated tier-blind rendering** and the pre-existing **company-alias display (matched_companies vs. identified_companies)** gap both independently apply |
| 4. Samsung One UI 9 | None — correct classification (Background/Watchlist/High-Signal materiality logic working as intended for this content shape) |
| 5. L3Harris | None — correct classification; same **strategic thought-leadership/commentary format** already handled correctly by the existing "no anchor, no reasons" fallback |

No fixture in this sample implicates company alias/matching (beyond the already-known, already-documented display gap), generic AI keyword/theme mismatch, or consumer OS/device format as a *new* defect — those categories are either not exercised by this sample or already behaving correctly.

---

## New policy gap analysis (design only — not implemented)

### 1. Private-company third-party attribution
Not exercised as a defect by this specific sample (smartARM/Meta is a genuine Lane A first-party disclosure, not a Lane B text-match false attachment) — but the general rule remains worth designing preemptively, since a *future* Lane B story about a private startup that merely name-drops a tracked company's product (without that company's own newsroom publishing it) would need this protection. Narrow design: extend `_identified_subject_companies()`'s existing rival-entity-exclusion mechanism with a parallel "private-company subject, tracked-company-mention-only" check — when the grammatically-identified actor of the sentence is an untracked/private entity and a tracked company is named only as a used-technology/platform reference (not the sentence's own subject), the tracked company should not independently qualify as `company_subject` unless a `_HARD_MATERIAL_REASON_PREFIXES`-qualifying anchor is also present locally. This mirrors the same "identity precedes materiality, anchor evidence rescues" shape already used throughout `editorial_admission.py`.

### 2. Consumer/hobbyist/developer modification content
**Directly evidenced by Fixture 3.** Narrow design: add a fifth admission-layer content-shape exclusion in `editorial_admission.py`, structurally identical to the existing four (`_matched_consumer_format`, `_matched_law_firm_solicitation`, `_matched_personnel_announcement`, and PR #62's retail-deal/scarcity patterns) — a curated, narrow phrase/pattern list naming the real, distinctive vocabulary of this content shape: "vibe cod(ed|es|ing)", "modder", "mod tool", "overclock(ing)?", "power limit" + "crank(ing)?"/"unlock", "GitHub" + "experimental", "benchmark" + "tweak", "DIY", "hobbyist", "enthusiast tool". Same anchor-evidence-required rescue as the other four (an official, company-disclosed driver/firmware/security update mentioned in the same piece remains admissible). This must never be a bare ban on "GitHub," "developer," or "power" — only the distinctive combination already observed live.

### 3. Consumer software/OS rollout
**Already correctly handled** by existing materiality logic for Fixture 4 (Background by default, absent a quantified figure). No code change indicated by this sample; the existing Gate B/C machinery already produces the exact desired outcome for this content shape. Recommend documenting this as an already-satisfied requirement rather than opening new work.

### 4. Strategic/defense commentary
**Already correctly handled** by existing materiality logic for Fixture 5 (Background by default, absent a named program/contract/award figure) — consistent with the same pattern already confirmed in the prior performance audit. No code change indicated.

### 5. Dashboard tier-aware preview
**Confirmed, live, cross-cutting exposure** — all four Background-tier items in this sample (smartARM, Samsung, L3Harris, and — had it been found live — presumably CoreWeave if it also lands at Background) are exposed to Dashboard "Recently Updated" exactly as already described in the prior clarification turn; Fixture 3 additionally shows that even a **Watchlist**-tier false positive is visible without any click at all, on the Signals page's own default view. This is a UI proposal, kept separate per your own instruction, **not** implemented here: `recently_updated.py`'s `_load_daily_news_rows()`/`_load_editorial_rows()` would need to either exclude Background-tier rows from the default preview, restrict the preview to High Signal/Watchlist only, or add a visible tier label and rank Background rows below true Signal rows. No live-item evidence in this audit indicates the underlying admission/materiality *pipeline* itself has a defect for Fixtures 4 and 5 — the exposure is purely a rendering/filtering gap in a file already identified as out of scope for the prior branch.

---

## Required controls — verification

| Control | Result |
|---|---|
| CoreWeave/Nvidia real production deployment with named scale/counterparty | Not independently verifiable this cycle (Fixture 1 not found live) |
| Meta direct investment/partnership/product integration | **Confirmed preserved** — Fixture 2 is exactly this shape (Meta AI Glasses + Wearables Device Access Toolkit), correctly published (Lane A) and correctly tiered Background pending further material evidence |
| Nvidia/Intel official driver/security/enterprise infrastructure disclosure | **Partially confirmed, with an important caveat.** The synthetic, unit-tested positive control (`test_nvidia_quantified_ai_factory_power_flexibility_remains_high_signal`, still passing) proves the *logic* correctly promotes a quantified NVIDIA infrastructure disclosure to High Signal when a real figure is present in the text. Live-checked directly against two real, current NVIDIA newsroom items about the same Vera Rubin NVL72 platform this week: both landed at **Background**, `off_taxonomy_no_anchor` — not because of any classification defect, but because NVIDIA's own RSS feed provides only a short (448-character), figure-free abstract for these specific posts, ending in a literal "[…]" truncation marker. This is a **source/feed-richness limitation**, independent of and not introduced by the Signals precision work — worth flagging for awareness, not a regression to fix in this branch. |
| Samsung disclosure with material demand/revenue/capacity/AI monetization evidence | Not present in Fixture 4's own available content — correctly demoted to Background; the rule itself (promote to High Signal when such evidence exists) remains unchanged and untested by this specific sample, since no qualifying Samsung item was found live this cycle |
| L3Harris named defense/space award, procurement, funded program, or deployment | Not present in Fixture 5; the rule remains unchanged and already proven correct on other L3Harris fixtures in the prior audit and in this session's own regression suite (`test_p03_spaceforce_item_with_named_funded_program_remains_eligible`, `test_regression_space_executive_tied_to_funded_program_is_eligible`) |

---

## Summary verdicts

| # | Fixture | Verdict | Pre/post merge |
|---|---|---|---|
| 1 | CoreWeave / Vera Rubin NVL72 | Not verifiable (insufficient live evidence) | Not determinable |
| 2 | smartARM / Meta | Correct | Article pre-merge (16:00 UTC vs. 17:15:52 UTC merge); Eeva ingestion timing not observable |
| 3 | RTX power-limit mod / Intel+NVIDIA | **False positive** | Article pre-merge (15:19 UTC); Eeva ingestion timing not observable |
| 4 | Samsung One UI 9 | Correct | Effectively post-merge (future-pubDate discard places true availability at/after probe time) |
| 5 | L3Harris "Invisible War" | Correct | Article pre-merge (15:12 UTC); Eeva ingestion timing not observable |

---

*No file other than this new document was created, modified, staged, committed, or pushed while producing this audit. No worker was run; no item was ingested, cached, or persisted; no source registry, scheduler, configuration, or deployment setting was touched.*
