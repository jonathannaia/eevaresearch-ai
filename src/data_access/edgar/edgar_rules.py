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
