# DART Low-Value Filing Leakage — Root Cause and Suppression Design

Status: **read-only investigation and design report**. No production code, model, schema, or test was modified. No ingestion, backfill, worker, or scan was run. No cache or database was written.

## 0. Scope note on the Wonik IPS filing itself

No filing or candidate matching Wonik IPS (KRX `240810`, corp_code `01135941`, tracked under `ai-buildout` per `src/config/tracked_companies.py`) exists in this local environment's on-disk DART cache (`data/cache/dart_filing_events.json`/`dart_candidates.json` contain only Samsung Electronics and SK Hynix records — the two original DART pilot companies). This report therefore treats the facts given in the task (September 15/16, 2026; 51,456 treasury shares; ₩6.14bn; employee-directed disposal; no operational/customer/capex/demand/technology/earnings/supply-chain content) as the authoritative input, and traces the exact, real pipeline logic that a filing matching that description would pass through — verified against the actual current code, not assumed or independently re-fetched from a live source (this task is explicitly read-only, no ingestion).

## 1. Root cause

**Two independent, structurally distinct root causes, not one:**

1. **The Radar/CandidateSignal promotion path has a materiality gate for exactly one category (`ownership_change`) and none for any other — including `financing`, the category a treasury-share disposal filing matches.** `src/data_access/dart/dart_rules.py`'s keyword lexicon files `자기주식처분` ("treasury stock disposal") under the same `financing` category as `유상증자` (a genuine capital raise) and `배당결정` (a dividend decision) — a single keyword hit produces `confidence="Moderate"` regardless of transaction scale, and `src/data_access/dart/radar_pipeline.py::process_candidate()` promotes **every** non-`ownership_change` category match straight to `CandidateStatus.NEEDS_REVIEW` with no scale/context check of any kind (confirmed at `radar_pipeline.py:225-237` — the `else: final_status = CandidateStatus.NEEDS_REVIEW` branch has no gate at all).
2. **The Research Theses "theme feed" surface applies no content filter whatsoever to raw `FilingEvent` records — not even the title-keyword check `dart_rules.py` itself performs.** `src/logic/theme_evidence.py::recent_filing_evidence()` matches *any* `FilingEvent` purely by `corp_name` membership in the viewed theme's company set (`theme_evidence.py:57-69`) and renders it as "Radar filing" evidence. Every filing DART returns for a tracked company becomes a persisted `FilingEvent` **unconditionally, before `dart_rules.evaluate_report_name()` is even called** (confirmed at `src/data_access/dart/scan_service.py:203-226` — `new_filing_events.append(filing)` precedes the rule-evaluation call) — so this leak does not require the filing to match any keyword category at all, and is strictly more permissive than the Radar path.

Both are real, live, currently-wired code paths — not dormant infrastructure — confirmed by tracing every call site to its actual UI entry point (§2).

## 2. Exact end-to-end trace, per surface

### 2a. Source adapter → what DART actually gives the pipeline

`FilingEvent.report_nm` is the **only** classification signal available at scan time. Confirmed directly from the model's own field documentation (`src/models/models.py:337-395`): for `source_name == "OpenDART / DART"`, `pblntf_ty`/`pblntf_detail_ty` (the fields that would carry a structured disclosure-type code) are **always empty strings** — DART's `list.json` response does not echo a type code per row (`pblntf_ty`/`pblntf_detail_ty` are documented as search *filters*, not response fields — `dart_rules.py`'s own module docstring, lines 16-24). There is no `report_category`, `disclosure_type`, or similar field anywhere on `FilingEvent` for DART. Every downstream classification decision in this codebase is therefore a plain-substring match against the raw Korean title string alone (until a document is fetched and excerpted, which happens later and only for candidates that already reached `NEEDS_REVIEW`/processing — see §2c).

### 2b. Issuer and source classification

Wonik IPS is a real, currently-tracked `TrackedCompany` (`src/config/tracked_companies.py:853-861`): `name="Wonik IPS Co., Ltd."`, `exchange="KRX"`, `krx_code="240810"`, `source="OpenDART / DART"`, `themes=("ai-buildout",)`, `subthemes=()`, `corp_code` resolved lazily to `01135941` via `with_resolved_corp_codes()`. No subtheme is set (its own notes: "No existing subtheme fits; left unset"). Nothing about issuer/source classification is implicated in the leak — the company is legitimately tracked and legitimately in-scope for scanning; the defect is entirely in what happens to its filings once fetched.

### 2c. Filing classification → scoring → promotion (Radar path)

1. `scan_service.py` fetches DART's disclosure list for Wonik IPS, constructs a `FilingEvent` per row, and appends it to `new_filing_events` **unconditionally** (`scan_service.py:203-225`), *then* calls `dart_rules.evaluate_report_name(record.report_nm)`.
2. A treasury-disposal filing's real, standard DART title shape is `"주요사항보고서(자기주식처분결정)"` — confirmed via the existing, real test fixture `tests/test_dart_rules.py::test_financing_keyword_matches_treasury_stock_disposal` (line 37-41), which already asserts this exact title reaches `confidence == "Moderate"` today. `자기주식처분` is one of six keywords filed under the single `financing` category (`dart_rules.py:68-71`, alongside `유상증자`/`무상증자`/`자기주식취득`/`증권신고서`/`배당결정`) — one match, one category, `confidence="Moderate"` (`dart_rules.py:112-135`).
3. `radar_pipeline.py::process_candidate()` (line 154 onward) drives the candidate through document retrieval/extraction/translation, then decides the terminal status (lines 224-242):
   ```python
   if candidate.extraction_state == ExtractionState.EXTRACTED:
       if _is_ownership_change_candidate(candidate):
           gate_result = ownership_materiality.assess_ownership_materiality(...)
           final_status = CandidateStatus.NOT_MATERIAL if gate_result.outcome == "not_material" else CandidateStatus.NEEDS_REVIEW
       else:
           final_status = CandidateStatus.NEEDS_REVIEW   # <- every financing-category candidate lands here, unconditionally
   ```
   `_is_ownership_change_candidate()` (line 247-248) checks only for a `matched_rules` entry starting with `"ownership_change:"` — a `financing:capital_raise_or_treasury_stock:자기주식처분` match never matches this, so the one existing materiality gate in this codebase (`src/data_access/dart/ownership_materiality.py`) is **never even invoked** for this filing type.
4. Terminal status: `CandidateStatus.NEEDS_REVIEW`.
5. `src/ui/pages/radar_inbox.py` — "the only page in this app backed by real, live data" (its own module docstring, line 4) — is a public, read-only page (its own docstring: "this page is now strictly read-only... Publish/Monitor/Exclude review decision... removed") that renders whatever is in the on-disk candidate store, with no server-side status filter found anywhere in `radar_card.py`/`radar_service.py`/`radar_status.py` that would hide a `NEEDS_REVIEW` item. **A live reader sees this filing exactly as they would see a genuine material capex/earnings/contract disclosure**, distinguished only by a "Moderate" confidence label — which `dart_rules.format_confidence_label()`'s own docstring already warns "reads as a materiality judgment out of context."

### 2d. Theme feed (Research Theses page) — independent, unconditional leak

`src/ui/pages/themes_research.py:461` calls `theme_evidence.recent_filing_evidence(filings, company_names, _LIVE_EVIDENCE_LIMIT)` directly, on the live page render path. `recent_filing_evidence()` (`theme_evidence.py:57-69`) filters only on `f.corp_name in company_names` — no `dart_rules` call, no status check, no category check of any kind. Every Wonik IPS `FilingEvent` — including one that never matched any `dart_rules` keyword at all — is eligible to render as "Radar filing" evidence under the `ai-buildout` theme. This is a **more permissive** leak than the Radar path: it does not require the title to match any category keyword whatsoever.

### 2e. Daily News — currently dormant, but scoped to leak identically once wired

`src/data_access/daily_news/dart_filing_candidate_adapter.py` is an explicitly unwired "shadow adapter" (its own docstring: "Not imported by daily_news_pipeline.py, scripts/daily_news_worker.py, feed_registry.py, daily_news_store.py, any UI page, or radar_pipeline.py/scan_service.py/client.py"). It does **not** currently surface anything in Daily News. However, its own `_INCLUDED_CATEGORIES` (line 62-65) explicitly includes `"financing"` — the same category `자기주식처분` matches — and deliberately excludes only `listing_or_market_event`/`ownership_change`/`market_rumor_response`. **The moment this adapter is wired to a live caller (a stated future step, not this task's concern), this exact filing type would surface in Daily News with zero additional gate**, since nothing in the adapter distinguishes a capital-raise-scale financing event from a routine treasury transfer.

### 2f. "Research-facing signal pipeline" (auto-generated `ResearchCase`) — currently dormant, currently DART-excluded by default, but architecturally primed for DART

A full, tested, three-module pipeline exists (`src/logic/research_lead_selection.py`, `research_lead_factory.py`, `research_lead_orchestration.py`, Phase 4 Steps 4A-1/4A-2/4B-1) that can turn a `NEEDS_REVIEW` `CandidateSignal` into a real `ResearchCase` whose auto-generated research question is literally *"What are the evidence-backed dependencies, relationships, or second-order effects connected to this {source_name} filing?"* (`research_lead_factory.py:162-165`). Tracing precisely, not assuming:
- `select_research_lead()`'s own `recognized_source_names` default **already includes** `"OpenDART / DART"` (`research_lead_selection.py:78-82`) — DART is anticipated as a valid source at the selector layer.
- `select_research_lead()` would qualify this candidate: `confidence="Moderate"` is in `_QUALIFYING_CONFIDENCE_LEVELS` (line 51), `status is NEEDS_REVIEW` (line 264), source recognized, one category (`financing`) → `priority=QUALIFIED` (not `HIGH_SIGNAL`, which needs 2+ categories, but `QUALIFIED` alone is sufficient for `research_lead_factory.py` to build a case, line 121).
- **However**, `research_lead_orchestration.py`'s own `ResearchLeadOrchestrationConfig.allowed_source_names` defaults to `("SEC EDGAR",)` only (line 63), and this is enforced as a real, hard pre-filter (`_source_recognized()`, called at line 194, before a candidate ever reaches the selector). Under the *current default config*, a DART candidate is filtered out at the orchestration layer and never reaches `select_research_lead()` at all.
- The whole orchestration module's own docstring additionally confirms it is "not implemented" as a live worker step yet ("Step 4B-2, not implemented here") and "nothing in this step is reachable from `scripts/radar_worker.py`, `scripts/run_scan.py`, any UI page, or app startup."

**Precise conclusion**: this specific path is **not currently live** for DART filings, and its own default configuration already excludes DART — but the selector layer's own recognized-source list already anticipates DART, meaning this is a *near-term* risk (the moment a worker is wired and/or `allowed_source_names` is widened to include DART, exactly as it already can be for EDGAR) rather than a currently-operative leak. Flagged, not overstated.

### 2g. Candidate-evidence generation (Company Discovery) — dormant, but DART is an explicit input source

`src/data_access/company_discovery/candidate_pipeline.py:67` defines `_FILING_SOURCES: tuple[str, ...] = ("OpenDART / DART", "SEC EDGAR", "EDINET")` — DART is explicitly, unconditionally one of the three sources this pipeline reads persisted `FilingEvent`/`CandidateSignal` records from (line 110 onward). The package itself is settings-gated and not run by default (`company_discovery_live_enabled`/`company_discovery_admin_enabled`, both default off — established in this session's earlier discovery work). **If** this dormant worker or `scripts/backfill_company_discovery.py` is ever run, Wonik IPS's persisted `FilingEvent`/`CandidateSignal` becomes eligible input for `CandidateEvidence` extraction; whether it actually produces a relationship-evidence row depends on `extraction_rules.py`'s own trigger-phrase matching against the filing text — a separate mechanism this report does not audit exhaustively, since the candidate never needs to reach `NEEDS_REVIEW` for this specific read path (it reads raw `FilingEvent`s too, per `SourceType.FILING`).

### 2h. Alerts

**No alerting/notification system exists anywhere in this codebase.** Searched exhaustively (`grep -rli "alert" src/`) — the only hits are unrelated: DART's own "market rumor" disclosure-response terminology, Daily News source-registry prose, and an unrelated feedback-submission model. This is reported as a genuine non-finding, not silently assumed to be a real surface that needs a fix.

## 3. Exact existing filters/scores that allowed it through — summary table

| Surface | What ran | What should have run but didn't |
|---|---|---|
| `dart_rules.evaluate_report_name()` | Title-keyword match → `financing` category, `confidence="Moderate"` | Working exactly as designed — this function's own docstring is explicit that it is "never a market judgment," purely a keyword-presence detector. Not itself a bug. |
| `radar_pipeline.process_candidate()` | `_is_ownership_change_candidate()` → False → unconditional `NEEDS_REVIEW` | A materiality/scale gate for the `financing` category, mirroring the one that already exists for `ownership_change` |
| `radar_inbox.py` (public UI) | Renders every candidate in the store, no status filter | Nothing — this page is correctly "strictly read-only" by design; the fix belongs upstream, not in a UI-level filter that would just hide the symptom |
| `theme_evidence.recent_filing_evidence()` | Company-name match only | Any content/category filter at all — currently zero |
| `dart_filing_candidate_adapter.py` (dormant) | `_INCLUDED_CATEGORIES` includes `financing` | A materiality distinction within `financing`, same gap as the Radar path, inherited by construction |
| `research_lead_orchestration.py` (dormant) | Default config excludes DART entirely | N/A today — flagged as a near-term risk, not a current gap |
| `company_discovery/candidate_pipeline.py` (dormant) | Reads all three filing sources unconditionally | Out of this report's audited scope beyond flagging the read path exists |

## 4. Proposed filing-type taxonomy

A new, small, title-driven classifier — `classify_low_value_filing(report_nm: str) -> LowValueFilingType | None` — recognizing DART's own standard, documented `report_nm` shapes for exactly the five classes named in the task, mirroring the exact mechanism `dart_rules.ROUTINE_EXCLUDE_PATTERNS` already uses (a fixed tuple of real, standardized Korean disclosure-title substrings, each commented with its provenance):

| Class | Representative real DART title substrings (standardized regulatory terminology, same "observed live" / "standard, not observed" provenance discipline as the existing lexicon) |
|---|---|
| `employee_directed_treasury_disposal` | `자기주식처분` when the filing's own title or (where available) excerpt names an employee/officer benefit-plan purpose (e.g. `임직원`, `상여`, `성과급`) rather than a market/strategic purpose |
| `employee_share_plan_transfer` | `우리사주조합` (employee stock ownership association) transfer/disposal filings |
| `stock_option_or_rsu_exercise` | `주식매수선택권행사` (stock option exercise), `양도제한조건부주식` (RSU) settlement filings |
| `routine_internal_equity_transaction` | A residual bucket for other internally-directed, no-counterparty equity mechanics not covered above — deliberately narrow, never a catch-all for anything with an external party |
| `routine_corrective_or_administrative_filing` | `[기재정정]`-prefixed filings (already detected via the existing `AMENDMENT_MARKER`) that amend only an administrative field, and other standard administrative-only report types (e.g. routine `공시책임자` (disclosure officer) change notices) |

**Deliberately not a rename or restructuring of `KOREAN_KEYWORD_LEXICON`'s existing `financing` category** — this taxonomy is a *narrower, second, independent classification layer* applied on top of (never instead of) the existing category match, exactly mirroring how `ownership_materiality.py` sits on top of the `ownership_change` category today.

## 5. Recommended hard-suppression rules — two layers, matched to what data each surface actually has

The single most important architectural finding of this investigation: **not every leaking surface has the same information available.** `theme_evidence.py` only ever sees the bare `report_nm` (no document excerpt is fetched at that point in the pipeline); the Radar candidate-processing path (`radar_pipeline.py`) *does* have the extracted excerpt by the time a status decision is made. A single suppression mechanism cannot correctly serve both — proposing one would either (a) be too weak where excerpt text is available, or (b) be impossible to implement where it isn't.

- **Layer 1 — title-only, category-level suppression** (applies wherever only `report_nm` is available: `theme_evidence.recent_filing_evidence()`, and as a first-pass check inside `radar_pipeline.py` before document retrieval even begins). A filing whose title matches one of §4's five classes, **and does not also match a title-level escape-hatch marker** (reusing `ownership_materiality.MATERIAL_OWNERSHIP_MARKERS`' own categories directly — `최대주주변경`/`경영권변동`/`공개매수`/`질권설정`/`질권해지`/`합병`/`주식의포괄적교환`/`완전자회사` — since those are exactly the title-detectable strategic/control markers this task's own escape hatch describes), is suppressed from any title-only surface.
- **Layer 2 — excerpt-based materiality/exception gate** (applies only where excerpt text is genuinely available — inside `radar_pipeline.process_candidate()`, at the exact same call site `ownership_materiality.assess_ownership_materiality()` already occupies). A new, small module — proposed name `src/data_access/dart/equity_transaction_materiality.py`, mirroring `ownership_materiality.py`'s own shape file-for-file — is invoked whenever `_is_ownership_change_candidate()`-style scoping (renamed/generalized to also recognize the `financing:capital_raise_or_treasury_stock` rule specifically, not the whole `financing` category, since `유상증자`/`증권신고서` genuinely are capital-markets events) determines the candidate is a treasury/equity-mechanics filing. It checks the excerpt for:
  - Any named commercial counterparty, contract, capex, capacity, order/backlog, guidance, financial result, governance/control change, product, technology, or strategic-transaction language → **never suppress**, route to `NEEDS_REVIEW` exactly as today.
  - The explicit escape-hatch conditions from the task: material change to shares outstanding/free float (reusing `extract_ownership_delta_pp()`'s existing percentage-delta extraction where the excerpt shape allows it, or a new share-count-vs-outstanding-shares check where a raw count is given, as in the Wonik IPS example), ownership/control change, founder/CEO/controlling-shareholder involvement (a small, explicit named-role marker list, same discipline as `MATERIAL_OWNERSHIP_MARKERS`), or a disclosed restructuring/M&A/capital-raise event (reuses the same title/excerpt markers as Layer 1) → **never suppress**.
  - Otherwise (the Wonik IPS case: a named share count and won amount, an employee-directed purpose, no counterparty/contract/capex/product language, no control-change marker) → suppress, using the **existing** `CandidateStatus.NOT_MATERIAL` terminal state (never a new status — this is exactly what that status already means, and `ownership_materiality.py` already sets it via the identical mechanism).

## 6. Materiality / strategic exception rules (the escape hatch, stated precisely)

A filing in one of the five §4 classes is **never** suppressed, and instead proceeds to `NEEDS_REVIEW` exactly as today, if **any** of the following hold (checked in this order, first match wins, mirroring `assess_ownership_materiality()`'s own "marker checked before threshold" structure):

1. A material-ownership marker is present (reuse `ownership_materiality.MATERIAL_OWNERSHIP_MARKERS` directly — controlling-shareholder change, tender offer, pledge/collateral, merger/share-swap/wholly-owned-subsidiary conversion).
2. The transaction is material relative to shares outstanding or free float — a numeric check, only performed when the excerpt actually states both the transacted share count and a total-shares reference (never guessed when absent).
3. A named founder, CEO, or controlling shareholder is a party to the transaction (a small, explicit title/excerpt marker list — e.g. `최대주주`, `대표이사`, `창업자` — co-occurring with the equity-mechanics keyword).
4. The filing discloses or is explicitly linked to a restructuring, M&A transaction, or capital raise (reuses the same merger/tender-offer markers, plus the existing `유상증자`/`증권신고서` keywords already in `dart_rules.py`'s own `financing` category — these two specific existing keywords should **never** be routed through the new suppression classifier at all, since they are genuine capital-raise mechanisms, not employee-directed housekeeping; only `자기주식처분`/`자기주식취득`-class filings are candidates for the new gate).

## 7. Exact model, parser, scorer, repository, UI/API, and test files implicated

| File | Role | Proposed change |
|---|---|---|
| `src/data_access/dart/dart_rules.py` | Title-keyword lexicon | **No change to `KOREAN_KEYWORD_LEXICON` or `evaluate_report_name()`'s own output** — it stays a pure, unopinionated keyword detector, exactly as its docstring requires. Add the new §4 classifier as an additive function in this same module (or a new sibling module — see §13's phased plan for the choice) |
| `src/data_access/dart/ownership_materiality.py` | Existing materiality gate (pattern to mirror, not modify) | Unchanged — read-only reference implementation |
| `src/data_access/dart/radar_pipeline.py` | Promotion decision (`process_candidate()`, `_is_ownership_change_candidate()`) | Generalize the scoping check (or add a parallel one) to also route `financing:capital_raise_or_treasury_stock` matches through the new Layer 2 gate, exactly where `ownership_change` matches are routed today |
| `src/logic/theme_evidence.py` | `recent_filing_evidence()` | Add a Layer 1 title-check before including a `FilingEvent` as evidence |
| `src/data_access/daily_news/dart_filing_candidate_adapter.py` | Dormant shadow adapter | Apply the same Layer 1 check before it is ever wired live (pre-emptive, since it inherits the identical gap) |
| `src/models/models.py` (`CandidateStatus`, `FilingEvent`) | No new field or status needed | `CandidateStatus.NOT_MATERIAL` already exists and already means exactly this |
| `src/logic/research_lead_selection.py` / `research_lead_factory.py` / `research_lead_orchestration.py` | Dormant research-lead pipeline | No direct code change required for this fix — a `NOT_MATERIAL` candidate never reaches `select_research_lead()`'s `status is NEEDS_REVIEW` check (line 264) in the first place, so fixing §5/§7's Radar-path gate automatically closes this surface too, once/if it is ever wired live |
| `src/data_access/company_discovery/candidate_pipeline.py` | Dormant, reads raw `FilingEvent`s regardless of status | **Not fully closed by this fix** — it reads `FilingEvent` directly, not gated by `CandidateStatus` at all (§2g). Flagged as a residual risk, out of this task's narrow scope (see §14) |
| `tests/test_dart_rules.py` | Existing lexicon tests | Unchanged — `test_financing_keyword_matches_treasury_stock_disposal` should keep passing exactly as-is, since `evaluate_report_name()` itself is not being changed |
| `tests/test_ownership_materiality.py` | Existing gate tests (pattern to mirror) | Unchanged — reference for the new gate's own test file |
| New: `tests/test_equity_transaction_materiality.py` (or equivalent name matching the final module name) | New gate's tests | New — see §12 for exact fixtures |
| `tests/test_radar_pipeline.py` (confirm exact current file name at implementation time) | Promotion-decision integration tests | Extend with a Wonik-IPS-shaped end-to-end case |
| `tests/test_theme_evidence.py` | Existing theme-evidence tests | Extend with a suppressed-filing case |

## 8. Historical-data/backfill implications

**None required, and none proposed.** This fix changes only what happens to *newly scanned* filings going forward — it does not retroactively reclassify, hide, or delete any already-persisted `FilingEvent`/`CandidateSignal` record. A previously-promoted treasury-disposal candidate already sitting at `NEEDS_REVIEW` in the on-disk store remains exactly as it is (consistent with this codebase's own repeated "never silently rewrite persisted state" discipline, seen identically in the Daily News `identified_companies` work and the relationship-evidence design this same session already established). If a retroactive cleanup is ever wanted, it would be a separate, explicitly-approved, human-reviewed task — not an automatic consequence of shipping this fix.

## 9. False-positive and false-negative risk

- **False positive (suppressing something that should have surfaced)**: the escape hatch (§6) is the primary defense. The highest-risk failure mode is a genuinely material treasury transaction whose excerpt happens not to contain any of the marker terms and whose share-count-vs-outstanding math isn't extractable from the excerpt shape — this mirrors exactly the same, already-accepted risk `ownership_materiality.py`'s own docstring names for its threshold path ("do not assume a material change" when extraction fails) — i.e., an *ambiguous* case defaults to **not suppressed** (`NEEDS_REVIEW`), never the reverse, matching the existing fail-open-toward-review convention.
- **False negative (letting something through that should be suppressed)**: Layer 1's title-only check at `theme_evidence.py` cannot see excerpt content at all — a treasury-disposal filing whose title alone doesn't clearly signal "employee-directed" (many real DART titles are generic, e.g. bare `자기주식처분결정`) would still pass Layer 1 unless the classifier suppresses the *whole* `자기주식처분`-titled category by default (which this design recommends — see §4/§6: suppress by default, require an escape-hatch marker to rescue, never the reverse). The residual risk is therefore the opposite of the false-positive case: a small number of genuinely material treasury disposals (crossing the escape-hatch conditions) must be correctly rescued by title-level markers alone at the theme-feed layer, since no excerpt exists there — the Radar-path Layer 2 gate is strictly more accurate because it has the excerpt, so the theme-feed layer is deliberately the more conservative (more likely to under-suppress a borderline case) of the two, by architectural necessity, not oversight.

## 10. Test fixtures using this exact Wonik IPS filing

Proposed additions, matching this codebase's own existing fixture conventions exactly (`tests/test_dart_rules.py`'s real-title-substring style; `tests/test_ownership_materiality.py`'s real-vs-constructed labeling discipline). **All fixtures below are constructed** to match the facts given in this task — no real cached Wonik IPS filing exists locally (§0) — labeled as such, never presented as verified live data.

```python
# tests/test_equity_transaction_materiality.py (new file, mirrors
# tests/test_ownership_materiality.py's exact structure)

# Constructed to match the exact facts given for the Wonik IPS
# September 2026 filing (no real cached excerpt exists locally for
# this issuer — see design/DART_LOW_VALUE_FILING_SUPPRESSION_DESIGN_
# 2026_09_17.md §0/§10). Title uses the real, standardized DART shape
# already confirmed in tests/test_dart_rules.py.
_WONIK_IPS_TITLE = "주요사항보고서(자기주식처분결정)"
_WONIK_IPS_EXCERPT_CONSTRUCTED = (
    "자기주식처분결정 1. 처분예정주식 보통주식 51,456 2. 처분예정금액 6,140,000,000 "
    "3. 처분목적 임직원 성과급 지급을 위한 자기주식 처분 4. 처분방법 시간외대량매매 "
    "5. 처분예정기간 2026년 09월 17일 ~ 2026년 09월 18일"
    # No named commercial counterparty, contract, capex, capacity,
    # order/backlog, guidance, financial result, governance/control
    # change, product, technology, or strategic-transaction language
    # anywhere in the excerpt.
)


def test_wonik_ips_style_employee_directed_treasury_disposal_is_suppressed():
    result = assess_equity_transaction_materiality(_WONIK_IPS_TITLE, _WONIK_IPS_EXCERPT_CONSTRUCTED)
    assert result.outcome == "not_material"


def test_wonik_ips_style_filing_reaching_radar_pipeline_ends_at_not_material():
    # End-to-end shape, mirroring test_ownership_materiality's own
    # integration-style assertions against radar_pipeline.py.
    ...  # construct a CandidateSignal with the fixture title/excerpt above
    assert candidate.status == CandidateStatus.NOT_MATERIAL


def test_wonik_ips_style_filing_is_suppressed_from_theme_evidence():
    filing = FilingEvent(
        rcept_no="20260917000001", corp_code="01135941", corp_name="원익IPS",
        stock_code="240810", report_nm=_WONIK_IPS_TITLE, rcept_dt="2026-09-16",
        flr_nm="원익IPS",
    )
    links = recent_filing_evidence((filing,), frozenset({"Wonik IPS Co., Ltd."}), limit=10)
    assert links == ()


def test_same_title_with_a_controlling_shareholder_marker_is_not_suppressed():
    # Escape hatch: a title/excerpt naming a control-change marker must
    # never be suppressed, regardless of share count.
    excerpt_with_marker = _WONIK_IPS_EXCERPT_CONSTRUCTED + " 최대주주변경을 수반하는 처분"
    result = assess_equity_transaction_materiality(_WONIK_IPS_TITLE, excerpt_with_marker)
    assert result.outcome == "material_marker"


def test_genuine_large_capital_raise_is_never_routed_through_the_new_gate_at_all():
    # 유상증자/증권신고서 are capital-raise mechanisms, not employee-
    # directed housekeeping — must not be affected by this change at all.
    result = evaluate_report_name("주요사항보고서(유상증자결정)")
    assert result.confidence == "Moderate"  # unchanged behavior — this filing was never in scope
```

## 11. Safe implementation plan — no unrelated refactors

1. **Phase 1 (additive, isolated)**: add the new §5 Layer 1 classifier and Layer 2 materiality module as new, standalone functions/files, with their own new test file(s), touching zero existing production logic. Fully reviewable and testable in isolation, exactly like `ownership_materiality.py` itself was.
2. **Phase 2 (one narrow call-site change per surface)**: wire Layer 2 into `radar_pipeline.process_candidate()` at the exact existing `_is_ownership_change_candidate()` branch point — a small, additive `elif` alongside the existing check, not a rewrite of that function. Wire Layer 1 into `theme_evidence.recent_filing_evidence()` as one additional filter predicate in its existing list comprehension. Each is a single-purpose, independently revertable change.
3. **Phase 3 (dormant-surface parity, optional/deferred)**: apply the identical Layer 1 check inside `dart_filing_candidate_adapter.py`, pre-emptively, before that adapter is ever wired live — cheap insurance, no behavior change today since nothing calls it.
4. **Explicitly not touched**: `dart_rules.py`'s own lexicon/`evaluate_report_name()` output shape; `KOREAN_KEYWORD_LEXICON`'s existing category structure; `CandidateStatus`/`FilingEvent`/any model field; any UI page's rendering logic beyond the one new filter predicate; the research-lead pipeline (closed automatically by the Radar-path fix, per §7); Company Discovery's `candidate_pipeline.py` (flagged, not fixed — see §14).

## 12. Explicit non-goals

Not touching `src/data_access/company_discovery/candidate_pipeline.py`'s own unconditional `_FILING_SOURCES` read (a genuine residual risk, explicitly out of this narrow report's scope — it reads raw `FilingEvent`s regardless of `CandidateStatus`, so this fix alone does not close it); not building the research-lead orchestration's live worker wiring (Step 4B-2, not this task's concern); not resolving whether `allowed_source_names` should ever include DART; not touching the Radar Inbox UI's own rendering/filter logic (the fix belongs upstream of it, per §3); not backfilling or reclassifying any historical record (§8); not building an alerting system (§2h — none exists, and none was asked for); not changing `ownership_change`'s own existing gate.

## 13. Files inspected

`src/data_access/dart/dart_rules.py`, `ownership_materiality.py`, `radar_pipeline.py`, `scan_service.py`; `src/models/models.py` (`FilingEvent`, `CandidateStatus`); `src/ui/pages/radar_inbox.py`; `src/ui/components/radar_status.py`, `radar_card.py`; `src/logic/theme_evidence.py`; `src/ui/pages/themes_research.py`; `src/data_access/daily_news/dart_filing_candidate_adapter.py`; `src/logic/research_lead_selection.py`, `research_lead_factory.py`, `research_lead_orchestration.py`; `src/data_access/company_discovery/candidate_pipeline.py`, `extraction_rules.py` (header only), `entity_resolution.py` (header only); `src/config/tracked_companies.py` (Wonik IPS entry); `tests/test_dart_rules.py`, `tests/test_ownership_materiality.py`; local cache files `data/cache/dart_filing_events.json`, `dart_candidates.json`, `dart_corp_codes.json` (checked for a real Wonik IPS record — none found).

## Confirmations

- **Exact file changed**: one — this document, `design/DART_LOW_VALUE_FILING_SUPPRESSION_DESIGN_2026_09_17.md`, newly created and untracked. No other file was created, modified, or deleted.
- No worker, ingestion, discovery, backfill, or scan was run. No cache or database was accessed for writing — the three DART cache files listed above were opened read-only to check for an existing Wonik IPS record.
