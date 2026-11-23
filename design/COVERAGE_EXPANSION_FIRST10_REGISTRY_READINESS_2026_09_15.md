# Registry-Readiness Verification — First 10 Candidates (2026-09-15)

Phase 3 of the coverage-expansion work. Read-only, research-only. No
file under `src/`, no test, no schema, and no production data was
modified to produce this document.

## Headline finding — please read before the per-candidate sections

**TSMC and ASML cannot be added as-is.** Both are foreign private
issuers whose entire real filing history on SEC EDGAR consists of
**6-K** and **20-F** filings — confirmed directly against live,
primary-source SEC data fetched today (not assumed, not carried over
from Phase 2). Direct code inspection of
`src/data_access/edgar/edgar_rules.py`'s `FORM_TYPE_CATEGORIES` dict —
the *only* table `evaluate_form_type()` consults to decide whether a
filing gets promoted from a bare `FilingEvent` to a reviewable
`CandidateSignal` — shows **zero entries for `20-F` or `6-K`**. Every
key is a domestic US form: `10-K`, `10-Q`, `SC 13D`/`13D-A`/`13G`/
`13G-A`, `S-1`, `S-3`, `424B1`–`424B5`. `8-K` is handled by a separate
special case in `_evaluate_row()`, also irrelevant to a foreign private
issuer that never files one.

Tracing `scan_service.py`'s own `scan()` loop confirms the mechanical
consequence precisely: there is **no form-type filter at the
`FilingEvent`-creation step at all** — every row in a company's
`filings.recent` window becomes a `FilingEvent` regardless of form
type (line ~295: `new_filing_events.append(filing)`, unconditional).
Only the *separate* `CandidateSignal` promotion step depends on
`evaluate_form_type()`'s result (`if evaluation.confidence is not
None:`). So TSMC/ASML filings would ingest without error and appear as
inert "New filing" cards in Radar — **but would never once be promoted
to a reviewable candidate signal**, silently, for every single filing,
forever, under today's code.

**"Do not assume the Arm precedent is sufficient" was the right
instruction.** `tracked_companies.py`'s own note for Arm Holdings
("SEC-registered as 'ARM HOLDINGS PLC /UK'... cross-check passed
against its real submissions metadata regardless") documents only that
Arm's **CIK resolution** succeeded for a foreign filer — a narrower
claim than form-type classification. Direct search of this repo's
EDGAR code and test suite for `"20-F"` or `"6-K"` returns **zero
matches anywhere** — this gap has never been tested or exercised, for
Arm or anyone else. Arm Holdings is very likely silently affected by
the exact same gap today; this document does not attempt to confirm
that (Arm's own live production filing history is out of scope here),
but it means Arm cannot be cited as proof this path already works.

This finding drives both TSMC's and ASML's outcome below:
**REQUIRES_ADAPTER_CHANGE** — not a rejection, but a precise,
narrowly-scoped fix needed first (add `20-F` and `6-K` entries to
`FORM_TYPE_CATEGORIES`, with sensible category mappings — e.g. `20-F`
≈ `earnings_or_results` given it's the annual-report equivalent, `6-K`
plausibly needs its own coarse category or an items-style refinement
mirroring 8-K's treatment, itself worth a short separate design
decision, not assumed here).

---

## Per-candidate verification

### 1. Nabtesco Corporation

- **Legal registrant name**: Nabtesco Corporation (English); ナブテスコ株式会社 (Japanese, native)
- **Ticker/security code/exchange/jurisdiction**: Securities code 6268 (4-digit TSE form) / 62680 (5-digit EDINET form, per the project's own documented "4-digit TSE code + trailing 0" convention); TSE; Japan.
- **Eeva-compatible source identity — EDINET**: **EDINET code E01726**, confirmed directly against EDINET's own official code-list bulk file (`https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip`), downloaded and parsed live in this session, dated 2026-09-15 (11,389 total entries in the file). Row: `E01726, 内国法人・組合, 上場 (Listed), 有, ...,ナブテスコ株式会社, Nabtesco Corporation, ..., 62680, 3010001142283 (corporate number)`. **This is the same public, key-free URL `edinet_code_resolver.py`'s own `PROVISIONAL_CODE_LIST_URL` constant points to** — the code fetched here is the exact data the app's own resolver would produce.
- **Can the current adapter ingest without code changes?** Mechanically yes, with one important, pre-existing, non-Nabtesco-specific caveat: EDINET candidate promotion (`edinet_rules.py`'s `DEFAULT_CODE_CATEGORY_MAP`) is keyed by a generic `(ordinanceCode, formCode, docTypeCode)` triplet — a standardized Japanese regulatory code, not a per-company one. Only 3 real triplets are currently mapped (annual securities report, share buyback status, extraordinary report), all verified live against SoftBank Group's own filings. Because these triplets are standardized document-type codes, not SoftBank-specific ones, Nabtesco's own annual securities reports and extraordinary reports would very likely match the *same* existing triplets — but this document did not pull a live Nabtesco filing to directly confirm it. This is a pre-existing, general EDINET-coverage limitation shared by all 18 currently-tracked EDINET companies, not a new gap Nabtesco introduces.
- **Existing aliases/collisions**: none. Direct grep of `tracked_companies.py`, `issuer_registry.py`, and `company_aliases.py` for "Nabtesco" returns zero matches.
- **Theme mapping**: Primary: humanoids. Secondary: none proposed (no ai-buildout tie found beyond generic industrial supply-chain adjacency, deliberately not asserted). Subtheme: propose `precision-reducers` (new — no existing subtheme fits). Segment: Reducers. Supply-chain role: precision reduction-gear (RV/cycloidal) manufacturer for large-joint robot actuation.
- **Second-order signal rationale**: reported ~60% global market share in RV reducers for industrial/humanoid robot joints (industry-analysis sourced, not independently re-verified here) — a capacity or order-backlog signal here is a leading indicator across essentially every humanoid/industrial-robotics OEM.
- **Events to monitor**: EDINET extraordinary reports tied to capacity expansion or major customer agreements in the robotics-component segment specifically (Nabtesco also reports rail/aircraft/construction-equipment segments — event filtering would need to distinguish these); annual securities report segment breakdowns.
- **Outcome**: **READY_PENDING_IDENTITY_CONFIRMATION** — EDINET code independently confirmed against the live official code list; not yet cross-checked by running this project's own `edinet_code_resolver.resolve_and_cache()` against it (a mechanical, separate step this document did not execute, consistent with "no live scan").

### 2. Taiwan Semiconductor Manufacturing Company Limited

- **Legal registrant name**: "TAIWAN SEMICONDUCTOR MANUFACTURING CO LTD" (exact SEC EDGAR registrant name, confirmed live).
- **Ticker/exchange/jurisdiction**: TSM (also OTC: TSMWF), NYSE, Taiwan-domiciled.
- **Eeva-compatible source identity — SEC EDGAR**: **CIK 0001046179**, confirmed directly by fetching `https://data.sec.gov/submissions/CIK0001046179.json` live in this session (2026-09-15). **Expected filing pattern, confirmed from the real, current filing history**: 6-K (most recent: 2026-09-10, 2026-09-09 — filed very frequently), 20-F (annual, most recent 2026-01-23), plus SD, Schedule 13G/13G-A, S-8, and older 424B/F-3ASR/FWP entries. **Zero 10-K, 10-Q, or 8-K filings exist in this company's history** — confirming it is a pure foreign-private-issuer filer.
- **Can the current adapter ingest without code changes?** **No — see the headline finding above.** `FilingEvent` creation itself would work (the submissions API returns the same `accessionNumber`/`filingDate`/`form`/`primaryDocument` fields regardless of form type), but `evaluate_form_type("6-K")` and `evaluate_form_type("20-F")` both return `confidence=None` today — every TSMC filing would silently never reach `CandidateSignal` status.
- **Existing aliases/collisions**: none (grep confirmed zero matches for "Taiwan Semiconductor" / "TSMC" / "TSM" across the registry files).
- **Theme mapping**: Primary: ai-buildout. Secondary: none. Subtheme: propose `foundry-manufacturing` (new). Segment: Foundry. Supply-chain role: contract wafer foundry (pure-play, world's largest by revenue and process-node leadership).
- **Second-order signal rationale**: capacity/capex disclosures are a leading indicator for demand-side commentary from every fabless AI-accelerator designer already tracked (NVIDIA, AMD, Broadcom, Arm).
- **Events to monitor**: capex guidance (typically disclosed via 6-K earnings-call materials), new-node/new-fab capacity announcements, major customer risk-factor disclosures in the 20-F, export-control/geopolitical risk disclosures.
- **Outcome**: **REQUIRES_ADAPTER_CHANGE** — identity fully confirmed; the blocker is exclusively the `FORM_TYPE_CATEGORIES` gap documented above.

### 3. Tesla, Inc.

- **Legal registrant name**: "Tesla, Inc." (exact SEC EDGAR registrant name, confirmed live).
- **Ticker/exchange/jurisdiction**: TSLA, NASDAQ, US-domiciled (Texas-incorporated/headquartered).
- **Eeva-compatible source identity — SEC EDGAR**: **CIK 0001318605**, confirmed directly by fetching `https://data.sec.gov/submissions/CIK0001318605.json` live (2026-09-15). **Filing pattern, confirmed live**: 10-Q, 8-K, 10-K/A, plus 4/144/Schedule 13G/13G-A/S-8/3/DEFA14A — **entirely standard domestic-filer forms**, all already fully handled by `FORM_TYPE_CATEGORIES`/the 8-K special case.
- **Can the current adapter ingest without code changes?** **Yes.** No adapter gap of any kind.
- **Existing aliases/collisions**: none.
- **Theme mapping**: Primary: humanoids. Secondary theme candidate: ai-buildout is plausible given Tesla's own AI/compute investments (Dojo, in-house silicon) but this document does not assert it — **RESEARCH QUESTION — requires verification** before adding as a second theme. Subtheme: none proposed (no existing subtheme fits a humanoid-OEM company; propose leaving unset, matching this project's own precedent for e.g. Redwire). Segment: OEM. Supply-chain role: humanoid-robot platform developer (Optimus program).
- **Second-order signal rationale**: as the OEM, Tesla's own component-sourcing and production-ramp disclosures would be a leading indicator for demand at any actuator/motor/reducer/sensor supplier this registry later adds.
- **Maturity caveat, carried forward from Phase 2 and unchanged**: as of mid-2026 the Optimus program remains pre-revenue and explicitly "research phase" per the company's own Q4 2025 earnings-call commentary (per Phase 2's trade-press-sourced research, not independently re-verified against a primary transcript in this session).
- **Events to monitor**: 10-Q/10-K segment or risk-factor commentary specifically naming Optimus/humanoid-robot production status, 8-K disclosures tied to Optimus manufacturing milestones, named supplier disclosures if any appear in filings.
- **Outcome**: **READY_FOR_REGISTRY_ADD** — identity and adapter compatibility both fully confirmed against live primary sources; no blocker identified.

### 4. ASML Holding N.V.

- **Legal registrant name**: "ASML HOLDING NV" (exact SEC EDGAR registrant name, confirmed live).
- **Ticker/exchange/jurisdiction**: ASML (also OTC: ASMLF), NASDAQ, Netherlands-domiciled.
- **Eeva-compatible source identity — SEC EDGAR**: **CIK 0000937966**, confirmed directly by fetching `https://data.sec.gov/submissions/CIK0000937966.json` live (2026-09-15). **Filing pattern, confirmed live**: 6-K (most recent: 2026-07-15, 2026-06-05), 20-F (annual, most recent fiscal-year-end 2025-12-31), plus SD, S-8, S-8 POS, Schedule 13G/13G-A, and older 8-A12B/A, F-4/F-4A, 424B3, 425, F-3 entries. **Zero 10-K, 10-Q, or 8-K.**
- **Can the current adapter ingest without code changes?** **No — identical gap to TSMC.** Same `FORM_TYPE_CATEGORIES` blocker.
- **Existing aliases/collisions**: none.
- **Theme mapping**: Primary: ai-buildout. Secondary: none. Subtheme: propose `lithography-equipment` (new). Segment: Tools/materials (specifically the EUV lithography sub-segment — a single-supplier global bottleneck). Supply-chain role: sole global supplier of extreme-ultraviolet (EUV) lithography systems.
- **Second-order signal rationale**: EUV tool order backlog/shipment timing is a direct leading indicator for leading-edge capacity additions at every foundry/IDM in the registry (Samsung Electronics, SK Hynix, Micron, Tower Semiconductor, GlobalFoundries, TSMC if added).
- **Events to monitor**: EUV/High-NA EUV shipment and order disclosures (typically 6-K earnings materials), export-control developments (Dutch government licensing of EUV exports to China has been a recurring, market-moving event type), capex guidance.
- **Outcome**: **REQUIRES_ADAPTER_CHANGE** — identity fully confirmed; same blocker as TSMC.

### 5. Hanmi Semiconductor Co., Ltd.

- **Legal registrant name**: Hanmi Semiconductor Co., Ltd. (English, per Phase 2's trade-press sources).
- **Ticker/security code/exchange/jurisdiction**: KRX 042700, South Korea.
- **Eeva-compatible source identity — OpenDART**: **Not independently confirmed in this session.** DART's bulk `corpCode.xml` endpoint requires an API key (`crtfc_key`, per direct inspection of `src/data_access/dart/client.py:112-115` — `_require_key()` then `self._get("corpCode.xml", {"crtfc_key": key})`); this environment has no configured `EDGE_DART_API_KEY` (confirmed blank in the local `.env` in an earlier session). DART's own public human-facing search portal (`dart.fss.or.kr`) is JavaScript-rendered and did not return usable results via this session's fetch tooling. **The 6-digit KRX stock code (042700) is independently confirmed** via multiple financial-data sources in Phase 2; the separate 8-digit DART corp_code is not.
- **Can the current adapter ingest without code changes?** Cannot be fully confirmed without the corp_code, but no code-level gap is expected: DART's own candidate-promotion logic (`dart_rules.py`) is a generic Korean-keyword lexicon matched against `report_nm` text, not tied to any per-company or per-form-type configuration — the same code path already serving all 10 tracked KRX companies would apply unchanged.
- **Existing aliases/collisions**: none (grep confirmed zero matches for "Hanmi").
- **Theme mapping**: Primary: memory. Secondary theme candidate: ai-buildout (HBM feeds AI accelerator memory bandwidth — same bridge logic already applied to Samsung Electronics/SK Hynix). Subtheme: propose `hbm-packaging-equipment` (new). Segment: Packaging/TSV. Supply-chain role: thermal-compression (TC) bonding equipment for HBM stacking, reported ~80% global market share in this specific equipment category (industry-analysis sourced).
- **Second-order signal rationale**: as the reported-dominant HBM TC-bonder supplier, Hanmi's own order backlog is a direct, near-real-time leading indicator for HBM4/HBM5 production ramp timing at Samsung Electronics and SK Hynix, both already tracked.
- **Events to monitor**: named customer order announcements, new-generation equipment launches (e.g. "Wide TC Bonder" for HBM5/HBM6, per Phase 2 research), capacity-expansion disclosures.
- **Relationship evidence**: Phase 2 found two independent trade publications (TheElec, TrendForce) reporting a specific, dated Hanmi–SK Hynix TC-bonder order (~44.2 billion won, dated 2026-06). **This remains RESEARCH QUESTION — requires verification** — trade press, not a primary DART/company filing, and this document did not upgrade it despite being unusually specific and dated, per instruction.
- **Outcome**: **READY_PENDING_IDENTITY_CONFIRMATION** — KRX stock code confirmed; DART corp_code specifically requires either an OpenDART API key or a different verification path not available in this session.

### 6. HD Hyundai Electric Co., Ltd. (formerly Hyundai Electric & Energy Systems Co., Ltd.)

- **Legal registrant name**: HD Hyundai Electric Co., Ltd. — **name-change note**: the company changed its name from "Hyundai Electric & Energy Systems Co., Ltd." to "HD Hyundai Electric Co., Ltd." in March 2023 (per Phase 2 research, Wikipedia-sourced, not independently re-verified against a primary DART filing in this session). **Any future registry entry should use the current legal name, not the name used in Phase 2's original candidate research.**
- **Ticker/security code/exchange/jurisdiction**: KRX 267260, South Korea.
- **Eeva-compatible source identity — OpenDART**: **Not independently confirmed in this session** — identical limitation to Hanmi Semiconductor above (no API key available; DART's public search UI not fetchable via this session's tooling). KRX stock code 267260 is independently confirmed via multiple financial-data sources across both Phase 2 and this session.
- **Can the current adapter ingest without code changes?** Same reasoning as Hanmi Semiconductor — no code-level gap expected (DART's generic keyword-matching path applies), pending the corp_code itself.
- **Existing aliases/collisions**: none for either "HD Hyundai Electric" or "Hyundai Electric & Energy Systems" (grep confirmed).
- **Theme mapping**: Primary: ai-buildout. Secondary: none. Subtheme: `power-cooling` reused (closest existing fit, imperfect — the existing tag covers a broad power/cooling category already used for Navitas/Bloom Energy/Vertiv/etc.; transformers are a narrower fit within it). Segment: Power/grid/generation (transformer/grid-interconnect-equipment sub-segment specifically). Supply-chain role: transformers, switchgear, and grid-interconnect equipment manufacturer.
- **Second-order signal rationale**: transformer order backlog and delivery lead times are a widely-reported bottleneck for new AI datacenter power connections.
- **Events to monitor**: order backlog disclosures, capacity-expansion announcements, major grid-equipment supply contracts.
- **Outcome**: **READY_PENDING_IDENTITY_CONFIRMATION** — KRX code confirmed; DART corp_code pending, same limitation as Hanmi Semiconductor.

### 7. GE Vernova Inc.

- **Legal registrant name**: "GE Vernova Inc." (exact SEC EDGAR registrant name, confirmed live).
- **Ticker/exchange/jurisdiction**: GEV, NYSE, US-domiciled (2024 spin-off from General Electric).
- **Eeva-compatible source identity — SEC EDGAR**: **CIK 0001996810**, confirmed directly by fetching `https://data.sec.gov/submissions/CIK0001996810.json` live (2026-09-15). **Filing pattern, confirmed live**: 8-K, 10-Q, 11-K, plus 4/SD/144/ARS/DEFA14A/DEF 14A/Schedule 13G-A — **entirely standard domestic-filer forms**, already fully handled.
- **Can the current adapter ingest without code changes?** **Yes.**
- **Existing aliases/collisions**: none.
- **Theme mapping**: Primary: ai-buildout. Secondary: none. Subtheme: `power-cooling` reused (same precedent as Navitas/Bloom Energy/Quanta/nVent/Vertiv/Eaton). Segment: Power/grid/generation. Supply-chain role: gas turbines, grid equipment, and electrification systems manufacturer.
- **Second-order signal rationale**: gas-turbine order backlog and delivery lead times are widely cited as a bottleneck for new AI datacenter power availability.
- **Events to monitor**: turbine order backlog disclosures (8-K/10-Q), grid-equipment capacity-expansion announcements, major AI-datacenter power-supply agreements.
- **Outcome**: **READY_FOR_REGISTRY_ADD** — identity and adapter compatibility both fully confirmed against live primary sources.

### 8. Kulicke & Soffa Industries, Inc.

- **Legal registrant name**: "KULICKE & SOFFA INDUSTRIES INC" (exact SEC EDGAR registrant name, confirmed live).
- **Ticker/exchange/jurisdiction**: KLIC, NASDAQ, US-domiciled (Pennsylvania-incorporated).
- **Eeva-compatible source identity — SEC EDGAR**: **CIK 0000056978**, confirmed directly by fetching `https://data.sec.gov/submissions/CIK0000056978.json` live (2026-09-15). **Filing pattern, confirmed live**: 4, 3, 8-K, 10-Q, 10-K, Schedule 13G, 144, DEF 14A, ARS, S-8 — **entirely standard domestic-filer forms**.
- **Can the current adapter ingest without code changes?** **Yes.**
- **Existing aliases/collisions**: none.
- **Theme mapping**: Primary: ai-buildout. Secondary theme candidate: memory (packaging equipment serves memory assembly too, same cross-theme logic as Amkor/Ibiden already tracked under ai-buildout only) — **RESEARCH QUESTION — requires verification** before adding as a formal second theme. Subtheme: propose `wire-bonding-packaging-equipment` (new — the existing `semiconductor-test` subtheme is an imperfect fit; wire-bonding/assembly is a distinct process step from post-fabrication test). Segment: Advanced packaging/test. Supply-chain role: world's largest wire-bonding and semiconductor assembly equipment maker.
- **Second-order signal rationale**: order flow here is a direct leading indicator for AI-chip assembly capacity additions across the already-tracked OSAT cohort (Amkor, SFA Semicon, Hana Micron, Ibiden).
- **Events to monitor**: order-backlog disclosures tied to AI semiconductor demand specifically, new advanced-packaging product launches.
- **Outcome**: **READY_FOR_REGISTRY_ADD** — identity and adapter compatibility both fully confirmed against live primary sources.

### 9. Amphenol Corporation

- **Legal registrant name**: "AMPHENOL CORP /DE/" (exact SEC EDGAR registrant name, confirmed live — the "/DE/" suffix is SEC's own disambiguation notation for the Delaware-incorporated entity, not part of the company's actual legal name in ordinary usage).
- **Ticker/exchange/jurisdiction**: APH, NYSE, US-domiciled.
- **Eeva-compatible source identity — SEC EDGAR**: **CIK 0000820313**, confirmed directly by fetching `https://data.sec.gov/submissions/CIK0000820313.json` live (2026-09-15). **Filing pattern, confirmed live**: 8-K, 4, Schedule 13G-A, 144, CERT, 8-A12B, 424B5, FWP, 424B3, 10-Q — **entirely standard domestic-filer forms**.
- **Can the current adapter ingest without code changes?** **Yes.**
- **Existing aliases/collisions**: none.
- **Theme mapping**: Primary: photonics. Secondary theme candidate: ai-buildout (connectors serve general AI-datacenter interconnect, not exclusively optical) — **RESEARCH QUESTION — requires verification**. Subtheme: propose `connectors` (new). Segment: Connectors. Supply-chain role: high-speed interconnect and connector systems for AI datacenters and general electronics.
- **Second-order signal rationale**: Amphenol's book-to-bill ratio and order growth are frequently cited by industry analysts as a leading indicator for overall AI-datacenter buildout pace.
- **Events to monitor**: quarterly order/book-to-bill disclosures (10-Q), M&A activity expanding fiber/connector capability (a recent CommScope CCS unit acquisition was reported in Phase 2 — **RESEARCH QUESTION — requires verification**, trade-press sourced, not confirmed against a primary 8-K in this session), datacenter-specific segment commentary.
- **Outcome**: **READY_FOR_REGISTRY_ADD** — identity and adapter compatibility both fully confirmed against live primary sources.

### 10. Firefly Aerospace Inc.

- **Legal registrant name**: "Firefly Aerospace Inc." (exact SEC EDGAR registrant name, confirmed live).
- **Ticker/exchange/jurisdiction**: FLY, NASDAQ, US-domiciled.
- **Eeva-compatible source identity — SEC EDGAR**: **CIK 0001860160**, confirmed directly by fetching `https://data.sec.gov/submissions/CIK0001860160.json` live (2026-09-15). **Filing pattern, confirmed live**: 4, Schedule 13G-A, 424B3, 10-Q, 8-K, D, 3, 424B4, S-1, 144 — standard domestic-filer forms (the S-1/424B3/424B4/D entries reflect its recent August 2025 IPO, not a foreign-filer pattern).
- **Can the current adapter ingest without code changes?** **Yes** — a genuinely short filing history (IPO'd 2025-08-07) is a real, disclosed characteristic (as Phase 2 already noted, matching the existing registry's own treatment of CoreWeave), not an adapter compatibility concern.
- **Existing aliases/collisions**: none.
- **Theme mapping**: Primary: space. Secondary: none. Subtheme: `launch` reused (same product category as the existing Rocket Lab entry). Segment: Launch (with Lunar as a real secondary business line, per the company's own public description — "develops and operates space launch and lunar infrastructure technologies"). Supply-chain role: launch-vehicle and lunar-lander developer.
- **Second-order signal rationale**: as a second, independent launch provider, Firefly's own manifest/cadence disclosures give a direct comparison point against the already-tracked Rocket Lab for reading the health of the broader small/medium launch market.
- **Events to monitor**: launch cadence/manifest updates (8-K), lunar mission milestones, capacity-expansion or new-vehicle-program announcements.
- **Outcome**: **READY_FOR_REGISTRY_ADD** — identity and adapter compatibility both fully confirmed against live primary sources.

---

## Summary table

| # | Candidate | Outcome |
|---|---|---|
| 1 | Nabtesco Corporation | READY_PENDING_IDENTITY_CONFIRMATION |
| 2 | Taiwan Semiconductor Manufacturing Co. Ltd. | REQUIRES_ADAPTER_CHANGE |
| 3 | Tesla, Inc. | READY_FOR_REGISTRY_ADD |
| 4 | ASML Holding N.V. | REQUIRES_ADAPTER_CHANGE |
| 5 | Hanmi Semiconductor Co., Ltd. | READY_PENDING_IDENTITY_CONFIRMATION |
| 6 | HD Hyundai Electric Co., Ltd. | READY_PENDING_IDENTITY_CONFIRMATION |
| 7 | GE Vernova Inc. | READY_FOR_REGISTRY_ADD |
| 8 | Kulicke & Soffa Industries, Inc. | READY_FOR_REGISTRY_ADD |
| 9 | Amphenol Corporation | READY_FOR_REGISTRY_ADD |
| 10 | Firefly Aerospace Inc. | READY_FOR_REGISTRY_ADD |

**No candidate received DEFER** — every one of the 10 had a clear,
specific, actionable outcome; none needed to be set aside entirely.

---

## Sources checked

- **SEC EDGAR** (`data.sec.gov/submissions/CIK##########.json`, fetched
  live 2026-09-15): TSMC (CIK 1046179), ASML (CIK 937966), Tesla (CIK
  1318605), GE Vernova (CIK 1996810), Kulicke & Soffa (CIK 56978),
  Amphenol (CIK 820313), Firefly Aerospace (CIK 1860160) — 7 direct
  primary-source fetches, each yielding registrant name, ticker(s),
  exchange(s), and the most recent ~10-15 distinct form types on
  record.
- **EDINET official code list** (`disclosure2dl.edinet-fsa.go.jp/
  searchdocument/codelist/Edinetcode.zip`, downloaded and parsed live
  2026-09-15, dated as-of 2026-09-15, 11,389 total entries) — the same
  public URL `edinet_code_resolver.py`'s own `PROVISIONAL_CODE_LIST_URL`
  points to. Direct primary-source confirmation of Nabtesco's EDINET
  code (E01726).
- **OpenDART** (`dart.fss.or.kr`) — attempted; the bulk `corpCode.xml`
  endpoint requires an API key this environment doesn't have, and the
  public search UI is JavaScript-rendered and did not return usable
  results via this session's fetch tooling. **Not resolved** for Hanmi
  Semiconductor or HD Hyundai Electric — see Unresolved Questions.
- **This repository's own code**, read directly (not assumed): `src/
  data_access/edgar/edgar_rules.py` (`FORM_TYPE_CATEGORIES`,
  `evaluate_form_type`), `src/data_access/edgar/scan_service.py` (the
  full `scan()` loop and `_filing_event_from_row`/`_evaluate_row`),
  `src/data_access/edgar/cik_resolver.py` (confirmed CIK resolution is
  ticker-based and generic — no foreign-filer-specific blocker there),
  `src/data_access/dart/client.py` (confirmed the API-key requirement
  for `corpCode.xml`), `src/config/tracked_companies.py` and `src/
  config/issuer_registry.py` (grepped for all 10 candidate names —
  zero collisions).
- **Trade press / established-profile sources**, carried forward from
  Phase 2 and re-cited where still relevant (TheElec, TrendForce for
  the Hanmi–SK Hynix relationship; general company-identity sources for
  the HD Hyundai Electric name-change history) — none upgraded in
  evidence tier from Phase 2.

## Unresolved verification questions

1. **DART corp_code for Hanmi Semiconductor and HD Hyundai Electric** —
   genuinely unresolved in this session. Requires either a working
   `EDGE_DART_API_KEY` to run the existing `corp_code_resolver.py`
   against OpenDART's real bulk endpoint, or a different (non-JS-
   dependent) way to query DART's public company search. This is the
   single most actionable next step before either candidate can move
   past READY_PENDING_IDENTITY_CONFIRMATION.
2. **Exact 20-F/6-K category mapping design** — this document
   identifies precisely *where* the EDGAR adapter gap is
   (`FORM_TYPE_CATEGORIES`) but does not design the fix. A short,
   separate decision is needed: does `20-F` map to
   `earnings_or_results` outright (the closest analogue to 10-K), or
   does it need its own category? Does `6-K` need an items-style
   refinement mirroring 8-K's own `refine_8k_evaluation()` path (6-K
   filings can themselves carry very different content — earnings
   releases, material event disclosures, press releases), or would a
   single coarse category risk over- or under-promoting foreign-filer
   filings relative to the domestic-filer bar? Not resolved here —
   flagged as the concrete next design question.
3. **Whether Arm Holdings is currently, silently affected by the same
   20-F/6-K gap** — very likely, based on this document's code
   inspection, but this document did not pull Arm's own live
   production filing/candidate history to confirm it (no production
   database access in this session, consistent with every prior phase).
4. **EDINET triplet coverage for Nabtesco specifically** — this
   document argues by structural analogy (the 3 known triplets are
   generic document-type codes, not SoftBank-specific) that Nabtesco's
   own filings would likely match the same triplets, but did not pull
   a live Nabtesco EDINET document list to directly confirm this.
5. **Secondary-theme candidates flagged RESEARCH QUESTION above**
   (Tesla→ai-buildout, Kulicke & Soffa→memory, Amphenol→ai-buildout)
   — none upgraded from Phase 2's own hedged framing; each would need
   its own targeted verification pass.

## Adapter gaps identified

**One, precisely scoped**: `src/data_access/edgar/edgar_rules.py`'s
`FORM_TYPE_CATEGORIES` dict has no entry for `20-F` or `6-K` — the
standard annual/interim-report forms every foreign private issuer
files instead of `10-K`/`10-Q`. This silently prevents any such
issuer's filings from ever being promoted to a reviewable
`CandidateSignal`, while still allowing them to ingest as inert
`FilingEvent`s with no error or warning. Affects TSMC and ASML
directly among this batch of 10, and very likely Arm Holdings in the
already-live registry (unconfirmed — see Unresolved Question 3). No
other adapter gap was found across EDGAR, EDINET, or DART for any of
the 10 candidates.

## Files read / created

**Read**: `design/COVERAGE_EXPANSION_CANDIDATES_TIER1_2026_09_15.md`
(this phase's own input); `src/data_access/edgar/edgar_rules.py`,
`src/data_access/edgar/scan_service.py`,
`src/data_access/edgar/cik_resolver.py`,
`src/data_access/dart/client.py`,
`src/data_access/edinet/edinet_code_resolver.py`,
`src/config/tracked_companies.py`, `src/config/issuer_registry.py`,
`src/data_access/daily_news/company_aliases.py` (all read-only, no
edits).

**Created**:
`design/COVERAGE_EXPANSION_FIRST10_REGISTRY_READINESS_2026_09_15.md`
(this document).

No other file was read or written. No code, test, schema, or
production-data change was made. No live scan was run against any
tracked or candidate company.
