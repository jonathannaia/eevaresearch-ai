"""Research Theses — live evidence wiring (design/DECISIONS.md). Pure-
function tests for src.logic.theme_evidence. No I/O, no Streamlit, no
network — every fixture is directly constructed."""
from __future__ import annotations

from src.config.tracked_companies import TrackedCompany
from src.logic import theme_evidence
from src.models.daily_news_models import EditorialStory, NewsSourceReference, NewsStory, NewsStoryStatus, SourceClass
from src.models.models import FilingEvent


def _now_iso() -> str:
    return "2026-09-15T00:00:00+00:00"


def _filing(corp_name: str, rcept_dt: str, rcept_no: str = "X") -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="00126380", corp_name=corp_name, stock_code="005930",
        report_nm="신규시설투자등 결정", rcept_dt=rcept_dt, flr_nm=corp_name,
        source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + rcept_no, retrieved_at=_now_iso(),
    )


def _news_story(company_name: str, status=NewsStoryStatus.PUBLISHED, published_at="2026-09-10T00:00:00+00:00") -> NewsStory:
    source = NewsSourceReference(
        publisher="Company IR", source_class=SourceClass.OFFICIAL_COMPANY, url="https://example.com/press",
        title="Press release", published_at=published_at, retrieved_at=_now_iso(), original_language="English",
    )
    return NewsStory(
        id="story-1", company_name=company_name, ticker="005930", theme_slug="memory",
        headline="Company announces new facility", eeva_summary="Summary.", is_fallback_summary=False,
        translation_unavailable=False, original_title=None, sources=(source,), status=status,
    )


def _editorial_story(matched_companies: tuple[str, ...], published_at="2026-09-11T00:00:00+00:00") -> EditorialStory:
    return EditorialStory(
        id="edit-1", headline="Sector-wide capex surge", publisher="Korea Herald",
        source_url="https://www.koreaherald.com/article/1", published_at=published_at, retrieved_at=_now_iso(),
        excerpt="An excerpt.", matched_companies=matched_companies, matched_themes=("memory",),
        source_feed_id="korea-herald-business-rss",
    )


def _tracked_company(name: str, themes: tuple[str, ...]) -> TrackedCompany:
    return TrackedCompany(name=name, exchange="KRX", krx_code="005930", source="OpenDART / DART", themes=themes)


# ============================================================
# theme_tags_for_companies
# ============================================================


def test_theme_tags_returns_the_union_of_mapped_companies_own_tracked_themes():
    tracked = (_tracked_company("Samsung Electronics", ("memory", "ai-buildout")), _tracked_company("SK Hynix", ("memory",)))
    tags = theme_evidence.theme_tags_for_companies(frozenset({"Samsung Electronics", "SK Hynix"}), tracked)
    assert tags == ("memory", "ai-buildout")


def test_theme_tags_ignores_a_mapped_company_that_is_not_a_tracked_company():
    tracked = (_tracked_company("Samsung Electronics", ("memory",)),)
    tags = theme_evidence.theme_tags_for_companies(frozenset({"Some Untracked Startup"}), tracked)
    assert tags == ()


def test_theme_tags_returns_empty_for_an_empty_company_set():
    tracked = (_tracked_company("Samsung Electronics", ("memory",)),)
    assert theme_evidence.theme_tags_for_companies(frozenset(), tracked) == ()


def test_theme_tags_never_duplicates_a_theme_shared_by_two_mapped_companies():
    tracked = (_tracked_company("Samsung Electronics", ("memory",)), _tracked_company("SK Hynix", ("memory",)))
    tags = theme_evidence.theme_tags_for_companies(frozenset({"Samsung Electronics", "SK Hynix"}), tracked)
    assert tags == ("memory",)


# ============================================================
# recent_filing_evidence
# ============================================================


def test_recent_filing_evidence_matches_by_exact_corp_name():
    filings = (_filing("Samsung Electronics", "20260901"), _filing("Unrelated Corp", "20260902"))
    result = theme_evidence.recent_filing_evidence(filings, frozenset({"Samsung Electronics"}), limit=5)
    assert len(result) == 1
    assert result[0].company == "Samsung Electronics"
    assert result[0].kind == "Radar filing"


def test_recent_filing_evidence_sorts_newest_first_and_respects_the_limit():
    filings = (
        _filing("Samsung Electronics", "20260801", rcept_no="A"),
        _filing("Samsung Electronics", "20260901", rcept_no="B"),
        _filing("Samsung Electronics", "20260701", rcept_no="C"),
    )
    result = theme_evidence.recent_filing_evidence(filings, frozenset({"Samsung Electronics"}), limit=2)
    assert [link.date for link in result] == ["20260901", "20260801"]


def test_recent_filing_evidence_empty_when_no_company_matches():
    filings = (_filing("Unrelated Corp", "20260901"),)
    assert theme_evidence.recent_filing_evidence(filings, frozenset({"Samsung Electronics"}), limit=5) == ()


# ============================================================
# recent_daily_news_evidence
# ============================================================


def test_recent_daily_news_evidence_includes_a_published_matching_news_story():
    result = theme_evidence.recent_daily_news_evidence((_news_story("Samsung Electronics"),), (), frozenset({"Samsung Electronics"}), limit=5)
    assert len(result) == 1
    assert result[0].kind == "Daily News"
    assert result[0].company == "Samsung Electronics"


def test_recent_daily_news_evidence_excludes_a_non_published_news_story():
    story = _news_story("Samsung Electronics", status=NewsStoryStatus.DISCOVERED)
    assert theme_evidence.recent_daily_news_evidence((story,), (), frozenset({"Samsung Electronics"}), limit=5) == ()


def test_recent_daily_news_evidence_includes_an_editorial_story_with_overlapping_matched_companies():
    story = _editorial_story(("Samsung Electronics", "SK Hynix"))
    result = theme_evidence.recent_daily_news_evidence((), (story,), frozenset({"SK Hynix"}), limit=5)
    assert len(result) == 1
    assert result[0].title == "Sector-wide capex surge"


def test_recent_daily_news_evidence_excludes_an_editorial_story_with_no_overlap():
    story = _editorial_story(("Unrelated Corp",))
    assert theme_evidence.recent_daily_news_evidence((), (story,), frozenset({"Samsung Electronics"}), limit=5) == ()


def test_recent_daily_news_evidence_merges_and_sorts_both_kinds_newest_first():
    news = _news_story("Samsung Electronics", published_at="2026-09-01T00:00:00+00:00")
    editorial = _editorial_story(("Samsung Electronics",), published_at="2026-09-12T00:00:00+00:00")
    result = theme_evidence.recent_daily_news_evidence((news,), (editorial,), frozenset({"Samsung Electronics"}), limit=5)
    assert [link.title for link in result] == ["Sector-wide capex surge", "Company announces new facility"]


def test_recent_daily_news_evidence_respects_the_limit_across_both_kinds():
    news = _news_story("Samsung Electronics")
    editorial = _editorial_story(("Samsung Electronics",))
    result = theme_evidence.recent_daily_news_evidence((news,), (editorial,), frozenset({"Samsung Electronics"}), limit=1)
    assert len(result) == 1
