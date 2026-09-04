"""EDINET Extraordinary Report (臨時報告書) shadow eligibility evaluator —
Radar shadow-observation workstream (design/DECISIONS.md).

Disabled by default (Settings.edinet_material_event_lexicon_enabled), and
even when enabled this module is structurally incapable of creating,
persisting, or displaying anything: every function here is pure (no I/O,
no network, no database access) and operates only on already-fetched
FilingEvent metadata. It is called only from edinet_pipeline.run_pipeline's
own optional, additive shadow step — never from edinet_rules.py's own
evaluate_document()/merge_evaluations() candidate-detection path, never
wired to CandidateSignal creation, translation, persistence, or any UI
surface. The existing annual_securities_report rule and every other
existing EDINET rule/exclusion is untouched by this module.

Verified official EDINET metadata pattern (bounded, read-only observation
exercise — design/DECISIONS.md, 2026-09-03: one market-wide document-list
request, 9 independent filers, one consistent triplet):

    Eligible: docDescription (NFKC-normalized) exactly "臨時報告書"
              AND ordinanceCode:formCode:docTypeCode == "010:053000:180"

Both the exact title AND the exact triplet must match — either alone is
insufficient. The same observation also confirmed two real, internally-
consistent but different-triplet look-alikes that must never be mistaken
for the eligible pattern:

  - "臨時報告書（内国特定有価証券）" (Extraordinary Report — Domestic
    Specified Securities), triplet 030:995000:180 — filed exclusively by
    asset-management/fund entities about securities they manage, never a
    tracked issuer's own material corporate event (17 independent
    instances observed, one consistent triplet).
  - "訂正臨時報告書" (correction/amendment), triplet 010:053001:190 (2
    independent instances observed, one consistent triplet).

Explicit exclusions, checked before eligibility, in this exact order:
  1. Any title containing 訂正 (correction/amendment marker) — this first
     rollout treats every such disclosure as non-promoting, regardless of
     triplet.
  2. The domestic-specified-securities triplet (030:995000:180).
  3. The correction triplet (010:053001:190) — redundant with (1) for
     every real observed instance; kept as an independent, defense-in-
     depth triplet-level check.
  4. Every other already-confirmed-real exclusion this project's EDINET
     evidence has established: 確認書 (Confirmation Letter,
     010:042000:135), 内部統制報告書 (Internal Control Report,
     015:010000:235). 有価証券報告書 (Annual Securities Report) and 自己
     株券買付状況報告書 (treasury-stock buyback status) need no explicit
     triplet/title entry here — their titles never equal "臨時報告書", so
     the exact-title-equality requirement alone already excludes them
     structurally; the existing annual_securities_report rule in
     edinet_rules.py is entirely separate from and unaffected by this
     module either way.
  5. Any unknown, partial, or unrecognized triplet — the default,
     fail-closed outcome for anything not explicitly matched above.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from src.models.models import FilingEvent

_ELIGIBLE_TITLE = "臨時報告書"
_ELIGIBLE_TRIPLET = ("010", "053000", "180")

# Explicit, real, live-confirmed non-eligible triplets (design/DECISIONS.md's
# own EDINET Extraordinary Report verification entry) — checked before
# eligibility as independent, defense-in-depth guards, not merely relying
# on the exact-title-equality check alone.
_DOMESTIC_SPECIFIED_SECURITIES_TRIPLET = ("030", "995000", "180")
_CORRECTION_TRIPLET = ("010", "053001", "190")
_CONFIRMATION_LETTER_TRIPLET = ("010", "042000", "135")
_INTERNAL_CONTROL_REPORT_TRIPLET = ("015", "010000", "235")

_EXCLUDED_TRIPLETS = frozenset({
    _DOMESTIC_SPECIFIED_SECURITIES_TRIPLET,
    _CORRECTION_TRIPLET,
    _CONFIRMATION_LETTER_TRIPLET,
    _INTERNAL_CONTROL_REPORT_TRIPLET,
})

_CORRECTION_MARKER = "訂正"


def _normalize(text: str) -> str:
    """NFKC only — the project's existing CJK title-normalization
    convention (src.logic.theme_matching.normalize_text), reused rather
    than reimplemented, per the explicit "no new normalization
    dependency" requirement. No casefold: Japanese statutory titles carry
    no case-sensitivity concern the way Latin-script matching does."""
    return unicodedata.normalize("NFKC", text)


@dataclass(frozen=True)
class ShadowMatch:
    """A read-only observation record — never a CandidateSignal, never
    written anywhere, never carrying a `status` field. Exists purely to
    give the worker's own bounded shadow log line something typed to
    report. `triplet` is the human-readable "ordinanceCode:formCode:
    docTypeCode" string, matching this codebase's own existing
    matched_rules formatting convention."""

    doc_id: str
    issuer_name: str
    title: str
    triplet: str


def is_eligible_extraordinary_report(filing: FilingEvent) -> bool:
    """Pure function, no I/O. True only when the NFKC-normalized title
    exactly equals 臨時報告書 AND the code triplet exactly equals the one
    real, confirmed eligible triplet — both conditions required. Every
    known exclusion is checked first and short-circuits to False before
    either positive condition is even evaluated."""
    normalized_title = _normalize(filing.report_nm or "")

    if _CORRECTION_MARKER in normalized_title:
        return False

    triplet = (filing.ordinance_code, filing.pblntf_ty, filing.pblntf_detail_ty)
    if triplet in _EXCLUDED_TRIPLETS:
        return False

    if normalized_title != _ELIGIBLE_TITLE:
        return False

    return triplet == _ELIGIBLE_TRIPLET


def find_matches(filings: tuple[FilingEvent, ...]) -> tuple[ShadowMatch, ...]:
    """Evaluates every filing already fetched this scan tick — no new
    network request, no document fetch, purely a filter over already-
    in-memory FilingEvent objects already discovered for already-tracked
    issuers by scan_service.scan()'s own existing company-matching logic.
    Never mutates any input FilingEvent; never reads or writes any
    CandidateSignal, repository, or cache."""
    matches = []
    for filing in filings:
        if is_eligible_extraordinary_report(filing):
            matches.append(ShadowMatch(
                doc_id=filing.rcept_no,
                issuer_name=filing.corp_name,
                title=filing.report_nm,
                triplet=f"{filing.ordinance_code}:{filing.pblntf_ty}:{filing.pblntf_detail_ty}",
            ))
    return tuple(matches)
