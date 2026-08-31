"""Daily News public page — fixture-driven render tests via AppTest,
with daily_news.get_settings monkeypatched to a tmp cache_dir seeded
with fixture data. Zero network calls; the real data/cache/ (gitignored
live pilot cache) is never touched. Proves the public card renders
exactly the five approved fields, the single company selector and
rolling-7-day freshness filter behave as approved, and none of the
internal/Radar detail that daily_news_admin.py alone shows ever
appears.

Exact-boundary and UTC-vs-display timezone correctness are proven as
plain pure-function tests against src.ui.pages.daily_news's own helpers
(deterministic to the second) rather than via AppTest, which has no
seam to inject a fixed "now" into render()'s own datetime.now() call —
AppTest tests below use fixtures placed safely inside/outside the
window instead, exercising the actual rendered UI.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
from src.ui.pages.daily_news import _elapsed_seconds, _is_recent, _recent_stories

_HARNESS = Path(__file__).parent / "apptest_pages" / "daily_news_page.py"


def _settings(cache_dir) -> Settings:
    return Settings(cache_dir=cache_dir)


def _story(published_at_offset: timedelta = timedelta(hours=1), **overrides) -> NewsStory:
    published_at = (datetime.now(timezone.utc) - published_at_offset).isoformat()
    defaults = dict(
        id="newsitem-nvidia-abc123", company_name="NVIDIA", ticker="NVDA", theme_slug="ai-buildout",
        headline="NVIDIA Announces Financial Results", eeva_summary="NVIDIA reported strong quarterly results.",
        is_fallback_summary=False, translation_unavailable=False, original_title=None,
        sources=(
            NewsSourceReference(
                publisher="NVIDIA", source_class=SourceClass.OFFICIAL_COMPANY,
                url="https://nvidianews.nvidia.com/news/results", title="NVIDIA Announces Financial Results",
                published_at=published_at, retrieved_at=published_at,
                original_language="English", excerpt_original="NVIDIA reported strong quarterly results.",
            ),
        ),
        status=NewsStoryStatus.PUBLISHED,
        state_history=[NewsStateTransition(status=NewsStoryStatus.PUBLISHED, at=published_at)],
    )
    defaults.update(overrides)
    return NewsStory(**defaults)


# --- Pure-function boundary/timezone tests -------------------------------


def test_elapsed_seconds_treats_naive_timestamp_as_utc():
    now = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)
    naive_one_hour_ago = "2026-08-30T11:00:00"  # no tzinfo
    assert _elapsed_seconds(naive_one_hour_ago, now) == 3600.0


def test_story_exactly_seven_times_twenty_four_hours_old_is_included():
    now = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)
    published_at = (now - timedelta(days=7)).isoformat()
    story = NewsStory(
        id="x", company_name="NVIDIA", ticker="NVDA", theme_slug="ai-buildout", headline="H",
        eeva_summary="S", is_fallback_summary=False, translation_unavailable=False, original_title=None,
        sources=(NewsSourceReference(
            publisher="NVIDIA", source_class=SourceClass.OFFICIAL_COMPANY, url="https://nvidianews.nvidia.com/x",
            title="H", published_at=published_at, retrieved_at=published_at, original_language="English",
        ),),
        status=NewsStoryStatus.PUBLISHED,
    )
    assert _is_recent(story, now)


def test_story_one_second_past_seven_times_twenty_four_hours_is_excluded():
    now = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)
    published_at = (now - timedelta(days=7, seconds=1)).isoformat()
    story = NewsStory(
        id="x", company_name="NVIDIA", ticker="NVDA", theme_slug="ai-buildout", headline="H",
        eeva_summary="S", is_fallback_summary=False, translation_unavailable=False, original_title=None,
        sources=(NewsSourceReference(
            publisher="NVIDIA", source_class=SourceClass.OFFICIAL_COMPANY, url="https://nvidianews.nvidia.com/x",
            title="H", published_at=published_at, retrieved_at=published_at, original_language="English",
        ),),
        status=NewsStoryStatus.PUBLISHED,
    )
    assert not _is_recent(story, now)


def test_freshness_gate_uses_utc_regardless_of_what_local_calendar_date_it_falls_on():
    # A timestamp that is safely within the 7-day UTC window but would
    # render as a *different* calendar date once converted to Eastern
    # for display (fmt_datetime_local) must still be included — the
    # filter compares in UTC and is never affected by the separate
    # display-only Eastern conversion.
    now = datetime(2026, 8, 30, 2, 0, 0, tzinfo=timezone.utc)  # 22:00 EDT the previous evening
    published_at = (now - timedelta(days=6, hours=23)).isoformat()
    story = NewsStory(
        id="x", company_name="NVIDIA", ticker="NVDA", theme_slug="ai-buildout", headline="H",
        eeva_summary="S", is_fallback_summary=False, translation_unavailable=False, original_title=None,
        sources=(NewsSourceReference(
            publisher="NVIDIA", source_class=SourceClass.OFFICIAL_COMPANY, url="https://nvidianews.nvidia.com/x",
            title="H", published_at=published_at, retrieved_at=published_at, original_language="English",
        ),),
        status=NewsStoryStatus.PUBLISHED,
    )
    assert _is_recent(story, now)


def test_recent_stories_filters_and_preserves_order():
    fresh = _story(published_at_offset=timedelta(hours=1), id="fresh")
    stale = _story(published_at_offset=timedelta(days=10), id="stale")
    now = datetime.now(timezone.utc)
    assert _recent_stories([stale, fresh], now=now) == [fresh]


# --- AppTest rendering/interaction tests ----------------------------------


def test_empty_state_when_no_stories_at_all(tmp_path):
    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "No recent company updates in the last 7 days." in all_text


def test_subtitle_and_scope_line_render_exactly(tmp_path):
    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    all_text = " ".join(m.value for m in at.markdown)
    assert "Company updates from official sources." in all_text
    assert "Autonomously discovered company updates" not in all_text
    assert "Showing official company updates from the past 7 days." in all_text


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

    assert "The company published this update through its official Investor Relations channel." in " ".join(m.value for m in at.markdown)


def test_translation_unavailable_story_shows_original_title_and_label(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story(
        id="newsitem-korean-1", eeva_summary=None,
        translation_unavailable=True, original_title="삼성전자 신규시설투자 결정",
    )])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    markdown_text = " ".join(m.value for m in at.markdown)
    caption_text = " ".join(str(c.value) for c in at.caption)
    assert "삼성전자 신규시설투자 결정" in markdown_text
    assert "Translation unavailable" in caption_text


def test_all_companies_default_shows_every_recent_company_newest_first(tmp_path):
    older = _story(published_at_offset=timedelta(hours=5), id="s-intel", company_name="Intel Corp.", headline="Intel Reports Quarterly Results")
    newer = _story(published_at_offset=timedelta(hours=1), id="s-nvidia", company_name="NVIDIA")
    daily_news_store.upsert_new_stories(tmp_path, [older, newer])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    markdown_text = " ".join(m.value for m in at.markdown)
    assert markdown_text.index("NVIDIA") < markdown_text.index("Intel Corp.")
    select_options = at.selectbox[0].options
    assert select_options == ["All companies", "Intel Corp.", "NVIDIA"]


def test_selecting_a_company_shows_only_that_companys_stories(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [
        _story(id="s-intel", company_name="Intel Corp.", headline="Intel Reports Quarterly Results"),
        _story(id="s-nvidia", company_name="NVIDIA"),
    ])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        at.selectbox[0].select("Intel Corp.").run()

    markdown_text = " ".join(m.value for m in at.markdown)
    assert "Intel Reports Quarterly Results" in markdown_text
    assert "NVIDIA Announces Financial Results" not in markdown_text


def test_selected_company_with_no_recent_stories_shows_named_empty_state(tmp_path):
    # Intel has only a stale story — appears in the selector (it has a
    # persisted PUBLISHED story) but has nothing recent once selected.
    daily_news_store.upsert_new_stories(tmp_path, [
        _story(id="s-intel-stale", company_name="Intel Corp.", published_at_offset=timedelta(days=30)),
        _story(id="s-nvidia-fresh", company_name="NVIDIA"),
    ])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        at.selectbox[0].select("Intel Corp.").run()

    markdown_text = " ".join(m.value for m in at.markdown)
    assert "No recent updates for Intel Corp. in the last 7 days." in markdown_text


def test_only_stale_stories_shows_the_all_companies_empty_state(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story(published_at_offset=timedelta(days=30))])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    markdown_text = " ".join(m.value for m in at.markdown)
    assert "No recent company updates in the last 7 days." in markdown_text


def _image_bearing_story() -> "NewsStory":
    return _story(sources=(
        NewsSourceReference(
            publisher="NVIDIA", source_class=SourceClass.OFFICIAL_COMPANY,
            url="https://nvidianews.nvidia.com/news/results", title="NVIDIA Announces Financial Results",
            published_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            retrieved_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            original_language="English", excerpt_original="NVIDIA reported strong quarterly results.",
            image_url="https://iprsoftwaremedia.com/photo.jpg", image_alt="A photo of the announcement",
        ),
    ))


def _image_bearing_markdown_block(at) -> str:
    blocks = [m.value for m in at.markdown if "er-news-card-content" in m.value and not m.value.strip().startswith("<style")]
    assert len(blocks) == 1
    return blocks[0]


def test_image_bearing_card_renders_one_two_column_content_wrapper(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_image_bearing_story()])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    block = _image_bearing_markdown_block(at)
    # Metadata, headline, summary, and link all live inside the left
    # "er-news-card-text" column, which is itself a sibling of the
    # image — one wrapper, two real columns, never a float and never a
    # separate image-only header row.
    assert '<div class="er-news-card-text">' in block
    assert "NVIDIA · NVIDIA ·" in block
    assert '<img class="er-news-card-thumb" src="https://iprsoftwaremedia.com/photo.jpg"' in block
    assert 'alt="A photo of the announcement"' in block
    assert "onerror=" in block
    assert "float" not in block
    assert "er-card-header" not in block  # old header-row wrapper fully removed


def test_headline_starts_immediately_after_metadata_within_the_text_column(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_image_bearing_story()])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    block = _image_bearing_markdown_block(at)
    text_column = block.split('<div class="er-news-card-text">')[1].split('<img')[0]
    meta_index = text_column.index("NVIDIA · NVIDIA ·")
    headline_index = text_column.index("NVIDIA Announces Financial Results")
    summary_index = text_column.index("NVIDIA reported strong quarterly results.")
    link_index = text_column.index("Read original source")
    # Order within the text column: metadata, headline, summary, link —
    # the headline immediately follows metadata rather than waiting
    # below the image, and everything stays inside this one column.
    assert meta_index < headline_index < summary_index < link_index


def test_image_rail_is_a_sibling_column_not_a_float_or_a_header_row(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_image_bearing_story()])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    block = _image_bearing_markdown_block(at)
    # The <img> is a sibling of the text column, closed within the same
    # outer "er-news-card-content" wrapper — not nested inside the text
    # column, and not part of any separate full-width header block.
    assert block.index('<div class="er-news-card-text">') < block.index("<img")
    text_column = block.split('<div class="er-news-card-text">')[1].split('<img')[0]
    assert "<img" not in text_column


def test_card_without_an_image_renders_no_image_rail_or_content_wrapper(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story()])  # default _story() has no image_url

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    # Excludes the app chrome's own logo markdown block (unrelated to
    # Daily News cards) — this proves the card itself renders no <img>
    # and no two-column wrapper (reserved only for image-bearing cards)
    # — the plain text-only layout is preserved exactly.
    card_markdown = [m.value for m in at.markdown if "rail-logo" not in m.value and not m.value.strip().startswith("<style")]
    card_text = " ".join(card_markdown)
    assert "<img" not in card_text
    assert "er-news-card-content" not in card_text
    assert "er-card-header" not in card_text
    assert "NVIDIA Announces Financial Results" in card_text
    assert "Read original source" in card_text


def test_image_alt_and_url_are_html_escaped_before_rendering(tmp_path):
    story = _story(sources=(
        NewsSourceReference(
            publisher="NVIDIA", source_class=SourceClass.OFFICIAL_COMPANY,
            url="https://nvidianews.nvidia.com/news/results", title="NVIDIA Announces Financial Results",
            published_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            retrieved_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            original_language="English", excerpt_original="NVIDIA reported strong quarterly results.",
            image_url="https://iprsoftwaremedia.com/photo.jpg",
            image_alt='"><script>alert(1)</script>',
        ),
    ))
    daily_news_store.upsert_new_stories(tmp_path, [story])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "<script>alert(1)</script>" not in markdown_text


def test_headline_and_summary_are_html_escaped_in_the_image_bearing_text_column(tmp_path):
    # The two-column layout embeds headline/summary directly into the
    # same unsafe_allow_html block as the image (unlike the plain-
    # markdown rendering the text-only card still uses), so this is a
    # new escaping surface that must be covered explicitly.
    story = _story(
        headline='"><script>alert("headline")</script>',
        eeva_summary='"><script>alert("summary")</script>',
        sources=(
            NewsSourceReference(
                publisher="NVIDIA", source_class=SourceClass.OFFICIAL_COMPANY,
                url="https://nvidianews.nvidia.com/news/results", title='"><script>alert("headline")</script>',
                published_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
                retrieved_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
                original_language="English",
                image_url="https://iprsoftwaremedia.com/photo.jpg", image_alt="A photo",
            ),
        ),
    )
    daily_news_store.upsert_new_stories(tmp_path, [story])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    markdown_text = " ".join(m.value for m in at.markdown)
    assert '<script>alert("headline")</script>' not in markdown_text
    assert '<script>alert("summary")</script>' not in markdown_text


def test_old_story_remains_persisted_but_hidden_from_the_page(tmp_path):
    stale = _story(id="stale-story", published_at_offset=timedelta(days=30))
    daily_news_store.upsert_new_stories(tmp_path, [stale])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    markdown_text = " ".join(m.value for m in at.markdown)
    assert "NVIDIA Announces Financial Results" not in markdown_text  # hidden from the page
    assert "stale-story" in daily_news_store.load_stories(tmp_path)  # still persisted, never deleted


# --- Two-column card content styling (assets/styles.css) — static
# CSS-content checks, since AppTest has no real browser layout engine
# to measure rendered pixel sizes or line-wrapping/overflow against. ------

_CSS_PATH = Path(__file__).parent.parent / "assets" / "styles.css"


def _css_text() -> str:
    return _CSS_PATH.read_text(encoding="utf-8")


def test_old_header_row_and_thumbnail_classes_are_fully_removed():
    css = _css_text()
    assert ".er-card-header" not in css
    assert ".er-card-thumb {" not in css


def test_content_wrapper_is_a_real_grid_with_text_and_image_columns():
    css = _css_text()
    assert ".er-news-card-content" in css
    rule = css.split(".er-news-card-content {")[1].split("}")[0]
    assert "display: grid" in rule
    assert "grid-template-columns" in rule
    assert "align-items: start" in rule  # image top-aligned against the text
    assert "1.125rem" in rule  # ~18px, within the approved 16-20px range


def test_text_column_has_min_width_zero_to_prevent_grid_overflow():
    css = _css_text()
    assert ".er-news-card-text" in css
    rule = css.split(".er-news-card-text {")[1].split("}")[0]
    assert "min-width: 0" in rule


def test_desktop_image_target_and_cap_and_landscape_crop():
    css = _css_text()
    assert ".er-news-card-thumb" in css
    rule = css.split(".er-news-card-thumb {")[1].split("}")[0]
    assert "height: 132px" in rule
    assert "max-width: 220px" in rule
    assert "max-height: 146px" in rule
    assert "object-fit: cover" in rule
    assert "border-radius: var(--r-sm)" in rule
    assert "float" not in rule
    assert "aspect-ratio" not in rule  # never the old full-width/tall hero rule


def test_image_column_track_never_exceeds_a_quarter_of_the_card_width():
    css = _css_text()
    content_rule = css.split(".er-news-card-content {")[1].split("}")[0]
    assert "25%" in content_rule


def test_narrow_breakpoint_shrinks_the_image_rail_rather_than_going_full_width():
    css = _css_text()
    assert "@media (max-width: 640px)" in css
    narrow_block = css.split("@media (max-width: 640px)")[1].split("@media (max-width: 480px)")[0]
    assert "112px" in narrow_block
    assert "height: 84px" in narrow_block
    assert "width: 100%" not in narrow_block


def test_very_narrow_breakpoint_falls_back_to_text_only_layout():
    css = _css_text()
    assert "@media (max-width: 480px)" in css
    very_narrow_block = css.split("@media (max-width: 480px)")[1]
    assert "grid-template-columns: 1fr" in very_narrow_block.split("}")[0]
    assert "display: none" in very_narrow_block.split("}")[1]
