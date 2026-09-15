"""Gated Japan/Korea source expansion (design/DECISIONS.md) —
EDGE_DAILY_NEWS_ENABLED_SOURCES_JP_KR settings-flag tests. Same
comma-separated-allow-list shape as EDGE_DAILY_NEWS_ENABLED_SOURCES
(market-news), reused here for a SEPARATE allow-list — deliberately a
different env var/field so the two gated expansions are independently
controllable."""
from __future__ import annotations

from src.config.settings import Settings


def test_defaults_to_empty_frozenset_when_env_is_unset(monkeypatch):
    monkeypatch.delenv("EDGE_DAILY_NEWS_ENABLED_SOURCES_JP_KR", raising=False)

    assert Settings().daily_news_enabled_jp_kr_sources == frozenset()


def test_defaults_to_empty_frozenset_when_env_is_blank(monkeypatch):
    monkeypatch.setenv("EDGE_DAILY_NEWS_ENABLED_SOURCES_JP_KR", "")

    assert Settings().daily_news_enabled_jp_kr_sources == frozenset()


def test_parses_a_single_source_id(monkeypatch):
    monkeypatch.setenv("EDGE_DAILY_NEWS_ENABLED_SOURCES_JP_KR", "businesskorea-industries-rss")

    assert Settings().daily_news_enabled_jp_kr_sources == frozenset({"businesskorea-industries-rss"})


def test_parses_multiple_comma_separated_source_ids(monkeypatch):
    monkeypatch.setenv(
        "EDGE_DAILY_NEWS_ENABLED_SOURCES_JP_KR", "businesskorea-industries-rss,businesskorea-science-tech-rss",
    )

    assert Settings().daily_news_enabled_jp_kr_sources == frozenset({
        "businesskorea-industries-rss", "businesskorea-science-tech-rss",
    })


def test_strips_whitespace_and_lowercases(monkeypatch):
    monkeypatch.setenv("EDGE_DAILY_NEWS_ENABLED_SOURCES_JP_KR", " BUSINESSKOREA-INDUSTRIES-RSS ")

    assert Settings().daily_news_enabled_jp_kr_sources == frozenset({"businesskorea-industries-rss"})


def test_is_a_distinct_field_from_the_market_news_allow_list(monkeypatch):
    """Setting one gated-source env var must never populate the other —
    the two expansions are independently controllable, per design."""
    monkeypatch.setenv("EDGE_DAILY_NEWS_ENABLED_SOURCES_JP_KR", "businesskorea-industries-rss")
    monkeypatch.delenv("EDGE_DAILY_NEWS_ENABLED_SOURCES", raising=False)

    settings = Settings()

    assert settings.daily_news_enabled_jp_kr_sources == frozenset({"businesskorea-industries-rss"})
    assert settings.daily_news_enabled_market_news_sources == frozenset()


def test_market_news_flag_does_not_populate_the_jp_kr_flag(monkeypatch):
    monkeypatch.setenv("EDGE_DAILY_NEWS_ENABLED_SOURCES", "light-reading-rss")
    monkeypatch.delenv("EDGE_DAILY_NEWS_ENABLED_SOURCES_JP_KR", raising=False)

    settings = Settings()

    assert settings.daily_news_enabled_market_news_sources == frozenset({"light-reading-rss"})
    assert settings.daily_news_enabled_jp_kr_sources == frozenset()


def test_both_gated_flags_can_be_set_independently_and_simultaneously(monkeypatch):
    monkeypatch.setenv("EDGE_DAILY_NEWS_ENABLED_SOURCES", "light-reading-rss")
    monkeypatch.setenv("EDGE_DAILY_NEWS_ENABLED_SOURCES_JP_KR", "businesskorea-industries-rss")

    settings = Settings()

    assert settings.daily_news_enabled_market_news_sources == frozenset({"light-reading-rss"})
    assert settings.daily_news_enabled_jp_kr_sources == frozenset({"businesskorea-industries-rss"})
