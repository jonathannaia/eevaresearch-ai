# Autonomous Evidence-First Research Agent — Design

Status: **design only**. No code, dependency, MCP server, schema, model, config, database, worker, ingestion, backfill, or discovery change was made or run while producing this report. See §17 for exact git/verification state.

Scope discipline: one agent, one workflow, one publication policy. Not a multi-agent network, not a general autonomy platform, not a chat assistant. Every claim about "what exists today" below was verified directly against this repository (main branch, HEAD `b240def`) — file paths and symbol names are real, not assumed. Where something the task asks to "reuse" does not actually exist on `main`, that is stated explicitly rather than silently assumed into existence.

---

## 1. Executive recommendation

Build **one** autonomous pipeline — trigger → resolve → classify → suppress → retrieve → compare → propose → validate → synthesize → decide → route → audit — implemented as three cooperating layers that already have direct analogs in this codebase:

1. **A narrow, read-only MCP tool surface** (10 tools, §6) that is the *only* way the agent touches EevaResearch data. It wraps existing adapter/repository functions (DART/EDGAR/EDINET scan+document services, `tracked_companies`/`issuer_registry`, `theme_evidence`, `research_store`) — it does not reimplement them.
2. **An Anthropic Agent SDK session** (§7) running a single, short, budgeted tool-use loop per filing, with SDK-level pre-tool/post-tool hooks enforcing the tool allowlist and per-call bounds, and a structured-output contract for the final claim proposal.
3. **Deterministic Eeva backend services** (§8) that own state, audit, auth, retries, and — critically — the **publication decision**. The agent never writes production data and never decides eligibility; it calls `request_publication_decision`, a backend policy evaluation that either publishes, files a verified draft, or routes to review/insufficient-evidence/not-material/failed-retrieval. This mirrors the *existing* separation in this codebase between detection (`radar_pipeline.py`, agent-free) and publication (`review_actions.record_review_decision` + `signal_promotion.is_eligible_for_signal`, human-only today) — the agent slots into the second half of that existing boundary, it does not replace it.

The state machine adds exactly **two** new `CandidateStatus` values (`VERIFIED_DRAFT`, `INSUFFICIENT_EVIDENCE`) and **one** new provenance field (`published_by`) on `CandidateSignal`. It reuses `NOT_MATERIAL`, `RETRIEVAL_FAILED`, `NEEDS_REVIEW`, and `PUBLISHED` verbatim. No other model changes. Public rendering is a **new, narrow, additive** surface (a "verified update" card, distinct from the existing analytical `Signal` card) rather than a repurposing of the existing Signal Board, because the two content types are semantically different (raw reported fact vs. an investment thesis with direction/strength/interpretation) — see §9 for why forcing one into the other would be unsafe.

This is a design document. Nothing here is built. §12 gives an exact, minimal, phased file list for a future implementation task.

---

## 2. Verified current architecture and exact reuse map

Everything below was confirmed by direct repository inspection (paths exact). Where a "prior report" concept turned out not to exist on `main`, that is flagged.

### 2.1 Filing-source adapters (per-source, structurally parallel)

| Concern | DART (`src/data_access/dart/`) | SEC EDGAR (`src/data_access/edgar/`) | EDINET (`src/data_access/edinet/`) |
|---|---|---|---|
| Rules/classification | `dart_rules.py` — `KOREAN_KEYWORD_LEXICON`, `evaluate_report_name()`, `treasury_stock_activity` category (added this session) | `edgar_rules.py` — `FORM_TYPE_CATEGORIES`, `EIGHT_K_ITEM_CATEGORIES`, `evaluate_form_type()`, `evaluate_six_k()`, `refine_8k_evaluation()` | `edinet_rules.py` — `evaluate_document()`, `merge_evaluations()`, code-triplet routing |
| Scan/discovery | `scan_service.py::scan()` | `scan_service.py::scan()` | `scan_service.py::scan()` (day-by-day; no per-company feed) |
| Document retrieval | `document_service.py::get_or_fetch_excerpt()` | same shape | same shape, ZIP-aware (`document_extractor.py::_extract_from_zip()`) |
| Materiality gates | `ownership_materiality.py`, `equity_transaction_materiality.py`, `low_value_filing_rules.py` (this session) | — | — |
| Pipeline orchestration | `radar_pipeline.py::process_candidate()`/`run_pipeline()` | `edgar_pipeline.py` (same function names) | `edinet_pipeline.py` (same function names, + `backfill_candidate_from_existing_event()`) |
| Client/errors | `client.py`, `errors.py::DartApiError` etc. | `client.py::EdgarClient`, `errors.py::EdgarError` hierarchy | `client.py::EdinetClient`, `errors.py` |
| ID resolution | `corp_code_resolver.py` | `cik_resolver.py::resolve_and_cache()` | `edinet_code_resolver.py::resolve_and_cache()` |
| Retry policy | `retry_policy.py` — **DART-only**, not shared | none dedicated | none dedicated |

**Reuse decision**: the agent's `search_filing_metadata`/`get_filing_evidence_excerpt`/`retrieve_filing_table_or_locator` MCP tools call these three adapters' existing `scan_service`/`document_service`/`document_extractor` functions directly (read paths only — never `radar_pipeline.process_candidate`'s write side). One tool implementation per source behind a common typed interface; no fourth "unified adapter" is introduced (that would be new infrastructure, not reuse).

### 2.2 Issuer/tracked-company registry

- `src/config/tracked_companies.py` — the long-standing `TrackedCompany` registry, `get_tracked_companies_for_source()`.
- `src/models/issuer.py` — `Issuer` (frozen), `Issuer.identifiers: dict[str, str]` (open `{source_name: native_id}` map), `CoverageState`, `LifecycleState`.
- `src/config/issuer_registry.py` — `get_seed_issuers()`, `get_discovery_stubs()`, `get_all_issuers()`, `tracked_companies_from_issuer_registry()` (compatibility bridge back to `TrackedCompany`).
- `src/logic/issuer_coverage.py` — `filter_seed_issuers()`, `get_coverage_summary()`, `get_ambiguous_stub_labels()`.

There is **no single cross-source entity-resolution module**; resolution is per-adapter (`cik_resolver.py`, `corp_code_resolver.py`, `edinet_code_resolver.py`), each keyed onto `Issuer.identifiers`. `resolve_tracked_issuer` (§6) wraps this existing per-source pattern — it does not invent a new resolver.

### 2.3 Theme ontology

- `src/models/theme_registry.py` — `Theme`, `ThemeRegistry`, `Geography`, `MatchingPolicy`.
- `src/models/theme_research.py` — `ResearchTheme`, `ThemeEvidenceItem`, `ThemeCompanyMapEntry`, `CompanyRole`, `EvidenceDirection`.
- `src/config/ontology.py` — `PRIMARY_THEMES`, `SUPPLY_CHAIN_LAYERS`, `is_valid_theme()`, `is_valid_layer()`, `KNOWN_CATEGORY_CONFLICTS`. (There is no separate `theme_validation.py`/`theme_matching_rules.py` file — validation lives here.)
- `backend_factory.get_theme_repository()` / `get_theme_curator_repository()` / `get_theme_matching_repository()`.

The agent never writes a theme, a layer, or a company-role mapping. `get_validated_relationship_context` (§6) is **read-only** against this ontology plus the relationship registry in §2.6.

### 2.4 `CandidateStatus` and the candidate-signal lifecycle

`src/models/models.py`: `CandidateStatus(str, Enum)` — `NEW_FILING_EVENT, CANDIDATE_DETECTED, QUEUED_FOR_PROCESSING, RETRIEVAL_IN_PROGRESS, EXTRACTION_PENDING, EXTRACTED, TRANSLATION_PENDING, TRANSLATED, NEEDS_REVIEW, PROCESSING_DEFERRED, PARSE_FAILED, RETRIEVAL_FAILED, TRANSLATION_UNAVAILABLE, PUBLISHED, DISMISSED, NOT_MATERIAL, MONITORING`. `CandidateSignal` (full field list confirmed): `id, filing: FilingEvent, matched_rules, confidence, status, extraction_state, translation_state, excerpt_quality, excerpt_original, title_translation, excerpt_translation, reviewed_at, reviewed_note, state_history: list[StateTransition], materiality_assessment, excerpt_supplemental, excerpt_retrieved_at, flag_reason: FlagReason | None, evidence_location: EvidenceLocation | None, evidence_source_member: str | None`. `StateTransition(status, at, detail)` is append-only — the same immutable-history pattern is independently repeated for Daily News (`NewsStateTransition`) and Company Discovery (`CandidateStateTransition`). §5 builds the agent's state machine as an *extension* of this exact model, not a parallel one.

### 2.5 DART low-value filing suppression (this session's own work, already on `main`)

- `src/data_access/dart/dart_rules.py` — `treasury_stock_activity` category, split out of `financing` so a routine treasury-share transaction is never conflated with a genuine capital raise.
- `src/data_access/dart/equity_transaction_materiality.py` — `assess_equity_transaction_materiality()`, mirrors `ownership_materiality.py`'s marker-then-threshold shape; escape hatches: `MATERIAL_OWNERSHIP_MARKERS`, `FOUNDER_OR_CONTROLLING_HOLDER_MARKERS`.
- `src/data_access/dart/low_value_filing_rules.py` — `matched_rules_are_low_value_only()` (candidate-side), `is_low_value_filing_title()` (title-only, for surfaces with no excerpt).
- Applied at: `radar_pipeline.process_candidate()` (excerpt-based gate → `NOT_MATERIAL`), `src/logic/theme_evidence.py::recent_filing_evidence()` (title-only gate), `src/ui/pages/radar_inbox.py::_is_low_value_suppressed()` (default-query suppression).

**Reuse decision**: the agent's classification/suppression step calls `low_value_filing_rules`/`equity_transaction_materiality`/`ownership_materiality` directly, inside the MCP layer (§6, `search_filing_metadata`) and again inside deterministic validation (§8) — never inside agent-generated reasoning. This is the exact precedent for "deterministic, not prompt-only" suppression the task requires generally.

### 2.6 Evidence-packet models, research cases, and the YAML relationship cohort

- **Evidence packet** (`src/models/models.py`): `FlagReason`, `LocationKind` (`UNAVAILABLE/PAGE/SECTION/TABLE/PARAGRAPH`), `EvidenceLocation`, `build_flag_reason()`, `record_excerpt()` (first-write-only contract). Design record: `design/DECISIONS.md` (there is no separate "Phase 1/2" doc; the phase names live only in test-file docstrings, e.g. `tests/test_evidence_packet_phase1.py`).
- **Research cases** (`src/models/research_case.py`, a dedicated module — not `models.py`): `ResearchCase(id, trigger_source_type, trigger_source_id, trigger_source_name, trigger_summary, title, research_question, status, created_at, version)`, `ResearchEvidenceItem(id, case_id, source_type, source_id, source_url, source_publisher_or_system, source_date, retrieved_at, excerpt_original, original_language, added_at, excerpt_translated, translation_provider)`, plus `RelationshipAssertion`/`DependencyAssertion`. Persistence: `src/data_access/research_store.py` (`build_case_id()`, `append_research_case_bundle()` — atomic multi-file write). Validation: `src/logic/research_case_validation.py::validate_research_case_bundle()`.
- **`trigger_source_type`/`trigger_source_id` are already a generic, opaque reference** — `research_case.py` never imports a Radar/Daily-News type. This is exactly the seam the agent's `save_private_research_packet` (§6) writes through: a new `trigger_source_type="autonomous_agent"` value, no model change required.

**YAML-backed relationship cohort — verification correction.** The task describes this as an existing pattern to reuse ("the YAML-backed evidence/relationship proof cohort and its immutable-evidence/current-edge pattern"). Direct verification found it **does not exist on `main`**. It exists only on the unmerged branch `feat/supply-chain-relationship-seed-v1` (3 commits ahead of `main`'s `91b190e`, not merged): `src/models/relationship_models.py` (`RelationshipEvidence` — immutable citations — vs. `RelationshipEdge` — a normalized, current status/confidence *derived from* linked evidence, never freely edited; `RelationshipStatus = ACTIVE|LAPSED|DISPUTED|UNVERIFIED|SUPERSEDED|HISTORICAL`), `src/config/relationship_seed_loader.py` (two-entry-point fail-closed pattern: `load_relationship_registry()`/`load_relationship_registry_or_none()`), `config/eevaresearch_relationship_seed.yaml` (12 evidence records / 12 edges, 18 issuers). `RelationshipType` on that branch is extended to 11 values (5 original + `FOUNDRY/MANUFACTURER/INTEGRATOR/INFRASTRUCTURE_PROVIDER/REGULATOR/DEPENDENCY`); on `main` today `RelationshipType` (`src/models/company_discovery_models.py`) still has only the original 5 (`SUPPLIER/CUSTOMER/PARTNER/COMPETITOR/THEMATIC_MENTION`).

This design treats the branch's model as the **intended reuse target** — its immutable-evidence/current-edge separation is exactly the shape `get_validated_relationship_context` (§6) needs, and Example 3/4/5 in §10 depend on it. **This is a real, load-bearing dependency of this design on a currently-unmerged branch**, called out explicitly so it is not mistaken for shipped `main` state; §14 lists it as a risk and §15 as a phase-0 prerequisite (merge or re-verify before implementation begins).

### 2.7 Company Discovery (context, not a build target)

`src/data_access/company_discovery/` (`candidate_pipeline.py`, `entity_resolution.py`, `extraction_rules.py`, `company_discovery_backend.py`, `scoring.py`) — fully dormant on `main`, gated by `Settings.company_discovery_live_enabled`/`company_discovery_admin_enabled`, and test-enforced dormant by `tests/test_company_discovery_scope_guard.py` (AST-based: no network I/O, no write-capable source-module import, no public-page import). **The new agent must never import from this package** and must never widen its dormancy — it is a separate, unrelated subsystem that happens to share `RelationshipType`/`CandidateEvidence` vocabulary with §2.6's branch. §8.4 states this as a hard boundary.

### 2.8 Daily News, Radar Inbox, Research Theses — current publication-adjacent surfaces

- `src/logic/signal_promotion.py::is_eligible_for_signal()` — **the only existing "what makes something public" gate in the whole codebase**: `_ELIGIBLE_STATUSES = frozenset({CandidateStatus.PUBLISHED})`, an explicit allowlist (not a denylist), documented as deliberate so a future status is excluded by default.
- `src/logic/review_actions.py::record_review_decision()` — the only existing function that *sets* `PUBLISHED`/`MONITORING`/`DISMISSED` (`_PERMITTED_REVIEWER_STATUSES`), with optimistic-concurrency `expected_version` and full JSON/SQLite/Postgres backend parity.
- **Important, verified gap**: `src/ui/pages/radar_inbox.py` is currently **strictly read-only** — the Publish/Monitor/Exclude UI actions that would have called `record_review_decision()` were removed in an earlier "reader-facing data-integrity pass." `record_review_decision()` and `is_eligible_for_signal()` still exist and are presumably still tested, but **nothing in the shipped UI calls the publish path today**. This is the exact mechanism §5/§8 reuse for the human-approval side of `REVIEW_REQUIRED`/`VERIFIED_DRAFT` — it needs a caller (an admin action), not new logic.
- `src/ui/pages/research_cases.py`, `src/ui/pages/themes_research.py` (Research Theses), `src/data_access/daily_news/dart_filing_candidate_adapter.py` (dormant shadow adapter, category-filtered) — read/rendering precedent, not writable by the agent.

### 2.9 Infrastructure precedent (auth, jobs, logging, tests)

- **Auth**: `app.py` — mandatory Google OAuth (`st.login("google")`/`st.user`), layered with `EDGE_PRIVATE_BETA_ALLOWED_EMAILS`; `src/ui/ui.py::is_admin()` — `EEVA_ADMIN_EMAILS`, fail-closed, cosmetic-only (real authorization is per-page). **No machine-to-machine/service-account/API-key inbound-auth pattern exists anywhere today** — all existing `*_API_KEY` settings (`dart_api_key`, `translation_api_key`, `edgar_user_agent`, `edinet_subscription_key`, `r2_access_key_id/secret`) are *outbound* credentials to third parties, never inbound. The agent needs a **new** inbound service-credential concept (§8.3) — this is real new surface area, called out honestly rather than glossed as "reuse."
- **Jobs**: `scripts/radar_worker.py`/`daily_news_worker.py`/`company_discovery_worker.py` — standalone always-on loops, each behind its own `EDGE_*_LIVE_SCAN_ENABLED` switch, default off. **No cron/scheduled-GHA exists** — `.github/workflows/manual-scan.yml` is `workflow_dispatch` only. Retry precedent: `src/data_access/dart/retry_policy.py` (DART-only today: `RETRY_COOLDOWN_SECONDS=60`, `MAX_RETRY_ATTEMPTS=3`, `AUTOMATIC_RETRY_MAX_PER_TICK=2`).
- **Logging**: no `logging` module usage anywhere in `src/`; error reporting is `print()` plus typed exception hierarchies plus status enums. **No audit-log model/table exists anywhere.** The closest real precedent is the append-only `state_history: list[StateTransition]` pattern, repeated three times (Radar/Daily News/Company Discovery) — §8.2 extends this pattern for the agent rather than inventing a new logging subsystem.
- **State backends**: `src/data_access/state_db/*` and `src/data_access/postgres_state_db/*`, near-mirror repository sets; schema versioning in `postgres_state_db/schema.py` (`CURRENT_SCHEMA_VERSION`, `_MIGRATIONS` tuple), tested against a disposable local Postgres in `tests/test_state_db_postgres_schema.py`.
- **Tests**: `tests/apptest_pages/*.py` — one thin `AppTest` harness file per page. Git-diff-based "scope guard" tests are a repo-wide, repeated convention, not unique to Radar (`tests/test_company_discovery_scope_guard.py`, `tests/test_daily_news_scope_guard.py`, `tests/test_ui_audit_phase_r1.py`/`_t1.py`, `tests/test_research_lead_selection.py::test_proof23_scope_guard_only_approved_files_changed`). §12's test plan follows this exact convention.

---

## 3. System/context diagram

```
                    ┌──────────────────────────────────────────────────────────┐
                    │              Eeva-owned backend services                  │
                    │  (job scheduling · state · audit · auth · publish svc)    │
                    │                                                            │
   trigger ────────▶│  Orchestrator (extends radar_worker.py-style loop)        │
  (new filing        │      │                                                    │
   scanned by an      │      ▼                                                    │
   EXISTING           │  Agent SDK session (one per filing, budgeted)            │
   scan_service)       │      │  tool calls only, via SDK hooks                   │
                    │      ▼                                                    │
                    │  ┌────────────────────────────────────────────┐          │
                    │  │  MCP tool surface (read-only, typed)         │          │
                    │  │  resolve_tracked_issuer                      │          │
                    │  │  search_filing_metadata                      │──┐       │
                    │  │  get_filing_evidence_excerpt                 │  │       │
                    │  │  retrieve_filing_table_or_locator            │  │ wraps │
                    │  │  compare_with_prior_filing                   │  │ real  │
                    │  │  search_validated_evidence                   │  │ repo  │
                    │  │  get_validated_relationship_context          │  │ funcs │
                    │  │  search_approved_official_sources            │  │ (§2)  │
                    │  └────────────────────────────────────────────┘  │       │
                    │      │ (structured claim proposal, no writes)     │       │
                    │      ▼                                            │       │
                    │  save_private_research_packet  ───────────────────┘       │
                    │      │ (writes ResearchCase-shaped bundle, private)       │
                    │      ▼                                                    │
                    │  request_publication_decision (backend policy service,   │
                    │      NOT the agent) ── deterministic validators (§8) ──┐  │
                    │      │                                                 │  │
                    │      ▼                                                 ▼  │
                    │  AUTO_PUBLISHED / VERIFIED_DRAFT / REVIEW_REQUIRED /       │
                    │  INSUFFICIENT_EVIDENCE / NOT_MATERIAL / FAILED_RETRIEVAL   │
                    │      │                                                    │
                    │      ▼                                                    │
                    │  immutable audit event (append-only, §8.2)                │
                    └──────────────────────────────────────────────────────────┘
                                   │ (AUTO_PUBLISHED / human-approved VERIFIED_DRAFT only)
                                   ▼
                    New "Verified Updates" surface (§9) — with_chrome() shell,
                    existing sidebar/CSS tokens/auth — NOT the Signal Board.
```

Data never leaves EevaResearch: the agent's outbound calls are confined to the MCP tools above, which themselves call only already-approved sources (SEC EDGAR/DART/EDINET APIs, issuer IR pages on an explicit allowlist). No email/social/webhook tool exists in the tool set (§4, §6).

---

## 4. End-to-end autonomous workflow

Restating the task's required pipeline with the exact real function/module each step calls:

| Step | What happens | Reuses |
|---|---|---|
| 1. Trigger | An existing scan tick (extends `radar_worker.py`'s per-provider loop) detects a new `FilingEvent` via `scan_service.scan()`, exactly as today — **no new ingestion path**. | `dart/edgar/edinet::scan_service.scan()` |
| 2. Source/issuer resolution | `resolve_tracked_issuer` MCP tool resolves the filing's native ID to a real `TrackedCompany`/`Issuer`. Unresolvable → `FAILED_RETRIEVAL` (never guessed). | `tracked_companies.py`, `issuer_registry.py`, per-source ID resolvers |
| 3. Relevance classification | `search_filing_metadata` returns `dart_rules.evaluate_report_name()` / `edgar_rules.evaluate_form_type()` / `edinet_rules.evaluate_document()` output verbatim — same keyword/form-type lexicon as today, not re-derived by the model. | `dart_rules.py`/`edgar_rules.py`/`edinet_rules.py` |
| 4. Low-value suppression | Same call applies `low_value_filing_rules.matched_rules_are_low_value_only()` / `is_low_value_filing_title()` and, once an excerpt exists, `equity_transaction_materiality`/`ownership_materiality`. A suppressed filing terminates here at `NOT_MATERIAL` — **the agent's model never sees its content** (§8.1: this check runs before any excerpt is released to the session). | `low_value_filing_rules.py`, `equity_transaction_materiality.py`, `ownership_materiality.py` |
| 5. Bounded evidence retrieval | `get_filing_evidence_excerpt`/`retrieve_filing_table_or_locator` return a capped excerpt (§6 bounds) via `document_service.get_or_fetch_excerpt()`/`document_extractor.extract_excerpt()` — never the raw document, never unbounded text. | `document_service.py`, `document_extractor.py` |
| 6. Prior-evidence comparison | `compare_with_prior_filing` wraps the existing prior-disclosure comparison logic (`tests/test_prior_disclosure_comparison.py` names the real module under test — comparison repository + `compare_matched_rules()`-style diff) to surface newer/superseding filings before any claim is drafted. | prior-disclosure comparison repository (existing) |
| 7. Structured claim proposal | The agent (inside its SDK session) emits a structured JSON claim list — never free prose — each claim carrying an `evidence_ids: list[str]` field pointing only at IDs the MCP tools actually returned this session. | Agent SDK structured output (§7) |
| 8. Deterministic claim/evidence validation | A backend validator (not the model) re-resolves every `evidence_id`, checks completeness (source URL/doc ID, date, excerpt/locator, tier, entity, freshness), checks contradiction/staleness against §2.6's relationship-edge model and `compare_with_prior_filing`'s output. | §8.1 validators |
| 9. Synthesis constrained to validated claims | Any claim that failed step 8 is dropped from the draft, not "softened" — a draft with zero surviving claims routes to `INSUFFICIENT_EVIDENCE`. | — |
| 10. Publication-policy decision | `request_publication_decision` (backend service, §6) applies the eligibility matrix in §5.2 — the agent has already lost write leverage by this point; it can only *ask*. | `signal_promotion.py`-style allowlist logic, extended |
| 11. Automatic routing | `AUTO_PUBLISHED` → new "Verified Updates" surface (§9); `VERIFIED_DRAFT`/`REVIEW_REQUIRED` → admin queue reusing `review_actions.record_review_decision()`; `NOT_MATERIAL`/`INSUFFICIENT_EVIDENCE`/`FAILED_RETRIEVAL` → retained, never rendered publicly. | `review_actions.py` |
| 12. Audit trail | Every step above appends one immutable event (§8.2) keyed to the same `candidate_id`/`case_id` — reproducible end to end. | extends `StateTransition` pattern |

---

## 5. State machine and publication-policy matrix

### 5.1 Terminal states — reuse mapping

| Task's required state | Maps to | Change required | Why |
|---|---|---|---|
| `NOT_MATERIAL` | `CandidateStatus.NOT_MATERIAL` | **none** | Already exists, already produced by the exact suppression gates in §2.5. |
| `FAILED_RETRIEVAL` | `CandidateStatus.RETRIEVAL_FAILED` | **none** | Already exists, already means "we could not fetch/parse the source." `PARSE_FAILED` is a closely related existing sibling in the same bucket. |
| `REVIEW_REQUIRED` | `CandidateStatus.NEEDS_REVIEW` | **none** | Already exists, already means "detected, not yet human-decided." The agent populates it with a structured draft attached (via `save_private_research_packet`) rather than leaving it bare — a data addition, not a status addition. |
| `AUTO_PUBLISHED` | `CandidateStatus.PUBLISHED` **+ new field** `published_by: Literal["human_reviewer","autonomous_agent"] = "human_reviewer"` on `CandidateSignal` | **one additive field, default-backward-compatible** | `signal_promotion.is_eligible_for_signal()`'s existing check (`status == PUBLISHED`) needs zero changes — it already means "live and public" regardless of who set it. A second, parallel status would fork that logic for no safety benefit; a provenance field preserves the single source of truth for "is this public" while keeping the human/agent distinction for audit, rollback, and any future differentiated rendering. |
| `VERIFIED_DRAFT` | **new** `CandidateStatus.VERIFIED_DRAFT` | **one additive enum value** | No existing state means "passed every deterministic evidence-completeness/contradiction check, but withheld from auto-publish by policy (not by failure)." `NEEDS_REVIEW` cannot safely stand in for this: it carries no guarantee that anything was checked, and reusing it here would either overstate confidence in ordinary unreviewed candidates or understate the verification this state actually represents — both are honesty failures against this product's own evidentiary contract (task's required contract statement). |
| `INSUFFICIENT_EVIDENCE` | **new** `CandidateStatus.INSUFFICIENT_EVIDENCE` | **one additive enum value** | Distinct from `PARSE_FAILED` (mechanical extraction failure — no text was ever obtained) and from `NEEDS_REVIEW` (a draft exists and looks plausible, a human should look). This state means text *was* obtained and read, but claim-to-evidence validation could not resolve enough evidence to support any claim — a different failure mode that a human reviewer should be able to distinguish from "the agent found something interesting" at a glance. |

Net model change: **2 new enum values, 1 new optional field, 0 new tables, 0 changes to any existing enum value or field.** `CandidateSignal.state_history` (already a list) is the audit trail for all six — no separate audit table is required at the model layer (see §8.2 for why a *separate, immutable, backend-owned* audit log is still added, distinct from this per-candidate history).

### 5.2 Publication-policy matrix

Deterministic backend logic (`request_publication_decision`, §6), never the agent, evaluates this table in order; first failing row wins.

| # | Condition | Pass → | Fail → |
|---|---|---|---|
| 1 | Source is SEC EDGAR/DART/EDINET filing, issuer IR release, or an entry in `APPROVED_OFFICIAL_SOURCE_ALLOWLIST` (§6, `search_approved_official_sources`) | continue | `REVIEW_REQUIRED` (unknown-source content is never silently dropped — a human decides once, and the allowlist can be extended) |
| 2 | Issuer resolves to `tracked_companies`/`issuer_registry` | continue | `FAILED_RETRIEVAL` if resolution itself errored; `REVIEW_REQUIRED` if it resolved to an ambiguous/untracked entity |
| 3 | Passes §2.5 low-value suppression | continue | `NOT_MATERIAL` |
| 4 | Structured output validates against the JSON schema (§9.1) | continue | `INSUFFICIENT_EVIDENCE` (malformed output is treated as "could not produce a valid claim," not retried with a looser schema) |
| 5 | Content classifies as direct reported fact (rule-based classifier over the claim's own structured `claim_type` field — never free-text sentiment) — not rumor/recommendation/price-target/causal-inference/new-relationship-edge | continue | `REVIEW_REQUIRED` |
| 6 | Every claim has ≥1 `evidence_id` | continue | `INSUFFICIENT_EVIDENCE` |
| 7 | Every referenced evidence record has: stable source URL/doc/accession ID, source date, exact excerpt/table locator, source tier, resolved entity, confidence/status | continue | `INSUFFICIENT_EVIDENCE` |
| 8 | No unresolved newer/stale/superseded/disputed/contradictory evidence (via `compare_with_prior_filing` + §2.6 relationship-edge status) | continue | `REVIEW_REQUIRED` |
| 9 | Duplicate/idempotency check (content hash + evidence-ID set against prior published/draft records) | continue | terminate silently (no new terminal state — this is "already handled," logged, not surfaced as a new failure) |
| 10 | Category is within the auto-publish content allowlist (§9) — no relationship-edge claim, no taxonomy change, no second-order inference | `AUTO_PUBLISHED` | `VERIFIED_DRAFT` (everything else about the draft already passed 1–9; only the category itself requires a human glance) |

Rows 1–9 all passing but row 10 failing is the *only* path to `VERIFIED_DRAFT` — this is what makes `VERIFIED_DRAFT` meaningfully different from `REVIEW_REQUIRED` (§5.1): every fact and every piece of evidence is already fully verified, only the publication category itself is gated.

### 5.3 Persistence, retry, and render semantics

| State | Persistence | Retry behavior | Public render |
|---|---|---|---|
| `AUTO_PUBLISHED` | `CandidateSignal.status=PUBLISHED, published_by="autonomous_agent"`; `ResearchEvidenceItem`s for every cited evidence ID (immutable, `research_store.append_evidence_item()`) | none — terminal | Yes — new "Verified Updates" card (§9), `VERIFIED FILING FACT`/`VERIFIED COMPANY ANNOUNCEMENT` label |
| `VERIFIED_DRAFT` | Same as above minus the status; private research packet saved via `save_private_research_packet` | none — awaits human action via `review_actions.record_review_decision()` (existing function, needs a caller — an admin queue action, §12) | No — internal only, until a human sets `PUBLISHED` |
| `REVIEW_REQUIRED` | `CandidateSignal.status=NEEDS_REVIEW`; packet saved as above | none — human triage, same as any ordinary Radar candidate today | No |
| `INSUFFICIENT_EVIDENCE` | `CandidateSignal.status=INSUFFICIENT_EVIDENCE`; raw retrieved excerpt retained for audit even though no claim survived | one bounded automatic retry only if the *cause* was a transient retrieval gap (mirrors `retry_policy.py`'s existing `RETRIEVAL_FAILED`-only automatic-retry scope); never retried for "the model produced no claims" | No |
| `NOT_MATERIAL` | `CandidateSignal.status=NOT_MATERIAL`; raw `FilingEvent` always retained (this session's DART suppression work already guarantees this) | none — deterministic, re-running produces the identical result | No — and, per §2.5, actively suppressed from theme evidence / Radar Inbox default view / research-lead selection |
| `FAILED_RETRIEVAL` | `CandidateSignal.status=RETRIEVAL_FAILED` | governed by existing `retry_policy.py` semantics, extended (not replaced) to the agent's own retrieval calls: bounded attempts, cooldown, never infinite | No |

---

## 6. Minimum MCP tool contracts

All ten tools are **read-only** except the last two, which are narrow, policy-mediated write *requests* — never direct writes. Every tool runs server-side (Eeva's own MCP server process), never in the agent's own process, so "the agent cannot bypass a tool's own validation" is an architectural fact, not a prompt instruction.

**Common contract for every tool**: caller authorization is always "this agent session only, scoped to one filing/source, one active `case_id`, time-boxed to the session TTL (§7.3)" — no tool accepts a broader credential. Every tool call and response is written to the audit event stream (§8.2) with a reproducibility hash of its exact inputs.

### `resolve_tracked_issuer`
- **Accepted inputs**: `source_name` (enum: `"OpenDART / DART" | "SEC EDGAR" | "EDINET"`), `native_id` (string, source-specific — CIK/corp_code/EDINET code), OR `name_hint` (string, only used as a fallback lookup key, never trusted alone).
- **Bounded outputs**: `{issuer_id, tracked_company_name, exchange, themes: [...], resolution_confidence: "exact"|"ambiguous"|"unresolved"}` — at most one match returned; `"ambiguous"` never guesses, it names the candidates and returns unresolved for policy purposes.
- **Source of truth**: `tracked_companies.py` + `issuer_registry.py` + the relevant per-source ID resolver (§2.2). Never a fuzzy/LLM-side match.
- **Validation**: input `native_id` format-checked per source before any lookup (fail closed on malformed input, not "best effort").
- **Error behavior**: unresolved → typed `IssuerNotTracked` result (not an exception the model has to interpret) — this is what routes the pipeline to `FAILED_RETRIEVAL`/`REVIEW_REQUIRED` per §5.2 row 2.
- **Audit event**: `issuer_resolution_attempted` with hashed input + resolution outcome.
- **Rate/cost limit**: 1 call per filing (cached per session) — this is a lookup, not a search; a session that calls it repeatedly for the same filing is a bug, flagged in observability (§11).

### `search_filing_metadata`
- **Accepted inputs**: `issuer_id`, `source_name`, `lookback_days` (int, capped at existing `scan_service.clamp_lookback_days()`/`clamp_max_candidates()` ceilings — no agent-supplied value can exceed the ceiling already enforced for human-triggered scans).
- **Bounded outputs**: list of `{rcept_no|accession_no|doc_id, report_nm|form_type, rcept_dt, matched_rules: [...], confidence, suppression: "none"|"not_material"|"routine_exclude"}` — **title/metadata only, never excerpt text**.
- **Source of truth**: `scan_service.scan()`'s already-fetched/cached data (never a fresh live fetch triggered by the agent — see §8.1's rate-limit note) + `dart_rules`/`edgar_rules`/`edinet_rules::evaluate_*()` + §2.5's suppression gates, applied server-side before the response is built.
- **Validation**: every row already carries `suppression` computed by the real deterministic functions, not left for the model to infer from a title.
- **Error behavior**: empty list is a valid, normal response (not an error) — a filing with nothing to report is common.
- **Audit event**: `filing_metadata_searched` (issuer, window, row count, suppressed count).
- **Rate/cost limit**: 3 calls per session max; each call capped at 50 rows.

### `get_filing_evidence_excerpt`
- **Accepted inputs**: `rcept_no|accession_no|doc_id` (must be one returned by `search_filing_metadata` this session — server-side foreign-key check, not agent-trusted), `max_chars` (capped, e.g. 4000 — same order of magnitude as the existing per-candidate `excerpt_original` bound already implicit in `document_extractor.py`'s own extraction logic).
- **Bounded outputs**: `{excerpt: str, excerpt_quality: ExcerptQuality, retrieved_at, evidence_location: EvidenceLocation|None, evidence_source_member: str|None}` — reuses the exact existing model shapes from `models.py` verbatim, not a new evidence shape.
- **Source of truth**: `document_service.get_or_fetch_excerpt()` (cache-first — an agent call never forces a fresh network fetch of a document already cached; a cache miss triggers exactly one bounded fetch, same as today's human-triggered path).
- **Validation**: §5.2 row 3 (suppression) is re-checked here as a second gate — a filing that slipped past `search_filing_metadata`'s suppression flag for any reason never reaches excerpt release. Excerpt text is scanned by the prompt-injection defense (§8.1) before being placed in the session context.
- **Error behavior**: `PARSE_FAILED`/`RETRIEVAL_FAILED`-shaped typed error, matching existing `ExtractionState` values — never silently returns an empty string as if it were a real (empty) excerpt.
- **Audit event**: `evidence_excerpt_retrieved` (doc ID, char count, quality, hash of the excerpt itself for reproducibility — not the excerpt text, to keep audit events small and never duplicate the evidence store).
- **Rate/cost limit**: 5 calls per session; total excerpt characters across a session capped (e.g. 20,000 chars) — this is the primary token/cost control (§11).

### `retrieve_filing_table_or_locator`
- **Accepted inputs**: same document identifier as above, plus `locator_hint` (e.g. "Item 2.03" or a table caption string) — advisory only, server does its own anchored search (mirrors `edgar_rules.iter_item_header_positions()`/`document_extractor._item_anchored_excerpt()`).
- **Bounded outputs**: `{locator: EvidenceLocation, excerpt: str (same bound as above), found: bool}`.
- **Source of truth**: `document_extractor.py`'s existing anchored-extraction functions per source.
- **Validation/error/audit/rate limit**: identical discipline to `get_filing_evidence_excerpt`, counted against the same session excerpt-character budget (not a separate budget — prevents budget-splitting to evade the cap).

### `compare_with_prior_filing`
- **Accepted inputs**: `issuer_id`, `current_doc_id`, `matched_rules` (from the current candidate) — no free-text query.
- **Bounded outputs**: `{has_newer_filing: bool, has_superseding_disclosure: bool, prior_doc_ids: [...], rule_diff: {added: [...], removed: [...]}}` — a structured diff, never a narrative summary the model would have to trust blindly.
- **Source of truth**: the existing prior-disclosure comparison repository/logic (exercised by `tests/test_prior_disclosure_comparison.py`) — read-only against comparison records already computed by the existing pipeline, not recomputed ad hoc by the agent.
- **Validation**: this tool is what feeds §5.2 row 8 — its output is re-validated by the backend policy service, never trusted as the final word by the agent itself.
- **Error behavior**: no prior filing found → `{has_newer_filing: false, ...}` (a normal, common result), never an error.
- **Audit event**: `prior_filing_comparison_performed`.
- **Rate/cost limit**: 1 call per session (comparison is issuer+rule scoped, not per-excerpt).

### `search_validated_evidence`
- **Accepted inputs**: `issuer_id`, `theme_slug` (optional), `keyword` (optional, matched server-side against already-persisted evidence, never a live web search).
- **Bounded outputs**: list of already-validated `ResearchEvidenceItem`-shaped records (existing model, §2.6) with `source_url, source_date, excerpt_original, retrieved_at` — capped at 20 rows.
- **Source of truth**: `research_store.py::load_evidence_items()`/`evidence_items_for_case_ids()` — never a fresh retrieval, purely a lookup over already-vetted prior evidence.
- **Validation**: nothing new to validate — this is read-only history.
- **Error behavior**: empty result is normal.
- **Audit event**: `validated_evidence_searched`.
- **Rate/cost limit**: 3 calls per session, 20 rows each.

### `get_validated_relationship_context`
- **Accepted inputs**: `issuer_id`, `counterparty_issuer_id` (optional — omitted, returns all edges touching `issuer_id`).
- **Bounded outputs**: list of `RelationshipEdge`-shaped records (§2.6; **flagged as depending on the currently-unmerged branch**, §2.6/§14) with `relationship_type, status: RelationshipStatus, direction, confidence, evidence_ids: [...]` — the agent receives the *current, derived* edge status, never asked to derive it itself.
- **Source of truth**: `relationship_seed_loader.py::load_relationship_registry_or_none()` output (once merged) — read-only, fail-closed (returns empty on any load error, never partial/guessed data).
- **Validation**: this is the tool that makes Examples 3/4/5 (§10) possible — the agent is handed the *correct* direction/status directly rather than asked to infer it from raw text, which is exactly the failure mode those examples exist to prevent.
- **Error behavior**: registry unavailable → empty list + a typed `RegistryUnavailable` flag, which §5.2 row 8 treats as "cannot confirm no contradiction exists" → routes to `REVIEW_REQUIRED`, never silently treated as "no relationship exists."
- **Audit event**: `relationship_context_retrieved`.
- **Rate/cost limit**: 2 calls per session.

### `search_approved_official_sources`
- **Accepted inputs**: `issuer_id`, `source_category` (enum: `"investor_relations" | "government_regulator"`), `query` (optional keyword, server-side matched against an allowlisted domain set only).
- **Bounded outputs**: list of `{url, title, published_at, domain}` — every `domain` is drawn from `APPROVED_OFFICIAL_SOURCE_ALLOWLIST` (a new, small, explicit backend config list — §8.3), never an open web search.
- **Source of truth**: the allowlist itself + a bounded fetch of only the allowlisted domain (no redirect-following off-allowlist — enforced server-side, not by asking the model not to follow links).
- **Validation**: domain match is exact-suffix, case-normalized; any URL not resolving to an allowlisted domain is dropped before the response is built, never returned "for the model to judge."
- **Error behavior**: empty result if nothing matches — never a fallback to an unapproved source.
- **Audit event**: `official_source_searched`.
- **Rate/cost limit**: 3 calls per session; response capped at 10 rows.

### `save_private_research_packet` (Eeva backend action, not a broad write)
- **Accepted inputs**: the agent's structured claim proposal (§9.1 schema) + the full list of MCP tool-call IDs/evidence IDs it used this session.
- **Bounded outputs**: `{packet_id, status: "saved"}` — no publication side effect of any kind.
- **Source of truth / what it writes**: a `ResearchCase`+`ResearchEvidenceItem` bundle via `research_store.append_research_case_bundle()`, with `trigger_source_type="autonomous_agent"` — **private by construction**: `signal_promotion.is_eligible_for_signal()` never reads `ResearchCase`, only `CandidateSignal.status`, so saving a packet has no publication effect on its own.
- **Validation**: `research_case_validation.validate_research_case_bundle()` (existing function) runs before persistence — a malformed packet is rejected, not silently stored.
- **Error behavior**: validation failure → typed rejection with the specific field(s) that failed, routes the pipeline to `INSUFFICIENT_EVIDENCE`.
- **Audit event**: `research_packet_saved` (packet_id, claim count, evidence_id count — never the claim text itself, to keep the audit stream small; full content lives in the packet).
- **Rate/cost limit**: 1 call per session (a session produces exactly one packet, or none).

### `request_publication_decision` (Eeva backend action — the only path to a public state)
- **Accepted inputs**: `packet_id` (must exist and be validated).
- **Bounded outputs**: `{decision: "AUTO_PUBLISHED"|"VERIFIED_DRAFT"|"REVIEW_REQUIRED"|"INSUFFICIENT_EVIDENCE"|"NOT_MATERIAL", reasons: [...]}`.
- **Source of truth**: §5.2's matrix, evaluated entirely server-side against already-persisted, already-validated data — **the agent supplies no input that can change the outcome once `packet_id` is fixed**; calling this tool twice for the same packet is idempotent and returns the same decision.
- **Validation**: this *is* the validation step; nothing upstream of it is trusted as final.
- **Error behavior**: `packet_id` not found/not validated → hard rejection, never a default decision.
- **Audit event**: `publication_decision_requested` + `publication_decision_made` (decision, every row of §5.2 that was evaluated and its pass/fail — full reproducibility, not just the final verdict).
- **Rate/cost limit**: 1 call per session (paired 1:1 with `save_private_research_packet`).

---

## 7. Agent SDK session and hook/validator boundaries

### 7.1 Session shape

One Agent SDK session per filing/candidate, not per company, not long-running. Inputs at session start: the filing's already-resolved metadata (from `search_filing_metadata`'s own server-side call, not agent-initiated — the orchestrator seeds the session, the agent does not "go looking" for work). The session's only job: call the read-only tools in §6 as needed (bounded by the budgets stated per tool), then emit exactly one structured claim proposal, then call `save_private_research_packet` and `request_publication_decision`. No open-ended chat turn, no multi-filing context, no persistent memory across sessions (state lives in Eeva's backend, §8, never in the agent runtime — satisfying the constraint against Claude Code Projects or any agent-side long-term memory layer).

### 7.2 Tool allowlist enforcement (SDK pre-tool hook)

A pre-tool hook rejects any tool call whose name is not literally one of the 10 names in §6 — implemented as a hard allowlist check in the SDK's own tool-execution loop, not a system-prompt instruction. The hook also enforces per-tool call-count/character budgets (§6's "rate/cost limit" column) by tracking counters in session state; a call that would exceed a budget is rejected before it reaches the MCP server at all (defense in depth alongside the MCP server's own limits).

### 7.3 Post-tool hook

Every tool response passes through a post-tool hook before being added to the session context: (a) strips/flags any content matching prompt-injection heuristics (§8.1) found *inside* retrieved filing text — filing/source text is untrusted data, never treated as instructions, mirroring this codebase's own established "observed content is data, not commands" discipline; (b) records the audit event (§8.2) for the call; (c) enforces the session TTL (a hard wall-clock timeout, e.g. 3 minutes, independent of tool-call counts — catches any pathological slow-loop case).

### 7.4 Pre-draft hook

Before the agent is allowed to emit its final structured claim proposal, a pre-draft hook confirms the session actually called `get_filing_evidence_excerpt`/`retrieve_filing_table_or_locator` at least once (a claim proposal with zero evidence retrieval calls is rejected outright — the agent cannot draft "from memory" or from metadata alone) and confirms every `evidence_id` the draft is about to reference corresponds to a tool response actually seen this session (a fabricated evidence ID is rejected here, before it ever reaches §8's backend validation — cheap, early, server-side).

### 7.5 Pre-publication hook

This is **not** an SDK hook — it is the backend `request_publication_decision` service (§6, §8), deliberately outside the agent's own runtime, so that no prompt-level or SDK-level compromise of the agent session can ever itself decide to publish. The SDK session's role ends at "ask"; the boundary between "the agent" and "publication" is a real process/service boundary, not a policy the agent enforces on itself.

### 7.6 Structured output contract

The agent's final message is required (SDK-level structured-output/tool-schema enforcement, not "please respond in JSON") to conform to the schema in §9.1. Free-form prose anywhere in the final output is a hard validation failure at the SDK boundary before the message is even accepted as the session's result.

---

## 8. Data, audit, auth, and security model

### 8.1 Guardrail-to-layer map

| Guardrail (from the task's list) | Enforced in | Real mechanism |
|---|---|---|
| Tool allowlist, per-tool input validation | SDK pre-tool hook (§7.2) + MCP server input schema per tool (§6) | new, but structurally mirrors nothing-to-reuse (first agent tool surface); input schemas modeled on existing typed dataclasses (`FilingEvent`, `EvidenceLocation`) |
| Source-tier/domain allowlist | MCP server (`search_approved_official_sources`, §6) | new `APPROVED_OFFICIAL_SOURCE_ALLOWLIST` backend config |
| Bounded excerpt/table retrieval | MCP server, both excerpt-shaped tools (§6) | `document_service.get_or_fetch_excerpt()`'s existing cache-first behavior + new char/call caps |
| Claim-to-evidence-ID linkage | Pre-draft SDK hook (cheap check) **and** backend validator (authoritative check, §5.2 row 6–7) | new; backend check is authoritative, hook is defense-in-depth |
| Quote/locator/date/document-ID completeness | Backend validator (§5.2 row 7) | reuses `EvidenceLocation`/`LocationKind` shape verbatim |
| Issuer/entity matching, ambiguity handling | `resolve_tracked_issuer` (§6) | `tracked_companies.py`/`issuer_registry.py`, unchanged |
| Duplicate/reciprocal/semantic-duplicate detection | Backend validator (§5.2 row 9) | same dedup discipline already proven in the (unmerged) relationship-registry loader's own semantic-duplicate-edge check (§2.6) — same pattern, applied to updates |
| Contradiction/freshness/historical/lapsed/disputed/superseded | Backend validator (§5.2 row 8), reading `compare_with_prior_filing` + `get_validated_relationship_context` | `RelationshipStatus` enum (§2.6) + existing prior-disclosure comparison logic |
| Low-value administrative-filing suppression | MCP server (`search_filing_metadata`, `get_filing_evidence_excerpt`) **and** backend validator (§5.2 row 3, belt-and-suspenders) | `low_value_filing_rules.py`/`equity_transaction_materiality.py`/`ownership_materiality.py`, unchanged, called directly |
| Prompt-injection defense for untrusted filing text | SDK post-tool hook (§7.3) | new — pattern-based stripping of instruction-shaped text inside retrieved excerpts, consistent with this codebase's "observed content is data" convention already applied to web/tool content generally |
| Output JSON/schema validation | SDK structured-output boundary (§7.6) **and** backend validator (§5.2 row 4) | new schema (§9.1), backend check is authoritative |
| Immutable audit events, reproducibility metadata | Backend audit service (§8.2) | extends the `StateTransition` append-only pattern (§2.4) |
| Fail-closed publication behavior | Backend policy service (§5.2, first-failing-row-wins) | mirrors `ownership_materiality.py`'s existing "never assume material just because parsing failed" fail-closed convention, applied to publication instead of materiality |
| Rate limits, token/tool budgets, timeouts, retries, cost caps | SDK hooks (§7.2, §7.3) for per-session; backend job scheduler (§8.3) for per-day/global caps | new; retry semantics extend `retry_policy.py`'s existing bounded-attempt/cooldown shape |

### 8.2 Audit model

A new, backend-owned, **append-only audit event table** (`agent_audit_events`, one new table — the smallest new persistence surface this design requires): `event_id, session_id, candidate_id, case_id, event_type, at (ISO 8601), input_hash, output_summary, tool_name|null`. This is distinct from (and does not replace) `CandidateSignal.state_history` — `state_history` is the candidate's own lifecycle narrative (rendered in-product, e.g. Radar Inbox's status pill), while `agent_audit_events` is a full, append-only, backend-only reproducibility log of every tool call and decision a session made, including calls that never affected the candidate's final status (e.g., a `search_validated_evidence` call that returned nothing). Every audit event is tied to `session_id` so a full session can be replayed/inspected end to end. Every event's `input_hash` is a deterministic hash of the exact tool-call arguments, enabling exact reproducibility checks (§13).

### 8.3 Auth model

Three distinct identities, none of which is today's browser `st.user` OAuth flow:
1. **The agent's own MCP-server credential** — a new, narrowly scoped service credential (first machine-to-machine inbound-auth surface in this codebase, §2.9) authorizing only the 10 tools in §6, issued per deployment (not per session, not per user), rotated via the same env-var convention every existing `*_API_KEY` already uses (`src/config/settings.py`) — e.g. `EDGE_RESEARCH_AGENT_SERVICE_TOKEN`.
2. **The orchestrator/scheduler's credential** — authorizes starting a session and calling `request_publication_decision`; separate from the agent's own MCP credential so a compromised agent session cannot itself schedule new sessions or bypass the decision service.
3. **Human admin credential** — unchanged, `EEVA_ADMIN_EMAILS`/`is_admin()` (§2.9), used only for the `VERIFIED_DRAFT`/`REVIEW_REQUIRED` approval action (extends `review_actions.record_review_decision()`, §2.8, with a new permitted status transition and a `published_by` write — no change to its authorization model).

### 8.4 Security boundaries

- The agent has **no filesystem access, no shell access, no raw database access, no arbitrary web browsing** — enforced structurally (the MCP server process is the only thing with those capabilities, and it exposes none of them to the agent; the agent's tool surface is exactly and only §6).
- The agent **never imports or reads from `src/data_access/company_discovery/`** (§2.7) — a hard boundary, not a convention; the existing scope-guard test pattern (`test_company_discovery_scope_guard.py`) is the template for enforcing this for the new agent code too (§12).
- No tool in §6 can send data outside EevaResearch (no email/social/webhook/push tool exists in the set — this is a completeness property of the tool list itself, not a filter on a broader set).
- Production write credentials (state-DB write, publication-service write) are held only by the backend services in §8.3's identities 2–3, never by identity 1 (the agent's own credential) — the agent cannot escalate its own permissions because it never held write permissions to escalate from.

---

## 9. Public-update schema and rendering contract

### 9.1 Structured claim-proposal schema (agent output, private, validated before anything public happens)

```
ClaimProposal {
  claims: [
    {
      claim_id: string,
      claim_type: "direct_reported_fact",   # closed enum — no other value is
                                             # accepted; rumor/recommendation/
                                             # causal/relationship claim types
                                             # are never emitted because the
                                             # agent's own prompt scope (not
                                             # enforcement — see below) only
                                             # ever asks for this one type,
                                             # and §5.2 row 5 independently
                                             # enforces it regardless
      headline: string (<= 140 chars),
      issuer_id: string,
      statement: string (<= 400 chars),      # the fact itself, plain language
      evidence_ids: [string, ...] (>= 1),
      factual_context_evidence_ids: [string, ...] (optional, from
                                     search_validated_evidence — prior
                                     evidence only, never new inference),
      what_this_does_not_establish: string (<= 300 chars, required)
    }, ...
  ],
  session_id: string,
  retrieved_evidence_ids: [string, ...]   # every evidence ID the session
                                           # actually saw, for the pre-draft
                                           # hook's linkage check (§7.4)
}
```

`claim_type` is a closed enum enforced at three independent layers (SDK structured-output schema, pre-draft hook, backend validator row 5) — this is deliberate defense-in-depth given how much of the auto-publication safety case depends on it never being anything but a direct reported fact.

### 9.2 Public rendering model — new, narrow, additive (not the existing `Signal`)

**Why not reuse `Signal` (`src/models/models.py`, §2.8)**: `Signal` carries `direction: Direction`, `strength: Strength`, `horizon: Horizon`, `interpretation: str`, `contrary_evidence: str`, `validation_criteria`, `invalidation_criteria` — an analytical-thesis shape, populated today only by human-curated promotion (`signal_promotion.py`) with deliberately conservative defaults for the fields it can't derive (documented in that module: "never invent a judgment"). Forcing agent-authored raw-fact content through this shape means either leaving five judgment fields permanently empty/default on every agent card (a confusing, half-filled thesis card masquerading as a fact card) or inventing values for them (a direct violation of the task's explicit exclusion of "causal or second-order investment interpretations"). A new, narrow model avoids both failure modes and is a smaller, more honest addition than bending an existing one out of shape.

New model, `VerifiedUpdate` (proposed location: `src/models/models.py`, alongside `Signal`, or a new `src/models/verified_update.py` if kept fully separate — a phase-0 decision, §12):

```
VerifiedUpdate {
  id: string
  candidate_id: string          # back-reference to CandidateSignal
  headline: string
  issuer: string                # resolved tracked-company name, never raw filing text
  entity_id: string
  fact_statement: string
  source_date: string            # ISO 8601, the filing/announcement's own date
  source_url: string
  source_document_id: string     # accession/rcept/doc ID
  excerpt_or_locator: string     # short quote OR EvidenceLocation-shaped locator
  factual_context: list[str]     # from validated prior evidence only, may be empty
  what_this_does_not_establish: string
  label: "VERIFIED FILING FACT" | "VERIFIED COMPANY ANNOUNCEMENT"
  published_at: string
  published_by: "autonomous_agent" | "human_reviewer"
}
```

**Rendering surface**: a new, minimal page/section reusing the existing shell exactly (`with_chrome()`, `render_sidebar()`, `assets/styles.css` tokens, the dark-theme pass already on `main`) — a card component in the same visual language as existing Radar/Signal cards but structurally simpler (no direction/strength chips). Placement (a new sidebar nav entry vs. a section inside an existing page) is a phase-0/1 product decision, not resolved here — see §15.

### 9.3 What is never auto-published (restated as a hard content filter, not a style guideline)

Enforced at §5.2 row 5 (claim-type classification) and row 10 (category allowlist), never left to agent judgment alone: anonymous/secondary-source rumor, causal/second-order interpretation, buy/sell/hold/valuation/target-price/trading language, inferred (unconfirmed) supplier/customer relationships, any new relationship edge or taxonomy change, any outbound message (email/social/push/alert — structurally impossible per §8.4, not merely disallowed).

---

## 10. Examples — all six required cases

### 1. Wonik IPS employee treasury-share disposal → `NOT_MATERIAL`

`search_filing_metadata` returns the filing already flagged `suppression: "not_material"` by `equity_transaction_materiality.assess_equity_transaction_materiality()` (this session's own work, §2.5) — the excerpt is never released to the agent session at all (§6, `get_filing_evidence_excerpt`'s own re-check would refuse it regardless, but in the normal path it never gets that far). Pipeline terminates at step 4 (§4). `CandidateSignal.status = NOT_MATERIAL`, raw `FilingEvent` retained, audit event `filing_metadata_searched` records the suppression reason. No agent tokens spent on drafting.

### 2. CoreWeave filing stating all infrastructure GPUs are NVIDIA GPUs → source-backed factual draft

Passes suppression (a genuine 8-K/annual-report disclosure, not routine). `get_filing_evidence_excerpt` returns the exact sentence with `EvidenceLocation(kind=SECTION, section="...")`. The agent proposes one claim: `claim_type=direct_reported_fact`, `statement="CoreWeave's infrastructure GPUs are exclusively NVIDIA GPUs, per its own filing."`, `evidence_ids=[that excerpt's ID]`, `what_this_does_not_establish="Does not establish future GPU sourcing, pricing terms, or exclusivity duration beyond the filing's own statement."`. All ten §5.2 rows pass (CoreWeave resolves to a tracked issuer per §2.2; single-source, single-supplier direct fact, no relationship-edge claim). → `AUTO_PUBLISHED`, rendered with label `VERIFIED FILING FACT`.

### 3. Fabrinet / Coherent → blocked unsupported-supplier assertion

If the agent's draft proposed "Fabrinet supplies Coherent," `get_validated_relationship_context(issuer_id=Coherent)` returns the real edge: `relationship_type=COMPETITOR` (or equivalent — primary evidence identifies Coherent as a competitor, per the task's own framing), not `SUPPLIER`. §5.2 row 8 (contradiction check) fails: the proposed claim directly contradicts an existing validated relationship edge. → `REVIEW_REQUIRED`, never `AUTO_PUBLISHED`. Note also: even absent a contradiction, a *new* supplier-relationship claim is categorically excluded from auto-publication by §5.2 row 10/§9.3 regardless — this example shows the *belt* (contradiction check), not just the *suspenders* (category exclusion).

### 4. Ciena / Marvell → blocked directional relationship error

Same mechanism as #3: `get_validated_relationship_context` returns the correct direction (Marvell supplies Ciena's *competitors*, not Ciena). A draft asserting "Marvell supplies Ciena" fails §5.2 row 8 on direction mismatch, not just entity mismatch — the tool returns `direction`, not just `relationship_type`, specifically so a directionally-inverted claim is caught even when the two parties and the relationship type are both nominally correct. → `REVIEW_REQUIRED`.

### 5. Applied Materials / Samsung → immutable history retained, current-relationship claim blocked

`RelationshipEvidence` records (§2.6) for the historical Applied Materials–Samsung disclosure are never deleted or edited — the immutable-evidence side of the model guarantees this. But `get_validated_relationship_context` returns the *current* `RelationshipEdge` status, which a newer disclosure has moved to `SUPERSEDED` or `LAPSED` (no longer naming Samsung). A draft claiming a *current* Applied Materials–Samsung relationship fails §5.2 row 8 (superseded/stale evidence) even though the historical evidence record itself is real and retained. → `REVIEW_REQUIRED` for the current-relationship claim; the historical evidence remains queryable via `search_validated_evidence` for factual-context purposes on other claims, just never presented as current.

### 6. Anonymous Google / TSMC CoWoS / Intel EMIB yield rumor → not published as fact

Source is not SEC EDGAR/DART/EDINET, not an issuer IR release, not on `APPROVED_OFFICIAL_SOURCE_ALLOWLIST` — §5.2 row 1 fails immediately. Separately, even if it somehow originated from an allowlisted source, "anonymous"/unnamed sourcing means no resolvable `evidence_id` with a real source URL/document ID/date/entity can ever be constructed (§5.2 row 7 fails) and the claim type itself is a rumor, not a direct reported fact (row 5 fails). Whichever row fires first, the routing is `INSUFFICIENT_EVIDENCE` (if the agent genuinely could not construct any valid evidence record) or `REVIEW_REQUIRED` (if a human should look at whether this deserves manual investigation, e.g. because multiple independent signals are pointing at the same yield question) — never `AUTO_PUBLISHED`, never `VERIFIED_DRAFT` (row 1 or row 7 failing means the draft never reaches "everything else passed" territory).

---

## 11. Error, retry, budget, observability, and incident-handling design

**Errors**: every MCP tool returns a typed result (never a bare exception surfaced to the model) — mirrors the existing `EdgarError`/`DartApiError`/EDINET error-hierarchy convention (§2.1) extended to the new tool layer. A tool-level error routes the pipeline to `FAILED_RETRIEVAL` (retrieval/network-shaped failures) or is caught by the pre-draft/backend validators (content-shaped failures) — never surfaced as a raw stack trace anywhere, matching this codebase's existing fail-closed discipline (`ownership_materiality.py`'s "never assume material just because parsing failed," applied here as "never assume evidence exists just because a tool call errored").

**Retries**: extends `retry_policy.py`'s existing bounded-attempt/cooldown shape (§2.9) to the agent's own retrieval calls — `MAX_RETRY_ATTEMPTS` analog, `AUTOMATIC_RETRY_MAX_PER_TICK` analog for the orchestrator's own retry scheduling. Only `FAILED_RETRIEVAL`-class failures are auto-retried (same restriction the existing code already applies: `PARSE_FAILED` is never auto-retried, and neither is `INSUFFICIENT_EVIDENCE` — retrying a content-validation failure without new information cannot change the outcome).

**Budgets**: per-session tool-call counts and excerpt-character totals (§6, §7.2); per-day/global session count and total cost cap owned by the orchestrator/scheduler (§8.3 identity 2) — a new, small piece of backend policy config, not agent-enforced (an agent session cannot know or affect the global budget, only its own).

**Timeouts**: session-level wall-clock TTL (§7.3) independent of tool-call counts; each individual tool call inherits the timeout discipline already present in each adapter's own client (`DartTimeoutError`/`EdgarTimeoutError`/EDINET equivalent).

**Observability**: the audit event stream (§8.2) is the primary observability surface — every session is fully reconstructable from it. Minimum dashboard metrics (new, small, additive to whatever metrics infra Eeva later chooses — none exists today per §2.9's "no logging module" finding, so this is genuinely new surface, called out honestly): sessions started/day, terminal-state distribution (the six states, §5), average tool calls per session, `AUTO_PUBLISHED` rate, `REVIEW_REQUIRED` queue depth and age, validator-row failure distribution (which of §5.2's 10 rows fails most often — the single most useful signal for tuning the suppression/allowlist rules over time, all without touching the agent's own behavior).

**Incident handling**: a "publication kill switch" (a single backend config flag, checked by `request_publication_decision` before evaluating §5.2 at all) that forces every decision to `REVIEW_REQUIRED` regardless of policy outcome — the fastest possible mitigation if a validator bug is discovered post-launch, requiring no code deploy. `AUTO_PUBLISHED` items remain individually revocable (unpublish = set `status` away from `PUBLISHED`, exactly the same operation a human reviewer already has via `review_actions.record_review_decision()`, §2.8 — no new revocation mechanism needed).

---

## 12. Minimal phased implementation plan (exact anticipated files/tests)

This section anticipates a **future** implementation task — nothing here is built now.

**Phase 0 — prerequisite verification** (no new code): confirm `feat/supply-chain-relationship-seed-v1` (§2.6) is merged or re-verify its module paths against `main` at implementation time; resolve the `VerifiedUpdate` placement decision (§9.2); confirm final MCP/Agent SDK package names available in `requirements.txt` at that time.

**Phase 1 — model additions** (smallest possible diff, per §5.1/§9.2):
- `src/models/models.py` — add `CandidateStatus.VERIFIED_DRAFT`, `CandidateStatus.INSUFFICIENT_EVIDENCE`; add `CandidateSignal.published_by: str = "human_reviewer"`; add `VerifiedUpdate` dataclass.
- `tests/test_models_agent_states.py` (new) — enum-value presence, `CandidateSignal` default-backward-compatibility (existing construction sites don't break), `VerifiedUpdate` field/serialization round-trip.
- Extend `src/logic/signal_promotion.py`'s docstring only (no logic change — its existing `status == PUBLISHED` check already covers `published_by="autonomous_agent"`); add a targeted test to `tests/test_signal_promotion.py` (or wherever that suite lives — confirm at implementation time) proving both provenance values promote identically.

**Phase 2 — MCP tool server (read-only)**:
- New package `src/mcp_agent/tools/` (name illustrative) — one module per tool in §6, each a thin wrapper calling the exact existing functions cited in that tool's "source of truth."
- `tests/test_mcp_agent_tools_*.py` — one file per tool, unit-level, fixture-driven (no network), following this repo's existing `tmp_path`/mock-client conventions (e.g. `tests/test_radar_pipeline.py`'s `_make_client()` pattern).
- `tests/test_mcp_agent_tool_scope_guard.py` — AST-based, modeled directly on `tests/test_company_discovery_scope_guard.py` (§2.9): no tool module imports `src/data_access/company_discovery/`, no tool module performs a write, no tool module imports an unapproved network client.

**Phase 3 — backend policy service**:
- `src/logic/publication_policy.py` (new) — §5.2's matrix as a pure function `evaluate_publication_eligibility(packet, context) -> PolicyDecision`.
- `src/data_access/agent_audit.py` (new) — `append_audit_event()`/`load_audit_events_for_session()`, extending the append-only pattern (§8.2), new `agent_audit_events` table/JSON store following the existing `state_db`/`postgres_state_db` dual-backend convention.
- `tests/test_publication_policy.py` — one test per §5.2 row (pass and fail), plus the six §10 examples as named integration-style tests (`test_wonik_ips_style_treasury_disposal_is_not_material`, `test_corewave_nvidia_gpu_statement_auto_publishes`, `test_fabrinet_coherent_supplier_claim_is_blocked_by_contradiction`, `test_ciena_marvell_directional_claim_is_blocked`, `test_applied_materials_samsung_current_claim_blocked_by_superseded_edge`, `test_anonymous_yield_rumor_never_auto_publishes`) — directly exercising this design's own worked examples as the acceptance tests.

**Phase 4 — orchestrator + Agent SDK session wiring**:
- `scripts/research_agent_worker.py` (new) — modeled on `scripts/radar_worker.py`'s existing always-on-loop shape, own `EDGE_RESEARCH_AGENT_LIVE_ENABLED` master switch (default off, matching every other worker), one bounded Agent SDK session per eligible new filing.
- `tests/test_research_agent_worker_scope_guard.py` — modeled on `tests/test_daily_news_scope_guard.py`/`tests/test_company_discovery_scope_guard.py`: never imported by `app.py`/any UI page, dormant by default.

**Phase 5 — rendering**:
- `src/ui/pages/verified_updates.py` (new, placement per Phase 0's decision) + `tests/apptest_pages/verified_updates_page.py` + `tests/test_verified_updates_page.py`, following the exact existing AppTest convention (§2.9).

No phase touches `src/data_access/dart|edgar|edinet/*` write paths, `src/data_access/company_discovery/*`, or any existing published test file's assertions beyond the one documented, additive docstring change in Phase 1.

---

## 13. Acceptance criteria and offline evaluation harness

**Acceptance criteria** (must all hold before any live/production enablement):
1. Every one of §10's six examples produces exactly the stated terminal state, deterministically, across repeated runs with identical input (audit `input_hash` equality proves this — §8.2).
2. Zero `AUTO_PUBLISHED` results in the offline harness (below) contain a claim whose `claim_type` is anything but `direct_reported_fact`.
3. Zero `AUTO_PUBLISHED` results reference an `evidence_id` not present in that session's own audit trail.
4. 100% of `AUTO_PUBLISHED` results pass every §5.2 row when re-evaluated independently against the persisted packet (a second, out-of-band check — proves the policy service, not just the agent, is deterministic).
5. The publication kill switch (§11), when enabled, drives 100% of decisions to `REVIEW_REQUIRED` regardless of §5.2 outcome, verified by test.
6. No test in Phase 2–5's scope-guard suites (§12) fails.

**Offline evaluation harness** (new, `tests/fixtures/agent_eval_cases/`): one fixture per real historical filing already present in this codebase's own test fixtures or design-report examples (the six in §10, plus the existing Wonik-IPS-shaped fixtures already added this session in `tests/test_radar_pipeline.py`/`tests/test_equity_transaction_materiality.py`, reused rather than re-authored) — each fixture pairs a filing input with an expected terminal state and (for `AUTO_PUBLISHED` cases) an expected claim shape. Run entirely offline against mocked MCP tool responses (no live network, no live model call required for the deterministic-validator portion — only the claim-proposal step needs a real or recorded model response, and that step can be fixture-recorded for CI, consistent with this repo's own "zero network calls in tests" convention already enforced across the existing suite).

---

## 14. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| §2.6's relationship-registry branch never merges, or merges with a different shape | Medium | High — Examples 3/4/5 and `get_validated_relationship_context` depend on it | Phase 0 gate (§12); if unmerged at implementation time, `get_validated_relationship_context` degrades to "always REVIEW_REQUIRED for any relationship-adjacent claim" (fail closed, never silently skips the check) |
| Model produces a plausible-sounding claim with a subtly wrong evidence_id (right document, wrong sentence) | Low-Medium | High — this is the core trust risk of the whole product | §7.4/§8.1 linkage checks confirm the ID exists and was seen this session, but cannot by themselves confirm the *quote text* is verbatim; recommend (deferred, §15) an exact-substring-match check between the claim's excerpt and the tool-returned excerpt as a Phase-3 addition |
| Source-tier allowlist (§6, `search_approved_official_sources`) becomes stale or is misconfigured to include an unvetted domain | Low | High | Allowlist is backend config, not agent-editable; changes should themselves require the same admin authorization as any other production config change (out of this design's scope to specify further) |
| Duplicate/idempotency check (§5.2 row 9) has a false-negative, double-publishing the same fact from two different filings | Low | Medium | Content-hash + evidence-ID-set check is deterministic and testable in isolation (§13); not a novel risk class — mirrors existing dedup logic already proven in the (unmerged) relationship loader |
| Budget/rate limits (§6, §11) are set too low for legitimately complex filings, causing false `INSUFFICIENT_EVIDENCE` | Medium | Low | Budgets are backend config, tunable without a schema change; §11's observability (validator-row failure distribution) directly surfaces this as a tuning signal |
| New inbound service-credential surface (§8.3) is the first machine-to-machine auth this codebase has ever had | Medium | Medium | Scoped to exactly the 10 read-only tools + the two mediated write-request tools; no broader credential is introduced |
| A future engineer widens the agent's tool set "just this once" (e.g. adds a write tool) without going through this design's boundary | Medium | High | Phase 2's scope-guard test (§12) is the enforcement mechanism, modeled on this repo's own proven pattern for exactly this class of drift |

---

## 15. Explicitly deferred features

- Any second agent, agent variant, or multi-agent coordination — out of scope by the task's own instruction.
- Exact-substring quote verification against the source document (flagged as a risk-mitigation candidate, §14, not designed here).
- `VerifiedUpdate` page placement/navigation decision (§9.2) — a product decision, not an architecture one.
- Any notification/alerting surface (explicitly excluded from auto-publication content, §9.3, and no such surface exists in this codebase to extend, §2.9).
- Human-approval UI for `VERIFIED_DRAFT`/`REVIEW_REQUIRED` beyond "reuse `review_actions.record_review_decision()` with a new caller" — the exact admin queue UI/UX is a Phase-5-or-later product decision.
- Cross-session agent memory or learning of any kind (explicitly excluded by the task's Claude Code Projects restriction and by §7.1's stateless-per-filing session design).
- Extending the agent to Company Discovery's dormant relationship-extraction pipeline (§2.7) — a deliberate non-goal; that subsystem's own dormancy is preserved, not built upon.
- A general-purpose "approved official source" onboarding workflow (how a new IR page or regulator domain gets added to the allowlist) — assumed to be ordinary backend config change process, not designed here.
- Metrics/dashboard infrastructure beyond the audit-event stream itself (§11) — this codebase has none today; choosing one is out of scope for a single-agent design.

---

## 16. Files inspected

Direct reads/greps this report is grounded in (representative, not exhaustive — every file path cited by name above was independently verified to exist at the stated location):

`app.py`; `src/models/models.py`; `src/models/issuer.py`; `src/models/research_case.py`; `src/models/theme_registry.py`; `src/models/theme_research.py`; `src/models/company_discovery_models.py`; `src/models/daily_news_models.py`; `src/config/tracked_companies.py`; `src/config/issuer_registry.py`; `src/config/ontology.py`; `src/config/settings.py`; `src/logic/issuer_coverage.py`; `src/logic/signal_promotion.py`; `src/logic/review_actions.py`; `src/logic/theme_evidence.py`; `src/logic/research_case_validation.py`; `src/data_access/dart/{dart_rules,radar_pipeline,ownership_materiality,equity_transaction_materiality,low_value_filing_rules,scan_service,document_service,retry_policy,corp_code_resolver,client,errors}.py`; `src/data_access/edgar/{edgar_rules,edgar_pipeline,scan_service,client,document_service,document_extractor,discovery_rules,discovery_service,cik_resolver,edgar_service,errors}.py`; `src/data_access/edinet/{edinet_rules,edinet_pipeline,scan_service,document_service,document_extractor,discovery_service,edinet_code_resolver,client,edinet_service,material_event_shadow,errors}.py`; `src/data_access/research_store.py`; `src/data_access/company_discovery/{candidate_pipeline,entity_resolution,extraction_rules,company_discovery_backend,scoring}.py`; `src/data_access/daily_news/{materiality_classification,editorial_admission,daily_news_pipeline}.py`; `src/data_access/backend_factory.py`; `src/data_access/postgres_state_db/schema.py`; `src/ui/pages/{radar_inbox,research_cases,company_discovery_admin}.py`; `src/ui/ui.py`; `tests/apptest_pages/*.py` (listing); `tests/test_evidence_packet_phase1.py`; `tests/test_evidence_packet_phase2_step2.py`; `tests/test_company_discovery_scope_guard.py`; `tests/test_daily_news_scope_guard.py`; `tests/test_research_lead_selection.py`; `.github/workflows/manual-scan.yml`; `scripts/radar_worker.py`; `design/RADAR_WORKER_DEPLOYMENT.md`; branch `feat/supply-chain-relationship-seed-v1` (`src/models/relationship_models.py`, `src/config/relationship_seed_loader.py`, `config/eevaresearch_relationship_seed.yaml`) via `git log`/`git branch -a` comparison against `main`.

## 17. Exact changed file, git status, stash status, and confirmation

**Exact changed file**: this document only — `design/AUTONOMOUS_EVIDENCE_FIRST_RESEARCH_AGENT_DESIGN_2026_09_17.md` (new file).

```
$ git status --short
?? design/AUTONOMOUS_EVIDENCE_FIRST_RESEARCH_AGENT_DESIGN_2026_09_17.md
?? design/COMPANY_UNIVERSE_AND_SUPPLY_CHAIN_AUDIT_PLAN_2026_09_16.md
?? design/COVERAGE_EXPANSION_CANDIDATES_TIER1_2026_09_15.md
   ... (pre-existing untracked design/*.md files and tests/test_radar_status.py
       from earlier, unrelated work — untouched, not staged, not created by
       this task)

$ git stash list
stash@{0}: On fix/signals-admission-precision: Signals admission precision fix (P0/P1/P2, validated, not yet committed) - parked before returning to feat/daily-news-cohort1-sources for commit
stash@{1}: On feat/daily-news-cohort1-sources: Daily News Cohort 1 source-expansion (provisionally approved, pending scope-guard exception) - parked before fix/signals-admission-precision work
stash@{2}: On feat/edinet-filing-source-usability: EDINET filing-source usability implementation (approved, pending commit)
   (unchanged — not touched by this task)
```

**Confirmation**: no code file was created or modified; no dependency was added to `requirements.txt`; no MCP server was created; no database schema, model, or existing configuration file was changed; no ingestion, worker, backfill, discovery process, or network operation was run. All repository inspection for this report was read-only (file reads, `grep`, `git log`/`git branch -a` for branch comparison) — no `streamlit run`, no scan, no write of any kind, anywhere in the repository, during this task.
