"""Centralized DART low-value-filing suppression predicates (design/
DART_LOW_VALUE_FILING_SUPPRESSION_DESIGN_2026_09_17.md). One place both
gates described in that report share their scoping logic from, so a
surface with only a filing title (no excerpt/body) and a surface with a
full CandidateSignal never drift onto two different definitions of "this
is the low-value treasury/equity class":

- `matched_rules_are_low_value_only` — for any caller that already has a
  CandidateSignal's `matched_rules` (radar_pipeline.py's own excerpt-based
  routing, and Radar Inbox's default-query suppression below).
- `is_low_value_filing_title` — the metadata/title-only gate, for any
  caller that only ever sees a bare FilingEvent.report_nm with no excerpt
  available at all (theme_evidence.recent_filing_evidence(), most
  notably). Deliberately more conservative than the excerpt-based
  src.data_access.dart.equity_transaction_materiality gate — it never
  fires unless dart_rules.py itself would already, deterministically
  classify the title into the low-value category alone, and it defers to
  any escape-hatch marker (ownership_materiality.MATERIAL_OWNERSHIP_
  MARKERS or equity_transaction_materiality.
  FOUNDER_OR_CONTROLLING_HOLDER_MARKERS) present in that same title.

Both predicates are pure, deterministic, no I/O — same discipline as
dart_rules.py and ownership_materiality.py."""
from __future__ import annotations

from typing import Sequence

from src.data_access.dart import dart_rules
from src.data_access.dart.equity_transaction_materiality import find_escape_hatch_marker

# Currently just the one category — a bare employee-directed treasury-
# share disposal/acquisition (see dart_rules.KOREAN_KEYWORD_LEXICON's own
# `treasury_stock_activity` entry). Kept as a frozenset (not a single
# constant) so a future additional low-value-only category can be added
# here without touching either predicate's own logic.
_LOW_VALUE_ONLY_CATEGORIES = frozenset({"treasury_stock_activity"})

# dart_rules.evaluate_report_name()'s own literal, bare (no category
# prefix) matched_rules entry for the [기재정정] amendment marker — an
# amended routine treasury filing is still routine, so this token is
# never counted as a disqualifying "other category" below.
_AMENDMENT_MATCHED_RULE = "amendment_or_correction"


def matched_rules_are_low_value_only(matched_rules: Sequence[str]) -> bool:
    """True iff every non-amendment category present in `matched_rules`
    is one of `_LOW_VALUE_ONLY_CATEGORIES`. False for an empty/no-match
    `matched_rules` (never assumed low-value without a positive category
    match), and false the moment any other real dart_rules category is
    also present — that other category is itself real, independently-
    recognized content (a named customer/contract, capacity, product/
    technology investment, capital raise, earnings, or guidance event —
    see dart_rules.KOREAN_KEYWORD_LEXICON), never suppressed by this
    gate."""
    categories = {rule.split(":", 1)[0] for rule in matched_rules if rule != _AMENDMENT_MATCHED_RULE}
    return bool(categories) and categories.issubset(_LOW_VALUE_ONLY_CATEGORIES)


def is_low_value_filing_title(report_nm: str) -> bool:
    """Pure, deterministic, title-only — for surfaces with no excerpt/
    document body available at all (see module docstring). True only when
    dart_rules.evaluate_report_name(report_nm) matches exclusively the
    low-value category set AND no escape-hatch marker (control/ownership
    change, tender offer, pledge, merger/share-swap, or a founder/CEO/
    controlling-holder mention) is present anywhere in the same title.
    False for a title dart_rules doesn't recognize at all — an
    unclassified title is never assumed low-value — and false the moment
    any other real category also matches."""
    if not report_nm:
        return False
    evaluation = dart_rules.evaluate_report_name(report_nm)
    if not matched_rules_are_low_value_only(evaluation.matched_rules):
        return False
    return find_escape_hatch_marker(report_nm) is None
