# `design/` — what is in here, and how much to trust it

This directory holds two different kinds of file, and the difference matters
more than anything else on this page.

## Current documentation

| File | Role |
|---|---|
| `DECISIONS.md` | **The architectural record.** ~5,000 lines, one section per phase, append-only. The authoritative "why" for anything non-obvious in this codebase. |
| `HANDOFF.md` | Onboarding summary for a new contributor or assistant. |
| `../IMPLEMENTATION_NOTES.md` | What is built, what is partial, what is known-broken, as of a stated date. |

Those are maintained. Everything else here is not.

## Dated files are historical records

**Any filename ending in a date (`_2026_09_15`) is a snapshot of what was true
on that date. It was not updated afterwards and is not maintained.**

These documents are deliberately kept, because they are the provenance for
decisions now baked into the code — which companies entered the registry and
on what primary-source evidence, why an admission rule is shaped the way it
is, which false positives were observed live. That reasoning is expensive to
reconstruct and impossible to recover from a diff.

But they are a liability if read as current. They were written in a
house style that states present-tense facts about the repository with
confidence — "`X` deploys from branch `Y`", "the registry holds 105
companies", "no `render.yaml` is tracked" — and every one of those three
examples is now false. A September audit describing "the current pipeline"
describes September's pipeline.

**So: read them for reasoning, never for current state.** When a dated document
and the code disagree, the code is right. When a dated document and
`DECISIONS.md` disagree, `DECISIONS.md` is right.

This is not hypothetical. `IMPLEMENTATION_NOTES.md` has a standing
"Documentation drift" section because prose in this repository has repeatedly
outlived its accuracy, including a document that confidently described a
feature's "shadow mode" that had never existed.

## Undated files

A few files carry no date (`CHINA_COVERAGE_DESIGN_NOTE.md`,
`RESEARCH_THESES_ACTIVE_DORMANT.md`, the `*_DEPLOYMENT.md` runbooks). Treat
these as dated too — they simply predate the convention. Check `git log` for
when one was last touched before relying on it.

## Status of the work each document describes

A dated document does not tell you whether its work shipped. This table does,
as of **2026-11-23**. It is itself a snapshot; verify against the code.

### Shipped

| Document | Work it describes |
|---|---|
| `TIER1_COHORT1_*` (2), `TIER1_*COHORT2*` (2) | Tier 1 company cohorts. Registry has grown from 105 to 129 issuers. |
| `COVERAGE_TAXONOMY_AUDIT_2026_09_15` | The baseline audit the expansion work built on. |
| `COVERAGE_EXPANSION_CANDIDATES_TIER1_2026_09_15` | Phase 2 candidate research. |
| `COVERAGE_EXPANSION_FIRST10_REGISTRY_READINESS_2026_09_15` | Phase 3 readiness for the first ten. |
| `THEMATIC_COVERAGE_UNIVERSE_AUDIT_2026_09_15` | Coverage universe vs. thematic taxonomy. |
| `COMPANY_UNIVERSE_AND_SUPPLY_CHAIN_AUDIT_PLAN_2026_09_16` | Universe reconciliation plan. |
| `DAILY_NEWS_SOURCE_CENSUS_AND_COHORT1_DESIGN_2026_09_15` | Source census and Cohort 1 onboarding. |
| `DAILY_NEWS_COHORT1_IMPLEMENTATION_DESIGN_2026_09_15` | Cohort 1 implementation. |
| `DAILY_NEWS_IDENTIFIED_VS_MATCHED_COMPANIES_DISCOVERY_2026_09_16` | The `identified_companies` vs. `matched_companies` distinction. |
| `DAILY_NEWS_COMPANY_ATTRIBUTION_IMPLEMENTATION_READINESS_2026_09_16` | Attribution change readiness. |
| `DAILY_NEWS_HIGH_SIGNALS_WATCHLIST_*` (2) | The High Signals and Watchlist tier views. |
| `CURRENT_SIGNALS_POLICY_AND_GAP_INVENTORY_2026_09_15` | Full admission/materiality policy inventory. |
| `SIGNALS_ADMISSION_MATERIALITY_CALIBRATION_2026_09_15` | Admission calibration. |
| `SIGNALS_PRECISION_FOLLOWUP_RETAIL_BOILERPLATE_GUIDANCE_2026_09_16` | Retail/boilerplate/guidance precision follow-up. |

### Built but deliberately switched off

| Document | Note |
|---|---|
| `AUTONOMOUS_EVIDENCE_FIRST_RESEARCH_AGENT_DESIGN_2026_09_17` | The full agent design: a ten-tool read-only MCP surface, one budgeted SDK session per filing, one publication policy. |
| `AUTONOMOUS_RESEARCH_AGENT_PRODUCTION_READINESS_2026_09_17` | **Read this before re-enabling the agent.** It lists what must be true first. The worker's Render service is suspended and publication is kill-switched. |

### Not shipped — open proposals and verification records

| Document | Note |
|---|---|
| `EVIDENCE_BACKED_SUPPLY_CHAIN_RELATIONSHIPS_DISCOVERY_2026_09_16` | Architecture discovery; 22 candidate relationships. |
| `SUPPLY_CHAIN_RELATIONSHIPS_V1_EVIDENCE_VALIDATION_2026_09_16` | The 22 checked against primary sources; 12 survive. |
| `SUPPLY_CHAIN_RELATIONSHIPS_V1_YAML_IMPLEMENTATION_READINESS_2026_09_16` | Turns those 12 into an implementable seed. |
| `RESEARCH_THESES_ACTIVE_DORMANT` | Active/Dormant indicator for theses. Proposal only; no `theme_workspace.py` change exists. |
| `POST_MERGE_SIGNALS_PRECISION_AUDIT_2026_09_15` | Proposes a "concrete-event admission floor". Not implemented. |
| `POST_MERGE_SIGNALS_LIVE_CARD_AUDIT_2026_09_16` | Five live fixtures with per-fixture verdicts. Names a confirmed live false positive as its most severe finding. |
| `POST_MERGE_DAILY_NEWS_SIGNAL_PERFORMANCE_AUDIT_2026_09_16` | Post-#60/#61 audit; the base the precision follow-up builds on. |
| `POST_MERGE_PR63_LIVE_VALIDATION_2026_09_16` | Confirms the hobbyist filter and tier filter work post-merge. |
| `DAILY_NEWS_COHORT1_SCOPE_GUARD_FAILURE_DISPOSITION_2026_09_15` | Why the Cohort 1 source work tripped the diff-scope guards. The record behind `rescue/daily-news-cohort1-sources` having no pull request. |

The four `POST_MERGE_*` audits look like disposable "I checked after merging"
receipts and are not. They carry named live false positives and design
proposals nobody has acted on. If you are looking for undone work, start
there.

## Adding a document

Date the filename. State up front what you did and did not change — every
document here opens with an explicit read-only or scope declaration, which is
how a reader knows whether a design note also touched the database. Record the
`main` commit you verified against. Then, if the work changes architecture, add
the decision to `DECISIONS.md`, because that is the file anyone will actually
read later.
