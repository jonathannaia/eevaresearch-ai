"""Deterministic, versioned Korean-keyword rules for promoting a
FilingEvent to a CandidateSignal (Korea DART radar pilot — Memory + AI
Buildout only). No LLM calls, no market interpretation — every rule is a
plain substring match against `report_nm`, and every match is recorded
by name so a reviewer can see exactly why a filing was flagged.

The lexicon below was built primarily from real, live-observed report_nm
values pulled from OpenDART for the two tracked companies (Samsung
Electronics, SK Hynix) over a 90-day window during development — not
guessed from training-data recall. A small number of entries are
documented, legally standardized categories from Korea's regulated
"major event report" (주요사항보고서) subject taxonomy that did not
happen to appear in that specific window; each is commented "standard,
not observed" below rather than presented as independently verified.

Important limitation, discovered via that same live pull (not assumed):
DART's disclosure-list ("list.json") response does NOT echo back the
pblntf_ty/pblntf_detail_ty type code per row — those are documented as
*search filters*, not response fields. So rule-matching here is
keyword-driven against report_nm text, not a disclosure-type-code
allowlist as originally planned; FilingEvent.pblntf_ty stays empty for
an unfiltered scan (see scan_service.py). A future milestone could add
type-filtered sweeps (one search per pblntf_ty value) if that
granularity turns out to be worth the extra API calls per scan.
"""
from __future__ import annotations

from dataclasses import dataclass

LEXICON_VERSION = "v1-2026-08"

# Routine, high-frequency filings that are procedurally required and
# essentially never material on their own — confirmed by volume in the
# live pull (dozens of individual insider-ownership filings per month for
# a company this size). Matching one of these suppresses candidate
# promotion entirely; the filing still becomes a plain FilingEvent.
ROUTINE_EXCLUDE_PATTERNS: tuple[str, ...] = (
    "임원ㆍ주요주주특정증권등소유상황보고서",  # exec/major-shareholder securities ownership report — routine, Form-4-style; observed live at high volume
    "기업설명회(IR)개최",  # IR event announcement — informational, not itself an event; observed live
)

# category -> (rule_name, keyword substrings). Keywords marked "observed
# live" were seen verbatim in the development pull; keywords marked
# "standard, not observed" are documented statutory major-event-report
# subject categories under Korean securities regulation that didn't
# happen to appear in that specific 90-day window.
KOREAN_KEYWORD_LEXICON: dict[str, tuple[str, tuple[str, ...]]] = {
    "earnings": (
        "earnings_or_results_report",
        ("실적", "사업보고서", "반기보고서", "분기보고서"),  # all observed live
    ),
    "guidance": (
        "forward_looking_business_plan",
        ("장래사업ㆍ경영계획",),  # observed live (SK Hynix)
    ),
    "capex_or_facility_investment": (
        "facility_investment",
        ("신규시설투자", "시설투자"),  # observed live (SK Hynix, x3 in-window)
    ),
    "supply_or_sales_contract": (
        "supply_or_sales_contract",
        ("단일판매ㆍ공급계약체결", "공급계약"),  # standard, not observed in this window
    ),
    "equity_or_jv_investment": (
        "equity_stake_or_investment_decision",
        ("타법인주식및출자증권취득", "특수관계인으로부터자산양수"),  # 2nd observed live (SK Hynix); 1st standard, not observed
    ),
    "financing": (
        "capital_raise_or_treasury_stock",
        ("유상증자", "무상증자", "자기주식처분", "자기주식취득", "증권신고서", "배당결정"),  # all observed live except 무상증자 (standard sibling of 유상증자, not observed)
    ),
    "listing_or_market_event": (
        "listing_decision",
        ("상장결정", "해외증권시장주권등상장"),  # observed live (SK Hynix)
    ),
    "ownership_change": (
        "major_shareholder_change",
        ("최대주주등소유주식변동", "대량보유상황보고서"),  # both observed live
    ),
    "risk_disclosure": (
        "risk_or_incident",
        ("중대재해발생", "파생상품거래손실발생", "부도발생", "영업정지"),  # first two observed live; last two standard, not observed
    ),
    "market_rumor_response": (
        "rumor_inquiry_or_response",
        ("조회공시요구", "풍문또는보도"),  # observed live (SK Hynix)
    ),
}

# A [기재정정] prefix means this filing amends/corrects an earlier one —
# a real, observable "change" condition, distinct from (and additional
# to) the category keywords above.
AMENDMENT_MARKER = "[기재정정]"


@dataclass(frozen=True)
class RuleEvaluation:
    matched_rules: tuple[str, ...]
    # None means: stay a FilingEvent, do not promote to CandidateSignal.
    # Never a market judgment — purely a count of independent rule
    # categories that matched.
    confidence: str | None


def evaluate_report_name(report_nm: str) -> RuleEvaluation:
    """Pure function, no I/O. Deterministic and versioned (see
    LEXICON_VERSION) — the same report_nm always produces the same
    result for a given lexicon version."""
    if any(pattern in report_nm for pattern in ROUTINE_EXCLUDE_PATTERNS):
        return RuleEvaluation(matched_rules=(), confidence=None)

    matched: list[str] = []
    for category, (rule_name, keywords) in KOREAN_KEYWORD_LEXICON.items():
        for kw in keywords:
            if kw in report_nm:
                matched.append(f"{category}:{rule_name}:{kw}")
                break  # one match is enough to count this category once

    if AMENDMENT_MARKER in report_nm:
        matched.append("amendment_or_correction")

    if not matched:
        return RuleEvaluation(matched_rules=(), confidence=None)

    category_matches = len([m for m in matched if m != "amendment_or_correction"])
    if category_matches >= 2:
        confidence = "High"
    elif category_matches == 1:
        confidence = "Moderate"
    else:
        # Only the amendment marker matched, with no category keyword —
        # not itself a reason to claim relevance.
        confidence = None

    return RuleEvaluation(matched_rules=tuple(matched), confidence=confidence)


def format_confidence_label(confidence: str) -> str:
    """A bare "Moderate"/"High" reads as a materiality judgment out of
    context — every UI surface must show what the value actually
    measures instead of the raw word alone. No Radar Inbox exists yet to
    consume this, but this is the seam future UI code should call rather
    than reading CandidateSignal.confidence directly."""
    return f"Detection confidence: {confidence}"
