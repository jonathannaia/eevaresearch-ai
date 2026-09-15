"""Deterministic, versioned SEC form/item-based rules for promoting a
FilingEvent to a CandidateSignal (SEC EDGAR pilot — NVIDIA, Micron
Technology, Coherent Corp, Rockwell Automation, Rocket Lab only). No LLM
calls, no market interpretation — form type and 8-K item number are used
purely as a *routing* input, never as proof of theme relevance or
materiality on their own, exactly like DART's title keywords.

The category list below is grounded in SEC's own real, documented Form
8-K item taxonomy and standard form types (verified via SEC rule-making
documentation and cross-confirming technical sources during milestone-8
planning — SEC.gov itself 403'd this session's direct-fetch tooling, so
this is "documented and cross-confirmed," not independently re-verified
against a live pull the way DART's dart_rules.py lexicon was). Exactly
which items/forms actually fire for this specific 5-company cohort still
needs a live pull to confirm before this is called calibrated — same
caveat dart_rules.py's own "standard, not observed" entries carry.

Real structural difference from DART: DART's disclosure-list response
gives the full title (report_nm) at scan time, so keyword matching runs
immediately. EDGAR's filing-list metadata was originally assumed not to
carry 8-K item numbers — corrected by a real live pull (milestone 8,
Gate 3): SEC's submissions API's `filings.recent` block DOES include an
`items` column per filing (a comma-separated string, e.g.
`"1.01,2.03,7.01"`) directly in the list-level metadata, no document
fetch required. So 8-K classification now has two paths, not a strict
two-*stage* pipeline:
  1. Scan time (evaluate_form_type + parse_items_metadata, wired
     together in scan_service.scan()): if the real `items` column is
     present and non-empty/well-formed, the candidate is classified
     immediately via refine_8k_evaluation — the full category+confidence,
     no document fetch needed. If `items` is absent or malformed, the
     filing falls back to the coarse, Moderate-confidence
     "8-K filed, items pending" classification exactly as before.
  2. Post-extraction (extract_item_numbers + refine_8k_evaluation,
     called from edgar_pipeline.py once/if the real document text is
     later fetched): parses "Item X.XX" headers out of the extracted
     excerpt and re-applies the same refinement. This is now an
     idempotent consistency check/fallback, not the only path to a
     refined classification — a candidate already refined at scan time
     will get the same result again; a candidate that fell back to the
     coarse classification (scan-time items missing/malformed) can still
     be refined here if the document text itself reveals item headers.

A second real live-pull finding (Gate 3) corrected `FORM_TYPE_CATEGORIES`
too: SEC's actual data returns the spelled-out `"SCHEDULE 13G"` rather
than the abbreviated `"SC 13G"` this module originally assumed — see
normalize_form_type().
"""
from __future__ import annotations

import re
from dataclasses import dataclass

LEXICON_VERSION = "v1-2026-08-edgar"

# Form 8-K item number -> category. Real, documented item numbers (see
# module docstring) — not the full 33-category taxonomy, only the subset
# the milestone-8 brief asked for.
EIGHT_K_ITEM_CATEGORIES: dict[str, str] = {
    "1.01": "material_agreement",
    "2.01": "acquisition_or_disposition",
    "2.02": "earnings_or_results",
    "2.03": "financing_or_debt",
    "5.02": "governance_or_management_change",
    "7.01": "regulation_fd_disclosure",
    "8.01": "other_material_event",  # SEC's own documented catch-all provision
}

# Non-8-K form types handled directly by form type (complete signal at
# scan time — no item-number-style sub-detail exists for these).
FORM_TYPE_CATEGORIES: dict[str, str] = {
    "10-Q": "earnings_or_results",
    "10-K": "earnings_or_results",
    # Foreign-private-issuer annual report (design/DECISIONS.md, "EDGAR
    # foreign-private-issuer 20-F/6-K admission", Phase 1). SEC's own
    # designated 10-K equivalent for a foreign private issuer — always a
    # comprehensive, substantive annual filing, structurally never a
    # "routine" or ambiguous document the way a 6-K can be (see 6-K's own
    # handling below, deliberately NOT in this dict). Mapped to the exact
    # same category 10-K already uses — no new category, no new gate,
    # the narrowest possible change. 20-F/A (amendment) is explicitly
    # OUT OF SCOPE for this phase — not observed live for TSMC/ASML in
    # the design document's own evidence, and left unmapped here
    # deliberately rather than guessed; falls through to the existing
    # "unrecognized form type" behavior (confidence=None) exactly like
    # today's already-existing, separately-unresolved 10-K/A gap.
    "20-F": "earnings_or_results",
    "SC 13D": "ownership_change",
    "SC 13D/A": "ownership_change",
    "SC 13G": "ownership_change",
    "SC 13G/A": "ownership_change",
    # "Selected financing forms" per the milestone-8 brief — standard,
    # documented SEC registration/prospectus form types.
    "S-1": "financing_or_debt",
    "S-3": "financing_or_debt",
    "424B1": "financing_or_debt",
    "424B2": "financing_or_debt",
    "424B3": "financing_or_debt",
    "424B4": "financing_or_debt",
    "424B5": "financing_or_debt",
}

# Real SEC form-type spellings, verified live (milestone 8, Gate 3 — a
# real NVDA SCHEDULE 13G was silently missed before this alias map
# existed). FORM_TYPE_CATEGORIES stays keyed on the abbreviated form
# (the canonical form for lookup purposes); both spellings normalize to
# it, so no existing key needed to change.
_FORM_ALIASES: dict[str, str] = {
    "SCHEDULE 13D": "SC 13D",
    "SCHEDULE 13D/A": "SC 13D/A",
    "SCHEDULE 13G": "SC 13G",
    "SCHEDULE 13G/A": "SC 13G/A",
}

_GENERIC_8K_CATEGORY = "material_event_8k_pending_items"

_ITEM_NUMBER_PATTERN = re.compile(r"\bItem\s+(\d{1,2}\.\d{2})\b", re.IGNORECASE)

# --- 6-K foreign-private-issuer current-report gate (design/
# DECISIONS.md, "EDGAR foreign-private-issuer 20-F/6-K admission",
# Phase 1). A 6-K is a general-purpose "furnish anything a foreign
# issuer wants to disclose" wrapper — unlike 10-K/10-Q/20-F (always
# substantive) or 8-K (SEC provides structured Item-number metadata at
# scan time), a 6-K carries no SEC-provided sub-classification at scan
# time. Live evidence gathered for the design document (real ASML and
# Arm Holdings submissions, fetched 2026-09-15) confirmed
# `primaryDocDescription` is non-informative — it is literally the
# string "6-K" for every row of both filers, never a real description —
# so this gate reads `primaryDocument` (the filing's own filename)
# instead, the only other scan-time-available field with real signal,
# and only when that filer's own naming convention happens to be
# descriptive (confirmed true for ASML's real filenames, e.g.
# "form6-kquarterlyfilings.htm" vs. "form6-kagmdisclosureofagmr.htm";
# confirmed NOT true for Arm's own real filenames, which are opaque and
# date-stamped, e.g. "arm-20260910.htm").
#
# PHASE 1 LIMITATION, preserved deliberately: this filename-only gate
# WILL MISS materially relevant 6-K filings from issuers whose own
# naming convention is opaque (Arm Holdings is the confirmed, real
# example) — an opaque filename fails closed exactly like an unknown
# form type today, which is a known, accepted trade-off for this phase,
# not an oversight. A future phase could recover these by reading the
# actual document/exhibit text once fetched (the same two-stage
# scan-time-coarse-pass + post-extraction-refinement shape already
# established for 8-K via refine_8k_evaluation()/
# merge_8k_item_evaluation() below) — deliberately NOT built here.
#
# Deny-list checked BEFORE the allow-list, so a routine/governance term
# always wins over an incidental substantive-sounding word elsewhere in
# the same filename — never the reverse.
_SIX_K_ROUTINE_CONTENT_TERMS: tuple[str, ...] = (
    "agm", "annualgeneralmeeting", "annual-general-meeting", "shareholder",
    "proxy", "governance", "administrative", "notice",
)
# Directly observed in this filer's own real, live filenames (design
# document evidence, ASML CIK 0000937966, fetched 2026-09-15):
# "quarterly" (form6-kquarterlyfilings.htm), "annualreport"
# (form6-kannualreportbasedon.htm). The remaining terms are a direct,
# narrow implementation of the approved category list (financial/
# quarterly/annual results, earnings, guidance, investor presentation,
# acquisition/transaction, financing, material contract/order,
# capacity/operational event) — never a broad or speculative addition
# beyond it.
#
# Final allow-list review (design/DECISIONS.md, "EDGAR foreign-private-
# issuer 20-F/6-K admission, Phase 1 allow-list review"): removed three
# terms present in the original draft — "financial", "results", "order"
# — as individually too generic/ambiguous to prove materiality on their
# own. Each is subsumed by a more specific, already-present term that
# still fully covers its intended category without the added collision
# risk: "financial"/"results" -> "financialresults" (the compound form
# is what a genuine results filing would actually say; bare "results"
# alone risks matching a routine item like an AGM VOTE results
# announcement — exactly the class of routine/procedural 6-K this gate
# exists to reject); "order" -> "contract" (the "material contract/
# order" category is still represented; bare "order" alone is too short
# and generic to trust without a co-occurring, more specific term).
# Every surviving term is a real word or compound at least 6 characters
# long, chosen specifically to minimize the chance of matching inside
# an unrelated word by coincidence.
_SIX_K_MATERIAL_CONTENT_TERMS: tuple[str, ...] = (
    "quarterly", "annualreport", "financialresults",
    "earnings", "guidance", "investorday", "investorpresentation",
    "acquisition", "merger", "transaction", "financing", "contract",
    "capacity", "expansion",
)


@dataclass(frozen=True)
class RuleEvaluation:
    matched_rules: tuple[str, ...]
    # None means: stay a FilingEvent, do not promote to CandidateSignal.
    # Same category-count-based scale as dart_rules.py.
    confidence: str | None


def _confidence_for(matched: list[str]) -> str | None:
    if not matched:
        return None
    distinct_categories = len({m.split(":", 1)[0] for m in matched})
    return "High" if distinct_categories >= 2 else "Moderate"


def normalize_form_type(form_type: str) -> str:
    """Canonicalizes known abbreviated/spelled-out SEC form-type aliases
    (see _FORM_ALIASES) to the single form FORM_TYPE_CATEGORIES is keyed
    on. Unrecognized form types pass through unchanged
    (stripped/uppercased) for the existing lookup to handle — this
    function only resolves known real-world naming variance, it never
    invents a new category."""
    normalized = form_type.strip().upper()
    return _FORM_ALIASES.get(normalized, normalized)


def evaluate_form_type(form_type: str) -> RuleEvaluation:
    """Scan-time evaluation — form type only, no document text available
    yet. Pure function, no I/O."""
    normalized = normalize_form_type(form_type)
    if normalized == "8-K":
        return RuleEvaluation(matched_rules=(f"{_GENERIC_8K_CATEGORY}:8-K",), confidence="Moderate")
    category = FORM_TYPE_CATEGORIES.get(normalized)
    if category is None:
        return RuleEvaluation(matched_rules=(), confidence=None)
    return RuleEvaluation(matched_rules=(f"{category}:{normalized}",), confidence="Moderate")


_FOREIGN_ISSUER_CURRENT_REPORT_CATEGORY = "foreign_issuer_current_report"


_KNOWN_DOCUMENT_EXTENSIONS: tuple[str, ...] = (".htm", ".html", ".txt", ".pdf")


def _normalized_filename(primary_document: str | None) -> str:
    """Lowercases and strips a recognized trailing file extension —
    the file extension carries no content signal and must never
    participate in either term list's matching. Deliberately does NOT
    attempt true regex word-boundary (\\b) matching: real, live filer
    filenames (ASML CIK 0000937966, fetched 2026-09-15 — e.g.
    "form6-kquarterlyfilings.htm") concatenate words with no internal
    separator at all ("k" runs directly into "quarterly", which runs
    directly into "filings") — a strict \\b boundary would never match
    "quarterly" inside that real filename, which this gate is required
    to keep matching. Substring-collision risk is instead controlled at
    the term-list level (see _SIX_K_MATERIAL_CONTENT_TERMS's own
    comment): every retained term is a real word or compound at least 6
    characters long, specifically chosen to make an accidental match
    inside an unrelated word implausible, rather than a false
    guarantee of true linguistic boundaries this filename convention
    cannot support."""
    lowered = (primary_document or "").strip().lower()
    for extension in _KNOWN_DOCUMENT_EXTENSIONS:
        if lowered.endswith(extension):
            return lowered[: -len(extension)]
    return lowered


def evaluate_six_k(primary_document: str | None) -> RuleEvaluation:
    """Scan-time-only evaluation for a 6-K row — no document fetch, pure
    function, no I/O. Deliberately NOT reachable via evaluate_form_type()/
    FORM_TYPE_CATEGORIES (6-K is never added to that dict) — a bare 6-K
    form type alone must never promote a candidate; only a filename that
    passes this gate may. See this module's own "6-K foreign-private-
    issuer current-report gate" comment above _SIX_K_ROUTINE_CONTENT_
    TERMS for the full rationale, evidence, and Phase 1 limitation, and
    _normalized_filename()'s own docstring for why filename matching
    here is normalized-and-curated rather than regex-word-boundary-safe
    in the strict sense.

    Fails closed on every path: an empty/missing primary_document, a
    filename matching any _SIX_K_ROUTINE_CONTENT_TERMS entry (checked
    first, always wins), or a filename matching none of
    _SIX_K_MATERIAL_CONTENT_TERMS all return confidence=None — stay a
    bare FilingEvent, never promoted. Only a filename containing one of
    the curated material-content terms, with no routine-content term
    present, returns confidence="Moderate"."""
    filename = _normalized_filename(primary_document)
    if not filename:
        return RuleEvaluation(matched_rules=(), confidence=None)
    if any(term in filename for term in _SIX_K_ROUTINE_CONTENT_TERMS):
        return RuleEvaluation(matched_rules=(), confidence=None)
    hit = next((term for term in _SIX_K_MATERIAL_CONTENT_TERMS if term in filename), None)
    if hit is None:
        return RuleEvaluation(matched_rules=(), confidence=None)
    return RuleEvaluation(matched_rules=(f"{_FOREIGN_ISSUER_CURRENT_REPORT_CATEGORY}:6-K:{hit}",), confidence="Moderate")


def parse_items_metadata(raw: str | None) -> tuple[str, ...]:
    """Parses SEC's real scan-time `items` column — a comma-separated
    string, e.g. "1.01,2.03,7.01" (verified live, milestone 8 Gate 3,
    NVDA accession 0001045810-26-000069). Preserves absence/malformed
    input as absence rather than guessing: None, empty string, and
    whitespace-only all return (); any comma-separated token that isn't
    a configured EIGHT_K_ITEM_CATEGORIES key is silently dropped (same
    "no new semantic inference" rule as extract_item_numbers), not
    substituted or guessed. Deduplicates while preserving order."""
    if not raw or not raw.strip():
        return ()
    found: list[str] = []
    for token in raw.split(","):
        item = token.strip()
        if item in EIGHT_K_ITEM_CATEGORIES and item not in found:
            found.append(item)
    return tuple(found)


def extract_item_numbers(text: str) -> tuple[str, ...]:
    """Pure regex parse of 'Item X.XX' headers from extracted 8-K
    document text. Only returns item numbers this module has a
    configured category for — an unrecognized item number is silently
    not included, not guessed into a category."""
    if not text:
        return ()
    found = []
    for match in _ITEM_NUMBER_PATTERN.finditer(text):
        item = match.group(1)
        if item in EIGHT_K_ITEM_CATEGORIES and item not in found:
            found.append(item)
    return tuple(found)


def refine_8k_evaluation(item_numbers: tuple[str, ...]) -> RuleEvaluation:
    """Post-extraction refinement for an 8-K candidate — called once the
    real document text has been parsed for item numbers. Returns
    confidence=None (caller should NOT apply this — keep the original
    coarse scan-time classification) if no configured item number was
    found in the text."""
    matched = [f"{EIGHT_K_ITEM_CATEGORIES[item]}:8-K item {item}" for item in item_numbers]
    return RuleEvaluation(matched_rules=tuple(matched), confidence=_confidence_for(matched))


def merge_8k_item_evaluation(
    scan_time_items: tuple[str, ...], excerpt_items: tuple[str, ...],
) -> tuple[RuleEvaluation, tuple[str, ...]]:
    """Monotonic merge for post-extraction 8-K refinement (milestone 8,
    Gate 8 fix). SEC scan-time item metadata — parsed from the complete
    submissions payload, see parse_items_metadata — is authoritative and
    must never be removed or downgraded by what a bounded document
    excerpt happens to reach (a real live case: a 1200-character
    item-anchored excerpt reaching only Item 1.01 of a real three-item
    filing must not overwrite the correct three-category classification
    with an incomplete one-category one). Bounded excerpt-detected items
    may only ADD to the scan-time set, never replace or shrink it:

        final_categories = scan_time_items UNION excerpt_items

    Returns (merged RuleEvaluation, newly_added_items) — newly_added_items
    is the excerpt-only items not already in scan_time_items, letting the
    caller record accurate provenance (which items came from complete
    scan-time metadata vs. a document excerpt) without re-deriving the
    diff. When scan_time_items is empty (no valid scan-time metadata —
    still the coarse "8-K filed, items pending" state, or malformed
    input), this reduces to the pre-existing document-only behavior:
    excerpt_items alone determine the result."""
    merged: list[str] = list(scan_time_items)
    newly_added: list[str] = []
    for item in excerpt_items:
        if item not in merged:
            merged.append(item)
            newly_added.append(item)
    return refine_8k_evaluation(tuple(merged)), tuple(newly_added)


def items_from_matched_rules(matched_rules: list[str]) -> tuple[str, ...]:
    """Extracts item numbers back out of already-classified matched_rules
    entries shaped like 'category:8-K item X.XX' (the shape
    refine_8k_evaluation produces). Used by document_extractor's excerpt
    anchoring (see EIGHT_K_ITEM_EXCERPT_CHARS) to know which items a
    candidate is already known to have, without re-deriving them from raw
    scan-time metadata a second time. A candidate still at the coarse
    "material_event_8k_pending_items:8-K" classification (no items known
    yet) correctly yields ()."""
    found: list[str] = []
    for rule in matched_rules:
        if ":8-K item " in rule:
            item = rule.split(":8-K item ", 1)[1].strip()
            if item in EIGHT_K_ITEM_CATEGORIES and item not in found:
                found.append(item)
    return tuple(found)


def iter_item_header_positions(text: str) -> list[tuple[str, int]]:
    """Every 'Item X.XX' header occurrence in document order as
    (item_number, start_index) pairs — ALL item numbers found, not
    filtered to EIGHT_K_ITEM_CATEGORIES, since this is used for excerpt
    boundary-finding (e.g. detecting the next item header, including an
    unconfigured one like 9.01 Financial Statements/Exhibits, as a stop
    point), not for classification."""
    if not text:
        return []
    return [(m.group(1), m.start()) for m in _ITEM_NUMBER_PATTERN.finditer(text)]
