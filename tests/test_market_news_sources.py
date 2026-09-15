"""Gated market-news source expansion (design/DECISIONS.md) —
market_news_sources.enabled_gated_sources() and the
GATED_MARKET_NEWS_SOURCE_REGISTRY entry it filters."""
from __future__ import annotations

from src.config.settings import Settings
from src.data_access.daily_news.market_news_sources import enabled_gated_sources
from src.data_access.daily_news.source_registry import (
    GATED_MARKET_NEWS_SOURCE_REGISTRY,
    SourceCategory,
    SourceFormat,
    SourceHealthState,
    find_registry_violations,
    validate_source_entry,
)


def test_gated_registry_has_zero_validation_violations():
    assert find_registry_violations(GATED_MARKET_NEWS_SOURCE_REGISTRY) == ()
    for entry in GATED_MARKET_NEWS_SOURCE_REGISTRY:
        assert validate_source_entry(entry) == ()


def test_light_reading_entry_has_the_expected_fields():
    entry = next(e for e in GATED_MARKET_NEWS_SOURCE_REGISTRY if e.source_id == "light-reading-rss")
    assert entry.canonical_url == "https://www.lightreading.com/rss.xml"
    assert entry.domains == ("www.lightreading.com",)
    assert entry.format == SourceFormat.RSS_ATOM
    assert entry.category == SourceCategory.INDEPENDENT_NEWS
    assert entry.jurisdiction == "United States"
    assert entry.issuer_agnostic is True
    assert entry.allowlisted is True  # required for INDEPENDENT_NEWS — see validate_source_entry
    assert entry.health_state == SourceHealthState.VERIFIED
    assert entry.attribution_label == "Light Reading"
    assert entry.licensing_classification


def test_gated_registry_never_merged_into_editorial_source_registry():
    from src.data_access.daily_news.source_registry import EDITORIAL_SOURCE_REGISTRY

    gated_ids = {e.source_id for e in GATED_MARKET_NEWS_SOURCE_REGISTRY}
    editorial_ids = {e.source_id for e in EDITORIAL_SOURCE_REGISTRY}
    assert not (gated_ids & editorial_ids)


def test_enabled_gated_sources_empty_allow_list_returns_nothing():
    settings = Settings(daily_news_enabled_market_news_sources=frozenset())

    assert enabled_gated_sources(settings) == ()


def test_enabled_gated_sources_returns_the_named_entry():
    settings = Settings(daily_news_enabled_market_news_sources=frozenset({"light-reading-rss"}))

    result = enabled_gated_sources(settings)

    assert len(result) == 1
    assert result[0].source_id == "light-reading-rss"


def test_enabled_gated_sources_ignores_an_unknown_source_id():
    settings = Settings(daily_news_enabled_market_news_sources=frozenset({"not-a-real-gated-source"}))

    assert enabled_gated_sources(settings) == ()


def test_enabled_gated_sources_partial_match_only_returns_the_matching_entries():
    settings = Settings(
        daily_news_enabled_market_news_sources=frozenset({"light-reading-rss", "not-a-real-gated-source"}),
    )

    result = enabled_gated_sources(settings)

    assert [e.source_id for e in result] == ["light-reading-rss"]
