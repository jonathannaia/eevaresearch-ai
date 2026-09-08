"""Deterministic signal routing for extracted SEC EDGAR filings.

This module is pure: it performs no I/O and does not mutate candidates —
`decide_signal_route()` only returns a `SignalDecision`, it never writes
one. The name "shadow" here refers to how this policy was introduced,
not to its current effect: it is not shadow-only today.

Corrected (Phase R1, design/DECISIONS.md): an earlier version of this
docstring claimed shadow mode "never changes the candidate's actual
workflow status" — that is no longer accurate and this module's own
behavior already contradicted it before this correction. In
`src/data_access/edgar/edgar_pipeline.py`, the route this function
returns *does* set the candidate's real `CandidateStatus`: `TIMELINE`
sets `MONITORING`, `ARCHIVE` sets `DISMISSED`, and `PUBLISH` sets
`PUBLISHED` — but only when `auto_publish_enabled` is also true (`REVIEW`,
and a `PUBLISH` route while `auto_publish_enabled` is false, both fall
through to `NEEDS_REVIEW`, the human-gated status). Today, only a
narrow, fully-evidenced 424B5 offering (offering language, a dollar
amount, a share/note quantity, and proceeds language all present in the
extracted excerpt) can ever reach `PUBLISH`. `auto_publish_enabled`
itself defaults to false and is additionally forced false, structurally,
inside `scripts/radar_worker.py` regardless of its own process
environment (Durable-State Phase 4M-0's own safety invariant) — so this
policy's one `PUBLISH` route cannot autonomously publish anything once a
worker is deployed unless that separate, already-documented safeguard is
itself revisited. This correction is documentation only; no behavior in
this module or `edgar_pipeline.py` changed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from src.data_access.edgar.edgar_rules import items_from_matched_rules, normalize_form_type
from src.models.models import CandidateSignal, ExtractionState


class SignalRoute(str, Enum):
    PUBLISH = "PUBLISH"
    TIMELINE = "TIMELINE"
    ARCHIVE = "ARCHIVE"
    REVIEW = "REVIEW"


@dataclass(frozen=True)
class SignalDecision:
    route: SignalRoute
    reason: str
    rule_ids: tuple[str, ...] = ()


_SUPPORTING_DOCUMENT_PATTERNS = (
    "incorporated by reference",
    "pro forma financial statements",
    "consent of independent registered public accounting firm",
    "previously announced",
    "registration statement",
    "subject to closing conditions",
)

_OFFERING_LANGUAGE_PATTERN = re.compile(
    r"\b(priced|pricing|offering|price to the public|public offering|private placement)\b",
    re.IGNORECASE,
)
_DOLLAR_AMOUNT_PATTERN = re.compile(
    r"\$\s*\d[\d,]*(?:\.\d+)?\s*(?:million|billion|thousand|m|bn)?\b",
    re.IGNORECASE,
)
_QUANTITY_PATTERN = re.compile(
    r"\b\d[\d,]*(?:\.\d+)?\s+(?:shares|units|notes|warrants|common stock|preferred stock)\b",
    re.IGNORECASE,
)
_PROCEEDS_PATTERN = re.compile(r"\b(?:gross|net)\s+proceeds\b", re.IGNORECASE)


def decide_signal_route(candidate: CandidateSignal) -> SignalDecision:
    """Returns a conservative proposed route for one extracted EDGAR candidate.

    In this initial policy, only a fact-complete 424B5 offering can receive
    a proposed PUBLISH route. All other paths are monitoring/archive/review
    recommendations. Callers decide whether to apply the result.
    """
    if candidate.extraction_state != ExtractionState.EXTRACTED:
        return SignalDecision(
            SignalRoute.REVIEW,
            "Document extraction is incomplete; no automatic route proposed.",
            ("edgar.shadow.extraction_incomplete",),
        )

    filing = candidate.filing
    form_type = normalize_form_type(filing.pblntf_ty)
    text = (candidate.excerpt_original or "").lower()
    item_numbers = items_from_matched_rules(candidate.matched_rules)

    if form_type in {"SC 13G", "SC 13G/A"}:
        return SignalDecision(
            SignalRoute.TIMELINE,
            "Passive beneficial-ownership filing; no verified threshold, control, or material-change analysis in this policy.",
            ("edgar.13g.default_timeline",),
        )

    if form_type in {"10-Q", "10-K"}:
        return SignalDecision(
            SignalRoute.TIMELINE,
            "Periodic report; this policy does not yet verify earnings, guidance, or risk-factor novelty.",
            ("edgar.periodic_report.default_timeline",),
        )

    if form_type == "8-K" and "8.01" in item_numbers:
        matched_supporting = tuple(
            phrase for phrase in _SUPPORTING_DOCUMENT_PATTERNS if phrase in text
        )
        if len(matched_supporting) >= 2:
            return SignalDecision(
                SignalRoute.TIMELINE,
                "Item 8.01 supporting-document pattern detected: "
                + ", ".join(matched_supporting) + ".",
                ("edgar.8k.8_01.supporting_documents",),
            )

    if form_type == "424B5":
        has_offering_language = bool(_OFFERING_LANGUAGE_PATTERN.search(text))
        has_dollar_amount = bool(_DOLLAR_AMOUNT_PATTERN.search(text))
        has_quantity = bool(_QUANTITY_PATTERN.search(text))
        has_proceeds = bool(_PROCEEDS_PATTERN.search(text))
        if has_offering_language and has_dollar_amount and has_quantity and has_proceeds:
            return SignalDecision(
                SignalRoute.PUBLISH,
                "Offering language, dollar amount, security quantity, and proceeds language were all found in the extracted excerpt.",
                ("edgar.424b5.complete_offering_terms",),
            )
        return SignalDecision(
            SignalRoute.TIMELINE,
            "424B5 filing lacks one or more required extracted offering terms for a proposed publication.",
            ("edgar.424b5.incomplete_offering_terms",),
        )

    if not text.strip():
        return SignalDecision(
            SignalRoute.ARCHIVE,
            "No extracted filing text is available for a user-facing update.",
            ("edgar.excerpt.empty",),
        )

    return SignalDecision(
        SignalRoute.REVIEW,
        "No high-precision automatic routing rule matched this filing.",
        ("edgar.fallback.review",),
    )
