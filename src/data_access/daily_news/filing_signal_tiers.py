"""Filing event category -> Signals materiality tier.

Pure: one dict, two lookups, no I/O. Kept apart from the adapters so
the question "what tier does this filing get" has exactly one answer in
exactly one place, readable without tracing three source-specific
modules.

WHY A FILING GETS A TIER AT ALL. News items earn a tier from their text
through the gates in materiality_classification.py. A filing cannot and
should not: its materiality comes from WHAT IT IS. An 8-K Item 5.02 is
a change of executive officer whether or not the headline says so, and
the headline is usually just a form name. So the statutory document
type is the classification, and the mapping below is the whole of it.

HIGH IS FOR A COMPLETED, NAMED EVENT. Watchlist is for the routine, the
preliminary and the amended. An amendment is always Watchlist on its
own: in Korean and Japanese practice an amended major-decision filing
is often where the real numbers finally land, but it is equally often a
typo fix, and the document type alone cannot tell those apart. Only an
observed change to amounts, prices or key terms lifts one — see
amendment_changes_material_terms().

NOTHING HERE INFERS. A category with no entry below is not guessed at;
tier_for_filing_category() returns None and the caller does not publish
it. Adding a document type is a deliberate edit to this file."""
from __future__ import annotations

import re

from src.data_access.daily_news.dart_structured_fields import (
    CASH_DIVIDEND,
    FACILITY_INVESTMENT,
    DividendChange,
    PROVISIONAL_RESULTS,
    SUPPLY_CONTRACT,
    TREASURY_ACQUISITION,
    TREASURY_DISPOSAL,
    StructuredFilingFacts,
)
from src.models.daily_news_models import NewsMaterialityTier

#: Category -> tier. Source-system-agnostic on purpose: EDGAR, DART and
#: EDINET all resolve to category strings first, and a governance
#: change is a governance change in three jurisdictions.
FILING_CATEGORY_TIERS: dict[str, NewsMaterialityTier] = {
    # --- SEC EDGAR (edgar_rules.EIGHT_K_ITEM_CATEGORIES / FORM_TYPE_CATEGORIES)
    "governance_or_management_change": NewsMaterialityTier.HIGH_SIGNAL,   # 8-K 5.02
    "material_agreement": NewsMaterialityTier.HIGH_SIGNAL,                # 8-K 1.01
    "earnings_or_results": NewsMaterialityTier.HIGH_SIGNAL,               # 8-K 2.02, 10-K, 10-Q, 20-F
    "acquisition_or_disposition": NewsMaterialityTier.HIGH_SIGNAL,        # 8-K 2.01
    "financing_or_debt": NewsMaterialityTier.HIGH_SIGNAL,                 # 8-K 2.03
    # 8-K 3.02. Early rather than High: the filing reports that shares
    # were issued, not what they were issued for. Dilution and a
    # stock-funded acquisition look identical here, and only one of
    # those belongs at the top of the feed.
    "unregistered_equity_sale": NewsMaterialityTier.WATCHLIST,

    # --- OpenDART (dart_rules.KOREAN_KEYWORD_LEXICON)
    "earnings": NewsMaterialityTier.HIGH_SIGNAL,
    "guidance": NewsMaterialityTier.HIGH_SIGNAL,
    "supply_or_sales_contract": NewsMaterialityTier.HIGH_SIGNAL,
    "equity_or_jv_investment": NewsMaterialityTier.HIGH_SIGNAL,
    "capex_or_facility_investment": NewsMaterialityTier.HIGH_SIGNAL,
    "financing": NewsMaterialityTier.HIGH_SIGNAL,
    "risk_disclosure": NewsMaterialityTier.HIGH_SIGNAL,

    # --- EDINET (edinet_rules.DEFAULT_CODE_CATEGORY_MAP), tiers as
    #     approved 2026-11-24 after the triplet verification pass.
    "extraordinary_report": NewsMaterialityTier.HIGH_SIGNAL,              # 臨時報告書
    "annual_securities_report": NewsMaterialityTier.HIGH_SIGNAL,          # 有価証券報告書
    "semi_annual_report": NewsMaterialityTier.HIGH_SIGNAL,                # 半期報告書
    "securities_registration": NewsMaterialityTier.HIGH_SIGNAL,           # 有価証券届出書
    "ipo_registration": NewsMaterialityTier.HIGH_SIGNAL,                  # 有価証券届出書（新規公開時）
    "share_buyback_status": NewsMaterialityTier.WATCHLIST,                # routine, monthly
    "shelf_registration": NewsMaterialityTier.WATCHLIST,                  # 発行登録書 — capacity, not an issue
    "shelf_registration_supplement": NewsMaterialityTier.WATCHLIST,       # 発行登録追補書類
    "parent_company_status": NewsMaterialityTier.WATCHLIST,               # 親会社等状況報告書
}

#: Every amended_* category resolves here, so adding an amendment type
#: to the EDINET map needs no edit at all — and cannot accidentally be
#: given High by omission, which is the direction that matters.
_AMENDMENT_PREFIX = "amended_"
_AMENDMENT_TIER = NewsMaterialityTier.WATCHLIST

#: Korean amendment marker. DART prefixes an amended filing's report
#: name with this, and the base title that follows is the original
#: document's.
DART_AMENDMENT_MARKER = "[기재정정]"

#: A card label, not a tier input. "IPO" is shown because an IPO
#: registration reads oddly next to ordinary offerings otherwise.
CATEGORY_CARD_LABELS: dict[str, str] = {
    "ipo_registration": "IPO",
    "unregistered_equity_sale": "Financing",
    "share_buyback_status": "Routine",
    "shelf_registration": "Financing",
    "shelf_registration_supplement": "Financing",
    "parent_company_status": "Routine",
}
AMENDMENT_CARD_LABEL = "Amendment"


def is_amendment_category(category: str) -> bool:
    return (category or "").startswith(_AMENDMENT_PREFIX)


def base_category_of(category: str) -> str:
    """The document type an amendment amends. "amended_semi_annual_report"
    -> "semi_annual_report". Returns the input unchanged when it is not
    an amendment."""
    if not is_amendment_category(category):
        return category
    return category[len(_AMENDMENT_PREFIX):]


def tier_for_filing_category(category: str) -> NewsMaterialityTier | None:
    """None for an unmapped category — never a guess, and never a
    default of High. The caller must not publish what this cannot
    place."""
    if not category:
        return None
    if is_amendment_category(category):
        # Known base type or not, an amendment is Watchlist. An
        # amendment of something we cannot place is still an amendment.
        return _AMENDMENT_TIER
    return FILING_CATEGORY_TIERS.get(category)


#: Terms whose presence in an amended filing's own title indicates the
#: amendment restates figures rather than correcting a clerical error.
#: Deliberately narrow: a false positive promotes a typo fix to the top
#: of the feed, which is exactly the noise this work exists to remove.
_MATERIAL_AMENDMENT_TERMS: tuple[re.Pattern[str], ...] = (
    # Korean: 정정 (correction) of 금액 (amount), 단가 (unit price),
    # 계약금액 (contract amount), 조건 (terms), 규모 (scale).
    re.compile(r"(금액|단가|계약금액|조건|규모|수량|기간)", re.IGNORECASE),
    # Japanese: 金額 (amount), 価格 (price), 条件 (terms), 数量 (quantity).
    re.compile(r"(金額|価格|条件|数量|取得価額)", re.IGNORECASE),
    # English, for an EDGAR amendment or a translated title.
    re.compile(r"\b(amount|price|pricing|consideration|terms|quantity|volume|value)\b", re.IGNORECASE),
)


def amendment_changes_material_terms(title: str, *, previous_title: str | None = None) -> bool:
    """Whether an amendment restates amounts, prices or key terms, and
    so earns its base document's tier instead of Watchlist.

    Evidence-based and deliberately conservative. The only evidence
    available at this layer is the filing's own title, so this looks
    for the vocabulary a restatement uses. When `previous_title` is
    supplied and the two titles are identical once the amendment marker
    is removed, the amendment changed nothing in the title and is
    treated as clerical regardless of vocabulary — a title that says
    "amount" because the ORIGINAL said "amount" is not evidence of a
    change.

    Returns False for anything it cannot establish. A wrongly-demoted
    amendment sits in Watchlist, fully visible; a wrongly-promoted one
    puts a typo fix at the top of the feed."""
    text = (title or "").strip()
    if not text:
        return False
    if previous_title is not None:
        stripped = text.replace(DART_AMENDMENT_MARKER, "").strip()
        if stripped and stripped == (previous_title or "").strip():
            return False
    return any(pattern.search(text) for pattern in _MATERIAL_AMENDMENT_TERMS)


def tier_for_amendment(category: str, title: str, *, previous_title: str | None = None) -> NewsMaterialityTier:
    """Watchlist, unless the title shows a change to amounts, prices or
    key terms — in which case the base document's own tier applies."""
    if not amendment_changes_material_terms(title, previous_title=previous_title):
        return _AMENDMENT_TIER
    base_tier = FILING_CATEGORY_TIERS.get(base_category_of(category))
    return base_tier or _AMENDMENT_TIER


# ============================================================
# Tiers a filing's own numbers decide
# ============================================================
#
# The table above answers "what tier does this DOCUMENT TYPE get",
# which is the right question for a filing whose materiality comes
# from what it is. Four Korean disclosure types are different: a
# treasury-share decision is routine at ₩1bn and front-page at ₩15tn,
# and the document type is identical either way. Where the filing
# states the size, the size decides.
#
# This runs BEFORE the document-type table and overrides it, and only
# for the templates named below — a supply contract and a facility
# investment keep their category tier, because nobody asked for those
# to move and a threshold nobody has reviewed is worse than a
# consistent rule.

#: "A meaningful amount". One trillion won is roughly where a Korean
#: large-cap's treasury action stops being housekeeping: Samsung's
#: ₩15.0tn buyback and SK Hynix's ₩40.0tn clear it, Hanwha
#: Aerospace's ₩149.6bn does not.
LARGE_TREASURY_ACTION_WON = 1_000_000_000_000

#: Deliberately absent: "≥1% of shares outstanding", which the brief
#: also offered. A Korean treasury filing does NOT state the action as
#: a percentage of shares outstanding. It states EXISTING treasury
#: holdings as a percentage ("취득 전 자기주식 보유현황 ... 비율(%)
#: 1.4"), which is a different number about a different quantity.
#: Shares outstanding could be back-computed from that pair, but 1.4%
#: carries two significant figures, so the result would be ±3% — and
#: Samsung's buyback lands at 0.93% of the implied total, right on the
#: threshold where that error decides the tier. The amount test is
#: exact; the ratio test would be a coin flip dressed as arithmetic.


def tier_for_structured_facts(
    facts: StructuredFilingFacts | None,
    dividend_change: DividendChange | None = None,
) -> NewsMaterialityTier | None:
    """The tier this filing's own figures justify, or None to leave
    the document-type table in charge.

    None rather than a default for the two templates not covered, and
    None whenever the figure the rule needs was withheld — a rule that
    cannot be evaluated must not quietly pick a side."""
    if facts is None or facts.template in (SUPPLY_CONTRACT, FACILITY_INVESTMENT):
        return None

    if facts.template == PROVISIONAL_RESULTS:
        return NewsMaterialityTier.HIGH_SIGNAL

    if facts.template == CASH_DIVIDEND:
        # Early by default. High when the filing says it is special —
        # which nothing in this corpus does — or when it is at least
        # twice, or at most half, the issuer's previous rate. That
        # second test is what catches Samsung's ₩4,601 against its own
        # ₩374: the filing itself still calls it 분기배당, so only the
        # comparison can tell the two apart.
        material_change = dividend_change is not None and dividend_change.is_material
        return (
            NewsMaterialityTier.HIGH_SIGNAL
            if facts.is_special_dividend or material_change
            else NewsMaterialityTier.WATCHLIST
        )

    if facts.template in (TREASURY_ACQUISITION, TREASURY_DISPOSAL):
        if facts.amount_won is None:
            return None
        large = facts.amount_won >= LARGE_TREASURY_ACTION_WON
        if facts.template == TREASURY_ACQUISITION:
            return NewsMaterialityTier.HIGH_SIGNAL if large else NewsMaterialityTier.WATCHLIST
        # A disposal is the issuer selling its own shares back into
        # the market. Routine at any ordinary size, which is why it
        # starts at Background rather than Early.
        return NewsMaterialityTier.WATCHLIST if large else NewsMaterialityTier.BACKGROUND

    return None
