"""Filings -> Signals bridge (filing_signal_bridge, filing_signal_tiers).

NVIDIA's 2026-11-23 8-K reinstating its chief executive was ingested,
became a candidate, and could never reach Signals — filings live in
`candidates`, Signals reads `editorial_stories`. The three adapters
that map a filing into the Daily News domain existed, were tested, and
ran in shadow mode with their output discarded.

Every fixture here is a real filing from the live store, and the two
tests that matter most are the ones asserting what the bridge will NOT
do: it never invents a tier for a category it cannot place, and it
never promotes an amendment on the document type alone."""
from __future__ import annotations

import pytest

from src.data_access.daily_news.filing_signal_bridge import (
    PENDING_RECHECK_MAX_DAYS,
    build_filing_signal_story,
    card_label_for,
    has_pending_items,
    project_filing_signals,
    recheck_pending_8k_items,
    story_id_for,
)
from src.data_access.daily_news.filing_signal_tiers import (
    FILING_CATEGORY_TIERS,
    tier_for_amendment,
    tier_for_filing_category,
)
from src.data_access.edinet.edinet_rules import DEFAULT_CODE_CATEGORY_MAP
from src.models.daily_news_models import NewsMaterialityTier
from src.models.models import FilingEvent

NOW = "2026-11-24T00:00:00+00:00"
ITEM_502_EXCERPT = "Item 5.02 Departure of Directors or Certain Officers; Election of Directors."


def _edgar(**overrides) -> FilingEvent:
    """NVIDIA's real identifiers, so company resolution runs against
    the real tracked registry rather than a stub."""
    base = dict(
        rcept_no="0001045810-26-000200", corp_code="0001045810", corp_name="NVIDIA Corporation",
        stock_code="NVDA", report_nm="8-K", pblntf_ty="8-K", rcept_dt="2026-11-23",
        flr_nm="NVIDIA", source_name="SEC EDGAR",
        source_url="https://www.sec.gov/Archives/edgar/x", retrieved_at=NOW,
    )
    base.update(overrides)
    return FilingEvent(**base)


# --- the filing that started this ------------------------------------

def test_the_nvidia_governance_8k_becomes_a_high_signal():
    story = build_filing_signal_story(
        _edgar(), matched_rules=("governance_or_management_change:8-K item 5.02",), retrieved_at=NOW,
    )
    assert story is not None
    assert story.materiality_tier is NewsMaterialityTier.HIGH_SIGNAL
    assert story.materiality_reasons[0] == "filing_event:governance_or_management_change"
    assert story.matched_companies == ("NVIDIA",), "resolved against the tracked registry, not the filing's own name"
    assert story.publisher == "SEC EDGAR"
    assert story.source_feed_id == "filing-sec-edgar"
    assert story.excerpt is None, "a filing's summary comes from extraction, never invented here"


def test_the_story_id_is_deterministic_so_a_replay_upserts():
    filing = _edgar()
    assert story_id_for(filing) == story_id_for(_edgar())
    assert story_id_for(_edgar(rcept_no="other")) != story_id_for(filing)


# --- what it refuses to do -------------------------------------------

def test_an_unmapped_category_is_never_given_a_default_tier():
    """The single most important property. A default of High would
    turn every unrecognised document type into front-page material;
    a default of Watchlist would quietly bury a new one. There is no
    default."""
    assert tier_for_filing_category("technology_or_product") is None
    assert tier_for_filing_category("") is None
    assert tier_for_filing_category("something_invented") is None


def test_a_filing_whose_category_has_no_tier_is_not_projected():
    story = build_filing_signal_story(
        _edgar(), matched_rules=("regulation_fd_disclosure:8-K item 7.01",), retrieved_at=NOW,
    )
    assert story is None, "Reg FD is excluded by the adapter and must not slip through here"


def test_an_unroutable_source_is_not_projected():
    story = build_filing_signal_story(
        _edgar(source_name="Some Other Venue"), matched_rules=(), retrieved_at=NOW,
    )
    assert story is None


def test_an_untracked_company_is_not_projected():
    """Company identity comes from the tracked registry. A filing we
    cannot attribute is not shown, rather than shown with the filer's
    own unverified name."""
    story = build_filing_signal_story(
        _edgar(stock_code="ZZZZ"), matched_rules=("governance_or_management_change:8-K item 5.02",),
        retrieved_at=NOW,
    )
    assert story is None


# --- amendments -------------------------------------------------------

def test_an_amendment_is_watchlist_on_the_document_type_alone():
    """In Korean and Japanese practice an amended major-decision filing
    is often where the real numbers land — and equally often a typo
    fix. The document type cannot tell those apart, so it never
    promotes one."""
    assert tier_for_amendment(
        "amended_supply_or_sales_contract", "[기재정정]단일판매ㆍ공급계약 체결",
    ) is NewsMaterialityTier.WATCHLIST


def test_an_amendment_that_restates_amounts_earns_its_base_tier():
    assert tier_for_amendment(
        "amended_supply_or_sales_contract", "[기재정정]단일판매ㆍ공급계약 체결 금액 변경",
    ) is NewsMaterialityTier.HIGH_SIGNAL


@pytest.mark.parametrize("title", [
    "[기재정정]타법인주식 및 출자증권 취득결정 취득금액 정정",
    "訂正臨時報告書 取得価額の変更",
    "Amendment No. 1 restating the purchase price",
])
def test_material_amendment_vocabulary_in_three_languages(title):
    assert tier_for_amendment("amended_securities_registration", title) is not NewsMaterialityTier.WATCHLIST


def test_an_amendment_whose_title_is_unchanged_is_clerical():
    """A title that says "amount" because the ORIGINAL said "amount"
    is not evidence that an amount changed."""
    original = "단일판매ㆍ공급계약 체결 금액"
    assert tier_for_amendment(
        "amended_supply_or_sales_contract", f"[기재정정]{original}", previous_title=original,
    ) is NewsMaterialityTier.WATCHLIST


def test_every_amendment_category_resolves_even_when_unlisted():
    """Amendments resolve by prefix, so adding one to the EDINET map
    cannot accidentally leave it tierless — or give it High."""
    assert tier_for_filing_category("amended_never_heard_of_it") is NewsMaterialityTier.WATCHLIST


# --- pending-items 8-K re-check ---------------------------------------

def test_a_pending_items_8k_recovers_from_its_own_excerpt():
    """Eight of thirty-two filings in the live window were lost this
    way — not unwanted, merely unresolved when SEC had not yet
    published the item numbers."""
    assert has_pending_items(("material_event_8k_pending_items:8-K",))
    recovered = recheck_pending_8k_items(
        ("material_event_8k_pending_items:8-K",),
        excerpt=ITEM_502_EXCERPT, filing_date="2026-11-23", now=NOW,
    )
    assert recovered == ("governance_or_management_change:8-K item 5.02",)


def test_the_recovered_items_carry_the_filing_all_the_way_to_high_signal():
    story = build_filing_signal_story(
        _edgar(), matched_rules=("material_event_8k_pending_items:8-K",), retrieved_at=NOW,
        excerpt=ITEM_502_EXCERPT,
    )
    assert story is not None and story.materiality_tier is NewsMaterialityTier.HIGH_SIGNAL


def test_the_recheck_is_bounded_so_it_does_not_retry_forever():
    """An 8-K whose items never arrive would otherwise be re-derived on
    every tick, for ever."""
    assert recheck_pending_8k_items(
        ("material_event_8k_pending_items:8-K",),
        excerpt=ITEM_502_EXCERPT, filing_date="2026-01-01", now=NOW,
    ) is None
    assert PENDING_RECHECK_MAX_DAYS <= 30, "a window this wide stops being a window"


@pytest.mark.parametrize("filing_date", ["", "not-a-date"])
def test_an_unusable_filing_date_fails_the_window_rather_than_opening_it(filing_date):
    assert recheck_pending_8k_items(
        ("material_event_8k_pending_items:8-K",),
        excerpt=ITEM_502_EXCERPT, filing_date=filing_date, now=NOW,
    ) is None


def test_a_bare_calendar_date_is_handled():
    """filing.rcept_dt is a bare date while `now` is an aware
    timestamp. A naive-vs-aware guard alone rejected every real
    comparison and silently disabled the whole re-check."""
    assert recheck_pending_8k_items(
        ("material_event_8k_pending_items:8-K",),
        excerpt=ITEM_502_EXCERPT, filing_date="2026-11-23", now=NOW,
    ) is not None


def test_the_recheck_does_nothing_for_a_filing_that_is_not_pending():
    assert recheck_pending_8k_items(
        ("earnings_or_results:8-K item 2.02",),
        excerpt=ITEM_502_EXCERPT, filing_date="2026-11-23", now=NOW,
    ) is None


# --- EDINET mapping ----------------------------------------------------

def test_every_verified_edinet_category_has_a_tier():
    """The map and the tiers are edited in different files; this is
    what stops a newly verified triplet from being projectable with no
    tier, or silently dropped."""
    for triplet, category in DEFAULT_CODE_CATEGORY_MAP.items():
        assert tier_for_filing_category(category) is not None, f"{triplet} -> {category}"


@pytest.mark.parametrize("triplet,expected", [
    ("010:053000:180", NewsMaterialityTier.HIGH_SIGNAL),   # 臨時報告書
    ("010:102000:180", NewsMaterialityTier.HIGH_SIGNAL),   # 臨時報告書, second form code
    ("010:030000:120", NewsMaterialityTier.HIGH_SIGNAL),   # 有価証券報告書
    ("010:043A00:160", NewsMaterialityTier.HIGH_SIGNAL),   # 半期報告書
    ("010:024000:030", NewsMaterialityTier.HIGH_SIGNAL),   # 有価証券届出書（新規公開時）
    ("010:110000:080", NewsMaterialityTier.WATCHLIST),     # 発行登録書
    ("010:170000:220", NewsMaterialityTier.WATCHLIST),     # 自己株券買付状況報告書
    ("010:054000:200", NewsMaterialityTier.WATCHLIST),     # 親会社等状況報告書
    ("010:113001:090", NewsMaterialityTier.WATCHLIST),     # 訂正発行登録書
])
def test_the_approved_edinet_tiers(triplet, expected):
    assert tier_for_filing_category(DEFAULT_CODE_CATEGORY_MAP[triplet]) is expected


def test_the_second_form_code_of_each_duplicated_type_is_mapped():
    """The verification pass found that 臨時報告書 and 半期報告書 each
    have two form codes. Mapping one silently loses the other, which
    is invisible until filings go missing."""
    extraordinary = [k for k, v in DEFAULT_CODE_CATEGORY_MAP.items() if v == "extraordinary_report"]
    semi_annual = [k for k, v in DEFAULT_CODE_CATEGORY_MAP.items() if v == "semi_annual_report"]
    assert len(extraordinary) >= 2, extraordinary
    assert len(semi_annual) >= 2, semi_annual


def test_no_non_corporate_ordinance_is_mapped():
    """ordinanceCode 030 is investment trusts and 060 is third-party
    shareholding reporting. Both are high volume and neither is a
    tracked issuer's own event."""
    for triplet in DEFAULT_CODE_CATEGORY_MAP:
        assert triplet.startswith("010:"), triplet


# --- card labels -------------------------------------------------------

@pytest.mark.parametrize("category,label", [
    ("ipo_registration", "IPO"),
    ("shelf_registration", "Financing"),
    ("share_buyback_status", "Routine"),
    ("amended_extraordinary_report", "Amendment"),
    ("governance_or_management_change", None),
])
def test_the_card_label(category, label):
    assert card_label_for(category) == label


def test_the_label_is_carried_on_the_story():
    story = build_filing_signal_story(
        _edgar(), matched_rules=("governance_or_management_change:8-K item 5.02",), retrieved_at=NOW,
    )
    assert not any(r.startswith("filing_label:") for r in story.materiality_reasons)


# --- batch projection --------------------------------------------------

def test_two_filings_sharing_a_form_name_are_both_projected():
    """THE BUG THIS FUNCTION SHIPPED WITH, and the reason dedupe is
    keyed on story id.

    The first version suppressed any filing whose (publisher,
    headline) pair had been seen. A filing's headline is its FORM
    NAME: every SEC 8-K is titled "8-K". So one EDGAR 8-K survived per
    run and every other one was discarded as a duplicate of an
    unrelated company's filing.

    Observed live on the first tick after the flag went on: 14
    projectable EDGAR filings collapsed to 4, one per distinct form
    name — and NVIDIA's Item 5.02 governance filing, the single filing
    this whole feature was built for, was one of the ten discarded."""
    governance = (_edgar(rcept_no="a1"), ("governance_or_management_change:8-K item 5.02",))
    earnings = (_edgar(rcept_no="a2"), ("earnings_or_results:8-K item 2.02",))
    other_company = (
        _edgar(rcept_no="a3", corp_code="0001158114", stock_code="CSCO", corp_name="Cisco Systems, Inc."),
        ("earnings_or_results:8-K item 2.02",),
    )

    stories = project_filing_signals((governance, earnings, other_company), retrieved_at=NOW)

    assert len(stories) == 3, [s.materiality_reasons[0] for s in stories]
    assert any("governance_or_management_change" in s.materiality_reasons[0] for s in stories)
    # The form name no longer reaches the card at all — item 4 replaced
    # it with an event headline (filing_event_headline.py). That removes
    # the collision these three filings used to have, so the guard above
    # no longer exercises the failure on its own; the next test keeps
    # it exercised with headlines that really are identical.
    assert "8-K" not in {s.headline for s in stories}


def test_two_filings_with_the_SAME_headline_are_still_both_projected():
    """The guard the test above used to carry, kept alive deliberately.

    Two filings by one company, same category, both falling back to the
    same "[Company] — [event] (Form 8-K)" headline. If dedupe ever moves
    back to (publisher, headline), this is the test that fails."""
    first = (_edgar(rcept_no="b1"), ("earnings_or_results:8-K item 2.02",))
    second = (_edgar(rcept_no="b2"), ("earnings_or_results:8-K item 2.02",))

    stories = project_filing_signals((first, second), retrieved_at=NOW)

    assert len({s.headline for s in stories}) == 1, "the premise: one headline, two filings"
    assert len(stories) == 2, "both survive, because dedupe is keyed on story id"


def test_the_same_filing_twice_in_one_batch_is_projected_once():
    """Story ids are deterministic on (source, corp_code, rcept_no) —
    the same key the filing store deduplicates by — so a repeat upserts
    rather than duplicating."""
    filing = _edgar()
    rules = ("governance_or_management_change:8-K item 5.02",)
    stories = project_filing_signals(((filing, rules), (filing, rules)), retrieved_at=NOW)
    assert len(stories) == 1


def test_a_news_item_about_the_same_event_is_not_deduped_against():
    """A filing is the primary source and a news story is a third
    party's account of it. Showing both is the point — which is also
    why this function no longer looks at news headlines at all."""
    import inspect

    source = inspect.getsource(project_filing_signals)
    assert "existing_headlines" not in source, (
        "cross-checking against news headlines would suppress a primary source "
        "because a journalist wrote about it"
    )


def test_an_empty_batch_is_not_an_error():
    assert project_filing_signals((), retrieved_at=NOW) == ()


# --- the tier table itself ---------------------------------------------

def test_no_tier_entry_is_an_amendment():
    """Amendments resolve by prefix. An explicit amended_* row in the
    table would be a second, conflicting source of truth."""
    assert not [c for c in FILING_CATEGORY_TIERS if c.startswith("amended_")]


# --- item 4: the card's headline and summary --------------------------

_ADI_2_02 = (
    "Item 2.02. Results of Operations and Financial Condition On November 24, 2026, "
    "NVIDIA Corporation announced its financial results for its third quarter and "
    "fiscal year ended October 31, 2026. The full text of the press release is "
    "furnished herewith as Exhibit 99.1."
)
_XBRL = (
    "nvda-20261031 0001045810 False 2026 FY "
    "http://fasb.org/us-gaap/2026#AccruedIncomeTaxesCurrent"
)


def test_the_card_no_longer_shows_the_form_name_as_its_headline():
    story = build_filing_signal_story(
        _edgar(), matched_rules=("earnings_or_results:8-K item 2.02",), retrieved_at=NOW,
        excerpt=_ADI_2_02, item_anchor="Item Item 2.02",
    )
    assert story is not None
    assert story.headline != "8-K"
    assert story.headline.startswith("NVIDIA Corporation announced its financial results")


def test_the_headlines_provenance_is_recorded_on_the_story():
    """A dry run, an audit and a future repair all need to tell an
    extracted headline from a fallback without re-deriving either."""
    extracted = build_filing_signal_story(
        _edgar(), matched_rules=("earnings_or_results:8-K item 2.02",), retrieved_at=NOW,
        excerpt=_ADI_2_02,
    )
    fell_back = build_filing_signal_story(
        _edgar(), matched_rules=("earnings_or_results:8-K item 2.02",), retrieved_at=NOW,
        excerpt=_XBRL,
    )
    assert "filing_headline:excerpt" in extracted.materiality_reasons
    assert "filing_headline:fallback" in fell_back.materiality_reasons


def test_a_readable_english_excerpt_becomes_the_cards_summary():
    """Was unconditionally None: the filing cards showed no summary at
    all while the text sat in the candidate the worker had already
    loaded."""
    story = build_filing_signal_story(
        _edgar(), matched_rules=("earnings_or_results:8-K item 2.02",), retrieved_at=NOW,
        excerpt=_ADI_2_02,
    )
    assert story.excerpt
    assert story.excerpt.startswith("The full text of the press release")


def test_raw_xbrl_is_never_shown_as_a_summary():
    story = build_filing_signal_story(
        _edgar(), matched_rules=("earnings_or_results:8-K item 2.02",), retrieved_at=NOW,
        excerpt=_XBRL,
    )
    assert story.excerpt is None
    assert story.headline == "NVIDIA — Results of operations (Form 8-K)"


def _summary(filing, **kw):
    from src.data_access.daily_news.filing_signal_bridge import _card_excerpt

    kw.setdefault("excerpt", None)
    kw.setdefault("excerpt_translated", None)
    kw.setdefault("headline", "Some headline")
    kw.setdefault("headline_sentence", None)
    return _card_excerpt(filing, **kw)


def test_an_untranslated_korean_excerpt_is_never_shown_to_an_english_reader():
    """The bridge is not permitted to translate, and a Korean paragraph
    on an English card is not a summary. Only an already-translated
    excerpt is shown for a non-English source."""
    korean = _edgar(source_name="OpenDART / DART", original_language="Korean")
    assert _summary(korean, excerpt="중대재해 발생 장소 사외기숙사") is None
    assert _summary(
        korean, excerpt="중대재해 발생 장소 사외기숙사",
        excerpt_translated=(
            "LG Innotek / Serious Accident / (November 19, 2026) A serious industrial "
            "accident occurred at an off-site dormitory in Gumi, North Gyeongsang Province."
        ),
    ) == (
        "A serious industrial accident occurred at an off-site dormitory in Gumi, "
        "North Gyeongsang Province."
    ), "the DART cover-page header is stripped; the statement itself is kept"


def test_a_translated_dart_cover_page_masthead_is_not_a_summary():
    """Nine of the eighteen summaries in the first production dry run
    were this: a heading, a form-version marker and the issuer's own
    name. It passes is_readable_extracted_text, which is why the
    stricter gate has to run too."""
    korean = _edgar(
        source_name="OpenDART / DART", corp_name="LG Innotek Co., Ltd.",
        flr_nm="LG Innotek Co., Ltd.", original_language="Korean",
    )
    assert _summary(
        korean, excerpt="반기보고서",
        excerpt_translated="Semiannual Report 6.7 LG Innotek Co., Ltd.",
        headline="Earnings or Results Report — 반기보고서 (2026.06)",
    ) is None


def test_the_summary_does_not_repeat_the_sentence_used_as_the_headline():
    sentence = (
        "On November 24, 2026, NVIDIA Corporation announced its financial results "
        "for its third quarter and fiscal year ended October 31, 2026."
    )
    summary = _summary(
        _edgar(), excerpt=_ADI_2_02,
        headline="NVIDIA Corporation announced its financial results",
        headline_sentence=sentence,
    )
    assert summary
    assert "announced its financial results" not in summary
    assert summary.startswith("The full text of the press release")


# --- DART structured-field headlines ----------------------------------

_CROATIA = (
    "한화에어로스페이스/단일판매·공급계약 체결(자율공시)/(2026.09.11)단일판매·공급계약 체결(자율공시) "
    "1. 판매·공급계약 구분 상품공급 2. 계약내역 계약금액(원) 641,095,389,786 "
    "최근매출액(원) 26,702,901,248,815 매출액대비(%) 2.40 "
    "3. 계약상대 크로아티아 국방부 (The Ministry of Defence of the Republic of Croatia) "
    "- 회사와의 관계 - 5. 계약기간 시작일 2026-09-10 종료일 2030-09-09"
)


def test_a_dart_field_table_beats_the_curated_document_type():
    """"Supply or Sales Contract — 단일판매·공급계약체결" says what KIND
    of filing this is. The field table says what happened."""
    from src.data_access.daily_news.filing_signal_bridge import _structured_dart_headline

    result = _structured_dart_headline("Hanwha Aerospace Co., Ltd.", _CROATIA)
    assert result is not None
    assert result.provenance == "structured_fields"
    assert result.headline == (
        "Hanwha Aerospace Co., Ltd. — ₩641.1bn supply contract with "
        "The Ministry of Defence of the Republic of Croatia (2.40% of recent revenue)"
    )


def test_a_dart_filing_with_no_field_table_still_gets_the_curated_phrase():
    from src.data_access.daily_news.filing_signal_bridge import _structured_dart_headline

    assert _structured_dart_headline("LG Innotek Co., Ltd.", "중대재해 발생 장소 사외기숙사") is None


def test_the_exact_filed_figure_reaches_the_card_without_any_translation():
    """These disclosures often have no translated excerpt at all, and
    the bargain behind rounding the headline is that the filed number
    is always on the card. It must not depend on a translation."""
    korean = _edgar(source_name="OpenDART / DART", original_language="Korean")
    assert _summary(korean, excerpt=_CROATIA, excerpt_translated=None) == (
        "Contract value as filed: ₩641,095,389,786. Term: 2026-09-10 to 2030-09-09."
    )
