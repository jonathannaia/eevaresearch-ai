# Tier 1 Cohort 1 — Registry Readiness Verification (2026-09-15)

Read-only, research-only. No file under `src/`, no test, no schema, no
`tracked_companies.py` entry, and no production data was modified to
produce this document. Scopes exactly the 13 Cohort 1 candidates named
in the current request, drawn from
`design/THEMATIC_COVERAGE_UNIVERSE_AUDIT_2026_09_15.md`. TSMC, ASML,
Nanya Technology, Winbond Electronics, Innolight, and Siemens Energy
are explicitly excluded from the per-candidate sections below — see
§4 for their (corrected) status.

**Evidence tiers**, same discipline as the audit document:
**[Primary/regulatory, session-verified]** (fetched live this session
against SEC EDGAR, EDINET, or OpenDART/DART), **[Established public
profile]**, **[Trade press]**, and **RESEARCH LEAD — requires
verification**.

## Headline findings — read before the per-candidate sections

**1. A working `EDGE_DART_API_KEY` is now configured in this
environment.** The prior research document
(`COVERAGE_EXPANSION_FIRST10_REGISTRY_READINESS_2026_09_15.md`) left
Hanmi Semiconductor's and HD Hyundai Electric's DART `corp_code`
unresolved specifically because no key was available at that time.
This session used the project's own `DartClient.fetch_all_corp_codes()`
(read-only — no file modified, no cache written, no registry entry
created) to fetch DART's live bulk corp-code file (119,281 records,
fetched 2026-09-15) and resolve both identities directly:
**Hanmi Semiconductor → corp_code `00161383`**, **HD Hyundai Electric
→ corp_code `01205851`**. Both companies move from
`READY_PENDING_IDENTITY_CONFIRMATION` to fully identity-confirmed.
**[Primary/regulatory, session-verified]**.

**2. The EDGAR 20-F/6-K foreign-private-issuer adapter gap the prior
research document flagged as `REQUIRES_ADAPTER_CHANGE` for TSMC and
ASML has already been closed** — by this session's own earlier,
separately-approved and merged work (PR #56, "Add conservative EDGAR
20-F and 6-K foreign issuer support," commit `84db0b7`, merged into
`origin/main` at `a430e1d`, well before this document). Direct
inspection of the current `src/data_access/edgar/edgar_rules.py`
confirms `FORM_TYPE_CATEGORIES` now maps `"20-F": "earnings_or_results"`,
and a separate `evaluate_six_k()` gate promotes a 6-K when its
filing's own filename (the only scan-time-available signal SEC
provides for a 6-K) matches a curated material-content allow-list and
fails closed otherwise. **This means TSMC's and ASML's own
`REQUIRES_ADAPTER_CHANGE` status in the prior document is now
stale as written** — the code that document said was missing has
since been built (in a separate, already-completed and merged phase
of this project's work, not as part of the coverage audit). This
matters directly for this cohort: **ASE Technology Holding** (§1.5,
below) is a 20-F/6-K filer of exactly the same shape, and this
document independently verified its real, live 6-K filenames this
session — see §1.5 for why that verification produces a HOLD, not a
clean GO, despite the code path technically existing.

**3. Iridium Communications is under a pending $8.0B acquisition by
Rocket Lab Corporation — a company already tracked in this
registry.** Announced 2026-06-29, a definitive merger proxy (Form
DEFM14A) was filed with the SEC, and the transaction is expected to
close mid-2027, subject to Iridium shareholder approval, FCC
license-transfer consent, and HSR antitrust clearance. **[Primary/
regulatory, session-verified via SEC EDGAR filing history, corroborated
by contemporaneous reporting]**. This was not visible in the original
thematic audit (compiled before this specific verification pass) and
materially changes Iridium's recommendation — see §1.12.

---

## 1. Per-candidate verification

### 1.1 Hanmi Semiconductor Co., Ltd.

- **Legal registrant name**: Hanmi Semiconductor Co., Ltd. (per DART's
  own `corp_eng_name` field: "HANMI Semiconductor CO., LTD.");
  한미반도체 (Korean, native, per DART).
- **Ticker/security code/exchange/jurisdiction**: KRX 042700; South
  Korea.
- **Source/adapter route**: OpenDART / DART.
- **Identity verification**: **DART corp_code `00161383`**, resolved
  live this session against DART's real bulk corp-code endpoint using
  the project's own `DartClient` (see headline finding 1).
  **[Primary/regulatory, session-verified, 2026-09-15]**. Stock code
  042700 independently corroborated by the same record.
- **Scan-eligibility**: **Representable under the existing DART
  adapter with no code change.** `dart_rules.py`'s candidate-promotion
  logic is a generic Korean-keyword lexicon matched against each
  disclosure's `report_nm` title — not tied to any per-company or
  per-form-type configuration — the same path already serving all 10
  currently-tracked KRX companies would apply unchanged.
- **Theme/tags**: Primary: `memory`. Secondary: `ai-buildout`
  (HBM-bridge logic, same pattern already applied to SK Hynix/Samsung
  Electronics). Proposed subtheme: `hbm-packaging-equipment` (new —
  no existing subtheme fits thermal-compression bonding specifically;
  distinct from the existing `hbm` subtheme, which describes the
  memory device itself, not the equipment that assembles it).
- **Supply-chain role**: Thermal-compression (TC) bonding equipment
  for HBM stacking — reported ~80% global share in this specific
  equipment category **[Trade press — industry-analysis sourced, not
  independently re-verified]**.
- **Relationships**: Two independent trade publications (TheElec,
  TrendForce) reported a specific, dated Hanmi–SK Hynix TC-bonder
  order (~44.2 billion won, 2026-06) — **RESEARCH LEAD — requires
  verification** (trade press, not a primary DART filing; not
  upgraded this session). SK Hynix and Samsung Electronics are both
  already tracked, making this the clearest equipment-to-device bridge
  candidate in the cohort.
- **Event triggers to monitor**: named customer order announcements,
  new-generation equipment launches (e.g. "Wide TC Bonder" for
  HBM5/HBM6), capacity-expansion disclosures, DART report titles
  containing HBM/반도체 capacity-expansion keywords.
- **Order classification**: 2nd-order (order backlog is a near-real-
  time leading indicator for HBM4/HBM5 ramp timing at Samsung/SK
  Hynix).
- **Importance score**: 9/10.
- **Recommended initial coverage state**: `active=True` on add — no
  reason to stage it inactive; the adapter route is fully functional
  today.
- **Proposed exact registry field values**:
  ```
  name="Hanmi Semiconductor Co., Ltd."
  exchange="KRX"
  krx_code="042700"
  source="OpenDART / DART"
  themes=("memory", "ai-buildout")
  subthemes=("hbm-packaging-equipment",)
  corp_code=None  # left unresolved at add-time, consistent with all
                  # 10 existing DART entries' convention (runtime-
                  # resolved via corp_code_resolver's own cache) —
                  # NOT hardcoded here despite being independently
                  # confirmed this session; see note below.
  notes="DART corp_code independently confirmed live this session as "
        "00161383 (2026-09-15) — a cross-check value for whichever "
        "runtime resolution path is used at add-time, not a claim "
        "that this value should be hardcoded (existing DART entries "
        "leave corp_code=None by convention; only EDINET entries "
        "hardcode their resolved code)."
  ```
- **Aliases required**: none identified — grep of `tracked_companies.py`,
  `issuer_registry.py`, and `company_aliases.py` for "Hanmi" returns
  zero matches (checked this session).
- **GO / HOLD / BLOCKED: GO.**

### 1.2 Nabtesco Corporation

- **Legal registrant name**: Nabtesco Corporation; ナブテスコ株式会社
  (Japanese, native).
- **Ticker/security code/exchange/jurisdiction**: Securities code
  6268 (4-digit TSE form) / 62680 (5-digit EDINET form); TSE; Japan.
- **Source/adapter route**: EDINET.
- **Identity verification**: **EDINET code `E01726`**, re-confirmed
  this session directly against EDINET's own official code-list bulk
  file (`disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/
  Edinetcode.zip`, downloaded and parsed fresh this session, dated
  2026-09-15, 11,389 total entries) — the exact same source and result
  the prior research document reported, independently reproduced.
  **[Primary/regulatory, session-verified, 2026-09-15]**.
- **Scan-eligibility**: EDINET candidate promotion
  (`edinet_rules.py`'s `DEFAULT_CODE_CATEGORY_MAP`) is keyed by a
  generic `(ordinanceCode, formCode, docTypeCode)` triplet — a
  standardized regulatory document-type code, not a per-company one.
  Carried forward from the prior document: only 3 real triplets are
  currently mapped (annual securities report, share buyback status,
  extraordinary report), verified live against SoftBank Group's
  filings — this document did not pull a live Nabtesco EDINET document
  list to directly confirm the same triplets recur for Nabtesco's own
  filings, so this remains **RESEARCH LEAD — requires verification**
  (structural analogy only, unchanged from the prior document).
- **Theme/tags**: Primary: `humanoids`. No secondary theme proposed.
  Proposed subtheme: `precision-reducers` (new).
- **Supply-chain role**: Precision reduction-gear (RV/cycloidal)
  manufacturer for large-joint robot actuation; reported ~60% global
  RV-reducer share **[Trade press, not independently re-verified]**.
- **Relationships**: No single named customer relationship documented
  in primary sources — Nabtesco's reducers are a horizontal input
  across the industrial/humanoid-robotics OEM base broadly (FANUC,
  Yaskawa — §1.4 — and others), not a single-customer dependency.
  This breadth is itself the signal value, not a specific named deal.
- **Event triggers to monitor**: EDINET extraordinary reports tied to
  capacity expansion or major customer agreements in the robotics-
  component segment specifically (Nabtesco also reports rail/
  aircraft/construction-equipment segments — event filtering would
  need to distinguish these); annual securities report segment
  breakdowns.
- **Order classification**: 2nd-order (a capacity/backlog signal here
  is a leading indicator across nearly every humanoid/industrial-
  robotics OEM).
- **Importance score**: 9/10.
- **Recommended initial coverage state**: `active=True`.
- **Proposed exact registry field values**:
  ```
  name="Nabtesco Corporation"
  native_name="ナブテスコ株式会社"
  exchange="TSE"
  krx_code="62680"
  source="EDINET"
  themes=("humanoids",)
  subthemes=("precision-reducers",)
  corp_code="E01726"  # hardcoded — consistent with the FANUC/Nidec/
                      # Renesas precedent of hardcoding an EDINET
                      # code already live-verified via the official
                      # code list, rather than deferring to runtime
                      # resolution.
  notes="EDINET code and securities code independently re-confirmed "
        "live this session (2026-09-15) against the official EDINET "
        "code list, reproducing the prior research document's result "
        "exactly. Triplet-coverage for Nabtesco's own filing types is "
        "a RESEARCH LEAD (structural analogy to SoftBank's confirmed "
        "triplets), not independently pulled live."
  ```
- **Aliases required**: none identified — zero collisions.
- **GO / HOLD / BLOCKED: GO.**

### 1.3 Harmonic Drive Systems Inc.

- **Legal registrant name**: Harmonic Drive Systems Inc.;
  株式会社ハーモニック・ドライブ・システムズ (Japanese, native).
- **Ticker/security code/exchange/jurisdiction**: Securities code 6324
  (4-digit TSE form) / 63240 (5-digit EDINET form); TSE; Japan.
- **Source/adapter route**: EDINET.
- **Identity verification**: **EDINET code `E01712`**, confirmed live
  this session directly against the same official EDINET code-list
  bulk file used for Nabtesco (§1.2) — fiscal year end 3/31, industry
  classification 機械 (Machinery), listed (上場). **[Primary/
  regulatory, session-verified, 2026-09-15]**. This is a genuinely new
  finding not present in either prior research document — Harmonic
  Drive Systems was not previously researched at all.
- **Scan-eligibility**: Same triplet-coverage caveat as Nabtesco
  (§1.2) — **RESEARCH LEAD — requires verification** whether Harmonic
  Drive Systems' own annual/extraordinary report filings match the 3
  known triplets; not pulled live this session.
- **Theme/tags**: Primary: `humanoids`. No secondary theme proposed.
  Proposed subtheme: `precision-reducers` (shared with Nabtesco —
  strain-wave/harmonic-drive gearing is the other dominant precision-
  reduction technology for robot-joint actuation, alongside Nabtesco's
  RV/cycloidal gears; together the two cover nearly the entire
  addressable reducer market for this application).
- **Supply-chain role**: Strain-wave (harmonic-drive) gear
  manufacturer — the namesake and inventor of this gear category,
  widely used in space, medical, semiconductor, and robot-joint
  applications. **[Established public profile]** for the company's
  core business; the specific "namesake/inventor" framing is
  **[Established public profile]**, not independently re-verified
  against a primary corporate filing this session.
- **Relationships**: No single named customer relationship documented
  in primary sources this session — same horizontal-input pattern as
  Nabtesco. **RESEARCH LEAD — requires verification** for any specific
  named humanoid-OEM or space-program customer relationship.
- **Event triggers to monitor**: EDINET extraordinary reports tied to
  capacity expansion; annual securities report segment breakdowns
  (the company also serves space and semiconductor-equipment
  end-markets — event filtering would need the same kind of
  segment-distinguishing care Nabtesco's own multi-segment caveat
  requires).
- **Order classification**: 2nd-order, same rationale as Nabtesco.
- **Importance score**: 8/10 — a genuine gap of comparable importance
  to Nabtesco, newly identified in the audit document and independently
  identity-confirmed here; scored one point below Nabtesco only because
  Nabtesco's reported market-share figure (~60%) is more specific than
  any comparable figure found for Harmonic Drive Systems this session.
- **Recommended initial coverage state**: `active=True`.
- **Proposed exact registry field values**:
  ```
  name="Harmonic Drive Systems Inc."
  native_name="株式会社ハーモニック・ドライブ・システムズ"
  exchange="TSE"
  krx_code="63240"
  source="EDINET"
  themes=("humanoids",)
  subthemes=("precision-reducers",)
  corp_code="E01712"
  notes="EDINET code and securities code confirmed live this session "
        "(2026-09-15) against the official EDINET code list. New "
        "candidate, not present in either prior research document — "
        "identified during the thematic coverage audit as the "
        "second dominant precision-reducer technology alongside "
        "Nabtesco. Triplet-coverage for this filer's own filing "
        "types is a RESEARCH LEAD, not independently pulled live."
  ```
- **Aliases required**: none identified — zero collisions (checked
  this session for "Harmonic Drive").
- **GO / HOLD / BLOCKED: GO.**

### 1.4 Yaskawa Electric Corporation

- **Legal registrant name**: YASKAWA Electric Corporation;
  株式会社安川電機 (Japanese, native).
- **Ticker/security code/exchange/jurisdiction**: Securities code 6506
  (4-digit TSE form) / 65060 (5-digit EDINET form); TSE; Japan.
- **Source/adapter route**: EDINET.
- **Identity verification**: **EDINET code `E01741`**, confirmed live
  this session against the same official EDINET code-list bulk file
  — fiscal year end end-of-February, industry classification 電気機器
  (Electric Machinery), listed (上場). Two unrelated individual
  filers sharing the "安川" surname also appear in the code list
  (`E08569`, `E40004`) — confirmed distinct by entity type (個人,
  "individual," not 内国法人・組合, "domestic corporation") and by
  having no stock code — no collision risk with the corporate entity.
  **[Primary/regulatory, session-verified, 2026-09-15]**.
- **Scan-eligibility**: Same triplet-coverage caveat as §1.2/§1.3 —
  **RESEARCH LEAD — requires verification**.
- **Theme/tags**: Primary: `humanoids`. No secondary theme proposed
  (a `power-cooling`/motor-adjacent secondary tie to `ai-buildout` is
  plausible given Yaskawa's servo-drive business but not asserted —
  **RESEARCH QUESTION — requires verification**). Proposed subtheme:
  none new — reuse `industrial-automation` (already live on Doosan
  Robotics, FANUC's peer entries, Mitsubishi Electric, Rockwell,
  Symbotic).
- **Supply-chain role**: One of the world's top-three industrial-robot
  OEMs and a major servo-motor/drive maker. **[Established public
  profile]**. This is the single most surprising pre-existing gap
  identified anywhere in the audit: FANUC is already tracked as the
  registry's sole robot-OEM name, while Yaskawa — a direct peer at
  comparable scale — was absent.
- **Relationships**: No single named customer relationship documented
  this session; as a direct OEM (not a component supplier), Yaskawa's
  own disclosures are the primary signal rather than a downstream
  relationship. **RESEARCH LEAD — requires verification** for any
  specific humanoid-program customer or partnership beyond its
  established industrial-robot business.
- **Event triggers to monitor**: EDINET annual/extraordinary reports
  naming humanoid or next-generation robotics program status
  specifically (distinct from Yaskawa's much larger existing
  industrial-arm business); capacity-expansion disclosures; named
  order announcements.
- **Order classification**: 1st-order (a direct OEM, not an inferred
  bridge).
- **Importance score**: 7/10.
- **Recommended initial coverage state**: `active=True`.
- **Proposed exact registry field values**:
  ```
  name="YASKAWA Electric Corporation"
  native_name="株式会社安川電機"
  exchange="TSE"
  krx_code="65060"
  source="EDINET"
  themes=("humanoids",)
  subthemes=("industrial-automation",)
  corp_code="E01741"
  notes="EDINET code and securities code confirmed live this session "
        "(2026-09-15) against the official EDINET code list. Two "
        "unrelated individual filers sharing the same surname "
        "confirmed distinct (entity type and absence of a stock "
        "code) — no collision. New candidate, not present in either "
        "prior research document; identified during the thematic "
        "coverage audit as the most surprising pre-existing "
        "single-name gap (FANUC tracked, its direct peer was not)."
  ```
- **Aliases required**: none identified — zero collisions for
  "Yaskawa" checked this session.
- **GO / HOLD / BLOCKED: GO.**

### 1.5 ASE Technology Holding Co., Ltd.

- **Legal registrant name**: ASE Technology Holding Co., Ltd.
  (exact SEC EDGAR registrant name). Former names on file: Advanced
  Semiconductor Engineering Inc. (through April 2018), ASE Industrial
  Holding Co., Ltd. (through June 2018).
- **Ticker/exchange/jurisdiction**: ASX (ADR), NYSE, Taiwan-domiciled
  (SEC-listed state of incorporation: Taiwan).
- **Source/adapter route**: SEC EDGAR (foreign private issuer, 20-F/
  6-K filer).
- **Identity verification**: **CIK `0001122411`**, confirmed live this
  session by fetching `data.sec.gov/submissions/CIK0001122411.json`.
  SIC classification "Semiconductors & Related Devices." **[Primary/
  regulatory, session-verified, 2026-09-15]**.
- **Scan-eligibility — the key finding for this candidate**: **The
  20-F/6-K adapter gap that blocked TSMC/ASML in the prior research
  document no longer exists in this codebase** (headline finding 2,
  above) — `FORM_TYPE_CATEGORIES` maps `20-F` today, so ASE's annual
  report would promote to a `CandidateSignal` with no further code
  change. **However**, this document independently fetched ASE's real,
  live recent 6-K filenames this session (its most frequent, highest-
  value disclosure channel — 12+ filings in the last 3 months alone,
  vs. one 20-F per year) and found them **structurally opaque**:
  `dp253117_6k.htm`, `d77314d6k.htm`, `dp251538_6k.htm`, etc. —
  auto-generated filing-system identifiers with no content-descriptive
  words at all. This is **exactly** the Arm Holdings-class failure
  mode `evaluate_six_k()`'s own module docstring explicitly documents
  as a known, accepted Phase 1 limitation: the filename-content gate
  "fails closed" on an opaque filename, meaning **every one of ASE's
  6-K filings would silently never promote to a reviewable candidate**
  under today's code, while its single annual 20-F would. **[Primary/
  regulatory, session-verified, 2026-09-15]** — this is a directly
  observed fact about this specific filer, not an inference from the
  ASML/Arm precedent.
- **Theme/tags**: Primary: `ai-buildout`. Secondary: none. Proposed
  subtheme: `advanced-packaging-test` (new — `semiconductor-test` is
  an imperfect fit, since OSAT/assembly is a distinct process step
  from post-fabrication test).
- **Supply-chain role**: World's largest outsourced semiconductor
  assembly and test (OSAT) provider. **[Established public profile]**.
- **Relationships**: Assembly/test partner across the fabless/foundry
  ecosystem broadly (NVIDIA, AMD, Broadcom, Qualcomm among the
  industry's largest fabless customers) — **RESEARCH LEAD — requires
  verification** for any specific named customer relationship; not
  independently confirmed against a primary ASE filing this session.
- **Event triggers to monitor**: capacity-expansion disclosures
  (currently only reachable via the low-frequency 20-F unless the
  filename gate is addressed), advanced-packaging capacity relevant to
  CoWoS-adjacent work, any named customer agreement.
- **Order classification**: 2nd-order (advanced-packaging capacity is
  a bottleneck for AI-accelerator shipment volume).
- **Importance score**: 8/10 (unchanged from the audit document's
  score — the strategic importance of the gap is undiminished; it is
  the *representability* that is now in question, not the value).
- **Recommended initial coverage state**: if added at all in this
  phase, `active=True` but with an explicit, visible caveat in `notes`
  that 6-K-sourced signals will be systematically under-captured —
  **not silently presented as full coverage**.
- **Proposed exact registry field values** (for reference — see GO/
  HOLD/BLOCKED recommendation below before acting on these):
  ```
  name="ASE Technology Holding Co., Ltd."
  exchange="NYSE"
  krx_code="ASX"
  source="SEC EDGAR"
  themes=("ai-buildout",)
  subthemes=("advanced-packaging-test",)
  corp_code=None  # consistent with the existing SEC EDGAR entries'
                  # convention of leaving corp_code unresolved at
                  # add-time (CIK cross-check lives in notes instead).
  notes="CIK 0001122411, confirmed live 2026-09-15. Foreign private "
        "issuer (20-F/6-K filer, Taiwan state of incorporation) — "
        "the 20-F/6-K FORM_TYPE_CATEGORIES/evaluate_six_k() support "
        "already exists (PR #56) so this is NOT a REQUIRES_ADAPTER_ "
        "CHANGE case. However, this filer's real 6-K filenames "
        "(confirmed live this session: dp253117_6k.htm and similar) "
        "are structurally opaque — the same failure mode documented "
        "for Arm Holdings — so 6-K-sourced candidates will very "
        "likely never promote under today's filename-content gate. "
        "Only the annual 20-F would reliably promote. Do not present "
        "this entry as full-coverage without this caveat visible."
  ```
- **Aliases required**: none identified — zero collisions for "ASE
  Technology" checked this session.
- **GO / HOLD / BLOCKED: HOLD.** Not `BLOCKED` — the code path is real
  and functional for the 20-F; not a clean `GO` — its primary
  disclosure channel would be silently under-captured in a way a
  reviewer could easily mistake for full coverage. Recommend holding
  until either (a) a scoped decision is made on whether 20-F-only
  coverage is acceptable for this filer, or (b) the filename gate is
  extended (e.g. reading actual 6-K exhibit text, the same follow-up
  path `evaluate_six_k()`'s own docstring already flags as a future,
  not-yet-built option) — both are implementation decisions, not
  something this read-only document resolves.

### 1.6 GE Vernova Inc.

- **Legal registrant name**: GE Vernova Inc. (exact SEC EDGAR
  registrant name; formerly "GE Vernova LLC" through 2024-03-13, a
  2024 spin-off from General Electric).
- **Ticker/exchange/jurisdiction**: GEV, NYSE, Delaware-incorporated,
  US-domiciled (HQ Cambridge, MA).
- **Source/adapter route**: SEC EDGAR (standard domestic filer).
- **Identity verification**: **CIK `0001996810`**, confirmed live this
  session (`data.sec.gov/submissions/CIK0001996810.json`). SIC
  "Electronic & Other Electrical Equipment (No Computer Equip)."
  **[Primary/regulatory, session-verified, 2026-09-15]**.
- **Scan-eligibility**: **Fully representable, no code change.**
  Recent real filing history confirmed live: 8-K, 10-Q, 11-K, Form 4,
  SD, Form 144 — all standard domestic-filer forms, already handled
  (10-Q via `FORM_TYPE_CATEGORIES`, 8-K via the dedicated item-number
  path). 11-K (an annual report for employee stock plans) is not
  individually mapped, but is immaterial to this company's core
  signal value — its 8-K/10-Q coverage is what matters.
- **Theme/tags**: Primary: `ai-buildout`. Secondary: none. Subtheme:
  `power-cooling` (reused — same precedent as Navitas/Bloom Energy/
  Quanta/nVent/Vertiv/Eaton).
- **Supply-chain role**: Gas turbines, grid equipment, and
  electrification systems manufacturer.
- **Relationships**: Gas-turbine order backlog and delivery lead times
  are widely cited industry-wide as a bottleneck for new AI-datacenter
  power availability — **[Trade press]**, a general industry
  observation, not a specific named customer relationship; no single
  named hyperscaler contract independently confirmed this session.
- **Event triggers to monitor**: turbine order-backlog disclosures
  (8-K/10-Q), grid-equipment capacity-expansion announcements, major
  AI-datacenter power-supply agreements.
- **Order classification**: 2nd-order.
- **Importance score**: 8/10.
- **Recommended initial coverage state**: `active=True`.
- **Proposed exact registry field values**:
  ```
  name="GE Vernova Inc."
  exchange="NYSE"
  krx_code="GEV"
  source="SEC EDGAR"
  themes=("ai-buildout",)
  subthemes=("power-cooling",)
  corp_code=None
  notes="CIK 0001996810, confirmed live 2026-09-15. Standard "
        "domestic filer (8-K/10-Q/11-K/Form 4/SD) — fully "
        "representable under today's adapter, no code change needed."
  ```
- **Aliases required**: none identified — zero collisions.
- **GO / HOLD / BLOCKED: GO.**

### 1.7 HD Hyundai Electric Co., Ltd.

- **Legal registrant name**: HD Hyundai Electric Co., Ltd. — name-
  change note carried forward: the company changed its name from
  "Hyundai Electric & Energy Systems Co., Ltd." in March 2023
  **[Established public profile, prior session, not independently
  re-verified against a primary DART filing this session]**. HD현대일렉트릭
  (Korean, native, per DART's own `corp_name` field).
- **Ticker/security code/exchange/jurisdiction**: KRX 267260; South
  Korea.
- **Source/adapter route**: OpenDART / DART.
- **Identity verification**: **DART corp_code `01205851`**, resolved
  live this session (headline finding 1) — DART's own record
  confirms `corp_eng_name` = "HD HYUNDAI ELECTRIC CO.,LTD", exactly
  matching the expected current legal name. **[Primary/regulatory,
  session-verified, 2026-09-15]**.
- **Scan-eligibility**: Same generic-lexicon reasoning as Hanmi
  Semiconductor (§1.1) — representable with no code change.
- **Theme/tags**: Primary: `ai-buildout`. Secondary: none. Subtheme:
  `power-cooling` (reused — the closest existing fit, imperfect;
  transformers are a narrower category within it than the tag's
  current broader usage covers).
- **Supply-chain role**: Transformers, switchgear, and grid-
  interconnect equipment manufacturer.
- **Relationships**: Transformer order backlog/delivery lead times are
  a widely-reported bottleneck for new AI-datacenter power connections
  **[Trade press]**, general industry observation, no specific named
  customer relationship confirmed this session.
- **Event triggers to monitor**: order-backlog disclosures, capacity-
  expansion announcements, major grid-equipment supply contracts.
- **Order classification**: 2nd-order.
- **Importance score**: 7/10.
- **Recommended initial coverage state**: `active=True`.
- **Proposed exact registry field values**:
  ```
  name="HD Hyundai Electric Co., Ltd."
  exchange="KRX"
  krx_code="267260"
  source="OpenDART / DART"
  themes=("ai-buildout",)
  subthemes=("power-cooling",)
  corp_code=None  # same runtime-resolution convention as §1.1
  notes="DART corp_code independently confirmed live this session as "
        "01205851 (2026-09-15) — DART's own corp_eng_name field "
        "('HD HYUNDAI ELECTRIC CO.,LTD') confirms the current legal "
        "name post the 2023 rename. Cross-check value only; not "
        "hardcoded, per existing DART-entry convention."
  ```
- **Aliases required**: consider a `HYUNDAI ELECTRIC & ENERGY SYSTEMS`
  historical-name alias for any Daily News/company-aliases matching
  that might still reference the pre-2023 name in older articles —
  **RESEARCH QUESTION — requires verification** whether
  `company_aliases.py`'s matching logic needs this or already handles
  name-change cases generically; not resolved in this read-only
  document. Zero collisions found for either "HD Hyundai Electric" or
  "Hyundai Electric & Energy Systems" this session.
- **GO / HOLD / BLOCKED: GO.**

### 1.8 Digital Realty Trust, Inc.

- **Legal registrant name**: DIGITAL REALTY TRUST, INC. (exact SEC
  EDGAR registrant name).
- **Ticker/exchange/jurisdiction**: DLR, NYSE, Maryland-incorporated,
  US-domiciled (HQ Austin, TX).
- **Source/adapter route**: SEC EDGAR (standard domestic filer, REIT).
- **Identity verification**: **CIK `0001297996`**, confirmed live this
  session. SIC "Real Estate Investment Trusts." **[Primary/regulatory,
  session-verified, 2026-09-15]**.
- **Scan-eligibility**: **Fully representable, no code change.**
  Recent real filing history confirmed live: Form 4, 8-K, 424B7,
  Schedule 13G, 10-Q — 8-K and 10-Q are already fully handled; Form 4
  and Schedule 13G(/A) are handled (13G maps to `ownership_change`).
  **One minor, non-blocking gap noted**: `424B7` (a prospectus
  supplement specific to resales by selling security holders) is not
  itself in `FORM_TYPE_CATEGORIES` (only `424B1`-`424B5` are mapped)
  — immaterial to this company's core signal value, since 8-K/10-Q
  already cover its substantive disclosures, but flagged for
  completeness since it appeared in this company's own real recent
  filing history.
- **Theme/tags**: Primary: `ai-buildout`. Proposed segment:
  "Datacenter infrastructure / REIT" (new — no existing segment fits).
  No subtheme proposed (none of today's subthemes fit a REIT).
- **Supply-chain role**: Hyperscale/colocation datacenter REIT; leases
  capacity directly to hyperscalers and neoclouds.
- **Relationships**: Landlord/developer relationship to the
  hyperscaler cohort broadly (several already tracked: Microsoft,
  Amazon, Alphabet, Meta, Oracle) — **[Established public profile]**
  for the general REIT-to-hyperscaler leasing model; no single named
  lease agreement independently confirmed this session (REITs
  typically do not name individual tenants in routine disclosures).
- **Event triggers to monitor**: development-pipeline updates,
  power-availability disclosures in 10-Q/10-K, major pre-lease
  announcements (8-K).
- **Order classification**: 1st-order — its own development pipeline
  and pre-lease disclosures are a direct, unmediated read on
  AI-datacenter buildout pace, not an inferred one, which
  distinguishes it from most of this cohort's 2nd-order component
  suppliers.
- **Importance score**: 6/10.
- **Recommended initial coverage state**: `active=True`.
- **Proposed exact registry field values**:
  ```
  name="Digital Realty Trust, Inc."
  exchange="NYSE"
  krx_code="DLR"
  source="SEC EDGAR"
  themes=("ai-buildout",)
  subthemes=()
  corp_code=None
  notes="CIK 0001297996, confirmed live 2026-09-15. Standard "
        "domestic REIT filer (8-K/10-Q/Form 4/13G). Minor, "
        "non-blocking note: this filer's real recent history includes "
        "424B7, which is not in FORM_TYPE_CATEGORIES (only 424B1-B5 "
        "are) — immaterial given 8-K/10-Q coverage, flagged for "
        "completeness."
  ```
- **Aliases required**: none identified — zero collisions.
- **GO / HOLD / BLOCKED: GO.**

### 1.9 Equinix, Inc.

- **Legal registrant name**: EQUINIX INC (exact SEC EDGAR registrant
  name).
- **Ticker/exchange/jurisdiction**: EQIX, NASDAQ, Delaware-
  incorporated, US-domiciled (HQ Redwood City, CA).
- **Source/adapter route**: SEC EDGAR (standard domestic filer, REIT).
- **Identity verification**: **CIK `0001101239`**, confirmed live this
  session. SIC "Real Estate Investment Trusts." **[Primary/regulatory,
  session-verified, 2026-09-15]**.
- **Scan-eligibility**: **Fully representable, no code change.**
  Recent real filing history confirmed live: Form 4, Form 144, 8-K,
  424B2, FWP, 424B5 — 8-K and 424B5 already fully handled; Form 4/144
  are routine ownership forms, not promoted (by design, same as every
  other tracked company). No gap identified.
- **Theme/tags**: Primary: `ai-buildout`. Same proposed segment as
  Digital Realty ("Datacenter infrastructure / REIT"). No subtheme.
- **Supply-chain role**: Global colocation/interconnection operator.
- **Relationships**: Same general hyperscaler/neocloud tenant-base
  relevance as Digital Realty — **[Established public profile]** for
  the business model; overlaps materially with Digital Realty's
  signal, added specifically for interconnection-specific disclosure
  coverage rather than as a duplicate.
- **Event triggers to monitor**: same category as Digital Realty
  (development-pipeline, power-availability, pre-lease/interconnection
  capacity announcements).
- **Order classification**: 1st-order, same rationale as Digital
  Realty.
- **Importance score**: 5/10 (one point below Digital Realty
  specifically because of the signal overlap between the two — see
  the audit document's own note on this).
- **Recommended initial coverage state**: `active=True`.
- **Proposed exact registry field values**:
  ```
  name="Equinix, Inc."
  exchange="NASDAQ"
  krx_code="EQIX"
  source="SEC EDGAR"
  themes=("ai-buildout",)
  subthemes=()
  corp_code=None
  notes="CIK 0001101239, confirmed live 2026-09-15. Standard "
        "domestic REIT filer (8-K/424B2/424B5/Form 4/144). Signal "
        "overlaps materially with Digital Realty (§1.8) — both are "
        "recommended together deliberately, not as duplicates, for "
        "interconnection- vs. leasing-specific disclosure coverage."
  ```
- **Aliases required**: none identified — zero collisions.
- **GO / HOLD / BLOCKED: GO.**

### 1.10 Firefly Aerospace Inc.

- **Legal registrant name**: Firefly Aerospace Inc. (exact SEC EDGAR
  registrant name).
- **Ticker/exchange/jurisdiction**: FLY, NASDAQ, Delaware-
  incorporated, US-domiciled.
- **Source/adapter route**: SEC EDGAR (standard domestic filer).
- **Identity verification**: **CIK `0001860160`**, re-confirmed live
  this session (`data.sec.gov/submissions/CIK0001860160.json`) —
  identical to the prior research document's finding. SIC "Guided
  Missiles & Space Vehicles & Parts." **[Primary/regulatory, session-
  verified, 2026-09-15]**.
- **Scan-eligibility**: **Fully representable, no code change.**
  Recent real filing history confirmed live: Form 4, Schedule 13G/A,
  424B3, 10-Q, 8-K, Form D, Form 3 — standard domestic-filer forms
  (the S-1/424B3/Form D entries reflect its August 2025 IPO, not a
  foreign-filer pattern, per the prior document's own finding,
  reconfirmed here).
- **Theme/tags**: Primary: `space`. Secondary: none. Subtheme:
  `launch` (reused — same product category as the existing Rocket Lab
  entry).
- **Supply-chain role**: Launch-vehicle and lunar-lander developer.
- **Relationships**: As a second, independent launch provider,
  Firefly's manifest/cadence disclosures give a direct comparison
  point against the already-tracked Rocket Lab — a `comparison_peer`-
  type relationship (see the audit document's §4.3 Chain D), not a
  supplier/customer edge. **[Established public profile]**.
- **Event triggers to monitor**: launch cadence/manifest updates
  (8-K), lunar mission milestones, capacity-expansion or new-vehicle-
  program announcements.
- **Order classification**: 1st-order.
- **Importance score**: 7/10.
- **Recommended initial coverage state**: `active=True`.
- **Proposed exact registry field values**:
  ```
  name="Firefly Aerospace Inc."
  exchange="NASDAQ"
  krx_code="FLY"
  source="SEC EDGAR"
  themes=("space",)
  subthemes=("launch",)
  corp_code=None
  notes="CIK 0001860160, re-confirmed live 2026-09-15 (matches prior "
        "research document exactly). Standard domestic filer, "
        "IPO'd 2025-08-07. Recommended alongside the existing Rocket "
        "Lab entry as a comparison-peer pairing for launch-cadence "
        "signal reading, per the coverage audit's own propagation- "
        "chain example."
  ```
- **Aliases required**: none identified — zero collisions.
- **GO / HOLD / BLOCKED: GO.**

### 1.11 L3Harris Technologies, Inc.

- **Legal registrant name**: "L3HARRIS TECHNOLOGIES, INC. /DE/" (exact
  SEC EDGAR registrant name — the "/DE/" suffix is SEC's own
  disambiguation notation, not part of ordinary usage). Formerly
  "HARRIS CORP /DE/" prior to the 2019 Harris/L3 merger.
- **Ticker/exchange/jurisdiction**: LHX, NYSE, Delaware-incorporated,
  US-domiciled (HQ Melbourne, FL).
- **Source/adapter route**: SEC EDGAR (standard domestic filer).
- **Identity verification**: **CIK `0000202058`**, confirmed live this
  session. SIC "Search, Detection, Navigation, Guidance, Aeronautical
  Sys." **[Primary/regulatory, session-verified, 2026-09-15]**.
- **Scan-eligibility**: **Fully representable, no code change.**
  Recent real filing history confirmed live: Form 144, Form 4, Form 3,
  8-K, Schedule 13G/A — 8-K and 13G/A already fully handled; the
  remainder are routine ownership forms, not promoted by design.
- **Theme/tags**: Primary: `space`. No formal secondary theme
  proposed — L3Harris's defense-electronics business is substantial
  but "defense" is not one of the 5 tracked themes; flagged in the
  audit document (§4.2) as a possible future theme-boundary question,
  not resolved here.
- **Supply-chain role**: Satellite payloads, space-domain-awareness
  systems, and defense electronics; an integrator whose subcontractor
  awards can signal activity across smaller tracked names.
- **Relationships**: **RESEARCH LEAD — requires verification** for any
  specific named subcontractor relationship to a smaller tracked space
  company (AST SpaceMobile, Intuitive Machines, Redwire, Planet Labs)
  — plausible given L3Harris's integrator role, not independently
  confirmed against a primary filing this session.
- **Event triggers to monitor**: space-segment 8-K disclosures
  specifically (L3Harris's non-space defense-electronics business is
  much larger and would need filtering — a multi-segment caveat of the
  same shape as Nabtesco's and Mitsubishi Heavy Industries'), major
  contract awards, space-domain-awareness program milestones.
- **Order classification**: mixed — 1st-order for its own space-
  segment disclosures, 2nd-order as an integrator whose awards signal
  activity at smaller tracked subcontractors.
- **Importance score**: 5/10.
- **Recommended initial coverage state**: `active=True`, with the
  multi-segment filtering caveat noted above visible in `notes` so a
  future implementer does not assume every L3Harris 8-K is
  space-relevant.
- **Proposed exact registry field values**:
  ```
  name="L3Harris Technologies, Inc."
  exchange="NYSE"
  krx_code="LHX"
  source="SEC EDGAR"
  themes=("space",)
  subthemes=()
  corp_code=None
  notes="CIK 0000202058, confirmed live 2026-09-15. Standard "
        "domestic filer. Multi-segment company — space/satellite "
        "disclosures are a minority of its overall defense-"
        "electronics business; event filtering at classification "
        "time should not assume every 8-K is space-relevant, same "
        "caveat already applied to Nabtesco's multi-segment filings."
  ```
- **Aliases required**: consider a "Harris Corp" historical-name
  alias for pre-2019 references — **RESEARCH QUESTION — requires
  verification** whether this matters for Daily News matching given
  the merger is 7 years old; not resolved here. Zero collisions found
  for "L3Harris" this session.
- **GO / HOLD / BLOCKED: GO.**

### 1.12 Iridium Communications Inc.

- **Legal registrant name**: Iridium Communications Inc. (exact SEC
  EDGAR registrant name).
- **Ticker/exchange/jurisdiction**: IRDM, NASDAQ, Delaware-
  incorporated, US-domiciled (HQ McLean, VA).
- **Source/adapter route**: SEC EDGAR (standard domestic filer).
- **Identity verification**: **CIK `0001418819`**, confirmed live this
  session. SIC "Communication Services, NEC." **[Primary/regulatory,
  session-verified, 2026-09-15]**.
- **Scan-eligibility**: Mechanically representable, no code change —
  but see the material finding below before treating that as
  sufficient reason to add it.
- **Material finding (headline finding 3, detailed here)**: Iridium's
  real, live recent filing history (fetched this session) includes
  **Form 425** (prospectus/communication filed in connection with a
  business combination) and **Form DEFM14A** (definitive merger proxy
  statement) dated 2026-08-26, alongside routine Form 4s. A web search
  corroborated the underlying event: **Rocket Lab Corporation agreed
  to acquire Iridium Communications in an $8.0 billion cash-and-stock
  transaction, announced 2026-06-29** (Iridium shareholders to receive
  $27.00 cash plus Rocket Lab stock, combined value $54.00/share),
  **expected to close mid-2027**, subject to Iridium shareholder
  approval, FCC license-transfer consent, and HSR antitrust clearance.
  **[Primary/regulatory, session-verified via SEC filing history]**,
  corroborated by **[Trade press — SatNews, CNBC, TipRanks, The Globe
  and Mail, and Iridium's own investor-relations press release, dated
  2026-06-29]**.
- **Why this changes the recommendation**: **Rocket Lab is already a
  tracked company in this registry** (`space`, `launch` subtheme).
  Adding Iridium now as a fresh, independent registry entry would
  track a company whose independent public existence is expected to
  end within roughly 9-12 months of this document, at which point its
  disclosure stream converges into Rocket Lab's own filings anyway.
  This is a real, near-term durability problem for a registry
  addition, not a theoretical one — distinct from every other
  candidate in this cohort.
- **Theme/tags**: Primary: `space`. Subtheme: none proposed pending
  the merger-status decision below.
- **Supply-chain role**: Owner/operator of a commercial LEO satellite
  constellation (communications) — would become, post-merger, a
  direct extension of Rocket Lab's own vertically-integrated launch-
  and-satellite-operations business ("fully vertically integrated
  space powerhouse," per the companies' own announced deal framing).
- **Relationships**: Rocket Lab (acquirer, already tracked) —
  **[Primary/regulatory, session-verified]**, the strongest-evidenced
  relationship of any candidate in this cohort, precisely because it
  is a pending M&A transaction rather than a supply-chain inference.
- **Event triggers to monitor, if tracked during the pending period**:
  shareholder-vote outcome, FCC/HSR clearance milestones, any deal-
  termination or amendment disclosure (425/DEFM14A/8-K), and — once
  closed — the transition of Iridium's disclosure stream into Rocket
  Lab's own filings.
- **Order classification**: 1st-order today; would become a
  sub-component of an already-tracked 1st-order entity post-close.
- **Importance score**: 5/10 as a standalone signal source today,
  but this score is explicitly time-limited given the pending
  acquisition — not a durable, multi-year coverage addition the way
  the rest of this cohort's GO candidates are.
- **Recommended initial coverage state**: if tracked at all during the
  pending-merger window, `active=True` but explicitly framed in
  `notes` as a time-boxed addition tied to deal-closing milestones,
  not a standard open-ended entry.
- **Proposed exact registry field values** (for reference only — see
  recommendation below):
  ```
  name="Iridium Communications Inc."
  exchange="NASDAQ"
  krx_code="IRDM"
  source="SEC EDGAR"
  themes=("space",)
  subthemes=()
  corp_code=None
  notes="CIK 0001418819, confirmed live 2026-09-15. UNDER A PENDING "
        "$8.0B ACQUISITION BY ROCKET LAB (already tracked), announced "
        "2026-06-29, DEFM14A filed, expected close mid-2027. Do not "
        "add as a standard open-ended entry without an explicit "
        "decision on how to handle the pending-merger window — see "
        "TIER1_COHORT1_REGISTRY_READINESS_2026_09_15.md §1.12 and "
        "§2.1."
  ```
- **Aliases required**: none identified — zero collisions for
  "Iridium" this session.
- **GO / HOLD / BLOCKED: HOLD.** Mechanically representable, but
  adding it now, without an explicit decision on how the registry
  should treat a company mid-acquisition by an already-tracked peer,
  risks duplicate/confusing signal coverage within roughly a year.
  This is a product/coverage-policy decision (§2.1 proposes a general
  rule), not something this read-only document resolves unilaterally.

### 1.13 Viasat, Inc.

- **Legal registrant name**: VIASAT INC (exact SEC EDGAR registrant
  name).
- **Ticker/exchange/jurisdiction**: VSAT, NASDAQ, Delaware-
  incorporated, US-domiciled (HQ Carlsbad, CA; fiscal year end
  March 31).
- **Source/adapter route**: SEC EDGAR (standard domestic filer).
- **Identity verification**: **CIK `0000797721`**, confirmed live this
  session. SIC "Communications Services, NEC." **[Primary/regulatory,
  session-verified, 2026-09-15]**.
- **Scan-eligibility**: **Fully representable, no code change.**
  Recent real filing history confirmed live: Form 4, 8-K, Form 144 —
  8-K already fully handled; the remainder are routine ownership
  forms, not promoted by design. No gap identified. Fiscal year end
  March 31 (not December) — worth noting only as an operational detail
  for anyone scheduling periodic-report expectations, not a
  scan-eligibility concern.
- **Theme/tags**: Primary: `space`. No secondary theme proposed.
  No subtheme proposed — none of today's subthemes cleanly fit a
  satellite-communications operator/equipment maker (distinct from
  `launch`).
- **Supply-chain role**: Satellite communications operator and
  equipment maker.
- **Relationships**: **RESEARCH LEAD — requires verification** for any
  specific named customer/government-contract relationship; general
  satcom-market relevance only, not independently confirmed against a
  primary filing this session.
- **Event triggers to monitor**: 8-K material-event disclosures,
  major government or commercial satcom contract awards, satellite
  launch/deployment milestones for its own fleet.
- **Order classification**: 1st-order.
- **Importance score**: 4/10 (lowest in this cohort's GO list — a
  real but comparatively thin evidence base for second-order signal
  value relative to the rest of the space additions).
- **Recommended initial coverage state**: `active=True`.
- **Proposed exact registry field values**:
  ```
  name="Viasat, Inc."
  exchange="NASDAQ"
  krx_code="VSAT"
  source="SEC EDGAR"
  themes=("space",)
  subthemes=()
  corp_code=None
  notes="CIK 0000797721, confirmed live 2026-09-15. Standard "
        "domestic filer, fiscal year end March 31 (not calendar "
        "year) — an operational detail for periodic-report "
        "scheduling, not a scan-eligibility concern."
  ```
- **Aliases required**: none identified — zero collisions.
- **GO / HOLD / BLOCKED: GO.**

---

## 2. Cohort-level guidance

### 2.1 Minimum evidence threshold: audit recommendation → registry addition

Proposed, in order — a candidate should not move from "recommended in
an audit document" to "added to `tracked_companies.py`" until **all**
of the following are independently true, not merely asserted from the
audit document's own text:

1. **Identity resolved against the actual source the adapter will
   query** — not a ticker or name alone. For SEC EDGAR: a live CIK
   fetch. For EDINET: a live match against the official code list.
   For DART: a live `corp_code` resolution (as this document did for
   all applicable candidates — the single biggest gap the prior
   research document left open).
2. **Scan-eligibility verified against the adapter's real, current
   rule set**, not assumed from a similar company's precedent. This
   document's ASE Technology Holding finding (§1.5) is the clearest
   argument for this step: the code-level blocker TSMC/ASML were
   flagged for no longer exists, yet a superficially similar company
   (ASE) still has a real, filer-specific problem the generic
   "20-F/6-K support exists now" fact would have hidden.
3. **Zero unresolved alias/collision risk** against
   `tracked_companies.py`, `issuer_registry.py`, and
   `company_aliases.py` — checked directly, not assumed from the audit
   document's broader company list.
4. **No pending corporate-action event** (merger, acquisition,
   delisting, bankruptcy) that would materially shorten the company's
   remaining life as an independent registry entry — this document's
   Iridium finding (§1.12) is the concrete example; this check was not
   part of either prior research document's own methodology and should
   be added to it going forward.
5. **At least one theme/subtheme assignment that survives a "would an
   analyst dispute this" test** — not necessarily a second-order
   relationship (several GO candidates above, e.g. Yaskawa, GE Vernova,
   have no independently-confirmed named relationship and are still
   recommended GO), but the *theme fit itself* should not be resting on
   an unflagged RESEARCH QUESTION at add-time.

A candidate satisfying 1-3 and 5 but failing 4 is **HOLD**, not
`GO` or `BLOCKED` — exactly Iridium's and ASE Technology's respective
situations in this cohort, for two structurally different reasons
(one a pending-merger timing risk, the other a systematic
under-capture risk).

### 2.2 Representing multi-listing / foreign-issuer companies

None of this cohort's candidates carry a genuine dual-primary-listing
situation (e.g., a company with two co-equal primary listings on
different exchanges) — every candidate here has exactly one clear
primary listing and adapter route. For the general case, though, the
existing `TrackedCompany` shape (`source` is a single value; `exchange`
is a single value) has no field for "also listed on X" — a real,
if not yet triggered, limitation.

**Recommendation for a future multi-listing candidate**: track it
under its **single most-liquid, most-disclosure-rich listing** (almost
always its home-market primary listing for a foreign-private-issuer
ADR structure, since the ADR itself is what actually files 20-F/6-K
with the SEC — exactly ASE Technology's, TSMC's, and ASML's situation:
each has real operations and a home-exchange listing in
Taiwan/Netherlands respectively, but this registry can only ever
observe their SEC-filed disclosure stream, not a home-exchange one,
since no adapter exists for TWSE or Euronext Amsterdam). Document the
other listing(s) in `notes` for context, but do not attempt to
represent them as a second `TrackedCompany` row for the same legal
entity — that would risk double-counting a single company's signal
across two registry entries with no existing de-duplication logic to
prevent it.

### 2.3 Handling an unsupported-exchange company without false scan-eligibility

The risk this guards against: a future contributor sees a company in a
research document, finds it plausible and well-evidenced, and adds it
to `tracked_companies.py` with `source="SEC EDGAR"` (or DART/EDINET)
by copy-paste habit, without verifying that source actually covers
that company's real listing. **Recommendation**: before any registry
addition, the identity-verification step in §2.1 (item 1) must
specifically confirm the *live* adapter-queryable identity exists —
a CIK that 404s, an EDINET code-list search that returns zero rows, or
a DART `corp_code` lookup that returns "No data found" (status `013`)
should each be treated as a hard stop, not a "try the next listing"
fallback. None of Nanya Technology, Winbond Electronics, Innolight, or
Siemens Energy (§4) would pass this check today, by construction — a
TWSE/Shenzhen/Frankfurt company has no CIK, no EDINET code, and no
DART `corp_code` to even attempt to resolve. The absence of a plausible
identifier to try in the first place — not a failed lookup — is itself
the correct signal that a company belongs in an "unsupported exchange"
category (§4), distinct from a company whose identifier resolution
merely failed for a fixable reason (e.g. missing API key, as Hanmi/HD
Hyundai Electric were until this session).

### 2.4 Fixture/test matrix for a later implementation

Proposed, mapped to the existing test-suite's own established pattern
(fixture-per-source, before/after promotion outcome, per the style
already used for EDINET/EDGAR/DART work earlier in this project):

| Fixture | Source | What it exercises | Expected outcome |
|---|---|---|---|
| `hanmi_semiconductor_dart_quarterly_report` | DART | Generic Korean-keyword lexicon match on a routine quarterly-report title | Promotes (existing DART path, no new logic) |
| `hanmi_semiconductor_dart_capacity_expansion_title` | DART | Lexicon match on a capacity-expansion-styled title, the theme's actual event-trigger case | Promotes with `High` confidence if 2+ categories match |
| `nabtesco_edinet_extraordinary_report_robotics_segment` | EDINET | Extraordinary-report triplet match, robotics-segment content | Promotes (existing 3-triplet path) |
| `nabtesco_edinet_extraordinary_report_rail_segment` | EDINET | Same triplet match, but rail-segment content (the multi-segment caveat) | Promotes today (triplet-only classification, no segment filter exists) — **documents the known limitation, does not fix it** |
| `harmonic_drive_edinet_annual_securities_report` | EDINET | Annual securities-report triplet match for a brand-new filer | Promotes (no filer-specific configuration exists to be missing) |
| `yaskawa_edinet_extraordinary_report` | EDINET | Same triplet path, industrial-robot OEM context | Promotes |
| `ase_technology_20f_annual_report` | SEC EDGAR | `20-F` via `FORM_TYPE_CATEGORIES` | Promotes |
| `ase_technology_6k_opaque_filename` | SEC EDGAR | A real observed filename shape (`dp253117_6k.htm`) through `evaluate_six_k()` | **Does not promote** — the exact, now-documented failure case; this fixture's purpose is to make the gap visible in the test suite, not to fix it |
| `ase_technology_6k_hypothetical_descriptive_filename` | SEC EDGAR | A counterfactual descriptive filename (e.g. `form6-kquarterlyfilings.htm`, ASML's real pattern) through the same function | Promotes — demonstrates the gate *would* work if ASE's naming convention were descriptive, isolating "this filer's naming convention" as the specific variable, not the gate logic itself |
| `ge_vernova_8k_material_agreement` | SEC EDGAR | Standard 8-K item-number path | Promotes |
| `digital_realty_10q_earnings` | SEC EDGAR | Standard 10-Q path | Promotes |
| `digital_realty_424b7_unmapped_form` | SEC EDGAR | The observed-but-unmapped `424B7` form | Does not promote — documents the minor gap noted in §1.8, does not fix it |
| `equinix_8k_material_agreement` | SEC EDGAR | Standard 8-K path | Promotes |
| `firefly_aerospace_8k_launch_manifest` | SEC EDGAR | Standard 8-K path, space-launch content | Promotes |
| `l3harris_8k_multi_segment_filtering` | SEC EDGAR | An 8-K with no space-specific content (a defense-electronics-only disclosure) | Promotes today (no segment filter exists) — same "documents, does not fix" pattern as the Nabtesco rail-segment fixture |
| `iridium_425_merger_communication` | SEC EDGAR | A `425` merger-communication filing | **Does not promote today** — `425` is not in `FORM_TYPE_CATEGORIES`; worth a fixture regardless of whether Iridium itself is added, since a pending-merger 425/DEFM14A filing is exactly the kind of event a future "pending corporate action" detection rule (§2.1, item 4) would need to recognize |
| `viasat_8k_material_agreement` | SEC EDGAR | Standard 8-K path | Promotes |
| `registry_scope_guard_no_unsupported_exchange_entry` | n/a (registry-level) | Asserts no `TrackedCompany` row is ever added with a `source` value outside `{"SEC EDGAR", "EDINET", "OpenDART / DART"}` | A structural guard against exactly the §2.3 risk — TWSE/Shenzhen/Frankfurt companies could never pass it |

### 2.5 Final GO / HOLD / BLOCKED table

| # | Company | Theme | Adapter route | Status |
|---|---|---|---|---|
| 1 | Hanmi Semiconductor Co., Ltd. | memory / ai-buildout | OpenDART / DART | **GO** |
| 2 | Nabtesco Corporation | humanoids | EDINET | **GO** |
| 3 | Harmonic Drive Systems Inc. | humanoids | EDINET | **GO** |
| 4 | YASKAWA Electric Corporation | humanoids | EDINET | **GO** |
| 5 | ASE Technology Holding Co., Ltd. | ai-buildout | SEC EDGAR (20-F/6-K) | **HOLD** — 20-F works; 6-K systematically under-captured (opaque filenames) |
| 6 | GE Vernova Inc. | ai-buildout | SEC EDGAR | **GO** |
| 7 | HD Hyundai Electric Co., Ltd. | ai-buildout | OpenDART / DART | **GO** |
| 8 | Digital Realty Trust, Inc. | ai-buildout | SEC EDGAR | **GO** |
| 9 | Equinix, Inc. | ai-buildout | SEC EDGAR | **GO** |
| 10 | Firefly Aerospace Inc. | space | SEC EDGAR | **GO** |
| 11 | L3Harris Technologies, Inc. | space | SEC EDGAR | **GO** |
| 12 | Iridium Communications Inc. | space | SEC EDGAR | **HOLD** — pending $8.0B acquisition by Rocket Lab (already tracked), expected close mid-2027 |
| 13 | Viasat, Inc. | space | SEC EDGAR | **GO** |

**10 GO, 2 HOLD, 0 BLOCKED** in this cohort. (`BLOCKED` is reserved
for the six explicitly excluded companies — §4.)

---

## 3. Adapter routes supporting the GO candidates

- **SEC EDGAR, standard domestic-filer path** (10-Q/10-K/8-K/13G,
  already fully built, no change needed): GE Vernova, Digital Realty,
  Equinix, Firefly Aerospace, L3Harris Technologies, Viasat. Six of
  the ten GO candidates.
- **OpenDART / DART, generic Korean-keyword lexicon path** (already
  fully built, no change needed): Hanmi Semiconductor, HD Hyundai
  Electric. Both were blocked only by a missing API key, not a code
  gap — now resolved (headline finding 1).
- **EDINET, triplet-based extraordinary/annual-report path** (already
  fully built for the 3 known triplets, no change needed): Nabtesco,
  Harmonic Drive Systems, YASKAWA Electric.

No new source adapter and no change to any existing adapter's rule set
is required for any of the 10 GO candidates.

---

## 4. Adapter-blocked follow-up (TSMC, ASML, Nanya, Winbond, Innolight, Siemens Energy)

Kept out of Cohort 1 per instruction. Status, corrected where this
session's own code inspection changed the picture:

- **TSMC, ASML**: the prior research document's `REQUIRES_ADAPTER_
  CHANGE` finding is **stale** — the specific gap it identified
  (`FORM_TYPE_CATEGORIES` missing `20-F`/`6-K`) was closed by this
  project's own already-merged PR #56, before this document was
  written. Both companies are foreign private issuers filing 20-F/6-K
  with the SEC, exactly like ASE Technology Holding (§1.5). Whether
  either is a clean GO, a HOLD (ASE Technology's outcome), or
  something else depends entirely on the same filer-specific 6-K
  filename check this document ran for ASE Technology — **not
  independently re-run for TSMC or ASML in this document**, since they
  are explicitly out of scope for Cohort 1. This is the single most
  important correction in this document and should inform whichever
  future session evaluates them: **do not re-apply the "REQUIRES_
  ADAPTER_CHANGE" label to either without first re-checking today's
  code**, and expect their disposition to hinge on filename
  descriptiveness, not on missing 20-F/6-K support.
- **Nanya Technology, Winbond Electronics**: still genuinely
  unsupported — Taiwan Stock Exchange listings with no US-listed ADR
  identified, no SEC CIK, no EDINET code, no DART corp_code possible
  even in principle. Requires a new TWSE source adapter — a
  substantially larger effort than any filename-gate refinement,
  out of scope for any near-term registry addition.
- **Innolight**: still genuinely unsupported — Shenzhen Stock Exchange
  (China A-share) listing, no adapter of any kind exists for a
  mainland China exchange in this codebase. Same category of effort
  as Nanya/Winbond, for a different exchange.
- **Siemens Energy**: still genuinely unsupported — Frankfurt Stock
  Exchange listing, no adapter exists for a European exchange in this
  codebase (Netherlands-domiciled ASML is representable only because
  it separately files with the SEC as a foreign private issuer;
  Siemens Energy has no equivalent US SEC filing presence identified
  this session — **RESEARCH LEAD — requires verification** on whether
  it files any US-reportable security at all).

None of these six were added, staged, or otherwise acted upon in this
document beyond the status correction above.

---

## Files read / created

**Read (research inputs only, none modified)**:
`design/THEMATIC_COVERAGE_UNIVERSE_AUDIT_2026_09_15.md`,
`design/COVERAGE_EXPANSION_FIRST10_REGISTRY_READINESS_2026_09_15.md`,
`src/config/tracked_companies.py` (dataclass shape and representative
entries, read-only), `src/data_access/edgar/edgar_rules.py` (read-only,
full file), `src/data_access/dart/client.py` (read-only).

**Live data fetched this session (read-only, no cache/file written by
this document's own process beyond this document itself)**: SEC EDGAR
`data.sec.gov/submissions/CIK##########.json` for 8 CIKs (ASE
Technology Holding, GE Vernova, Digital Realty, Equinix, L3Harris,
Iridium Communications, Viasat, Firefly Aerospace — the last
re-confirming the prior document's finding); SEC EDGAR
`browse-edgar?action=getcompany` search for the same 8 companies'
initial CIK lookup; EDINET's official code-list bulk file
(`Edinetcode.zip`, 11,389 entries, downloaded and parsed fresh this
session) for Nabtesco (re-confirmed), Harmonic Drive Systems, and
YASKAWA Electric; OpenDART's live `corpCode.xml` bulk endpoint
(119,281 records), fetched via the project's own `DartClient` (used
read-only — no cache file written, no registry entry created) for
Hanmi Semiconductor and HD Hyundai Electric; one web search
corroborating the Iridium/Rocket Lab merger.

**Not touched**: `tests/test_radar_status.py` (not opened);
`design/COVERAGE_TAXONOMY_AUDIT_2026_09_15.md`,
`design/COVERAGE_EXPANSION_CANDIDATES_TIER1_2026_09_15.md`,
`design/RESEARCH_THESES_ACTIVE_DORMANT.md` (not re-read or modified
this turn — not needed for this document's scope).

**Created**: `design/TIER1_COHORT1_REGISTRY_READINESS_2026_09_15.md`
(this document — untracked, not staged, not committed).

No code, config, schema, test, or production-data file was modified.
No git operation beyond read-only status checks was performed.
Nothing was staged, committed, pushed, merged, or deployed.
