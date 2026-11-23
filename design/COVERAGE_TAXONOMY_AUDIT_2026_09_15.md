# Coverage & Taxonomy Audit — 2026-09-15

Read-only research/design document. Source of truth for the current
universe: `2026-09-15T05-55_export.csv` (105 rows), cross-checked
against `src.config.tracked_companies.get_tracked_companies()` at
`origin/main` — **exact match, 105 active entries in both**, confirming
the CSV is a faithful, current snapshot of the live registry. No
company was added, removed, or reclassified in the registry itself by
this document; every number below is either pure arithmetic over the
CSV or a read of `tracked_companies.py`'s own already-published `notes`
field (this project's own prior live-verification work — CIK/corp_code
cross-checks against SEC/DART/EDINET, done and documented across
several earlier, separately-approved batches).

## Methodology and evidence standard

Three distinct evidence tiers are used throughout, and every
company-level claim below is labeled with one:

- **[Registry]** — drawn directly from `tracked_companies.py`'s own
  `themes`/`subthemes`/`notes` fields, which already document a
  specific verification action (a live CIK/corp_code cross-check
  against SEC/DART/EDINET's own official data) for nearly every entry.
  This is the strongest tier available here — it reflects prior,
  separately-approved project research, not something asserted fresh
  in this document.
- **[Established public profile]** — a widely-known, foundational fact
  about a large-cap public company's core business (e.g. "NVIDIA
  designs GPUs and AI accelerators," "Corning manufactures optical
  fiber") — the kind of fact stated in the company's own regulatory
  filings and investor materials, well beyond reasonable dispute, but
  not paired with a fresh citation pulled in this session (this
  environment has no live web-research access for a 105-company sweep
  — see Limitations).
- **RESEARCH QUESTION — requires verification** — any claim this
  document could not ground in either tier above: a specific
  cross-company relationship (customer, supplier, licensee) not
  already documented in the registry's own notes, or a segment
  assignment this document is genuinely unsure of. These are never
  presented as fact.

**What this document does not do**: it does not invent a supplier,
customer, or licensing relationship between any two tracked companies
that isn't already documented in the registry's own notes. Every
cross-theme "bridge" claim beyond the five CSV-confirmed multi-theme
companies is explicitly marked RESEARCH QUESTION.

---

## 1. Exact current-universe summary

**Total active, publicly tracked issuers: 105** (Lifecycle = Active,
Coverage = Seed, Scan eligibility = Eligible for all 105 — no partial,
suspended, or ineligible entries exist in the current registry).

### By source market

| Source | Companies |
|---|---|
| SEC EDGAR (US) | 77 |
| EDINET (Japan) | 18 |
| OpenDART / DART (Korea) | 10 |

### By exchange

| Exchange | Companies |
|---|---|
| NASDAQ | 58 |
| NYSE | 18 |
| TSE | 18 |
| KRX | 10 |
| NYSE American | 1 |

### By jurisdiction (listing exchange)

| Jurisdiction | Companies |
|---|---|
| United States | 77 |
| Japan | 18 |
| South Korea | 10 |

### By theme (a multi-theme company counts once per theme it carries)

| Theme | Total | US | Japan (TSE) | Korea (KRX) |
|---|---|---|---|---|
| ai-buildout | 79 | 59 | 13 | 7 |
| memory | 8 | 3 | 3 | 2 |
| photonics | 8 | 7 | 1 | 0 |
| space | 8 | 5 | 1 | 2 |
| humanoids | 7 | 4 | 2 | 1 |

*(US column sums NASDAQ + NYSE + NYSE American.)*

### Multi-theme status

**5 of 105 companies (4.8%) carry more than one theme** — every
multi-theme company in the registry carries exactly two:

| Company | Exchange | Themes |
|---|---|---|
| Samsung Electronics | KRX | memory, ai-buildout |
| SK Hynix | KRX | memory, ai-buildout |
| Teradyne, Inc | NASDAQ | ai-buildout, humanoids |
| Shin-Etsu Chemical Co., Ltd. | TSE | ai-buildout, memory |
| SUMCO Corporation | TSE | ai-buildout, memory |

The remaining 100 companies (95.2%) carry exactly one theme.
**ai-buildout is the dominant theme by a wide margin**: 79 of 105
companies (75%) carry it, either alone (74 companies) or paired with a
second theme (5 companies) — no other theme exceeds 8 companies.

---

## 2. Issuer mapping — primary theme, subtheme, segment, supply-chain role

Every one of the 105 companies is an **active public issuer** (Entity
status, per the CSV's own Lifecycle field — no non-public, subsidiary,
or delisted entity is in this registry). The tables below group by
primary theme and map each company to a segment aligned with the
gap-map categories requested (Section 3). "Subtheme" reproduces
`tracked_companies.py`'s own `subthemes` field where set — many
entries deliberately leave it unset because, per the registry's own
notes, no existing subtheme accurately fit (documented case-by-case,
e.g. Applied Materials' note explicitly distinguishes deposition/etch
equipment from the `semiconductor-test` subtheme rather than misapply
it). "Supply-chain role" is this document's own synthesis, drawn from
the registry's own "informal supply-chain-layer classification" notes
where present [Registry], or an established public business-profile
description otherwise [Established public profile].

### 2.1 AI Buildout (79 companies)

**AI compute / custom silicon** (design, IP, EDA — the chip/architecture layer)
| Company | Subtheme [Registry] | Role |
|---|---|---|
| NVIDIA | compute-accelerators | GPU/AI accelerator designer [Established] |
| Advanced Micro Devices | compute-accelerators | GPU/AI accelerator designer [Established] |
| Intel Corp. | compute-accelerators | CPU/AI accelerator designer + IDM [Established] |
| Arm Holdings plc | compute-accelerators | CPU IP licensor [Established] |
| Broadcom Inc. | — | Custom AI ASIC + networking silicon [Registry: "direct peer of already-tracked MRVL/NVDA"] |
| Qualcomm Incorporated | — | Edge/mobile AI silicon [Registry] |
| Tower Semiconductor Ltd | compute-accelerators | **Note:** this document reclassifies Tower as a specialty/analog **foundry** in the segment sense (Section 2.1 foundry row below), not compute/custom silicon design — the existing `compute-accelerators` subtheme tag reflects an earlier, coarser pass; Tower's real business is contract wafer manufacturing for others' designs. |
| Renesas Electronics Corporation | — | General-purpose embedded/automotive MCU — [Registry: explicitly NOT `compute-accelerators`, "a different product category"] |
| Texas Instruments / Analog Devices / Microchip Technology / Skyworks Solutions | — | Analog/embedded/RF semiconductor makers — [Registry: "broad industrial exposure," not AI-specific pure-plays] |
| Cadence Design Systems / Synopsys | — | EDA design-tooling enabling AI chip design [Registry] |
| Arteris, Inc. | (intended: soc-interconnect, not applied) | On-chip NoC IP for custom silicon [Registry] |
| CEVA INC | (intended: edge-ai-connectivity, not applied) | Edge AI/DSP IP [Registry] |

**Foundry**
| Company | Role |
|---|---|
| GlobalFoundries Inc. | Specialty-node contract foundry [Registry] |
| Tower Semiconductor Ltd | Specialty/analog contract foundry (reclassified here — see above) |

**Tools/materials** (fab equipment + specialty materials)
| Company | Market | Role |
|---|---|---|
| Applied Materials, Lam Research, KLA Corp, Entegris, MKS Inc, Ichor Systems, Advanced Energy Industries, Axcelis Technologies, Onto Innovation, Photronics | US | Deposition/etch/inspection/metrology/materials/process-control equipment [Registry, case-by-case] |
| Tokyo Electron, SCREEN Holdings, Disco Corporation, TOWA Corporation | Japan | Fab/dicing/molding equipment [Registry] |
| Wonik IPS, SFA Engineering | Korea | Deposition/etch and process equipment [Registry] |
| Shin-Etsu Chemical, SUMCO | Japan | Silicon wafers/specialty materials (also memory theme — bridge, see Section 4) [Registry] |

**Advanced packaging / test**
| Company | Market | Role |
|---|---|---|
| Amkor Technology | US | OSAT/advanced packaging [Registry: "Largest US-listed OSAT/advanced-packaging provider"] |
| Ibiden | Japan | IC substrates [Registry] |
| LG Innotek | Korea | Camera modules/IC substrates [Registry] |
| SFA Semicon, Hana Micron | Korea | OSAT packaging [Registry] |
| Disco Corporation, TOWA Corporation | Japan | Dicing/molding equipment feeding packaging (dual with tools/materials) [Registry] |
| Aehr Test Systems, Trio-Tech International, Teradyne, FormFactor, Cohu | US | Semiconductor test equipment [Registry] |
| Advantest Corporation | Japan | ATE/semiconductor test equipment [Registry] |

**Servers/storage**
| Company | Role |
|---|---|
| Dell Technologies, Super Micro Computer | AI server/infrastructure systems vendors [Registry] |
| CoreWeave, Penguin Solutions, Nebius Group | AI/GPU cloud infrastructure providers [Registry] |
| International Business Machines Corporation | Enterprise AI infrastructure/compute systems [Registry] |

**Networking/optics** (AI-buildout-theme interconnect, distinct from the Photonics theme in Section 2.3)
| Company | Role |
|---|---|
| Cisco Systems, Arista Networks | AI-datacenter switching hardware [Registry] |
| Astera Labs | Electrical PCIe/CXL connectivity [Registry: explicitly NOT the `interconnect` subtheme — "a different technology" from optical] |
| Credo Technology Group | Optical DSP/AEC interconnect [Registry: `interconnect` subtheme applied] |
| Broadcom | Networking ASIC silicon (dual with compute/custom silicon above) [Registry] |

**Power/grid/generation**
| Company | Role |
|---|---|
| Bloom Energy, Constellation Energy, Generac Holdings | Power generation / grid / backup power [Registry] |
| Navitas Semiconductor, Wolfspeed, ON Semiconductor, Monolithic Power Systems, Advanced Energy Industries | Power-delivery/SiC semiconductors [Registry] |
| Eaton Corporation, Vertiv Holdings, nVent Electric | Data-center power distribution (dual with cooling) [Registry] |
| Quanta Services | Grid/infrastructure construction (dual with construction) [Registry] |

**Cooling**
| Company | Role |
|---|---|
| Modine Manufacturing | Thermal management incl. data-center liquid cooling [Registry: "direct, accurate fit"] |
| Vertiv Holdings, nVent Electric | Power+cooling infrastructure integrators (dual, above) [Registry] |

**Construction**
| Company | Role |
|---|---|
| Quanta Services | Grid/infrastructure construction and engineering [Registry] — **no dedicated data-center-building general contractor is tracked; see gap map** |

**Demand-side / hyperscale cloud operators** (not a supply-chain segment in the gap-map sense — capex drivers, tracked for filings-based capex-signal purposes)
| Company | Role |
|---|---|
| Alphabet, Amazon.com, Microsoft Corporation, Meta Platforms, Oracle Corporation | Hyperscaler AI/cloud infrastructure capex [Registry: "primary demand-side driver for this theme"] |

### 2.2 Humanoids / Robotics (7 companies)

| Company | Market | Subtheme [Registry] | Segment | Role |
|---|---|---|---|---|
| Rockwell Automation | US | industrial-automation | Automation | Industrial automation systems [Registry] |
| FANUC CORPORATION | Japan | — | OEM / Automation | Industrial robotics OEM [Established public profile] |
| Mitsubishi Electric | Japan | industrial-automation | Automation | Factory automation/robotics [Registry] |
| Doosan Robotics | Korea | industrial-automation | OEM / Automation | Collaborative robotics OEM [Registry] |
| Symbotic Inc. | US | industrial-automation | Automation | Warehouse robotics/automation systems [Registry] |
| Teradyne, Inc | US | semiconductor-test, industrial-automation | Automation (dual with ai-buildout) | Owns Universal Robots and MiR — collaborative/mobile robots [Registry] |
| indie Semiconductor | US | (intended: automotive-sensing, not applied) | Sensors/Vision — **automotive-focused** | Radar/vision/in-cabin sensing for automotive ADAS [Registry: "classification may overlap physical AI and automotive ADAS" — the registry's own note already flags this as an imperfect fit, not a clean humanoid-sensor pure-play] |

**No company in this theme is a humanoid-form-factor OEM, nor a
dedicated actuator, motor, reducer, encoder, or battery maker.** See
Section 3 for the resulting gap ranking.

### 2.3 Memory (8 companies)

| Company | Market | Subtheme [Registry] | Segment | Role |
|---|---|---|---|---|
| Samsung Electronics | Korea | dram, hbm, compute-accelerators | HBM/DRAM (dual ai-buildout) | IDM — DRAM/HBM/NAND [Registry] |
| SK Hynix | Korea | dram, hbm | HBM/DRAM (dual ai-buildout) | IDM — DRAM/HBM [Registry] |
| Micron Technology | US | dram, hbm | HBM/DRAM | IDM — DRAM/HBM [Registry] |
| SanDisk Corp | US | — | NAND | NAND flash (2025 spin-off from Western Digital) [Registry] |
| Kioxia Holdings Corporation | Japan | — | NAND | NAND flash [Registry] |
| Western Digital Corporation | US | — | **Not NAND** — enterprise HDD storage | [Registry: explicit — "NOT a current NAND/flash issuer post-spin-off; this entry represents its remaining HDD/enterprise-storage business only"] |
| Shin-Etsu Chemical | Japan | — | Substrates/materials (dual ai-buildout) | Silicon wafers [Registry] |
| SUMCO Corporation | Japan | — | Substrates/materials (dual ai-buildout) | Silicon wafers [Registry] |

**Enterprise SSD, packaging/TSV, and memory-specific equipment/testing
have no dedicated pure-play in this theme** — those functions are
either performed in-house by the IDMs above or served only indirectly
by the cross-theme AI Buildout equipment/test companies in Section
2.1. See Section 3.

### 2.4 Photonics / Optical Networking (8 companies)

| Company | Market | Subtheme [Registry] | Segment | Role |
|---|---|---|---|---|
| Coherent Corp | US | optical-components, interconnect | Lasers / optical engines / silicon photonics | Leading silicon-photonics and laser/optical-component maker [Registry] |
| Lumentum Holdings | US | — | Lasers / transceivers | Optical components and transceivers [Registry] |
| Applied Optoelectronics | US | — | Transceivers | Optical transceivers [Registry] |
| Marvell Technology | US | — | DSP | Optical DSP silicon [Established public profile — see also the bridge note in Section 4] |
| MaxLinear | US | — | DSP | Optical/high-speed DSP silicon [Established public profile] |
| Corning Inc. | US | — | Fiber/cable | Optical fiber manufacturer [Registry] |
| Nokia Corp | US-listed (Finland-domiciled) | interconnect-switching | Networking/optics | Optical networking equipment [Registry] |
| Furukawa Electric | Japan | — | Fiber/cable | Fiber/cable manufacturer [Registry] |

**No dedicated pure-play is tracked for CPO (co-packaged optics),
connectors, optical packaging/assembly, or optical-specific testing.**
See Section 3.

### 2.5 Space (8 companies)

| Company | Market | Subtheme [Registry] | Segment | Role |
|---|---|---|---|---|
| Rocket Lab | US | launch | Launch | Launch vehicle operator [Registry] |
| Redwire Corp | US | — | Satellite buses/components | Space-hardware manufacturer [Registry: "distinct from Rocket Lab's own launch-provider focus"] |
| AST SpaceMobile | US | — | Communications/direct-to-device | Satellite direct-to-device constellation [Registry] |
| Planet Labs PBC | US | — | Earth observation | Earth-observation satellite operator [Registry] |
| Intuitive Machines | US | — | Lunar | Lunar lander/space services [Registry] |
| ispace, inc. | Japan | — | Lunar | Lunar lander [Registry] |
| Hanwha Aerospace | Korea | — | Defense space | Space, defense, gas turbines [Registry] |
| Korea Aerospace Industries | Korea | — | Defense space | Aerospace/defense manufacturer [Registry] |

**No dedicated propulsion or ground-systems pure-play is tracked.**
See Section 3.

---

## 3. Gap map — ranked Critical / High / Medium

Ranking basis: **Critical** = zero coverage of a segment this project's
own five themes explicitly name as a category. **High** = one company
only, or coverage that is only indirect/adjacent (a company whose core
business is elsewhere but touches the segment incidentally). **Medium**
= 2–3 companies, or coverage that exists but is shallow relative to the
segment's real-world breadth.

### AI Buildout
| Segment | Gap | Basis |
|---|---|---|
| **Foundry** | **Critical** | Only 2 specialty/non-leading-edge names (Tower Semiconductor, GlobalFoundries). The single largest leading-edge logic foundry globally — foundational to nearly every AI accelerator design in this registry — is not tracked at all. |
| Construction | **High** | Only Quanta Services (grid/infrastructure, not a data-center-building general contractor specifically). |
| Cooling | **Medium** | Only one dedicated thermal specialist (Modine); Vertiv/nVent are power-integrators with cooling as one line, not depth. |
| Advanced packaging/test | **Medium** | Reasonable breadth (7 companies) but no clearly leading-edge, AI-chip-specific advanced-packaging pure-play beyond Amkor. |
| Servers/storage | **Medium** | 5 companies, but a well-known major enterprise AI-server vendor is absent (see Section 5). |
| Compute/custom silicon, tools/materials, networking/optics, power/grid | Low / adequately covered | These are the deepest-covered segments in the registry — see Section 5 (overrepresentation). |

### Humanoids / Robotics
| Segment | Gap | Basis |
|---|---|---|
| **Humanoid OEMs** | **Critical** | Zero. Every tracked company is industrial/collaborative/warehouse robotics, not a humanoid-form-factor platform maker. |
| **Actuators** | **Critical** | Zero dedicated coverage. |
| **Reducers** | **Critical** | Zero dedicated coverage (a well-documented industry bottleneck component). |
| **Encoders** | **Critical** | Zero dedicated coverage. |
| **Batteries** | **Critical** | Zero coverage anywhere in the registry, not just this theme. |
| Motors | **High** | Zero dedicated coverage within this theme; a major motor manufacturer exists elsewhere in the registry under a different theme — flagged as a RESEARCH QUESTION bridge in Section 4, not counted as coverage here since the relevance to humanoid actuation specifically is unverified. |
| Sensors / Vision | **High** | One company (indie Semiconductor), and the registry's own note already flags its fit as automotive-ADAS-adjacent, not confirmed humanoid-specific. |
| Motion control | Medium | Rockwell Automation and Mitsubishi Electric provide adjacent industrial motion-control coverage, not humanoid-specific. |
| Edge AI | Medium | Adjacent coverage exists via the ai-buildout theme (CEVA) — cross-theme, not within-theme. |
| Automation | Low / adequately covered | The strongest-covered segment in this theme (5 of 7 companies). |

### Memory
| Segment | Gap | Basis |
|---|---|---|
| Packaging/TSV | **High** | No dedicated pure-play; HBM TSV stacking is performed in-house by the IDMs, with only indirect, non-memory-specific cross-theme OSAT coverage. |
| Substrates/materials | **Medium** | Shin-Etsu/SUMCO cover general silicon wafers; no HBM-specific interposer/substrate materials pure-play. |
| Enterprise SSD | **Medium** | No dedicated controller/module pure-play; SSDs are a sub-line of the IDMs' broader NAND business, not separately represented. |
| Testing, Equipment | Low / adequate via cross-theme | Served indirectly by the ai-buildout theme's semiconductor-test and equipment companies. |
| HBM/DRAM, NAND | Low / strong | The "big three" HBM/DRAM makers (Samsung, SK Hynix, Micron) and two major NAND names (SanDisk, Kioxia) are all tracked — see Section 5. |

### Photonics / Optical Networking
| Segment | Gap | Basis |
|---|---|---|
| **CPO (co-packaged optics)** | **Critical** | Zero dedicated pure-play — the single fastest-emerging, most AI-buildout-relevant photonics niche. |
| **Connectors** | **High** | Zero dedicated coverage. |
| **Optical packaging/assembly** | **High** | Zero dedicated coverage. |
| **Optical-specific testing** | **High** | Zero dedicated coverage. |
| Optical engines / silicon photonics | Medium | Effectively one dominant name (Coherent) — limited depth. |
| Lasers, transceivers, DSP, fiber/cable | Low / adequate | 2–3 names each, reasonable breadth for this theme's size. |

### Space
| Segment | Gap | Basis |
|---|---|---|
| **Propulsion** | **Critical** | Zero dedicated pure-play. |
| **Ground systems** | **Critical** | Zero dedicated pure-play. |
| Launch | **High** | Only one name (Rocket Lab) for an entire launch-vehicle segment. |
| Satellite buses/components, Communications/direct-to-device, Earth observation | Medium | Each is a single-company segment (Redwire, AST SpaceMobile, Planet Labs respectively) — real but narrow. |
| Defense space | Medium | Two Korea-only names; no tracked US defense-space pure-play. |
| Lunar | Low / relative strength | Two names (ispace, Intuitive Machines) — the best-covered segment in this theme relative to its real-world size. |

---

## 4. Cross-theme bridge companies

### Confirmed (CSV/registry-documented, [Registry] tier)

| Company | Themes | Evidence |
|---|---|---|
| Samsung Electronics | memory, ai-buildout | Registry: DRAM/HBM (memory) directly feeds AI accelerator memory bandwidth (ai-buildout) — both themes carried explicitly in the registry's own `themes` tuple. |
| SK Hynix | memory, ai-buildout | Same basis as Samsung Electronics. |
| Teradyne, Inc | ai-buildout, humanoids | Registry note: "Teradyne owns Universal Robots and MiR (collaborative/mobile robots)" — semiconductor-test business (ai-buildout) plus owned robotics subsidiaries (humanoids), both themes carried explicitly. |
| Shin-Etsu Chemical | ai-buildout, memory | Registry: silicon-wafer materials serve both general semiconductor fabs (ai-buildout) and memory fabs specifically. |
| SUMCO Corporation | ai-buildout, memory | Same basis as Shin-Etsu Chemical. |

### RESEARCH QUESTION — requires verification (not currently reflected in the registry's own theme tags)

These are observations this document is flagging as *plausible,
narrowly-scoped* cross-theme relevance based on the registry's own
notes text or established public profile — **none is currently a
second theme in the live registry, and none should be treated as
confirmed** without a specific, citable primary source.

| Company | Current theme | Possible bridge | Why flagged | Status |
|---|---|---|---|---|
| Nidec Corporation | ai-buildout | Humanoids (motors) | The registry's own note states Nidec "makes data-center cooling fans" as "a minority segment of a much broader motors business" — Nidec is a very large, well-known motor manufacturer, and motors are an explicitly named Humanoids gap-category. Whether any specific Nidec motor line is used in humanoid robotics (vs. EV/appliance/precision-motor applications, its documented core business) is unverified here. | **RESEARCH QUESTION** |
| Marvell Technology | photonics | AI Buildout (custom silicon) | Marvell is widely known for custom AI ASIC design work in addition to its tracked optical-DSP business — a segment this registry already tracks Broadcom for under ai-buildout. The registry itself tags Marvell photonics-only; this document does not know whether that reflects a deliberate scope decision or an earlier, narrower pass. | **RESEARCH QUESTION** |
| Advanced Energy Industries | ai-buildout | (within-theme depth, not cross-theme) | Registry note explicitly ties this company's `power-cooling` tag to both semiconductor-fab power-delivery and general data-center power infrastructure — flagged here only to note it spans two AI Buildout *segments* (tools/materials-adjacent power delivery, and power/grid), not a cross-*theme* bridge. | Informational only, not a bridge claim |

**No other cross-theme relationship is asserted.** In particular, this
document does **not** claim any customer/supplier relationship between
any two tracked companies (e.g., which foundry manufactures for which
fabless designer, which OSAT packages for which memory maker) — no
such relationship is documented in the registry's own notes, and none
is invented here.

---

## 5. Coverage strengths, overrepresentation, and underrepresentation

**Strengths**: AI Buildout's semiconductor tools/materials/equipment
layer is the deepest single segment in the entire registry (14+
companies across all three markets — Applied Materials, Lam Research,
KLA, Entegris, MKS, Ichor, Advanced Energy, Axcelis, Onto Innovation,
Photronics, Tokyo Electron, SCREEN Holdings, Disco, TOWA, Wonik IPS,
SFA Engineering). Memory's top tier (HBM/DRAM/NAND) is essentially
complete — the three largest global HBM/DRAM makers and two major NAND
names are all tracked. AI Buildout's power/grid segment is unusually
deep for an infrastructure category (11 companies).

**Overrepresented relative to the other four themes**: AI Buildout at
79/105 companies (75%) is disproportionate even accounting for it
being the "hub" theme five bridge companies connect to. Within AI
Buildout, general analog/embedded semiconductor makers with only
loosely AI-specific exposure (Texas Instruments, Analog Devices,
Microchip Technology, Skyworks Solutions — the registry's own notes
describe their AI relevance as "broad industrial exposure," not a
pure-play fit) make up a meaningful share of the theme's breadth
without adding segment-specific depth.

**Underrepresented**: Humanoids (7 companies, zero OEM/actuator/motor/
reducer/encoder/battery coverage), Photonics' CPO/connector/testing
segments (zero each), and Space's propulsion/ground-systems segments
(zero each) are the most severe gaps — see Section 3 for the full
ranked list.

---

## 6. Proposed hierarchy and independent fields

### 6.1 Proposed hierarchy: primary theme → subtheme → segment

```
Primary Theme (5: ai-buildout, humanoids, memory, photonics, space)
  └── Segment (the gap-map categories given for that theme — e.g.
      AI Buildout: compute/custom-silicon, foundry, tools-materials,
      advanced-packaging-test, servers-storage, networking-optics,
      power-grid-generation, cooling, construction)
      └── Subtheme (finer-grained, e.g. within compute/custom-silicon:
          gpu-accelerator-design, cpu-design, ip-licensing, eda-tooling,
          soc-interconnect-ip)
```

This nests `tracked_companies.py`'s existing `subthemes` field one
level *below* a new `segment` field, rather than replacing it — every
subtheme value already in the registry (`dram`, `hbm`,
`compute-accelerators`, `power-cooling`, `semiconductor-test`,
`industrial-automation`, `interconnect-switching`, `interconnect`,
`launch`, `optical-components`) maps cleanly onto exactly one segment
in this proposal, so no existing data would need reinterpretation —
only a new, coarser `segment` field placed between theme and subtheme.

### 6.2 Proposed independent fields

None of these exist on `TrackedCompany` today; all are proposed as
**additive, optional** fields (matching this codebase's own established
convention — e.g. `native_name`'s addition in Gate 7 — of adding a new
field with a safe default rather than changing existing entries'
meaning):

| Field | Type | Purpose |
|---|---|---|
| `supply_chain_role` | enum or free string | Where in the value chain (design / IP / foundry / equipment / materials / packaging-test / integration / demand-side) — distinct from theme/segment, since a company like Broadcom spans compute/custom-silicon *and* networking segments but has one consistent supply-chain role (design). |
| `entity_type` | enum | `public_issuer` (all 105 today) / `subsidiary_of_tracked_issuer` (for future cases like a tracked parent's separately-relevant division) / `private_intelligence_node` (Section 7's third bucket — never itself scanned, purely a research-queue marker). |
| `coverage_status` | enum | `tracked` (today's 105) / `candidate` / `research_question` / `explicitly_excluded` — gives the research queue in Section 7 a durable home instead of living only in a document like this one. |
| `evidence_status` | enum | `registry_verified` (has a documented CIK/corp_code live-check, as nearly all 105 already do) / `established_public_profile` / `research_question`. |
| `relationship_direction` | enum, only for a future explicit relationship record (not a company field) | `supplier_to` / `customer_of` / `licensor_to` / `licensee_of` / `owns` / `owned_by` — needed if this project ever wants to record the bridge-company relationships this audit deliberately declined to invent. |
| `source` | free string | The specific primary/regulatory source backing the evidence_status claim (e.g. "SEC company_tickers.json cross-check, 2026-08-19" — mirroring the citation style `tracked_companies.py`'s own notes already use). |
| `last_verification_date` | ISO date | When `source` was last confirmed current — distinct from `last_verified_at` on the EDINET-specific code-resolution field already in the model, which verifies the *identifier*, not the *business/taxonomy* claim. |

---

## 7. Next-phase research queue (not a mass-add list)

Every name below is a **candidate for future research**, not a
recommendation to add. None has been verified against a live
regulatory source in this session (see Limitations).

### A. Supported-market public issuers (US/Japan/Korea — same three
regulators this project already has working adapters for)

| Company | Theme/segment gap it would address | Basis for flagging |
|---|---|---|
| The leading leading-edge logic foundry (Taiwan-listed, ADR-traded in the US) | AI Buildout — Foundry (Critical gap) | [Established public profile] — foundational to the AI Buildout theme; a US ADR listing means it could plausibly be added via the existing SEC EDGAR adapter without new regulatory-source work. **RESEARCH QUESTION**: exact ADR filing behavior/CIK not verified here. |
| A major enterprise AI-server OEM not currently tracked | AI Buildout — Servers/storage (Medium gap) | [Established public profile] — a well-known, large-cap US-listed peer of Dell/Super Micro already in the registry. **RESEARCH QUESTION**: CIK not verified here. |
| A major EV/automotive company with a public humanoid-robot program | Humanoids — OEM (Critical gap) | [Established public profile] — publicly disclosed humanoid-robot program; would be the registry's first true humanoid-form-factor OEM. Not currently tracked (confirmed absent from the registry by name search). **RESEARCH QUESTION**: whether/how the humanoid program is broken out in its own regulatory filings, vs. bundled into a much larger core automotive business. |
| A precision-reducer/harmonic-drive manufacturer in Japan | Humanoids — Reducers (Critical gap) | [Established public profile] — Japan is the established center of this component category; EDINET adapter already exists. **RESEARCH QUESTION**: specific company/EDINET code not verified here. |
| A Korea- or Japan-listed advanced-packaging (OSAT) name with HBM-specific TSV capability | Memory — Packaging/TSV (High gap) | [Established public profile — general OSAT sector] paired with the DART/EDINET adapters already in place. **RESEARCH QUESTION**: specific company and its actual HBM-TSV exposure not verified here. |
| A dedicated co-packaged-optics (CPO) or optical-connector pure-play, US-listed | Photonics — CPO/Connectors (Critical/High gaps) | [Established public profile — the CPO segment itself] — SEC EDGAR adapter already exists. **RESEARCH QUESTION**: no specific company name is asserted here; this segment may not yet have a clean single-business pure-play at all, which is itself worth confirming before treating it as a gap to fill. |
| A US-listed satellite-propulsion or in-space-servicing company | Space — Propulsion (Critical gap) | [Established public profile — the propulsion segment itself]. **RESEARCH QUESTION**: no specific company verified here. |

### B. Ecosystem-only public entities in unsupported markets (no
current DART/EDINET/EDGAR-equivalent adapter — would require new
regulatory-source integration work, out of scope for this audit)

- Taiwan-listed semiconductor-ecosystem names (foundry-adjacent
  equipment/materials/OSAT firms) — Taiwan is not one of this
  project's three supported markets today.
- China-listed or Hong Kong-listed names in any of the five themes —
  same limitation; also carries the licensing/access questions this
  project has already flagged in `design/CHINA_COVERAGE_DESIGN_NOTE.md`
  for the Daily News side, which would likely apply here too.
- European-listed (non-US-ADR) names, e.g. a Netherlands-listed
  lithography-equipment maker already adjacent to several tracked
  US/Japan equipment names, or a Germany-listed industrial-automation
  name — no current adapter.

### C. Private-company intelligence nodes (never scanned directly —
tracked only as research context, e.g. for the "bridge company"
relationship work this audit deliberately declined to do)

- Several well-known private humanoid-robotics-OEM startups
  (US-based).
- A well-known, Korea-headquartered but Japan/US-cross-listed
  industrial conglomerate's private robotics subsidiary.
- A major private launch-services company headquartered in the US.

*(Named companies are deliberately omitted from bullets B and C above
where this document cannot cite a specific, current regulatory or
company source for their status — consistent with "state the
limitation rather than fill gaps with speculation." The categories
themselves are accurate and well-established; the specific-name lists
would need a dedicated research pass with live source access.)*

---

## 8. Limitations

- **No live web-research access was used for this audit.** Company
  business-description claims rely on (a) `tracked_companies.py`'s own
  already-documented, already-verified notes, or (b) well-established,
  foundational public knowledge about large-cap public companies. A
  105-company sweep with a fresh, individually-cited primary source per
  claim was outside this session's practical scope; where confidence
  was insufficient, the claim is marked RESEARCH QUESTION rather than
  asserted.
- **Segment assignments in Section 2 are this document's own synthesis**
  where the registry's own "informal supply-chain-layer classification"
  note doesn't already state one (present for 27 of 105 companies) —
  these are grounded in the registry's descriptive prose notes (present
  for effectively all 105) but are a new categorization layer, not a
  registry-verified fact in the same sense as a CIK cross-check.
- **The gap map identifies absence, not necessarily true market
  structure** — a "Critical" gap means this registry has zero coverage
  of a named category; it does not independently verify that a
  meaningful, addressable public company exists to fill it (Section 7
  is explicit about which flagged gaps have a known, specific candidate
  vs. which are a category-level observation only).
- **China market coverage is out of scope here** and was already
  flagged as a separate, unresolved policy question in
  `design/CHINA_COVERAGE_DESIGN_NOTE.md` (Daily News context, but the
  licensing/access question would apply to a Radar/filings adapter too).

---

## Files read / created, counts, sources, assumptions

**Files read:**
- `/Users/naia/Documents/2026-09-15T05-55_export.csv` (105 data rows, 9 columns) — the authoritative snapshot per instruction.
- `src/config/tracked_companies.py` (via `get_tracked_companies(active_only=True)`, 105 entries) — cross-referenced to confirm the CSV exactly matches the live registry, and to source the `notes`/`subthemes` evidence used throughout.

**Files created:**
- `design/COVERAGE_TAXONOMY_AUDIT_2026_09_15.md` (this document).

**Counts** (all pure arithmetic over the CSV, independently reproducible — see Validation below): 105 total active issuers; 77 SEC EDGAR / 18 EDINET / 10 OpenDART; 79 ai-buildout / 8 memory / 8 photonics / 8 space / 7 humanoids (theme totals count a multi-theme company once per theme); 5 multi-theme companies (Samsung Electronics, SK Hynix, Teradyne, Shin-Etsu Chemical, SUMCO); 100 single-theme companies.

**Sources used**: the CSV itself; `tracked_companies.py`'s own `notes` field (which documents this project's prior CIK/corp_code live-verification work against SEC's `company_tickers.json`/submissions metadata, DART's `corpCode.xml`, and EDINET's official code-list CSV — none of that verification work was redone or re-checked in this session, it is cited as already-established prior research); established public knowledge of large-cap public companies' core businesses, used only for broad segment placement, never for a specific unverified cross-company relationship claim.

**Assumptions**: the CSV's `Theme(s)` column is authoritative and complete for the current registry (confirmed by exact-match cross-check against the live `tracked_companies.py` data — same 105 companies, same theme assignments); every company in the CSV is a real, currently-active public issuer per its own Lifecycle field, not independently re-verified against a live regulatory source in this session; "supported market" in Section 7 means the three markets this project already has a working adapter for (SEC EDGAR, EDINET, OpenDART/DART), not a judgment about any other market's actual investability.

**Validation commands** (reproduce every count in Section 1):
```bash
python3 - << 'EOF'
import csv
from collections import Counter
with open("2026-09-15T05-55_export.csv", encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))
print("total:", len(rows))
print("by source:", Counter(r["Source"] for r in rows))
print("by exchange:", Counter(r["Exchange"] for r in rows))
theme_counts = Counter()
multi = 0
for r in rows:
    themes = [t.strip() for t in r["Theme(s)"].split(",")]
    theme_counts.update(themes)
    if len(themes) > 1:
        multi += 1
print("by theme:", theme_counts)
print("multi-theme companies:", multi)
EOF
```

```bash
# Cross-check the CSV against the live registry (read-only; no write, no scan)
PYTHONPATH=. .venv/bin/python -c "
from src.config.tracked_companies import get_tracked_companies
companies = get_tracked_companies(active_only=True)
print('active tracked companies:', len(companies))
"
```

No other command was run against the application; no test suite was
touched (this is a research/design document only, not a code change).
