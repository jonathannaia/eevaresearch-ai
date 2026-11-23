# Thematic Coverage-Universe Audit (2026-09-15)

Read-only research document. No file under `src/`, no test, no schema,
no `tracked_companies.py` entry, and no production data was modified to
produce this document. Builds directly on three prior untracked
research artifacts (treated as inputs, not edited):
`design/COVERAGE_TAXONOMY_AUDIT_2026_09_15.md` (gap-map and segment
methodology), `design/COVERAGE_EXPANSION_CANDIDATES_TIER1_2026_09_15.md`
(19 previously-researched candidates), and
`design/COVERAGE_EXPANSION_FIRST10_REGISTRY_READINESS_2026_09_15.md`
(10 candidates with live-verified SEC EDGAR/EDINET identity data,
2026-09-15). Facts carried from those documents are marked
**[Prior session, verified]**; new facts established in this document
are marked **[Established public profile]** unless a stronger tier
applies; anything not independently groundable is marked **RESEARCH
LEAD — requires verification**, never presented as fact.

**Evidence tiers used throughout:**
- **[Registry]** — drawn directly from `tracked_companies.py` today.
- **[Primary/regulatory, session-verified]** — fetched live against
  SEC EDGAR, EDINET, or an equivalent primary source in this or the
  prior session (CIK, EDINET code, filing-form history).
- **[Established public profile]** — widely-known, foundational,
  undisputed facts about a company (ticker, exchange, core business) —
  not independently re-fetched from a primary source this session.
- **[Trade press]** — a named industry publication; credible but not
  primary; any relationship claim resting only here is labeled.
- **RESEARCH LEAD — requires verification** — a plausible but
  unconfirmed claim (a relationship, a market-share figure, a
  source-feasibility assumption). Never presented as settled fact.

---

## 1. Current coverage inventory and CSV reconciliation

### 1.1 Registry snapshot

`src/config/tracked_companies.py` contains **105 active
`TrackedCompany` entries, 0 inactive**, as of `origin/main` @
`4aaca8c` (post PR #57, PR #58). **[Registry]**

**By source:**

| Source | Count |
|---|---|
| SEC EDGAR | 77 |
| EDINET | 18 |
| OpenDART / DART | 10 |
| **Total** | **105** |

**By primary theme** (first entry in each company's `themes` tuple):

| Theme | Primary-theme count | All-mentions count (incl. secondary) |
|---|---|---|
| ai-buildout | 77 | 79 |
| photonics | 8 | 8 |
| space | 8 | 8 |
| memory | 6 | 8 |
| humanoids | 6 | 7 |

Only 3 companies currently carry a secondary theme at all: SK Hynix
and Samsung Electronics (memory primary, ai-buildout secondary — the
HBM bridge), and Teradyne (ai-buildout primary, humanoids secondary —
the industrial-automation/test bridge). SUMCO and Shin-Etsu Chemical
are tagged `ai-buildout, memory` with ai-buildout listed first. No
company today carries a photonics or space secondary tag, and no
company carries three or more themes. **[Registry]**

**Segment concentration inside ai-buildout** (from
`COVERAGE_TAXONOMY_AUDIT_2026_09_15.md`'s prior segment mapping,
reused here): the 77 ai-buildout companies cluster heavily in
compute/silicon, semiconductor-test, and power/cooling — all three
already carry 6+ tracked names each. This is the single most
imbalanced fact in the registry: **ai-buildout has 10-13x the coverage
depth of every other theme**, while the other four themes have 6-8
names each, most with only 1-2 names per supply-chain layer (a single
tracked reducer maker for all of humanoids, a single tracked EUV-tool
company... currently *zero*, in fact — see §2.1).

### 1.2 CSV reconciliation

The attached export (`/Users/naia/Desktop/csv.csv`, columns `Company,
Ticker, Exchange, Jurisdiction, Source, Theme(s), Coverage, Lifecycle,
Scan eligibility`) was parsed with `utf-8-sig` (the file carries a
UTF-8 BOM) and compared row-for-row against a fresh parse of
`tracked_companies.py`.

**Result: 105 CSV rows, 105 registry entries, exact 1:1 name match,
zero field-level mismatches** (ticker/code, exchange, source, and
theme values are identical for all 105 companies). **No CSV/config
discrepancy exists.** This confirms and reproduces the same clean
result `COVERAGE_TAXONOMY_AUDIT_2026_09_15.md` reached against an
earlier, identically-structured export — the CSV is a faithful,
current snapshot of the live registry, not an independent or stale
source. **[Registry, cross-checked against CSV]**

### 1.3 Over/underrepresented layers (headline gaps)

Read together with the taxonomy doc's segment table, four structural
gaps stand out before any new candidate is even considered:

1. **Zero EUV/advanced-lithography equipment coverage.** ASML is the
   sole global supplier and is entirely absent — not a depth gap, a
   *zero* (see §2.1, item 2).
2. **Zero foundry coverage.** TSMC, the leading-edge foundry for
   nearly every fabless company already tracked (NVIDIA, AMD,
   Broadcom, Qualcomm, Arm), is absent (§2.1, item 1).
3. **Humanoids/robotics is one company deep in its two most important
   component layers** (precision reduction gears, strain-wave gears) —
   only Nabtesco-class coverage is even proposed, and today's registry
   has *no* tracked reducer/gear company of any kind, and no tracked
   servo-motor or industrial-robot-OEM company outside FANUC.
4. **No China-domiciled company of any kind is trackable** — not a
   registry omission but a **source-adapter limitation**: EDGAR, DART,
   and EDINET cover the US, Korea, and Japan respectively; no adapter
   exists for a mainland China or Hong Kong exchange. This silently
   caps coverage of several genuinely important nodes (YMTC/CXMT in
   memory, Innolight in optical modules) to the private-ecosystem
   watchlist at best, permanently, until a new source adapter is
   built. This is flagged again in §6 as a taxonomy-level constraint,
   not a company-selection problem.

---

## 2. Priority additions — ranked Tier 1 / Tier 2 / Tier 3

**33 Tier 1 candidates** are proposed below (not 30-50 spread evenly
per theme — deliberately weighted toward the four thin themes, since
ai-buildout's 77-name depth means its highest-value remaining gaps are
few and specific, while memory/photonics/space/humanoids each have
much larger proportional blind spots). Tier 2 is reserved for
candidates blocked by a genuine structural limitation (adapter gap or
unsupported exchange), not a lower-quality candidate. Tier 3 is a
small number of specific small/mid-cap bottleneck names, not a general
extension of the list.

Fields provided per company: legal name, ticker, exchange, country,
public/private, primary theme, secondary theme(s), supply-chain role,
order (1st-/2nd-order beneficiary), known relationships (evidence-tier
labeled), key event triggers, importance score (1-10, this document's
own judgment, not a market metric).

### 2.1 AI Buildout — 10 additions

| # | Company | Ticker | Exch. | Country | Type |
|---|---|---|---|---|---|
| 1 | Taiwan Semiconductor Mfg. Co. Ltd. | TSM (ADR) | NYSE | Taiwan | Public |
| 2 | ASML Holding N.V. | ASML | NASDAQ | Netherlands | Public |
| 3 | ASE Technology Holding Co., Ltd. | ASX (ADR) | NYSE | Taiwan | Public |
| 4 | Hanmi Semiconductor Co., Ltd. | 042700 | KRX | South Korea | Public |
| 5 | HD Hyundai Electric Co., Ltd. | 267260 | KRX | South Korea | Public |
| 6 | GE Vernova Inc. | GEV | NYSE | USA | Public |
| 7 | Kulicke & Soffa Industries, Inc. | KLIC | NASDAQ | USA | Public |
| 8 | Amphenol Corporation | APH | NYSE | USA | Public |
| 9 | Digital Realty Trust, Inc. | DLR | NYSE | USA | Public |
| 10 | Equinix, Inc. | EQIX | NASDAQ | USA | Public |

1. **TSMC** — Primary: ai-buildout. Supply-chain role: leading-edge
   contract foundry (world's largest, process-node leader). Order:
   2nd-order (capacity/capex is the leading indicator for demand
   commentary at every fabless customer tracked — NVIDIA, AMD,
   Broadcom, Qualcomm, Arm). Relationships: foundry for NVIDIA/AMD/
   Apple/Qualcomm — **[Established public profile]**, undisputed.
   Triggers: capex guidance (disclosed via 6-K earnings materials),
   new-node/new-fab announcements, export-control disclosures. **CIK
   0001046179, 6-K/20-F-only filer, zero 10-K/10-Q/8-K —
   [Primary/regulatory, session-verified, prior session 2026-09-15].**
   **Outcome: REQUIRES_ADAPTER_CHANGE** — `edgar_rules.py`'s
   `FORM_TYPE_CATEGORIES` has no `20-F`/`6-K` entries, so filings would
   ingest as inert `FilingEvent`s but never promote to a
   `CandidateSignal` under today's code. Score: **10/10** (single
   highest-value gap in the entire registry).
2. **ASML** — Primary: ai-buildout, proposed subtheme
   `lithography-equipment`. Supply-chain role: sole global supplier of
   EUV lithography systems (single-supplier bottleneck). Order:
   2nd-order (EUV shipment/order backlog is a direct leading indicator
   for leading-edge capacity at every foundry/IDM tracked — Samsung,
   SK Hynix, Micron, Tower, GlobalFoundries, TSMC if added).
   Relationships: sole EUV supplier to TSMC/Samsung/Intel —
   **[Established public profile]**. Triggers: EUV/High-NA EUV
   shipment disclosures, Dutch export-licensing developments (a
   recurring market-moving event type), capex guidance. **CIK
   0000937966, 6-K/20-F-only filer —
   [Primary/regulatory, session-verified, prior session].** **Outcome:
   REQUIRES_ADAPTER_CHANGE**, same blocker as TSMC. Score: **10/10**.
3. **ASE Technology Holding** — Primary: ai-buildout, proposed
   subtheme `advanced-packaging-test` (existing `semiconductor-test`
   is an imperfect fit — OSAT/assembly is a distinct process step).
   Supply-chain role: world's largest outsourced semiconductor
   assembly and test (OSAT) provider. Order: 2nd-order (advanced-
   packaging capacity, incl. CoWoS-adjacent work, is a bottleneck for
   AI-accelerator shipment volume). Relationships: assembly/test
   partner across the fabless/foundry ecosystem —
   **[Established public profile]**. Files as a foreign private issuer
   (ADR, Taiwan-domiciled) — **RESEARCH LEAD — requires verification**
   whether its EDGAR form mix is 20-F/6-K like TSMC/ASML (highly
   likely given its FPI status, not independently confirmed this
   session). **Outcome: likely REQUIRES_ADAPTER_CHANGE, pending
   confirmation.** Score: **8/10**.
4. **Hanmi Semiconductor** — Primary: memory (secondary: ai-buildout).
   Supply-chain role: thermal-compression (TC) bonding equipment for
   HBM stacking, reported ~80% share in this equipment category
   **[Trade press — TheElec/TrendForce, prior session, not
   independently re-verified]**. Order: 2nd-order (order backlog is a
   near-real-time leading indicator for HBM4/HBM5 ramp at Samsung/SK
   Hynix). Relationships: a specific ~44.2bn-won Hanmi–SK Hynix
   TC-bonder order (2026-06) reported by two independent trade
   publications — **RESEARCH LEAD — requires verification** (trade
   press only, not a primary DART filing). KRX 042700 confirmed
   **[Established public profile]**; DART corp_code unresolved
   (requires an `EDGE_DART_API_KEY`). **Outcome:
   READY_PENDING_IDENTITY_CONFIRMATION.** Score: **9/10** (highest
   memory-side gap — see §2.2 for why this is cross-listed).
5. **HD Hyundai Electric** — Primary: ai-buildout, subtheme
   `power-cooling` (reused). Supply-chain role: transformers,
   switchgear, grid-interconnect equipment. Order: 2nd-order
   (transformer lead times are a widely-reported datacenter
   power-connection bottleneck). KRX 267260 confirmed; DART corp_code
   unresolved, same limitation as Hanmi. **[Established public
   profile / prior session]. Outcome:
   READY_PENDING_IDENTITY_CONFIRMATION.** Score: **7/10**.
6. **GE Vernova** — Primary: ai-buildout, subtheme `power-cooling`.
   Supply-chain role: gas turbines, grid equipment, electrification
   systems (2024 GE spin-off). Order: 2nd-order (turbine backlog is a
   cited bottleneck for new datacenter power availability). CIK
   0001996810, standard domestic filer (8-K/10-Q) —
   **[Primary/regulatory, session-verified, prior session]. Outcome:
   READY_FOR_REGISTRY_ADD.** Score: **8/10**.
7. **Kulicke & Soffa** — Primary: ai-buildout (secondary candidate:
   memory — RESEARCH QUESTION), proposed subtheme
   `wire-bonding-packaging-equipment`. Supply-chain role: world's
   largest wire-bonding/assembly equipment maker. Order: 2nd-order
   (leading indicator for AI-chip assembly capacity across the
   tracked OSAT cohort — Amkor, SFA Semicon, Hana Micron, Ibiden). CIK
   0000056978, standard domestic filer — **[Primary/regulatory,
   session-verified, prior session]. Outcome:
   READY_FOR_REGISTRY_ADD.** Score: **6/10**.
8. **Amphenol** — Primary: photonics (secondary candidate:
   ai-buildout — RESEARCH QUESTION), proposed subtheme `connectors`.
   Supply-chain role: high-speed interconnect/connector systems for AI
   datacenters. Order: 2nd-order (book-to-bill ratio widely cited by
   analysts as a datacenter-buildout-pace indicator —
   **[Trade press]**). CIK 0000820313, standard domestic filer —
   **[Primary/regulatory, session-verified, prior session]. Outcome:
   READY_FOR_REGISTRY_ADD.** Score: **7/10**.
9. **Digital Realty Trust** — Primary: ai-buildout, proposed segment
   "Datacenter infrastructure / REIT" (new — no existing segment
   fits). Supply-chain role: hyperscale/colocation datacenter REIT;
   leases capacity directly to hyperscalers. Order: 1st-order (its own
   development pipeline and pre-lease disclosures are a direct,
   unmediated read on AI-datacenter buildout pace, not an inferred
   one). Relationships: landlord/developer for hyperscaler and
   neocloud capacity — **[Established public profile]**. Triggers:
   development-pipeline updates, power-availability disclosures in
   10-Q/10-K, major pre-lease announcements. Score: **6/10**.
10. **Equinix** — same segment as Digital Realty. Supply-chain role:
    global colocation/interconnection operator. Order: 1st-order,
    same rationale as Digital Realty. **[Established public
    profile]**. Score: **5/10** (overlaps materially with Digital
    Realty's signal; added for interconnection-specific disclosure
    coverage, not as a duplicate).

### 2.2 Memory — 1 clean addition, 1 cross-listed

| # | Company | Ticker | Exch. | Country | Type |
|---|---|---|---|---|---|
| 11 | ChipMOS Technologies (Bermuda) Ltd. | IMOS (ADR) | NASDAQ | Taiwan | Public |

11. **ChipMOS Technologies** — Primary: memory, proposed subtheme
    `memory-osat` (new). Supply-chain role: OSAT (assembly/test)
    specialized in memory and display-driver ICs. Order: 2nd-order
    (assembly/test capacity for DRAM/NAND/specialty-memory packages).
    **[Established public profile]**; likely a foreign-private-issuer
    20-F filer given its Bermuda/Taiwan structure —
    **RESEARCH LEAD — requires verification** whether the same
    `FORM_TYPE_CATEGORIES` gap applies. Score: **5/10**.

    Hanmi Semiconductor (§2.1, item 4) is memory's single highest-value
    gap and is cross-listed there rather than duplicated; its primary
    theme is memory, ai-buildout secondary.

    Memory's real structural gap is not a missing US/Japan/Korea
    company — it is the **absence of any DRAM/NAND maker outside the
    three already tracked (Micron, SK Hynix, Samsung)**. The two next
    largest DRAM/NAND names by volume — **Nanya Technology** (Taiwan,
    TWSE 2408) and **Winbond Electronics** (Taiwan, TWSE 2344) — are
    both **structurally unreachable today**: TWSE has no adapter in
    this codebase (EDGAR/DART/EDINET only), and neither trades a
    US-listed ADR. Listed in Tier 2 below as a source-feasibility gap,
    not a candidate-quality issue.

### 2.3 Photonics / Optical Networking — 4 additions

| # | Company | Ticker | Exch. | Country | Type |
|---|---|---|---|---|---|
| 12 | Ciena Corporation | CIEN | NYSE | USA | Public |
| 13 | Fabrinet | FN | NYSE | Cayman Is. / Thailand ops | Public |
| 14 | Viavi Solutions Inc. | VIAV | NASDAQ | USA | Public |
| 15 | Nippon Telegraph and Telephone Corp. | 9432 | TSE | Japan | Public |

12. **Ciena** — Primary: photonics. Supply-chain role: optical
    networking systems/coherent-optics vendor for telecom and
    datacenter interconnect. Order: 1st-order (a direct optical-
    networking-capex bellwether). **[Established public profile,
    carried forward from prior-session Tier 1 research; not
    re-verified live this turn]**. Score: **6/10**.
13. **Fabrinet** — Primary: photonics. Supply-chain role: contract
    manufacturer for optical transceivers/modules (Thailand-based
    manufacturing, Cayman-domiciled). Order: 2nd-order (manufacturing
    capacity for optical-module demand from Coherent, Lumentum, and
    others' outsourced production — **RESEARCH LEAD — requires
    verification** of the specific customer list). **[Established
    public profile, carried forward from prior session]**. Score:
    **6/10**.
14. **Viavi Solutions** — Primary: photonics, proposed subtheme
    `optical-test-measurement` (new). Supply-chain role: optical/RF
    test-and-measurement equipment for network buildouts. Order:
    2nd-order (test-equipment order flow leads network-buildout
    capex). **[Established public profile]**. Score: **4/10**.
15. **NTT** — Primary: photonics (RESEARCH QUESTION on strength of
    fit — primarily a telecom operator). Supply-chain role: R&D leader
    in IOWN, an optical-transport/all-photonics-network initiative.
    Order: 1st-order for its own optical R&D commercialization
    disclosures. **[Established public profile]**; the IOWN
    relevance-to-theme claim is **RESEARCH LEAD — requires
    verification** before treating this as a core rather than
    peripheral photonics name. Score: **3/10** (lowest-confidence
    Tier 1 addition in this document — included for completeness,
    flagged as the weakest fit).

    Cross-theme notes (no new registry entry — proposed secondary-theme
    additions to companies already tracked): **Broadcom** and
    **Credo Technology** (both ai-buildout primary today) both have
    substantial optical-DSP/AEC interconnect businesses —
    **RESEARCH QUESTION — requires verification** before adding
    photonics as a formal secondary theme to either.

### 2.4 Space — 8 additions

| # | Company | Ticker | Exch. | Country | Type |
|---|---|---|---|---|---|
| 16 | Firefly Aerospace Inc. | FLY | NASDAQ | USA | Public |
| 17 | L3Harris Technologies, Inc. | LHX | NYSE | USA | Public |
| 18 | Iridium Communications Inc. | IRDM | NASDAQ | USA | Public |
| 19 | Viasat, Inc. | VSAT | NASDAQ | USA | Public |
| 20 | Kratos Defense & Security Solutions, Inc. | KTOS | NASDAQ | USA | Public |
| 21 | Voyager Technologies, Inc. | VOYG | NYSE | USA | Public |
| 22 | Astroscale Holdings Inc. | 186A | TSE Growth | Japan | Public |
| 23 | Mitsubishi Heavy Industries, Ltd. | 7011 | TSE | Japan | Public |

16. **Firefly Aerospace** — Primary: space, subtheme `launch` (reused).
    Supply-chain role: launch-vehicle and lunar-lander developer.
    Order: 1st-order (a second, independent launch provider alongside
    the already-tracked Rocket Lab). CIK 0001860160, standard domestic
    filer, IPO'd 2025-08-07 — **[Primary/regulatory, session-verified,
    prior session]. Outcome: READY_FOR_REGISTRY_ADD.** Score: **7/10**.
17. **L3Harris** — Primary: space (secondary: defense-adjacent, not a
    tracked theme). Supply-chain role: satellite payloads, space
    domain awareness, defense electronics. Order: 1st-order for its
    own space-segment disclosures; 2nd-order as an integrator whose
    subcontractor awards signal activity across smaller tracked names.
    **[Established public profile, carried forward from prior
    session]**. Score: **5/10**.
18. **Iridium Communications** — Primary: space. Supply-chain role:
    owner/operator of a commercial LEO satellite constellation
    (communications). Order: 1st-order (constellation refresh/capacity
    disclosures). **[Established public profile, carried forward from
    prior session]**. Score: **5/10**.
19. **Viasat** — Primary: space. Supply-chain role: satellite
    communications operator/equipment. Order: 1st-order. **[Established
    public profile, carried forward from prior session]**. Score:
    **4/10**.
20. **Kratos Defense** — Primary: space (secondary candidate:
    defense-autonomy bridge — RESEARCH QUESTION). Supply-chain role:
    satellite ground systems, space-domain-awareness, and unmanned
    systems. Order: 2nd-order (a bridge name between space and
    defense-autonomy). **[Established public profile]**. Score:
    **4/10**.
21. **Voyager Technologies** — Primary: space. Supply-chain role:
    space-station and space-infrastructure developer (Starlab
    commercial space station program); IPO'd June 2025.
    **[Established public profile]** — ticker/listing not
    independently re-verified against a live primary source this
    session. Score: **5/10**.
22. **Astroscale Holdings** — Primary: space, proposed subtheme
    `debris-removal-servicing` (new). Supply-chain role: on-orbit
    debris removal and satellite-servicing technology; listed on
    TSE's Growth Market, 2024. Order: 1st-order for its own program
    milestones; a leading indicator for the broader in-space-servicing
    market's maturity. **[Established public profile]** — EDINET
    eligibility not independently confirmed this session (Growth
    Market listings should be covered by the same EDINET filer
    universe as TSE Prime, but this document did not verify Astroscale
    specifically against the EDINET code list the way Nabtesco was
    verified in the prior session). Score: **5/10**.
23. **Mitsubishi Heavy Industries** — Primary: space (secondary:
    ai-buildout-adjacent defense/industrial — not proposed). Supply-
    chain role: H3 launch-vehicle prime contractor (Japan's national
    launch system) and major heavy-industrial conglomerate. Order:
    1st-order for launch-segment disclosures specifically (MHI's
    non-space businesses — power, shipbuilding, defense — would need
    filtering, similar to Nabtesco's multi-segment caveat in §2.5).
    **[Established public profile]**. Score: **4/10** (high strategic
    relevance, tempered by the multi-segment filtering problem it
    would introduce).

### 2.5 Humanoids / Robotics — 10 additions

This is the theme with the largest proportional gap relative to its
real-world supply-chain depth: today's registry has exactly one
tracked reducer/gear maker candidate under research (Nabtesco, not yet
added) and zero tracked servo-motor, precision-bearing, or
machine-vision companies outside FANUC's own vertically-integrated
business.

| # | Company | Ticker | Exch. | Country | Type |
|---|---|---|---|---|---|
| 24 | Nabtesco Corporation | 6268 | TSE | Japan | Public |
| 25 | Tesla, Inc. | TSLA | NASDAQ | USA | Public |
| 26 | Cognex Corporation | CGNX | NASDAQ | USA | Public |
| 27 | MinebeaMitsumi Inc. | 6479 | TSE | Japan | Public |
| 28 | Panasonic Holdings Corporation | 6752 | TSE | Japan | Public |
| 29 | Harmonic Drive Systems Inc. | 6324 | TSE | Japan | Public |
| 30 | THK Co., Ltd. | 6481 | TSE | Japan | Public |
| 31 | Yaskawa Electric Corporation | 6506 | TSE | Japan | Public |
| 32 | Keyence Corporation | 6861 | TSE | Japan | Public |
| 33 | Omron Corporation | 6645 | TSE | Japan | Public |

24. **Nabtesco** — Primary: humanoids, proposed subtheme
    `precision-reducers` (new). Supply-chain role: precision
    reduction-gear (RV/cycloidal) manufacturer for large-joint robot
    actuation; reported ~60% global RV-reducer share **[Trade press,
    prior session, not independently re-verified]**. Order: 2nd-order
    (a capacity/backlog signal here is a leading indicator across
    nearly every humanoid/industrial-robotics OEM). EDINET code E01726
    confirmed against the live official EDINET code list —
    **[Primary/regulatory, session-verified, prior session]**.
    **Outcome: READY_PENDING_IDENTITY_CONFIRMATION.** Score: **9/10**
    (highest single gap in humanoids).
25. **Tesla** — Primary: humanoids (Optimus program). Order: 2nd-order
    for the broader actuator/sensor supply chain once such suppliers
    are added; 1st-order for its own production-ramp disclosures.
    Maturity caveat carried forward: Optimus remained pre-revenue,
    explicitly "research phase" per the company's own Q4 2025
    earnings-call commentary **[Trade press, prior session, not
    independently re-verified against a primary transcript]**. CIK
    0001318605, standard domestic filer — **[Primary/regulatory,
    session-verified, prior session]. Outcome:
    READY_FOR_REGISTRY_ADD.** Score: **7/10**.
26. **Cognex** — Primary: humanoids, proposed subtheme
    `machine-vision` (new). Supply-chain role: machine-vision sensors
    and software for industrial and robotic automation. Order:
    2nd-order (vision-system attach rate across the broader industrial-
    automation base, incl. Rockwell/Symbotic/Doosan Robotics
    customers — **RESEARCH LEAD — requires verification** of specific
    named relationships). **[Established public profile, carried
    forward from prior session]**. Score: **5/10**.
27. **MinebeaMitsumi** — Primary: humanoids, subtheme candidate
    `precision-bearings-motors` (new). Supply-chain role: precision
    ball bearings and micro-motors used in robot joints/actuators.
    **[Established public profile, carried forward from prior
    session]**. Score: **5/10**.
28. **Panasonic Holdings** — Primary: humanoids (secondary candidate:
    ai-buildout via battery/energy-storage business — RESEARCH
    QUESTION). Supply-chain role: motors and battery/energy-storage
    technology relevant to robot actuation and power. **[Established
    public profile, carried forward from prior session]**. Score:
    **3/10** (broad conglomerate — weakest-fit addition in this
    theme, included for battery-tech relevance only).
29. **Harmonic Drive Systems** — Primary: humanoids, subtheme
    `precision-reducers` (shared with Nabtesco). Supply-chain role:
    strain-wave (harmonic-drive) gearing — the other dominant
    precision-reduction technology for robot joints alongside
    Nabtesco's RV/cycloidal gears; together the two technologies cover
    nearly the entire addressable reducer market for humanoid/
    industrial-robot actuation. **[Established public profile]** — not
    independently verified against the EDINET code list this session
    (unlike Nabtesco). Score: **8/10** (a genuine gap of comparable
    importance to Nabtesco, newly identified in this document — not
    present in either prior research doc).
30. **THK Co.** — Primary: humanoids, subtheme candidate
    `linear-motion-actuators` (new). Supply-chain role: linear-motion
    guides and actuators used in robotic and precision-automation
    systems. **[Established public profile]**. Score: **4/10**.
31. **Yaskawa Electric** — Primary: humanoids. Supply-chain role: one
    of the world's top-three industrial-robot OEMs and servo-motor/
    drive makers — a notable omission given FANUC is already tracked
    as the sole robot-OEM name. Order: 1st-order. **[Established
    public profile]**. Score: **7/10** (a direct FANUC peer; the
    single most surprising pre-existing gap found in this audit).
32. **Keyence** — Primary: humanoids, subtheme `machine-vision`
    (shared with Cognex). Supply-chain role: sensors and machine-vision
    systems for factory/robotic automation. **[Established public
    profile]**. Score: **4/10**.
33. **Omron** — Primary: humanoids, subtheme candidate
    `industrial-automation` (reused). Supply-chain role: industrial
    automation controllers, sensors, and safety systems. **[Established
    public profile]**. Score: **3/10**.

### 2.6 Tier 2 — structural blind spots (not a candidate-quality issue)

These are real, important companies that cannot be added under today's
source adapters (EDGAR, DART, EDINET only) — listed per the task's
"Tier 2 only for structural blind spots" instruction, not as
lower-priority Tier 1 names.

| Company | Exchange | Theme | Why blocked |
|---|---|---|---|
| Nanya Technology Corp. | TWSE 2408 | memory | Taiwan-only listing, no US ADR, no TWSE adapter |
| Winbond Electronics Corp. | TWSE 2344 | memory | Same |
| Suzhou Zhongji Innolight Co. ("Innolight") | Shenzhen 300308 | photonics | China A-share, no China adapter, no ADR |
| Siemens Energy AG | Frankfurt ENR | ai-buildout (power/grid) | German listing, no European-exchange adapter |

All four are named specifically because they sit in segments the
registry already tracks thinly (memory DRAM/NAND breadth, optical
modules, grid/transformer equipment) — the gap is source coverage, not
research effort. Building any new adapter is out of scope for this
read-only document; flagged here so it is visible as a deliberate,
documented limitation rather than a silent one. **RESEARCH LEAD** on
whether any of the four maintain a US-listed ADR that would sidestep
the limitation — not confirmed in this session.

### 2.7 Tier 3 — small/mid-cap, specific bottleneck rationale only

| Company | Ticker | Exch. | Theme | Bottleneck rationale |
|---|---|---|---|---|
| Belden Inc. | BDC | NYSE | ai-buildout | Datacenter cabling/interconnect; a smaller, more specialized bellwether than Amphenol for copper/fiber cabling specifically. |
| Novanta Inc. | NOVT | NASDAQ | humanoids + photonics (bridge) | Photonics/motion-control components used in both robotics and laser/photonics systems — a genuine cross-theme bridge name, not a duplicate of either theme's core list. |
| BWX Technologies, Inc. | BWXT | NYSE | space | Nuclear components and space-nuclear-propulsion work (a specific, narrow bottleneck relevant to next-generation propulsion programs, not a general space name). |
| Lightwave Logic, Inc. | LWLG | NASDAQ | photonics | Small-cap polymer electro-optic materials — an early-stage, high-uncertainty name; included only as a named research lead for materials-layer photonics coverage, explicitly **RESEARCH LEAD — requires verification** on commercial maturity before any registry action. |

**Tier 1 total: 33.** Within the requested ~30-50 range, weighted
toward memory (structurally thinnest relative to its real supply
chain), humanoids (largest proportional gap found), and space, with a
smaller, more surgical set of ai-buildout additions given its existing
79-name depth.

---

## 3. Private ecosystem watchlist

Kept entirely separate from the public-equity lists above, per
instruction. These are not registry candidates (no public filing
exists to track) — they matter as **intelligence nodes**: their
funding events, partnership announcements, and production milestones
are second-order signals for the public companies that supply, invest
in, or compete with them.

| Entity | Theme | What it builds | Known funding/investors/partners | Associated public beneficiaries/threatened incumbents | Why it matters |
|---|---|---|---|---|---|
| SpaceX | space | Launch (Falcon/Starship), Starlink constellation | Privately held; **[Established public profile]** for its scale/cadence, funding rounds are **RESEARCH LEAD — requires verification** for any specific current valuation figure | Beneficiary signal for launch-adjacent suppliers; competitive pressure on Rocket Lab/Firefly on cost-per-launch | Sets the cost/cadence benchmark the entire launch segment is measured against |
| Blue Origin | space | New Glenn launch vehicle, lunar lander (Blue Moon) | Privately held (Bezos Expeditions) | Competitive pressure on the same launch cohort as SpaceX; NASA lunar-lander program beneficiary signal | Second major US heavy-lift provider; program milestones move launch-segment sentiment broadly |
| Sierra Space | space | Dream Chaser spaceplane, commercial space-station modules (with Voyager Technologies/Starlab) | Spun off from Sierra Nevada Corp, 2021; **RESEARCH LEAD** on current investor list | Direct partner to Voyager Technologies (already Tier 1, §2.4) — a rare public/private pairing worth tracking together | A named partner of an already-recommended public Tier 1 addition |
| Boston Dynamics | humanoids | Atlas humanoid, Spot quadruped | Majority-owned by Hyundai Motor Group since 2021 | Hyundai Motor Group (not currently tracked — **RESEARCH QUESTION** whether it should be, given this ownership) is the direct public beneficiary/parent | A Hyundai-group signal correlates directly with Boston Dynamics program news |
| Figure AI | humanoids | Figure 01/02 humanoid robots | Backed by OpenAI, Microsoft, NVIDIA, Intel Capital among reported investors — **RESEARCH LEAD — requires verification** of the exact current cap table | NVIDIA (already tracked) is a reported investor — a direct ecosystem link into an existing Tier 1 registry name | One of the best-known humanoid-robot startups; NVIDIA linkage makes it directly relevant to existing coverage |
| Apptronik | humanoids | Apollo humanoid robot | Reported partnership with Mercedes-Benz for factory deployment — **RESEARCH LEAD — requires verification** | Mercedes-Benz (not tracked, non-US/non-tracked-exchange) | A named commercial deployment partner, if confirmed, would be a concrete second-order signal source |
| Agility Robotics | humanoids | Digit humanoid robot | Reported partnership with Amazon and GXO Logistics for warehouse deployment — **RESEARCH LEAD — requires verification** | Amazon.com (already tracked, ai-buildout) | Amazon linkage, if confirmed, ties a private humanoid maker directly to an existing registry name |
| 1X Technologies | humanoids | NEO humanoid robot | Reported OpenAI investment/partnership — **RESEARCH LEAD — requires verification** | Same OpenAI-ecosystem relevance as Figure AI | A second OpenAI-linked humanoid program, useful for cross-checking ecosystem-wide humanoid momentum |
| YMTC (Yangtze Memory Technologies Co.) | memory | 3D NAND flash memory | Chinese state-linked entity; on the US Entity List since 2022 — **[Established public profile]** for the listing fact itself | Competitive/geopolitical signal for Micron, Kioxia, SK Hynix, Samsung, Western Digital, SanDisk (all tracked) | China's leading NAND producer; capacity/export-control news moves the entire tracked NAND cohort's competitive narrative |
| CXMT (ChangXin Memory Technologies) | memory | DRAM | Chinese, privately/state-linked; **RESEARCH LEAD** on precise ownership structure | Same competitive-signal relevance as YMTC, for the DRAM side specifically (Micron, SK Hynix, Samsung) | China's leading DRAM producer; the clearest domestic-DRAM-ambition signal to watch alongside Nanya/Winbond (§2.6) |

---

## 4. Supply-chain and cross-theme graph

### 4.1 Per-theme layer map (reused/extended from
`COVERAGE_TAXONOMY_AUDIT_2026_09_15.md`'s segment methodology)

**AI Buildout**: end customer (hyperscaler/neocloud: Microsoft,
Amazon, Alphabet, Meta, Oracle, CoreWeave, Nebius) → compute silicon
(NVIDIA, AMD, Broadcom, Intel, Arm) → foundry (**TSMC — gap**,
GlobalFoundries, Tower) → lithography/fab-tools (**ASML — gap**,
Applied Materials, Lam Research, KLA, Tokyo Electron) →
assembly/test/OSAT (Amkor, **ASE — gap**, SFA Semicon, **Kulicke &
Soffa — gap**) → networking/interconnect (Arista, Cisco, Credo,
Astera Labs, **Amphenol — gap**) → power/cooling/grid (Vertiv, Eaton,
Bloom Energy, **GE Vernova — gap**, **HD Hyundai Electric — gap**) →
datacenter infrastructure (**Digital Realty — gap**, **Equinix —
gap**).

**Memory**: end customer (same hyperscaler cohort, plus AI-accelerator
vendors as a direct HBM consumer) → DRAM/NAND maker (Micron, SK Hynix,
Samsung, Kioxia, SanDisk, Western Digital; **Nanya/Winbond/CXMT —
blocked/private**) → HBM-specific equipment (**Hanmi Semiconductor —
gap**, Tokyo Electron and Advantest already tracked under ai-buildout
only) → memory OSAT (**ChipMOS — gap**) → silicon-wafer materials
(SUMCO, Shin-Etsu Chemical, both already tracked, tagged
`ai-buildout, memory`).

**Photonics/Optical Networking**: end customer (hyperscaler
datacenter interconnect, telecom carriers) → systems/network vendors
(Nokia, **Ciena — gap**) → optical components/modules (Lumentum,
Coherent, Applied Optoelectronics, MaxLinear, **Fabrinet — gap** as
contract manufacturer) → materials/fiber (Corning, Furukawa Electric)
→ test/measurement (**Viavi — gap**) → adjacent silicon (Marvell —
optical DSP already tracked; Broadcom/Credo — cross-theme candidates,
§2.3).

**Space**: end customer (government/defense — NASA, Space Force,
commercial telecom/EO customers) → launch providers (Rocket Lab,
**Firefly — gap**; SpaceX/Blue Origin — private, §3) → satellite
platform/payload primes (**L3Harris — gap**, Redwire, Intuitive
Machines) → constellation operators (AST SpaceMobile, Planet Labs,
**Iridium — gap**, **Viasat — gap**) → ground systems/defense-autonomy
bridge (**Kratos — gap**) → national/heavy-industrial launch capacity
(**Mitsubishi Heavy Industries — gap**, ispace already tracked for
lunar) → emerging in-space servicing (**Astroscale — gap**).

**Humanoids/Robotics**: end customer (manufacturing/logistics
integrators, direct-to-OEM for Tesla) → robot OEM (FANUC, **Yaskawa —
gap**, **Tesla — gap**; Boston Dynamics/Figure AI/Apptronik/Agility/1X
— private, §3) → precision actuation components (**Nabtesco — gap**,
**Harmonic Drive Systems — gap**, **THK — gap**) → motors/bearings
(**MinebeaMitsumi — gap**, Nidec already tracked under ai-buildout
only) → sensors/vision (**Cognex — gap**, **Keyence — gap**) →
industrial-automation integration (Rockwell, Symbotic, Doosan
Robotics, Mitsubishi Electric, **Omron — gap**) → semiconductor/test
bridge (Teradyne, already tagged humanoids secondary).

### 4.2 Cross-theme bridge companies

Confirmed today, already reflected in registry tags:
- **SK Hynix, Samsung Electronics** — memory ↔ ai-buildout (HBM).
- **SUMCO, Shin-Etsu Chemical** — ai-buildout ↔ memory (wafer/materials).
- **Teradyne** — ai-buildout ↔ humanoids (semiconductor test ↔
  industrial automation).

Proposed new bridges (this document, RESEARCH QUESTION unless noted):
- **Hanmi Semiconductor** — memory ↔ ai-buildout, same HBM logic as
  SK Hynix/Samsung, but from the equipment side rather than the
  device-maker side.
- **Novanta** — humanoids ↔ photonics (motion-control and laser/
  photonics components share a customer base) — Tier 3, §2.7.
- **Kratos Defense** — space ↔ a not-yet-tracked defense-autonomy
  theme — flagged as a possible future theme boundary, not resolved
  here.
- **Broadcom, Credo Technology** — ai-buildout ↔ photonics (optical-
  DSP/AEC interconnect businesses) — RESEARCH QUESTION, not yet
  verified against either company's actual segment-revenue disclosure.

### 4.3 Example propagation chains

Each chain lists the trigger event, the propagation path, and a
per-edge confidence/evidence label. These are illustrative detection
patterns, not live signals — see §6 for the formal detection-rule
design.

**Chain A — HBM capacity signal (memory → ai-buildout):**
Hanmi Semiconductor reports a large TC-bonder order backlog increase
→ [**RESEARCH LEAD** edge: implies near-term HBM stacking-capacity
growth at its customers] → SK Hynix / Samsung HBM output rises
[**Established public profile** edge: SK Hynix and Samsung are
Hanmi's reported primary customers] → NVIDIA / AMD accelerator
shipment volume becomes less memory-constrained [**RESEARCH LEAD**
edge: HBM supply has been reported as a binding constraint on
accelerator shipments in prior periods, not independently verified as
currently binding] → hyperscaler capex guidance reflects higher
deliverable compute [**RESEARCH LEAD**, several steps removed].
Confidence decays sharply after the second hop — this chain is
useful for a two-hop "Review needed" candidate, not a four-hop
auto-classified one (see §6.2).

**Chain B — EUV bottleneck signal (ai-buildout, single-node):**
ASML reports an EUV/High-NA order or shipment update
[**Primary/regulatory** edge, once ASML is added and 6-K ingestion is
fixed] → TSMC/Samsung/Intel leading-edge capacity outlook shifts
[**Established public profile** edge: these three are ASML's
publicly known EUV customer base] → fabless AI-accelerator
availability outlook shifts one to two quarters later [**RESEARCH
LEAD** edge: lead-time assumption, not independently modeled here].
Two-hop chain, first edge would be primary-source-grade once the
adapter gap (§2.1) is fixed — the strongest-confidence chain in this
document precisely because its first hop is a direct regulatory
filing rather than trade press.

**Chain C — Precision-reducer signal (humanoids, single-node):**
Nabtesco or Harmonic Drive Systems reports a capacity-expansion
extraordinary filing (EDINET) tied to the robotics-component segment
[**Primary/regulatory** edge, once either is added — EDINET
extraordinary-report ingestion already works for the 18 tracked
EDINET companies] → industrial/humanoid robot OEM production capacity
outlook shifts (FANUC, Yaskawa, Tesla Optimus) [**RESEARCH LEAD**
edge: capacity-to-OEM-output relationship asserted by structural
analogy to the reducer market's reported concentration, not
independently modeled]. One-hop primary-source trigger, one-hop
inferred consequence — a good candidate for an early, low-risk
detection rule (§6.1) precisely because it needs only one inference
hop.

**Chain D — Launch-cadence comparison signal (space, cross-provider):**
Firefly Aerospace reports a launch-manifest update [**Primary/
regulatory** edge, once added, via 8-K] → read alongside Rocket Lab's
existing tracked cadence [**Registry** edge — already-tracked
comparison] → sector-wide small/medium launch-market health inference
[**RESEARCH LEAD** edge: a comparative judgment, not a company-
specific fact]. Included as an example of a **comparison-based**
propagation pattern (two already-tracked-or-addable companies read
together) rather than a strict supply-chain hop — the taxonomy in §5
should support this pattern explicitly, not just directional
supply-chain edges.

---

## 5. Taxonomy and data-model recommendation

This reuses and extends `COVERAGE_TAXONOMY_AUDIT_2026_09_15.md`'s
proposed hierarchy rather than replacing it, since it already maps
cleanly onto every `subthemes` value already live in the registry.

### 5.1 Hierarchy

`Primary Theme (5, unchanged: ai-buildout / humanoids / memory /
photonics / space) → Segment (gap-map categories — e.g. for
ai-buildout: compute-silicon, foundry, lithography-equipment [new,
currently empty], tools-materials, advanced-packaging-test,
servers-storage, networking-optics, power-grid-generation, cooling,
datacenter-infrastructure [new, currently empty], construction) →
Subtheme (finer-grained; every subtheme proposed in §2 nests under an
existing or newly-named segment: `lithography-equipment`,
`advanced-packaging-test` → OSAT-specific, `memory-osat`,
`precision-reducers`, `precision-bearings-motors`,
`linear-motion-actuators`, `machine-vision`, `connectors`,
`optical-test-measurement`, `debris-removal-servicing`).`

### 5.2 Recommended new `TrackedCompany`-adjacent fields (additive, not
proposed as an edit in this document)

Carried forward unchanged from `COVERAGE_TAXONOMY_AUDIT_2026_09_15.md`:
`supply_chain_role`, `entity_type` (`public_issuer` /
`subsidiary_of_tracked_issuer` / `private_intelligence_node`),
`coverage_status` (`tracked` / `candidate` / `research_question` /
`explicitly_excluded`), `evidence_status`, `source`,
`last_verification_date`.

**New, proposed by this document:**
- **`order_classification`**: `first_order` / `second_order` — the
  field this document's per-candidate profiles (§2) populate
  informally today; formalizing it lets a future scoring pass query
  "how many hops from an end customer" directly instead of re-deriving
  it from `supply_chain_role` text each time.
- **`importance_score`**: 1-10 integer, the same scale used
  informally throughout §2 — explicitly documented as *this project's
  own editorial judgment*, not a market-derived metric, to avoid it
  being read as a valuation or investment signal.
- **`exchange_adapter_status`**: `supported` / `requires_adapter_change`
  / `unsupported_exchange` — makes the TSMC/ASML/ASE-class gap (§2.1)
  and the Tier-2 structural-blind-spot class (§2.6) queryable directly,
  instead of living only in prose across research documents.

### 5.3 Public issuer / private entity / relationship separation

Recommend three distinct record types, not one table with a nullable
"is this public" flag:
1. **`TrackedCompany`** (existing) — public issuers only, unchanged
   shape plus the additive fields above.
2. **`PrivateEcosystemEntity`** (new) — the §3 watchlist shape: name,
   theme(s), what it builds, funding/investor list (each entry
   evidence-tier labeled), associated public entities (a list of
   `TrackedCompany` foreign keys), why-it-matters narrative,
   `last_verification_date`. Explicitly never scanned by any filing
   adapter — it exists for narrative/context lookup only, the same
   role the existing `ResearchTheme`/`ThemeCompanyMapEntry` machinery
   already plays for curated theses (per
   `RESEARCH_THESES_ACTIVE_DORMANT.md`'s description of that system).
3. **`EcosystemRelationship`** (new) — a directed edge type covering
   both §4.2's cross-theme bridges and §3's public/private links:
   `source_entity`, `target_entity` (either may be a `TrackedCompany`
   or a `PrivateEcosystemEntity`), `relationship_type` (`supplier` /
   `customer` / `investor` / `competitor` / `parent_subsidiary` /
   `comparison_peer` — the last one specifically to support Chain D's
   comparison pattern from §4.3, which is not a supplier/customer
   edge at all), `direction`, `evidence_status`, `evidence_tier` (the
   same four-tier scale used throughout this document),
   `last_verification_date`.

---

## 6. Second-order detection rules

### 6.1 Rule shape

Each rule: **trigger event type** → **traversal** (which
`EcosystemRelationship` edge types it follows, and how many hops) →
**candidate signal** → **confidence threshold** → **decay/
revalidation** → **default state**.

| # | Trigger event type | Traversal | Max hops | Confidence threshold to auto-surface | Decay/revalidation | Default state |
|---|---|---|---|---|---|---|
| 1 | Hyperscaler capex guidance change (10-Q/10-K/8-K language) | `customer` edge, hyperscaler → its named/inferred suppliers | 1 | High — only if the supplier relationship is `evidence_tier = primary_or_established` | Revalidate each quarter against the next capex disclosure | Auto-surface at 1 hop with primary/established evidence; else **Review needed** |
| 2 | HBM/DRAM supply disclosure (earnings, capacity announcement) | `supplier` edge, memory maker → AI-accelerator maker | 1-2 | Medium — 1-hop only; 2-hop always **Review needed** regardless of edge evidence | Revalidate on next earnings cycle (quarterly) | 1-hop: surfaced with a confidence label; 2-hop: **Review needed** |
| 3 | Advanced-packaging/OSAT capacity announcement | `supplier` edge, OSAT → fabless/IDM customer | 1 | Medium — requires `evidence_tier` at least `trade_press`, with a visible tier label on the surfaced signal | Revalidate on next capacity-relevant filing from either party | Surfaced with tier label; never silently upgraded to "confirmed" without a primary-source edge |
| 4 | Transformer/grid-equipment lead-time or backlog disclosure | `supplier` edge, grid-equipment maker → datacenter/REIT/hyperscaler | 1 | Medium | Revalidate quarterly | Surfaced with tier label |
| 5 | Optical-module demand signal (earnings commentary, capacity expansion) | `supplier` edge, module/component maker → networking-systems vendor | 1-2 | Low-medium — this theme's edges are the least evidenced today (§2.3's RESEARCH QUESTION-heavy bridge list) | Revalidate quarterly; edges lacking `established_public_profile` or better evidence expire (move to **Review needed**) after 2 missed revalidation cycles | Default **Review needed** unless the edge is `primary_or_established` |
| 6 | Humanoid production-ramp disclosure (10-Q/10-K/8-K language naming a component supplier) | `supplier` edge, actuation/sensor component maker → robot OEM | 1 | Medium-high once Nabtesco/Harmonic Drive/MinebeaMitsumi-class suppliers are added — today near-zero eligible edges exist | Revalidate on next OEM disclosure | Surfaced with tier label; **Review needed** by default until at least one component-layer supplier is actually tracked (today's registry has none) |
| 7 | Launch cadence/manifest update | `comparison_peer` edge (not a supply-chain edge — see Chain D, §4.3) | 1 | Medium — comparison-pattern signals are inherently softer than supply-chain ones; cap confidence one level below what the same evidence tier would earn on a `supplier`/`customer` edge | Revalidate on next manifest disclosure from either peer | Always labeled explicitly as a "comparison signal," never framed as a causal one |
| 8 | Satellite contract award / constellation capacity disclosure | `customer` edge, government/commercial customer → constellation operator or component supplier | 1 | Medium | Revalidate on next contract-cycle disclosure | Surfaced with tier label |

### 6.2 General rules (apply across all 8 above)

- **Confidence caps at the weakest edge in the chain.** A 2-hop chain
  with one `primary_or_established` edge and one `trade_press` edge
  is capped at `trade_press`-level confidence overall, never averaged
  up. Chain A in §4.3 (four conceptual hops, only the first two
  edge-labeled with any confidence) illustrates why: it is presented
  as an illustrative pattern, not a rule the table above would
  auto-surface past hop 2.
- **Hop limit of 2 for any auto-surfaced signal.** Anything requiring
  a third inferential hop is **always** "Review needed," regardless of
  individual edge confidence — this mirrors §4.3's own observation
  that confidence decays sharply after hop 2.
- **Mandatory "Review needed" state**: any signal where (a) the
  strongest edge in the chain is below `established_public_profile`,
  (b) the chain exceeds 2 hops, (c) the relationship has not been
  revalidated within its decay window, or (d) the traversal crosses a
  `comparison_peer` edge without at least one supporting
  `primary_or_established` edge on either peer's own disclosure. This
  state is never silently hidden — it is a distinct, visible
  classification, not a suppressed one, consistent with this
  project's existing pattern of showing rather than hiding
  low-confidence items (the same principle
  `RESEARCH_THESES_ACTIVE_DORMANT.md` applies to its own Active/
  Dormant/No-coverage-yet three-state design, reused here rather than
  invented fresh).
- **Decay is theme-specific, not global**: memory/ai-buildout
  disclosure cadence (quarterly earnings, frequent 6-K/8-K activity)
  supports a shorter revalidation window than space (launch manifests
  and satellite contracts are lumpier and less periodic) — exact
  windows are a follow-up design decision, not fixed numerically in
  this document, mirroring how `RESEARCH_THESES_ACTIVE_DORMANT.md`
  treated its own 30-day Active/Dormant threshold as "a starting
  point, not a hard recommendation."

---

## 7. Explicitly out of scope for this document

- No change to `tracked_companies.py`, any source adapter
  (`edgar_rules.py`, `dart_rules.py`, `edinet_*`), any schema, any
  test, or any production data.
- No resolution of the `FORM_TYPE_CATEGORIES` 20-F/6-K adapter gap
  (§2.1) — flagged as the single highest-value follow-up engineering
  task, not designed or implemented here.
- No resolution of the DART `corp_code` lookups for Hanmi
  Semiconductor / HD Hyundai Electric (requires an `EDGE_DART_API_KEY`
  not available in this session).
- No new source adapter design for TWSE, Shenzhen/Shanghai, or
  Frankfurt exchanges (§2.6) — flagged as a taxonomy-level constraint
  only.
- No implementation of the `order_classification`, `importance_score`,
  or `exchange_adapter_status` fields, the `PrivateEcosystemEntity` or
  `EcosystemRelationship` record types (§5), or any of the 8 detection
  rules (§6) — all proposed, none built.

---

## Files read / created

**Read (research inputs only, none modified):**
`design/COVERAGE_TAXONOMY_AUDIT_2026_09_15.md`,
`design/COVERAGE_EXPANSION_CANDIDATES_TIER1_2026_09_15.md`,
`design/COVERAGE_EXPANSION_FIRST10_REGISTRY_READINESS_2026_09_15.md`,
`design/RESEARCH_THESES_ACTIVE_DORMANT.md`, `src/config/
tracked_companies.py` (read-only, plus a read-only Python/regex
analysis run through Bash — no edits), `/Users/naia/Desktop/csv.csv`
(read-only reconciliation).

**Not touched**: `tests/test_radar_status.py` (not opened).

**Created**: `design/THEMATIC_COVERAGE_UNIVERSE_AUDIT_2026_09_15.md`
(this document — untracked, not staged, not committed).

No code, config, schema, test, or production-data file was modified.
No git operation beyond read-only status/log/fetch checks was
performed. Nothing was staged, committed, pushed, merged, or deployed.
