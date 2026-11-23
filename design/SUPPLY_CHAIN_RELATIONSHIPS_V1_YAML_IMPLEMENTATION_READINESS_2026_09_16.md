# Supply-Chain Relationships v1 — YAML Implementation Readiness

Status: **read-only implementation-readiness design**. No YAML, code, model, schema, test, registry, ontology, or existing document was created or modified. This document transforms the 12 primary-source-validated relationships from `design/SUPPLY_CHAIN_RELATIONSHIPS_V1_EVIDENCE_VALIDATION_2026_09_16.md` into implementation-ready row specifications, proposes an exact YAML schema and loader/model design (reusing existing patterns, not inventing new ones where an existing one fits), and specifies a full test plan — without implementing any of it.

## 0. Canonical registry identifiers — verified directly, not assumed

Every endpoint's `krx_code`, `exchange`, `source`, `themes`, and `subthemes` was re-confirmed this session by direct query against the current `src/config/tracked_companies.py` (not carried over from memory or the prior validation report):

| Company | `krx_code` | Exchange | Source | Themes | Subthemes |
|---|---|---|---|---|---|
| Taiwan Semiconductor Manufacturing Co Ltd | `TSM` | NYSE | SEC EDGAR | `ai-buildout` | — |
| NVIDIA | `NVDA` | NASDAQ | SEC EDGAR | `ai-buildout` | `compute-accelerators` |
| Advanced Micro Devices | `AMD` | NASDAQ | SEC EDGAR | `ai-buildout` | `compute-accelerators` |
| ASML Holding N.V. | `ASML` | NASDAQ | SEC EDGAR | `ai-buildout` | — |
| Lam Research Corp | `LRCX` | NASDAQ | SEC EDGAR | `ai-buildout` | — |
| SK Hynix | `000660` | KRX | OpenDART / DART | `memory`, `ai-buildout` | `dram`, `hbm` |
| Applied Materials, Inc. | `AMAT` | NASDAQ | SEC EDGAR | `ai-buildout` | — |
| Samsung Electronics | `005930` | KRX | OpenDART / DART | `memory`, `ai-buildout` | `dram`, `hbm`, `compute-accelerators` |
| Lumentum Holdings Inc. | `LITE` | NASDAQ | SEC EDGAR | `photonics` | — |
| Cisco Systems, Inc. | `CSCO` | NASDAQ | SEC EDGAR | `ai-buildout` | `interconnect-switching` |
| CoreWeave, Inc. | `CRWV` | NASDAQ | SEC EDGAR | `ai-buildout` | — |
| Super Micro Computer, Inc. | `SMCI` | NASDAQ | SEC EDGAR | `ai-buildout` | — |
| Dell Technologies Inc. | `DELL` | NYSE | SEC EDGAR | `ai-buildout` | — |
| Vertiv Holdings Co | `VRT` | NYSE | SEC EDGAR | `ai-buildout` | `power-cooling` |
| GE Vernova Inc. | `GEV` | NYSE | SEC EDGAR | `ai-buildout` | `power-cooling` |
| Amazon.com, Inc. | `AMZN` | NASDAQ | SEC EDGAR | `ai-buildout` | — |
| Redwire Corp | `RDW` | NYSE | SEC EDGAR | `space` | — |
| Intuitive Machines, Inc. | `LUNR` | NASDAQ | SEC EDGAR | `space` | — |

**Result: all 18 unique companies across the 12 edges map cleanly to existing registry identifiers. Zero candidates are blocked on a missing registry identifier.** This is the expected, verified outcome of the validation report's own entity-model constraint (§3 of the discovery document) — every endpoint in the v1 cohort is a currently-tracked public issuer.

## 1. Final 12-edge readiness table

For every field below: `primary_theme` uses only `ontology.PRIMARY_THEMES`; `layer` uses only `ontology.SUPPLY_CHAIN_LAYERS`; `subtheme` reuses an existing subtheme string already in use somewhere in `TRACKED_COMPANIES`, or is explicitly left unset — never invented. Where a mapping required a genuine judgment call (not a mechanical lookup), it is flagged as such rather than presented with false certainty.

### 1. `tsm-nvda-foundry`
- Source: `TSM` (public_issuer) → Target: `NVDA` (public_issuer)
- Type: `foundry` **(requires vocabulary extension — see §4)** · Direction: directional, TSM is the foundry
- Display: on TSM's page — "Foundry for NVIDIA"; on NVDA's page (reciprocal) — "Foundry: Taiwan Semiconductor Manufacturing"
- Product/process: Advanced-node wafer fabrication for AI accelerators
- Theme/layer/subtheme: `ai-buildout` / `semiconductor-foundry` / `compute-accelerators` (matches NVDA's own tracked subtheme exactly — a mechanical, non-judgment mapping)
- Evidence: NVIDIA 10-K, https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm, acc. 0001045810-26-000021, 2026-02-25 — *"We utilize foundries, such as Taiwan Semiconductor Manufacturing Company Limited, or TSMC... to produce our semiconductor wafers."*
- Confidence: High · Status: active · valid_from/valid_to: unknown/none (ongoing, no disclosed end) · last_verified_at: 2026-09-16
- Revalidation: annual, or upon either party's next 10-K/20-F
- Permitted v1 use case: TSMC capacity/utilization disclosure → potential read-through for NVIDIA supply
- Exclusions: no exclusivity implied — NVIDIA's own filing also names Samsung as a foundry; this edge does not represent single-sourcing

### 2. `tsm-amd-foundry`
- Source: `TSM` → Target: `AMD` · Type: `foundry`, directional
- Display: "Foundry for AMD" / "Foundry: Taiwan Semiconductor Manufacturing"
- Product/process: Advanced-node wafer fabrication (HPC/FPGA/adaptive SoC products)
- Theme/layer/subtheme: `ai-buildout` / `semiconductor-foundry` / `compute-accelerators` (matches AMD's own tracked subtheme)
- Evidence: AMD 10-K, https://www.sec.gov/Archives/edgar/data/2488/000000248826000018/amd-20251227.htm, acc. 0000002488-26-000018, 2026-02-04 — *"We utilize Taiwan Semiconductor Manufacturing Company Limited (TSMC) for the production of wafers for our HPC, FPGA and adaptive SoC products..."*
- Confidence: High · Status: active · valid_from/valid_to: unknown/none · last_verified_at: 2026-09-16
- Revalidation: annual, or upon either party's next 10-K/20-F
- Permitted v1 use case: same pattern as edge 1, for AMD
- Exclusions: AMD's own filing also names GlobalFoundries/UMC/Samsung for other nodes/products — this edge covers only the TSMC-specific relationship

### 3. `asml-tsm-supplier`
- Source: `ASML` → Target: `TSM` · Type: `supplier`, directional
- Display: "Supplier to TSM" / "Supplier: ASML Holding"
- Product/process: EUV lithography systems
- Theme/layer/subtheme: `ai-buildout` / `semiconductor-equipment` / *unset* — no existing subtheme names lithography specifically; left unset per this registry's own established convention rather than stretched
- Evidence: ASML 20-F, https://www.sec.gov/Archives/edgar/data/937966/000093796625000009/asml-20241231.htm, acc. 0000937966-25-000009, 2025-03-05 — *"...one of our key customers, TSMC... a broad range of ASML tools in action in the chip manufacturing process."*
- Confidence: High · Status: active · valid_from/valid_to: unknown/none · last_verified_at: 2026-09-16
- Revalidation: annual, or upon either party's next 20-F
- Permitted v1 use case: ASML shipment/backlog disclosure → potential read-through for TSMC capacity
- Exclusions: none noted

### 4. `lrcx-skhynix-supplier`
- Source: `LRCX` → Target: `000660` (SK Hynix) · Type: `supplier`, directional
- Display: "Supplier to SK Hynix" / "Supplier: Lam Research"
- Product/process: Etch and deposition equipment for memory fabrication
- Theme/layer/subtheme: `memory` **(judgment call — see below)** / `semiconductor-equipment` / *unset*
- **Judgment call, flagged explicitly**: Lam Research's own company-level primary theme is `ai-buildout`, but SK Hynix's is `memory`. This edge's `primary_theme` is set to `memory` because the disclosed product ("memory fabrication" equipment) is specifically memory-themed, not because either endpoint's own registry theme dictates it. This establishes the general rule stated in §1 preamble: **an edge's `primary_theme` reflects the thematic context of the specific product/process, not necessarily either endpoint's own company-level theme** — recommended as an explicit, documented design principle, not resolved silently.
- Evidence: Lam Research 10-K, https://www.sec.gov/Archives/edgar/data/707549/000070754926000037/lrcx-20260628.htm, acc. 0000707549-26-000037, 2026-08-07 — *"Our most significant customers... included Micron Technology, Inc., Samsung Electronics Company, Ltd., SK hynix Inc., and Taiwan Semiconductor Manufacturing Company."*
- Confidence: High · Status: active · valid_from/valid_to: unknown/none · last_verified_at: 2026-09-16
- Revalidation: annual, or upon LRCX's next 10-K
- Permitted v1 use case: Lam order/backlog disclosure → potential read-through for SK Hynix capex
- Exclusions: SK Hynix is not SEC-registered — this edge's evidence is one-sided (from the supplier only); no SK Hynix-side corroboration exists in this cohort

### 5. `amat-samsung-supplier`
- Source: `AMAT` → Target: `005930` (Samsung Electronics) · Type: `supplier`, directional
- Display: "Supplier to Samsung Electronics" / "Supplier: Applied Materials"
- Product/process: Wafer deposition/etch equipment
- Theme/layer/subtheme: `ai-buildout` **(judgment call)** / `semiconductor-equipment` / *unset*
- **Judgment call, flagged explicitly**: the evidence names Samsung as accounting for a % of net revenue without specifying which Samsung business unit (memory vs. logic/foundry) the equipment serves — Samsung's own registry entry carries both `memory` and `ai-buildout`. `ai-buildout` is chosen (matching Applied Materials' own theme, and a valid co-theme on Samsung's own entry) but this is explicitly a call that should be confirmed by a human reviewer at implementation time, not treated as settled by this document.
- Evidence: Applied Materials 10-K FY2024, https://www.sec.gov/Archives/edgar/data/6951/000000695124000044/amat-20241027.htm, acc. 0000006951-24-000044, 2024-12-13 — *"Samsung Electronics Co., Ltd. 12% [of net revenue]"*
- Confidence: High, **with an active freshness caveat** — see §7 for the full freshness-policy treatment of this specific edge
- Status: active (not yet downgraded — see §7 for why) · valid_from/valid_to: unknown/none · last_verified_at: 2026-09-16
- Revalidation: **Immediate** — the cited evidence is already ~21 months stale relative to AMAT's current (anonymized) reporting practice
- Permitted v1 use case: Applied Materials guidance disclosure → potential read-through for Samsung's fab investment
- Exclusions: this edge's confidence should not be treated as durable without re-confirmation — see §7

### 6. `lite-csco-supplier`
- Source: `LITE` → Target: `CSCO` · Type: `supplier`, directional
- Display: "Supplier to Cisco Systems" / "Supplier: Lumentum Holdings"
- Product/process: Optical transceiver modules
- Theme/layer/subtheme: `photonics` / `interconnect` / `optical-components` (reused from Coherent Corp's own existing tracked subtheme — not invented)
- Evidence: Lumentum 10-K FY2022, https://www.sec.gov/Archives/edgar/data/1633978/000162828022023617/lite-20220702.htm, acc. 0001628280-22-023617, 2022-08-24 — *"Our OpComms customers include... Cisco Systems (including Acacia Communications, which was acquired by Cisco)..."*
- Confidence: High, **with a freshness flag** — the cited filing is a fiscal-2022 10-K, over 3 years old at implementation time; recommend re-confirming against Lumentum's current 10-K before this edge is treated as fully current
- Status: active · valid_from/valid_to: unknown/none · last_verified_at: 2026-09-16
- Revalidation: annual — explicitly re-verify against Lumentum's most recent 10-K before production use, not just at some future annual cadence
- Permitted v1 use case: Lumentum order trends → potential read-through for Cisco's networking supply
- Exclusions: none noted beyond the freshness flag

### 7. `crwv-nvda-integrator`
- Source: `CRWV` → Target: `NVDA` · Type: `integrator` **(requires vocabulary extension)**, directional
- Display: "Integrates: NVIDIA" (on CoreWeave's page) / "Integrated by: CoreWeave" (reciprocal, on NVIDIA's page)
- Product/process: NVIDIA GPU systems for AI cloud infrastructure
- Theme/layer/subtheme: `ai-buildout` / `compute-hardware` / `compute-accelerators` (matches NVDA's own tracked subtheme)
- Evidence: CoreWeave Form S-1, https://www.sec.gov/Archives/edgar/data/1769628/000119312525044231/d899798ds1.htm, acc. 0001193125-25-044231, 2025-03-03 — *"...all of the GPUs used in our infrastructure today are NVIDIA GPUs"*; *"our current customers have contractually specified our use of NVIDIA GPUs."*
- Confidence: High · Status: active · valid_from/valid_to: unknown/none · last_verified_at: 2026-09-16
- Revalidation: annual, or upon CRWV's next 10-K (post-IPO)
- Permitted v1 use case: NVIDIA product-launch/allocation disclosure → potential read-through for CoreWeave capacity; CoreWeave capex → potential read-through for NVIDIA demand (this edge is the strongest bidirectional-inference candidate in the whole cohort)
- Exclusions: an S-1 is a point-in-time IPO disclosure, not a recurring annual filing — recommend the first post-IPO 10-K be the trigger for the next revalidation, not a fixed 12-month clock from the S-1 date

### 8. `smci-nvda-integrator`
- Source: `SMCI` → Target: `NVDA` · Type: `integrator`, directional
- Display: "Integrates: NVIDIA" / "Integrated by: Super Micro Computer"
- Product/process: NVIDIA GPU-based server systems (Hopper/Blackwell platforms)
- Theme/layer/subtheme: `ai-buildout` / `compute-hardware` / `compute-accelerators`
- Evidence: Super Micro 10-K FY2025, https://www.sec.gov/Archives/edgar/data/1375365/000137536525000027/smci-20260630.htm, acc. 0001375365-25-000027, 2025-08-28 — *"...products compatible with NVIDIA's Hopper (H100/H200) and Blackwell (GB200/B200/RTX Pro 6000) generation platforms... We work closely with NVIDIA..."*
- Confidence: High · Status: active · valid_from/valid_to: unknown/none · last_verified_at: 2026-09-16
- Revalidation: annual, or upon SMCI's next 10-K
- Permitted v1 use case: same bidirectional pattern as edge 7
- Exclusions: none noted

### 9. `dell-nvda-integrator`
- Source: `DELL` → Target: `NVDA` · Type: `integrator`, directional
- Display: "Integrates: NVIDIA" / "Integrated by: Dell Technologies"
- Product/process: NVIDIA GPU-based AI server systems (PowerEdge XE series)
- Theme/layer/subtheme: `ai-buildout` / `compute-hardware` / `compute-accelerators`
- Evidence: Dell newsroom press release, https://www.dell.com/en-us/dt/corporate/newsroom/announcements/detailpage.press-releases~usa~2024~03~20240318-dell-offers-complete-nvidia-powered-ai-factory-solutions-to-help-global-enterprises-accelerate-ai-adoption.htm, 2024-03-18 — *"Dell PowerEdge XE9680 servers will support new NVIDIA GPU models, including the NVIDIA B200 Tensor Core GPU..."*
- Confidence: High, **with a source-hierarchy caveat**: Dell's own FY2025 10-K contains zero occurrences of "NVIDIA" — the evidence for this edge is a press release only, never corroborated by the SEC filing. Record this explicitly rather than implying 10-K-level corroboration exists.
- Status: active · valid_from/valid_to: unknown/none · last_verified_at: 2026-09-16
- Revalidation: semi-annual — Dell issues frequent product-refresh press releases (corroborating releases exist dated 2024-05-20, 2025-05-19, 2025-11-17); use the most recent one at any inference time, not this founding one indefinitely
- Permitted v1 use case: Dell product-line launch → potential read-through for NVIDIA demand
- Exclusions: press-release-only evidence; do not represent this edge as 10-K-corroborated

### 10. `vrt-crwv-infrastructure_provider`
- Source: `VRT` → Target: `CRWV` · Type: `infrastructure_provider` **(requires vocabulary extension)**, directional
- Display: "Provides infrastructure to CoreWeave" / "Infrastructure from: Vertiv Holdings"
- Product/process: Liquid cooling and power infrastructure for AI/Neocloud data centers
- Theme/layer/subtheme: `ai-buildout` / `power-infrastructure` **(judgment call — see below)** / `power-cooling` (matches VRT's own tracked subtheme exactly)
- **Judgment call, flagged explicitly**: the evidence describes both cooling and power infrastructure, and `ontology.SUPPLY_CHAIN_LAYERS` has these as two *separate* values (`power-infrastructure` and `thermal-management`) with no combined option — mirroring the granularity gap already visible in Vertiv's own tracked `power-cooling` subtheme (a combined term the layer taxonomy itself doesn't have an equivalent for). `power-infrastructure` is chosen as primary; `thermal-management` is equally defensible. This is a real, existing ontology-granularity mismatch, noted here rather than silently resolved or "fixed" (fixing it is explicitly out of scope for this task).
- Evidence: Vertiv 10-K FY2025, https://www.sec.gov/Archives/edgar/data/1674101/000167410126000008/vrt-20251231.htm, acc. 0001674101-26-000008, 2026-02-13 — *"Examples of companies in this space include CoreWeave and Nebius."* (under "Our Customers → Neocloud")
- Confidence: **Moderate** (named, dated, Tier A, but one-sided market-segment framing, not an explicit vendor-relationship statement)
- Status: active, one-sided disclosure · valid_from/valid_to: unknown/none · last_verified_at: 2026-09-16
- Revalidation: annual — flag for human review if a firmer, two-sided disclosure (e.g. a named contract) surfaces, which would raise confidence to High
- Permitted v1 use case: a Vertiv capacity/order disclosure is a *moderate-confidence-only* potential read-through for Neocloud-segment operators including CoreWeave — never presented at the same confidence as the High-tier edges above
- Exclusions: do not treat as a confirmed vendor contract; CoreWeave's own filings never name Vertiv

### 11. `gev-amzn-infrastructure_provider`
- Source: `GEV` → Target: `AMZN` · Type: `infrastructure_provider`, directional
- Display: "Provides infrastructure to Amazon.com" / "Infrastructure from: GE Vernova"
- Product/process: Turnkey substation solutions connecting AWS data centers to the grid
- Theme/layer/subtheme: `ai-buildout` / `power-infrastructure` / `power-cooling` (matches GEV's own tracked subtheme)
- Evidence: GE Vernova press release, https://www.gevernova.com/news/press-releases/ge-vernova-aws-accelerate-global-energy-demand, 2025-03-04 — *"GE Vernova will continue to provide AWS with turnkey substation solutions to enable connectivity of AWS data centers to the grid."*
- Confidence: Moderate-High · Status: active · valid_from/valid_to: unknown/none · last_verified_at: 2026-09-16
- Revalidation: upon disclosed capacity expansion/renewal news, or annually if none
- Permitted v1 use case: a GE Vernova grid/substation order disclosure → potential read-through for AWS data-center power buildout pace
- Exclusions: the same press release's separate, more speculative "explore additional opportunities... to provide power generation equipment" sentence (gas turbines) is deliberately excluded from this edge's `product_or_process` — it describes a possible future relationship, not a confirmed one, and including it would overstate the evidence

### 12. `rdw-lunr-supplier`
- Source: `RDW` → Target: `LUNR` · Type: `supplier`, directional
- Display: "Supplier to Intuitive Machines" / "Supplier: Redwire"
- Product/process: Terrain Relative Navigation / Hazard Detection and Avoidance cameras (Sentinel product line) for the IM-2 lunar lander mission
- Theme/layer/subtheme: `space` / *unset* **(judgment call — see below)** / *unset*
- **Judgment call, flagged explicitly**: no `SUPPLY_CHAIN_LAYERS` value cleanly describes spacecraft navigation/hazard-detection hardware. `edge-physical-ai` was considered (the cameras perform autonomous, onboard hazard detection) but rejected as a stretch relative to that layer's more general edge-AI-compute framing elsewhere in this codebase; recommend leaving `layer` unset per this registry's own established "don't force a stretched fit" convention, rather than inventing new data or misapplying an existing value.
- Evidence: Redwire press release, https://ir.rdw.com/news-events/press-releases/detail/157/redwire-camera-technology-to-launch-on-board-intuitive, 2025-02-24 — *"Redwire is proud to be the trusted camera supplier for Intuitive Machines' second mission to the lunar surface..."* — Adam Biskner, President, Redwire Space Systems
- Confidence: High · Status: **historical (mission-specific)** — IM-2 has since flown; this is not an open-ended ongoing supply agreement but a completed, mission-scoped delivery
- valid_from: unknown (not disclosed) · valid_to: unknown — **recommend representing this differently from a truly ongoing edge**: see §6 for how v1 should model a mission-scoped, already-completed relationship without a disclosed end date
- last_verified_at: 2026-09-16
- Revalidation trigger: upon the next Intuitive Machines mission announcement — confirm whether Redwire remains the named camera supplier for that mission, or a different edge should be created
- Permitted v1 use case: a future Intuitive Machines mission-hardware disclosure → potential read-through for Redwire, and vice versa
- Exclusions: this edge should not be read as implying an ongoing, open-ended supply contract — it is evidence of one specific, named, completed delivery

## 2. Blocked candidates — none

Every one of the 12 validated relationships maps cleanly to existing registry identifiers and ontology values (with the judgment calls flagged explicitly above, none of which required inventing new registry or ontology data). **No candidate in this cohort is blocked.**

## 3. Evidence-record / relationship-edge count — verified, not assumed

Per the task's own instruction, this was checked directly against the validation report rather than assumed. **Result: 12 evidence records, 12 relationship edges — a strict 1:1 mapping.** Every one of the 12 cohort entries in the validation report's own final table cites exactly one primary evidence URL/document; no two edges share the same evidence document, and no edge in the validated cohort has more than one cited evidence source. (Note: the underlying research pass for edge 5, `amat-samsung-supplier`, also surfaced a second, older corroborating Tier A source — a 2010 8-K press release — but it is **not** included in this count, since it was not carried into the validation report's own final cohort table, and adding it now would mean introducing evidence beyond what the report this task is scoped to actually documents. Flagged here as a legitimate future enhancement to that specific edge, not implemented.)

## 4. Existing code reuse — what's reusable, what stays untouched

| Existing asset | Reuse recommendation |
|---|---|
| `config/eevaresearch_theme_registry.yaml` + `theme_registry_loader.py` + `theme_registry.py` | **Pattern reused, file untouched.** The proposed relationship YAML mirrors this file's exact top-level shape (`registry_version`, `registry_name`, a documented controlled-vocabulary list, list-of-dicts sections) and its loader's exact two-function fail-closed contract (a raising loader and a `load_..._or_none()` non-raising variant returning `(result, reason_code)`). Nothing in the theme registry itself is touched. |
| `src/data_access/company_discovery/` (whole package) | **Left completely untouched.** Per the discovery document's own §1.9 conclusion: this package's dormant, settings-gated, hidden-UI semantics must not be inherited by a research-facing relationship display. |
| `CandidateEvidence` (`src/models/company_discovery_models.py`) | **Not reused directly, not modified.** Conceptually the nearest ancestor of the new `RelationshipEvidence` shape (§5), but it is a per-mention discovery-candidate row, not an edge-supporting evidence record — a new, small, purpose-built model is cleaner than repurposing it in place. |
| `RelationshipType` (`company_discovery_models.py`) | **Extend, don't replace.** Add 6 new values (`foundry`, `manufacturer`, `integrator`, `infrastructure_provider`, `regulator`, `dependency`) to the existing enum. Of these, this specific 12-edge cohort actually needs only `foundry`, `integrator`, and `infrastructure_provider` (plus the already-existing `supplier`) — `manufacturer`, `regulator`, and `dependency` have no cohort member yet but should still be added for the vocabulary's own completeness, per the discovery document. |
| `EntityKind` (`company_discovery_models.py`) | **Reuse by import, unmodified.** `CORPORATE` covers every endpoint in this cohort (all 18 companies are ordinary corporate public issuers) — no new value is needed for v1. |
| `src.config.tracked_companies.get_tracked_companies()` / the `krx_code` field | **Reused directly as the public-issuer lookup helper** — the loader's endpoint-validation step calls this existing function to confirm `entity_ref` resolves to a real, active tracked company; no new lookup mechanism is built. |
| `src.config.ontology.is_valid_theme()` / `is_valid_layer()` | **Reused directly, unmodified** — the loader calls these exact existing functions for `primary_theme`/`layer` validation. |
| Subtheme reuse-or-unset convention (currently only enforced at the *test* level, e.g. `test_tier1_cohort1_subthemes_only_reuse_existing_vocabulary_or_stay_unset`) | **Promoted to a real, code-level loader validation** for this new registry — the loader computes the set of subthemes already in use anywhere in `TRACKED_COMPANIES` at load time and fail-closes if a relationship's `subtheme` isn't in that set and isn't empty. This is a genuinely new piece of validation logic (the existing convention is test-only today), but it reuses the *existing data* (no new subtheme vocabulary is introduced) — flagged as the one place this design adds real logic rather than pure reuse. |

**Recommendation: the smallest additive layer is two new files** (`src/models/relationship_models.py`, `src/config/relationship_seed_loader.py`) plus one extended enum (`RelationshipType`) — no other existing file needs to change.

## 5. YAML schema proposal

**File location**: `config/eevaresearch_relationship_seed.yaml` (mirrors `config/eevaresearch_theme_registry.yaml`'s own location convention exactly — top-level `config/`, not `src/`).

**Top-level shape** — two separate, explicitly-justified sections (`evidence_records`, `relationship_edges`), directly implementing the evidence-vs-edge separation from the discovery document, cross-referenced by ID rather than nested/duplicated:

```yaml
registry_version: 1
registry_name: EevaResearch Supply-Chain Relationship Registry (v1 proof cohort)

# Documents the controlled vocabulary the loader validates relationship_type
# against — mirrors the theme registry's own documented entity_roles list.
relationship_types:
  - customer
  - supplier
  - partner
  - competitor
  - foundry
  - manufacturer
  - integrator
  - infrastructure_provider
  - regulator
  - dependency

evidence_records:
  - evidence_id: tsm-nvda-foundry-ev1
    relationship_id: tsm-nvda-foundry   # explicit FK, never implicit/positional
    source_type: issuer_filing          # controlled: issuer_filing | official_press_release | official_customer_supplier_statement | official_government_record | contract_award_disclosure
    document_type: "10-K"
    filer: "NVIDIA Corp"
    url: "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm"
    accession_number: "0001045810-26-000021"   # optional — only for SEC filings
    evidence_date: "2026-02-25"
    excerpt: >
      We utilize foundries, such as Taiwan Semiconductor Manufacturing
      Company Limited, or TSMC, and Samsung Electronics Co., Ltd., or
      Samsung, to produce our semiconductor wafers.
    discovered_at: "2026-09-16"
    confidence: High     # controlled: High | Moderate | Low
  # ... 11 more, one per edge (§3)

relationship_edges:
  - relationship_id: tsm-nvda-foundry
    source_entity:
      entity_kind: public_issuer   # controlled: public_issuer | private_company | ecosystem
      entity_ref: TSM              # matches TrackedCompany.krx_code exactly when entity_kind=public_issuer
    target_entity:
      entity_kind: public_issuer
      entity_ref: NVDA
    relationship_type: foundry
    direction: directional         # controlled: directional | bidirectional — derivable from relationship_type but kept explicit
    product_or_process: "Advanced-node wafer fabrication for AI accelerators"
    primary_theme: ai-buildout
    layer: semiconductor-foundry   # optional
    subtheme: compute-accelerators # optional
    evidence_ids: [tsm-nvda-foundry-ev1]   # list, not a single ID — supports future multi-evidence edges
    relationship_status: active    # controlled: active | lapsed | disputed | unverified | superseded | historical
    valid_from: null               # ISO date or null
    valid_to: null                 # ISO date or null — see §6 for the historical/mission-scoped case
    last_verified_at: "2026-09-16"
    revalidation_trigger: "annual, or upon either party's next 10-K/20-F"
    reviewer_or_validation_status: human_reviewed_approved   # controlled
    notes: ""
  # ... 11 more
```

**Required vs. optional fields**:
- Evidence record required: `evidence_id`, `relationship_id`, `source_type`, `url`, `evidence_date`, `excerpt`, `discovered_at`, `confidence`. Optional: `document_type`, `filer`, `accession_number`.
- Relationship edge required: `relationship_id`, `source_entity`, `target_entity`, `relationship_type`, `direction`, `product_or_process`, `primary_theme`, `evidence_ids`, `relationship_status`, `last_verified_at`, `reviewer_or_validation_status`. Optional: `layer`, `subtheme`, `valid_from`, `valid_to`, `revalidation_trigger`, `notes`.

**Controlled-vocabulary validation**: `relationship_type` against the file's own documented `relationship_types` list (and, redundantly, against the extended `RelationshipType` enum in code — the YAML-level list exists so the *file itself* is self-documenting and diffable, exactly mirroring the theme registry's own `entity_roles` precedent, even though the enum is the actual source of enforcement); `entity_kind` against `EntityKind`; `direction` against a fixed two-value set; `confidence`/`relationship_status`/`reviewer_or_validation_status`/`source_type` against their own fixed sets (defined in the loader, not the YAML, since these aren't reused elsewhere the way theme/layer are).

**IDs and uniqueness**: `relationship_id` is a human-authored, readable slug (not a hash) — format `{source_ref}-{target_ref}-{relationship_type}`, all lowercase, hyphenated (e.g. `tsm-nvda-foundry`, `lrcx-skhynix-supplier` — note the SK Hynix `krx_code` `000660` is replaced with a readable short-name `skhynix` in the slug for legibility; the loader validates the *slug pattern* loosely, it does not require the slug to literally embed the exact `krx_code`). `evidence_id` is `{relationship_id}-ev{N}`. The loader fails closed on any duplicate `relationship_id` or `evidence_id`.

**Duplicate evidence handling**: the loader fails closed if two evidence records share the same `url` **and** the same `relationship_id` (a true duplicate); it explicitly *permits* the same `url` to appear on evidence records for two *different* `relationship_id`s (the documented "one source, multiple distinct claims" case from the validation report's own §5) — because those are legitimate, separately-excerpted citations, not duplicates.

**Reciprocal display**: never stored — derived at render time from `(relationship_type, direction, which entity is being viewed)`, per the validation report's own §5 policy. No schema field represents it; this is a pure rendering-layer concern, explicitly out of scope for the YAML/loader itself.

**Safe handling of historical/stale/uncertain evidence**: `relationship_status` carries this (§6/§7) — never expressed by deleting or overwriting a record.

**Config-loading failures and error messages**: mirrors `theme_registry_loader.py`'s exact fail-closed contract — `load_relationship_registry(path)` raises a new `RelationshipRegistryError` on any problem (missing file, malformed YAML, non-mapping root, unsupported `registry_version`, a duplicate ID, an unknown controlled-vocabulary value, an orphaned `evidence_ids`/`relationship_id` cross-reference, an `entity_ref` that doesn't resolve to a real tracked company, an invalid `primary_theme`/`layer`, an invalid `subtheme`); `load_relationship_registry_or_none(path)` never raises, returning `(None, <sanitized reason code>)` on any of the same failures — the contract every real future caller should use, exactly mirroring the theme registry's own two-function shape.

**Deterministic ordering and parse behavior**: both `evidence_records` and `relationship_edges` are loaded as **tuples**, preserving the YAML file's own on-disk order (never re-sorted, never keyed by a dict that could reorder on Python version/hash-seed differences) — mirroring `TRACKED_COMPANIES`'s own tuple-preserves-declaration-order convention exactly.

**How an edge references evidence without duplicating text**: `relationship_edges[i].evidence_ids` is a list of foreign keys into `evidence_records` — the excerpt/URL/date text exists exactly once, in the evidence record; the edge never repeats it. This is the literal mechanism implementing the evidence-vs-edge separation.

## 6. Representing a historical, mission-scoped relationship without a disclosed end date (edge 12)

`rdw-lunr-supplier` is evidence of one specific, named, already-completed delivery (IM-2 camera hardware) — not an open-ended ongoing contract, but also not a relationship that was disclosed as *ending* (no "valid_to" date exists to record). Recommend: `relationship_status: historical` (a distinct value from `active`, added to the controlled set specifically for this pattern — mission/delivery-scoped relationships that are real and evidenced but not ongoing in the same sense as a recurring supply agreement), `valid_from`/`valid_to` both left `null` (neither was disclosed), and the `revalidation_trigger` field doing the real work: *"upon the next Intuitive Machines mission announcement — confirm whether Redwire remains the named camera supplier."* This is v1's answer to "how do you represent something real, evidenced, and completed, without inventing dates that were never disclosed" — status carries the semantic distinction, not fabricated date fields.

## 7. Freshness rule — the Applied Materials/Samsung case, worked through exactly

**The rule, restated precisely**: older evidence remains immutable historical evidence; a newer filing that removes/anonymizes a named counterparty must not auto-delete the relationship, but must be able to mark the edge uncertain/stale pending reconfirmation — and v1 must do this with **no database, no worker, no live monitoring**.

**How v1 actually represents this, mechanically**:
1. The `amat-samsung-supplier` edge's founding evidence record (`amat-samsung-supplier-ev1`, the FY2024 10-K quote) is **never edited or deleted**, regardless of what happens later — it remains permanently, verbatim, in `evidence_records`.
2. Because v1 has no live monitoring, the *only* way staleness gets detected is a **human re-check**, driven by the edge's own `revalidation_trigger` field — which for this specific edge is set to `"Immediate"` (§1, edge 5) rather than the generic annual cadence, precisely because this session's own research already found the current AMAT 10-K anonymizes customers.
3. When that human re-check happens (a future, separately-approved action, not performed by this document), one of two things is recorded, **never a silent edit to the existing record**:
   - If the reviewer finds a *new*, current, named disclosure reconfirming the relationship: a **second** evidence record is added (`amat-samsung-supplier-ev2`), and `last_verified_at` is bumped. The edge stays `active`, now with two evidence records spanning two fiscal years — a stronger edge than before, not merely a refreshed one.
   - If the reviewer finds the *current* filing has genuinely gone anonymized/silent (matching what this session's research already observed): the edge's `relationship_status` changes from `active` to `unverified`, `last_verified_at` is bumped to the re-check date, and a **second** evidence record is still added — not corroborating the relationship, but documenting the re-check itself (`source_type: issuer_filing`, `confidence: Low`, `excerpt` quoting the anonymized customer-table language, with a `notes` field on the record explaining what it demonstrates). The founding `ev1` record is untouched.
4. **Illustrative example only, not applied to any real file** — this is what step 3's second branch would look like in the YAML, shown purely to demonstrate the mechanism the freshness rule requires:
```yaml
  - evidence_id: amat-samsung-supplier-ev2
    relationship_id: amat-samsung-supplier
    source_type: issuer_filing
    document_type: "10-K"
    filer: "Applied Materials, Inc."
    url: "<AMAT's current, anonymized 10-K URL — not fetched/recorded by this document>"
    evidence_date: "<the current filing's date>"
    excerpt: "Customer One 11%, Customer Two [unnamed]..."
    discovered_at: "<re-check date>"
    confidence: Low
    notes: "Re-check: current filing no longer names Samsung by name — this record documents the anonymization, does not corroborate the relationship."
```
This example is **not added to this task's proposed data** — the 12-edge cohort in §1 keeps `amat-samsung-supplier` at `relationship_status: active` for now, since the anonymization is a caveat about a *later, not-yet-verified* filing, not a confirmed status change as of this session's own research. Downgrading it preemptively, without an actual re-check having occurred, would be inventing a status this task's own research doesn't support.

## 8. Exact anticipated production and test file list

| File | Purpose |
|---|---|
| `config/eevaresearch_relationship_seed.yaml` (new) | The 12-edge, 12-evidence-record v1 cohort data |
| `src/models/relationship_models.py` (new) | Frozen dataclasses: `RelationshipEvidence`, `RelationshipEdge`, `EntityReference`, `RelationshipRegistry` |
| `src/config/relationship_seed_loader.py` (new) | `load_relationship_registry(path)` / `load_relationship_registry_or_none(path)`, `RelationshipRegistryError` |
| `src/models/company_discovery_models.py` (extend) | Add 6 `RelationshipType` values |
| `tests/test_relationship_seed_loader.py` (new) | Loader validation tests (§9) |
| `tests/test_relationship_models.py` (new) | Model-shape/dedup-key unit tests (§9) |

No change to `config/eevaresearch_theme_registry.yaml`, its loader, `ontology.py`, `tracked_companies.py`, `issuer_registry.py`, `src/data_access/company_discovery/`, or any UI/persistence file.

## 9. Required test plan

| Test | File | What it proves |
|---|---|---|
| `test_all_12_edges_and_evidence_records_parse_successfully` | `test_relationship_seed_loader.py` | The real proposed YAML loads without error, `len(edges) == 12`, `len(evidence_records) == 12` |
| `test_every_relationship_id_is_unique` | same | No duplicate `relationship_id` across the 12 edges |
| `test_every_evidence_id_is_unique` | same | No duplicate `evidence_id` across the 12 evidence records |
| `test_every_edge_endpoint_resolves_to_a_tracked_public_issuer` | same | Every `entity_ref` (all 18 unique across the cohort) resolves via `get_tracked_companies()` to a real, active `TrackedCompany.krx_code` |
| `test_every_evidence_record_has_required_tier_a_fields` | same | `url`, `evidence_date`, `excerpt`, `source_type` are all non-empty for all 12 records |
| `test_every_relationship_type_is_in_the_controlled_vocabulary` | same | Every edge's `relationship_type` is one of the (extended) `RelationshipType` values |
| `test_every_entity_kind_is_valid` | same | Every `entity_kind` is a valid `EntityKind`; confirms all 12 edges use `public_issuer` only (no private/ecosystem in v1) |
| `test_every_primary_theme_and_layer_is_a_valid_ontology_value` | same | `is_valid_theme()`/`is_valid_layer()` pass for every set value |
| `test_every_subtheme_reuses_existing_vocabulary_or_is_unset` | same | Mirrors the existing test-level convention, now enforced as real loader logic (§4) |
| `test_every_edge_evidence_ids_resolve_to_real_evidence_records` | same | Referential integrity: no orphaned `evidence_ids` reference |
| `test_every_evidence_record_relationship_id_resolves_to_a_real_edge` | same | Referential integrity, the reverse direction |
| `test_reciprocal_display_label_derivation_for_each_relationship_type` | `test_relationship_models.py` | A pure function, given `(relationship_type, direction, viewing_entity)`, produces the correct display string for both the forward and reciprocal view — covering `foundry`, `supplier`, `integrator`, `infrastructure_provider` (the 4 types this cohort actually uses) |
| `test_historical_status_edge_does_not_require_valid_to` | same | The `rdw-lunr-supplier` pattern (§6) — a `historical`-status edge with `valid_from`/`valid_to` both `null` loads and validates successfully |
| `test_active_status_edge_with_flagged_freshness_still_loads` | same | The `amat-samsung-supplier` pattern (§7) — an `active` edge whose `revalidation_trigger` says "Immediate" still loads normally; staleness is a human-review signal, not a load-time failure |
| `test_malformed_yaml_returns_none_and_reason_code` | `test_relationship_seed_loader.py` | `load_relationship_registry_or_none()` never raises |
| `test_missing_required_field_fails_closed` | same | Parametrized across every required field in §5 |
| `test_unknown_controlled_vocabulary_value_fails_closed` | same | Parametrized across `relationship_type`/`entity_kind`/`direction`/`confidence`/`relationship_status` |
| `test_deterministic_ordering_matches_file_order` | same | `edges`/`evidence_records` are tuples preserving on-disk declaration order across repeated loads |
| `test_loader_has_zero_runtime_side_effects` | same | An AST-based structural test, mirroring `theme_matching.py`'s own existing structural test — confirms the loader module imports nothing from `src.ui`, no repository/backend/worker module, no `CandidateSignal`/`Issuer`-mutating function; proves this is load-only, never a live caller, never a UI, never persistence, never inference |

**Expected counts, stated exactly (per §3)**: 12 evidence records, 12 relationship edges, 18 unique entity references, 4 distinct `relationship_type` values actually used (`foundry`, `supplier`, `integrator`, `infrastructure_provider`) out of the 10 in the full controlled vocabulary.

## 10. Exact proposed commit boundaries (not created — for a future, separately-approved implementation task)

1. **Model + vocabulary extension**: `src/models/relationship_models.py` (new) + `src/models/company_discovery_models.py` (extend `RelationshipType`) + `tests/test_relationship_models.py`. Self-contained — proves the shapes and the reciprocal-display/status-semantics logic before any real data exists.
2. **Loader + validation**: `src/config/relationship_seed_loader.py` (new) + `tests/test_relationship_seed_loader.py`, developed and tested against a small in-test fixture YAML (not the real cohort yet) — proves the fail-closed contract works before the real file is authored.
3. **The real v1 seed data**: `config/eevaresearch_relationship_seed.yaml` (new, the 12 edges from §1) — a pure-data commit, reviewable as a diff exactly like the theme registry's own YAML, with every field traceable back to this readiness document and the validation report.

## 11. Validation commands (for the future implementation task)

```bash
pytest tests/test_relationship_seed_loader.py tests/test_relationship_models.py -q
pytest tests/ -k "relationship" -q
git diff --check
```

## 12. Backward-compatibility and non-side-effect guarantees

- No existing file is modified except one additive enum extension (`RelationshipType`, 6 new values, zero existing values changed or removed).
- No existing test, model, schema, or loader changes behavior — every existing caller of `RelationshipType`/`EntityKind`/`ontology`/`tracked_companies` continues to see exactly what it sees today.
- The new loader has no runtime caller anywhere in this proposal — it is load-only, imported by nothing outside its own tests, exactly mirroring `theme_registry_loader.py`'s own current "loadable but zero live callers" state (confirmed by the discovery document). No UI, worker, persistence, or inference code is touched or created.
- Loading the YAML performs no network call, no cache write, no database write — it is a pure, local, read-only file parse.

## 13. Risk register and mitigations

| Risk | Mitigation |
|---|---|
| The 5 judgment-call theme/layer mappings (edges 4, 5, 10, 12) get silently treated as settled fact rather than reviewed | Each is flagged explicitly in §1 with its own reasoning; recommend a human product-owner sign-off on exactly these 5 mappings before the real YAML is authored, not a blanket approval of all 12 |
| `amat-samsung-supplier`'s freshness caveat is forgotten and the edge is later relied on as if fully current | `revalidation_trigger: "Immediate"` is a loud, edge-specific signal, not buried in a generic annual cadence; §7's mechanism requires a human re-check before this edge should ever feed a real inference |
| A future implementer repurposes `CandidateEvidence` directly instead of building the new, small model | §4 states explicitly why not to, citing the discovery document's own reasoning; a code review checklist item, not a technical guardrail |
| The subtheme-reuse validation (§4, promoted from test-level to loader-level logic) is the one genuinely new piece of validation logic in this design and could have edge cases not seen in this 12-edge cohort (e.g. a subtheme used by an *inactive* `TrackedCompany`) | Recommend the loader compute its "subthemes in use" set from `active_only=True` tracked companies only, and test this explicitly, rather than assuming it during implementation |
| `krx_code`-based slugs (e.g. `lrcx-skhynix-supplier`) don't literally embed SK Hynix's real `krx_code` (`000660`) for readability — a future maintainer could be confused about the mapping | §5 states the loader validates the *entity_ref* field strictly (must match `000660` exactly) while the *slug* is only loosely pattern-checked for readability — the authoritative identifier is always `entity_ref`, never the slug itself |

## 14. Pre-implementation checklist

- [ ] Human product-owner sign-off on the 5 flagged judgment-call theme/layer/status mappings (edges 4, 5, 10, 12 — four distinct judgment calls, one of which, edge 12's `historical` status, is a new controlled-vocabulary value this design introduces and which should be confirmed as acceptable before implementation).
- [ ] Confirm the exact current line/location of `RelationshipType` in `company_discovery_models.py` hasn't shifted since this document was written.
- [ ] Re-confirm all 18 entity `krx_code` values are still present and unchanged in `tracked_companies.py` at implementation time (this document's own §0 data is current as of 2026-09-16 and could drift).
- [ ] Decide whether to also add the Applied Materials 2010 8-K as a second evidence record for `amat-samsung-supplier` (§3's noted-but-not-added observation) as part of implementation, or defer it — not decided by this document.
- [ ] Confirm whether `manufacturer`/`regulator`/`dependency` (the 3 `RelationshipType` values with zero cohort members) should still be added in the same commit as the 3 values this cohort actually uses, or deferred until a real candidate needs them — this document recommends adding all 6 together for vocabulary completeness, but flags it as a decision, not a foregone conclusion.

## 15. Deferred/rejected candidates — reference only (not re-litigated)

The 10 candidates that did not survive validation (Fabrinet/Coherent, Corning/Coherent, Ciena/Marvell, Astera Labs/Microsoft, Eaton/Digital Realty, Nabtesco/FANUC, Harmonic Drive Systems/Yaskawa, Cognex/Symbotic, Rocket Lab/AST SpaceMobile, Hanwha Aerospace/KAI) are fully documented with specific rejection reasons in `design/SUPPLY_CHAIN_RELATIONSHIPS_V1_EVIDENCE_VALIDATION_2026_09_16.md` §3 and are not reproduced or re-evaluated here — this document's scope is implementation readiness for the 12 that *did* validate.

## 16. Files inspected

`design/SUPPLY_CHAIN_RELATIONSHIPS_V1_EVIDENCE_VALIDATION_2026_09_16.md` (the source of the 12-edge cohort, read in full); `design/EVIDENCE_BACKED_SUPPLY_CHAIN_RELATIONSHIPS_DISCOVERY_2026_09_16.md` (the architecture discovery this readiness document builds on, read in full for its reuse/entity-model/evidence-ladder recommendations); `src/config/tracked_companies.py` (queried directly this session for all 18 entity records — exact `krx_code`/`exchange`/`source`/`themes`/`subthemes`, not assumed from memory); `src/config/ontology.py` (`PRIMARY_THEMES`, `SUPPLY_CHAIN_LAYERS` — reused from this session's already-confirmed values, re-applied here); `config/eevaresearch_theme_registry.yaml` + `src/models/theme_registry.py` + `src/config/theme_registry_loader.py` (the schema/loader pattern this design mirrors, already fully inspected earlier this session); `src/models/company_discovery_models.py` (`RelationshipType`, `EntityKind`, `CandidateEvidence` — already fully inspected earlier this session, reused here without re-reading).

## Confirmations

- **Only-report-file confirmation**: this task created exactly one file — `design/SUPPLY_CHAIN_RELATIONSHIPS_V1_YAML_IMPLEMENTATION_READINESS_2026_09_16.md`. No other file was created, modified, or deleted.
