"""Daily News public page — fixture-driven render tests via AppTest,
with daily_news.get_settings monkeypatched to a tmp cache_dir seeded
with fixture data. Zero network calls; the real data/cache/ (gitignored
live pilot cache) is never touched. Proves the public card renders
exactly the five approved fields and none of the internal/Radar detail
that daily_news_admin.py alone shows."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access.daily_news import daily_news_store
from src.models.daily_news_models import (
    NewsSourceReference,
    NewsStateTransition,
    NewsStory,
    NewsStoryStatus,
    SourceClass,
)

_HARNESS = Path(__file__).parent / "apptest_pages" / "daily_news_page.py"


def _settings(cache_dir) -> Settings:
    return Settings(cache_dir=cache_dir)


def _story(**overrides) -> NewsStory:
    defaults = dict(
        id="newsitem-nvidia-abc123", company_name="NVIDIA", ticker="NVDA", theme_slug="ai-buildout",
        headline="NVIDIA Announces Financial Results", eeva_summary="NVIDIA reported strong quarterly results.",
        is_fallback_summary=False, translation_unavailable=False, original_title=None,
        sources=(
            NewsSourceReference(
                publisher="NVIDIA", source_class=SourceClass.OFFICIAL_COMPANY,
                url="https://nvidianews.nvidia.com/news/results", title="NVIDIA Announces Financial Results",
                published_at="2026-08-24T12:00:00+00:00", retrieved_at="2026-08-24T12:05:00+00:00",
                original_language="English", excerpt_original="NVIDIA reported strong quarterly results.",
            ),
        ),
        status=NewsStoryStatus.PUBLISHED,
        state_history=[NewsStateTransition(status=NewsStoryStatus.PUBLISHED, at="2026-08-24T12:05:00+00:00")],
    )
    defaults.update(overrides)
    return NewsStory(**defaults)


def test_empty_state_when_no_stories(tmp_path):
    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "No Daily News stories yet" in all_text


def test_public_card_shows_exactly_the_five_approved_fields(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story()])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "NVIDIA" in markdown_text  # company name
    assert "NVIDIA Announces Financial Results" in markdown_text  # headline
    assert "Read original source" in markdown_text
    assert "https://nvidianews.nvidia.com/news/results" in markdown_text


def test_public_card_never_shows_source_class_or_radar_terminology(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story()])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    # Excludes with_chrome's own globally-injected <style> block — its CSS
    # comments describe shared, Radar-unrelated tokens (e.g. a generic
    # "er-status-tag" comment mentioning "Candidate signal" as an
    # unrelated example) and are not part of what daily_news.py itself
    # renders, so checking against them would be a false positive.
    content_only = [m.value for m in at.markdown if not m.value.strip().startswith("<style")]
    all_text = " ".join(content_only) + " ".join(str(c.value) for c in at.caption)
    for forbidden in (
        "Official company source", "RETRIEVAL_FAILED", "EXTRACTED", "PENDING",
        "confidence", "matched_rules", "Review processing",
    ):
        assert forbidden not in all_text


def test_fallback_summary_story_renders_the_exact_fallback_sentence(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story(
        id="newsitem-amd-xyz", company_name="Advanced Micro Devices", ticker="AMD",
        eeva_summary="The company published this update through its official Investor Relations channel.",
        is_fallback_summary=True,
    )])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    all_text = " ".join(m.value for m in at.markdown) + " ".join(str(w.value) for w in at.text)
    assert "The company published this update through its official Investor Relations channel." in " ".join(m.value for m in at.markdown)


def test_translation_unavailable_story_shows_original_title_and_label(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story(
        id="newsitem-korean-1", company_name="NVIDIA", eeva_summary=None,
        translation_unavailable=True, original_title="삼성전자 신규시설투자 결정",
    )])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    markdown_text = " ".join(m.value for m in at.markdown)
    caption_text = " ".join(str(c.value) for c in at.caption)
    assert "삼성전자 신규시설투자 결정" in markdown_text
    assert "Translation unavailable" in caption_text
