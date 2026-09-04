"""EDINET's own category-routing rules (Japan radar pilot). No LLM
calls, no market interpretation — same "routing input only, never proof
of relevance" posture as dart_rules.py/edgar_rules.py.

Gate 1–9 status: real Japanese statutory document-type codes must NOT be
guessed into this module — they are populated "only after live
verification, never guessed," and the plan explicitly forbids encoding a
Japan ownership/large-shareholding materiality threshold in this pilot.
So through Gate 9 this module shipped with only the STRUCTURE of a
category system (the taxonomy slugs) and an always-empty
DEFAULT_CODE_CATEGORY_MAP, matching nothing.

Gate 10 populated DEFAULT_CODE_CATEGORY_MAP with its first real entry —
see the constant's own docstring below for the exact evidence chain and
the explicit correction (form identity alone does not establish a
current earnings event; the category is `annual_securities_report`, not
`earnings_or_results`). Routing now requires ordinanceCode, formCode,
AND docTypeCode to all match (see evaluate_document/_routing_key) — a
formCode/ordinanceCode pair alone is deliberately no longer sufficient,
since a real live pull (Gate 6/9) showed multiple real SoftBank Group
filings sharing the same ordinanceCode with different, semantically
distinct docTypeCode values.
"""
from __future__ import annotations

from dataclasses import dataclass

LEXICON_VERSION = "v1-2026-08-edinet-gate10-first-real-mapping"

# Category taxonomy (English slugs only) — see module docstring. Every
# entry except "annual_securities_report" remains an unbacked structural
# placeholder — not yet backed by any real, mapped EDINET code.
EDINET_CATEGORIES: tuple[str, ...] = (
    "annual_securities_report",
    "earnings_or_results",
    "guidance",
    "capex_or_facility_expansion",
    "material_contract_or_partnership",
    "financing_or_debt",
    "ma_or_strategic_investment",
    "governance",
    "ownership_or_large_shareholding",
    "regulatory_or_legal",
    "technology_or_product",
    "other",
)

# Gate 10 — the first, and this gate the only, real EDINET category
# mapping. Every value here has been independently live-verified twice:
# Gate 2 (2026-08-17, the real EDINET code-list artifact) and Gate 6/9
# (2026-06-22 document-list observation + form-code backfill) both
# confirmed this exact tuple for SoftBank Group's real annual securities
# report (docID S100YGH5). Deliberately does NOT use "earnings_or_
# results": form identity alone (a statutory Annual Securities Report
# filing) does not establish a current earnings event, guidance change,
# or market-relevant result — an Annual Securities Report can carry
# financial, risk, governance, and business information together, so
# "annual_securities_report" names what was actually verified (the
# statutory report itself), not an inferred market meaning.
#
# The two other real, verified SoftBank Group tuples from the same
# filing day — S100YFHB's 010:042000:135 (確認書/Confirmation Letter)
# and S100YFH8's 015:010000:235 (内部統制報告書/Internal Control
# Report) — are deliberately NOT mapped here; they remain FilingEvent-
# only. Do not add another entry to this map without the same
# live-verification discipline these three tuples went through.
#
# Second real entry (share-buyback status reports) — ordinanceCode=010,
# formCode=170000, docTypeCode=220 → "share_buyback_status". Live-
# verified via an authenticated EDINET document-list sample spanning
# 2026-06-03 through 2026-09-04 (8 dates): 384 filings carried the exact
# title 自己株券買付状況報告書（法２４条の６第１項に基づくもの）
# ("Status Report of Purchase of Own Shares"), of which 239 independent
# issuers (anchored by Shin-Etsu Chemical Co., Ltd., docID S100Z0ID,
# EDINET code E00776, filed 2026-09-04) used this exact triplet — the
# project's own established minimum-evidence bar for a real mapping
# (Gate 10 required 9 independent filers; this is a wide margin beyond
# that). Two real, confirmed look-alikes are deliberately excluded, on
# the same title-plus-triplet discipline the Extraordinary Report shadow
# evaluator (material_event_shadow.py) already established, and must
# never be added to this map without independent re-verification:
#   - 010:170001:230 — 訂正自己株券買付状況報告書 (corrected/amended
#     buyback report; 6 independent instances observed, one consistent
#     triplet, always distinct from the base 220 docTypeCode).
#   - 030:253000:220 — the specified-securities/fund/REIT variant (e.g.
#     KDX Real Estate Investment Corporation, EDINET code E14109,
#     secCode None), filed under a different ordinance (特定有価証券の
#     内容等の開示に関する内閣府令) despite an identical title string —
#     title-text matching alone would wrongly capture it; the triplet
#     correctly excludes it.
# A real post-patch fetch/extraction validation (docID S100Z0ID) confirms
# this category's documents are extractable by the existing pipeline: the
# ZIP package's safe "honbun" inline-XBRL HTML body yielded 600 real
# extracted Japanese characters via document_extractor.py's HTML-in-ZIP
# fallback — no further extraction work was needed for this category.
DEFAULT_CODE_CATEGORY_MAP: dict[str, str] = {
    "010:030000:120": "annual_securities_report",
    "010:170000:220": "share_buyback_status",
}


@dataclass(frozen=True)
class RuleEvaluation:
    matched_rules: tuple[str, ...]
    # None means: stay a FilingEvent, do not promote to CandidateSignal.
    confidence: str | None


def _confidence_for(matched: list[str]) -> str | None:
    if not matched:
        return None
    distinct_categories = len({m.split(":", 1)[0] for m in matched})
    return "High" if distinct_categories >= 2 else "Moderate"


def _routing_key(ordinance_code: str, form_code: str, doc_type_code: str) -> str:
    return f"{ordinance_code.strip()}:{form_code.strip()}:{doc_type_code.strip()}"


def evaluate_document(
    ordinance_code: str,
    form_code: str,
    doc_type_code: str,
    code_category_map: dict[str, str] = DEFAULT_CODE_CATEGORY_MAP,
) -> RuleEvaluation:
    """Scan-time evaluation from EDINET's ordinanceCode+formCode+
    docTypeCode triplet (the field names the user explicitly authorized
    recording — see design/DECISIONS.md's Gate 0 entry). Pure function,
    no I/O.

    Gate 10 correction: routing now requires ALL THREE fields to match,
    never ordinanceCode+formCode alone — a real live pull (Gate 6/9)
    showed multiple genuine SoftBank Group filings sharing the same
    ordinanceCode/formCode-adjacent shape but carrying semantically
    distinct docTypeCode values (the two companion filings' own real
    tuples, 010:042000:135 and 015:010000:235, must never accidentally
    collide with the mapped 010:030000:120 annual-report tuple).

    `code_category_map` is injectable specifically so fixtures can supply
    their own explicit, labeled-fictional test mappings without this
    module claiming any real EDINET code as fact — with the module-level
    DEFAULT_CODE_CATEGORY_MAP (Gate 10: exactly one real entry), every
    input other than that one exact verified tuple yields
    confidence=None."""
    key = _routing_key(ordinance_code, form_code, doc_type_code)
    category = code_category_map.get(key)
    if category is None:
        return RuleEvaluation(matched_rules=(), confidence=None)
    return RuleEvaluation(matched_rules=(f"{category}:{key}",), confidence="Moderate")


def merge_evaluations(evaluations: list[RuleEvaluation]) -> RuleEvaluation:
    """Combines multiple independent matches (e.g. a code-based match plus
    a future keyword-based match) into one — additive union, never a
    destructive overwrite, same monotonic-merge principle
    edgar_rules.merge_8k_item_evaluation established. Not yet exercised by
    any real caller in Gate 1 (only one evaluation source — code-based —
    exists so far); included now so scan_service has a stable seam to
    call if/when a second evaluation source (e.g. docDescription keyword
    matching) is added in a later gate, without needing to change the
    merge contract at that point."""
    matched: list[str] = []
    for evaluation in evaluations:
        for rule in evaluation.matched_rules:
            if rule not in matched:
                matched.append(rule)
    return RuleEvaluation(matched_rules=tuple(matched), confidence=_confidence_for(matched))
