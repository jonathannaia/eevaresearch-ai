# Company Universe, Taxonomy, and Supply-Chain Coverage Audit Plan

Status: **read-only audit and design plan**. No registry, model, or database change. No company added, no relationship persisted, no worker run. This document inventories the current tracked-company universe against its actual source of truth, evaluates the existing (partially built, deliberately unreconciled) taxonomy and scoring infrastructure, and proposes a plan — not an implementation — for a granular taxonomy, a relationship/edge model, and a scoring framework for future company additions and second-order Signal generation, across: AI Buildout, Humanoids / Robotics, Memory, Photonics / Optical Networking, Space.

## 0. Authoritative files inspected

The instruction to "not assume the old CSV is current" was correct to give: **there is no CSV anywhere in this repository.** `find . -iname "*.csv"` (excluding `.venv`) returns zero results. Any CSV referenced in earlier design docs (e.g. `COVERAGE_TAXONOMY_AUDIT_2026_09_15.md`'s "105-row CSV export") was a working export from a prior session, not a tracked or currently-present artifact — it no longer exists and was not treated as authoritative here.

The real source of truth is code, verified by direct import and static read, not by memory or by trusting any prior doc's stated counts (several of which are stale — see §5):

| File | Role |
|---|---|
| `src/config/tracked_companies.py` (1,792 lines) | **The authoritative tracked-company registry.** A frozen-dataclass tuple, `TRACKED_COMPANIES`, of 115 `TrackedCompany` records — the real input to every live DART/EDGAR/EDINET scan pipeline. |
| `src/config/issuer_registry.py` (555 lines) | `SEED_ISSUERS` (generated losslessly from `TRACKED_COMPANIES`, never hand-maintained) + `DISCOVERY_STUBS` (26 unverified candidate `Issuer` records, structurally excluded from every scan pipeline). |
| `src/config/ontology.py` | `PRIMARY_THEMES` (the real, enforced 5-theme dashboard taxonomy) + `SUPPLY_CHAIN_LAYERS` (16 formally-defined layer labels) + `KNOWN_CATEGORY_CONFLICTS` (4 already-documented, deliberately-unresolved classification disagreements). |
| `config/eevaresearch_theme_registry.yaml` + `src/models/theme_registry.py` + `src/config/theme_registry_loader.py` | A separate, more granular 13-theme classification taxonomy for future autonomous discovery/retrieval — validated and loadable, but with **zero live callers** today. |
| `src/models/issuer.py` | The `Issuer` model — `CoverageState`, `LifecycleState`, `parent_issuer_id`/`entity_kind` (subsidiary-only relationship fields). No supplier/customer/relationship field of any kind. |
| `src/logic/issuer_coverage.py` | Pure count/filter utility over the registry. No thematic or supply-chain gap analysis. |
| `src/models/company_discovery_models.py` + `src/data_access/company_discovery/scoring.py` (Company Discovery Phase 2, dormant, no promotion path) | A **already-built, tested, 7-factor weighted composite scoring framework** for candidate-company relevance, and a `RelationshipType` enum (`SUPPLIER`, `CUSTOMER`, `PARTNER`, `COMPETITOR`, `THEMATIC_MENTION`) used today only as a per-mention evidence tag, not a persisted company-to-company edge. |
| `data/seed/themes.json` | A 5-theme × fixed-subtheme "demo catalog" taxonomy — formally disconnected from the real registry (`ontology.py`'s own docstring calls it out as such); `TrackedCompany.subthemes` do not validate against it. |

Ten pre-existing, untracked 2026-09-15 design docs (`COVERAGE_TAXONOMY_AUDIT`, `THEMATIC_COVERAGE_UNIVERSE_AUDIT`, `COVERAGE_EXPANSION_CANDIDATES_TIER1`, `COVERAGE_EXPANSION_FIRST10_REGISTRY_READINESS`, `TIER1_COHORT1_REGISTRY_READINESS`, `TIER1_COHORT1_TRACKED_COMPANIES_IMPLEMENTATION_DESIGN`, `ISSUER_REGISTRY_FOUNDATION`, `THEME_REGISTRY_FOUNDATION`, plus two Daily-News-materiality docs that are out of scope here) were read for continuity. Their stated counts (29 tracked companies, then 105; 21 discovery stubs) are stale — the registry has grown since each was written. Their conclusions were treated as prior analysis to build on, not as current state.

## 1. Tracked-company inventory (verified by direct parse of `TRACKED_COMPANIES`, 2026-09-16)

**Total: 115 entries, all `active=True`.** No inactive/retired entries exist.

**By source:** SEC EDGAR 82 (71%), EDINET (Japan) 21 (18%), OpenDART/DART (Korea) 12 (10%).
**By exchange:** NASDAQ 60, NYSE 21, TSE 21, KRX 12, NYSE American 1.
**By listing-country proxy (exchange only — not legal domicile; several EDGAR entries are foreign private issuers, see §2):** United States 82, Japan 21, South Korea 12. **Zero** companies listed in China, Taiwan, or continental Europe (ex. exchange-agnostic ADRs).

**By primary theme (`themes[0]`):**

| Theme | Count | Share |
|---|---|---|
| ai-buildout | 81 | 70% |
| space | 10 | 9% |
| humanoids | 9 | 8% |
| photonics | 8 | 7% |
| memory | 7 | 6% |

ai-buildout outnumbers every other theme 9–11x. This is the single largest structural imbalance in the registry (detailed in §2).

**Full company list by theme** (primary theme; 6 companies additionally bridge — see §3):

- **memory (7):** Samsung Electronics, SK Hynix, Micron Technology, SanDisk Corp, Kioxia Holdings, Western Digital Corporation, Hanmi Semiconductor
- **photonics (8):** Coherent Corp, Lumentum Holdings, Applied Optoelectronics, Corning Inc., Marvell Technology, MaxLinear, Nokia Corp, Furukawa Electric
- **humanoids (9):** Rockwell Automation, FANUC, indie Semiconductor, Doosan Robotics, Mitsubishi Electric, Symbotic, Nabtesco, Harmonic Drive Systems, Yaskawa Electric
- **space (10):** Rocket Lab, ispace, Redwire, Hanwha Aerospace, Korea Aerospace Industries, Intuitive Machines, AST SpaceMobile, Planet Labs, Firefly Aerospace, L3Harris Technologies
- **ai-buildout (81):** NVIDIA, AMD, Intel, Arm Holdings, Nebius Group, Penguin Solutions, Tower Semiconductor, Aehr Test Systems, Trio-Tech International, Navitas Semiconductor, Bloom Energy, SoftBank Group, Arteris, CEVA, Cisco Systems, Arista Networks, Quanta Services, nVent Electric, Applied Materials, Lam Research, KLA Corp, Entegris, Amkor Technology, MKS Inc, Vertiv Holdings, Teradyne, Astera Labs, LG Innotek, Wonik IPS, SFA Engineering, SFA Semicon, Hana Micron, Tokyo Electron, Advantest, Disco Corporation, Shin-Etsu Chemical, SUMCO, Ibiden, Renesas Electronics, Alphabet, Amazon.com, Microsoft, Meta Platforms, Oracle, Broadcom, Qualcomm, Dell Technologies, Super Micro Computer, Credo Technology, Eaton Corp, Cadence Design Systems, Synopsys, FormFactor, Cohu, Axcelis Technologies, Onto Innovation, Photronics, CoreWeave, Texas Instruments, Analog Devices, Monolithic Power Systems, ON Semiconductor, Microchip Technology, GlobalFoundries, Constellation Energy, Skyworks Solutions, Modine Manufacturing, Wolfspeed, Advanced Energy Industries, Ichor Systems, IBM, Generac Holdings, SCREEN Holdings, Nidec, TDK, Murata Manufacturing, TOWA, HD Hyundai Electric, GE Vernova, Digital Realty Trust, Equinix

**Subtheme usage** (formally unvalidated free text — see §2/§4): only **10 distinct subtheme strings** appear across all 115 entries, and **74 of 115 companies (64%) carry no subtheme at all.**

| Subtheme | Count | Companies |
|---|---|---|
| power-cooling | 15 | Navitas, Bloom Energy, Quanta Services, nVent, Vertiv, Eaton, Monolithic Power, ON Semi, Constellation Energy, Modine, Wolfspeed, Advanced Energy Industries, Generac, HD Hyundai Electric, GE Vernova |
| compute-accelerators | 6 | Samsung, NVIDIA, AMD, Intel, Arm, Tower Semiconductor |
| industrial-automation | 6 | Rockwell, Teradyne, Doosan Robotics, Mitsubishi Electric, Symbotic, Yaskawa |
| semiconductor-test | 6 | Aehr, Trio-Tech, Teradyne, Advantest, FormFactor, Cohu |
| dram | 3 | Samsung, SK Hynix, Micron |
| hbm | 3 | Samsung, SK Hynix, Micron |
| interconnect-switching | 3 | Nokia, Cisco, Arista |
| interconnect | 2 | Coherent Corp, Credo Technology |
| launch | 2 | Rocket Lab, Firefly Aerospace |
| optical-components | 1 | Coherent Corp |

## 2. Coverage strengths, overrepresentation, gaps, and missing bottleneck categories

### Strengths
- **Semiconductor-equipment depth is genuinely strong**: Applied Materials, Lam Research, KLA, Tokyo Electron, Advantest, Disco, SCREEN Holdings, Wonik IPS, SFA Engineering — a real, multi-geography bench across deposition/etch/inspection/test.
- **Power/cooling for AI datacenters is the single deepest subtheme** (15 companies, `power-cooling`) — reflects a deliberate, well-executed buildout of that bottleneck category.
- **Memory duopoly-plus is covered**: Samsung, SK Hynix, Micron, Kioxia, SanDisk, Western Digital — the six companies that matter globally in DRAM/NAND/HBM are essentially all present.
- **Geographic diversity across three real regulatory pipelines** (SEC EDGAR, Korea DART, Japan EDINET) is a structural strength most single-market registries don't have.

### Overrepresentation
- **ai-buildout at 70% of the registry (81/115)** structurally crowds out the other four themes, which are proportionally very thin (6–10 companies each). This is not necessarily wrong — AI Buildout may legitimately have the largest addressable universe — but it means the other four themes currently have far less room to represent depth, bridges, or bottleneck redundancy.
- Within ai-buildout itself, `power-cooling` (15) and hyperscaler/demand-side names (Alphabet, Amazon, Microsoft, Meta, Oracle — 5 single-purpose "capex driver" entries with no subtheme) are heavily represented relative to design-tooling (Cadence, Synopsys — 2 entries) or specialty-materials (Entegris only — 1 entry for the entire materials layer outside Japan).

### Missing bottleneck categories (the most material finding)
- **TSMC and ASML are both completely absent from `TRACKED_COMPANIES`, `issuer_registry.py`, and every other registry file** (confirmed by direct grep — zero occurrences of "TSM" or "ASML" as a tracked or stub entity anywhere in `src/`). These are arguably the two single most important bottleneck companies across ai-buildout, memory, and photonics: ASML holds an effective monopoly on EUV lithography; TSMC fabricates the overwhelming majority of advanced-node logic silicon that every tracked fabless name (NVIDIA, AMD, Broadcom, Marvell, Qualcomm, Astera Labs, Credo...) depends on. Both are **EDGAR-addressable today with no new source-adapter work**: both are foreign private issuers with real ADR listings that file 20-F/6-K with the SEC (ASML's CIK, 0000937966, was already independently live-verified elsewhere in this codebase — `src/data_access/edgar/edgar_rules.py` — for an unrelated 20-F filename-rules design, on 2026-09-15). This is not a hypothetical gap; it is a verified, immediately actionable one.
- **This exact gap was already identified and scoped in the 2026-09-15 doc set** (`COVERAGE_EXPANSION_CANDIDATES_TIER1_2026_09_15.md` names TSMC and ASML explicitly among 21 proposed candidates; `TIER1_COHORT1_TRACKED_COMPANIES_IMPLEMENTATION_DESIGN_2026_09_15.md` produced a ready-to-apply dataclass-level implementation design for a 10-company GO-status batch). None of that batch has been applied to `tracked_companies.py` — the current 115-company registry does not include it. **This is the single most efficient near-term action available**: the research and readiness-check work already exists and only needs re-verification (CIK/name/listing status may have moved on since 2026-09-15) before being proposed for implementation, rather than repeating the analysis from scratch.
- **Zero China and zero Taiwan coverage.** Given East Asia cross-border supply chains are explicitly named as a critical-priority theme in the 13-theme YAML registry, and given Taiwan (TSMC, MediaTek, and others) and China (memory, packaging, and materials names) are structurally central to every one of the five product themes, this is a real, load-bearing gap — not a stylistic one. `DISCOVERY_STUBS` already names Taiwan-jurisdiction candidates, but there is no source adapter for Taiwan (TWSE) today, so they are structurally unreachable regardless of registry changes.
- **Humanoids has only one confirmed reducer/actuator specialist gap already self-identified**: the registry's own notes (Yaskawa Electric's entry) call out Yaskawa as *"the most surprising pre-existing single-name gap found in the coverage audit (FANUC already tracked as the registry's sole robot-OEM name; Yaskawa is a direct peer at comparable scale)"* — meaning this gap was identified and then filled by adding Yaskawa. The theme still has thin sensor/vision and battery/power-for-robotics representation (`data/seed/themes.json`'s own subtheme catalog names `sensors-vision` and `batteries-power` as humanoids subthemes; zero tracked companies use either).
- **Space has zero satellite-component/subsystem-supplier depth below the prime level** — the 10 tracked names are all primes or launch/lander companies (Rocket Lab, ispace, Redwire, Hanwha Aerospace, Korea Aerospace Industries, Intuitive Machines, AST SpaceMobile, Planet Labs, Firefly Aerospace, L3Harris); no dedicated propulsion, solar-array, or radiation-hardened-electronics component supplier is present.
- **Photonics has essentially no laser/light-source-specific pure play** distinct from transceiver/switch vendors already covered; `data/seed/themes.json`'s own `lasers-optical-components` subtheme has no tracked-company match at all.
- **A REIT/real-estate business model has no home in the layer taxonomy** — the registry's own notes (on a datacenter REIT entry) state *"No existing subtheme fits a REIT; left unset"* — flagging that the taxonomy has no category at all for datacenter-infrastructure-as-real-estate, despite Digital Realty Trust and Equinix both being tracked under ai-buildout with no subtheme.

## 3. Bridge companies (2+ themes)

Only **6 of 115 companies (5%)** carry more than one theme. No company carries three or more, and none bridges to photonics or space:

| Company | Themes |
|---|---|
| Samsung Electronics | memory, ai-buildout |
| SK Hynix | memory, ai-buildout |
| Hanmi Semiconductor | memory, ai-buildout |
| Teradyne | ai-buildout, humanoids |
| Shin-Etsu Chemical | ai-buildout, memory |
| SUMCO | ai-buildout, memory |

This is almost certainly an undercount of real cross-theme relevance, not evidence that only 6 companies are genuinely cross-thematic. Two already-documented signals support this:
- `ontology.py`'s own `KNOWN_CATEGORY_CONFLICTS` records Marvell Technology as a real, unresolved photonics-vs-AI-compute classification disagreement (registered single-theme photonics here; a separate seed list groups it under AI compute) and Tower Semiconductor as ai-buildout-vs-foundry. Both are single-tagged in the current registry despite the conflict being explicitly known.
- Companies like Broadcom (custom AI ASICs *and* networking silicon) and Astera Labs (AI-datacenter interconnect) are single-tagged ai-buildout even though their product lines plausibly touch photonics/interconnect adjacency — the registry's own notes for both explicitly discuss and then decline a second theme/subtheme tag, out of taxonomic caution rather than a substantive absence of cross-theme relevance.

## 4. Proposed granular hierarchical taxonomy (all five themes)

Two taxonomies already exist and are deliberately unreconciled — this proposal does not invent a third. It extends `ontology.py`'s existing, enforced, 5-theme/16-layer split (the one that actually governs the registry) into a real three-level hierarchy, and separately recommends how the dormant 13-theme YAML registry should eventually plug in (§4.3), rather than merging the two namespaces (which `THEME_REGISTRY_FOUNDATION.md` already explicitly declined to do, for good reason: the YAML registry serves a materially different purpose — autonomous classification/retrieval over free text — from the dashboard's coarse company grouping).

### 4.1 Structure: Theme → Layer → Subtheme

```
Theme (5, closed set, ontology.PRIMARY_THEMES)
  └── Layer (from ontology.SUPPLY_CHAIN_LAYERS, 16 values — a company's position in
       the value chain; a company can occupy more than one layer within a theme)
        └── Subtheme (theme-specific product/process granularity — today free text;
             recommend an enum, one closed list per theme, replacing
             data/seed/themes.json's disconnected catalog)
```

`SUPPLY_CHAIN_LAYERS` already exists and is under-used: only `compute-hardware`, `interconnect`, `memory`, and `edge-physical-ai` appear anywhere in current tracked-company data (informally, in prose notes — see §1's supply-chain-layer tally), while `application`, `ai-model`, `cloud-infrastructure`, `semiconductor-foundry`, `critical-minerals`, and `security` are defined but currently unused by any tracked entry. The recommendation is to **populate `TrackedCompany`/`Issuer.supply_chain_layers` as a real, structured field going forward** (it already exists on `Issuer`, per `src/models/issuer.py:102`, validated against `ontology.is_valid_layer()`, but is populated for zero of the 115 migrated `SEED_ISSUERS` today) rather than leaving the layer classification as unstructured prose in `notes`, which is unqueryable.

### 4.2 Proposed per-theme subtheme sets (draft, for review — not applied)

Each set below reconciles three inputs: (a) the 10 subtheme strings already in live use, (b) `data/seed/themes.json`'s existing (disconnected) subtheme catalog, (c) the "left unset, no accurate match" gaps §1 documents (e.g. REIT/real-estate, semiconductor materials, wafer metrology vs. device test). Layer tags in brackets show which `SUPPLY_CHAIN_LAYERS` value each subtheme maps to.

- **ai-buildout**: `compute-accelerators` [compute-hardware], `general-purpose-silicon` [compute-hardware] (new — covers Renesas/Tower's non-accelerator chip production, explicitly distinguished in existing notes), `foundry-fabrication` [semiconductor-foundry] (new — TSMC/GlobalFoundries/Tower's fab role, distinct from equipment), `semiconductor-equipment` [semiconductor-equipment], `semiconductor-materials` [semiconductor-materials] (new — Entegris/Shin-Etsu/SUMCO/MKS currently have no subtheme at all), `advanced-packaging` [advanced-packaging], `semiconductor-test` [semiconductor-equipment], `interconnect-switching` [interconnect], `power-cooling` [power-infrastructure, thermal-management], `datacenter-real-estate` [cloud-infrastructure] (new — resolves the REIT gap in §2), `hyperscale-demand` [application] (new — Alphabet/Amazon/Microsoft/Meta/Oracle's demand-side role, distinct from every supply-side subtheme above), `design-automation` [software-infrastructure] (new — Cadence/Synopsys currently unset)
- **memory**: `dram`, `nand-flash` (new — SanDisk/Kioxia/Western Digital currently have no subtheme despite NAND being their core product), `hbm`, `memory-modules-packaging` (from `data/seed/themes.json`, currently unused)
- **photonics**: `optical-components`, `interconnect` (electrical/protocol, per the Astera Labs precedent — kept distinct from optical `interconnect-switching`), `interconnect-switching` (optical/photonic switching fabric, per Coherent's precedent), `silicon-photonics` (from the seed catalog, currently unused), `lasers-optical-components` (from the seed catalog, currently unused — the §2 gap)
- **humanoids**: `industrial-automation`, `actuators-motors` (from the seed catalog — currently no tracked company uses it despite Harmonic Drive Systems/Nabtesco being actuator/reducer specialists with no subtheme today), `sensors-vision` (seed catalog, unused — the §2 gap), `batteries-power` (seed catalog, unused — the §2 gap), `integrators-oems` (seed catalog, unused), `software-ai-stack` (seed catalog, unused)
- **space**: `launch`, `landers-exploration` (new — ispace/Intuitive Machines currently unset), `space-manufacturing` (new — Redwire's own notes explicitly call for this, currently unset), `satellite-communications` (new — AST SpaceMobile/Planet Labs currently unset), `space-and-defense-systems` (new — Hanwha Aerospace/KAI/L3Harris currently unset), `ground-systems` (seed catalog, unused)

This is a **draft for review, not an approval request to implement** — applying it means a real registry-wide subtheme migration (115 records), which is out of scope for this audit phase (see §11).

### 4.3 Reconciling with the dormant 13-theme YAML registry

Recommend the crosswalk `THEME_REGISTRY_FOUNDATION.md` already names as a "future plug-in point" (`Issuer.theme_registry_ids`, additive, non-repurposing) be built as a **separate, later, explicitly-approved phase** — a many-to-many mapping table (5 dashboard themes ↔ 13 classification-registry themes; e.g. `ai-buildout` maps to at least `ai_infrastructure_and_semiconductors`, `advanced_packaging_memory_and_semiconductor_equipment`, and partially `data_centers_and_compute_capacity`/`power_grid_infrastructure_and_energy`), not a merge of the two vocabularies. This is named here as a dependency for future second-order Signal generation (§10) but is explicitly a non-goal of this audit (§11).

## 5. Public / private / ecosystem classification model

Today, `Issuer`/`TrackedCompany` are **100% public/listed-issuer scoped** — no field anywhere represents a private company. Recommend a new, additive `CompanyClassification` enum-like field (not a repurposing of `CoverageState`, which describes *scan status*, not *market status*):

| Classification | Definition | Registry implication |
|---|---|---|
| **Public** | Listed issuer, files with a regulator EevaResearch has (or could have) a source adapter for. | Eligible for `TrackedCompany`/`SEED_ISSUERS` — the only classification eligible for live scanning today. |
| **Public — unreachable** | Listed issuer, but on an exchange/regulator with no source adapter today (e.g. Taiwan/TWSE, most of continental Europe). | `DISCOVERY_STUBS`-equivalent: recorded, never scanned, until a new adapter exists. This is already `issuer_coverage.get_jurisdiction_gaps()`'s exact concept — formalize it as a field value rather than inferring it from `normalization_status` prose. |
| **Private** | Not listed on any public exchange (VC-backed, family-owned, PE-owned, or a subsidiary of a private parent). | Never scan-eligible by definition; relevant only as a relationship-graph node (a supplier or customer of a tracked public company) and for the private-company watchlist (§9). No filing-based Signal can ever be generated directly from a private company under the existing `signal_promotion.py` gate — only evidence *about* it, sourced from a public counterparty's own disclosure or reputable news, can ever become a Signal. |
| **Ecosystem** | A non-corporate but materially relevant entity: a government program, a consortium/standards body, an index/exchange itself, or a research institution whose actions move a theme (e.g. a CHIPS Act award, a DARPA program, an industry consortium). | Never a company-scoring or relationship-edge *subject* in the customer/supplier sense — tracked (if at all) as a distinct, smaller "ecosystem entity" list, referenced by relationship edges as a `regulator`/`government_or_regulator` counterparty (§6), not folded into the company universe. |

This model is designed to compose with the existing `entity_kind` field already on `Issuer` (`"corporate" | "subsidiary" | "fund" | "agency" | "government" | "unknown"`, added for the dormant Company Discovery Phase 2 work) rather than duplicate it: `entity_kind` describes *what kind of legal entity*, the new classification field describes *market/scan reachability*. A private-company subsidiary of a tracked public parent should carry both `entity_kind="subsidiary"` and classification `Private`, with `parent_issuer_id` pointing at the public parent.

## 6. Evidence-backed relationship types

Recommend **extending**, not replacing, the `RelationshipType` enum that already exists in `src/models/company_discovery_models.py` (`SUPPLIER`, `CUSTOMER`, `PARTNER`, `COMPETITOR`, `THEMATIC_MENTION`) — it is dormant (used only as a per-mention evidence tag, never persisted as a standing edge) but already wired into the Company Discovery Phase 2 pipeline's tests and scoring, so reusing its name and enum shape avoids a second, parallel vocabulary. Proposed full set, covering every relationship type the user specified:

| Type | Definition | Direction convention |
|---|---|---|
| `CUSTOMER` (existing) | Entity A purchases entity B's product/service as a material input. | A → B (A is the customer of B) |
| `SUPPLIER` (existing) | Entity A provides a component, material, or service that entity B incorporates. | A → B (A supplies B) — the inverse of `CUSTOMER` between the same pair |
| `FOUNDRY` (new) | Entity A fabricates silicon designed by entity B (a specific, narrower case of `SUPPLIER` — kept distinct because foundry capacity/allocation is its own, frequently-Signal-relevant event category). | A → B (A fabricates for B) |
| `MANUFACTURER` (new) | Entity A performs contract manufacturing/assembly of entity B's product (distinct from `FOUNDRY`: finished-goods or subsystem assembly, not wafer fabrication — e.g. an OSAT packaging house, a satellite-bus integrator). | A → B |
| `PARTNER` (existing) | A named, mutual commercial or technical collaboration without a clear buyer/seller direction (e.g. a joint development agreement, an MOU). | Bidirectional (A ↔ B) |
| `COMPETITOR` (existing) | Entity A and entity B compete for the same customer/contract/market segment. | Bidirectional (A ↔ B) |
| `INTEGRATOR` (new) | Entity A assembles/incorporates entity B's component into a larger system it sells (distinct from `CUSTOMER`: emphasizes the systems relationship, e.g. a robot OEM integrating a reducer maker's actuators). | A → B |
| `INFRASTRUCTURE_PROVIDER` (new) | Entity A provides physical or utility infrastructure (power, cooling, colocation, connectivity) entity B's operations depend on. | A → B (A provides to B) |
| `REGULATOR` (new) | Entity A (a government body, standards body, or exchange) has authority over entity B's operations, licensing, or compliance. | A → B; A is typically an "Ecosystem" classification, not a company (§5) |
| `DEPENDENCY` (new) | A general, evidence-backed material dependency that does not cleanly fit any of the above (e.g. a single-source critical-minerals dependency, a shared fab-node capacity constraint). The deliberately-general fallback — used only when a more specific type from this table genuinely does not apply, not as a default. | A → B |
| `THEMATIC_MENTION` (existing, retained) | Not a relationship — a co-mention in the same source text without asserted causal or commercial connection. Retained explicitly to preserve the existing evidence-tagging distinction between "this text names both companies" and "this text asserts a relationship between them" — critical for §10's no-automatic-causal-claims rule. | N/A |

## 7. Minimum metadata for a relationship edge

Every persisted relationship edge (a `CompanyRelationship` record, additive, new — no existing model persists standing company-to-company edges today, only per-mention evidence tags) must carry, at minimum:

| Field | Requirement |
|---|---|
| `subject_issuer_id` / `object_issuer_id` | Both ends of the edge, by `Issuer.issuer_id` (public) or a new private-company identifier scheme (§5) — never a free-text name, to avoid the entity-resolution ambiguity `company_discovery`'s own `entity_resolution.py` already exists to guard against. |
| `relationship_type` | One value from §6's enum. |
| `direction` | `directional` (subject→object per §6's convention) or `bidirectional` (`PARTNER`/`COMPETITOR`). |
| `product_or_process` | Free text naming the specific product/process the relationship concerns (e.g. "HBM4 supply," "3nm wafer fabrication," "reducer/actuator supply") — a relationship type alone ("SUPPLIER") is not evidence-actionable without this. |
| `source` | The exact evidence source: a filing accession number/URL, an IR press release URL, a reputable-news article URL — never "inferred" or absent. Mirrors the existing `classification_output_contract.required_fields` pattern already established in the YAML theme registry (`source_url`, `evidence_excerpt`). |
| `evidence_excerpt` | The exact quoted text the relationship claim is drawn from — reuses the existing `candidate_requirements: evidence_excerpt` convention from the theme registry YAML. |
| `date_observed` | The date the source evidence itself is dated (a filing's period, a press release's dateline) — distinct from `date_recorded`. |
| `date_recorded` | When EevaResearch's own pipeline captured this edge — for staleness/audit purposes, mirroring `Issuer.discovered_at`/`last_verified_at`. |
| `confidence` | A bounded, explainable value (reuse the existing `evidence_confidence: str` convention already on `Issuer`, or adopt `company_discovery`'s numeric composite-score pattern) — never a bare boolean. |
| `last_verified_at` | ISO 8601 date of the most recent re-confirmation that the relationship is still current — required because supplier/customer relationships lapse (contracts end, designs get re-sourced) in a way a one-time observation does not capture. |
| `status` | `active` / `lapsed` / `disputed` / `unverified` — mirrors the existing `CoverageState`/`LifecycleState` pattern of never silently deleting a record, only marking its current status. |

## 8. Scoring framework for immediate public-company additions

`src/data_access/company_discovery/scoring.py` already implements a tested, weighted, 7-factor composite scoring framework (relationship specificity 0.30, thematic fit 0.10, Core proximity 0.15, source authority 0.15, independent recurrence 0.15, recency 0.05, identifier confidence 0.10 — sums to 1.0). **Recommend adapting this existing framework rather than building a parallel one** — it was designed for the same underlying question (is this candidate company worth tracking?), just for a discovery-pipeline candidate rather than a manually-researched addition. Proposed mapping from the user's 7 requested factors onto (a) reused scoring-framework factors and (b) genuinely new factors this audit's purpose requires:

| Requested factor | Source | Notes |
|---|---|---|
| Materiality | New | How large a share of theme-relevant revenue/capex this company represents — not in the existing framework, which scores *evidence quality* about a candidate, not the candidate's own economic scale. |
| Bottleneck power | New | Degree of market concentration / substitutability (an ASML-style monopoly scores far higher than a company with five direct substitutes) — directly addresses the §2 gap-finding methodology. |
| Relationship centrality | Reuse — extends "relationship specificity" (0.30) | The existing factor already scores how specifically evidence ties a candidate to tracked companies; centrality extends it to count *how many* tracked-company relationships a candidate would have once added (using §6/§7's edge model). |
| Signalability | Reuse — extends "source authority" (0.15) + "independent recurrence" (0.15) | Whether the company's disclosures (filing cadence, IR quality, news coverage) can reliably feed the existing materiality-tier Signal pipeline — the existing framework already scores exactly this for evidence about a candidate. |
| Liquidity / market accessibility | New | Whether the company is realistically EDGAR/DART/EDINET-addressable today (a real listing, a real adapter) — directly reuses the §5 "Public / Public-unreachable" distinction as a hard gate before any score is computed, not a weighted factor (an unreachable candidate scores zero regardless of its other merits, same as `DISCOVERY_STUBS`' current structural exclusion). |
| Source availability | Reuse — "source authority" (0.15) | Already scored by the existing framework. |
| Cross-theme relevance | New | Directly extends §3's bridge-company finding — a candidate that would plausibly bridge two or more of the five themes scores higher, since bridge companies are currently only 5% of the registry and under-detected per §3. |

Recommend a combined score as: `0.5 × (adapted existing composite, factors reused above) + 0.5 × (new factors: materiality, bottleneck power, cross-theme relevance, each equally weighted)`, gated by the liquidity/market-accessibility hard filter. This weighting is a starting proposal for review, not a final calibration — real calibration requires running it against a labeled batch of already-known-good additions (e.g. the existing 2026-09-15 Tier 1 Cohort 1 GO-status batch) to sanity-check it before trusting it on novel candidates.

## 9. Research method for the ranked add list, Tier 2 backlog, and private-company watchlist

1. **Re-verify, don't re-derive, the existing 2026-09-15 candidate work first.** `COVERAGE_EXPANSION_CANDIDATES_TIER1_2026_09_15.md` (21 candidates) and `TIER1_COHORT1_TRACKED_COMPANIES_IMPLEMENTATION_DESIGN_2026_09_15.md` (10 GO-status, implementation-ready) already exist, already include TSMC and ASML, and already passed a real EDGAR/EDINET adapter readiness check. The most efficient first step of any future add-list work is confirming those candidates' identifiers/listing status are still current (they may have moved in the ~2 months since), not re-running the discovery process from scratch.
2. **Apply §8's scoring framework to any new candidate beyond that existing batch**, generated via: (a) `company_discovery`'s own dormant pipeline (`scoring.py`, `entity_resolution.py`, `extraction_rules.py`) re-pointed at real evidence sources — it already exists and is tested, just unwired to any promotion path; (b) manual research seeded from the §2 gap list (TSMC/ASML-class bottleneck absences, zero-China/Taiwan coverage, thin humanoids sensor/battery layers, thin space component-supplier layer).
3. **Tier 2 backlog**: candidates that score above a minimum bar on §8's framework but are not liquidity/accessibility-eligible today (e.g. genuine Taiwan/China/continental-Europe candidates with no source adapter) — recorded the same way `DISCOVERY_STUBS` already records jurisdiction-blocked candidates, not discarded.
4. **Private-company watchlist**: built from relationship edges (§6/§7) pointing *at* a private entity from an already-tracked public company's own disclosure (e.g. a 10-K naming a private key supplier) — never independently researched from scratch, since a private company has no regulator filing of its own to verify identity against, and inventing identifiers for one would repeat exactly the mistake `TrackedCompany`'s own docstring already warns against ("guessing... would break the same verify-against-real-data discipline the rest of this app follows"). A private company enters the watchlist only when a public counterparty's own real disclosure names it.

## 10. Rules for future second-order Signal generation

1. **No automatic causal claims.** A relationship edge (§6/§7) or a theme-level `CrossThemeRelationship` (already defined in the YAML registry, e.g. `ai_compute_drives`) describes an *asserted, evidenced* connection or a *thematic* co-occurrence pattern — neither is ever auto-converted into a causal Signal ("Company A's earnings beat implies Company B will benefit") without a human review step. This mirrors the existing, explicit, non-negotiable boundary already enforced elsewhere in this codebase: `theme_matching.py` is structurally forbidden (checked by an AST-based test) from importing anything that could construct a `CandidateSignal` or set a `PUBLISHED`/`MONITORING`/`DISMISSED` status: the same boundary must hold for any future relationship-graph-driven Signal path.
2. **Distinguish direct relationship evidence from thematic inference, explicitly, in the data model.** A second-order Signal candidate must carry a field stating which of the two produced it: `direct_relationship_evidence` (a §7 edge with a real source/excerpt naming both companies) or `thematic_inference` (two companies share a theme/layer with no direct edge between them) — the second category is strictly weaker and must never be silently presented with the same confidence as the first.
3. **Always show confidence and source provenance.** Every second-order Signal surfaced to a human reviewer must display the full §7 metadata (source, date, confidence, `last_verified_at`) of the underlying edge(s) it was built from — not just the final inference. This directly extends the theme registry's own existing `classification_output_contract.required_fields` pattern (`source_url`, `evidence_excerpt`, `confidence`, `requires_human_review`) to the relationship-graph case.
4. **The existing human-review gate is the only route to publication.** `signal_promotion.py`'s `record_review_decision()` remains the sole path to `PUBLISHED`/`MONITORING`/`DISMISSED`, unchanged — a second-order Signal is a new *candidate source*, not a new publication path.

## 11. Explicit non-goals (this audit phase)

- No company added to `tracked_companies.py`, `issuer_registry.py`, or any registry file.
- No relationship, edge, or `CompanyClassification` field implemented — §5–§7 are proposals for a future, separately-approved phase.
- No resolution of the 4 existing `KNOWN_CATEGORY_CONFLICTS` (MRVL, TSEM, the interconnect-switching/networking-interconnect naming overlap, Kioxia's ticker format) — flagged as prior art this future work must account for, not silently resolved here.
- No crosswalk built between the 5-theme dashboard taxonomy and the 13-theme YAML classification registry (§4.3) — named as future work, not built.
- No wiring of `company_discovery`'s dormant scoring/entity-resolution pipeline to any promotion path.
- No live discovery, database write, cache write, worker run, or source/adapter change of any kind.
- No new-jurisdiction source adapter (Taiwan/TWSE, China, continental Europe) designed or scoped — only the coverage gap those jurisdictions represent is documented.
- No re-verification of the 2026-09-15 candidate batch's identifiers against live sources — §9 recommends this as the next step, but performing it is out of scope here.
- No taxonomy migration applied to any of the 115 existing tracked companies' `subthemes` field.

## 12. Unresolved research questions for a follow-up phase

1. Are the 2026-09-15 Tier 1 Cohort 1 candidates' CIKs/listing status still current, and has anything material changed (delisting, M&A, ticker change) in the ~2 months since that batch was verified?
2. What is the real overlap between `data/seed/themes.json`'s existing subtheme catalog and this audit's §4.2 proposal — should the demo catalog be retired in favor of the proposal, or does something else in the UI still depend on its exact current values?
3. Should `CompanyClassification` (§5) be a new field on `Issuer` only, or does `TrackedCompany` also need it (given `TrackedCompany` is the file every live pipeline actually reads, and `Issuer` is presently a compatibility-layer-only construct with no live caller of its own)?
4. What identifier scheme should a private company use in a relationship edge (§7) when it has no regulator-issued identifier of any kind — an internal EevaResearch-minted ID, a LEI (if available), or something else?
5. Does `company_discovery`'s dormant scoring framework's 0.30 weight on "relationship specificity" already implicitly capture most of what §8 proposes calling "relationship centrality," making the two redundant rather than complementary once actually run against real data?
6. Should the private-company watchlist (§9.4) have its own lighter-weight review/promotion gate distinct from `signal_promotion.py`'s existing one, given a private company can never itself become a `PUBLISHED` Signal subject but its disclosed relationships to tracked public companies still need *some* review path?

---

## Confirmations

- **Exact files changed by this task: one.** This document (`design/COMPANY_UNIVERSE_AND_SUPPLY_CHAIN_AUDIT_PLAN_2026_09_16.md`) is newly created and untracked. No other file in the repository was modified, staged, or committed.
- No company, registry entry, model field, or database/cache record was added, changed, or written.
- No worker was run, no live discovery was performed, no source/adapter code was touched.
- No stash operation occurred.
- No staging, commit, push, PR, or merge occurred.
