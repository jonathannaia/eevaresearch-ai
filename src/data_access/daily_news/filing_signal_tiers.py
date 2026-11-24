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
