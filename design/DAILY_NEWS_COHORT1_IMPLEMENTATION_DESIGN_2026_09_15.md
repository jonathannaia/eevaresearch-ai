# Daily News Cohort 1 — Implementation Design (2026-09-15)

Read-only implementation design. No file under `src/`, no test, no
runtime source registry, feed registry, configuration, worker,
database, cache, or production data was modified to produce this
document. Starts from `origin/main` @ `b39ac24` (post PR #59;
confirmed via `git log`/`git status` this session — working tree clean
except the 9 pre-existing untracked artifacts, now 10 including this
document's own immediate predecessor). Builds directly on `design/
DAILY_NEWS_SOURCE_CENSUS_AND_COHORT1_DESIGN_2026_09_15.md` (the
approved census/candidate-selection work — treated here as a read-only
research input, not re-derived) and reuses facts from `design/SIGNALS_
ADMISSION_MATERIALITY_CALIBRATION_2026_09_15.md` (the admission/
materiality rule audit) wherever a candidate's own editorial-admission
risk maps onto a fixture already analyzed there.

Scopes exactly the 6 GO sources from the census document: **Equinix
issuer newsroom/IR, L3Harris Technologies issuer newsroom/IR, Firefly
Aerospace issuer newsroom/IR, Yaskawa Electric issuer newsroom/IR,
SpaceNews, The Robot Report.** The 7 HOLD candidates (GE Vernova,
Digital Realty, Nabtesco, Harmonic Drive Systems, Hanmi Semiconductor,
HD Hyundai Electric, KRX) and the 4 dormant env-gated sources (Light
Reading, Japan Times Business, BusinessKorea ×2) are explicitly out of
scope — see §4.3.

---

## 1. Per-source design

Each source is placed into one of the two existing, structurally
separate lanes traced in the census document: **Lane A** (issuer-
linked, `RUNTIME_SOURCE_REGISTRY`, `daily_news_pipeline.py`, no
admission gate — every item tiered, never rejected) or **Lane B**
(editorial/issuer-agnostic, `EDITORIAL_SOURCE_REGISTRY`, `editorial_
pipeline.py`, the full precision-first admission gate applies).

### 1.1 Equinix, Inc. — official IR (Lane A)

| Field | Value |
|---|---|
| Canonical source name | Equinix, Inc. investor relations press releases |
| Owner | Equinix, Inc. (first-party) |
| Domain | `investor.equinix.com` |
| Homepage | `https://investor.equinix.com/news-events/press-releases` |
| Feed URL | `https://investor.equinix.com/news-events/press-releases/rss` |
| Source class | `SourceCategory.OFFICIAL_IR` |
| Format | `SourceFormat.RSS_ATOM` (RSS 2.0, confirmed live) |
| Language | English |
| Jurisdiction | United States |

**Registry/model fields required to activate**: a new `DailyNewsSourceEntry`
(`source_registry.py`) — `source_id="equinix-ir-rss"`, `category=
OFFICIAL_IR`, `format=RSS_ATOM`, `canonical_url` as above, `domains=
("investor.equinix.com",)`, `jurisdiction="United States"`, `enabled=
True`, `health_state=VERIFIED`, `attribution_label="Equinix, Inc."`,
`licensing_classification=_PILOT_LICENSING_CLASSIFICATION` (reused
verbatim — standard official press-release content, no new licensing
text needed), `priority=1`, `issuer_name="Equinix, Inc."`, `last_
verified_at="2026-09-15"`. No `DISCOVERY_STUBS` entry needed —
Equinix is already a real `TrackedCompany` (merged PR #59). No
`feed_registry.py` code change — `PILOT_FEEDS` derives automatically
from `RUNTIME_SOURCE_REGISTRY`.

**Thematic tags / covered issuers / event types**: `ai-buildout`
(Equinix's own `TrackedCompany.themes`, confirmed via the live
registry read in the census document). Covers exactly one issuer,
Equinix itself. Expected event types (confirmed live, census
document): executive appointments, investor-conference media alerts,
M&A completions (the live-verified sample included "Equinix Names DD
Dasgupta as Chief Marketing Officer" and "CPP Investments and Equinix
Complete atNorth Acquisition").

**Freshness expectation**: Lane A's 7-day (`_FRESHNESS_WINDOW_SECONDS
= 7 * 24 * 3600`, `daily_news_pipeline.py`) window, 5-item-per-source
cap. IR cadence is irregular/event-driven, not daily — 3 items
observed within an 8-day span during live verification, consistent
with this window.

**Attribution behavior**: headline, publisher-provided excerpt, and
direct link only, per `summary_grounding.py`'s no-full-article-
reproduction policy — identical to every existing Lane A source.

**Evidence of official/authorized/stable/compatible**: live-fetched
this session (see census document §4.1) — real HTTP response, valid
RSS 2.0, channel title "Equinix, Inc. (EQIX) Press Releases," 3
dated items (2026-09-02 to 2026-09-10), every link on-domain
(`investor.equinix.com/news-events/press-releases/detail/...`, never
the feed URL itself or a bare homepage) — passes `canonical_url.
validate_canonical_url()`'s exact shape (https, on-domain, non-empty
path, no `search`/`results` segment) by direct inspection of the
observed URLs. `RSS_ATOM`/`OFFICIAL_IR` is the identical shape every
one of the 28 existing Lane A entries already uses — zero new adapter
code required.

**Duplicate/localization risk**: low. No existing Lane A or Lane B
source currently names Equinix by alias (confirmed: `company_aliases.
py`'s mechanical alias for `"Equinix, Inc."` is `("Equinix, Inc.",
"Equinix")` — a single, low-ambiguity word, no curated brand-alias
entry needed or proposed). If a future PR Newswire/CNBC item also
covers an Equinix story, the merged-PR-#57 mechanisms apply unchanged:
`dedup.is_duplicate_title()` (same-title-and-publisher, cross-source)
and, since Equinix publishes English-only and there is no Equinix-
adjacent non-English source in the registry, `localization_dedup.py`'s
cross-language path is not applicable to this source specifically.

**Editorial-admission risk**: Lane A has no admission gate — every
item is tiered, never rejected, by the architecture the census
document already traced. Positive/negative fixtures (materiality
tiering, not admission):
- *Negative-shaped, still visible at a lower tier*: "Equinix Names DD
  Dasgupta as Chief Marketing Officer" — a routine executive
  appointment. Under `materiality_classification.classify_issuer_
  story()`, this does not hit Gate A2/A4/B/B2 and very likely does
  not hit Gate C either (no `TAXONOMY_KEYWORDS` phrase in an
  executive-bio announcement) — correctly lands at `BACKGROUND`
  (`off_taxonomy_no_anchor`), still displayed (Lane A never rejects),
  matching the product policy that first-party company news stays
  visible at whatever tier it earns.
- *Positive*: "CPP Investments and Equinix Complete atNorth
  Acquisition to Support Growth of Leading Nordic Data Center
  Platform" — genuine M&A/capacity-expansion language; plausibly
  reaches `HIGH_SIGNAL` via Gate B/C if a real figure or the `ai_
  compute_and_data_center`/`memory_networking_power_cooling_
  packaging` taxonomy co-occurs with an anchor (`capacity`,
  `expansion`, `investment`) in the same piece — this is exactly the
  shape of story Lane A's materiality gates are designed to surface.

**Failure behavior**:
- *Feed outage*: `rss_atom_client.fetch_entries()` never raises;
  returns `FeedFetchResult(entries=(), failure_code=type(exc).
  __name__)` (e.g. `"ConnectionError"`, or `"HTTPError:503"` when a
  real status code is available) — isolated per-source, per the
  worker's own established per-feed isolation; every other source's
  polling that same tick is unaffected.
- *Malformed feed*: `failure_code="MalformedFeed"` (same isolation).
- *Stale item*: rejected by Lane A's own `_is_fresh()` check
  (`daily_news_pipeline.py` line ~553) — never persisted, never
  displayed.
- *Missing publication date*: `_parse_published_at()` returns `""`
  when neither `published_parsed` nor `updated_parsed` is present;
  an empty `published_at` fails the freshness check outright (treated
  as stale, not as "now") — never fabricated.
- *Non-English content*: N/A — Equinix's feed is confirmed English-
  only; no translation pathway is triggered for this source.
- *Translation unavailable*: N/A, same reason.
- *URL canonicalization*: `canonical_url.validate_canonical_url()`
  requires `https`, an exact match against `domains=
  ("investor.equinix.com",)`, a non-empty path, and rejects the bare
  feed URL itself or a `search`/`results` path segment — every
  observed live item link passes this gate.

### 1.2 L3Harris Technologies, Inc. — official newsroom (Lane A)

| Field | Value |
|---|---|
| Canonical source name | L3Harris Technologies newsroom |
| Owner | L3Harris Technologies, Inc. (first-party) |
| Domain | `www.l3harris.com` |
| Homepage | `https://www.l3harris.com/newsroom` |
| Feed URL | `https://www.l3harris.com/feeds/newsroom/rss.xml` |
| Source class | `SourceCategory.OFFICIAL_NEWSROOM` |
| Format | `SourceFormat.RSS_ATOM` |
| Language | English |
| Jurisdiction | United States |

**Registry/model fields**: `source_id="l3harris-newsroom-rss"`,
`category=OFFICIAL_NEWSROOM`, `domains=("www.l3harris.com",)`,
`jurisdiction="United States"`, `attribution_label="L3Harris
Technologies, Inc."`, `issuer_name="L3Harris Technologies, Inc."`,
`enabled=True`, `health_state=VERIFIED`, `licensing_classification=
_PILOT_LICENSING_CLASSIFICATION`, `priority=1`, `last_verified_at=
"2026-09-15"`. Already a real `TrackedCompany` (PR #59) — no stub
needed.

**Thematic tags / covered issuers / event types**: `space` — the
**first-ever issuer-linked `space`-theme Lane A source** (census
document §3.3 found zero prior). Covers L3Harris only. Expected event
types (live-verified): program milestones ("Modernizing the KC-135
for Decades of Mission Readiness"), contract-adjacent production news
("L3Harris Advances Production of Proximity Sensors for US Air
Force"), technology-anniversary features ("One Year of NTS-3").

**Freshness expectation**: Lane A 7-day window, 5-item cap. This feed
mixes editorial-style features and press releases (observed cadence:
daily-to-near-daily, denser than a typical pure-IR feed), so the
5-item cap is more likely to bind here than for a slower feed like
Equinix's.

**Attribution behavior**: identical to every Lane A source.

**Evidence**: live-fetched this session — valid RSS 2.0, channel title
"L3Harris Technologies," 3 items dated 2026-09-14/2026-09-15, all
on-domain (`www.l3harris.com/newsroom/editorial/...`,
`www.l3harris.com/newsroom/press-release/...`). `RSS_ATOM`/`OFFICIAL_
NEWSROOM` matches the existing shape exactly (identical to `nvidia-
newsroom-rss`, `cisco-newsroom-rss`, etc.).

**Duplicate/localization risk**: low. Mechanical alias `("L3Harris
Technologies, Inc.", "L3Harris Technologies")` — no ambiguity. Census
document §4.2 flagged `"L3Harris"` (bare) as a *possible* future
`_BRAND_ALIAS_OVERLAY` candidate for the separate Daily News alias
system — **not part of this implementation's scope**, since this
design only activates the source itself, not a new brand alias; noted
here for completeness only.

**Editorial-admission risk**: no admission gate (Lane A). Positive/
negative fixtures:
- *Negative-shaped, still visible at a lower tier*: "One Year of
  NTS-3: Delivering the Future of Resilient Positioning, Navigation
  and Timing" — an anniversary/editorial-style feature, not a new
  disclosure. Likely `BACKGROUND` unless it independently states a
  new figure.
- *Positive*: "L3Harris Advances Production of Proximity Sensors for
  US Air Force" — genuine production/program language; plausible
  `WATCHLIST`/`HIGH_SIGNAL` depending on whether a quantified
  contract/order figure is present in the full text (Gate B) —
  consistent with the `SIGNALS_ADMISSION_MATERIALITY_CALIBRATION`
  document's own finding that Lane A's only levers are Gates
  A2/A4/B/B2/C, never Gate D.
- *Known multi-segment caveat* (carried forward from the Tier 1
  Cohort 1 tracked-company implementation design, PR #59): L3Harris's
  much larger defense-electronics business is not space-specific — an
  L3Harris newsroom item about a non-space program still gets
  ingested and tiered (this source has no per-item segment filter,
  matching every other issuer feed in the registry); this is a known,
  accepted characteristic, not a defect this implementation proposes
  to fix.

**Failure behavior**: identical shape to §1.1 (feed outage → isolated
`failure_code`; malformed feed → `"MalformedFeed"`; stale/missing-date
→ rejected via Lane A's `_is_fresh()`; non-English/translation → N/A,
confirmed English; URL canonicalization → `validate_canonical_url()`
against `domains=("www.l3harris.com",)`).

### 1.3 Firefly Aerospace Inc. — official news (Lane A)

| Field | Value |
|---|---|
| Canonical source name | Firefly Aerospace news |
| Owner | Firefly Aerospace Inc. (first-party) |
| Domain | `fireflyspace.com` |
| Homepage | `https://fireflyspace.com/news/` |
| Feed URL | `https://fireflyspace.com/feed/` |
| Source class | `SourceCategory.OFFICIAL_NEWSROOM` |
| Format | `SourceFormat.RSS_ATOM` |
| Language | English |
| Jurisdiction | United States |

**Registry/model fields**: `source_id="firefly-aerospace-news-rss"`,
`category=OFFICIAL_NEWSROOM`, `domains=("fireflyspace.com",)`,
`jurisdiction="United States"`, `attribution_label="Firefly Aerospace
Inc."`, `issuer_name="Firefly Aerospace Inc."`, `enabled=True`,
`health_state=VERIFIED`, `licensing_classification=_PILOT_LICENSING_
CLASSIFICATION`, `priority=1`, `last_verified_at="2026-09-15"`.
Already a real `TrackedCompany` (PR #59).

**Thematic tags / covered issuers / event types**: `space` — the
**second new issuer-linked `space` source**, pairing with L3Harris to
bring the theme from 0 to 2 issuer-linked sources. Covers Firefly
only. Expected event types (live-verified): launch-services contracts
("Firefly Aerospace Signs Contract with SSC Space for Two Alpha
Launches from Esrange Space Center"), mission-payload partnerships
("Onboards Zeno Power on Blue Ghost Mission"), government contracts
("Onboarded to NASA Spacecraft Processing Operations Contract").

**Freshness expectation**: Lane A 7-day window, 5-item cap. Observed
cadence: roughly biweekly to monthly per the 3-item sample spanning
2026-08-17 to 2026-09-09 — the least dense of the 4 issuer candidates,
meaning the cap is unlikely to bind.

**Attribution behavior**: identical to every Lane A source.

**Evidence**: live-fetched this session — valid feed, channel title
"Firefly Aerospace," 3 items with real summaries and dates, all links
on-domain (`fireflyspace.com/news/...`). Standard WordPress-style
`/feed/` endpoint, the same shape already proven for other Lane A
sources' non-Q4-IR-platform feeds.

**Duplicate/localization risk**: low. Mechanical alias `("Firefly
Aerospace Inc.", "Firefly Aerospace")`. Real cross-source overlap risk
worth naming explicitly: **SpaceNews** (§1.5, this same cohort) covers
the same launch-industry beat and could legitimately cover the same
Firefly event — this is precisely the cross-lane duplicate shape
`select_canonical_stories()`/`dedup.is_duplicate_title()` already
handle (Lane A's own issuer feed vs. Lane B's independent trade-press
coverage of the same underlying news).

**Editorial-admission risk**: no admission gate (Lane A). Positive/
negative fixtures:
- *Positive*: "Firefly Aerospace Signs Contract with SSC Space for Two
  Alpha Launches" — a genuine, named contract; `_COMPANY_ACTION_
  KEYWORDS`-shaped language ("signs") and plausible Gate B/C hit if a
  contract value or launch count/date is quantified in the full text.
- *Negative-shaped, still visible at a lower tier*: a routine mission-
  partnership announcement with no quantified figure (e.g. the Zeno
  Power radioisotope-technology item, as observed, reads as a
  technical-collaboration feature with no disclosed dollar value) —
  plausibly `BACKGROUND`/`WATCHLIST`.

**Failure behavior**: identical shape to §1.1/§1.2, scoped to `domains=
("fireflyspace.com",)`.

### 1.4 YASKAWA Electric Corporation — official newsroom, Japan (Lane A)

| Field | Value |
|---|---|
| Canonical source name | Yaskawa Global news releases |
| Owner | YASKAWA Electric Corporation (first-party) |
| Domain | `www.yaskawa-global.com` |
| Homepage | `https://www.yaskawa-global.com/newsrelease` |
| Feed URL | `https://www.yaskawa-global.com/feed/` |
| Source class | `SourceCategory.OFFICIAL_NEWSROOM` |
| Format | `SourceFormat.RSS_ATOM` |
| Language | **English** (curated value — see below) |
| Jurisdiction | Japan |

**Registry/model fields**: `source_id="yaskawa-newsroom-rss"`,
`category=OFFICIAL_NEWSROOM`, `domains=("www.yaskawa-global.com",)`,
`jurisdiction="Japan"`, `attribution_label="YASKAWA Electric
Corporation"`, `issuer_name="YASKAWA Electric Corporation"`,
`enabled=True`, `health_state=VERIFIED`, `licensing_classification=
_PILOT_LICENSING_CLASSIFICATION`, `priority=1`, `last_verified_at=
"2026-09-15"`, **`language="English"`** — a required, deliberate
override of the feed's own `<channel><language>ja</language>` tag
(confirmed live this session: the feed metadata declares Japanese, but
every sampled item's actual title/content is plain English, on
Yaskawa's own English-language global site). Per `DailyNewsSourceEntry.
language`'s own field docstring ("a human-verified fact about the
source, never derived from text"), the curated value must reflect
observed content, not the feed's own possibly-mislabeled tag — this is
a **required implementation detail**, not optional, flagged again here
since it is the one field on this source most likely to be
miscopied by a future implementer pulling directly from the feed's own
metadata. Already a real `TrackedCompany` (PR #59, source=`EDINET`) —
this Daily News registration is entirely independent of that EDINET
filing-adapter registration; see §4.4 for the filing/Daily-News
separation statement.

**Thematic tags / covered issuers / event types**: `humanoids` — the
**second issuer-linked `humanoids` source** (after Rockwell
Automation), bringing the theme from 1 to 2 issuer-linked sources.
Covers Yaskawa only. Expected event types (live-verified): new-product
launches ("Launch of Collaborative Robot MOTOMAN-HC12 with 12 kg
Payload Capacity"), personnel changes ("Personnel Changes"), IR/
earnings-date notices ("Scheduled Date for FY26 2Q Results
Announcement").

**Freshness expectation**: Lane A 7-day window, 5-item cap. Observed
cadence: 3 items across 2026-08-17 to 2026-09-13 — moderate.

**Attribution behavior**: identical to every Lane A source.

**Evidence**: live-fetched this session — valid RSS 2.0, 3 items, all
on-domain (`www.yaskawa-global.com/newsrelease/...`,
`www.yaskawa-global.com/ir/news/...`). The feed mixes News/IR/
Technology/New-Product/Event categories under one combined feed
(confirmed by the item paths' differing subpaths — `/newsrelease/
product/`, `/newsrelease/news/`, `/ir/news/` — all still resolving to
the same `www.yaskawa-global.com` domain, so `domains=
("www.yaskawa-global.com",)` correctly covers all three without
needing a wider allowlist).

**Duplicate/localization risk**: **moderate, the highest of the 4
issuer candidates.** Yaskawa is a global industrial-robotics OEM whose
product launches are routinely covered by **The Robot Report** (§1.6,
this same cohort) and by existing `EDITORIAL_SOURCE_REGISTRY` sources
(IEEE Spectrum, Semiconductor Engineering) — a genuine cross-lane
duplicate is plausible for a major product announcement like the
MOTOMAN-HC12 launch. Mitigated by the existing, unmodified
`select_canonical_stories()`; the merged-PR-#57 localization-dedup
path (`localization_dedup.is_localized_duplicate()`) is **not**
triggered for this specific source, since Yaskawa's own feed is
declared/curated English (not a genuine cross-language pair the way a
Japanese-native EDINET filing translation would be) — but the general
principle that a Japan-jurisdiction source needs this consideration
explicitly checked, not assumed, is exactly why this field is called
out here.

**Editorial-admission risk**: no admission gate (Lane A). Positive/
negative fixtures, explicitly cross-referenced against `SIGNALS_
ADMISSION_MATERIALITY_CALIBRATION_2026_09_15.md`'s own Rule 3:
- *Negative-shaped, still visible at a lower tier*: "Personnel
  Changes" — a routine leadership/personnel item, the **exact content
  shape Rule 3 (personnel/leadership/governance-announcement rule)**
  in the companion calibration document targets. Under today's
  unmodified code, this lands at `BACKGROUND` (no Gate A2/A4/B/B2/C
  hit expected from a bare personnel-changes notice) and stays
  visible at that tier — correct outcome under current rules, and
  **this implementation does not depend on Rule 3 being built first**
  (Lane A already naturally demotes this kind of item via its
  existing materiality gates alone, since Rule 3 was designed for
  Lane B's admission layer, which Lane A doesn't have).
- *Positive*: "Launch of Collaborative Robot MOTOMAN-HC12 with 12 kg
  Payload Capacity" — a real product spec with a quantified payload
  figure ("12 kg") — `_NUMERIC_MAGNITUDE_PATTERNS` does **not** match
  a bare "12 kg" (no billion/million/GW/MW/%/thousand-scaled unit), so
  this likely does **not** reach Gate B on the payload figure alone;
  most likely lands at `WATCHLIST` or `BACKGROUND` under current rules
  unless the full announcement text separately quantifies a
  production/shipment/investment figure. Named here explicitly as a
  **known, accepted current-rules outcome**, not a defect this
  implementation proposes to fix.
- *Positive, IR-adjacent*: "Scheduled Date for FY26 2Q Results
  Announcement" — this is exactly the **scheduling-notice** shape
  `materiality_classification.py`'s own "Earnings vs. scheduling"
  calibration fix already handles: `_SCHEDULING_KEYWORDS` disqualifies
  it from Gate A2, correctly landing at `WATCHLIST`
  (`scheduling_notice_not_yet_substantive`), never `HIGH_SIGNAL` —
  confirming this source will not inflate the registry with false
  earnings-shaped High Signal items, the exact failure mode that fix
  was built for.

**Failure behavior**: identical shape to §1.1-§1.3, scoped to
`domains=("www.yaskawa-global.com",)`. **Non-English content**: N/A
under the curated `language="English"` value — every observed item is
genuinely English-language content, so no translation pathway is
needed despite the feed's own misleading metadata tag; this is a
one-time curation decision, not a runtime behavior this
implementation needs to build.

### 1.5 SpaceNews — independent trade press (Lane B)

| Field | Value |
|---|---|
| Canonical source name | SpaceNews |
| Owner | SpaceNews, Inc. (independent trade publisher — not first-party to any tracked issuer) |
| Domain | `spacenews.com` |
| Homepage | `https://spacenews.com/` |
| Feed URL | `https://spacenews.com/feed/` |
| Source class | `SourceCategory.INDEPENDENT_NEWS` |
| Format | `SourceFormat.RSS_ATOM` |
| Language | English |
| Jurisdiction | United States |

**Registry/model fields**: a new `DailyNewsSourceEntry` in `EDITORIAL_
SOURCE_REGISTRY` — `source_id="spacenews-rss"`, `category=
INDEPENDENT_NEWS`, `domains=("spacenews.com",)`, `jurisdiction=
"United States"`, `attribution_label="SpaceNews"`, `licensing_
classification=_EDITORIAL_LICENSING_CLASSIFICATION` (reused verbatim —
independent journalism, headline/excerpt/link only), `priority=1`,
`issuer_agnostic=True`, **`allowlisted=True`** (required — `validate_
source_entry()` rejects any `INDEPENDENT_NEWS` entry without this
flag), `enabled=True`, `health_state=VERIFIED`, `last_verified_at=
"2026-09-15"`. No `issuer_name` (issuer-agnostic, matching every
other Lane B independent-news source).

**Thematic tags / covered issuers / event types**: no stored theme
field on the source itself (Lane B has none — theme relevance is
determined per-item at match time via `editorial_matching.
THEME_KEYWORDS`'s `space` bucket: "satellite launch," "rocket
launch," "orbital launch," "launch vehicle," "spacecraft," "space
station," "satellite constellation"). Qualitatively `space`-themed —
fills the **first-ever dedicated space-industry trade-press source**
in `EDITORIAL_SOURCE_REGISTRY` (census document §3.3 found zero
prior). Relevant to every tracked `space` issuer whose name or a
`space` theme phrase appears in an item (Rocket Lab, AST SpaceMobile,
Redwire, Planet Labs, Intuitive Machines, ispace, and — once this
same cohort's §1.2/§1.3 entries are added — Firefly Aerospace and
L3Harris directly by name). Expected event types (live-verified):
launch-industry analysis, satellite-constellation news, on-orbit-
servicing coverage, national-security-space reporting.

**Freshness expectation**: Lane B's 72-hour (`_FRESHNESS_WINDOW_HOURS
= 72`) window — materially tighter than Lane A's 7 days — 5-item cap.
Observed cadence: 3 items within roughly 24 hours during live
verification (2026-09-14/2026-09-15), a high-frequency daily trade
publication, meaning the 5-item-per-run cap is the binding constraint
here, not freshness.

**Attribution behavior**: headline, publisher-provided excerpt, and
direct link only — independent journalism, per `_EDITORIAL_LICENSING_
CLASSIFICATION`, distinct in kind from Lane A's official-source text
(this source did not write or authorize any tracked issuer's own
statements; it is third-party reporting about them).

**Evidence**: live-fetched this session — valid RSS 2.0, channel title
"SpaceNews," 3 items all dated 2026-09-14/2026-09-15, all on-domain.
`RSS_ATOM`/`INDEPENDENT_NEWS` is the identical shape TechCrunch/Ars
Technica/The Verge/Data Center Frontier already use — zero new
adapter code required.

**Duplicate/localization risk**: low-moderate. Overlap possible with
Firefly Aerospace's/L3Harris's own Lane A feeds (§1.2/§1.3, this same
cohort) on the same underlying event — the standard cross-lane
`select_canonical_stories()` path applies, unmodified. No non-English
publisher in the `space` theme today, so `localization_dedup.py`'s
cross-language path is not presently exercised by this source.

**Editorial-admission risk**: SpaceNews **is** subject to the full
`assess_admission()` gate (Lane B, no source-level bypass — unlike
Space Force/NIST). Positive/negative fixtures, explicitly cross-
referenced against the companion calibration document:
- *Negative, rumor-shaped (Rule 1 territory)*: a hypothetical
  "SpaceX reportedly delays next Starship test flight, sources say" —
  under today's **unmodified** code (Rule 1 is proposed, not yet
  built), this could reach `HIGH_SIGNAL` via Gate D exactly like
  Fixture A if it names a tracked company in the title with action
  language, or via Gate C on the `space` taxonomy... **except**
  `materiality_classification.TAXONOMY_KEYWORDS` has **no `space`
  bucket** (confirmed in the companion document's Fixture C trace) —
  so a pure-space-topic item cannot reach Gate C today regardless of
  content, meaningfully limiting (but not eliminating — Gate D remains
  open) this specific risk shape for a `space`-themed source
  specifically, compared to an `ai-buildout`-themed one.
- *Negative, marketing-shaped (Rule 2 territory)*: a hypothetical
  "[Small satellite company] achieves ISO certification" — no company
  in Eeva's universe is named as subject unless the certification-
  holder is itself tracked; under current rules this would need a
  `space` theme-phrase match to be admitted at all (unlikely for a
  bare certification announcement, which rarely uses "satellite
  launch"/"rocket launch"/etc. phrasing) — this source's structural
  shape (independent trade press covering an entire industry, not one
  issuer) makes marketing-designation false positives less likely than
  Fixture B's shape (a wire-syndicated single-company press release),
  though not impossible.
- *Positive*: "More satellites watch the battlefield, putting a
  premium on making sense of the data" (live-verified sample item) —
  genuine industry-analysis journalism; plausible `WATCHLIST` via
  theme-only admission, `HIGH_SIGNAL` only if it independently
  quantifies a contract/capacity/program figure tied to a named,
  tracked company.

**Failure behavior**: identical mechanical shape to Lane A (isolated
`fetch_entries()` failure, `_is_fresh()` staleness check — but at
Lane B's 72-hour window, not Lane A's 7 days — `_parse_published_at()`
empty-string fallback for a missing date, `canonical_url.validate_
canonical_url()` scoped to `domains=("spacenews.com",)`). **Additional
Lane-B-specific failure mode**: an item that matches no company and no
`space`/other theme phrase is silently dropped at the fail-closed
pre-check (`items_no_match`), before `assess_admission()` is even
called — this is expected, routine behavior for a broad trade
publication whose non-space coverage (SpaceNews covers some adjacent
defense/policy topics) will not always match.

### 1.6 The Robot Report — independent trade press (Lane B)

| Field | Value |
|---|---|
| Canonical source name | The Robot Report |
| Owner | WTWH Media LLC, d/b/a The Robot Report (independent trade publisher) |
| Domain | `www.therobotreport.com` |
| Homepage | `https://www.therobotreport.com/` |
| Feed URL | `https://www.therobotreport.com/feed/` |
| Source class | `SourceCategory.INDEPENDENT_NEWS` |
| Format | `SourceFormat.RSS_ATOM` |
| Language | English |
| Jurisdiction | United States |

**Registry/model fields**: `source_id="robot-report-rss"`, `category=
INDEPENDENT_NEWS`, `domains=("www.therobotreport.com",)`, `jurisdiction
="United States"`, `attribution_label="The Robot Report"`, `licensing_
classification=_EDITORIAL_LICENSING_CLASSIFICATION`, `priority=1`,
`issuer_agnostic=True`, `allowlisted=True` (required), `enabled=True`,
`health_state=VERIFIED`, `last_verified_at="2026-09-15"`.

**Thematic tags / covered issuers / event types**: qualitatively
`humanoids`-themed via `THEME_KEYWORDS`'s `humanoids` bucket
("humanoid robot," "humanoid robotics," "industrial robot,"
"warehouse robotics," "robotics automation"). Fills the **first-ever
dedicated robotics trade-press source** in `EDITORIAL_SOURCE_
REGISTRY`. Relevant to every tracked `humanoids` issuer whose name
appears (FANUC, Rockwell Automation, Doosan Robotics, Mitsubishi
Electric, Symbotic, indie Semiconductor, and — once §1.4 is added —
Yaskawa directly by name) and to private-ecosystem entities named in
the thematic coverage audit (Boston Dynamics, Figure AI, Apptronik,
Agility Robotics — a live-verified sample item directly covered
"Agility's Digit 5 humanoid"). Expected event types (live-verified):
humanoid-robot product news, industrial-automation/RaaS business-model
analysis, standards/interoperability news (a live item covered an ISO
21423 reference implementation).

**Freshness expectation**: Lane B 72-hour window, 5-item cap. Observed
cadence: 3 items within roughly 24 hours (2026-09-14/2026-09-15) —
another high-frequency daily publication; the cap, not freshness, is
the binding constraint.

**Attribution behavior**: independent journalism, identical shape to
SpaceNews above.

**Evidence**: live-fetched this session — valid RSS 2.0, channel title
"The Robot Report," 3 items dated 2026-09-14/2026-09-15, all on-domain.

**Duplicate/localization risk**: low-moderate. Overlap possible with
Yaskawa's own newsroom feed (§1.4) on a shared product-launch story —
same standard `select_canonical_stories()` mitigation. This is also
the source pairing this design's own §1.4 admission-risk analysis
named explicitly (`localized_duplicate_yaskawa_newsroom_vs_robot_
report`, carried forward from the census document's own §5.3 fixture
list — not re-invented here).

**Editorial-admission risk**: full `assess_admission()` gate applies
(Lane B, no bypass). Positive/negative fixtures:
- *Negative, marketing-shaped (Rule 2 territory)*: a hypothetical
  "[Robotics startup] wins Robotics Business Review award" — under
  today's unmodified rules, admitted only if a `humanoids` theme
  phrase is also present (a bare-award announcement rarely uses
  "humanoid robot"/"industrial robot"/etc. literally) — structurally
  similar risk-limiting factor to SpaceNews's own case.
- *Positive*: "Agility's Digit 5 humanoid has new legs, batteries and
  safety upgrades" (live-verified sample) — matches `humanoids` theme
  directly (`"humanoid robot"` phrase or close variant); admitted via
  `theme_subject` (Agility Robotics is not a tracked company — private
  ecosystem entity per the thematic coverage audit, so no company
  attaches, correctly, matching Fixture D's own "theme-only, no
  company" pattern in the companion calibration document); materiality
  likely `WATCHLIST` unless a quantified spec/production figure is
  present.
- *Positive, rumor-adjacent test*: a hypothetical "Sources say
  [Company] plans layoffs amid robotics slowdown" — this is exactly
  Rule 1's target shape; under today's unmodified code this could
  still reach `HIGH_SIGNAL` if a tracked company is named with nearby
  action language ("plans") and Gate D's reporting-verb list is
  satisfied — **named here as a real, currently-open risk for this
  source specifically**, not resolved by this implementation (Rule 1
  is proposed, separately-scoped work, per the companion document).

**Failure behavior**: identical shape to §1.5, scoped to `domains=
("www.therobotreport.com",)`.

---

## 2. Duplicate/localization risk — cohort-wide summary, PR #57 rules applied

All three merged-PR-#57 mechanisms apply unmodified to all 6 new
sources, with no source-specific code change required:

1. **`dedup.is_duplicate_title()`** (same normalized title + publisher,
   cross-source) — applies wherever a Lane A issuer feed (Equinix/
   L3Harris/Firefly/Yaskawa) and a Lane B trade-press source
   (SpaceNews/The Robot Report, or an existing source) cover the same
   underlying event.
2. **`localization_dedup.is_localized_duplicate()`** (cached-
   translation-only cross-language comparison, render-time-pure per the
   PR #57 architectural invariant) — **not actively exercised by any
   of the 6 new sources**, since all 6 are curated/declared English
   (§1.4's Yaskawa language-curation decision is what keeps this true
   — had the feed's own `ja` metadata been trusted blindly, this
   mechanism would need to actually run for Yaskawa-vs-Japanese-outlet
   pairs; the curation decision avoids that scenario entirely for this
   cohort).
3. **`select_canonical_stories()`** (cross-source canonicalization at
   render time) — the general mechanism covering every cross-lane
   duplicate named in §1: Equinix-vs-SpaceNews-style theme overlap is
   not applicable (different themes), but Firefly/L3Harris-vs-
   SpaceNews and Yaskawa-vs-The-Robot-Report are both real, named,
   already-mitigated-by-existing-code pairs.

No new duplicate-detection code is proposed or required.

---

## 3. Exact proposed source/test files and minimal test matrix

### 3.1 Production files

| File | Change |
|---|---|
| `src/data_access/daily_news/source_registry.py` | Append a new `EXPANSION_BATCH_6_SOURCE_REGISTRY` tuple (Equinix, L3Harris, Firefly Aerospace, Yaskawa — the 4 Lane A entries from §1.1-§1.4) to `RUNTIME_SOURCE_REGISTRY`'s assembly (`RUNTIME_SOURCE_REGISTRY = PILOT_SOURCE_REGISTRY + EXPANSION_BATCH_1..5 + EXPANSION_BATCH_6`); append a new `EDITORIAL_SOURCE_REGISTRY_BATCH_4` tuple (SpaceNews, The Robot Report — §1.5-§1.6) to `EDITORIAL_SOURCE_REGISTRY`'s assembly. **The only file that changes the actual source list — same pattern as every prior batch.** |

No change to `feed_registry.py` (derives `PILOT_FEEDS` automatically),
`editorial_pipeline.py`, `editorial_admission.py`, `editorial_
matching.py`, `materiality_classification.py`, `dedup.py`,
`localization_dedup.py`, `canonical_url.py`, `rss_atom_client.py`, or
`daily_news_pipeline.py` — every one of the 6 sources fits the
existing `RSS_ATOM`/`OFFICIAL_IR`/`OFFICIAL_NEWSROOM`/`INDEPENDENT_
NEWS` shapes exactly, with zero new adapter or gate logic required.
No change to `src/config/tracked_companies.py` (all 4 Lane A issuers
already exist there, merged PR #59) or `src/config/issuer_registry.py`
(no `DISCOVERY_STUBS` entry needed for any of the 4 — unlike HPE/
Quanta/nVent/Arista/Cisco, none of this cohort needs the stub path).

### 3.2 Test files

| File | Change |
|---|---|
| `tests/test_daily_news_source_registry.py` | Update exact-count assertions (`len(RUNTIME_SOURCE_REGISTRY) == 28` → `32`; `len(feed_registry.PILOT_FEEDS) == 28` → `32`; `len(EDITORIAL_SOURCE_REGISTRY) == 36` → `38`); add per-entry identity/field assertions for all 6 new sources (source_id, category, domains, issuer_name where applicable, `language="English"` explicitly asserted for Yaskawa as a named regression guard against the feed-metadata-mislabeling risk in §1.4); add a duplicate-identifier guard extension (no `source_id`/`canonical_url` collision against the existing 64), matching this file's own established per-batch pattern. |
| `tests/test_daily_news_feed_registry.py` | Confirm the 4 new Lane A entries appear in `PILOT_FEEDS` with the correct `company_name`/`feed_url`/`language` fields, mirroring existing per-batch assertions in this file. |
| `tests/test_editorial_admission.py` | New fixtures: SpaceNews/Robot Report items exercising the `theme_subject` (no-company, humanoids/space) admission path — no new admission code, so these are regression proofs the existing gate handles the 2 new sources' real content shapes correctly, not new gate logic. |
| `tests/test_daily_news_worker.py` | The `sources_polled={len(EDITORIAL_SOURCE_REGISTRY)}` assertions need no code change (computed from the live constant) but must be re-run to confirm the new count (38) flows through worker log lines correctly. |
| `tests/test_daily_news_dedup.py`, `tests/test_localization_dedup.py` | Re-run only — no code change; confirms the existing mechanisms handle the 6 new sources' real content without regression. |
| `tests/test_daily_news_canonical_url.py` | Re-run only — confirms every live-observed item link from all 6 sources (documented per-source in §1) passes `validate_canonical_url()` given each source's own `domains` tuple. |

### 3.3 Minimal test matrix (one row per source × failure/positive mode)

| Fixture | Source | Proves |
|---|---|---|
| `equinix_ir_rss_publishes_real_story` | Equinix | End-to-end Lane A ingestion (fetch → validate → persist), no admission gate needed |
| `l3harris_newsroom_rss_publishes_mixed_editorial_and_press_release` | L3Harris | A feed mixing feature/press-release content shapes still ingests correctly |
| `firefly_aerospace_feed_publishes_real_story` | Firefly Aerospace | Same, low-cadence feed |
| `yaskawa_newsroom_rss_language_declared_english_not_feed_metadata` | Yaskawa | The curated `language="English"` value is what's stored and used, never the feed's own `ja` tag — direct regression guard for §1.4's central finding |
| `spacenews_independent_news_requires_allowlisting` | SpaceNews | `validate_source_entry()` rejects the entry if `allowlisted` were ever accidentally left `False` |
| `spacenews_matches_tracked_space_companies_by_theme_and_name` | SpaceNews | `editorial_matching.py` correctly resolves a sample headline naming Rocket Lab/Firefly/L3Harris |
| `robot_report_independent_news_requires_allowlisting` | The Robot Report | Same as SpaceNews |
| `robot_report_matches_tracked_humanoids_companies_by_theme_and_name` | The Robot Report | Resolves a sample headline naming Yaskawa/FANUC/Rockwell |
| `source_failure_isolated_new_source_down_does_not_block_others` | All 6 | A simulated fetch failure for any one new source does not interrupt polling of any other source, existing or new |
| `stale_entry_beyond_lane_specific_freshness_window_does_not_publish` | All 6, both windows | A fixture item dated outside each lane's own window (7 days for the 4 Lane A sources, 72 hours for the 2 Lane B sources — **not the same number**, a real distinction this matrix must exercise explicitly) is correctly excluded |
| `missing_publication_date_treated_as_stale_not_as_now` | All 6 | An item with no parseable date is rejected, never silently backfilled with the current timestamp |
| `equinix_l3harris_firefly_yaskawa_no_krx_code_collision_with_existing_registry` | All 4 Lane A | No `source_id`/`canonical_url` collision against the live 64-source registry — a negative fixture proving the batch addition is clean |
| `hold_candidates_never_silently_added` | GE Vernova, Digital Realty, Nabtesco, Harmonic Drive Systems, Hanmi Semiconductor, HD Hyundai Electric, KRX | A negative fixture confirming none of the 7 HOLD candidates gained a Lane A/B entry as a side effect of this batch |

---

## 4. Cohort-level design

### 4.1 Validation gate before source activation

Reusing and making concrete, per-source, the acceptance checklist
already proposed in the census document (§5.1 there) — every item
below is independently confirmed **already** for all 6 sources in this
document's own §1 tables, not merely proposed:

1. **URL/feed validity** — real HTTP fetch, valid RSS 2.0, this
   session, all 6. ✓
2. **Content ownership** — domain matches claimed owner for all 4
   issuer sources; SpaceNews/The Robot Report are named, real,
   independent trade publishers, not aggregators. ✓
3. **Attribution** — `_PILOT_LICENSING_CLASSIFICATION`/`_EDITORIAL_
   LICENSING_CLASSIFICATION` reused verbatim, correctly matching each
   source's real legal character (first-party press release vs.
   independent journalism) — no new classification text needed or
   invented. ✓
4. **Cadence** — at least 3 real dated items observed for every
   source. ✓
5. **Freshness** — newest item within days of verification for all 6
   (none of the batch-3 "Microsoft, newest item over a year old" shape
   present). ✓
6. **Translation-language declaration** — explicit, human-verified for
   all 6; Yaskawa's the one requiring active correction against its own
   feed metadata (§1.4). ✓
7. **Duplicate behavior** — named explicitly per source in §1/§2, all
   mapped to existing, unmodified mechanisms. ✓
8. **Editorial-admission compatibility** — Lane B sources (SpaceNews,
   The Robot Report) confirmed to produce real `theme_subject`-eligible
   content via live sample items; Lane A sources need no admission
   compatibility check (none exists for that lane, by design). ✓
9. **Failure/fallback handling** — confirmed generic, existing,
   per-source-isolated mechanisms cover all 6 with no new code (§1's
   per-source "Failure behavior" subsections, all citing the same
   `fetch_entries()`/`_is_fresh()`/`validate_canonical_url()`
   mechanisms). ✓

**All 6 sources clear the gate as designed.** No source in this
cohort requires new admission/materiality logic to be built first —
this is a deliberate scoping property of this cohort (unlike, e.g., a
future source that might need Rule 1-6 from the companion calibration
document built first to be safely activatable).

### 4.2 How these 6 sources close the identified gaps

| Gap (from the census document) | Before this cohort | After this cohort |
|---|---|---|
| `space` theme, issuer-linked Daily News sources | 0 | **2** (Firefly Aerospace, L3Harris) |
| `space` theme, dedicated trade press | 0 | **1** (SpaceNews) |
| `humanoids` theme, issuer-linked Daily News sources | 1 (Rockwell Automation) | **2** (+ Yaskawa Electric) |
| `humanoids` theme, dedicated trade press | 0 | **1** (The Robot Report) |
| Direct Daily News coverage for Equinix (Cohort 1 tracked-company merge, PR #59) | none | **1** (own IR feed) |
| Direct Daily News coverage for L3Harris (PR #59) | none | **1** (own newsroom feed) |
| Direct Daily News coverage for Firefly Aerospace (PR #59) | none | **1** (own news feed) |
| Direct Daily News coverage for Yaskawa Electric (PR #59) | none | **1** (own newsroom feed) |
| Runtime-active source total | 64 (28 Lane A + 36 Lane B) | **70** (32 Lane A + 38 Lane B) |

4 of the 10 Cohort 1 supply-chain issuers (Equinix, L3Harris, Firefly
Aerospace, Yaskawa Electric) now have direct Daily News coverage; the
remaining 6 (Hanmi Semiconductor, HD Hyundai Electric, Nabtesco,
Harmonic Drive Systems, GE Vernova, Digital Realty) remain uncovered —
all 6 are among the 7 explicit HOLD exclusions below (§4.3), by
design, not omission.

### 4.3 Explicit exclusions from this implementation's scope

**The 7 HOLD candidates** (no working feed independently confirmed in
the census document; none is added by this design): GE Vernova Inc.,
Digital Realty Trust Inc., Nabtesco Corporation, Harmonic Drive
Systems Inc., Hanmi Semiconductor Co., Ltd., HD Hyundai Electric Co.,
Ltd., Korea Exchange (KRX).

**The 4 dormant env-gated sources** (already registered in `source_
registry.py` as `GATED_MARKET_NEWS_SOURCE_REGISTRY`/`GATED_JP_KR_
SOURCE_REGISTRY`, blocked by empty `EDGE_DAILY_NEWS_ENABLED_SOURCES`/
`EDGE_DAILY_NEWS_ENABLED_SOURCES_JP_KR` allow-lists; untouched by this
design): Light Reading (`light-reading-rss`), The Japan Times Business
(`japan-times-business-rss`), Business Korea Industries
(`businesskorea-industries-rss`), Business Korea Science & Tech
(`businesskorea-science-tech-rss`).

Neither group receives a registry entry, an env-var change, a code
change, or any other action in this document or a later implementation
following it.

### 4.4 Filing sources kept separate from Daily News sources

This design touches **only** `src/data_access/daily_news/*` and its
own test files (§3). No file under `src/data_access/edgar/`, `src/
data_access/edinet/`, or `src/data_access/dart/` is referenced,
read, or proposed for change anywhere in this document. Yaskawa
Electric's own EDINET filing-adapter registration (`tracked_
companies.py`, `source="EDINET"`, merged PR #59) and this document's
own, entirely independent Daily News newsroom-feed registration
(§1.4) are two structurally unrelated systems that happen to share
one issuer identity string (`issuer_name="YASKAWA Electric
Corporation"`, resolved via `feed_registry.tracked_company_for()`
read-only lookup) — activating one has zero effect on, and requires
zero change to, the other. The same separation holds for all 3 other
Lane A candidates already tracked via SEC EDGAR.

### 4.5 Non-goals, explicit

- **No taxonomy redesign** — `TAXONOMY_KEYWORDS`/`THEME_KEYWORDS` are
  read (to describe each source's qualitative thematic coverage,
  §1) but never edited anywhere in this document. The `space`-
  taxonomy-bucket gap in `materiality_classification.py` (noted in the
  companion calibration document's Fixture C trace) is not addressed
  here.
- **No relationship-graph work** — nothing in this document creates or
  modifies any entity-relationship, supply-chain, or company-linkage
  data structure.
- **No Dashboard UI redesign** — no file under `src/ui/` is referenced
  as a target for change; the 6 new sources render through the
  existing, unmodified Dashboard/Daily News components once activated,
  exactly like every one of the 64 sources already live.
- **No admission/materiality rule implementation** — Rules 1-6 from
  `SIGNALS_ADMISSION_MATERIALITY_CALIBRATION_2026_09_15.md` are
  referenced only to characterize each new source's own risk profile
  (§1); none is implemented, and this cohort's own validation gate
  (§4.1) confirms none is a prerequisite for safely activating these 6
  specific sources.

---

## Files read / created

**Read this session (all read-only)**: `src/data_access/daily_news/
source_registry.py`, `src/data_access/daily_news/feed_registry.py`
(prior session, facts reused), `src/data_access/daily_news/
canonical_url.py`, `src/data_access/daily_news/rss_atom_client.py`,
`src/data_access/daily_news/editorial_pipeline.py` (freshness-window
and per-source-cap constants), `src/data_access/daily_news/daily_
news_pipeline.py` (Lane A's own freshness-window/cap constants,
grepped — confirmed 7-day window, distinct from Lane B's 72-hour
window), `src/data_access/daily_news/dedup.py`/`localization_dedup.py`
(function signatures, grepped), `tests/` directory listing (exact
existing test-file names for §3.2's file-targeting accuracy — `test_
editorial_admission.py`, `test_editorial_matching.py`, `test_
editorial_pipeline.py`, `test_localization_dedup.py`, `test_
government_editorial_sources.py`, confirmed distinct from the `test_
daily_news_*`-prefixed files), `git log`/`git status` (branch/state
confirmation). No live external web fetch was performed this session
— every source's live-verification evidence cited in §1 is reused
directly from `design/DAILY_NEWS_SOURCE_CENSUS_AND_COHORT1_DESIGN_
2026_09_15.md`'s own already-completed, same-session verification
work, not re-fetched.

**Not touched**: all 9 pre-existing untracked artifacts plus `design/
DAILY_NEWS_SOURCE_CENSUS_AND_COHORT1_DESIGN_2026_09_15.md` and
`design/SIGNALS_ADMISSION_MATERIALITY_CALIBRATION_2026_09_15.md`
(both treated as read-only research inputs, neither modified).

**Created**: `design/DAILY_NEWS_COHORT1_IMPLEMENTATION_DESIGN_2026_
09_15.md` (this document — untracked, not staged, not committed).

No code, config, schema, test, worker, or production-data file was
modified. No git operation beyond read-only status/log checks was
performed. No discovery run, scan, cache write, or database write
occurred. Nothing was staged, committed, pushed, merged, or deployed.
