# Daily News Source Census and Cohort 1 Onboarding Design (2026-09-15)

Read-only research/design document. No file under `src/`, no test, no
runtime source registry, feed registry, configuration, worker,
database, cache, or production data was modified to produce this
document. Starts from `origin/main` after merged PRs #57, #58, #59
(confirmed via `git log`/`git status` this session — working tree
clean except the 8 pre-existing untracked artifacts). No discovery
run, network scan, cache write, or database write was performed.
Public web checks (read-only HTTP fetches to verify a candidate
source's own feed URL, ownership, and language) were used only where
this document proposes a new candidate — never to ingest content into
any repository file.

---

## Headline finding — read before the rest of this document

**The real, current runtime-active Daily News source count is 64, not
~20.** The "prior 20-entry registry/pilot baseline" this task's own
framing references is a real, precise snapshot from earlier in this
project's own commit history — `PILOT_SOURCE_REGISTRY` (12) +
`EXPANSION_BATCH_1_SOURCE_REGISTRY` (7) + `EXPANSION_BATCH_2_SOURCE_
REGISTRY` (1) = exactly 20, the issuer-linked lane's size at that
point — but three more batches (+4, +3, +1 = +8) have since grown that
same lane to 28, **and a wholly separate, later-built 36-source
editorial (issuer-agnostic) lane was layered on top**, unconditionally
polled every worker tick alongside the issuer-linked lane. See §1.4
for the full reconciliation.

**Practical consequence for the rest of this document**: this task's
own "path to 25 validated sources" framing (§5) needs inverting. The
raw count is not the bottleneck — it already exceeds 25 by a wide
margin. What's actually missing is *targeted* coverage: as of this
session, **zero of the 10 newly-added Cohort 1 supply-chain issuers
(Hanmi Semiconductor, HD Hyundai Electric, Nabtesco, Harmonic Drive
Systems, Yaskawa Electric, GE Vernova, Digital Realty, Equinix,
Firefly Aerospace, L3Harris Technologies) have any Daily News source
at all**, the `space` theme has **zero** issuer-linked sources and
**zero** dedicated trade-press coverage, and `humanoids` has exactly
one issuer-linked source and zero dedicated trade-press coverage. §4's
6 recommended additions are chosen specifically to close these
precise gaps, not to move a raw counter.

---

## 1. Runtime source census

### 1.1 Architecture trace — registry to discovery to Dashboard Signals rendering

Two structurally separate, parallel lanes, both invoked once per tick
by `scripts/daily_news_worker.py` (confirmed by direct read this
session — `worker.py`'s own docstring: "this worker now also runs
`editorial_pipeline.run_editorial_discovery()` exactly once, second"):

**Lane A — issuer-linked (company press-release) lane:**
```
src/data_access/daily_news/source_registry.py
  RUNTIME_SOURCE_REGISTRY (28 entries: PILOT_SOURCE_REGISTRY +
  EXPANSION_BATCH_1..5_SOURCE_REGISTRY)
        │  to_daily_news_feed_source(), filtered to
        │  entry.enabled and entry.format == RSS_ATOM
        ▼
src/data_access/daily_news/feed_registry.py
  PILOT_FEEDS (28 DailyNewsFeedSource records)
        │  company identity/ticker/theme resolved via
        │  tracked_company_for() against tracked_companies.py,
        │  then issuer_registry.DISCOVERY_STUBS
        ▼
src/data_access/daily_news/daily_news_pipeline.py
  run_discovery() — fetches each feed (rss_atom_client.py),
  validates each item's link (canonical_url.py — https-only,
  on-domain gate), applies editorial_admission.py's consumer-retail
  suppression + localization_dedup.py's cross-language duplicate
  gate + materiality_classification.py, calls
  select_canonical_stories() for cross-source dedup
        ▼
src/data_access/daily_news/daily_news_backend.py
  get_daily_news_repository() — JSON/SQLite/Postgres-backed,
  environment-selected
        ▼
src/ui/pages/dashboard.py, src/ui/components/recently_updated.py,
src/ui/components/recent_theme_activity.py, src/ui/pages/daily_news.py
```

**Lane B — editorial (issuer-agnostic) lane:**
```
src/data_access/daily_news/source_registry.py
  EDITORIAL_SOURCE_REGISTRY (36 entries, always-on) +
  GATED_MARKET_NEWS_SOURCE_REGISTRY (1) / GATED_JP_KR_SOURCE_REGISTRY (3),
  filtered through market_news_sources.enabled_gated_sources() /
  jp_kr_sources.enabled_gated_sources() against the
  daily_news_enabled_market_news_sources / daily_news_enabled_jp_kr_sources
  env-var allow-lists (both EMPTY in this environment — see §1.3)
        ▼
src/data_access/daily_news/editorial_pipeline.py
  run_editorial_discovery() — fetches each feed, matches company/theme
  via editorial_matching.py's alias + compound-phrase tables (built
  from company_aliases.py), applies the same admission/dedup/
  materiality gates as Lane A
        ▼
src/data_access/daily_news/daily_news_backend.py
  get_editorial_story_repository()
        ▼
src/ui/components/editorial_coverage.py, src/ui/pages/daily_news.py
```

**Structurally separate, currently dormant mechanisms** (not counted
as "sources" — a different kind of pathway, described here for
completeness since the task asks to distinguish them explicitly):

- **Filing-candidate shadow adapters** (`dart_filing_candidate_
  adapter.py`, `edgar_filing_candidate_adapter.py`, `edinet_filing_
  candidate_adapter.py`) — surface an EDGAR/DART/EDINET filing as a
  Daily-News-shaped candidate, gated by three independent env flags
  (`EDGE_EDGAR_FILING_CANDIDATE_SHADOW_ENABLED`, `EDGE_DART_FILING_
  CANDIDATE_SHADOW_ENABLED`, `EDGE_EDINET_FILING_CANDIDATE_SHADOW_
  ENABLED`), **all three unset in this environment** (confirmed via
  `.env` read this session) — this pathway produces zero output today.
- **`source_candidates.py`'s `VALIDATED_SOURCE_CANDIDATES`** (5
  entries: Federal Register, NIST/CHIPS, JPX, BIS, USTR) — an
  explicitly inert, never-imported record of a completed manual
  validation pass (confirmed by the module's own docstring and its
  isolation tests: "NOT `RUNTIME_SOURCE_REGISTRY`... NOT imported by
  `daily_news_pipeline.py`, `scripts/daily_news_worker.py`, `daily_
  news_backend.py`, any UI page"). Dead weight for provenance/history
  only, never a live source.

### 1.2 Runtime-active source count

| Lane | Registry | Count | Polling |
|---|---|---|---|
| A — issuer-linked | `RUNTIME_SOURCE_REGISTRY` | **28** | Unconditional every worker tick (all `enabled=True`) |
| B — editorial | `EDITORIAL_SOURCE_REGISTRY` | **36** | Unconditional every worker tick (all `enabled=True`) |
| **Runtime-active total** | | **64** | |

**Non-runtime, separately counted:**

| Category | Registry | Count | Status |
|---|---|---|---|
| Gated market-news | `GATED_MARKET_NEWS_SOURCE_REGISTRY` | 1 (Light Reading) | Dormant — `EDGE_DAILY_NEWS_ENABLED_SOURCES` unset, empty allow-list |
| Gated JP/KR | `GATED_JP_KR_SOURCE_REGISTRY` | 3 (Japan Times Business, BusinessKorea ×2) | Dormant — `EDGE_DAILY_NEWS_ENABLED_SOURCES_JP_KR` unset, empty allow-list |
| Inert research record | `source_candidates.VALIDATED_SOURCE_CANDIDATES` | 5 | Never imported by any runtime path |
| **Total inert/non-runtime candidate records** | | **9** | |

One health-state caveat within the 28 "runtime-active" issuer-linked
sources: `meta-newsroom-rss` (about.fb.com) is `enabled=True` and
genuinely polled every tick (`PILOT_FEEDS`'s filter is `entry.enabled
and entry.format == RSS_ATOM`, not health-state-gated), but its own
`health_state` is `PENDING_REVIEW`, not `VERIFIED` — it was added as a
worker-context validation candidate for the separately-registered,
reportedly-403'd `meta-ir-rss`, and its own real fetch outcome under
the worker's live signature has never been confirmed in this session.
It counts toward the 28/64, but is flagged here as the one
runtime-active source whose health is genuinely unconfirmed, not
merely a formality.

### 1.3 Environment check performed this session (read-only)

`.env` was read (grep only, no secret values printed) to confirm the
two gated allow-lists' actual current state: `EDGE_DAILY_NEWS_ENABLED_
SOURCES` and `EDGE_DAILY_NEWS_ENABLED_SOURCES_JP_KR` are both **absent**
locally, and no `render.yaml` or equivalent infrastructure-as-code file
exists in this repository to check for a production override — this
document does not claim visibility into Render's own dashboard-managed
environment variables, and does not assume they match this local
environment. The three filing-candidate shadow-adapter flags
(`EDGE_EDGAR_FILING_CANDIDATE_SHADOW_ENABLED`, `EDGE_DART_FILING_
CANDIDATE_SHADOW_ENABLED`, `EDGE_EDINET_FILING_CANDIDATE_SHADOW_
ENABLED`) are likewise all absent locally.

### 1.4 Reconciliation against the prior 20-entry baseline

| Point in history | Issuer-linked lane size | What changed |
|---|---|---|
| Original pilot | 12 | `PILOT_SOURCE_REGISTRY` — the first 12 official IR/newsroom feeds |
| + Expansion batch 1 (2026-09-04) | 19 | +7 official IR/newsroom feeds |
| + Expansion batch 2 (2026-09-04) | **20** | +1 (`meta-newsroom-rss`, a worker-context validation candidate) — **this is the exact point matching the "prior 20-entry baseline"** |
| + Expansion batch 3 (2026-09-11) | 24 | +4 (Qualcomm, Corning, Synopsys, Cadence) |
| + Expansion batch 4 (2026-09-13) | 27 | +3 (Samsung, Murata, Microchip) |
| + Expansion batch 5 (2026-09-13) | **28** | +1 (HPE, via a `DISCOVERY_STUBS` entry) |

**Separately, layered on top afterward (2026-09-11 to 2026-09-13),
never part of the "20"**: `EDITORIAL_SOURCE_REGISTRY_V1` (12: 9 CNBC
sections + Korea Herald Business + Space Force + NIST) + `EDITORIAL_
SOURCE_REGISTRY_BATCH_2` (23) + `EDITORIAL_SOURCE_REGISTRY_BATCH_3` (1,
Data Center Dynamics) = **36**, an entirely separate issuer-agnostic
lane with its own pipeline (`editorial_pipeline.py`), never merged
into or counted alongside the issuer-linked lane.

**Every difference accounted for**: the growth from 20 → 28 in the
issuer-linked lane is 3 small, individually-documented batches (each
with its own live-verification notes already in `source_registry.py`,
including explicit "attempted and excluded" sub-lists for companies
with no working feed — Broadcom, Texas Instruments, Micron, Credo,
Microsoft, and 16 others named by ticker); the entirely-separate
36-source editorial lane's existence is the reason today's true total
(64) is more than 3x the "20" baseline, not a discrepancy or an
error — a new, deliberate, separately-designed feature ("Editorial
Daily News v1"), not an undocumented drift.

---

## 2. Full source tables

### 2.1 Lane A — issuer-linked (28), grouped by theme

Theme is derived from the linked issuer's own `TrackedCompany.themes`
(`tracked_companies.py`) — real, stored data, not inferred. HPE has no
Radar theme (Daily-News-only `DISCOVERY_STUBS` entry).

| Theme | Count | Sources (attribution — domain) |
|---|---|---|
| ai-buildout | 23 | NVIDIA (nvidianews.nvidia.com), Intel (www.intc.com), AMD (ir.amd.com), Bloom Energy (investor.bloomenergy.com), Rockwell Automation† (rockwell2023tf.q4web.com), Quanta Services (investors.quantaservices.com), nVent Electric (investors.nvent.com), Arista Networks (investors.arista.com), Cisco (newsroom.cisco.com), Amazon (ir.aboutamazon.com), Meta ×2 (investor.atmeta.com — verified; about.fb.com — pending_review), Oracle (investor.oracle.com), Applied Materials (ir.appliedmaterials.com), Lam Research (newsroom.lamresearch.com), KLA (ir.kla.com), Arm Holdings (newsroom.arm.com), Qualcomm (investor.qualcomm.com), Synopsys (investor.synopsys.com), Cadence (investor.cadence.com), Murata (www.murata.com), Microchip (www.microchip.com), SK Hynix‡, Samsung‡ |
| memory | 2 | SK Hynix‡ (news.skhynix.com), Samsung Electronics‡ (news.samsung.com) |
| photonics | 3 | Marvell Technology (investor.marvell.com), MaxLinear (investors.maxlinear.com), Corning (investor.corning.com) |
| humanoids | 1 | Rockwell Automation† (also ai-buildout, counted once above) |
| space | **0** | — |
| (no Radar theme) | 1 | Hewlett Packard Enterprise (investors.hpe.com) |

† Rockwell Automation carries no Radar `themes` beyond `humanoids` per
the registry read this session (it is not double-tagged
`ai-buildout`) — listed under ai-buildout above only because this
table groups by "AI Buildout–adjacent infrastructure" informally; the
stored `theme[0]` is `humanoids`. Corrected count: ai-buildout = 22
distinct sources + Rockwell's own separate humanoids tag. ‡ SK Hynix
and Samsung Electronics carry `("memory", "ai-buildout")` — counted in
both rows above, not double-counted in the 28 total.

All 28: `category` is `official_ir` (17) or `official_newsroom` (11);
`format` is `RSS_ATOM` for all 28 (the only format
`to_daily_news_feed_source()` supports); `language` is `English` for
all 28 (including the 2 Japan-jurisdiction and 2 Korea-jurisdiction
entries — each publishes an English-language feed, confirmed by the
original live-verification notes already in `source_registry.py`);
attribution/link behavior is uniform across the lane — headline,
publisher-provided excerpt, and direct link only, per
`summary_grounding.py`'s no-full-article-reproduction policy;
freshness behavior is IR/newsroom cadence (irregular, earnings-cycle-
driven, not daily); duplicate risk within this lane is low (each
source is issuer-exclusive by construction — `to_daily_news_feed_
source()` requires exactly one `issuer_name`) but cross-lane risk
against Lane B is real wherever a wire service (PR Newswire) or
general trade press (CNBC, TechCrunch) republishes or covers the same
press release — this is exactly what `dedup.is_duplicate_title()` and
`localization_dedup.py` exist to catch, per the Dashboard Signals
quality-fix work already merged in PR #57.

### 2.2 Lane B — editorial (36), grouped by category

| Category | Count | Sources |
|---|---|---|
| `independent_news` | 29 | CNBC ×9 (one publisher, 9 sections — top-news, business, finance, economy, technology, earnings, energy, politics-policy, asia), Korea Herald Business, TechCrunch, Ars Technica, The Verge, The Register ×2 (headlines + on-prem, one publisher), PR Newswire ×2 (general + financial-services, one wire service, not journalism), Semiconductor Engineering, IEEE Spectrum, Data Center Frontier, Tom's Hardware, Supply Chain Dive, Utility Dive, Japan Times, Yonhap News Agency, Korea Times, Korea IT Times, TheElec, Data Center Dynamics |
| `regulator` | 4 | SEC (www.sec.gov), Federal Reserve (www.federalreserve.gov), FTC (www.ftc.gov), Japan FSA (www.fsa.go.jp) |
| `government_policy` | 2 | U.S. Space Force (www.spaceforce.mil), NIST (www.nist.gov — restricted to a CHIPS/semiconductor-scoped allow-list in `editorial_pipeline.py`, general NIST science news excluded) |
| `exchange` | 1 | Japan Exchange Group / JPX (www.jpx.co.jp) |

**By jurisdiction**: United States 25, South Korea 5 (Korea Herald
Business, Yonhap, Korea Times, Korea IT Times, TheElec), Japan 3
(Japan Times, JPX, Japan FSA), United Kingdom 3 (The Register ×2, Data
Center Dynamics). **Distinct real-world publishers**: 26 (36 entries
collapse to 26 publishers once CNBC's 9 sections, The Register's 2
sections, and PR Newswire's 2 feeds are each counted once).

All 36: `language` is `English` for every entry (including all 5
Korea- and all 3 Japan-jurisdiction entries); `format` is `RSS_ATOM`
for all 36; freshness behavior varies sharply by publisher — CNBC/
TechCrunch/The Register/PR Newswire publish multiple times daily,
while `nist-news-rss`/`spaceforce-news-rss`/regulator feeds are
lower-frequency and event-driven; attribution/link behavior is
uniform (headline/excerpt/link only), except PR Newswire's own notes
explicitly flag it as "syndicated issuer press-release content the
wire service itself did not report or write, not independent
journalism" — a real attribution-accuracy nuance already captured in
its distinct `_WIRE_SERVICE_LICENSING_CLASSIFICATION` text.

**Known duplicate/translation risk, concretely**: PR Newswire syndicates
press releases that may *also* be the exact same release already
polled directly from an issuer's own official feed in Lane A (e.g. a
future NVIDIA or Amazon press release appearing on both `nvidia-
newsroom-rss` and `pr-newswire-general-rss`) — this is precisely the
cross-source duplicate case `daily_news_pipeline.select_canonical_
stories()` and `dedup.is_duplicate_title()` (keyed on
`(normalized_title, publisher)`) exist to catch, per the already-
merged Dashboard Signals quality fix (PR #57). CNBC Asia and Yonhap/
Korea Times/Korea IT Times/TheElec/Japan Times carry the highest
*localized-duplicate* risk (the same underlying story covered by a US
wire outlet and a regional outlet in different words) — the same
`_evaluate_localization_match()`/`_compare_using_cached_translation_
only()` mechanism from that same PR #57 work is the relevant existing
mitigation, not a new one this document proposes.

---

## 3. Quality and gap audit

### 3.1 Per-source-class assessment

**Official IR/newsroom (Lane A, 28)**: highest reliability and legal/
attribution suitability of any class in the registry — first-party,
issuer-owned, no syndication-rights ambiguity, and every entry's
`licensing_classification` states plainly "public press-release
content; headline, extractive excerpt, and direct link only." Signal
quality is high but narrow: an IR feed is optimized for earnings/
governance/financing disclosures, not the second-order supply-chain
signal this project's own coverage-audit work (see the three
untracked `COVERAGE_*`/`TIER1_*` design documents) is trying to
surface. **Distinct from EDGAR/EDINET/DART**: yes, materially — an IR
press release is often same-day or pre-filing (a product launch,
partnership announcement, or executive change rarely has a
corresponding same-day 8-K/6-K/DART report), and its prose gives
plain-language context a raw filing form never does. This is the
strongest, least-redundant class in the registry.

**Regulator/exchange/government (7: SEC, Federal Reserve, FTC, Japan
FSA, JPX, Space Force, NIST)**: very high reliability and legal
suitability (several are explicitly public-domain U.S. federal works,
correctly given their own distinct `_GOVERNMENT_LICENSING_
CLASSIFICATION`). Timeliness and thematic relevance are uneven by
design — NIST is scoped to a strict CHIPS/semiconductor allow-list
specifically because its raw feed is mostly irrelevant general science
news; Space Force and Fed/FTC/SEC are broad institutional feeds, only
a fraction of which will ever match a tracked company or theme under
`editorial_matching.py`'s fail-closed gate. **Distinct from EDGAR/
EDINET/DART**: partially — SEC's own press-release feed is genuinely
distinct from the EDGAR filing stream (rulemaking, enforcement,
speeches — not a duplicate of a company's own 10-K/8-K), but JPX
Market News has real thematic overlap with what EDINET's own
disclosure stream already covers for the same listed companies,
worth noting as the one source in this class with the least marginal
distinctness.

**Independent trade press (29 across 26 publishers)**: the most
heterogeneous class in signal quality. CNBC/TechCrunch/The Verge/Ars
Technica/The Register are broad, high-volume general/tech press —
useful for market context and cross-theme narrative, but carry the
highest consumer-retail and off-topic noise risk of any class (the
Asus RTX 5090 consumer-retail false-positive fixed in PR #57 was
exactly this kind of source). Semiconductor Engineering, IEEE
Spectrum, Data Center Frontier, Supply Chain Dive, Utility Dive, and
Data Center Dynamics are narrower trade press with materially higher
thematic precision for `ai-buildout` specifically (power/cooling,
supply-chain, data-center infrastructure) — these read as the
highest-value independent-news subset already in the registry.
**Distinct from EDGAR/EDINET/DART**: yes for all of these — none is a
regulatory filing summary; each adds either broad market narrative or
specialized trade-press analysis no filing provides.

**Wire service (PR Newswire, 2 feeds)**: reliable in the narrow sense
that it faithfully republishes real issuer press releases, but it is
explicitly *not* independent journalism (its own licensing text says
so) and carries the highest **direct** duplicate risk of any source in
the registry against Lane A's own official feeds — the mitigation
already exists (`select_canonical_stories()`), but this is the class
most likely to produce a same-story double-poll.

**Regional English-language press (Korea Herald Business, Yonhap,
Korea Times, Korea IT Times, TheElec, Japan Times, 6 total)**: solid,
credible regional coverage, correctly all-English (no translation
pathway needed for these 6 — they publish in English natively, unlike
the Japanese-language EDINET filing stream). Signal-quality and
timeliness are good; the real limitation is **coverage depth relative
to the size of the JP/KR-domiciled tracked-issuer population** — see
§3.3.

### 3.2 Localization/syndication duplication risk — summary

Three distinct risk shapes exist in the current 64-source registry,
each with its own already-built mitigation (none altered by this
document):
1. **Direct wire-service duplication** (PR Newswire vs. an issuer's
   own Lane A feed) — mitigated by `dedup.is_duplicate_title()`.
2. **Cross-language localized duplication** (a US-outlet English
   story and a JP/KR-outlet's own coverage of the same underlying
   event) — mitigated by `localization_dedup.py`'s cached-translation-
   only comparison (PR #57).
3. **Cross-lane duplication** (the same press release appearing in
   both Lane A's issuer feed and Lane B's CNBC/TechCrunch/PR Newswire
   coverage) — mitigated by `select_canonical_stories()`'s cross-
   source canonicalization, which both lanes' output flows through
   before persistence.

No new duplication shape was identified in this audit that lacks an
existing mitigation — the registry's real risk today is concentration
(how *much* of the 64-source volume is US-general-tech/wire-service,
diluting the JP/KR and thematically-narrow signal), not an unhandled
technical dedup gap.

### 3.3 Underserved themes and regions

| Theme | Issuer-linked sources | Dedicated trade press | Verdict |
|---|---|---|---|
| space | **0** | **0** | Most underserved theme in the registry — zero first-party and zero trade-press coverage despite 8 tracked space companies pre-Cohort-1, now 8 (Firefly/L3Harris not yet added) |
| humanoids | 1 (Rockwell Automation) | 0 | Second most underserved — one source for what is now 8 tracked humanoids companies (pre-Cohort-1) / 8 post |
| memory | 2 (SK Hynix, Samsung — both `memory, ai-buildout` dual-tagged) | 0 dedicated (TheElec covers Korea semiconductor broadly, not memory-specific) | Thin — Micron, Kioxia, Western Digital, SanDisk all have zero Daily News source (Micron was attempted and excluded — its feed's `http://` links fail the https-only gate) |
| photonics | 3 (Marvell, MaxLinear, Corning) | 0 dedicated | Moderate |
| ai-buildout | 22-23 | Several (Semiconductor Engineering, IEEE Spectrum, Data Center Frontier, Supply Chain Dive, Utility Dive, Data Center Dynamics) | Best-served theme by a wide margin |

**Region**: Japan and Korea combined contribute only 11 of 64 runtime-
active sources (2 JP + 2 KR issuer-linked; 3 JP + 5 KR editorial) —
17% of total volume against a tracked-issuer population where EDINET
(21 companies) + DART (12 companies) together are 29% of the full
115-company registry. The 4 gated-but-dormant JP/KR sources (§1.2)
would help if enabled, but even fully enabled they add no *issuer-
level* JP/KR coverage — all 4 are trade press, not company feeds.

**The Cohort 1 supply-chain issuers specifically**: confirmed
programmatically this session — **all 10** of Hanmi Semiconductor, HD
Hyundai Electric, Nabtesco, Harmonic Drive Systems, Yaskawa Electric,
GE Vernova, Digital Realty, Equinix, Firefly Aerospace, and L3Harris
Technologies have **zero** Daily News source of any kind today (no
Lane A feed, and no Lane B item would ever match them unless a
general-press story happens to name them, since `editorial_matching.
py`'s company matching is itself alias-based, not keyword-based). This
is the most concrete, actionable gap this audit found — directly
addressed by §4.

### 3.4 Retain / redesign / limit / remove — assessment only, no change made

- **Retain as-is**: all 28 Lane A sources, all 12 `EDITORIAL_SOURCE_
  REGISTRY_V1` sources, the 4 regulator/exchange/government sources,
  the 6 narrow-trade-press sources (Semiconductor Engineering, IEEE
  Spectrum, Data Center Frontier, Supply Chain Dive, Utility Dive,
  Data Center Dynamics), the 6 regional JP/KR press sources. No
  quality or legal concern identified with any of these 46.
- **Limit (a candidate for future scope-narrowing, not urgent)**: the
  9-section CNBC feed and PR Newswire's 2 feeds — highest cross-
  source duplicate-collision surface area of any sources in the
  registry (§3.1/§3.2). Not a defect; a future admission-policy
  refinement could consider capping CNBC to a smaller thematically-
  relevant subset of its 9 sections, but this document does not
  propose which, or whether the reduction is worth the coverage loss.
- **Redesign candidate**: `meta-newsroom-rss` — the one source whose
  own health state (`PENDING_REVIEW`) has never been resolved against
  a real worker-context fetch; either confirm it in worker logs and
  promote to `VERIFIED`, or retire it, rather than leaving it
  indefinitely unresolved.
- **Consider for removal**: none. No source in the current 64 was
  found to carry a legal, attribution, or reliability problem serious
  enough to warrant removal — the registry's own admission gates
  (`validate_source_entry`, the social-media-domain denylist, the
  three-name exclusion list) already screen out the highest-risk
  source shapes before they can be added at all.

---

## 4. Cohort 1 recommendations

**6 candidates recommended** (within the "maximum of 10" ceiling,
deliberately not padded to the cap — each fills a specific, named gap
identified in §3.3, none is a generic large-media addition). Every
candidate below was independently fetched and confirmed live this
session (real HTTP response, parseable RSS/Atom, real dated items,
on-domain per-item links) — never guessed from a URL pattern. A
companion list of attempted-and-excluded candidates follows, per this
project's own established discipline of never silently dropping a
checked-but-failed candidate.

### 4.1 Equinix, Inc. — official IR RSS

- **Source owner**: Equinix, Inc. (first-party).
- **Domain/feed URL**: `https://investor.equinix.com/news-events/press-releases/rss`.
- **Source class**: `official_ir`.
- **Language**: English.
- **Thematic/company coverage**: `ai-buildout` (Cohort 1 addition, datacenter-infrastructure REIT).
- **Expected event types**: earnings, executive changes, major
  facility/interconnection announcements, M&A (a live item during
  verification was a completed acquisition, "CPP Investments and
  Equinix Complete atNorth Acquisition").
- **Why it adds non-filing value**: Equinix files as a domestic SEC
  filer (10-Q/8-K already scan-eligible per the Cohort 1 registry
  work), but its press releases carry plain-language operational
  detail (specific facility/customer/interconnection news) a filing
  rarely states directly.
- **Duplication risk/mitigation**: low — no other Lane A or Lane B
  source currently names Equinix by alias; standard cross-source dedup
  applies if that changes.
- **Licensing/attribution notes**: standard official press-release
  content, matches `_PILOT_LICENSING_CLASSIFICATION` exactly.
- **Existing adapter compatibility**: full — `RSS_ATOM`/`official_ir`,
  identical shape to every existing Lane A entry, no new code path.
- **Validation method**: live `WebFetch` this session — HTTP-reachable,
  valid RSS 2.0, channel title "Equinix, Inc. (EQIX) Press Releases",
  3 most recent items dated 2026-09-02 to 2026-09-10, all links
  on-domain (`investor.equinix.com`).
- **Status**: **GO.**

### 4.2 L3Harris Technologies, Inc. — official newsroom RSS

- **Source owner**: L3Harris Technologies, Inc. (first-party).
- **Domain/feed URL**: `https://www.l3harris.com/feeds/newsroom/rss.xml`.
- **Source class**: `official_newsroom`.
- **Language**: English.
- **Thematic/company coverage**: `space` (Cohort 1 addition — the
  first-ever issuer-linked `space`-theme Daily News source; §3.3
  found zero prior).
- **Expected event types**: program milestones, contract awards,
  editorial features (the feed mixes press releases and editorial
  content — e.g. "Modernizing the KC-135" alongside "L3Harris Advances
  Production of Proximity Sensors").
- **Why it adds non-filing value**: L3Harris's space-segment
  disclosures are a minority of its much larger defense-electronics
  10-Q/8-K filings (per the Cohort 1 registry design's own multi-
  segment caveat) — the newsroom feed is the only channel that names
  space-specific program activity directly, without needing to parse
  it out of a mixed filing.
- **Duplication risk/mitigation**: low — no existing source names
  L3Harris.
- **Licensing/attribution notes**: standard official-newsroom content,
  matches `_PILOT_LICENSING_CLASSIFICATION`.
- **Existing adapter compatibility**: full, no new code path.
- **Validation method**: live `WebFetch` this session — valid RSS 2.0,
  channel title "L3Harris Technologies," 3 most recent items dated
  2026-09-14 to 2026-09-15, all links on-domain (`www.l3harris.com`).
- **Status**: **GO.**

### 4.3 Firefly Aerospace Inc. — official news RSS

- **Source owner**: Firefly Aerospace Inc. (first-party).
- **Domain/feed URL**: `https://fireflyspace.com/feed/`.
- **Source class**: `official_newsroom`.
- **Language**: English.
- **Thematic/company coverage**: `space` (Cohort 1 addition — second
  new issuer-linked `space` source, pairing with L3Harris and, per
  the coverage audit's own comparison-signal design, readable
  alongside the already-tracked Rocket Lab for launch-cadence
  comparison).
- **Expected event types**: launch manifest/contract announcements
  (a live item was a new multi-launch agreement with SSC Space),
  mission partnerships (Zeno Power radioisotope-technology
  collaboration), government contracts (a NASA spacecraft-processing
  contract).
- **Why it adds non-filing value**: Firefly IPO'd 2025-08-07 and is a
  standard domestic SEC filer, but launch-manifest and partnership
  news of exactly this kind is disclosed via press release well before
  (or entirely outside of) any 8-K materiality threshold.
- **Duplication risk/mitigation**: low.
- **Licensing/attribution notes**: standard official content, matches
  `_PILOT_LICENSING_CLASSIFICATION`.
- **Existing adapter compatibility**: full, no new code path.
- **Validation method**: live `WebFetch` this session — valid feed,
  channel title "Firefly Aerospace," 3 most recent items dated
  2026-08-19 to 2026-09-09, all links on-domain (`fireflyspace.com`).
- **Status**: **GO.**

### 4.4 YASKAWA Electric Corporation — official newsroom RSS (Japan)

- **Source owner**: YASKAWA Electric Corporation (first-party).
- **Domain/feed URL**: `https://www.yaskawa-global.com/feed/`.
- **Source class**: `official_newsroom`.
- **Language**: **English** (recommended registry value) — the feed's
  own `<channel><language>` metadata reports `ja`, but every item
  title/summary observed in this session's live fetch (3 sampled) was
  plain English, on Yaskawa's own English-language global site. Per
  `source_registry.py`'s own field discipline ("a human-verified fact
  about the source, never derived from text"), the curated `language`
  value should reflect the site's real content, not a possibly
  mislabeled feed-metadata field — flagged explicitly here as a real
  discrepancy any future implementer must resolve deliberately, not
  copy blindly from the feed's own `<language>` tag.
- **Thematic/company coverage**: `humanoids` (Cohort 1 addition —
  second issuer-linked `humanoids` source after Rockwell Automation;
  §3.3 found only one prior).
- **Expected event types**: new-product announcements (a live item was
  a new collaborative-robot launch, MOTOMAN-HC12), IR/earnings-date
  notices, personnel changes.
- **Why it adds non-filing value**: Yaskawa is EDINET-sourced (21-
  company cohort, Cohort 1 registry work) with English-language
  product/program news well ahead of what an annual securities report
  or extraordinary report would surface.
- **Duplication risk/mitigation**: low.
- **Licensing/attribution notes**: standard official-newsroom content.
- **Existing adapter compatibility**: full, no new code path — the
  registry's EDINET/DART/EDGAR source split is unrelated to Daily
  News's own RSS-only Lane A mechanism, which has no dependency on
  which filing adapter an issuer uses.
- **Validation method**: live `WebFetch` this session — valid RSS 2.0,
  3 most recent items dated 2026-08-17 to 2026-09-13, all links
  on-domain (`www.yaskawa-global.com`). The `language` discrepancy
  above was independently observed, not assumed.
- **Status**: **GO**, with the `language="English"` correction noted
  explicit for implementation.

### 4.5 SpaceNews — independent trade press

- **Source owner**: SpaceNews, Inc. (independent trade publisher, not
  first-party to any tracked issuer).
- **Domain/feed URL**: `https://spacenews.com/feed/`.
- **Source class**: `independent_news`.
- **Language**: English.
- **Thematic/company coverage**: `space` — issuer-agnostic (Lane B).
  Fills a real, currently-empty category: **zero** dedicated
  space-industry trade press exists in `EDITORIAL_SOURCE_REGISTRY`
  today (the closest existing sources, TechCrunch/Ars Technica/The
  Verge, are general tech press with only incidental space coverage).
- **Expected event types**: launch-industry analysis, satellite-
  constellation news, on-orbit-servicing coverage, national-security-
  space reporting — directly relevant to every tracked `space` issuer
  (Rocket Lab, AST SpaceMobile, Redwire, Planet Labs, Intuitive
  Machines, ispace, Firefly, L3Harris) and several §2.3-of-the-audit
  private-ecosystem entities.
- **Why it adds non-filing value**: dedicated trade-press analysis and
  cross-company comparison (e.g. its own live item on "on-orbit
  servicing" industry trends) no single issuer's own press release or
  filing would produce.
- **Duplication risk/mitigation**: low-moderate — some overlap
  possible with individual issuers' own Lane A feeds (once Firefly/
  L3Harris are added) on the same underlying event; mitigated by the
  existing cross-source `select_canonical_stories()` path.
- **Licensing/attribution notes**: independent journalism, matches
  `_EDITORIAL_LICENSING_CLASSIFICATION` exactly (headline, excerpt,
  link only).
- **Existing adapter compatibility**: full — `RSS_ATOM`/
  `independent_news`, identical shape to every existing Lane B entry;
  would require `allowlisted=True` per the existing `INDEPENDENT_NEWS`
  admission rule, same as every other Lane B source.
- **Validation method**: live `WebFetch` this session — valid RSS 2.0,
  3 most recent items all dated 2026-09-14/2026-09-15, all links
  on-domain (`spacenews.com`).
- **Status**: **GO.**

### 4.6 The Robot Report — independent trade press

- **Source owner**: WTWH Media LLC, d/b/a The Robot Report (independent
  trade publisher).
- **Domain/feed URL**: `https://www.therobotreport.com/feed/`.
- **Source class**: `independent_news`.
- **Language**: English.
- **Thematic/company coverage**: `humanoids` — issuer-agnostic (Lane
  B). Fills the same kind of currently-empty category as SpaceNews:
  **zero** dedicated robotics trade press exists in `EDITORIAL_SOURCE_
  REGISTRY` today.
- **Expected event types**: humanoid-robot product news (a live item
  was Agility Robotics' Digit 5 hardware upgrade — directly relevant
  to the private-ecosystem-watchlist entity named in the thematic
  coverage audit), industrial-automation and RaaS (robotics-as-a-
  service) business-model analysis, standards/interoperability news
  (a live item covered an ISO 21423 reference implementation).
- **Why it adds non-filing value**: dedicated coverage of exactly the
  precision-reducer/servo-motor/humanoid-OEM supply chain the Cohort 1
  registry additions (Nabtesco, Harmonic Drive Systems, Yaskawa) and
  the private-ecosystem watchlist (Boston Dynamics, Figure AI,
  Apptronik, Agility Robotics) target — no filing or IR feed covers
  this cross-company industry narrative.
- **Duplication risk/mitigation**: low — thematically narrow enough
  that overlap with Yaskawa's own newsroom feed (§4.4) would be rare
  and, if it occurs, already caught by cross-source dedup.
- **Licensing/attribution notes**: independent journalism, matches
  `_EDITORIAL_LICENSING_CLASSIFICATION`.
- **Existing adapter compatibility**: full, same as SpaceNews above;
  requires `allowlisted=True`.
- **Validation method**: live `WebFetch` this session — valid RSS 2.0,
  3 most recent items all dated 2026-09-14/2026-09-15, all links
  on-domain (`www.therobotreport.com`).
- **Status**: **GO.**

### 4.7 Attempted and excluded this session — not silently dropped

| Candidate | What was tried | Why excluded | Disposition |
|---|---|---|---|
| GE Vernova Inc. (official IR/newsroom) | `gevernova.com/news`, `gevernova.com/investors`, `gevernova.com/investors/news` | No `<link rel=alternate>` RSS tag found on any page checked; no working feed confirmed | **HOLD** — revisit with a more targeted search (Q4-platform-style guess did not resolve; a real page-source `<link>` scan by a future session may still find one) |
| Digital Realty Trust, Inc. (official IR) | `investors.digitalrealty.com` (DNS failure — wrong subdomain), `www.digitalrealty.com/about/newsroom` (no feed found, but confirmed correct IR domain is `investor.digitalrealty.com`), `investor.digitalrealty.com/rss/pressrelease.aspx` (Q4-platform pattern guess — request timed out, inconclusive) | No feed independently confirmed live this session | **HOLD** — the Q4-pattern URL is a plausible next attempt (correct IR domain now confirmed) but was not itself verified; must not be added without a real fetch confirming it |
| Nabtesco Corporation (official news, Japan) | `www.nabtesco.com/en/news/` | No RSS/Atom feed found on the page | **HOLD** — same "no working feed found" disposition as several companies already documented in `source_registry.py`'s own batch-3 exclusion notes (e.g. Broadcom, TI); revisit only if the company's own site later adds one |
| Harmonic Drive Systems Inc. (official news, Japan) | `www.hds.co.jp/english/news/` | No RSS/Atom feed found; domain confirmed correct (`hds.co.jp`) but no feed mechanism exists | **HOLD** |
| Hanmi Semiconductor Co., Ltd. (official news, Korea) | `www.hanmisemi.com/eng/rnd/news.asp` (guessed path) | HTTP 404 — guessed URL was wrong, and no correct URL was independently found this session | **HOLD** — needs a fresh, non-guessed domain/page discovery pass before any URL is proposed again |
| HD Hyundai Electric Co., Ltd. (official news, Korea) | `www.hyundai-electric.com/en/news/newsList.hw` (guessed path) | HTTP 404 — same disposition as Hanmi Semiconductor | **HOLD** |
| Korea Exchange / KRX (regulator/exchange, Korea) | `global.krx.co.kr/contents/GLB/05/0505/0505010000/GLB0505010000.jsp` (guessed path) | HTTP 404 | **HOLD** — no working English-language KRX feed confirmed; Korea's regulator/exchange gap (§3.3) remains open |

None of these 7 is proposed for addition. Each is named explicitly,
with what was tried and why it failed, so a future session does not
repeat the same failed guess — matching this project's own established
"attempted and excluded, never silently dropped" discipline (the same
pattern already visible in `source_registry.py`'s own batch-3/batch-2-
editorial-lane comments for Micron, Credo, Microsoft, Bank of Japan,
Yonhap's earlier attempt, and 16 named EDGAR companies).

---

## 5. Onboarding safety

### 5.1 Acceptance checklist — required before any source becomes runtime-active

Every item below must be independently confirmed (never assumed) before a
`DailyNewsSourceEntry` moves from `PENDING_REVIEW`/proposed to
`enabled=True` in a real registry batch:

1. **URL/feed validity** — a real HTTP fetch (not a guessed pattern)
   returns a parseable RSS 2.0 or Atom document; §4's 6 candidates
   already satisfy this.
2. **Content ownership** — the feed's own domain matches the claimed
   `attribution_label`/`issuer_name` (for Lane A) or is a genuine,
   named independent publisher (for Lane B) — never an aggregator
   presented as if it were the original source.
3. **Attribution** — a `licensing_classification` string accurately
   describing the content's real legal character (official press
   release vs. independent journalism vs. wire-service syndication
   vs. public-domain government work) — reusing one of the four
   already-established classification texts where it genuinely
   applies, never inventing vague or overbroad language.
4. **Cadence** — at least 3 real dated items observed, confirming the
   feed is not empty or single-entry.
5. **Freshness** — the newest observed item is recent enough that the
   source will not appear as permanently stale under the existing
   7-day freshness window (per the batch-3 Microsoft exclusion
   precedent: a feed whose newest item is already a year old at
   verification time must not be added as though it were live).
6. **Translation-language declaration** — an explicit, human-verified
   `language` value reflecting the feed's *actual observed content*,
   not blindly copied from the feed's own `<language>` metadata tag
   (§4.4's Yaskawa finding is the concrete example this checklist item
   exists to catch).
7. **Duplicate behavior** — an explicit note on which existing lane/
   source(s) this candidate could plausibly duplicate, and confirmation
   that the existing `dedup.is_duplicate_title()` / `localization_
   dedup.py` / `select_canonical_stories()` mechanisms already cover
   that case (or, if not, that gap must be resolved first — not
   discovered after the source is already live).
8. **Editorial-admission compatibility** — for a Lane B candidate,
   confirmation that `editorial_matching.py`'s existing alias/compound-
   phrase matching can actually resolve the source's items to a real
   tracked company or approved theme phrase (a source that would never
   produce a single matching item under the existing fail-closed gate
   is not useful to add, even if technically valid).
9. **Failure/fallback handling** — confirmation the source's own fetch
   failure mode (HTTP error, malformed XML, empty response) is caught
   by the existing per-source isolation already built into `daily_
   news_pipeline.run_discovery()`/`editorial_pipeline.run_editorial_
   discovery()` (both already isolate one feed's fetch failure from
   every other feed, per `scripts/daily_news_worker.py`'s own
   docstring) — never a new, source-specific error-handling path.

### 5.2 Exact files a later implementation would need to modify

| File | Change |
|---|---|
| `src/data_access/daily_news/source_registry.py` | Append a new `EXPANSION_BATCH_6_SOURCE_REGISTRY` tuple (the 4 Lane A candidates: Equinix, L3Harris, Firefly Aerospace, Yaskawa) to `RUNTIME_SOURCE_REGISTRY`'s assembly; append a new `EDITORIAL_SOURCE_REGISTRY_BATCH_4` tuple (the 2 Lane B candidates: SpaceNews, The Robot Report) to `EDITORIAL_SOURCE_REGISTRY`'s assembly. **The only file that changes the actual source list.** |
| `tests/test_daily_news_source_registry.py` | Update every exact-count assertion (`len(RUNTIME_SOURCE_REGISTRY) == 28` → `32`; `len(feed_registry.PILOT_FEEDS) == 28` → `32`; `len(EDITORIAL_SOURCE_REGISTRY) == 36` → `38`) plus add per-entry identity/field assertions for the 6 new sources, matching this file's own established per-batch pattern. |
| `tests/test_daily_news_worker.py` | The `sources_polled={len(EDITORIAL_SOURCE_REGISTRY)}` log-line assertions (multiple, e.g. lines 924/1205/1237/1281/1315) would need no code change (they already compute from the live constant) but should be re-run to confirm the new count flows through correctly. |
| `tests/test_daily_news_pipeline.py`, `tests/test_daily_news_backend.py` (if any Lane-A-count-sensitive fixture exists) | Re-run only — no expected code change unless a fixture hardcodes the 28-count. |
| `tests/test_editorial_matching.py` | No change expected — SpaceNews/The Robot Report use the existing alias/compound-phrase matching mechanism unchanged; re-run to confirm no regression. |

**No change to**: `src/data_access/edgar/*`, `src/data_access/edinet/*`,
`src/data_access/dart/*` (source-adapter/filing-rule files — entirely
unrelated to Daily News's own RSS mechanism), `src/config/tracked_
companies.py` (the 4 Lane A candidates' issuer identities already
exist there from the Tier 1 Cohort 1 merge — no registry edit needed),
`src/config/issuer_registry.py` (no new `DISCOVERY_STUBS` entry needed
— all 4 Lane A candidates are already real `TrackedCompany` entries,
unlike HPE/Quanta/nVent/Arista/Cisco which needed the stub path),
`src/data_access/daily_news/editorial_matching.py`,
`src/data_access/daily_news/dedup.py`,
`src/data_access/daily_news/localization_dedup.py`, `src/data_access/
daily_news/canonical_url.py` — none of these require a code change for
any of the 6 recommended candidates, since all 6 fit the existing
`RSS_ATOM`/`official_ir`/`official_newsroom`/`independent_news` shapes
exactly.

### 5.3 Validation fixtures needed for a later implementation

| Fixture | Proves |
|---|---|
| `equinix_ir_rss_publishes_real_story` | Lane A ingestion works end-to-end for the new entry (fetch → validate → admit → persist) |
| `l3harris_newsroom_rss_publishes_real_story` | Same, for a mixed editorial/press-release feed shape |
| `firefly_aerospace_feed_publishes_real_story` | Same |
| `yaskawa_newsroom_rss_language_declared_english_not_feed_metadata` | The §4.4 language-declaration finding is enforced, not silently reverted to the feed's own (misleading) `ja` tag |
| `spacenews_independent_news_requires_allowlisting` | A Lane B `INDEPENDENT_NEWS` entry without `allowlisted=True` fails `validate_source_entry()` — the existing gate, exercised against this specific new entry |
| `spacenews_matches_tracked_space_companies` | `editorial_matching.py`'s alias table actually resolves a sample SpaceNews headline naming Rocket Lab/Firefly/L3Harris to the correct tracked company |
| `robot_report_matches_tracked_humanoids_companies` | Same, for a sample headline naming Yaskawa/Nabtesco/Rockwell/FANUC |
| `source_failure_isolated_equinix_down_does_not_block_other_feeds` | A simulated fetch failure for one new source does not interrupt polling of any other Lane A/B source — the existing per-source isolation, exercised against a new entry rather than assumed to generalize |
| `stale_entry_beyond_freshness_window_does_not_publish` | A fixture item dated outside the 7-day freshness window from any of the 6 new sources is correctly excluded — reusing the existing freshness gate, not a new one |
| `consumer_retail_noise_suppressed_for_robot_report` | A synthetic Robot Report item matching the existing consumer-retail suppression phrase list (PR #57) is correctly suppressed — proves the general-purpose gate applies to a new trade-press source, not just the fixtures it was originally built against |
| `localized_duplicate_yaskawa_newsroom_vs_robot_report` | A synthetic same-event item appearing in both Yaskawa's own newsroom feed and The Robot Report is correctly canonicalized to one story, not two |
| `translated_non_english_item_never_auto_translates_at_render_time` | Reconfirms the existing render-time-translation-purity invariant (PR #57) holds for the new sources too — no new source introduces a live-translation code path |
| `primary_source_materiality_ge_vernova_hold_not_silently_added` | A negative fixture: confirms `GE Vernova Inc.` has **no** Lane A entry today (guards against a future contributor silently adding a guessed, unverified URL for a HOLD candidate) |

---

## 6. Path to beta

### 6.1 Reframing — quantity target already met; quality is the real gate

As established in the headline finding, **the raw runtime-active count
(64) already exceeds a 25-source minimum by more than 2.5x.** The
staged plan below is therefore **not** "grow from ~20 to 25" — that
milestone was passed months of commit history ago, before this
session even began, and largely by a lane (Editorial, 36 sources) this
task's own framing did not have visibility into. The real beta-
readiness question is **coverage completeness and quality**, not
count:

- Does every high-priority tracked issuer (particularly the 10 newly-
  added Cohort 1 supply-chain companies) have at least one non-filing
  source?
- Does every one of the 5 tracked themes have both issuer-level and
  trade-press coverage, not just `ai-buildout`?
- Are known duplicate/localization/consumer-retail risks proven
  handled for every new source added, not assumed to generalize?

### 6.2 Staged plan

**Today (baseline)**: 64 runtime-active sources (28 Lane A + 36 Lane
B). Theme coverage: `ai-buildout` strong, `photonics` moderate,
`memory`/`humanoids` thin, `space` empty. 10 of 115 tracked companies'
worth of brand-new supply-chain coverage (Cohort 1 registry, merged
PR #59) has zero Daily News presence.

**Cohort 1 (this document, §4 — 6 sources, GO)**: closes the two most
acute, concretely-identified gaps — (a) 4 of the 10 newly-added
supply-chain issuers gain a first-party source (Equinix, L3Harris,
Firefly Aerospace, Yaskawa Electric), and (b) `space` and `humanoids`
each gain their first dedicated trade-press source (SpaceNews, The
Robot Report), where previously each had zero. Resulting count: 70
runtime-active (32 Lane A + 38 Lane B). Success criteria for treating
Cohort 1 as complete: all 6 sources independently live-verified (done,
this session), zero net-new duplicate-detection gap identified (done,
§3.2), the §5.3 fixture set implemented and green, and the 6 sources'
first real published stories manually spot-checked against the §5.1
checklist once actually deployed.

**Cohort 2 (proposed scope, not designed in detail here — a future,
separately-approved document)**:
1. **Resolve the 6 HOLD candidates from §4.7** — GE Vernova, Digital
   Realty (retry the Q4-pattern URL with a real fetch), Nabtesco,
   Harmonic Drive Systems, Hanmi Semiconductor, HD Hyundai Electric —
   each needs a fresh, non-guessed discovery pass, not a repeat of
   this session's failed attempts.
2. **Close the `memory` theme's trade-press gap** — zero dedicated
   memory/semiconductor-manufacturing trade press exists in either
   lane today; a candidate was not identified this session (no URL
   guessed to avoid repeating the discipline violation this document
   otherwise avoided) — flagged as a research lead for Cohort 2.
3. **Resolve `meta-newsroom-rss`'s `PENDING_REVIEW` health state**
   (§3.4) — either confirm it via real worker logs and promote to
   `VERIFIED`, or retire it.
4. **Decide on the 4 currently-gated sources** (Light Reading, Japan
   Times Business, BusinessKorea ×2) — either populate the env-var
   allow-lists to activate them (a configuration decision, not a code
   change) or leave them deliberately dormant with a documented
   reason; today they are simply unresolved, not a deliberate choice.
5. **Revisit CNBC/PR Newswire scope** (§3.4's "limit" candidate) once
   real production duplicate-rate data exists to inform whether a
   narrower CNBC section set would reduce noise without losing real
   coverage.

### 6.3 Success criteria for beta publication (Daily News specifically)

Not "N sources," but:
- Every one of the 5 themes has at least 2 issuer-linked sources and
  at least 1 dedicated trade-press source (today: `ai-buildout` and
  `photonics` pass; `memory`, `humanoids`, `space` do not — Cohort 1
  brings `humanoids` and `space` to exactly 1 trade-press source each,
  still short of the 2-issuer-source bar for `space` specifically,
  which Cohort 1 brings from 0 to 2 — Firefly + L3Harris — clearing
  that bar).
- Every newly-tracked supply-chain issuer cohort (Cohort 1's 10
  companies) has a documented Daily News coverage decision — GO
  (source added), HOLD (attempted, specific reason recorded), or
  explicitly deferred — never silently absent with no record, which
  was this audit's own starting condition before today.
- The §5.3 fixture set passes in full, including the negative fixtures
  (no HOLD candidate silently added, no auto-translation at render
  time, consumer-retail suppression generalizes to new sources).
- No open, unresolved `PENDING_REVIEW` health-state source remains
  without an explicit plan to resolve it (today: `meta-newsroom-rss`).

---

## 7. Explicit non-goals / confirmation

- No file under `src/`, no test, no runtime source registry, feed
  registry, configuration, worker, database, cache, or production data
  was modified.
- No discovery run, network scan (beyond the read-only candidate-
  verification fetches described in §4, none of which wrote to any
  repository file), cache write, staging, commit, push, PR, merge, or
  deployment occurred.
- The 8 pre-existing untracked planning artifacts (`design/COVERAGE_
  EXPANSION_CANDIDATES_TIER1_2026_09_15.md`, `design/COVERAGE_
  EXPANSION_FIRST10_REGISTRY_READINESS_2026_09_15.md`, `design/
  COVERAGE_TAXONOMY_AUDIT_2026_09_15.md`, `design/RESEARCH_THESES_
  ACTIVE_DORMANT.md`, `design/THEMATIC_COVERAGE_UNIVERSE_AUDIT_2026_
  09_15.md`, `design/TIER1_COHORT1_REGISTRY_READINESS_2026_09_15.md`,
  `design/TIER1_COHORT1_TRACKED_COMPANIES_IMPLEMENTATION_DESIGN_2026_
  09_15.md`, `tests/test_radar_status.py`) were treated as read-only
  inputs, none modified.
- No relationship-graph, schema, or database change is proposed or
  implied by §5 beyond the exact file list stated there, none of which
  was touched by this document itself.

## Files read / created

**Read this session (all read-only)**: `src/data_access/daily_news/
source_registry.py` (full file, 1764 lines), `src/data_access/
daily_news/feed_registry.py`, `src/data_access/daily_news/source_
candidates.py`, `src/config/settings.py` (the two gated-source
env-var fields), `src/data_access/daily_news/market_news_sources.py`
and `jp_kr_sources.py` (grepped for wiring), `scripts/daily_news_
worker.py` (grepped for the two-lane invocation), `.env` (grepped for
the two gated allow-lists and three shadow-adapter flags, no secret
values printed), `tests/test_daily_news_source_registry.py` (grepped
for historical exact-count assertions), `git log` (Daily-News-related
commit history for the §1.4 reconciliation). Live external
verification this session (read-only web fetches, nothing written to
any repository file): 13 candidate URLs checked across GE Vernova,
Digital Realty, Equinix, Firefly Aerospace, L3Harris, Nabtesco,
Harmonic Drive Systems, Yaskawa Electric, Hanmi Semiconductor, HD
Hyundai Electric, KRX, SpaceNews, and The Robot Report.

**Not touched**: all 8 pre-existing untracked artifacts (listed in §7).

**Created**: `design/DAILY_NEWS_SOURCE_CENSUS_AND_COHORT1_DESIGN_2026_
09_15.md` (this document — untracked, not staged, not committed).
