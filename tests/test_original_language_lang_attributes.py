"""Original-language text carries the right `lang` (Theming + Typography
release). Korean and Japanese filing titles, company names and matched
keywords usually appear inside English sentences ("삼성전자 filed … on
Aug 14"), so a block-level lang would mislabel the English around them.
primitives.cjk_html() tags each Korean/Japanese run instead; these tests
pin the helper and the builders that previously emitted untagged runs.

A rendered audit of the Dashboard, Filings, Signals, Radar Signals and
Research Theses harness pages against the local data cache found 0
untagged CJK text nodes after this change (the cache is not tracked, so
that audit is not reproducible here; these fixtures are)."""
from __future__ import annotations

import re
from html.parser import HTMLParser

from src.models.models import FilingEvent
from src.ui.components import radar_card, regional_brief
from src.ui.components.primitives import cjk_html

_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")


class _UntaggedCjk(HTMLParser):
    """Collects every text node containing Korean/Japanese/Han script
    that has no ancestor carrying a lang attribute."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str | None] = []
        self.untagged: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in {"br", "img", "hr", "input", "wbr"}:
            self.stack.append(dict(attrs).get("lang"))

    def handle_endtag(self, tag):
        if tag not in {"br", "img", "hr", "input", "wbr"} and self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if _CJK.search(data) and not any(self.stack):
            self.untagged.append(data)


def _untagged(html_text: str) -> list[str]:
    parser = _UntaggedCjk()
    parser.feed(html_text)
    return parser.untagged


def _dart(**overrides) -> FilingEvent:
    fields = dict(
        rcept_no="20260814000001", corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm="[기재정정]임원ㆍ주요주주특정증권등소유상황보고서", rcept_dt="20260814", flr_nm="삼성전자",
        source_name="OpenDART / DART", original_language="Korean",
    )
    fields.update(overrides)
    return FilingEvent(**fields)


def _edinet(**overrides) -> FilingEvent:
    fields = dict(
        rcept_no="S100ABCD", corp_code="E01234", corp_name="東京エレクトロン株式会社", stock_code="80350",
        report_nm="有価証券報告書－第46期(2025/04/01－2026/03/31)", rcept_dt="20260620", flr_nm="東京エレクトロン株式会社",
        source_name="EDINET", original_language="Japanese",
    )
    fields.update(overrides)
    return FilingEvent(**fields)


def test_korean_run_inside_an_english_sentence_is_tagged_ko_and_the_english_is_not():
    out = cjk_html("삼성전자 filed 지급수단별ㆍ지급기간별지급금액 on Aug 14, 2026.", "Korean")
    assert out == ('<span lang="ko">삼성전자</span> filed <span lang="ko">지급수단별ㆍ지급기간별지급금액</span> '
                   "on Aug 14, 2026.")


def test_kana_means_japanese_and_han_only_follows_the_source_language():
    assert cjk_html("Annual Securities Report — 有価証券報告書", "Japanese") == (
        'Annual Securities Report — <span lang="ja">有価証券報告書</span>'
    )
    assert cjk_html("東京エレクトロン", None) == '<span lang="ja">東京エレクトロン</span>'
    assert cjk_html("確認書", "Japanese") == '<span lang="ja">確認書</span>'


def test_han_only_run_with_no_known_source_language_is_never_guessed():
    assert cjk_html("確認書", None) == "確認書"
    assert cjk_html("確認書", "English") == "確認書"


def test_latin_letters_glued_to_a_hangul_name_stay_in_the_same_run():
    assert cjk_html("SK하이닉스 · 000660") == '<span lang="ko">SK하이닉스</span> · 000660'


def test_english_only_text_is_just_escaped():
    assert cjk_html("NVIDIA filed 10-K <b> & more", "English") == "NVIDIA filed 10-K &lt;b&gt; &amp; more"
    assert cjk_html(None) == ""


def test_cjk_run_is_escaped_inside_its_span():
    assert cjk_html("삼성<전자>", "Korean") == '<span lang="ko">삼성</span>&lt;<span lang="ko">전자</span>&gt;'


def test_filings_card_identity_line_tags_the_korean_company_name():
    line = radar_card._identity_line(_dart())
    assert line == '<span lang="ko">삼성전자</span> · 005930'
    assert _untagged(line) == []


def test_regional_brief_rows_leave_no_untagged_korean_or_japanese_text():
    for filing in (_dart(), _edinet()):
        row = regional_brief._row_html(filing)
        assert _untagged(row) == [], filing.source_name
    assert '<div class="er-brief-title" lang="ko">' in regional_brief._row_html(_dart())


def test_metadata_only_summary_is_tagged_run_by_run():
    from src.logic import filing_display

    filing = _dart()
    summary = filing_display.metadata_only_summary(filing, filing.report_nm, "Aug 14, 2026")
    assert _untagged(cjk_html(summary, filing.original_language)) == []
    assert "filed" in cjk_html(summary, filing.original_language).split("</span>")[1]
