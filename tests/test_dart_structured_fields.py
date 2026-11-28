"""Structured facts from a Korean disclosure's field table.

Every excerpt here is the real stored text of a real OpenDART filing,
transcribed from the read-only survey in
scripts/survey_dart_structured_fields.py. The two that matter most are
the two that are NOT the happy path: Hanwha's 2026-08-31 contract,
which withheld both figures while filing the company's own revenue in
the next field, and Samsung's 장래사업·경영계획, which mentions an
investment amount in running prose and is not a field table at all."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.data_access.daily_news import dart_structured_fields as D
from src.data_access.daily_news.filing_signal_tiers import tier_for_structured_facts
from src.models.daily_news_models import NewsMaterialityTier

CROATIA = (
    "한화에어로스페이스/단일판매·공급계약 체결(자율공시)/(2026.09.11)단일판매·공급계약 체결(자율공시) "
    "1. 판매·공급계약 구분 상품공급 - 세부내용 크로아티아 천무 다연장로켓 수출사업 "
    "2. 계약내역 계약금액(원) 641,095,389,786 최근매출액(원) 26,702,901,248,815 "
    "매출액대비(%) 2.40 대규모법인여부 해당 "
    "3. 계약상대 크로아티아 국방부 (The Ministry of Defence of the Republic of Croatia) "
    "- 회사와의 관계 - 4. 판매·공급지역 크로아티아 "
    "5. 계약기간 시작일 2026-09-10 종료일 2030-09-09 6. 주요 계약조건 계약금·선급금 유무 유"
)

#: Both figures withheld, counterparty named, and the company's OWN
#: revenue sitting in the very next field.
INDRA = (
    "한화에어로스페이스/단일판매·공급계약체결/(2026.08.31)단일판매·공급계약체결 "
    "1. 판매·공급계약 구분 상품공급 - 체결계약명 스페인 K9 자주포 등 수출 실행계약 (Derivative Contract) "
    "2. 계약내역 계약금액(원) - 최근매출액(원) 26,702,901,248,815 매출액대비(%) - 대규모법인여부 해당 "
    "3. 계약상대 INDRA SISTEMAS S.A. - 회사와의 관계 -"
)

KAI = (
    "한국항공우주/단일판매·공급계약체결/(2026.10.08)단일판매·공급계약체결 "
    "1. 판매·공급계약 구분 상품공급 - 세부내용 MUH-1 4차 PBL(기체)사업 "
    "2. 계약내역 계약금액(원) 109,085,117,769 매출액대비(%) 3.0 "
    "3. 계약상대 방위사업청 - 회사와의 관계 - 5. 계약기간 시작일 2026-10-08 종료일 2031-12-31"
)

HYNIX_M17 = (
    "SK하이닉스/신규시설투자등/(2026.08.07)신규시설투자등 1. 투자구분 신규시설투자 - 투자대상 청주 M17 건설 "
    "2. 투자내역 투자금액(원) 19,100,000,000,000 자기자본(원) 120,666,750,890,698 "
    "자기자본대비(%) 15.83 대규모법인여부 해당 3. 투자목적 메모리 수요 대응 위한 중장기 생산 기반 확보"
)

AMENDMENT = (
    "SK하이닉스/타법인주식및출자증권취득결정/(2026.11.20)타법인주식및출자증권취득결정 정정신고(보고) "
    "정정일자 2026-11-20 1. 정정관련 공시서류 타법인 주식 및 출자증권 취득결정 "
    "2. 정정사항 정정항목 정정전 정정후 투자금액(원) 100 200"
)

SAMSUNG_PROSE = (
    "삼성전자/장래사업·경영계획(공정공시)/(2026.10.29)장래사업·경영계획(공정공시) "
    "투자금액 규모는 추후 확정 예정이며 이사회 결의 시 재공시할 예정입니다."
)


# --- the two numbers a headline is built from -------------------------

def test_the_filed_figures_are_read_exactly():
    facts = D.parse_structured_fields(CROATIA)
    assert facts.template == D.SUPPLY_CONTRACT
    assert facts.amount_won == 641_095_389_786
    assert facts.ratio_percent == Decimal("2.40")
    assert facts.start_date == "2026-09-10" and facts.end_date == "2030-09-09"


def test_a_facility_investment_reads_its_own_pair():
    facts = D.parse_structured_fields(HYNIX_M17)
    assert facts.template == D.FACILITY_INVESTMENT
    assert facts.amount_won == 19_100_000_000_000
    assert facts.ratio_percent == Decimal("15.83")


def test_a_value_never_runs_into_the_next_numbered_section():
    """판매·공급지역 is followed by "5. 계약기간", whose label this
    module does not track, so without a section-marker cut the region
    reads as "크로아티아 5. 계약기간"."""
    facts = D.parse_structured_fields(CROATIA)
    assert facts.region == "크로아티아"
    assert facts.detail == "크로아티아 천무 다연장로켓 수출사업"


# --- the dash ---------------------------------------------------------

def test_a_withheld_figure_is_never_filled_in_from_the_next_field():
    """THE failure this module exists to avoid. 계약금액(원) is "-" and
    최근매출액(원) — the company's OWN revenue — is the next number on
    the line. Reaching for it publishes "₩26.7tn contract"."""
    facts = D.parse_structured_fields(INDRA)
    assert facts.amount_won is None
    assert facts.ratio_percent is None
    assert "amount" in facts.withheld and "ratio" in facts.withheld
    assert "26,702,901,248,815" not in (D.structured_headline("Hanwha Aerospace Co., Ltd.", facts) or "")


def test_a_named_counterparty_carries_a_headline_on_its_own():
    facts = D.parse_structured_fields(INDRA)
    assert D.structured_headline("Hanwha Aerospace Co., Ltd.", facts) == (
        "Hanwha Aerospace Co., Ltd. — supply contract with INDRA SISTEMAS S.A. "
        "(contract value withheld)"
    )


def test_withheld_figures_and_nobody_named_produces_nothing():
    facts = D.parse_structured_fields(
        "1. 판매·공급계약 구분 상품공급 2. 계약내역 계약금액(원) - 매출액대비(%) - 3. 계약상대 -"
    )
    assert D.structured_headline("Hanwha Aerospace Co., Ltd.", facts) is None


# --- what is NOT read -------------------------------------------------

def test_an_amendment_is_not_read_at_all():
    """Its labels appear twice, once under 정정전 and once under
    정정후, so picking a value is a coin flip between a superseded
    figure and a current one."""
    assert D.is_amendment_body(AMENDMENT)
    assert D.parse_structured_fields(AMENDMENT) is None


def test_a_prose_mention_of_an_amount_is_not_a_field_table():
    """Samsung's 장래사업·경영계획 says an investment amount is still
    to be determined. Matching the bare label claimed a template this
    module cannot read; requiring the printed "(원)" unit does not."""
    assert D.detect_template(SAMSUNG_PROSE) is None
    assert D.parse_structured_fields(SAMSUNG_PROSE) is None


# --- names ------------------------------------------------------------

def test_the_filings_own_english_rendering_is_used_when_it_prints_one():
    facts = D.parse_structured_fields(CROATIA)
    name = D.counterparty_for_display(facts.counterparty)
    assert name.text == "The Ministry of Defence of the Republic of Croatia"
    assert name.as_filed is False


def test_a_latin_script_counterparty_is_kept_exactly_as_filed():
    facts = D.parse_structured_fields(INDRA)
    name = D.counterparty_for_display(facts.counterparty)
    assert name.text == "INDRA SISTEMAS S.A." and name.as_filed is False


def test_a_korean_counterparty_is_shown_as_filed_and_never_romanised():
    facts = D.parse_structured_fields(KAI)
    name = D.counterparty_for_display(facts.counterparty)
    assert name.text == "방위사업청"
    assert name.as_filed is True
    headline = D.structured_headline("Korea Aerospace Industries, Ltd.", facts)
    assert headline == (
        "Korea Aerospace Industries, Ltd. — ₩109.1bn supply contract with "
        "방위사업청 (as filed) (3.0% of recent revenue)"
    )


def test_registry_resolution_matches_nothing_until_native_name_is_populated():
    """Written, and currently inert by design: TrackedCompany.
    native_name is empty for all twelve DART-tracked companies. The
    point of asserting it is that the alternative — matching on a
    romanisation this module invents — must never quietly appear."""
    from src.config.tracked_companies import get_tracked_companies

    dart = [c for c in get_tracked_companies() if c.source == "OpenDART / DART"]
    assert dart, "the cohort should not be empty"
    assert not any(c.native_name for c in dart)


# --- figures on the card ----------------------------------------------

def test_the_headline_rounds_and_the_card_keeps_the_filed_figure():
    facts = D.parse_structured_fields(CROATIA)
    assert "₩641.1bn" in D.structured_headline("Hanwha Aerospace Co., Ltd.", facts)
    assert D.exact_figure_note(facts) == (
        "Contract value as filed: ₩641,095,389,786. Term: 2026-09-10 to 2030-09-09."
    )


def test_there_is_no_exact_note_when_there_was_no_figure():
    assert D.exact_figure_note(D.parse_structured_fields(INDRA)) is None


@pytest.mark.parametrize("amount,expected", [
    (641_095_389_786, "₩641.1bn"),
    (19_100_000_000_000, "₩19.1tn"),
    (35_224_600_000_000, "₩35.2tn"),
    (109_085_117_769, "₩109.1bn"),
    (4_500_000, "₩4.5m"),
    (900, "₩900"),
])
def test_rounded_won(amount, expected):
    assert D.rounded_won(amount) == expected


# ============================================================
# Phase 2 templates
# ============================================================

DIVIDEND = (
    "삼성전자/현금·현물배당 결정/(2026.07.30)현금·현물배당 결정 1. 배당구분 분기배당 "
    "2. 배당종류 현금배당 - 현물자산의 상세내역 - "
    "3. 1주당 배당금(원) 보통주식 374 종류주식 374 - 차등배당 여부 미해당 "
    "4. 시가배당률(%) 보통주식 0.1 종류주식 0.2 5. 배당금총액(원) 2,455,894,601,644 "
    "6. 배당기준일 2026-06-30 7. 배당금지급 예정일자 2026-08-28"
)

BUYBACK = (
    "삼성전자/주요사항보고서(자기주식취득결정)/ 자기주식 취득 결정 "
    "1. 취득예정주식(주) 보통주식 53,285,968 기타주식 - "
    "2. 취득예정금액(원) 보통주식 14,999,999,992,000 기타주식 - "
    "3. 취득예상기간 시작일 2026-08-24 종료일 2026-11-21 "
    "4. 보유예상기간 시작일 - 종료일 - 5. 취득목적 임직원 주식보상"
)

RESULTS = (
    "LG이노텍/연결재무제표기준영업(잠정)실적(공정공시)/(2026.10.27) "
    "실적기간 당기실적 2026-07-01 ~ 2026-09-30 전기실적 2026-04-01 ~ 2026-06-30 "
    "※ 동 정보는 잠정치로서 향후 확정치와는 다를 수 있음. 1. 연결실적내용 단위 : 백만원, % "
    "구분 당기실적 전기실적 전기대비 증감율(%) 흑자적자전환여부 전년동기실적 전년동기대비 증감율(%) 흑자적자전환여부 "
    "매출액 당해실적 6,367,124 5,527,241 15.2 - 5,369,431 18.6 - 누계실적 17,429,200"
)


def test_a_cash_dividend_reports_the_total_and_the_per_share_rate():
    facts = D.parse_structured_fields(DIVIDEND)
    assert facts.template == D.CASH_DIVIDEND
    assert facts.amount_won == 2_455_894_601_644
    assert facts.per_share_won == 374
    assert facts.ratio_percent == Decimal("0.1")
    assert D.structured_headline("Samsung Electronics", facts) == (
        "Samsung Electronics — ₩2.5tn dividend, ₩374 per common share (0.1% dividend yield)"
    )
    assert D.exact_figure_note(facts) == (
        "Dividend total as filed: ₩2,455,894,601,644. Record date: 2026-06-30."
    )


def test_the_first_share_class_is_the_one_reported():
    """"보통주식 374 종류주식 374" — the label is followed by a share
    class before its value, and scanning the span for any number
    instead of reading past the sub-label is how the wrong class ends
    up in a headline."""
    facts = D.parse_structured_fields(DIVIDEND)
    assert facts.per_share_won == 374


def test_a_buyback_reports_the_spend_and_the_share_count():
    facts = D.parse_structured_fields(BUYBACK)
    assert facts.template == D.TREASURY_ACQUISITION
    assert facts.amount_won == 14_999_999_992_000
    assert facts.shares == 53_285_968
    assert D.structured_headline("Samsung Electronics", facts) == (
        "Samsung Electronics — ₩15.0tn treasury share buyback (53,285,968 shares)"
    )


def test_the_share_unit_in_a_label_is_not_mistaken_for_a_value():
    """취득예정주식(주) — "(주)" is part of the label DART prints, and
    reading it as the value makes the share count unparseable."""
    assert D.parse_structured_fields(BUYBACK).shares == 53_285_968


# --- the table-shaped one ---------------------------------------------

def test_provisional_results_reads_revenue_in_the_filings_own_unit():
    facts = D.parse_structured_fields(RESULTS)
    assert facts.template == D.PROVISIONAL_RESULTS
    assert facts.amount_won == 6_367_124 * 1_000_000
    assert D.structured_headline("LG Innotek Co., Ltd.", facts) == (
        "LG Innotek Co., Ltd. — ₩6.4tn provisional revenue (+18.6% year on year)"
    )


HYNIX_RESULTS = (
    "SK하이닉스/연결재무제표기준영업(잠정)실적(공정공시)/(2026.07.29) 1. 연결실적내용 단위 : 백만원, % "
    "구분 당기실적 전기실적 전기대비 전년동기실적 전년동기대비 (2026년 2분기)(2026년 1분기) 증감율(%) "
    "흑자적자전환여부 (2025년 2분기) 증감율(%) 흑자적자전환여부 "
    "매출액 당해실적 79,318,746 52,576,287 50.9 - 22,231,952 256.8 - "
    "누계실적 131,895,033 - - - 39,871,093 230.8 - "
    "영업이익 당해실적 60,542,608 37,610,283 61.0 - 9,212,851 557.2 -"
)


@pytest.mark.parametrize("text,company,revenue,yoy", [
    (RESULTS, "LG Innotek Co., Ltd.", 6_367_124, Decimal("18.6")),
    (HYNIX_RESULTS, "SK Hynix", 79_318_746, Decimal("256.8")),
])
def test_the_year_on_year_cell_is_read_from_the_verified_column_contract(
    text, company, revenue, yoy,
):
    """Two real filings establish the row shape, and both are asserted
    here, because a contract proved by one sample is a guess."""
    facts = D.parse_structured_fields(text)
    assert facts.amount_won == revenue * 1_000_000
    assert facts.ratio_percent == yoy


def test_the_year_on_year_cell_is_the_right_one_of_the_two_percentages():
    """50.9 is quarter on quarter and sits three cells earlier.
    Reading it would tell a reader SK Hynix grew 50.9% when it grew
    256.8% — the exact failure the shape check exists to prevent."""
    facts = D.parse_structured_fields(HYNIX_RESULTS)
    headline = D.structured_headline("SK Hynix", facts)
    assert "+256.8% year on year" in headline
    assert "50.9" not in headline


@pytest.mark.parametrize("row,why", [
    ("매출액 당해실적 6,367,124 5,527,241 15.2 - 5,369,431", "row truncated before the cell"),
    ("매출액 당해실적 6,367,124 5,527,241 15.2 5,369,431 18.6 - -", "a swing-flag column missing"),
    ("매출액 당해실적 6,367,124 - - - - - -", "no comparative periods filed"),
])
def test_a_row_that_does_not_match_the_shape_loses_only_the_percentage(row, why):
    """The revenue figure is the first cell and needs no contract, so
    it survives; the percentage does not."""
    facts = D.parse_structured_fields(
        "x 1. 연결실적내용 단위 : 백만원, % " + row
    )
    assert facts is not None and facts.amount_won == 6_367_124 * 1_000_000, why
    assert facts.ratio_percent is None, why


@pytest.mark.parametrize("broken,why", [
    (RESULTS.replace("백만원", "억원"), "a unit this module does not scale"),
    (RESULTS.replace("연결실적내용", "기타내용"), "no results table heading"),
    (RESULTS.replace("당해실적", "기타실적"), "no this-period marker on the revenue row"),
    (RESULTS.replace("매출액 당해실적 6,367,124", "매출액 당해실적 -"), "revenue withheld"),
])
def test_a_results_table_missing_any_guard_is_skipped(broken, why):
    assert D.parse_structured_fields(broken) is None, why


def test_a_label_value_template_still_wins_over_the_table_reader():
    """parse_provisional_results is tried last, so a disclosure that
    is both is never read as the table."""
    facts = D.parse_structured_fields(CROATIA)
    assert facts.template == D.SUPPLY_CONTRACT


# ============================================================
# Operating profit
# ============================================================

DOOSAN_LOSS = (
    "두산로보틱스/연결재무제표기준영업(잠정)실적(공정공시)/(2026.10.30) 1. 연결실적내용 단위 : 백만원, % "
    "매출액 당해실적 16,175 10,167 59.1 - 10,167 59.1 - 누계실적 39,000 "
    "영업이익 당해실적 -13,404 -14,417 7.0 - -15,265 12.2 - 누계실적 -39,891"
)


def test_operating_profit_is_read_with_the_same_contract_as_revenue():
    facts = D.parse_structured_fields(HYNIX_RESULTS)
    assert facts.operating_profit_won == 60_542_608 * 1_000_000
    assert facts.operating_profit_yoy == Decimal("557.2")
    assert D.structured_headline("SK Hynix", facts) == (
        "SK Hynix — ₩79.3tn provisional revenue (+256.8% year on year), "
        "₩60.5tn operating profit (+557.2%)"
    )


def test_a_negative_operating_result_is_named_a_loss_and_loses_its_percentage():
    """두산로보틱스 filed -13,404 for the quarter against a 12.2 change.
    "+12.2%" beside a loss cannot be read without the filing's own sign
    convention, and "₩-13.4bn operating profit" is worse."""
    facts = D.parse_structured_fields(DOOSAN_LOSS)
    assert facts.operating_profit_won == -13_404 * 1_000_000
    headline = D.structured_headline("Doosan Robotics Inc.", facts)
    assert headline.endswith("₩13.4bn operating loss")
    assert "12.2" not in headline


def test_a_missing_operating_row_never_costs_the_revenue_figure():
    """The excerpt is capped and this row sits below the revenue one,
    so it is the first thing a cap removes."""
    revenue_only = HYNIX_RESULTS.split("영업이익")[0]
    facts = D.parse_structured_fields(revenue_only)
    assert facts.amount_won == 79_318_746 * 1_000_000
    assert facts.operating_profit_won is None
    assert "operating" not in D.structured_headline("SK Hynix", facts)


# ============================================================
# Tiers the filing's own numbers decide
# ============================================================

def _facts(template, **kw):
    return D.StructuredFilingFacts(template=template, **kw)


@pytest.mark.parametrize("amount,expected", [
    (15_000_000_000_000, NewsMaterialityTier.HIGH_SIGNAL),   # Samsung ₩15.0tn
    (40_000_000_000_000, NewsMaterialityTier.HIGH_SIGNAL),   # SK Hynix ₩40.0tn
    (149_600_000_000, NewsMaterialityTier.WATCHLIST),        # Hanwha ₩149.6bn
])
def test_a_buyback_is_tiered_on_what_it_costs(amount, expected):
    assert tier_for_structured_facts(_facts(D.TREASURY_ACQUISITION, amount_won=amount)) is expected


@pytest.mark.parametrize("amount,expected", [
    (1_134_980_000, NewsMaterialityTier.BACKGROUND),         # Samsung ₩1.1bn
    (122_590_000, NewsMaterialityTier.BACKGROUND),           # SK Hynix ₩122.6m
    (5_000_000_000_000, NewsMaterialityTier.WATCHLIST),
])
def test_a_disposal_starts_at_background(amount, expected):
    """The issuer selling its own shares back into the market is
    routine at any ordinary size."""
    assert tier_for_structured_facts(_facts(D.TREASURY_DISPOSAL, amount_won=amount)) is expected


def test_a_treasury_filing_that_withheld_its_amount_is_left_to_the_category():
    """A rule that cannot be evaluated must not quietly pick a side."""
    assert tier_for_structured_facts(_facts(D.TREASURY_ACQUISITION)) is None


def test_a_dividend_is_early_unless_the_filing_calls_it_special():
    assert tier_for_structured_facts(
        D.parse_structured_fields(DIVIDEND)
    ) is NewsMaterialityTier.WATCHLIST


def test_no_dividend_in_this_corpus_declares_itself_special():
    """Samsung's ₩4,601-per-share dividend is special against its own
    ₩374 quarterly, and the filing still says 분기배당. Detecting that
    means comparing an issuer against its own previous filing, which
    is not a field-table question — so this asserts the gap rather
    than papering over it."""
    assert D.parse_structured_fields(DIVIDEND).kind == "분기배당"
    assert not D.parse_structured_fields(DIVIDEND).is_special_dividend


def test_provisional_results_are_always_high():
    assert tier_for_structured_facts(
        _facts(D.PROVISIONAL_RESULTS, amount_won=1)
    ) is NewsMaterialityTier.HIGH_SIGNAL


@pytest.mark.parametrize("template", [D.SUPPLY_CONTRACT, D.FACILITY_INVESTMENT])
def test_the_two_shipped_templates_keep_their_category_tier(template):
    """Nobody asked for those to move, and a threshold nobody has
    reviewed is worse than a consistent rule."""
    assert tier_for_structured_facts(_facts(template, amount_won=10**15)) is None
