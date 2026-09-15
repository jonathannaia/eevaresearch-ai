# EDGAR Foreign-Private-Issuer 20-F/6-K Admission — Design Document

Design and code-path audit only. **No file under `src/` or `tests/`
was edited to produce this document.** No registry, schema, worker,
Render configuration, or production data was touched. No live scan was
run.

## 1. The exact code path, traced end to end

```
EdgarClient.get_submissions(cik)
  → real GET https://data.sec.gov/submissions/CIK##########.json
  → scan_service.normalize_recent_filings(payload["filings"]["recent"])
      parses _REQUIRED_COLUMNS (accessionNumber, filingDate, form,
      primaryDocument) + _OPTIONAL_COLUMNS (primaryDocDescription, items)
      into one dict per row — no form-type filtering here.
  → scan_service.scan()'s row loop (scan_service.py:~277-300):
      for every row within the lookback window, not already seen:
        1. filing = _filing_event_from_row(row, company, scanned_at)
           new_filing_events.append(filing)      # UNCONDITIONAL —
             every row becomes a FilingEvent regardless of form type.
        2. evaluation = _evaluate_row(row)
             if form == "8-K": refine via items metadata
             else:             edgar_rules.evaluate_form_type(form)
        3. if evaluation.confidence is not None:
             new_candidate_signals.append(
               _candidate_signal_from_evaluation(filing, evaluation))
           # THIS is the entire admission decision for EDGAR. There is
           # no second gate.
  → edgar_rules.evaluate_form_type(form)
      normalized = normalize_form_type(form)      # alias resolution only
                                                    # (SCHEDULE 13D -> SC 13D
                                                    # etc.) — does NOT
                                                    # strip "/A" or map
                                                    # anything else new.
      if normalized == "8-K": ... (unreachable here, already handled above)
      category = FORM_TYPE_CATEGORIES.get(normalized)
      if category is None: return RuleEvaluation((), confidence=None)
        # <-- 20-F and 6-K land here today: zero entries in
        #     FORM_TYPE_CATEGORIES for either.
      return RuleEvaluation((f"{category}:{normalized}",), "Moderate")
  → CandidateSignal (when confidence is not None):
      status=CandidateStatus.CANDIDATE_DETECTED, confidence=evaluation.
      confidence, matched_rules=list(evaluation.matched_rules),
      flag_reason=build_flag_reason(matched_rules, confidence)
  → persisted via the scan's own cache/DB write (JSON cache or the
      sqlite/postgres CandidateRepository, depending on settings.
      db_backend) — same path every other source's CandidateSignal uses.
  → radar_inbox.py's _build_items() merges FilingEvent + CandidateSignal
      (matched by rcept_no) into one RadarItem per filing.
  → radar_card.py's candidate_row() renders it; radar_status.py's
      _STATUS_LABEL_OVERRIDES has no override for CANDIDATE_DETECTED, so
      its own enum value ("Candidate detected") is the label used unless
      a later status supersedes it as the candidate moves through the
      document-retrieval/extraction/translation lifecycle toward
      NEEDS_REVIEW or PUBLISHED.
```

### Where "materiality/precision gates" actually live — and where they don't

This document traced every gate that could plausibly apply and found
**exactly one** applies to EDGAR CandidateSignal admission: the
`FORM_TYPE_CATEGORIES` lookup above (plus 8-K's item-number
refinement, a distinct path). Three things that sound like they might
be a second gate are **not**, and this design does not touch them:

- **`editorial_admission.assess_admission()` /
  `materiality_classification.classify_editorial_story()`** — these
  are Daily News (editorial-lane) modules, scope-guarded away from
  Radar by `tests/test_daily_news_scope_guard.py`'s own "never modifies
  a radar-owned file" assertion (confirmed live in this repository's
  own test suite — this session did not re-run it, but the guard's
  existence and direction were confirmed by reading the file). Radar's
  `CandidateSignal` pipeline imports neither module.
- **`CandidateSignal.materiality_assessment`** — per its own field
  docstring in `src/models/models.py`: "a narrow, deliberately
  non-numeric note from the ownership-change materiality gate
  (`src/data_access/dart/ownership_materiality.py`) ONLY — never a
  general materiality score, and never set for any non-ownership
  candidate." This is DART-specific (Korea ownership-change filings)
  and structurally cannot apply to an EDGAR filing.
- **`CandidateSignal.confidence`** IS the real admission signal for
  EDGAR — Low/Moderate/High, purely a function of how many distinct
  rule categories matched (`edgar_rules._confidence_for`). This is
  exactly what `FORM_TYPE_CATEGORIES` feeds.

**Conclusion**: EDGAR's admission model is a single-stage,
form-type-keyed lookup — categorically simpler than Daily News's
two-stage match-then-admit design. A 20-F/6-K fix means adding entries
to that one lookup (plus, for 6-K, a new pre-check this document
proposes below) — it does not require building or touching a separate
"materiality" system, because none exists for EDGAR today.

---

## 2. Proposed mapping

### 20-F — unconditional, direct mapping (mirrors 10-K exactly)

```python
FORM_TYPE_CATEGORIES: dict[str, str] = {
    "10-Q": "earnings_or_results",
    "10-K": "earnings_or_results",
    "20-F": "earnings_or_results",   # NEW — foreign private issuer annual report
    ...
}
```

**Rationale**: 20-F is SEC's own designated annual-report equivalent
for foreign private issuers — always a comprehensive, substantive
filing, structurally never a "routine" or ambiguous document the way a
6-K can be (see Section 3). This mirrors 10-K's own existing,
unconditional treatment exactly — no new gate, no new logic, the
narrowest possible change. Confidence remains `"Moderate"`, same as
10-K/10-Q today.

**Amendment variant**: `20-F/A` is a standard SEC amendment suffix
(the same convention already producing `10-K/A`, which — a genuinely
unrelated, incidentally-discovered finding — **is also currently
unmapped in `FORM_TYPE_CATEGORIES` today**, confirmed by cross-checking
Tesla's own live filing history against the dict's keys. This document
does not propose fixing `10-K/A`'s gap — out of this design's scope —
but flags it explicitly per "mark any unresolved behavior explicitly."
For 20-F specifically: this document did not find a live `20-F/A`
filing for TSMC or ASML in the data fetched (both showed bare `20-F`
only), so whether to add it now, preemptively, or defer it until
observed is a genuinely open question — see Section 8.

### 6-K — conditional, content-gated mapping (NOT a direct 8-K mirror)

**This document's answer to "does 6-K need a separate evidence/content
gate": yes.** Unlike 10-K/10-Q/20-F (always substantive) or 8-K
(SEC provides structured item-number metadata at scan time via the
`items` column, already handled by `refine_8k_evaluation`), a 6-K is a
general-purpose "furnish anything a foreign issuer wants to disclose"
wrapper — real filing evidence gathered in this session (Section 4)
shows it covers everything from quarterly financial results to routine
Annual General Meeting disclosures, with **no SEC-provided structured
sub-classification at scan time analogous to 8-K's `items` column.**
Blanket-promoting every 6-K exactly like 8-K's unconditional
"Moderate" default would flood Radar with the same class of noise the
Daily News precision-first admission gate was built to eliminate for a
different pipeline — this design proposes the EDGAR-side equivalent,
scoped only to 6-K.

Proposed shape:

```python
# New: 6-K content-keyword gate, scan-time only (no document fetch).
# Deliberately narrow and evidence-derived (see Section 4) — a small,
# curated list of terms found in REAL, currently-filed TSMC/ASML/Arm
# 6-K primaryDocument filenames that plausibly indicate substantive
# financial/earnings content, mirroring dart_rules.py's own "keyword
# lexicon, built from real observed filer text, not guessed" discipline.
_SIX_K_MATERIAL_CONTENT_KEYWORDS: tuple[str, ...] = (
    "quarterly", "annualreport", "earnings", "interimreport", "results",
)
# Deliberately excluded, evidence-derived: an Annual General Meeting
# disclosure filename ("agmdisclosureofagmr...") is a real, observed
# 6-K shape that is routine governance/procedural content, not a
# corporate financial event — must never match the keywords above.

def evaluate_six_k(primary_document: str) -> RuleEvaluation:
    """Scan-time only — no document fetch. Fails closed: returns
    confidence=None (stays a bare FilingEvent, exactly like an
    unrecognized form today) unless the primaryDocument filename itself
    contains one of the curated material-content keywords. Never
    promotes on the form type alone."""
    filename = (primary_document or "").lower()
    hit = next((k for k in _SIX_K_MATERIAL_CONTENT_KEYWORDS if k in filename), None)
    if hit is None:
        return RuleEvaluation((), confidence=None)
    return RuleEvaluation((f"foreign_issuer_current_report:6-K:{hit}",), confidence="Moderate")
```

Wired into `_evaluate_row()` alongside the existing 8-K special case:

```python
def _evaluate_row(row: dict) -> edgar_rules.RuleEvaluation:
    form = row.get("form", "")
    if form.strip().upper() == "8-K":
        ... # unchanged
    if form.strip().upper() == "6-K":
        return edgar_rules.evaluate_six_k(row.get("primaryDocument", ""))
    return edgar_rules.evaluate_form_type(form)
```

**Why `primaryDocument` (the filename), not `primaryDocDescription`,
and why this is only a partial solution** — see Section 4's live
evidence and Section 7's explicit limitation.

**New category name, deliberately distinct from `earnings_or_results`**:
`foreign_issuer_current_report` — a 6-K-sourced signal should be
visibly distinguishable in `matched_rules`/`flag_reason` from a real
10-K/10-Q/20-F-sourced one, mirroring how 8-K already gets its own
distinct category (`material_event_8k_pending_items`) rather than
being folded into `earnings_or_results` even when it clearly is an
earnings 8-K (Item 2.02 gets its own `earnings_or_results:8-K item
2.02` tag via the item-refinement path, not a blind reuse of the
bare-form-type category) — the pattern this design follows is
"preserve provenance in the category string," already established.

---

## 3. What must remain unchanged (explicit contract)

- **Existing entity resolution** — `cik_resolver.py` is untouched by
  this proposal; it is already generic (ticker-based, works
  identically for domestic and foreign filers, confirmed in Phase 3).
- **Existing match/admission controls for every other form type** —
  `FORM_TYPE_CATEGORIES`'s existing 8 keys (`10-Q`, `10-K`, `SC
  13D`/`13D-A`/`13G`/`13G-A`, `S-1`, `S-3`, `424B1`–`424B5`) and the
  8-K special case in `_evaluate_row()`/`refine_8k_evaluation()` are
  **not modified** — only new keys/branches are added alongside them.
- **Materiality/precision rules** — none exist for EDGAR today (see
  Section 1); this proposal does not create a general one. The 6-K
  keyword gate is scoped exclusively to 6-K, never applied to any other
  form, and is explicitly a *narrower* admission bar than 6-K currently
  has (currently: never admitted at all) — it can only ever be equally
  or more conservative than today's behavior for every other form.
- **Publishing behavior** — `radar_card.py`, `radar_status.py`,
  `CandidateStatus` transitions, document retrieval/extraction/
  translation — none of this is touched. A 20-F/6-K-sourced
  `CandidateSignal` enters the exact same downstream lifecycle as any
  other, with no special-casing.
- **All U.S. domestic form handling** — byte-for-byte unchanged, as a
  direct consequence of only adding new dict keys/branches rather than
  modifying existing ones.

---

## 4. Live public-filing evidence gathered (primary source, this session)

All fetched from `data.sec.gov/submissions/CIK##########.json` —
SEC's own public submissions API, no API key required, no adapter code
invoked, no scan run. This is also the answer to "how to test current
public filing metadata... without querying production data or running
a live scan": fetch this URL directly for a known CIK and read the
`filings.recent` block — the exact same data
`EdgarClient.get_submissions()`/`normalize_recent_filings()` would
process, entirely external to this application.

| Company | CIK | Real recent 6-K `primaryDocument` examples | Real `primaryDocDescription` |
|---|---|---|---|
| ASML Holding N.V. | 0000937966 | `form6-kquarterlyfilings.htm`, `form6-kagmdisclosureofagmr.htm`, `form6-kannualreportbasedon.htm`, `form6-kinvestordaypresenta.htm` | Literally `"6-K"` for every row — **provides zero content differentiation** |
| Arm Holdings plc /UK | 0001973239 | `arm-20260910.htm`, `arm-20260421.htm`, `irannotice.htm` (a date-stamped or opaque convention) | Also literally `"6-K"` |
| TSMC | 0001046179 | Not retrieved with filenames in this session's fetch (form/date list only) | Not retrieved |

**Key finding**: `primaryDocDescription` is confirmed, empirically,
across two real foreign filers, to carry **no usable content signal**
— it is just the form type repeated. `primaryDocument` (the filename)
does carry real signal, but **only when the filer's own naming
convention is descriptive** — true for ASML ("quarterlyfilings" vs.
"agmdisclosureofagmr" are clearly distinguishable), **not true for
Arm** (date-stamped filenames like `arm-20260910.htm` carry no
content-type signal beyond the date, which is already captured
separately as `filingDate`). This is exactly what Section 7 flags as
this design's central, unresolved limitation.

**Arm Holdings — confirmed, not merely suspected, to be currently
affected**: live fetch shows real, current 6-K filings dated
2026-09-10, 2026-08-11, 2026-07-29 and a real 20-F dated 2026-05-26.
Since Arm is already a tracked company (per `tracked_companies.py`),
and `FORM_TYPE_CATEGORIES` has never had 20-F/6-K entries (confirmed by
repository-wide search in Phase 3, re-confirmed here), **every one of
these real, current Arm filings has silently failed to produce a
CandidateSignal** under the code as it exists on `origin/main` today.
This is now a directly evidenced production-behavior claim for Arm's
*filing existence*, not for what actually happened inside a live
deployment's own database — this document still does not query
production data or claim to know what's in this project's live
candidate store; it only establishes, from Arm's real public filing
history plus the unmodified code path, what *would* happen if that
code ran against these real filings.

---

## 5. Test fixtures and expected outcomes

Following `tests/test_edgar_rules.py`'s own established convention
(pure function calls, no mocking, `evaluate_form_type(...)` /
`RuleEvaluation` assertions):

```python
# --- New: 20-F ---
def test_20f_gets_earnings_category_directly():
    result = evaluate_form_type("20-F")
    assert result.matched_rules == ("earnings_or_results:20-F",)
    assert result.confidence == "Moderate"

# --- New: 6-K, material (quarterly filing, ASML's own real shape) ---
def test_material_6k_quarterly_filing_is_admitted():
    result = evaluate_six_k("form6-kquarterlyfilings.htm")
    assert result.confidence == "Moderate"
    assert result.matched_rules == ("foreign_issuer_current_report:6-K:quarterly",)

# --- New: 6-K, material (annual report, ASML's own real shape) ---
def test_material_6k_annual_report_is_admitted():
    result = evaluate_six_k("form6-kannualreportbasedon.htm")
    assert result.confidence == "Moderate"
    assert "annualreport" in result.matched_rules[0]

# --- New: 6-K, routine (AGM disclosure, ASML's own real shape) ---
def test_routine_6k_agm_disclosure_is_not_admitted():
    result = evaluate_six_k("form6-kagmdisclosureofagmr.htm")
    assert result.confidence is None
    assert result.matched_rules == ()

# --- New: 6-K, opaque/unlabeled filename (Arm's own real shape) ---
def test_6k_with_opaque_date_stamped_filename_is_not_admitted():
    """Documents the known limitation (Section 7) directly: a
    non-descriptive filename fails closed rather than guessing."""
    result = evaluate_six_k("arm-20260910.htm")
    assert result.confidence is None

# --- New: 6-K, missing/empty primaryDocument (defensive) ---
def test_6k_with_missing_primary_document_is_not_admitted():
    result = evaluate_six_k("")
    assert result.confidence is None
    result_none = evaluate_six_k(None)  # type: ignore[arg-type]
    assert result_none.confidence is None

# --- Controls: existing US domestic forms, byte-for-byte unchanged ---
# (these three already exist verbatim in tests/test_edgar_rules.py —
# reproduced here only to state explicitly that this design proposes
# NO change to their expected outcome)
def test_10k_gets_earnings_category_directly_UNCHANGED():
    result = evaluate_form_type("10-K")
    assert result.matched_rules == ("earnings_or_results:10-K",)

def test_10q_gets_earnings_category_directly_UNCHANGED():
    result = evaluate_form_type("10-Q")
    assert result.matched_rules == ("earnings_or_results:10-Q",)

def test_8k_gets_coarse_moderate_confidence_at_scan_time_UNCHANGED():
    result = evaluate_form_type("8-K")
    assert result.confidence == "Moderate"
    assert result.matched_rules == ("material_event_8k_pending_items:8-K",)
```

Also proposed: a `scan_service.py`-level integration fixture
confirming `_evaluate_row()` correctly routes a `{"form": "6-K",
"primaryDocument": "form6-kquarterlyfilings.htm"}` row to
`evaluate_six_k` (not `evaluate_form_type`), and that the resulting
`FilingEvent` is still created unconditionally regardless of the
routing outcome — proving the "always ingest, selectively promote"
contract holds for 6-K exactly as it already does for every other form.

---

## 6. Proposed changed-file list (for a later implementation — not applied here)

| File | Change |
|---|---|
| `src/data_access/edgar/edgar_rules.py` | Add `"20-F": "earnings_or_results"` to `FORM_TYPE_CATEGORIES`; add `_SIX_K_MATERIAL_CONTENT_KEYWORDS` constant and `evaluate_six_k()` function. |
| `src/data_access/edgar/scan_service.py` | Add the `form == "6-K"` branch to `_evaluate_row()`, routing to the new `evaluate_six_k()`. |
| `tests/test_edgar_rules.py` | Add the new fixtures in Section 5 (20-F direct mapping; 6-K material/routine/opaque/empty cases). |
| `tests/test_edgar_scan_service.py` (or wherever `_evaluate_row`/`scan()` integration is currently tested — not independently confirmed by filename in this session) | Add the integration fixture confirming correct routing and unconditional `FilingEvent` creation for a 6-K row. |

No other file. In particular: no change to `cik_resolver.py`,
`client.py`, `tracked_companies.py`, any Radar UI component, any Daily
News file, or any schema/migration.

---

## 7. Explicit, unresolved limitation

**The 6-K content gate's real-world effectiveness is filer-dependent,
and this document does not know how many of the 10 candidate/tracked
companies' own 6-K filenames are descriptive (ASML-shaped) versus
opaque (Arm-shaped) beyond the two directly checked here.** For a
filer using Arm's own date-stamped convention, the proposed keyword
gate will fail closed on essentially every 6-K — the same practical
outcome as today (nothing promoted), just now via a deliberate,
documented decision rather than an unexamined gap. This is judged an
acceptable, conservative starting point (consistent with this
codebase's "never guess, fail closed" discipline applied everywhere
else — DART's routine-exclude patterns, the Daily News consumer-format
gate, etc.), but it means the fix, as scoped here, **will not
immediately surface every genuinely material Arm 6-K** — only the ones
whose primaryDocument filename happens to be descriptive.

A natural, structurally-precedented future enhancement — explicitly
**out of this design's scope, not proposed for implementation now** —
would mirror 8-K's own two-stage design: a coarse scan-time pass (this
proposal) followed by a post-extraction refinement once the real
document text is fetched (analogous to `refine_8k_evaluation()` /
`merge_8k_item_evaluation()`), which could recover Arm-shaped opaque
filenames by reading the document's own title/heading text instead of
relying on the filename alone.

---

## 8. Other unanswered questions

1. **`20-F/A` amendment handling** — not observed live for TSMC/ASML in
   this session's evidence; whether to add it now (matching `10-K`'s
   own still-missing `10-K/A` gap, itself unfixed here) or wait for
   direct evidence is undecided.
2. **Exact location of `scan()`/`_evaluate_row()` integration tests** —
   this document references `tests/test_edgar_scan_service.py` as a
   plausible location for the proposed integration fixture but did not
   independently confirm that exact filename/module in this session;
   the implementer should confirm the real test file before writing
   the fixture.
3. **Whether `foreign_issuer_current_report` is the right category
   name**, versus reusing `earnings_or_results` for the "quarterly"/
   "annualreport"/"earnings"/"results" keyword hits specifically (this
   document chose provenance-preservation over category reuse,
   following the 8-K precedent, but this is a naming decision, not a
   technical constraint, and could reasonably go the other way).
4. **Whether any other tracked or candidate foreign private issuer**
   (beyond Arm, TSMC, ASML) uses a materially different 6-K filename
   convention that would change the keyword list's real-world hit
   rate — not surveyed here.
5. **Confidence level for the new 6-K category** — this proposal
   defaults to `"Moderate"` (matching 8-K's own coarse default), but
   given `_confidence_for()` upgrades to `"High"` only when ≥2 distinct
   categories match, and a single 6-K filing will essentially always
   produce exactly one category hit, every admitted 6-K would land at
   `"Moderate"` — whether that's the right ceiling (vs. deliberately
   capping it lower, e.g. treating foreign-issuer signals as
   structurally less certain than a domestic form) is a judgment call
   this document does not resolve.

---

## Sources checked

- **This repository's own code, read directly**: `src/data_access/
  edgar/edgar_rules.py` (`FORM_TYPE_CATEGORIES`, `EIGHT_K_ITEM_
  CATEGORIES`, `evaluate_form_type`, `refine_8k_evaluation`,
  `normalize_form_type`), `src/data_access/edgar/scan_service.py`
  (`scan()`, `_evaluate_row`, `_filing_event_from_row`,
  `normalize_recent_filings`), `src/data_access/edgar/cik_resolver.py`,
  `src/models/models.py` (`CandidateStatus`, `CandidateSignal`,
  `FlagReason`, `build_flag_reason`), `src/ui/components/radar_status.py`
  (`_STATUS_LABEL_OVERRIDES`), `tests/test_edgar_rules.py` (existing
  fixture conventions).
- **SEC EDGAR public submissions API** (`data.sec.gov/submissions/
  CIK##########.json`), fetched live 2026-09-15, no API key, no
  application code invoked: TSMC (CIK 1046179), ASML (CIK 937966), Arm
  Holdings (CIK 1973239) — confirming exact current 6-K/20-F filing
  dates, and for ASML and Arm specifically, real `primaryDocument`
  filenames and `primaryDocDescription` values.

## Unanswered questions

Listed in full in Section 8 above; headline items: whether `20-F/A`
needs handling now, the exact integration-test file location, the
category-naming decision, and the fact that the proposed 6-K gate's
real-world hit rate is confirmed to vary significantly by filer
(strong signal from ASML's filenames, near-zero signal from Arm's).

## Files read / created

**Read** (all read-only, no edits): the file list in "Sources checked"
above, plus `design/COVERAGE_EXPANSION_FIRST10_REGISTRY_READINESS_
2026_09_15.md` (this design's own originating context).

**Created**: `design/EDGAR_FOREIGN_PRIVATE_ISSUER_20F_6K_ADMISSION_
DESIGN.md` (this document).

No other file was read or written. No code, test, schema, or
production-data change was made. No live scan was run.

---

## Implementation Note — Phase 1 (implemented 2026-09-15)

The narrow Phase 1 implementation proposed above has been built, on
branch `feat/edgar-20f-6k-foreign-issuer-phase1`, exactly as scoped:

**In scope, implemented:**
- `20-F` added to `edgar_rules.FORM_TYPE_CATEGORIES`, mapped to
  `earnings_or_results` — the same, unconditional treatment `10-K`
  already has.
- A new `evaluate_six_k()` function in `edgar_rules.py`, wired into
  `scan_service._evaluate_row()` via a dedicated `form == "6-K"`
  branch (mirroring the existing 8-K special case's shape, not its
  logic) — never reachable through `FORM_TYPE_CATEGORIES`/
  `evaluate_form_type()`, so a bare 6-K form type alone can never
  promote a candidate.
- The gate is deny-list-first, then allow-list, both over
  `primaryDocument` (the filename) — never `primaryDocDescription`,
  confirmed non-informative in Section 4's live evidence.
- New category name `foreign_issuer_current_report`, distinct from
  `earnings_or_results`, preserving provenance in `matched_rules`
  exactly as 8-K's own item-level categories already do.

**Explicitly out of scope, not touched:**
- `20-F/A` (amendment) — falls through to the existing "unrecognized
  form type" behavior, same as today's separately-unresolved `10-K/A`
  gap (also not touched).
- Any document-text or exhibit inspection — the 6-K gate is
  filename-only, scan-time-only, exactly as designed.
- Historical backfill of any kind — this change only affects filings
  discovered by a *future* scan; no existing cached `FilingEvent`/
  `CandidateSignal` is reclassified retroactively.
- Adding TSMC, ASML, or any other company to `tracked_companies.py` —
  no registry, schema, source, worker, or Render configuration file was
  touched.

**Known Arm-style opaque-filename limitation — preserved exactly as
designed**: `evaluate_six_k()`'s own docstring and the module-level
comment above `_SIX_K_ROUTINE_CONTENT_TERMS` both state explicitly that
a date-stamped, non-descriptive filename (Arm Holdings' own real,
confirmed convention) will fail closed in Phase 1 — this is a known,
accepted, documented trade-off, not a bug, and is directly covered by
`test_opaque_arm_style_6k_filename_is_rejected_in_phase_1` and its
end-to-end counterpart in `test_edgar_scan_service.py`.

### Changed files

| File | Change |
|---|---|
| `src/data_access/edgar/edgar_rules.py` | Added `"20-F": "earnings_or_results"` to `FORM_TYPE_CATEGORIES`; added `_SIX_K_ROUTINE_CONTENT_TERMS`, `_SIX_K_MATERIAL_CONTENT_TERMS`, `_FOREIGN_ISSUER_CURRENT_REPORT_CATEGORY`, and `evaluate_six_k()`. |
| `src/data_access/edgar/scan_service.py` | Added a `6-K` branch to `_evaluate_row()`, routing to `edgar_rules.evaluate_six_k(row.get("primaryDocument", ""))`. |
| `tests/test_edgar_rules.py` | Added 11 new tests: 20-F direct mapping; material 6-K (quarterly, annual-report); routine 6-K rejection (AGM); opaque Arm-style rejection; unknown-filename rejection; missing-filename rejection; deny-list-wins-over-allow-list; shareholder/proxy/governance rejection; bare-6-K-never-promotes; and the explicit "no generic 6-K without passing the gate" proof. |
| `tests/test_edgar_scan_service.py` | Added 4 new end-to-end tests: 20-F candidate creation; material 6-K candidate creation; routine 6-K (still ingests as FilingEvent, never promoted); opaque Arm-style 6-K (same). |

No other file was changed.

### Test results

```
pytest tests/test_edgar_rules.py            → 55 passed (11 new)
pytest tests/test_edgar_scan_service.py      → 30 passed (4 new)
pytest tests/test_edgar_*.py                 → 244 passed
pytest tests/test_radar_card_public_contract.py tests/test_radar_status.py \
       tests/test_radar_inbox*.py tests/test_daily_news_scope_guard.py \
       tests/test_company_discovery_scope_guard.py tests/test_models.py
                                              → 124 passed, 8 skipped, 2 failed
                                                (both known diff-based
                                                scope-guard artifacts —
                                                see below)

Full suite: 4401 passed, 228 skipped, 37 failed — 6 known baseline
failures (pre-existing on origin/main, unrelated to this change) plus
31 diff-based scope-guard artifacts. Every one of the 31 is a `git
diff --name-only HEAD`-based test asserting that some OTHER, unrelated
historical phase never touched `src/data_access/edgar/scan_service.py`
or `edgar_rules.py` — both files are watched by an unusually large
number of these guards precisely because they sit at the center of the
EDGAR pipeline. Spot-checked two directly (their full assertion
mechanism is reproduced above) — both are pure diff-detection, neither
exercises this change's actual behavior. No functional regression was
found in any of the 4401 passing tests, which include the full,
unchanged 10-K/10-Q/8-K/8-K-item-number/13D/13G/financing-form
regression suite already in `test_edgar_rules.py` and
`test_edgar_scan_service.py`.
```

### Trade-offs, restated plainly

1. **The 6-K gate will not surface Arm's own material 6-K filings in
   Phase 1** — the filename convention Arm actually uses carries no
   scan-time content signal this gate can read. This is the single
   biggest known gap in this implementation, deliberately accepted and
   documented rather than worked around (per the design document's own
   explicit instruction not to guess at document content).
2. **The allow-list terms beyond "quarterly" and "annualreport" are
   not individually confirmed against a live filename** in this
   session — they are a direct, narrow implementation of the
   user-approved category list, not independently re-verified. If a
   real filer's own naming convention uses different wording for the
   same concepts (e.g. "1h" instead of "financial", "fy" instead of
   "annualreport"), Phase 1 would miss it the same way it misses Arm.
3. **`20-F/A` and `10-K/A` remain unhandled** — both fall through to
   today's existing "unrecognized form type" behavior. Neither was in
   this Phase 1's scope.

No code was committed, pushed, or merged as part of writing this note.
