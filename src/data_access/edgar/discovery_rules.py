"""Discovery-time filing classification for the isolated EDGAR discovery
harness (Phase B — design/DECISIONS.md). Deliberately stricter than the
tracked pipeline's own `scan_service._evaluate_row()`: a discovery
proposal is about a company EevaResearch has never reviewed at all, so
there is no other signal (a known company, a known theme) to offset a
weak, boilerplate-only match the way the tracked pipeline can. This
module reuses `edgar_rules`' existing pure item-parsing/refinement
functions and constants unchanged — it adds a narrower gate in front of
them, it does not reimplement or alter them, and nothing in
`edgar_rules.py` itself is modified by this file existing."""
from __future__ import annotations

from src.data_access.edgar import edgar_rules

# The one form type this phase's discovery pilot considers — see
# design/DECISIONS.md for why 8-K specifically (it's the only form this
# codebase can classify at full resolution — category + confidence — from
# list-level metadata alone, via the real `items` column).
DISCOVERY_FORM_ALLOWLIST: frozenset[str] = frozenset({"8-K"})


def evaluate_discovery_row(form: str, items_raw: str | None) -> edgar_rules.RuleEvaluation | None:
    """Returns a real `edgar_rules.RuleEvaluation` (confidence always
    "Moderate" or "High", never None) only when ALL of the following hold:

    - `form` normalizes to "8-K" (the only allowlisted form this phase).
    - `items_raw` parses (via `edgar_rules.parse_items_metadata`) to at
      least one recognized, configured 8-K item number.

    Returns None — never propose a discovery — for every other case:
    a non-8-K form, missing `items`, malformed `items`, or `items`
    containing only unconfigured/unrecognized item numbers. This is the
    deliberate exclusion of the tracked pipeline's generic
    "material_event_8k_pending_items:8-K" fallback (see
    `scan_service._evaluate_row`) — that fallback fires on `items`
    absence/malformation precisely because the tracked pipeline already
    knows the company and its theme, so a coarse "something happened"
    signal is still useful there. A brand-new, unverified issuer has no
    such context, so the same coarse signal here would just be noise."""
    if edgar_rules.normalize_form_type(form) not in DISCOVERY_FORM_ALLOWLIST:
        return None
    item_numbers = edgar_rules.parse_items_metadata(items_raw)
    if not item_numbers:
        return None
    evaluation = edgar_rules.refine_8k_evaluation(item_numbers)
    if evaluation.confidence is None:
        return None
    return evaluation
