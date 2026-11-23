# Coverage Expansion Candidates — Tier 1 (2026-09-15)

Read-only research/design document. Phase 2 of the coverage-expansion
work, building on `design/COVERAGE_TAXONOMY_AUDIT_2026_09_15.md`'s
Critical/High gap rankings. **No code, schema, `tracked_companies.py`,
theme registry, or production data was touched to produce this
document** — every candidate below is a research proposal only.

## Methodology

21 candidates (under the 25 cap), each selected to fill one or more
*named* Critical/High gap from the Phase 1 audit — not thematic
adjacency. Every candidate's legal name, ticker, and exchange was
verified via live web search on 2026-09-15 (cited per entry, with the
search date). Regulator/monitoring eligibility (EDGAR/EDINET/DART) is
assessed by exchange/domicile only — **no CIK/EDINET-code/corp_code
cross-check was performed**, matching this project's own established
two-step process (`tracked_companies.py`'s "Core Issuer Expansion
batch" entries: identify the candidate first, live-verify the exact
identifier as a separate, later step). Where this document's search
results happened to surface a real CIK, it's noted as a convenience,
not as a substitute for that later verification step.

Evidence tiers used throughout (same convention as Phase 1):
- **[Primary/regulatory]** — an SEC/company/regulator source.
- **[Trade press]** — a named industry publication (TheElec,
  TrendForce, etc.) — credible and specific, but explicitly *not* a
  primary company/regulatory source. Per instruction, any
  customer/supplier/partner claim resting only on this tier is marked
  **RESEARCH QUESTION — requires verification**, never presented as
  confirmed.
- **[Established public profile]** — foundational, widely-reported
  facts about a large-cap public company's core business.

---

## Section A — Supported-market public issuers (could be added after identity verification)

### A1. Taiwan Semiconductor Manufacturing Company Limited
- **Ticker/exchange/jurisdiction**: TSM, NYSE (ADR — 5 common shares
  per ADS), Taiwan-domiciled. **EDGAR-monitorable today**: yes — active
  SEC filer, CIK 0001046179, files Form 20-F (annual) and 6-K (interim),
  the standard foreign-private-issuer forms — same filing-type pattern
  already handled for Arm Holdings (UK) in the current registry.
- **Entity class**: PUBLIC_ACTIVE_CANDIDATE
- **Primary theme / subtheme / segment / role**: ai-buildout /
  foundry-manufacturing / Foundry / Contract wafer foundry (pure-play,
  world's largest by revenue and process-node leadership)
  [Established public profile]
- **Gap filled**: AI Buildout — Foundry (Critical — Phase 1's single
  most severe AI Buildout gap; the only two currently-tracked names,
  Tower Semiconductor and GlobalFoundries, are both specialty/
  non-leading-edge)
- **Second-order signal rationale**: capacity/capex disclosures here
  are a leading indicator for essentially every fabless AI-accelerator
  designer already tracked (NVIDIA, AMD, Broadcom, Arm) — a capacity
  constraint or expansion announcement here precedes and explains
  demand-side commentary from those companies.
- **Event types to monitor**: capex guidance changes, new fab/node
  announcements (e.g. advanced-node capacity expansions), major
  customer disclosures in filings, export-control/geopolitical
  disclosures (Taiwan-specific risk factors).
- **Evidence**: [Primary/regulatory] SEC EDGAR filer record, CIK
  0001046179, NYSE ticker TSM — confirmed via SEC.gov filing index and
  NYSE listing history, searched 2026-09-15. Confidence: High.
- **Relationships**: None asserted beyond its foundational, universally
  reported role as a foundry to the semiconductor industry generally.

### A2. ASML Holding N.V.
- **Ticker/exchange/jurisdiction**: ASML, NASDAQ, Netherlands-domiciled.
  **EDGAR-monitorable today**: yes — active SEC filer (foreign private
  issuer, 20-F/6-K forms), CIK 0000937966.
- **Entity class**: PUBLIC_ACTIVE_CANDIDATE
- **Primary theme / subtheme / segment / role**: ai-buildout /
  lithography-equipment / Tools/materials / Sole global supplier of
  extreme-ultraviolet (EUV) lithography systems [Established public
  profile]
- **Gap filled**: AI Buildout — Tools/materials (this specific
  sub-segment — EUV lithography — is a single-supplier global
  bottleneck; the tools/materials segment overall is already the
  registry's deepest, but this exact chokepoint has no coverage today)
- **Second-order signal rationale**: EUV tool order backlog/shipment
  timing is a direct leading indicator for leading-edge capacity
  additions at every foundry and IDM in the registry (Samsung
  Electronics, SK Hynix, Micron, Tower Semiconductor, GlobalFoundries,
  and TSMC if added per A1).
- **Event types to monitor**: EUV/High-NA EUV shipment and order
  disclosures, export-control developments (Dutch government licensing
  of EUV exports to China has been a recurring, market-moving event
  type), capex guidance.
- **Evidence**: [Primary/regulatory] SEC EDGAR filer record, CIK
  0000937966, NASDAQ ticker ASML — searched 2026-09-15. Confidence:
  High.
- **Relationships**: None asserted.

### A3. Hewlett Packard Enterprise Company
- **Ticker/exchange/jurisdiction**: HPE, NYSE, US-domiciled. **EDGAR-
  monitorable today**: yes — long-standing US SEC filer.
- **Entity class**: PUBLIC_ACTIVE_CANDIDATE
- **Primary theme / subtheme / segment / role**: ai-buildout /
  (none of the existing subthemes fit — server systems, not a
  component) / Servers/storage / AI server and infrastructure systems
  vendor [Established public profile, corroborated by recent-quarter
  AI-order disclosures]
- **Gap filled**: AI Buildout — Servers/storage (Medium gap; Phase 1
  flagged the absence of a major enterprise AI-server peer to the
  already-tracked Dell/Super Micro/CoreWeave/Penguin/Nebius cohort)
- **Second-order signal rationale**: server order backlog and AI
  segment revenue are a direct demand-side read on hyperscaler/
  enterprise AI capex, complementing the hyperscaler filings already
  tracked (Alphabet, Amazon, Microsoft, Meta, Oracle).
- **Event types to monitor**: quarterly AI order/backlog disclosures,
  capacity/supply-chain commentary, major contract wins.
- **Evidence**: [Established public profile] NYSE:HPE, "AI orders
  surged to $2.1B in 2Q26" per company quarterly reporting as
  summarized in trade/financial press — searched 2026-09-15. Confidence:
  Medium-High (business classification well-established; exact figures
  are trade-press-reported, not pulled directly from a primary filing
  in this session).
- **Relationships**: None asserted.

### A4. GE Vernova Inc.
- **Ticker/exchange/jurisdiction**: GEV, NYSE, US-domiciled (2024
  spin-off from General Electric). **EDGAR-monitorable today**: yes,
  CIK 1996810.
- **Entity class**: PUBLIC_ACTIVE_CANDIDATE
- **Primary theme / subtheme / segment / role**: ai-buildout /
  power-cooling / Power/grid/generation / Gas turbines, grid equipment,
  and electrification systems [Established public profile]
- **Gap filled**: AI Buildout — Power/grid/generation. This segment is
  already relatively well-covered (Bloom Energy, Constellation Energy,
  Generac, Quanta Services, etc. per Phase 1), but GE Vernova is a
  distinctly larger-scale gas-turbine/grid-equipment name with reported
  multi-year AI-datacenter-driven order backlogs — a depth addition to
  an already-strong segment, included here specifically because of its
  scale and the severity of grid-interconnect/turbine-lead-time
  bottlenecks in AI datacenter buildouts.
- **Second-order signal rationale**: gas-turbine order backlog and
  delivery lead times are a widely-cited bottleneck for new AI
  datacenter power availability — a leading indicator for the pace of
  datacenter capacity additions industry-wide.
- **Event types to monitor**: turbine order backlog disclosures, grid
  equipment capacity expansion announcements, major AI-datacenter power
  supply agreements.
- **Evidence**: [Primary/regulatory] SEC EDGAR CIK 1996810, NYSE:GEV —
  searched 2026-09-15. Confidence: High.
- **Relationships**: None asserted.

### A5. HD Hyundai Electric Co., Ltd. (formerly Hyundai Electric & Energy Systems Co., Ltd.)
- **Ticker/exchange/jurisdiction**: 267260, KRX, South Korea. **DART-
  monitorable today**: yes — this project already has a working
  OpenDART/DART adapter for 10 KRX-listed companies.
- **Entity class**: PUBLIC_ACTIVE_CANDIDATE
- **Primary theme / subtheme / segment / role**: ai-buildout / (no
  existing subtheme fits precisely — closest is `power-cooling`,
  reused) / Power/grid/generation / Transformers, switchgear, and
  grid-interconnect equipment manufacturer [Established public profile]
- **Gap filled**: AI Buildout — Power/grid/generation, specifically the
  transformer/grid-interconnect-equipment sub-segment, which is a
  widely-reported global bottleneck for new AI datacenter power
  connections — this specific sub-segment has no Korea-market
  representation today.
- **Second-order signal rationale**: transformer order backlog and
  delivery lead times are a direct, frequently-cited constraint on how
  fast new AI datacenter capacity can actually be energized — relevant
  context for every AI Buildout demand-side and power-segment company
  already tracked.
- **Event types to monitor**: order backlog disclosures, capacity
  expansion announcements, major grid-equipment supply contracts.
- **Evidence**: [Established public profile] KRX 267260, company
  renamed from "Hyundai Electric & Energy Systems" to "HD Hyundai
  Electric" — searched 2026-09-15. Confidence: High for identity;
  Medium for the "critical AI-datacenter bottleneck" framing (well
  reported in industry press, not yet cross-checked against a primary
  regulatory source in this session).
- **Relationships**: None asserted.

### A6. Tesla, Inc.
- **Ticker/exchange/jurisdiction**: TSLA, NASDAQ, US-domiciled. **EDGAR-
  monitorable today**: yes — long-standing major US SEC filer.
- **Entity class**: PUBLIC_ACTIVE_CANDIDATE
- **Primary theme / subtheme / segment / role**: humanoids / (no
  existing subtheme fits) / OEM / Humanoid-robot platform developer
  (Optimus program) [Established public profile, with an important
  maturity caveat below]
- **Gap filled**: Humanoids — OEM (Critical — the single most severe
  gap identified in Phase 1; zero humanoid-form-factor OEM is currently
  tracked)
- **Second-order signal rationale**: as the OEM, Tesla's own component
  sourcing and production-ramp disclosures would be a leading indicator
  for demand at any actuator/motor/reducer/sensor supplier this
  registry later adds (Section 4/C below and Phase 1's still-Critical
  actuator/reducer/encoder/battery gaps).
- **Event types to monitor**: Optimus production-line status updates,
  unit-cost/manufacturing-ramp disclosures, named component-supplier
  announcements, any enterprise-customer sales disclosures.
- **Maturity caveat** (directly from search results, not omitted): as
  of mid-2026 the program remains pre-revenue and explicitly
  "research phase" per the company's own Q4 2025 earnings call —
  production-line installation only began around July–August 2026, and
  consumer availability is targeted for roughly end of 2027. This is
  an early-stage, not yet commercially proven, program — flagged
  explicitly so this isn't read as a mature production business.
- **Evidence**: [Established public profile / company-disclosed via
  earnings call, as reported in trade/financial press] — searched
  2026-09-15, multiple sources including Wikipedia's "Optimus (robot)"
  entry and contemporary AI/robotics trade coverage. Confidence: High
  for program's existence and stage; Medium for specific technical
  claims (actuator count, battery capacity), which are trade-press-
  sourced, not pulled from a primary Tesla filing in this session.
- **Relationships**: None asserted.

### A7. Nabtesco Corporation
- **Ticker/exchange/jurisdiction**: 6268, TSE, Japan. **EDINET-
  monitorable today**: yes — this project already has a working EDINET
  adapter for 18 TSE-listed companies.
- **Entity class**: PUBLIC_ACTIVE_CANDIDATE
- **Primary theme / subtheme / segment / role**: humanoids / (no
  existing subtheme fits — propose `precision-reducers`) / Reducers /
  Precision reduction-gear manufacturer, reported ~60% global market
  share in RV (cycloidal) reducers for industrial/humanoid robot joints
  [Established public profile, multiple industry sources]
- **Gap filled**: Humanoids — Reducers (Critical — zero coverage in
  Phase 1; precision reducers are a widely-documented humanoid-robot
  supply-chain bottleneck)
- **Second-order signal rationale**: Nabtesco's RV reducers and
  Harmonic Drive Systems' strain-wave gearing are, per industry
  reporting, the two dominant suppliers for large- and small-joint
  robot actuation respectively — "so standard in industrial robot
  design that a supply chain disruption at either Japanese company
  would halt global robot production at most major OEMs" (per search
  results, itself citing industry analysis, not a primary source). A
  capacity or order-backlog event here is a leading indicator across
  every humanoid/industrial-robotics OEM.
- **Event types to monitor**: production capacity expansion, order
  backlog for robotics-segment reducers specifically (vs. Nabtesco's
  other segments — rail, aircraft, construction-equipment
  components), major customer/OEM supply agreements.
- **Evidence**: [Established public profile, industry-analysis tier]
  — searched 2026-09-15, including Nabtesco's own product pages and
  independent robotics-industry analysis. Confidence: High for market
  position; the specific "60% global market share" figure is
  industry-analysis-sourced, not independently verified against a
  primary Nabtesco disclosure in this session.
- **Relationships**: **RESEARCH QUESTION — requires verification.**
  Industry commentary describes Nabtesco and Harmonic Drive Systems as
  the two dominant, complementary reducer suppliers to "most major
  [robot] OEMs" — no specific named OEM relationship (e.g., to any
  company already or newly tracked in this registry) is asserted here.
  **Deferred candidate noted**: Harmonic Drive Systems Inc. (also
  TSE-listed) is a second, comparably significant Japan-market reducer
  maker — this document does not include it as a full candidate
  alongside Nabtesco (per the "prefer fewer... don't pad" instruction,
  and because this session could not independently confirm its exact
  TSE code), but flags it as the natural next name to verify if this
  segment's Critical rating persists after Nabtesco is evaluated.

### A8. MinebeaMitsumi Inc.
- **Ticker/exchange/jurisdiction**: 6479, TSE, Japan. **EDINET-
  monitorable today**: yes.
- **Entity class**: PUBLIC_ACTIVE_CANDIDATE
- **Primary theme / subtheme / segment / role**: humanoids / (no
  existing subtheme fits — propose `precision-motors-bearings`) /
  Motors / Miniature bearings, precision motors, and sensors — one of
  few companies globally that makes bearings, motors, and sensors
  in-house [Established public profile]
- **Secondary theme candidate**: ai-buildout (the company's own
  investor materials reportedly also frame diversification toward
  "DC and physical AI" component demand — not independently confirmed
  here; **RESEARCH QUESTION**).
- **Gap filled**: Humanoids — Motors (Critical) and Sensors (High) —
  a single company addressing two of Phase 1's most severe component
  gaps.
- **Second-order signal rationale**: as a vertically-integrated
  motor/bearing/sensor supplier, MinebeaMitsumi's segment-level
  capacity and order disclosures are a broad leading indicator across
  precision-motion component demand for any humanoid or industrial
  robotics OEM.
- **Event types to monitor**: segment-level (bearings/motors/sensors)
  revenue and capacity disclosures, named robotics/automotive customer
  wins, capital-expenditure announcements tied to precision-motor
  capacity.
- **Evidence**: [Established public profile] TSE 6479 — searched
  2026-09-15, including the company's own product pages. Confidence:
  High for business description; the "physical AI" diversification
  framing is a single secondary-source (a Japanese-language investor
  commentary blog) and is marked RESEARCH QUESTION above rather than
  asserted.
- **Relationships**: None asserted beyond the RESEARCH QUESTION noted.

### A9. Cognex Corporation
- **Ticker/exchange/jurisdiction**: CGNX, NASDAQ, US-domiciled. **EDGAR-
  monitorable today**: yes, CIK 851205.
- **Entity class**: PUBLIC_ACTIVE_CANDIDATE
- **Primary theme / subtheme / segment / role**: humanoids / (no
  existing subtheme fits — propose `machine-vision`) / Vision /
  Machine-vision systems, software, and sensors for automated
  manufacturing and robot guidance [Established public profile,
  corroborated by the company's own 10-K filings on record with SEC
  EDGAR]
- **Gap filled**: Humanoids — Vision (High gap; the only current
  vision-adjacent name, indie Semiconductor, is automotive-ADAS-
  focused per the registry's own note, not a clean humanoid-vision fit)
- **Second-order signal rationale**: machine-vision order volume and
  new-product cycles are a leading indicator for automation/robotics
  capex broadly, including at the industrial-automation names already
  tracked (Rockwell Automation, FANUC, Doosan Robotics, Mitsubishi
  Electric, Symbotic).
- **Event types to monitor**: new product launches (vision systems for
  robot guidance specifically), major automation-customer contract
  disclosures, revenue-segment commentary on robotics/logistics demand.
- **Evidence**: [Primary/regulatory] SEC EDGAR filer, CIK 851205,
  NASDAQ:CGNX — 10-K filings directly located on SEC.gov in search
  results, searched 2026-09-15. Confidence: High.
- **Relationships**: None asserted.

### A10. Panasonic Holdings Corporation
- **Ticker/exchange/jurisdiction**: 6752, TSE, Japan. **EDINET-
  monitorable today**: yes.
- **Entity class**: PUBLIC_ACTIVE_CANDIDATE
- **Primary theme / subtheme / segment / role**: humanoids / (no
  existing subtheme fits — propose `batteries`) / Batteries / EV/
  high-capacity battery-cell manufacturer (4680-format cells) —
  [Established public profile]
- **Secondary theme candidate**: ai-buildout (battery/energy-storage
  relevance to datacenter backup power is plausible but not
  independently confirmed here; **RESEARCH QUESTION**).
- **Gap filled**: Humanoids — Batteries (Critical — zero battery
  coverage anywhere in the entire 105-company registry, not just this
  theme)
- **Second-order signal rationale**: battery cell capacity, cost curve,
  and named-customer disclosures are directly relevant to any
  battery-powered robotics OEM, including Tesla (A6) if added.
- **Event types to monitor**: new battery-plant capacity announcements,
  named-customer supply agreements, cell-cost/yield disclosures.
- **Evidence**: [Trade press, multiple independent outlets — Reuters
  via Investing.com, Green Car Reports, Yahoo Finance] — searched
  2026-09-15. Confidence: High for the battery business itself
  (long-established, widely reported); the Tesla-specific relationship
  is addressed separately below.
- **Relationships**: Panasonic Energy (a Panasonic Holdings subsidiary)
  is widely reported in trade press as a long-standing Tesla battery
  supplier and a partner in Tesla's Nevada "Gigafactory." **This
  specific claim rests on trade-press reporting (Reuters/Investing.com,
  Green Car Reports), not a primary SEC/EDINET filing pulled in this
  session — marked RESEARCH QUESTION — requires verification** per
  instruction, despite being very widely and consistently reported.

### A11. Hanmi Semiconductor Co., Ltd.
- **Ticker/exchange/jurisdiction**: 042700, KRX, South Korea. **DART-
  monitorable today**: yes.
- **Entity class**: PUBLIC_ACTIVE_CANDIDATE
- **Primary theme / subtheme / segment / role**: memory / (no existing
  subtheme fits — propose `hbm-packaging-equipment`) / Packaging/TSV /
  Thermal-compression (TC) bonding equipment for HBM stacking —
  reported ~80% global market share in this specific equipment
  category [Established public profile / industry trade press]
- **Secondary theme candidate**: ai-buildout (HBM feeds AI accelerator
  memory bandwidth — same bridge logic already applied to Samsung
  Electronics/SK Hynix in the live registry).
- **Gap filled**: Memory — Packaging/TSV (High gap — the single most
  specific, well-evidenced candidate found in this entire research
  pass)
- **Second-order signal rationale**: as the dominant HBM TC-bonder
  supplier, Hanmi's own order backlog is a direct, near-real-time
  leading indicator for HBM4/HBM5 production ramp timing at both
  already-tracked Korean memory makers (Samsung Electronics, SK Hynix).
- **Event types to monitor**: named customer order announcements
  (see below), new-generation equipment launches (e.g. "Wide TC
  Bonder" for HBM5/HBM6), capacity-expansion disclosures.
- **Evidence**: [Trade press — TheElec, TrendForce, both specialist
  Korea/semiconductor-industry trade publications with a track record
  of citing primary disclosures] — searched 2026-09-15. Confidence:
  High for the company's core business and market position; Medium for
  the specific order figures below (trade-press-reported).
- **Relationships**: TheElec ("Hanmi Semiconductor Wins First HBM4 TC
  Bonder Order From SK hynix") and TrendForce ("SK hynix Reportedly
  Places 44.2bn Won TC Bonder Order with Hanmi") both report a specific
  Hanmi–SK Hynix supply relationship, dated 2026-06. **This rests on
  trade-press reporting of the two companies' own disclosures, not a
  primary SEC/DART filing independently pulled in this session — marked
  RESEARCH QUESTION — requires verification** per instruction, despite
  being specific, dated, and sourced to two independent industry
  publications (unusually strong for this tier).

### A12. Kulicke & Soffa Industries, Inc.
- **Ticker/exchange/jurisdiction**: KLIC, NASDAQ, US-domiciled
  (Pennsylvania-incorporated). **EDGAR-monitorable today**: yes, CIK
  0000056978.
- **Entity class**: PUBLIC_ACTIVE_CANDIDATE
- **Primary theme / subtheme / segment / role**: ai-buildout /
  semiconductor-test (closest existing fit, imperfect — propose
  `wire-bonding-packaging-equipment` instead) / Advanced
  packaging/test / World's largest wire-bonding and semiconductor
  assembly equipment maker [Primary/regulatory — SEC EDGAR filer
  record confirms identity; business description is Established public
  profile]
- **Secondary theme candidate**: memory (packaging equipment serves
  memory assembly too, same cross-theme logic as Amkor/Ibiden already
  in the registry under ai-buildout only).
- **Gap filled**: AI Buildout — Advanced packaging/test (Medium gap;
  adds equipment-layer depth alongside the already-tracked OSAT names)
- **Second-order signal rationale**: order flow here is a direct
  leading indicator for AI-chip assembly capacity additions across the
  already-tracked OSAT cohort (Amkor, SFA Semicon, Hana Micron, Ibiden).
- **Event types to monitor**: order backlog disclosures tied to AI
  semiconductor demand specifically, new advanced-packaging product
  launches.
- **Evidence**: [Primary/regulatory] SEC EDGAR CIK 0000056978,
  NASDAQ:KLIC — confirmed directly via SEC.gov EDGAR browse page,
  searched 2026-09-15. Confidence: High.
- **Relationships**: None asserted.

### A13. Ciena Corporation
- **Ticker/exchange/jurisdiction**: CIEN, NYSE, US-domiciled. **EDGAR-
  monitorable today**: yes.
- **Entity class**: PUBLIC_ACTIVE_CANDIDATE
- **Primary theme / subtheme / segment / role**: photonics / (no
  existing subtheme fits — propose `optical-networking-systems`) /
  Networking/optics (systems layer, distinct from the component-layer
  names already tracked) / Optical networking systems and coherent
  optics vendor [Established public profile]
- **Gap filled**: Photonics — a systems-layer name distinct from the
  component-makers already tracked (Coherent, Lumentum, Applied
  Optoelectronics, Marvell, MaxLinear) — search results note Ciena's
  own strategic focus is "WAN connectivity, scale-across networking and
  pluggable optics," a different competitive position from Coherent
  Corp's CPO/silicon-photonics focus, so this is not a duplicate of an
  existing name.
- **Second-order signal rationale**: as a systems integrator, Ciena's
  component sourcing and order commentary is a downstream read on
  demand for the component-layer names already tracked.
- **Event types to monitor**: coherent-optics product cycle
  announcements, named hyperscaler/carrier contract wins, capacity
  commentary.
- **Evidence**: [Established public profile] NYSE:CIEN — searched
  2026-09-15. Confidence: High for identity; Medium for the specific
  "distinct from Coherent" positioning claim (trade-press comparative
  analysis, not a primary source).
- **Relationships**: None asserted.

### A14. Fabrinet
- **Ticker/exchange/jurisdiction**: FN, NYSE (note: not NASDAQ, despite
  this document's own initial search query assuming otherwise —
  corrected here), Thailand-headquartered, Cayman Islands-incorporated.
  **EDGAR-monitorable today**: yes, CIK 0001408710.
- **Entity class**: PUBLIC_ACTIVE_CANDIDATE
- **Primary theme / subtheme / segment / role**: photonics / (no
  existing subtheme fits — propose `optical-packaging-assembly`) /
  Packaging/assembly / Advanced optical packaging and precision
  electro-mechanical manufacturing services for optical communication
  components/modules [Primary/regulatory — SEC EDGAR record;
  business description Established public profile]
- **Gap filled**: Photonics — Packaging/assembly (High gap — zero
  dedicated coverage identified in Phase 1)
- **Second-order signal rationale**: as a contract manufacturer for
  optical components, Fabrinet's revenue/capacity disclosures are a
  direct read on order volume across its (undisclosed-by-name-here)
  optical-component customer base, likely overlapping several already-
  tracked photonics names.
- **Event types to monitor**: capacity-expansion announcements
  (Thailand facility expansions specifically), revenue-segment
  commentary on optical/high-performance-compute manufacturing.
- **Evidence**: [Primary/regulatory] SEC EDGAR CIK 0001408710,
  NYSE:FN — multiple SEC.gov filing links confirmed directly in search
  results, searched 2026-09-15. Confidence: High.
- **Relationships**: **RESEARCH QUESTION — requires verification.**
  Fabrinet is widely known in industry commentary as a contract
  manufacturer for several major optical-component brands, but no
  specific named customer relationship is asserted here without a
  primary-source citation.

### A15. Amphenol Corporation
- **Ticker/exchange/jurisdiction**: APH, NYSE, US-domiciled. **EDGAR-
  monitorable today**: yes, CIK 820313.
- **Entity class**: PUBLIC_ACTIVE_CANDIDATE
- **Primary theme / subtheme / segment / role**: photonics / (no
  existing subtheme fits — propose `connectors`) / Connectors /
  High-speed interconnect and connector systems for AI datacenters
  [Primary/regulatory — SEC EDGAR record; business description
  Established public profile, corroborated by very recent (Q1 2026)
  order/revenue disclosures]
- **Secondary theme candidate**: ai-buildout (connectors serve general
  AI-datacenter interconnect, not exclusively optical).
- **Gap filled**: Photonics — Connectors (High gap; zero dedicated
  coverage in Phase 1)
- **Second-order signal rationale**: Amphenol's book-to-bill ratio and
  order growth are frequently cited by industry analysts as a leading
  indicator for overall AI-datacenter buildout pace — relevant context
  across nearly every AI Buildout and Photonics name already tracked.
- **Event types to monitor**: quarterly order/book-to-bill disclosures,
  M&A activity expanding fiber/connector capability (a recent CommScope
  CCS unit acquisition was reported), datacenter-specific segment
  commentary.
- **Evidence**: [Primary/regulatory] SEC EDGAR CIK 820313, NYSE:APH,
  Form ARS FY2025 located directly on SEC.gov — searched 2026-09-15.
  Confidence: High.
- **Relationships**: A recent acquisition of CommScope's "CCS" unit
  (to expand fiber-optic connectivity offerings) is reported in
  multiple trade/financial press sources. **Trade-press sourced, not
  independently confirmed against a primary SEC 8-K in this session —
  marked RESEARCH QUESTION — requires verification.**

### A16. L3Harris Technologies, Inc.
- **Ticker/exchange/jurisdiction**: LHX, NYSE, US-domiciled. **EDGAR-
  monitorable today**: yes.
- **Entity class**: PUBLIC_ACTIVE_CANDIDATE
- **Primary theme / subtheme / segment / role**: space / (no existing
  subtheme fits — propose `defense-space-systems`) / Defense space /
  Satellite and missile-defense systems integrator for US government
  customers [Primary/regulatory — 10-KT on record with SEC; business
  description Established public profile]
- **Gap filled**: Space — Defense space (Medium gap — Phase 1's only
  defense-space names, Hanwha Aerospace and Korea Aerospace Industries,
  are both Korea-only; no US defense-space pure-play is tracked)
- **Second-order signal rationale**: US government satellite/missile-
  defense contract awards here are a leading indicator for broader US
  defense-space budget allocation and program timing, complementing the
  already-tracked commercial space names (Rocket Lab, Redwire,
  Intuitive Machines, AST SpaceMobile, Planet Labs).
- **Event types to monitor**: named Space Force/DoD contract awards,
  segment-level (Space & Mission Systems) revenue disclosures.
- **Evidence**: [Primary/regulatory] SEC EDGAR record (Form 10-KT
  located directly on SEC.gov), NYSE:LHX — searched 2026-09-15.
  Confidence: High.
- **Relationships**: A specific, named US Space Force Space Development
  Agency contract ("18 Accelerated Missile Defense Tranche 3
  satellites") is reported in trade/financial press dated around
  2026-09. **This is a government contract award, plausibly disclosable
  via a primary company 8-K/press release, but this session did not
  pull that primary source directly — marked RESEARCH QUESTION —
  requires verification** for the exact terms, even though the contract
  award itself is very likely real and independently reportable via a
  primary source at implementation time.

### A17. Iridium Communications Inc.
- **Ticker/exchange/jurisdiction**: IRDM, NASDAQ, US-domiciled. **EDGAR-
  monitorable today**: yes, CIK 0001418819.
- **Entity class**: PUBLIC_ACTIVE_CANDIDATE
- **Primary theme / subtheme / segment / role**: space / (no existing
  subtheme fits — propose `satellite-communications`) / Communications/
  direct-to-device / Global satellite voice/data constellation operator
  (66 operational satellites + 14 in-orbit spares) [Primary/regulatory
  — SEC EDGAR record; business description Established public profile]
- **Gap filled**: Space — Communications/direct-to-device (Medium gap;
  adds depth alongside the already-tracked AST SpaceMobile, a different
  competitive model — direct-to-standard-phone vs. Iridium's own
  dedicated-terminal constellation)
- **Second-order signal rationale**: constellation health/replenishment
  disclosures and service-revenue trends are a leading indicator for
  the broader satellite-communications capex cycle relevant to
  AST SpaceMobile and Viasat (A21) as well.
- **Event types to monitor**: satellite replenishment/launch schedule
  disclosures, service-revenue segment commentary, government-contract
  announcements.
- **Evidence**: [Primary/regulatory] SEC EDGAR CIK 0001418819,
  NASDAQ:IRDM — multiple SEC.gov 8-K filings located directly in search
  results, searched 2026-09-15. Confidence: High.
- **Relationships**: None asserted.

### A18. Firefly Aerospace Inc.
- **Ticker/exchange/jurisdiction**: FLY, NASDAQ, US-domiciled. **EDGAR-
  monitorable today**: yes — IPO'd 2025-08-07; a genuinely new filer,
  so its EDGAR filing history is short (an explicit, disclosed
  characteristic, not a data-quality concern — same treatment already
  given to CoreWeave in the live registry's own notes).
- **Entity class**: PUBLIC_ACTIVE_CANDIDATE
- **Primary theme / subtheme / segment / role**: space / launch (reused
  from the existing Rocket Lab entry — same product category) /
  Launch (secondary: Lunar) / Launch-vehicle and lunar-lander developer
  [Primary/regulatory — recent, well-documented IPO; business
  description Established public profile]
- **Gap filled**: Space — Launch (High gap; Rocket Lab is currently the
  *only* tracked launch name) and adds depth to Lunar (already a
  relative strength per Phase 1, but a third name from a different
  market segment).
- **Second-order signal rationale**: as a second, independent launch
  provider, Firefly's own manifest/cadence disclosures give a
  comparison point against Rocket Lab for reading the health of the
  broader small/medium launch market.
- **Event types to monitor**: launch cadence/manifest updates, lunar
  mission milestones, capacity-expansion or new-vehicle-program
  announcements.
- **Evidence**: [Primary/regulatory] IPO priced and confirmed via the
  company's own press releases (fireflyspace.com/news) and Nasdaq's own
  IPO overview page, both located directly in search results —
  searched 2026-09-15. Confidence: High.
- **Relationships**: None asserted.

### A19. Viasat, Inc.
- **Ticker/exchange/jurisdiction**: VSAT, NASDAQ, US-domiciled. **EDGAR-
  monitorable today**: yes.
- **Entity class**: PUBLIC_ACTIVE_CANDIDATE
- **Primary theme / subtheme / segment / role**: space / (no existing
  subtheme fits — propose `ground-systems`) / Ground systems /
  Multi-band satellite platform including ground infrastructure and
  user terminals, plus a 23-satellite fleet [Established public
  profile]
- **Gap filled**: Space — Ground systems (Critical gap — zero coverage
  identified in Phase 1)
- **Second-order signal rationale**: ground-terminal/gateway deployment
  disclosures are a leading indicator for downstream satellite-capacity
  utilization across the broader space-communications cohort (Iridium,
  AST SpaceMobile).
- **Event types to monitor**: new satellite launch/commercial-service
  milestones (a ViaSat-3 F2 satellite launched November 2025 per search
  results, commercial service targeted September 2026), government
  satcom contract awards, ground-infrastructure expansion.
- **Evidence**: [Established public profile] NASDAQ:VSAT — searched
  2026-09-15. Confidence: High.
- **Relationships**: None asserted.

---

## Section B — Ecosystem-only public entities in currently unsupported primary-filing markets

These are real, verified, actively-traded public companies whose
listing exchange has **no working adapter in this project today**
(only SEC EDGAR, EDINET, and OpenDART/DART are currently supported).
Adding either would require new regulatory-source integration work —
explicitly out of scope for this document, consistent with Phase 1's
own China-market limitation note.

### B1. Renishaw plc
- **Ticker/exchange/jurisdiction**: RSW, London Stock Exchange (FTSE
  250), UK-domiciled. **Not monitorable by EDGAR/EDINET/DART today.**
- **Entity class**: PUBLIC_ECOSYSTEM_ONLY
- **Theme/segment**: humanoids / Encoders / precision encoders
  (interferometric laser, magnetic, optical) and metrology systems
  [Established public profile]
- **Gap it would fill**: Humanoids — Encoders (Critical gap; zero
  coverage identified in Phase 1, and this document found no
  EDGAR/EDINET/DART-listed dedicated encoder pure-play to fill it
  instead)
- **Evidence**: [Established public profile] LSE:RSW — searched
  2026-09-15. Confidence: High.

### B2. BE Semiconductor Industries N.V. (Besi)
- **Ticker/exchange/jurisdiction**: BESI, Euronext Amsterdam,
  Netherlands-domiciled. **Not monitorable by EDGAR/EDINET/DART
  today** — despite being Netherlands-domiciled like ASML (A2), this
  company does not appear to have a US ADR/EDGAR filing presence based
  on this session's search results (all sources point to an
  Amsterdam-only listing).
- **Entity class**: PUBLIC_ECOSYSTEM_ONLY
- **Theme/segment**: ai-buildout, secondary memory / Packaging/TSV
  (hybrid bonding) / World's leading die-bonding and hybrid-bonding
  equipment maker, reported ~30% market share specifically in HBM3/
  HBM4 hybrid bonding [Established public profile / trade press]
- **Gap it would fill**: Memory — Packaging/TSV (same High gap as
  Hanmi Semiconductor, A11 — a different, complementary equipment
  category: hybrid bonding vs. TC bonding)
- **Evidence**: [Established public profile / trade press —
  GlobeNewswire company press release, Investing.com] — searched
  2026-09-15. Confidence: High for business description; the specific
  "~30% HBM3/HBM4 hybrid bonding market share" figure is trade-press-
  sourced.

---

## Section C — Private-company intelligence nodes

Never scanned or tracked directly — these exist only as research
context, since an event at a major private company can still be a
genuine second-order signal for a *public* company already or newly
tracked (e.g., a supplier disclosure in a public company's own
filing).

### C1. SpaceX (Space Exploration Technologies Corp.)
- **Status**: Privately held, US-domiciled.
- **Relevance**: The dominant global launch-services and satellite
  (Starlink) operator — the single most significant private node
  across the entire Space theme; its launch cadence and pricing are a
  standing reference point for reading Rocket Lab's and Firefly
  Aerospace's own competitive disclosures.
- **Evidence**: [Established public profile — a globally, extensively
  reported private company]. Confidence: High for status; not
  independently re-verified via a primary source in this session
  (not applicable — it is not a public filer).

### C2. Boston Dynamics
- **Status**: Privately held (owned by Hyundai Motor Group), US-
  domiciled.
- **Relevance**: A leading humanoid/quadruped-robotics developer —
  relevant research context for the Humanoids theme's still-Critical
  OEM gap, and a useful comparison point once Tesla (A6) is evaluated.
  Notably, its parent (Hyundai Motor Group) is a different corporate
  family from HD Hyundai Electric (A5) — **flagged explicitly so this
  is never mistakenly read as a relationship between the two Hyundai-
  named entities in this document.**
- **Evidence**: [Established public profile]. Confidence: High for
  status.

### C3. Figure AI, Inc.
- **Status**: Privately held, US-domiciled, well-funded humanoid-robot
  developer.
- **Relevance**: A second, independent humanoid-OEM research node
  distinct from Tesla's own program — useful for triangulating whether
  Humanoids — OEM (Critical) is best addressed by watching Tesla
  specifically or the segment more broadly if/when any humanoid-OEM
  startup pursues a public listing.
- **Evidence**: [Established public profile]. Confidence: Medium —
  this document did not run a fresh search to confirm current funding/
  status details, relying on general awareness of the company's
  existence and sector.

---

## Section D — Explicitly deferred candidates and why

| Candidate | Category | Why deferred |
|---|---|---|
| Harmonic Drive Systems Inc. | Humanoids — Reducers | A second major Japan reducer maker alongside Nabtesco (A7); deferred to avoid padding a single Critical gap with two near-duplicate candidates in this first pass, and because this session could not independently confirm its exact TSE code. Worth verifying if Nabtesco alone doesn't sufficiently address the reducer gap. |
| Marvell Technology's AI-ASIC business | AI Buildout — Compute/custom silicon | Already a *tracked* company (under the photonics theme only, per Phase 1) — this document does not treat it as a new candidate, but flags (per Phase 1 Section 4) that its custom-silicon business may warrant a secondary ai-buildout theme tag, itself marked RESEARCH QUESTION rather than acted on here. |
| Any China-listed or China-domiciled company | Any | Explicitly out of scope — no supported regulatory adapter, and Phase 1 already flagged the additional licensing/access policy question this would raise (see `design/CHINA_COVERAGE_DESIGN_NOTE.md`). |
| A dedicated CPO (co-packaged optics) pure-play | Photonics — CPO | Phase 1 flagged this as the single most severe Photonics gap (Critical). This document could not identify a confident, clean pure-play public company for this specific niche in the time available — Coherent Corp (already tracked) and Ciena (A13) both have *some* CPO exposure, but neither is a dedicated pure-play. Flagged as an open research question rather than forcing a weak candidate. |
| A dedicated propulsion pure-play (beyond launch-vehicle-integrated propulsion) | Space — Propulsion | Same reasoning — no confident, clean public pure-play identified; Rocket Lab and Firefly Aerospace are vertically-integrated launch providers, not propulsion-component suppliers to others. Left as an open research question. |
| A dedicated enterprise-SSD or memory-substrate pure-play | Memory — Enterprise SSD / Substrates | Same reasoning — Phase 1's Medium-rated gaps in these sub-segments did not surface a confident candidate distinct from companies already tracked (Shin-Etsu, SUMCO) or already proposed above (Hanmi, Besi). |

---

## Section E — Recommended first implementation batch (≤10, supported-market only — NOT added by this document)

Ranked by (a) gap severity in Phase 1's own Critical/High ranking, and
(b) evidence strength/specificity found in this pass:

1. **Nabtesco Corporation** (A7) — TSE 6268 — Humanoids Reducers, Critical gap, strongest single-company evidence found in this entire pass.
2. **Taiwan Semiconductor Manufacturing Company** (A1) — NYSE TSM (ADR) — AI Buildout Foundry, Critical gap, the single most consequential name in the whole batch.
3. **Tesla, Inc.** (A6) — NASDAQ TSLA — Humanoids OEM, Critical gap (with the maturity caveat explicitly carried forward).
4. **ASML Holding N.V.** (A2) — NASDAQ ASML — AI Buildout Tools/materials (EUV bottleneck specifically), Critical sub-segment.
5. **Hanmi Semiconductor Co., Ltd.** (A11) — KRX 042700 — Memory Packaging/TSV, High gap, exceptionally specific and well-evidenced (including a trade-press-reported, RESEARCH-QUESTION-flagged direct SK Hynix relationship).
6. **HD Hyundai Electric Co., Ltd.** (A5) — KRX 267260 — AI Buildout Power/grid (transformer bottleneck specifically), fills a real Korea-market gap.
7. **GE Vernova Inc.** (A4) — NYSE GEV — AI Buildout Power/grid, adds critical depth to a widely-cited AI-datacenter-power bottleneck.
8. **Kulicke & Soffa Industries, Inc.** (A12) — NASDAQ KLIC — AI Buildout Advanced packaging/test, Medium gap, clean EDGAR identity match.
9. **Amphenol Corporation** (A15) — NYSE APH — Photonics Connectors, High gap, strong current AI-datacenter-demand evidence.
10. **Firefly Aerospace Inc.** (A18) — NASDAQ FLY — Space Launch, High gap (only Rocket Lab currently covers this segment at all), and a clean, recent, well-documented EDGAR identity.

---

## Sources used

Every entry above cites its own evidence inline. In aggregate, this
document drew on: SEC EDGAR (sec.gov filing indices and CIK records,
consulted directly for TSMC, ASML, Cognex, Kulicke & Soffa, Fabrinet,
Amphenol, Iridium, and GE Vernova); company investor-relations/press
pages (Nabtesco, Renishaw, Firefly Aerospace, GE Vernova, Fabrinet);
and industry/financial trade press (TheElec, TrendForce, Reuters via
Investing.com, GlobeNewswire, Yahoo Finance, Seeking Alpha, and
several others, each cited per-entry above) — all searched live on
2026-09-15.

## Assumptions

- The Phase 1 audit's gap rankings are taken as given, not re-derived.
- "Supported market" means an exchange/jurisdiction this project
  already has a working regulatory adapter for (SEC EDGAR, EDINET,
  OpenDART/DART) — confirmed per-candidate by exchange, not by an
  independent legal analysis of filing obligations.
- Every ticker/exchange/legal-name pairing above was confirmed via a
  live web search dated 2026-09-15; none is carried over from training-
  data recall alone.
- No candidate's specific financial figures (revenue, order-backlog
  amounts, market-share percentages) were independently verified
  against a primary filing in this session — each is attributed to its
  actual source (primary filing where a direct SEC.gov link was
  found, trade press otherwise) and confidence-rated accordingly.

## Unanswered verification questions

1. **TSMC/ASML foreign-private-issuer filing cadence** — both file
   20-F (annual, not 10-K) and 6-K (interim, not 10-Q). Whether this
   project's existing EDGAR scan/parsing logic already handles this
   form-type pattern (it does for Arm Holdings today, per Phase 1) or
   would need any adjustment for these two specific filers is not
   verified here.
2. **Hanmi Semiconductor ↔ SK Hynix relationship** — reported
   specifically and with dates by two independent trade publications,
   but not yet confirmed against either company's own primary
   disclosure. This is the single highest-value relationship to verify
   first, given both companies' centrality to this registry.
3. **Harmonic Drive Systems' exact TSE code** — not confirmed in this
   session; needed before treating it as anything more than a deferred
   candidate name.
4. **BE Semiconductor Industries' EDGAR status** — this document
   found no evidence of a US ADR/EDGAR filing presence, but did not
   exhaustively rule one out; worth a direct SEC EDGAR company-search
   check before finalizing its PUBLIC_ECOSYSTEM_ONLY classification.
5. **No confident CPO or dedicated-propulsion pure-play was found** —
   both remain open Critical/High gaps in Phase 1's own ranking with no
   candidate proposed here; a further, more targeted search pass may
   be warranted specifically for these two segments.
6. **Marvell Technology's theme classification** (already tracked,
   photonics-only) — whether a secondary ai-buildout tag is warranted
   was flagged as a RESEARCH QUESTION in Phase 1 and remains
   unresolved here.

---

## Files read / created

**Read**: `design/COVERAGE_TAXONOMY_AUDIT_2026_09_15.md` (the Phase 1
baseline this document builds on).

**Created**: `design/COVERAGE_EXPANSION_CANDIDATES_TIER1_2026_09_15.md`
(this document).

No other file was read or written. No code, test, schema, or
production-data change was made.
