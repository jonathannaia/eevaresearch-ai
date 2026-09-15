"""Gated Japan/Korea source expansion (design/DECISIONS.md) —
jp_kr_sources.enabled_gated_sources() and the GATED_JP_KR_SOURCE_
REGISTRY entries it filters."""
from __future__ import annotations

from src.config.settings import Settings
from src.data_access.daily_news.jp_kr_sources import enabled_gated_sources
from src.data_access.daily_news.source_registry import (
    GATED_JP_KR_SOURCE_REGISTRY,
    SourceCategory,
    SourceFormat,
    SourceHealthState,
    find_registry_violations,
    validate_source_entry,
)


def test_gated_jp_kr_registry_has_zero_validation_violations():
    assert find_registry_violations(GATED_JP_KR_SOURCE_REGISTRY) == ()
    for entry in GATED_JP_KR_SOURCE_REGISTRY:
        assert validate_source_entry(entry) == ()


def test_gated_jp_kr_registry_has_exactly_three_entries():
    assert [e.source_id for e in GATED_JP_KR_SOURCE_REGISTRY] == [
        "japan-times-business-rss", "businesskorea-industries-rss", "businesskorea-science-tech-rss",
    ]


def test_japan_times_business_entry_is_pending_review_not_verified():
    """This entry is browser-confirmed live but got HTTPError:403 under
    the real worker's own fetch signature in this session's own dry
    run — must never be marked VERIFIED until re-confirmed working
    under that real signature. See its own notes for the full finding."""
    entry = next(e for e in GATED_JP_KR_SOURCE_REGISTRY if e.source_id == "japan-times-business-rss")
    assert entry.health_state == SourceHealthState.PENDING_REVIEW
    assert entry.canonical_url == "https://www.japantimes.co.jp/business/feed/"
    assert entry.jurisdiction == "Japan"


def test_businesskorea_entries_have_the_expected_fields():
    industries = next(e for e in GATED_JP_KR_SOURCE_REGISTRY if e.source_id == "businesskorea-industries-rss")
    sci_tech = next(e for e in GATED_JP_KR_SOURCE_REGISTRY if e.source_id == "businesskorea-science-tech-rss")
    for entry in (industries, sci_tech):
        assert entry.category == SourceCategory.INDEPENDENT_NEWS
        assert entry.format == SourceFormat.RSS_ATOM
        assert entry.jurisdiction == "South Korea"
        assert entry.issuer_agnostic is True
        assert entry.allowlisted is True  # required for INDEPENDENT_NEWS
        assert entry.health_state == SourceHealthState.VERIFIED
        assert entry.attribution_label == "Business Korea"
        assert entry.domains == ("www.businesskorea.co.kr",)
    assert industries.canonical_url == "https://www.businesskorea.co.kr/rss/gns_S1N17.xml"
    assert sci_tech.canonical_url == "https://www.businesskorea.co.kr/rss/gns_S1N27.xml"


def test_gated_jp_kr_registry_never_merged_into_editorial_source_registry():
    from src.data_access.daily_news.source_registry import EDITORIAL_SOURCE_REGISTRY

    gated_ids = {e.source_id for e in GATED_JP_KR_SOURCE_REGISTRY}
    editorial_ids = {e.source_id for e in EDITORIAL_SOURCE_REGISTRY}
    assert not (gated_ids & editorial_ids)


def test_gated_jp_kr_registry_never_merged_into_gated_market_news_registry():
    from src.data_access.daily_news.source_registry import GATED_MARKET_NEWS_SOURCE_REGISTRY

    jp_kr_ids = {e.source_id for e in GATED_JP_KR_SOURCE_REGISTRY}
    market_news_ids = {e.source_id for e in GATED_MARKET_NEWS_SOURCE_REGISTRY}
    assert not (jp_kr_ids & market_news_ids)


def test_enabled_gated_sources_empty_allow_list_returns_nothing():
    settings = Settings(daily_news_enabled_jp_kr_sources=frozenset())

    assert enabled_gated_sources(settings) == ()


def test_enabled_gated_sources_returns_only_the_named_entries():
    settings = Settings(
        daily_news_enabled_jp_kr_sources=frozenset({"businesskorea-industries-rss", "businesskorea-science-tech-rss"}),
    )

    result = enabled_gated_sources(settings)

    assert {e.source_id for e in result} == {"businesskorea-industries-rss", "businesskorea-science-tech-rss"}


def test_enabled_gated_sources_ignores_an_unknown_source_id():
    settings = Settings(daily_news_enabled_jp_kr_sources=frozenset({"not-a-real-jp-kr-source"}))

    assert enabled_gated_sources(settings) == ()


def test_enabled_gated_sources_is_independent_of_the_market_news_allow_list():
    """The two gated expansions use separate flags — enabling one must
    never enable the other."""
    settings = Settings(
        daily_news_enabled_market_news_sources=frozenset({"light-reading-rss"}),
        daily_news_enabled_jp_kr_sources=frozenset(),
    )

    assert enabled_gated_sources(settings) == ()
