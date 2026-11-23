# Tier 1 Company Cohort 2 — Implementation Readiness Review

Status: **read-only readiness review**. No code, test, registry, model, source, or worker file was modified. This document maps the exact, narrow changes a later, separately-approved implementation PR would make — it does not make them. Builds directly on `design/TIER1_COMPANY_COHORT2_VALIDATION_AND_IMPLEMENTATION_PLAN_2026_09_16.md`'s validated findings; nothing here re-litigates that validation.

## 0. Approved Cohort 2 candidates (confirmed, unchanged from the validation plan)

| Company | Ticker | Source | Theme |
|---|---|---|---|
| Taiwan Semiconductor Manufacturing Co. Ltd. | TSM | SEC EDGAR | ai-buildout |
| ASML Holding N.V. | ASML | SEC EDGAR | ai-buildout |
| Fabrinet | FN | SEC EDGAR | photonics |
| Ciena Corporation | CIEN | SEC EDGAR | photonics |
| Kulicke & Soffa Industries, Inc. | KLIC | SEC EDGAR | ai-buildout |
| Hewlett Packard Enterprise Company | HPE | SEC EDGAR | ai-buildout |
| Cognex Corporation | CGNX | SEC EDGAR | humanoids |
| Viasat, Inc. | VSAT | SEC EDGAR | space |

## 1. Production changes — the eight issuers, minimum-necessary metadata only

### 1.1 `src/config/tracked_companies.py`

Eight new `TrackedCompany(...)` entries appended after the existing "Tier 1 supply-chain coverage cohort" batch, following that batch's own exact conventions: `source="SEC EDGAR"`, `corp_code=None` (never hardcoded — resolved lazily via `with_resolved_ciks()`, same as every other EDGAR entry), CIKs found during validation recorded in `notes` only for audit-trail purposes, never as a structured field.

Per the existing, tested convention already governing every ambiguous-subtheme case in the registry (Applied Materials/Lam Research/KLA/Entegris/Nebius/Penguin Solutions/Marvell/MaxLinear/Broadcom all left `subthemes=()` rather than inventing a new string), **no new subtheme string is proposed** — this satisfies "add only the minimum necessary metadata using the existing ontology... do not perform an ontology redesign" directly. Each entry's `notes` records an *informal* intended subtheme in prose only, mirroring the INDI/AIP/CEVA batch's own "informal supply-chain-layer classification (not a structured field)" pattern:

| Company | `themes=` | `subthemes=` | Informal intended subtheme (notes only) |
|---|---|---|---|
| TSM | `("ai-buildout",)` | `()` | foundry-fabrication |
| ASML | `("ai-buildout",)` | `()` | semiconductor-equipment (EUV lithography) |
| FN | `("photonics",)` | `()` | optical-packaging-assembly |
| CIEN | `("photonics",)` | `()` | optical-networking-systems |
| KLIC | `("ai-buildout",)` | `()` | advanced-packaging (wire-bonding) |
| HPE | `("ai-buildout",)` | `()` | — (no subtheme fits, same as Dell/Super Micro's own existing entries) |
| CGNX | `("humanoids",)` | `()` | sensors-vision |
| VSAT | `("space",)` | `()` | satellite-communications |

No company carries a second theme — consistent with the registry's own conservative no-speculative-bridge convention (Broadcom, Astera Labs both declined a plausible second theme; TSMC's genuine ai-buildout/memory/photonics relevance is noted in prose only, per the validation plan's own §1.1).

### 1.2 `src/config/issuer_registry.py`

**No code change.** `SEED_ISSUERS` is generated programmatically from `TRACKED_COMPANIES` (confirmed by direct read of the module docstring and by `tests/test_issuer_registry.py::test_seed_issuer_count_matches_current_tracked_companies_registry`, which is not hardcoded and will pass automatically once §1.1 lands) — no hand-edit is needed or correct here. `DISCOVERY_STUBS`'s existing `stub:HPE` entry is **left untouched**, matching the already-established precedent for Cisco/Arista/Quanta/nVent's own now-redundant stubs (the prior audit's own finding: "left untouched... flagged for a future cleanup decision").

## 2. `tests/test_issuer_registry.py` — stale count correction

### 2.1 Root cause

The Tier 1 Cohort 1 batch (commit `1341ea1`, PR #59, 2026-09-15) added 10 companies to `tracked_companies.py` (105 → 115) and updated `tests/test_tracked_companies.py` accordingly (`git show 1341ea1 --stat` confirms that file was touched, 285 lines added) — but **did not touch `tests/test_issuer_registry.py` at all**. That file's hardcoded `== 105` assertions and its hardcoded 18-entry EDINET identifier list were never updated to reflect the 3 new EDINET entries (Nabtesco, Harmonic Drive Systems, Yaskawa Electric) or the 10-company total added by that same batch. This is confirmed as a real, currently-failing, pre-existing condition — not a hypothesis — by directly running `pytest tests/test_issuer_registry.py -q` against the current, unmodified working tree: **6 of 36 tests fail today**, independent of and prior to any Cohort 2 work.

### 2.2 Every stale hard-coded count, with exact line numbers, current/expected values, and preserved intent

| Test function | Line(s) | Current value | Why stale | Expected value after this PR | Intent preserved |
|---|---|---|---|---|---|
| `test_seed_issuer_count_is_105_after_the_edinet_filings_radar_expansion_batch` | 233 | `== 105` | Never updated after Tier 1 Cohort 1 added 10 more | `== 123` (115 current + 8 Cohort 2) | Pins the exact `SEED_ISSUERS` total after the most recent batch — same intent as every prior version of this test (the function's own name has been renamed at each batch already, per its own comment: "renamed and updated... after Gate 7.1's own established discipline for a stale count/name"). **Rename** to `test_seed_issuer_count_is_123_after_the_tier1_cohort2_batch` (or equivalent), continuing that exact established naming discipline, and update the in-function comment's running arithmetic (105 + 10 [Cohort 1] + 8 [Cohort 2] = 123) the same way every earlier version of this comment already does. |
| `test_edinet_identifiers_survive_migration_exactly` | 56–81 (assertion at 81) | `len(edinet_issuers) == 18` | Missing the 3 EDINET entries Tier 1 Cohort 1 added (Nabtesco E01726, Harmonic Drive Systems E01712, Yaskawa Electric E01741 — codes per the validation-plan research pass) | `== 21`, plus 3 new explicit `assert edinet_issuers["..."].identifiers["EDINET"] == "..."` lines, one per new company, in the exact same style as the existing 18 | Preserves the file's "hardcoded identifier survives migration" lossless-migration proof for every EDINET entry individually, not just a count — the existing pattern already does this per-company, not just in aggregate; the fix must add named assertions, not just bump the integer. **Unaffected by Cohort 2 itself** (Cohort 2 adds zero EDINET entries) — this fix corrects pre-existing Cohort-1-era staleness, bundled into the same PR only because it lives in the same file and the same root cause (an untouched-since-Cohort-1 file) applies. |
| `test_tracked_company_and_seed_issuer_counts_are_unaffected_by_quanta` | 299–300 | `== 105` (×2) | Same root cause | `== 123` (×2) | Preserves "Quanta's own addition, considered in isolation, didn't change these totals differently than any other batch" — the count itself simply needs to track the registry's current total, which this test was never designed to hardcode against a single batch, only to stay in sync (see its own comment's running arithmetic, which must be extended by "+ 10 [Tier 1 Cohort 1] + 8 [Tier 1 Cohort 2] = 123"). |
| `test_tracked_company_and_seed_issuer_counts_are_unaffected_by_nvent` | 345–346 | `== 105` (×2) | Same root cause | `== 123` (×2) | Same as above, for the nVent-specific version of this test. |
| `test_tracked_company_and_seed_issuer_counts_are_unaffected_by_arista_and_cisco` | 392–393 | `== 105` (×2) | Same root cause | `== 123` (×2) | Same as above, for the Arista/Cisco-specific version. |
| `test_tracked_company_and_seed_issuer_counts_are_unaffected_by_hpe` | 434–435 | `== 105` (×2) | Same root cause, **plus** this test's own premise changes independently — see §2.3 | See §2.3 — this test needs more than a number update | See §2.3. |

Every `>= N` `DISCOVERY_STUBS` assertion (lines 259, 278, 331, 374) remains correct and untouched — `DISCOVERY_STUBS` has 26 entries today, all four inequalities (`>= 21/22/23/25`) still hold, and Cohort 2 does not touch `DISCOVERY_STUBS` (HPE's stub is left in place, per §1.2). These are not stale and require no edit — flagged here only to show they were checked, not overlooked.

### 2.3 The HPE-specific graduation problem — a required test *rewrite*, not just a number fix

`test_tracked_company_and_seed_issuer_counts_are_unaffected_by_hpe` (line 431) and, more importantly, `test_hpe_is_structurally_excluded_from_tracked_companies_from_issuer_registry` (line 438) currently assert HPE is **stub-only** — `"HPE" not in compat_tickers` and `"Hewlett Packard Enterprise Company" not in edgar_names`. Once Cohort 2 adds HPE to `TRACKED_COMPANIES` (§1.1), both of these become **factually wrong**, not just numerically stale. This requires:

- **Rewrite** `test_hpe_is_structurally_excluded_from_tracked_companies_from_issuer_registry` to assert the *opposite* — mirroring the exact, already-established pattern `test_quanta_is_now_also_a_real_tracked_company_via_the_core_expansion_batch` / `test_nvent_is_now_also_a_real_tracked_company_via_the_core_expansion_batch` / `test_arista_and_cisco_are_now_also_real_tracked_companies_via_the_core_expansion_batch` already use for exactly this "stub graduates to real tracked company" transition. Rename to something like `test_hpe_is_now_also_a_real_tracked_company_via_the_tier1_cohort2_batch`.
- **Update** `test_discovery_stubs_never_appear_in_compatibility_output` (line 113–130): its `_known_redundant_tickers = {"PWR", "NVT", "ANET", "CSCO"}` set must add `"HPE"` — otherwise this test will newly fail, since HPE's stub ticker will now legitimately overlap the real tracked-company list, exactly like the four already-documented exceptions.
- **Update** `test_get_tracked_companies_for_source_is_untouched_by_discovery_stubs` (line 148–163): its `_known_redundant_names` set must add `"Hewlett Packard Enterprise Company"`, same reasoning.
- `test_discovery_stubs_includes_hpe_with_the_expected_fields` (line 420) requires **no change** — HPE's own `DISCOVERY_STUBS` entry is left untouched (§1.2), so this test's assertions about the stub's own fields remain true regardless of graduation.

## 3. Narrow TSMC monthly-revenue 6-K promotion path

### 3.1 Exact function and file

`src/data_access/edgar/edgar_rules.py`, `evaluate_six_k()` (lines 257–284), gated by the module-level tuple `_SIX_K_MATERIAL_CONTENT_TERMS` (lines 181–186). This is the sole, general-purpose, filer-agnostic allow-list every 6-K filing from every foreign-private-issuer filer is checked against — there is no per-company branch anywhere in this module, and this plan does not propose adding one.

### 3.2 Real, verified TSMC filename evidence (this session, live, 2026-09-16)

- `tsm-revenue20260910.htm` — TSMC's recurring monthly revenue disclosure, filed 2026-09-10 (CIK 0001046179, accession 0001046179-26-000658). **Would not promote today** — "revenue" is not in `_SIX_K_MATERIAL_CONTENT_TERMS`.
- `tsm-agmx20260604x6k.htm` — a real TSMC AGM notice (accession 0001046179-26-000302, filed 2026-06-04). Correctly denied today (matches `agm`) — must remain denied.
- `sonysemiconductorsolutions.htm` — a real, unrelated TSMC 6-K filename (accession 0001046179-26-000539, filed 2026-08-11) — content unknown from the filename alone, but a clean, real negative fixture: it does not and must not match "revenue" or any other current term.
- `a2026q2consolidatedreport-.htm` / `tsm-fsx20260814x6k.htm` — TSMC's quarterly consolidated financial report (accession 0001046179-26-000541, filed 2026-08-14). **Noted but explicitly out of scope**: this filename also does not match any current allow-list term (no "quarterly" or "financialresults" substring — TSMC's own naming convention differs from ASML's, which the existing terms were tuned against). This plan does **not** propose a fix for this filing type — only the monthly-revenue pattern the task specifically named. Flagged here so a future, separately-scoped pass doesn't need to re-discover it.

### 3.3 Proposed narrowest change

Add exactly one new entry to `_SIX_K_MATERIAL_CONTENT_TERMS`:

```python
_SIX_K_MATERIAL_CONTENT_TERMS: tuple[str, ...] = (
    "quarterly", "annualreport", "financialresults",
    "earnings", "guidance", "investorday", "investorpresentation",
    "acquisition", "merger", "transaction", "financing", "contract",
    "capacity", "expansion",
    "revenue",  # new
)
```

This is a **single-line data addition to an existing, general-purpose, already-curated table** — not a new function, not a TSMC-specific code path, not a new gate. It is the narrowest possible mechanism available in the current architecture, consistent with how every existing term was added (a real word, ≥6 characters — "revenue" is 7 — directly observed in real filer evidence, exactly the same evidentiary bar `quarterly`/`annualreport` were added under for ASML). Checked for collision against every existing allow-list and deny-list term: no substring overlap in either direction.

**Risk assessment — why this doesn't broadly admit unrelated foreign-issuer 6-K content**: "revenue" is a substantive financial term, not a generic one — the same reasoning that rejected "financial"/"results"/"order" (too generic/ambiguous to prove materiality alone, per the module's own "6-K allow-list final review" history) argues *for* keeping "revenue," since a 6-K filename containing "revenue" is inherently about a company's revenue disclosure, not a plausible false positive the way a bare "results" could match an AGM vote tally. No evidence gathered this session suggests any other tracked or candidate foreign-issuer filer uses "revenue" in a routine/non-material filename.

### 3.4 Preserved behavior (unaffected by this change)

- **20-F annual report**: handled entirely by `evaluate_form_type()`/`FORM_TYPE_CATEGORIES["20-F"]`, a completely separate code path from `evaluate_six_k()` — untouched by this proposal, verifiable by inspection (no code path connects the two).
- **AGM behavior**: the deny-list (`_SIX_K_ROUTINE_CONTENT_TERMS`, checked first, always wins) is untouched — `tsm-agmx20260604x6k.htm` and every other AGM-pattern filename remain denied regardless of the new term.
- **Every other foreign-issuer filer** (ASML, Arm Holdings, and any future 20-F/6-K filer): none of their already-tested real filenames (`form6-kquarterlyfilings.htm`, `form6-kannualreportbasedon.htm`, `form6-kagmdisclosureofagmr.htm`, `arm-20260910.htm`) contains "revenue" — all existing `tests/test_edgar_rules.py` assertions for these remain valid, unchanged, and are the exact regression proof this change must not break.

## 4. Required validation design

### 4.1 `tests/test_tracked_companies.py` — new `test_cohort2_*` block (mirrors the existing `test_tier1_cohort1_*` block's exact shape)

- `test_cohort2_present_exactly_once_each_and_active` — all 8 tickers appear exactly once, `active=True`.
- `test_cohort2_legal_names` — exact legal name per ticker.
- `test_cohort2_source_routing_matches_the_correct_adapter` — all 8 are `source="SEC EDGAR"`.
- `test_cohort2_corp_code_not_hardcoded` — `corp_code is None` for all 8.
- `test_cohort2_themes_use_only_existing_primary_themes` — each of the 8's `themes` is a subset of `ontology.PRIMARY_THEMES`, and non-empty.
- `test_cohort2_subthemes_are_left_unset` — `subthemes == ()` for all 8 (per §1.1's minimum-metadata decision — a stronger, more specific assertion than "reuse existing vocabulary or stay unset," since this batch deliberately never reuses).
- `test_cohort2_no_duplicate_identifiers_against_existing_registry` — no `krx_code`/`corp_code`/`name` collision across the full, live registry (mirrors `test_tier1_cohort1_no_duplicate_identifiers_against_existing_registry`).
- `test_cohort2_no_krx_or_corp_code_collision_with_existing_registry` — programmatic collision proof across all 123 entries (mirrors `test_tier1_cohort1_no_krx_or_corp_code_collision_with_existing_registry`).
- `test_cohort2_no_alias_collision_with_existing_companies` — mirrors `test_tier1_cohort1_no_alias_collision_with_existing_companies`, via `build_company_alias_entries()`.
- `test_cohort2_hpe_graduates_from_discovery_stub` — **new, not mirrored from Cohort 1** (Cohort 1 had no stub-graduation case): asserts HPE's `krx_code`/ticker is present in `get_tracked_companies()`'s real output, distinct from and in addition to `test_cohort2_not_previously_present_as_a_discovery_stub`'s logic for the other 7.
- `test_cohort2_not_previously_present_as_a_discovery_stub` — for the 7 non-HPE candidates only (TSM, ASML, FN, CIEN, KLIC, CGNX, VSAT), mirroring `test_tier1_cohort1_not_previously_present_as_a_discovery_stub`'s logic; HPE is deliberately excluded from this assertion since it *is* expected to already exist as a stub.
- `test_active_tracked_company_count_is_exactly_115` (existing, line ~269) — **update** to `test_active_tracked_company_count_is_exactly_123`, value `== 123`.
- `test_pre_existing_115_companies_retain_prior_behavior_after_tier1_cohort2_batch` — **new**, mirroring the existing `test_pre_existing_105_companies_retain_prior_behavior_after_tier1_cohort1_batch`'s exact spot-check shape (Samsung, SK Hynix, NVIDIA, Rocket Lab, FANUC identity fields unchanged), extended to also spot-check 2–3 Tier 1 Cohort 1 names (e.g. Hanmi Semiconductor's `krx_code`, GE Vernova's theme) to prove Cohort 2 didn't perturb Cohort 1 either — this is the explicit "preservation of all existing Cohort 1 issuers" test the task requires.

### 4.2 `tests/test_issuer_registry.py` — exact edits per §2

All six edits from §2.2/§2.3, applied in place (renames, value updates, the EDINET-identifier-list extension, and the HPE-graduation test rewrite plus the two `_known_redundant_*` set updates).

### 4.3 `tests/test_edgar_rules.py` — new tests for §3

- `test_material_6k_revenue_filename_is_admitted` — real TSMC filename `tsm-revenue20260910.htm`; asserts `confidence == "Moderate"` and `matched_rules == ("foreign_issuer_current_report:6-K:revenue",)`. **Positive fixture.**
- `test_tsmc_agm_filename_is_rejected` — real TSMC filename `tsm-agmx20260604x6k.htm`; asserts `confidence is None`. **Regression proof that TSMC's own AGM filings, not just ASML's, remain correctly denied.**
- `test_unrelated_tsmc_6k_filename_is_rejected` — real TSMC filename `sonysemiconductorsolutions.htm`; asserts `confidence is None`. **Negative fixture for an unrelated 6-K attachment**, proving the new "revenue" term does not overreach.
- No change needed to `test_opaque_arm_style_6k_filename_is_rejected_in_phase_1` (Arm's real filename contains no "revenue" substring) or to `test_material_6k_quarterly_filename_is_admitted`/`test_material_6k_annual_report_filename_is_admitted`/`test_routine_6k_agm_disclosure_filename_is_rejected` (ASML's own fixtures) — running the existing file unmodified except for the new term and the three new tests above **is itself the "unchanged non-TSMC 6-K eligibility behavior" proof** the task requires; no existing assertion's expected value changes.
- No new test needed for 20-F preservation — `test_20f_admitted...`-style existing test(s) already prove `FORM_TYPE_CATEGORIES["20-F"]` is issuer-agnostic and untouched by this change (§3.4).

### 4.4 Cross-cutting / no-unintended-behavior-change proof

- `pytest tests/ -k "edgar and not radar" -q` and the full targeted sweep in §6 below are the mechanism for "no unintended worker, polling, persistence, or source-registry behavior change" — no worker, scan-service, cache, or database code is touched by any change in §1–§3, so the existing test suite for those areas (already passing) is the regression proof; no *new* test is needed to prove a negative for code that was never edited. This is stated explicitly here rather than assumed, per the task's own requirement.

## 5. Explicit exclusions (confirmed, none proposed anywhere in this plan)

China/Taiwan local-exchange ingestion beyond the two SEC-addressable issuers (TSM/ASML's ADRs, both already EDGAR-native, not a new adapter); private-company tracking; relationship-graph activation; second-order automated Signal generation; ontology/data-model/schema changes (§1.1's "no new subtheme" decision is exactly this constraint applied); broad taxonomy cleanup; Daily News UI or Dashboard changes; source/polling/worker/CLI changes; cache/database writes or backfills; deployment changes.

## 6. Exact anticipated production file list

| File | Change |
|---|---|
| `src/config/tracked_companies.py` | Add 8 `TrackedCompany` entries (§1.1) |
| `src/data_access/edgar/edgar_rules.py` | Add one entry (`"revenue"`) to `_SIX_K_MATERIAL_CONTENT_TERMS` (§3.3) |

No other production file. `src/config/issuer_registry.py` needs no edit (§1.2).

## 7. Exact anticipated test file list

| File | Change |
|---|---|
| `tests/test_tracked_companies.py` | New `test_cohort2_*` block (11 new tests) + update `test_active_tracked_company_count_is_exactly_115` → `..._123` + new preservation test (§4.1) |
| `tests/test_issuer_registry.py` | Fix 5 stale `== 105` counts → `== 123` (one rename), fix the 18→21 EDINET identifier list (+3 named assertions), rewrite the HPE-exclusion test into an HPE-graduation test, update 2 `_known_redundant_*` sets to add HPE/"Hewlett Packard Enterprise Company" (§2) |
| `tests/test_edgar_rules.py` | 3 new tests for the `"revenue"` term (§4.3) |
| `tests/test_company_aliases.py` | No source change anticipated — confirm via a run whether an analogous new alias-collision test is warranted for the 8 new names (mirrors `test_tier1_cohort1_no_alias_collision_with_existing_companies`, which itself required no change to `company_aliases.py`, only a new test in `test_tracked_companies.py` — see §4.1's own alias-collision test, which already covers this without touching this file) |

## 8. Proposed commit boundaries (one coherent PR, preferred)

A single PR, three commits within it, matching this repository's own established convention of one focused commit per logical concern within one PR (the Tier 1 Cohort 1 PR itself was one commit; this plan proposes splitting Cohort 2 into three for reviewability, since it bundles a genuinely separate pre-existing bug fix):

1. **"Fix stale tracked-company counts in test_issuer_registry.py"** — §2's six edits only, against the *current* 115-company registry (temporarily asserting 115, not 123) — a standalone, pre-Cohort-2 bug fix that would pass on `main` today, before any Cohort 2 code exists. Isolates the pre-existing-bug fix from the new-feature addition for review clarity.
2. **"Add Tier 1 Cohort 2 public issuers"** — §1.1's 8 new `TrackedCompany` entries + §4.1's new test block + updating every count this commit itself changes (105/115 → 123, including re-touching the numbers commit 1 just fixed to 115) + §4.2's HPE-specific rewrite (only meaningful once HPE is actually added).
3. **"Add TSMC monthly-revenue 6-K promotion term"** — §3.3's one-line `_SIX_K_MATERIAL_CONTENT_TERMS` addition + §4.3's 3 new tests. Independent of commits 1–2 (touches a different file, no shared state) — could also be its own separate, smaller PR if preferred, but is small enough to bundle into the same PR as an additional commit without harming reviewability.

If a single commit is preferred instead, the same three groupings still apply as the natural "hunks" of one combined diff.

## 9. Exact test commands

```bash
pytest tests/test_tracked_companies.py -q
pytest tests/test_issuer_registry.py -q
pytest tests/test_edgar_rules.py -q
pytest tests/test_company_aliases.py -q
pytest tests/ -k "tracked_companies or issuer_registry or edgar_rules or company_aliases" -q
git diff --check
```

## 10. Risk register and mitigations

| Risk | Mitigation |
|---|---|
| The `test_issuer_registry.py` fix (§2) is bundled with new-feature work and reviewers conflate "why is this pre-existing bug being fixed here" with the Cohort 2 change itself | Commit-boundary 1 (§8) isolates it as its own commit with its own message explaining the pre-existing, unrelated root cause; the PR description should say so explicitly, mirroring how this report itself states it |
| `"revenue"` as a new allow-list term is broader than a TSMC-only fix, since the gate is filer-agnostic by design | §3.3's risk assessment already reasons through this; no mitigation beyond documenting the reasoning is proposed, since a per-filer carve-out would be a larger, more invasive, architecturally-inconsistent change this plan explicitly avoids |
| A future filer (not yet tracked) could have a routine, non-material filing whose filename happens to contain "revenue" in an unintended way (e.g. a name change, a subsidiary divestiture titled "...revenue-impact...") | Low-probability, and symmetric with the risk every existing term already carries (e.g. "expansion," "capacity") — no new class of risk introduced; if discovered in a future filer's real data, remove or narrow the term the same way "financial"/"results"/"order" were previously narrowed (§3.3's own precedent) |
| CIKs recorded in `notes` (per §1.1) could go stale between this report and actual implementation | Explicitly flagged in the validation plan's own pre-implementation checklist (re-verify all 8 CIKs live at implementation time, not just trust this report) — carried forward here, not re-solved |
| Ciena's CIK was never independently re-pulled this session (validation plan §2) | Same mitigation — implementation must pull it live before drafting that entry, not reuse a doc-stated value uncritically |
| Adding "revenue" could theoretically be interpreted as scope creep beyond "the eight issuers" | It is explicitly named as required work by the task itself (item 4 of "Required readiness analysis") — included deliberately, not as drift |

## 11. Pre-implementation checklist

- [ ] Re-verify all 8 CIKs (TSM 0001046179, ASML 0000937966, KLIC 0000056978, CGNX 0000851205, FN 0001408710, VSAT 0000797721 already live-confirmed; HPE and CIEN CIKs not yet independently pulled — do so) against live SEC EDGAR data at implementation time.
- [ ] Confirm TSMC's `tsm-revenue20260910.htm` filename convention is still current (re-pull TSMC's most recent 6-K filing list) — filing conventions can change.
- [ ] Confirm the exact current line numbers/content of every test named in §2/§4 before editing (this report cites them as of 2026-09-16; they may have shifted if any other change lands on `main` first).
- [ ] Decide the exact PR/commit-boundary structure (§8) with whoever reviews — this report recommends 3 commits in 1 PR but the choice is not itself a blocker.
- [ ] Confirm no other file references the literal count `105` or `115` for `TRACKED_COMPANIES`/`SEED_ISSUERS` outside the two files already identified in this report and the prior validation plan (a fresh `grep -rn "== 105\|== 115\|exactly_115" tests/ src/` immediately before implementation is cheap insurance against a missed reference).

## 12. Confirmation that no implementation has occurred

No file under `src/`, `tests/`, `config/`, or `data/` was created, modified, or deleted by this task. No `TrackedCompany` entry, test function, or allow-list term was actually added — every code block in §1–§4 is a proposal, quoted for precision, not applied. No worker, discovery, ingestion, or live scan was run. No cache or database state was written. Nothing was staged, committed, pushed, opened as a PR, merged, deployed, or stashed. No existing untracked artifact was altered.

## 13. Files inspected

- `design/TIER1_COMPANY_COHORT2_VALIDATION_AND_IMPLEMENTATION_PLAN_2026_09_16.md` (this task's own stated starting point)
- `src/config/tracked_companies.py` (Tier 1 Cohort 1 batch section, for exact addition-convention precedent)
- `src/config/issuer_registry.py` (module docstring, `SEED_ISSUERS`/`DISCOVERY_STUBS` generation logic)
- `tests/test_tracked_companies.py` (full `test_tier1_cohort1_*` block and `test_active_tracked_company_count_is_exactly_115`, for exact mirroring)
- `tests/test_issuer_registry.py` (full file, all 450 lines, read in full; also run live via `pytest tests/test_issuer_registry.py -q` against the current, unmodified working tree to confirm the 6 pre-existing failures directly rather than infer them)
- `src/data_access/edgar/edgar_rules.py` (`evaluate_six_k()`, `_SIX_K_ROUTINE_CONTENT_TERMS`, `_SIX_K_MATERIAL_CONTENT_TERMS`, `evaluate_form_type()`, `FORM_TYPE_CATEGORIES`)
- `tests/test_edgar_rules.py` (the full existing `evaluate_six_k`/6-K allow-list test section, for exact mirroring and to confirm real fixture precedent, e.g. the pre-existing `arm-20260910.htm` negative fixture)
- Git history: `git show 1341ea1 --stat` (to confirm exactly which test files the Tier 1 Cohort 1 commit did and did not touch)
- Live, read-only SEC EDGAR lookups (this session, 2026-09-16): filing-index directory listings for three specific additional TSMC accession numbers (`000104617926000539`, `000104617926000541`), to gather the `sonysemiconductorsolutions.htm` and `tsm-fsx.../a2026q2consolidatedreport-.htm` real filename evidence used in §3.2

## 14. Only-report-file confirmation

This task created exactly one new file — `design/TIER1_COHORT2_IMPLEMENTATION_READINESS_2026_09_16.md`. No other file was created, modified, or deleted.

## 15. `git status --short`

Unchanged from before this task except for the one new untracked file above — every other entry is a pre-existing untracked design doc or `tests/test_radar_status.py`, none of which this task touched. No tracked file shows any modification.

## 16. Stash status

Unchanged — the same 3 pre-existing stash entries as every prior report in this session documented, none accessed or modified by this task.
