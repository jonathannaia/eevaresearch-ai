"""Gated market-news source expansion (design/DECISIONS.md) —
EDGE_DAILY_NEWS_ENABLED_SOURCES settings-flag tests. Same comma-
separated-allow-list shape as _parse_beta_allowed_emails (case-
normalized, stripped, blank entries dropped), reused here for a
different allow-list: gated Daily News editorial source_ids."""
from __future__ import annotations

from src.config.settings import Settings


def test_defaults_to_empty_frozenset_when_env_is_unset(monkeypatch):
    monkeypatch.delenv("EDGE_DAILY_NEWS_ENABLED_SOURCES", raising=False)

    assert Settings().daily_news_enabled_market_news_sources == frozenset()


def test_defaults_to_empty_frozenset_when_env_is_blank(monkeypatch):
    monkeypatch.setenv("EDGE_DAILY_NEWS_ENABLED_SOURCES", "")

    assert Settings().daily_news_enabled_market_news_sources == frozenset()


def test_defaults_to_empty_frozenset_when_env_is_whitespace_only(monkeypatch):
    monkeypatch.setenv("EDGE_DAILY_NEWS_ENABLED_SOURCES", "   ")

    assert Settings().daily_news_enabled_market_news_sources == frozenset()


def test_parses_a_single_source_id(monkeypatch):
    monkeypatch.setenv("EDGE_DAILY_NEWS_ENABLED_SOURCES", "light-reading-rss")

    assert Settings().daily_news_enabled_market_news_sources == frozenset({"light-reading-rss"})


def test_parses_multiple_comma_separated_source_ids(monkeypatch):
    monkeypatch.setenv("EDGE_DAILY_NEWS_ENABLED_SOURCES", "light-reading-rss,some-future-source")

    assert Settings().daily_news_enabled_market_news_sources == frozenset({"light-reading-rss", "some-future-source"})


def test_strips_whitespace_around_each_entry(monkeypatch):
    monkeypatch.setenv("EDGE_DAILY_NEWS_ENABLED_SOURCES", " light-reading-rss , some-future-source ")

    assert Settings().daily_news_enabled_market_news_sources == frozenset({"light-reading-rss", "some-future-source"})


def test_drops_empty_entries_from_stray_commas(monkeypatch):
    monkeypatch.setenv("EDGE_DAILY_NEWS_ENABLED_SOURCES", "light-reading-rss,,")

    assert Settings().daily_news_enabled_market_news_sources == frozenset({"light-reading-rss"})


def test_lowercases_entries(monkeypatch):
    monkeypatch.setenv("EDGE_DAILY_NEWS_ENABLED_SOURCES", "LIGHT-READING-RSS")

    assert Settings().daily_news_enabled_market_news_sources == frozenset({"light-reading-rss"})


def test_independent_of_every_other_daily_news_flag(monkeypatch):
    monkeypatch.setenv("EDGE_DAILY_NEWS_ENABLED_SOURCES", "light-reading-rss")
    monkeypatch.delenv("EDGE_DAILY_NEWS_LIVE_SCAN_ENABLED", raising=False)
    monkeypatch.delenv("EDGE_DAILY_NEWS_ADMIN_ENABLED", raising=False)

    settings = Settings()

    assert settings.daily_news_enabled_market_news_sources == frozenset({"light-reading-rss"})
    assert settings.daily_news_live_scan_enabled is False
    assert settings.daily_news_admin_enabled is False
