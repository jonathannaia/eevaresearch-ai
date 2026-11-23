# Tier 1 Cohort 1 — Tracked-Company Implementation Design (2026-09-15)

Read-only implementation design. No file under `src/`, no test, no
schema, no `tracked_companies.py` entry, no worker schedule, no
deployment configuration, and no production data was modified to
produce this document. Starts from `origin/main` @ `4aaca8c` (post PR
#57, PR #58 — confirmed via `git log`/`git status` this session,
working tree clean except the 6 pre-existing untracked research
artifacts). Scopes exactly the 10 GO issuers approved in
`design/TIER1_COHORT1_REGISTRY_READINESS_2026_09_15.md`:

Hanmi Semiconductor, Nabtesco, Harmonic Drive Systems, Yaskawa
Electric, GE Vernova, HD Hyundai Electric, Digital Realty, Equinix,
Firefly Aerospace, L3Harris Technologies.

ASE Technology Holding and Iridium Communications (both HOLD in the
prior document) are **not** included. TSMC, ASML, Nanya Technology,
Winbond Electronics, Innolight, and Siemens Energy remain out of
scope entirely. See §5.

---

## 0. Grounding this design in the real codebase (not re-derived from memory)

This design reuses the exact `TrackedCompany` dataclass shape (`src/
config/tracked_companies.py`, read this session): `name`, `exchange`,
`krx_code`, `source`, `themes`, `subthemes`, `active`, `notes`,
`corp_code`, `native_name`. Three field-population conventions, each
verified directly against real existing entries and their matching
tests (`tests/test_tracked_companies.py`, read in full this session)
rather than assumed:

1. **`krx_code` is source-overloaded by design** — despite the name,
   it holds the SEC EDGAR ticker (e.g. `"NVDA"`), the DART 6-digit KRX
   stock code (e.g. `"005930"`), or the EDINET 5-character securities
   code (4-digit TSE code + trailing `"0"`, e.g. `"69540"` for FANUC).
   `test_edinet_cohort_preserves_source_native_five_character_
   securities_codes` asserts `len(c.krx_code) == 5` for every EDINET
   entry — a real, enforced format rule this design must satisfy.
2. **`corp_code` hardcoding is source-specific, not universal.**
   EDGAR and DART entries leave `corp_code=None` at add-time — it is
   resolved lazily at runtime (EDGAR via `with_resolved_ciks()` /
   `data/cache/edgar_ciks.json`; DART via `with_resolved_corp_codes()`)
   — confirmed by `test_edgar_cohort_identifiers_and_theme_mapping`'s
   own assertion (`for c in companies.values(): assert c.corp_code is
   None`) and `test_core_expansion_batch_dart_source_and_corp_code_
   not_hardcoded`. **Only EDINET entries hardcode `corp_code`**
   (`test_edinet_cohort_corp_code_is_hardcoded_not_none`), because
   Gate 2/Gate 6/Gate 7's live EDINET code-list verification already
   resolved them once and for all — there is no separate EDINET
   runtime-resolution cache the way EDGAR/DART have.
3. **Themes are a closed, 5-value set; subthemes must reuse existing
   vocabulary or stay unset — never invented in a registry-addition
   pass.** `test_core_expansion_batch_themes_use_only_existing_
   primary_themes` asserts `set(c.themes) <= {"ai-buildout",
   "humanoids", "space", "memory", "photonics"}`;
   `test_core_expansion_batch_subthemes_only_reuse_existing_
   vocabulary_or_stay_unset` asserts every subtheme string was already
   in use *before* that batch. This session independently re-derived
   the current full subtheme vocabulary by parsing the live registry
   (`src/config/tracked_companies.py`, 105 entries): exactly 10 values
   — `dram`, `hbm`, `compute-accelerators`, `industrial-automation`,
   `interconnect`, `interconnect-switching`, `launch`,
   `optical-components`, `power-cooling`, `semiconductor-test` —
   unchanged since the Core Issuer Expansion batch's own test fixed
   it. **No new subtheme string has entered the vocabulary since.**
   This one fact drives most of §4's "representable today vs. needs a
   taxonomy migration" split.

Also read this session: `src/config/issuer_registry.py` (the
`TrackedCompany` → `Issuer` bridge — `_issuer_id_for_tracked_company()`
builds `f"{source_prefix}:{tc.krx_code}"`, so `krx_code` uniqueness
*within a source* is load-bearing beyond the registry's own tests —
it is also the issuer-ID key; `_LISTING_JURISDICTION_BY_EXCHANGE` maps
`{"KRX", "NASDAQ", "NYSE", "NYSE American", "TSE"}` — every exchange
this cohort needs, `{KRX, NASDAQ, NYSE, TSE}`, is already covered, no
new exchange mapping required); `src/data_access/daily_news/
company_aliases.py` and its test file (the Daily News editorial-alias
system — see §4.2 for why this matters); `src/data_access/
company_discovery/candidate_pipeline.py`'s `_core_names()` (confirms
`native_name`, when set, **is** consulted for entity-resolution
matching in the discovery pipeline — a distinct, real mechanism from
the alias system, see §4.2); `src/data_access/edgar/edgar_rules.py`
(confirms today's adapter rule set needs no change for any of these
10 — see §3.2); `src/data_access/dart/client.py` (the DART corp_code
resolution path already used live this session).

---

## 1. Exact canonical registry records

Each record below is written in the exact literal syntax used
throughout `tracked_companies.py` today, ready to be inserted verbatim
by a future implementation batch (not inserted by this document).

### 1.1 SEC EDGAR (5 issuers)

```python
TrackedCompany(
    name="GE Vernova Inc.",
    exchange="NYSE",
    krx_code="GEV",
    source="SEC EDGAR",
    themes=("ai-buildout",),
    subthemes=("power-cooling",),
    notes=(
        "Tier 1 Cohort 1 batch (2026-09-15) — AI Buildout / power-grid "
        "bottleneck. CIK 0001996810 confirmed live via SEC EDGAR "
        "submissions API, 2026-09-15 (standard domestic filer: 8-K, "
        "10-Q, 11-K, Form 4, SD — 2024 spin-off from General Electric). "
        "corp_code left unresolved per existing SEC EDGAR convention — "
        "requires a separate cik_resolver.resolve_and_cache() gate "
        "against data/cache/edgar_ciks.json before scan-ready."
    ),
),
TrackedCompany(
    name="Digital Realty Trust, Inc.",
    exchange="NYSE",
    krx_code="DLR",
    source="SEC EDGAR",
    themes=("ai-buildout",),
    subthemes=(),
    notes=(
        "Tier 1 Cohort 1 batch (2026-09-15) — AI Buildout / datacenter "
        "infrastructure REIT, a first-order buildout-pace signal (direct "
        "development-pipeline/pre-lease disclosures) distinct from every "
        "other tracked ai-buildout name, which is a component/equipment "
        "supplier. CIK 0001297996 confirmed live, 2026-09-15 (Maryland "
        "incorporation, standard REIT filer: 8-K, 10-Q, Form 4, 424B7, "
        "Schedule 13G). No existing subtheme fits a REIT; left unset "
        "rather than reused inaccurately (see design/"
        "TIER1_COHORT1_TRACKED_COMPANIES_IMPLEMENTATION_DESIGN_2026_09_15"
        ".md §4 for the proposed 'datacenter-infrastructure-reit' "
        "future subtheme). corp_code left unresolved per convention."
    ),
),
TrackedCompany(
    name="Equinix, Inc.",
    exchange="NASDAQ",
    krx_code="EQIX",
    source="SEC EDGAR",
    themes=("ai-buildout",),
    subthemes=(),
    notes=(
        "Tier 1 Cohort 1 batch (2026-09-15) — AI Buildout / colocation "
        "and interconnection REIT, recommended alongside Digital Realty "
        "(DLR) deliberately, for interconnection-specific disclosure "
        "coverage rather than as a duplicate signal. CIK 0001101239 "
        "confirmed live, 2026-09-15 (Delaware incorporation, standard "
        "REIT filer: 8-K, 424B2, 424B5, Form 4/144). No subtheme fits; "
        "left unset. corp_code left unresolved per convention."
    ),
),
TrackedCompany(
    name="Firefly Aerospace Inc.",
    exchange="NASDAQ",
    krx_code="FLY",
    source="SEC EDGAR",
    themes=("space",),
    subthemes=("launch",),
    notes=(
        "Tier 1 Cohort 1 batch (2026-09-15) — Space / launch, a second, "
        "independent launch provider alongside the existing Rocket Lab "
        "(RKLB) entry for direct cadence/manifest comparison. CIK "
        "0001860160 confirmed live, 2026-09-15 (Delaware incorporation, "
        "standard domestic filer: Form 4, Schedule 13G/A, 424B3, 10-Q, "
        "8-K, Form D, Form 3 — reflects its 2025-08-07 IPO, not a "
        "foreign-filer pattern). corp_code left unresolved per "
        "convention."
    ),
),
TrackedCompany(
    name="L3Harris Technologies, Inc.",
    exchange="NYSE",
    krx_code="LHX",
    source="SEC EDGAR",
    themes=("space",),
    subthemes=(),
    notes=(
        "Tier 1 Cohort 1 batch (2026-09-15) — Space / satellite payloads "
        "and space-domain-awareness systems. Multi-segment company — "
        "its much larger defense-electronics business is NOT space-"
        "specific; event classification at implementation time should "
        "not assume every 8-K is space-relevant (same caveat already "
        "applied to Nabtesco's rail/aircraft segments, §1.3 of this "
        "document's companion readiness document). CIK 0000202058 "
        "confirmed live, 2026-09-15 (Delaware incorporation, formerly "
        "\"HARRIS CORP /DE/\" prior to the 2019 Harris/L3 merger; "
        "standard domestic filer: Form 144, Form 4, Form 3, 8-K, "
        "Schedule 13G/A). corp_code left unresolved per convention."
    ),
),
```

### 1.2 OpenDART / DART (2 issuers)

```python
TrackedCompany(
    name="Hanmi Semiconductor Co., Ltd.",
    exchange="KRX",
    krx_code="042700",
    source="OpenDART / DART",
    themes=("memory", "ai-buildout"),
    subthemes=(),
    notes=(
        "Tier 1 Cohort 1 batch (2026-09-15) — Memory / HBM thermal-"
        "compression bonding equipment, ~80% reported global share in "
        "this equipment category (trade press, TheElec/TrendForce, not "
        "independently re-verified). Secondary ai-buildout theme "
        "mirrors the existing SK Hynix/Samsung Electronics HBM-bridge "
        "pattern. DART corp_code independently confirmed live this "
        "session as 00161383 (2026-09-15, via DartClient."
        "fetch_all_corp_codes() against the real bulk corpCode.xml "
        "endpoint) — a cross-check value only; corp_code left "
        "unresolved here per the existing DART-entry convention (all "
        "10 current DART entries leave it None, resolved lazily via "
        "with_resolved_corp_codes()). Proposed subtheme "
        "'hbm-packaging-equipment' does NOT exist in today's subtheme "
        "vocabulary and is deliberately left unset rather than "
        "invented — see design/"
        "TIER1_COHORT1_TRACKED_COMPANIES_IMPLEMENTATION_DESIGN_2026_09_15"
        ".md §4.1 for the taxonomy-migration path, same "
        "'documented, not invented' discipline already established for "
        "the INDI/AIP/CEVA batch."
    ),
),
TrackedCompany(
    name="HD Hyundai Electric Co., Ltd.",
    exchange="KRX",
    krx_code="267260",
    source="OpenDART / DART",
    themes=("ai-buildout",),
    subthemes=("power-cooling",),
    notes=(
        "Tier 1 Cohort 1 batch (2026-09-15) — AI Buildout / transformer "
        "and grid-interconnect equipment, a bottleneck for new AI-"
        "datacenter power connections (trade press, general industry "
        "observation, not a specific named contract). Renamed from "
        "\"Hyundai Electric & Energy Systems Co., Ltd.\" in March 2023 "
        "(established public profile, not independently re-verified "
        "against a primary DART filing) — DART's own corp_eng_name "
        "field confirms the current legal name. DART corp_code "
        "independently confirmed live this session as 01205851 "
        "(2026-09-15) — cross-check value only, corp_code left "
        "unresolved per convention. `power-cooling` reused as the "
        "closest existing subtheme fit (imperfect — transformers are a "
        "narrower category than this tag's current broader usage)."
    ),
),
```

### 1.3 EDINET (3 issuers)

```python
TrackedCompany(
    name="Nabtesco Corporation",
    native_name="ナブテスコ株式会社",
    exchange="TSE",
    krx_code="62680",
    source="EDINET",
    themes=("humanoids",),
    subthemes=(),
    corp_code="E01726",
    notes=(
        "Tier 1 Cohort 1 batch (2026-09-15) — Humanoids / precision "
        "reduction-gear (RV/cycloidal) manufacturer for large-joint "
        "robot actuation, reported ~60% global RV-reducer share (trade "
        "press, not independently re-verified). EDINET code and "
        "securities code independently re-confirmed live this session "
        "(2026-09-15) against the official EDINET code-list bulk file "
        "(11,389 entries), reproducing the prior research document's "
        "result exactly. Multi-segment company — also reports rail/"
        "aircraft/construction-equipment segments; event classification "
        "should filter to the robotics-component segment specifically. "
        "Proposed subtheme 'precision-reducers' does NOT exist in "
        "today's vocabulary and is deliberately left unset — see §4.1."
    ),
),
TrackedCompany(
    name="Harmonic Drive Systems Inc.",
    native_name="株式会社ハーモニック・ドライブ・システムズ",
    exchange="TSE",
    krx_code="63240",
    source="EDINET",
    themes=("humanoids",),
    subthemes=(),
    corp_code="E01712",
    notes=(
        "Tier 1 Cohort 1 batch (2026-09-15) — Humanoids / strain-wave "
        "(harmonic-drive) gear manufacturer, the second dominant "
        "precision-reduction technology alongside Nabtesco's RV/"
        "cycloidal gears; together the two cover nearly the entire "
        "addressable reducer market for robot-joint actuation. New "
        "candidate, not present in either prior research document. "
        "EDINET code and securities code confirmed live this session "
        "(2026-09-15) against the official EDINET code-list bulk file — "
        "fiscal year end 3/31, industry classification 機械 "
        "(Machinery), listed. Also serves space and semiconductor-"
        "equipment end-markets — same multi-segment event-filtering "
        "caveat as Nabtesco. Proposed subtheme 'precision-reducers' "
        "(shared with Nabtesco) does NOT exist in today's vocabulary "
        "and is deliberately left unset — see §4.1."
    ),
),
TrackedCompany(
    name="YASKAWA Electric Corporation",
    native_name="株式会社安川電機",
    exchange="TSE",
    krx_code="65060",
    source="EDINET",
    themes=("humanoids",),
    subthemes=("industrial-automation",),
    corp_code="E01741",
    notes=(
        "Tier 1 Cohort 1 batch (2026-09-15) — Humanoids / one of the "
        "world's top-three industrial-robot OEMs and a major servo-"
        "motor/drive maker; the most surprising pre-existing single-"
        "name gap found in the coverage audit (FANUC already tracked "
        "as the registry's sole robot-OEM name; Yaskawa is a direct "
        "peer at comparable scale). EDINET code and securities code "
        "confirmed live this session (2026-09-15) — fiscal year end "
        "end-of-February, industry classification 電気機器 "
        "(Electric Machinery), listed. Two unrelated individual filers "
        "sharing the same surname (E08569, E40004) confirmed distinct "
        "by entity type (個人, 'individual', not 内国法"
        "人・組合, 'domestic corporation') and absence of "
        "a stock code — no collision risk. `industrial-automation` "
        "reused (already-existing subtheme, matches Doosan Robotics/"
        "Rockwell/Symbotic/Mitsubishi Electric usage) — fully "
        "representable under today's vocabulary, no migration needed."
    ),
),
```

---

## 2. Identity validation

### 2.1 How every identifier was verified

| Company | Identifier | Verification method | When |
|---|---|---|---|
| Hanmi Semiconductor | DART corp_code `00161383` | Live fetch, `DartClient.fetch_all_corp_codes()` against the real bulk `corpCode.xml` endpoint (119,281 records), matched by stock code `042700` | 2026-09-15, this session |
| HD Hyundai Electric | DART corp_code `01205851` | Same method, matched by stock code `267260` | 2026-09-15, this session |
| Nabtesco | EDINET code `E01726` | Live download/parse of the official EDINET code-list bulk file (`Edinetcode.zip`, 11,389 entries), matched by English filer name | 2026-09-15, this session (re-confirms prior research document's identical result) |
| Harmonic Drive Systems | EDINET code `E01712` | Same method, matched by English filer name "Harmonic Drive Systems Inc." | 2026-09-15, this session — **new**, not in either prior document |
| Yaskawa Electric | EDINET code `E01741` | Same method, matched by English filer name "YASKAWA Electric Corporation"; two unrelated individual-filer near-matches (`E08569`, `E40004`) explicitly ruled out by entity type | 2026-09-15, this session — **new**, not in either prior document |
| GE Vernova | CIK `0001996810` | Live fetch, `data.sec.gov/submissions/CIK0001996810.json` | 2026-09-15, this session (re-confirms prior research document) |
| Digital Realty | CIK `0001297996` | Same method | 2026-09-15, this session — **new** |
| Equinix | CIK `0001101239` | Same method | 2026-09-15, this session — **new** |
| Firefly Aerospace | CIK `0001860160` | Same method | 2026-09-15, this session (re-confirms prior research document exactly) |
| L3Harris Technologies | CIK `0000202058` | Same method | 2026-09-15, this session — **new** |

Every company's ticker and exchange were cross-checked against the
same live SEC EDGAR / EDINET / DART response that produced its
identifier (not sourced independently) — e.g. the DART record for
Hanmi Semiconductor carries both `corp_code` and `stock_code` in one
response row; the EDINET code-list row carries both the EDINET code and
the 5-character securities code together; the SEC submissions JSON
carries CIK, tickers, and exchanges in one document.

### 2.2 Collision check against the current 105-company registry

Run programmatically this session (not a text grep) against a fresh
`get_tracked_companies(active_only=False)` call:

- **`krx_code` collisions**: zero. Checked `042700`, `62680`, `63240`,
  `65060`, `GEV`, `267260`, `DLR`, `EQIX`, `FLY`, `LHX` against every
  existing `krx_code` in the live registry (105 values) — no match.
  This is the check that matters most given `krx_code` doubles as the
  issuer-ID key (§0) — a collision here would silently merge two
  distinct companies' signal streams under one `issuer_id`.
- **EDINET `corp_code` collisions**: zero. Checked `E01726`, `E01712`,
  `E01741` against every non-`None` `corp_code` currently in the
  registry (the 18 hardcoded EDINET values) — no match.
- **Exact `name` collisions**: zero. Checked all 10 proposed names
  against all 105 existing names.
- **`DISCOVERY_STUBS` collisions**: zero. Searched `issuer_registry.
  DISCOVERY_STUBS` for any stub whose `legal_name` contains "hanmi",
  "nabtesco", "harmonic drive", "yaskawa", "ge vernova"/"vernova",
  "hyundai electric", "digital realty", "equinix", "firefly", or
  "l3harris"/"harris" — no match. None of these 10 companies are
  already present as an unverified stub that would need to
  "graduate" (unlike the Arista/Cisco/Quanta/nVent precedent) — every
  one is a genuinely fresh addition.
- **Daily News alias/brand-alias collisions**: zero. Checked the three
  proposed brand-alias candidates from §4.2 ("HD Hyundai Electric",
  "Digital Realty", "L3Harris") against every existing company's
  mechanical `aliases` and curated `brand_aliases` from
  `build_company_alias_entries()` — no exact match against any
  existing alias string.

### 2.3 Padded-code convention compliance

| Company | Code | Convention | Compliant? |
|---|---|---|---|
| Hanmi Semiconductor | `krx_code="042700"` | DART 6-digit KRX stock code, leading zeros preserved as a string (matches Samsung `"005930"`, SK Hynix `"000660"`) | Yes |
| Hanmi Semiconductor | corp_code `00161383` (cross-check only) | DART 8-digit corp_code, leading zeros preserved | Yes |
| HD Hyundai Electric | `krx_code="267260"` | Same DART 6-digit convention | Yes (no leading zero needed) |
| HD Hyundai Electric | corp_code `01205851` (cross-check only) | Same DART 8-digit convention | Yes |
| Nabtesco | `krx_code="62680"` | EDINET 5-character securities code (4-digit TSE code `6268` + trailing `"0"`) | Yes — `len("62680") == 5`, matches `test_edinet_cohort_preserves_source_native_five_character_securities_codes`'s enforced rule |
| Harmonic Drive Systems | `krx_code="63240"` | Same EDINET 5-character convention (TSE code `6324` + `"0"`) | Yes |
| Yaskawa Electric | `krx_code="65060"` | Same EDINET 5-character convention (TSE code `6506` + `"0"`) | Yes |
| Nabtesco / Harmonic Drive Systems / Yaskawa | `corp_code="E01726"` / `"E01712"` / `"E01741"` | EDINET `E#####` format (matches every existing EDINET entry: `E01946`, `E02778`, `E35948`, etc.) | Yes |
| GE Vernova / Digital Realty / Equinix / Firefly Aerospace / L3Harris | CIK (cross-check only, not stored) | SEC's zero-padded 10-digit CIK string (`src/data_access/edgar/client.py`'s own `zfill(10)` convention) | Yes — `0001996810`, `0001297996`, `0001101239`, `0001860160`, `0000202058` are each already exactly 10 digits as returned live |

No format violation identified for any of the 10.

---

## 3. Expected code/test impact

### 3.1 Configuration files that would change in a later implementation

| File | Change |
|---|---|
| `src/config/tracked_companies.py` | Append the 10 `TrackedCompany(...)` literals from §1 to `TRACKED_COMPANIES`. **This is the only production config file this batch would touch.** |

No other production/config file requires a change — `issuer_registry.py`'s `SEED_ISSUERS` is generated programmatically from `TRACKED_COMPANIES` (confirmed by reading its own docstring and `_issuer_from_tracked_company()` this session), so it updates automatically with zero code change; `company_aliases.py`'s `daily_news_company_names()` is likewise data-driven from `get_tracked_companies()`, so all 10 companies become Daily-News-matchable automatically once added, with zero code change (see §4.2 for the one *optional* addition, the brand-alias overlay).

### 3.2 Whether existing source adapters require zero changes, with evidence

**Yes — zero adapter changes required for any of the 10.**

- **SEC EDGAR** (GE Vernova, Digital Realty, Equinix, Firefly
  Aerospace, L3Harris): this session read the complete `src/
  data_access/edgar/edgar_rules.py`. All 5 companies' real, live
  filing histories (fetched this session) consist entirely of forms
  already in `FORM_TYPE_CATEGORIES` or handled by the dedicated 8-K
  item-number path: `10-Q`, `8-K`, `Schedule 13G`/`13G-A`, `424B2`,
  `424B5`. **One minor, non-blocking, pre-existing gap observed**:
  Digital Realty's real recent history includes `424B7` (a prospectus
  supplement for resales by selling security holders), which is not
  itself a `FORM_TYPE_CATEGORIES` key (only `424B1`-`424B5` are) —
  immaterial to this company's core signal value since its `8-K`/`10-Q`
  disclosures are already fully covered, and not unique to this
  batch (the same gap would exist for any REIT filer). Not proposed
  as a fix in this document (out of scope — see §5).
- **OpenDART / DART** (Hanmi Semiconductor, HD Hyundai Electric):
  `dart_rules.py`'s candidate-promotion logic is a generic Korean-
  keyword lexicon matched against each disclosure's `report_nm`
  title, not tied to any per-company or per-form-type configuration —
  the identical path already serving all 10 current DART entries.
  Confirmed by the same read this session that resolved both
  companies' `corp_code`s using the existing, unmodified `DartClient`.
- **EDINET** (Nabtesco, Harmonic Drive Systems, Yaskawa Electric):
  candidate promotion is keyed by a generic `(ordinanceCode, formCode,
  docTypeCode)` triplet, not per-company — the same 3-triplet mapping
  (annual securities report, share buyback status, extraordinary
  report) already serving all 18 current EDINET entries.
  **Carried-forward limitation, not new to this batch**: this
  document did not pull a live document list for any of the 3 new
  EDINET filers to directly confirm their filings match the same 3
  triplets — a structural-analogy assumption, same caveat the prior
  research document already carried for Nabtesco specifically, now
  extended to Harmonic Drive Systems and Yaskawa Electric as well.

### 3.3 Minimal fixture/test plan

Proposed additions to `tests/test_tracked_companies.py`, matching the
file's own established per-batch pattern (a `_TIER1_COHORT1_*` name/
ticker mapping constant plus a small set of focused assertions,
exactly mirroring how `_EDGAR_BATCH_TICKERS`/`_DART_BATCH_TICKERS`/
`_EDINET_BATCH_TICKERS_TO_CODES` and `_BATCH_2_TICKERS_TO_NAMES` are
structured today):

```python
_TIER1_COHORT1_EDGAR_TICKERS = ("GEV", "DLR", "EQIX", "FLY", "LHX")
_TIER1_COHORT1_DART_TICKERS = ("042700", "267260")
_TIER1_COHORT1_EDINET_TICKERS_TO_CODES = {
    "62680": "E01726", "63240": "E01712", "65060": "E01741",
}
```

- `test_tier1_cohort1_present_exactly_once_each_and_active` — mirrors
  `test_core_expansion_batch_present_exactly_once_each_and_active`:
  each of the 10 `krx_code` values appears in `get_tracked_companies
  (active_only=True)` exactly once.
- `test_tier1_cohort1_legal_names` — mirrors
  `test_core_expansion_batch_legal_names`: exact-name assertion per
  ticker/code, using the §1 literal values verbatim.
- `test_tier1_cohort1_edgar_corp_code_not_hardcoded` /
  `test_tier1_cohort1_dart_corp_code_not_hardcoded` — mirrors the
  existing per-source `corp_code is None` assertions.
- `test_tier1_cohort1_edinet_corp_code_hardcoded` — mirrors
  `test_core_expansion_batch_edinet_source_and_corp_code_hardcoded`:
  `corp_code` equals the exact `E#####` value from §1 for each of the
  3 EDINET additions.
- `test_tier1_cohort1_edinet_securities_codes_are_five_characters` —
  new, but a direct extension of the existing
  `test_edinet_cohort_preserves_source_native_five_character_
  securities_codes` pattern applied to the 3 new EDINET entries
  specifically.
- `test_tier1_cohort1_themes_use_only_existing_primary_themes` /
  `test_tier1_cohort1_subthemes_only_reuse_existing_vocabulary_or_
  stay_unset` — direct reapplication of the Core Issuer Expansion
  batch's own two structural-guard tests to this batch.
- `test_tier1_cohort1_no_duplicate_identifiers_against_existing_
  registry` — direct reapplication of
  `test_core_expansion_batch_no_duplicate_identifiers_within_batch_
  or_against_existing_registry`'s exact assertion shape (no duplicate
  `krx_code` within a source, no duplicate non-`None` `corp_code`, no
  duplicate `name`) to this batch.
- `test_tier1_cohort1_hanmi_secondary_theme_mirrors_hbm_bridge_
  pattern` — asserts `Hanmi Semiconductor Co., Ltd.` themes equal
  `("memory", "ai-buildout")`, the same tuple shape/order already
  used for SK Hynix and Samsung Electronics.
- `test_tier1_cohort1_native_names_preserved_exactly` — asserts the
  3 EDINET entries' `native_name` values match the exact strings in
  §1, mirroring `test_edinet_cohort_preserves_japanese_legal_names_
  exactly`.
- `test_active_tracked_company_count_is_exactly_105` (existing test)
  would need its literal `105` updated to `115` as part of the same
  change — flagged explicitly here since it is a **modification to an
  existing test**, not a new one, and the one place this batch's
  addition is most likely to be missed if done incompletely.

Source-adapter-level fixtures (one company-scoped fixture per source,
proving scan-eligibility end-to-end rather than just registry
presence) — new, source-adapter test files, not `test_tracked_
companies.py`:

| Fixture | Source | What it proves |
|---|---|---|
| `hanmi_semiconductor_dart_capacity_disclosure_title` | DART | A DART `report_nm` fixture containing capacity/HBM-relevant Korean keywords promotes via the existing, unmodified lexicon — no company-specific code needed |
| `hd_hyundai_electric_dart_transformer_order_title` | DART | Same proof for a transformer/grid-equipment-styled title |
| `nabtesco_edinet_extraordinary_report_robotics_segment` | EDINET | Extraordinary-report triplet match promotes for a brand-new EDINET filer with no company-specific configuration |
| `harmonic_drive_edinet_annual_securities_report` | EDINET | Annual securities-report triplet match, new filer |
| `yaskawa_edinet_extraordinary_report` | EDINET | Same triplet path, industrial-robot-OEM context |
| `ge_vernova_8k_material_agreement` | SEC EDGAR | Standard 8-K item-number path |
| `digital_realty_10q_earnings` | SEC EDGAR | Standard 10-Q path |
| `digital_realty_424b7_unmapped_form_documents_not_promotes` | SEC EDGAR | Documents (does not fix) the minor §3.2 gap — asserts today's actual behavior, not a proposed fix |
| `equinix_8k_material_agreement` | SEC EDGAR | Standard 8-K path |
| `firefly_aerospace_8k_launch_manifest` | SEC EDGAR | Standard 8-K path, space-launch content |
| `l3harris_8k_non_space_segment_still_promotes` | SEC EDGAR | Documents (does not fix) the multi-segment filtering gap noted in §1.1 — an L3Harris 8-K with no space content still promotes today, since no segment filter exists |

### 3.4 Regression checks proving the 105 existing records retain their behavior

- Re-run `tests/test_tracked_companies.py` in full after the append —
  every existing assertion (`test_registry_contains_all_three_pilot_
  cohorts`, the per-batch identity/theme tests, the three duplicate-
  identifier guard tests) must still pass unchanged, since this batch
  is purely additive (new tuple entries appended, nothing existing
  reordered or edited).
- `test_active_tracked_company_count_is_exactly_105` must be updated
  (see §3.3) — this is an *expected*, deliberate change, not a
  regression, but the one place a future implementer could silently
  under- or over-count if the append is incomplete or duplicated.
- Re-run `tests/test_company_aliases.py` in full — every existing
  assertion should still pass unchanged (the 10 new companies flow
  into `daily_news_company_names()`/`build_company_alias_entries()`
  automatically; no existing company's alias output changes).
- Re-run `tests/test_issuer_registry.py` in full — `SEED_ISSUERS` is
  generated from `TRACKED_COMPANIES`, so the 10 new entries appear
  there automatically; existing entries' `Issuer` records are
  unaffected since `_issuer_from_tracked_company()` is a pure,
  per-entry mapping function with no cross-entry state.
- The full project regression sweep this session's own git-safety
  discipline has used for every prior registry-adjacent change
  (EDINET/DART/EDGAR adapter tests, Daily News tests, candidate-
  discovery tests) should be re-run once the batch is actually
  implemented — not run in this document, since no code change was
  made to run it against.

---

## 4. Editorial and graph readiness

### 4.1 Theme/subtheme representability today vs. a future taxonomy migration

| Company | Proposed tag | Representable today? |
|---|---|---|
| Hanmi Semiconductor | `memory` primary, `ai-buildout` secondary | **Yes** — both are existing primary themes, and the exact `("memory", "ai-buildout")` tuple shape/order already has precedent (SK Hynix, Samsung Electronics) |
| Hanmi Semiconductor | subtheme `hbm-packaging-equipment` | **No** — not in today's 10-value vocabulary; would require a taxonomy migration. Left unset in §1.2, with the intended subtheme documented in `notes` — the exact "report as a conflict, don't invent" discipline the INDI/AIP/CEVA batch already established for this codebase |
| Nabtesco, Harmonic Drive Systems | `humanoids` primary | **Yes** |
| Nabtesco, Harmonic Drive Systems | subtheme `precision-reducers` | **No** — same reasoning as Hanmi Semiconductor's subtheme; left unset, documented in `notes` |
| Yaskawa Electric | `humanoids` primary, subtheme `industrial-automation` | **Yes**, both — `industrial-automation` already exists (Doosan Robotics, Rockwell, Symbotic, Mitsubishi Electric) |
| GE Vernova, HD Hyundai Electric | `ai-buildout` primary, subtheme `power-cooling` | **Yes**, both — `power-cooling` is the registry's most heavily reused subtheme (Navitas, Bloom Energy, Quanta, nVent, Vertiv, Eaton, and now these two) |
| Digital Realty, Equinix | `ai-buildout` primary, no subtheme | **Partially** — the primary theme is representable, but the *segment* concept these two actually need ("datacenter infrastructure / REIT," per the coverage audit's own proposal) has no field in today's model at all: `TrackedCompany` has `themes`/`subthemes` only, no `segment` field. Left with `subthemes=()` in §1.1 rather than force-fitting an inaccurate existing subtheme |
| Firefly Aerospace | `space` primary, subtheme `launch` | **Yes** — `launch` already exists (Rocket Lab) |
| L3Harris | `space` primary, no subtheme | **Yes** for the primary theme; no subtheme forced (none fit; correctly left unset, not a taxonomy gap) |

**Summary**: 7 of 10 companies are fully representable today with no
taxonomy change (GE Vernova, HD Hyundai Electric, Yaskawa Electric,
Firefly Aerospace, Digital Realty, Equinix, L3Harris — the last three
at the theme level, with subtheme/segment left correctly empty rather
than forced). **3 companies have a real subtheme gap** (Hanmi
Semiconductor, Nabtesco, Harmonic Drive Systems) requiring a future,
separate taxonomy-migration decision to add `hbm-packaging-equipment`
and `precision-reducers` to the vocabulary — not decided or
implemented in this document. **A `segment` field does not exist in
today's model at all** — Digital Realty/Equinix are the first
candidates in this cohort to expose that gap concretely (every prior
`ai-buildout` addition has been a component/equipment company that
fits the existing `themes`/`subthemes` shape; a REIT does not).

### 4.2 Aliases — what would materially improve matching, and what would not

Two independent, real mechanisms exist in the codebase for a company
name to be "found" by downstream systems — this session read both in
full and distinguishes them precisely, since the task's framing
("aliases... including Korean/Japanese legal names") could otherwise
be answered incorrectly by conflating them:

1. **Daily News editorial-alias matching** (`company_aliases.py` /
   `editorial_matching.py`): purely mechanical — the exact `name`
   string plus one legal-suffix-stripped form, matched against
   English-language editorial article text with regex word
   boundaries. `native_name` is **never read** by this module
   (confirmed structurally by `test_no_ticker_field_is_ever_
   consulted`'s sibling discipline and by reading the module's own
   source — no `.native_name` reference exists in `company_aliases.
   py`). A curated `_BRAND_ALIAS_OVERLAY` exists for exactly 3
   companies today (`Amazon Web Services`/`AWS`, `Google`, `IBM`) —
   each added only after explicit human review for low collision
   risk. **A Korean- or Japanese-script alias would be inert here** —
   there is no mechanism to match non-Latin-script text against
   (presumably English-language) Daily News article text, so adding
   one would not "materially improve matching" in this system; it
   would do nothing.
   - For the 10 companies in this batch, the mechanical 1-2 alias
     generation already produces a clean, adequate result for all 10
     (e.g. `"Hanmi Semiconductor Co., Ltd."` → `"Hanmi Semiconductor"`;
     `"L3Harris Technologies, Inc."` → `"L3Harris Technologies"`).
   - **Three candidates are proposed for the curated overlay**,
     meeting the existing bar (multi-word or well-established-acronym,
     low ambiguity) — proposed here for a future, separately-approved
     review, not decided by this document:
     - `"HD Hyundai Electric Co., Ltd."` → `("Hyundai Electric &
       Energy Systems",)` — its real pre-2023 legal name; older
       editorial coverage plausibly still uses it. Multi-word, no
       ambiguity risk.
     - `"Digital Realty Trust, Inc."` → `("Digital Realty",)` — a
       very common trade-press shorthand that the mechanical suffix
       strip cannot reach (`"Trust"` is not a recognized legal
       suffix). Multi-word, no ambiguity risk.
     - `"L3Harris Technologies, Inc."` → `("L3Harris",)` — a
       single token, but not an ambiguous common word (same
       reasoning already accepted for the existing `"IBM"` entry).
     - Checked this session: none of the three collide with any
       existing company's `aliases` or `brand_aliases` (§2.2).
2. **Company-discovery entity resolution** (`candidate_pipeline.py`'s
   `_core_names()`): genuinely different and genuinely native-name-
   aware — it builds a normalized-name set from **both** `TrackedCompany.
   name` **and** `TrackedCompany.native_name` (when set), used to
   recognize when a discovered candidate entity is actually an
   already-tracked company under its native-script name. **This is
   where the 3 EDINET `native_name` values in §1.3 materially matter**
   — populating them correctly (already done, live-verified) directly
   extends this set for Nabtesco, Harmonic Drive Systems, and Yaskawa
   Electric, exactly matching every existing EDINET entry's own
   convention (FANUC's `native_name="ファナック株式会社"`, etc.).
   `native_name` is left empty (its dataclass default) for the 7
   non-EDINET companies in this batch, matching `test_native_name_
   defaults_to_empty_for_non_edinet_entries`'s existing invariant.

### 4.3 First evidence-backed relationship/event-trigger record proposed per issuer (not added here)

Each is the single most concrete, evidence-tiered starting point a
future relationship-graph/event-monitoring implementation should add
first for that issuer — named here for planning continuity only, per
the explicit instruction not to add relationship data in this
registry implementation:

| Company | Proposed first record | Evidence tier |
|---|---|---|
| Hanmi Semiconductor | Hanmi–SK Hynix TC-bonder order, ~44.2bn won, dated 2026-06 | **Trade press** (TheElec, TrendForce) — requires verification before any stronger tier |
| Nabtesco | EDINET extraordinary-report filing tied to robotics-segment capacity expansion (event-trigger type, not yet a specific dated filing) | **Primary/regulatory**, once observed live |
| Harmonic Drive Systems | Same event-trigger type (EDINET extraordinary/annual-report capacity disclosure) | **Primary/regulatory**, once observed live |
| Yaskawa Electric | EDINET annual/extraordinary report naming humanoid or next-generation robotics program status specifically (distinct from its much larger existing industrial-arm business) | **Primary/regulatory**, once observed live |
| GE Vernova | 8-K/10-Q gas-turbine order-backlog disclosure tied to an AI-datacenter power-supply agreement | **Primary/regulatory**, once observed live |
| HD Hyundai Electric | DART disclosure naming a transformer/grid-equipment supply contract tied to a datacenter developer | **Primary/regulatory**, once observed live |
| Digital Realty | 8-K pre-lease or development-pipeline announcement naming a hyperscaler tenant | **Primary/regulatory**, once observed live |
| Equinix | 8-K interconnection-capacity or pre-lease announcement | **Primary/regulatory**, once observed live |
| Firefly Aerospace | 8-K launch-manifest update, read as a `comparison_peer`-type signal alongside the already-tracked Rocket Lab (not a supplier/customer edge) | **Primary/regulatory**, once observed live |
| L3Harris | 8-K disclosure naming a space-segment contract award, filtered from its larger defense-electronics business | **Primary/regulatory**, once observed live |

No relationship record, edge, or event was created, stored, or staged
anywhere by this document.

---

## 5. Explicit non-goals

- ASE Technology Holding, Iridium Communications, TSMC, ASML, Nanya
  Technology, Winbond Electronics, Innolight, Siemens Energy — none
  named, added, or acted upon beyond already being out of scope.
- No foreign-issuer 6-K filename-gate change proposed or designed
  (the opaque-filename finding from the companion readiness document
  is not revisited here — none of these 10 companies are 20-F/6-K
  filers).
- No relationship-graph, database, or schema work — §4.3 names
  candidates for future planning only, per instruction.
- No Daily News source expansion — the 10 companies become Daily-
  News-matchable automatically via the existing data-driven `daily_
  news_company_names()` mechanism once added to the registry; no new
  source, feed, or matching code is proposed.
- No code, config, schema, test, or production-data file was created,
  modified, staged, committed, pushed, merged, or deployed by this
  document. The §1 `TrackedCompany` literals and §3.3 test sketches
  are proposed text for a future, separately-approved implementation
  pass — not applied here.

---

## Files read / created

**Read this session (all read-only)**: `src/config/tracked_companies.py`
(dataclass definition and representative entries across all three
sources), `tests/test_tracked_companies.py` (full file), `src/config/
issuer_registry.py` (the `TrackedCompany` → `Issuer` bridge, `_issuer_
id_for_tracked_company()`, `_LISTING_JURISDICTION_BY_EXCHANGE`), `src/
data_access/daily_news/company_aliases.py` (full file) and `tests/
test_company_aliases.py` (full file), `src/data_access/company_
discovery/candidate_pipeline.py` (`_core_names()`/`_core_issuer_id_
for_name()`), `src/data_access/edgar/edgar_rules.py` (full file, via
this document's own companion readiness document's earlier read),
`src/data_access/edgar/client.py` (CIK zero-padding convention), `src/
data_access/dart/client.py`, `data/cache/edgar_ciks.json` (confirmed
none of the 5 new EDGAR tickers are yet cached). Live external
verification this session: SEC EDGAR submissions API (5 CIKs,
re-confirming 2 from the prior document and newly resolving 3),
EDINET's official code-list bulk file (3 codes, re-confirming 1 and
newly resolving 2), OpenDART's live corp-code bulk endpoint via the
project's own `DartClient`, used read-only (2 corp_codes, both newly
resolved this session).

**Not touched**: `design/COVERAGE_TAXONOMY_AUDIT_2026_09_15.md`,
`design/COVERAGE_EXPANSION_CANDIDATES_TIER1_2026_09_15.md`,
`design/COVERAGE_EXPANSION_FIRST10_REGISTRY_READINESS_2026_09_15.md`,
`design/RESEARCH_THESES_ACTIVE_DORMANT.md`,
`design/THEMATIC_COVERAGE_UNIVERSE_AUDIT_2026_09_15.md`,
`design/TIER1_COHORT1_REGISTRY_READINESS_2026_09_15.md`,
`tests/test_radar_status.py` — none re-read or modified this turn.

**Created**: `design/
TIER1_COHORT1_TRACKED_COMPANIES_IMPLEMENTATION_DESIGN_2026_09_15.md`
(this document — untracked, not staged, not committed).

No code, config, schema, test, worker-schedule, deployment, or
production-data file was modified. No git operation beyond read-only
status/log checks was performed. Nothing was staged, committed,
pushed, merged, or deployed.
