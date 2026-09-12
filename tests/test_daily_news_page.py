"""Daily News public page — fixture-driven render tests via AppTest,
with daily_news.get_settings monkeypatched to a tmp cache_dir seeded
with fixture data. Zero network calls; the real data/cache/ (gitignored
live pilot cache) is never touched. Proves the public card renders
exactly the six approved fields — including the source-attribution pass's
visible source-type label (design/DECISIONS.md) — the single company
selector and rolling-7-day freshness filter behave as approved, and none
of the internal/Radar detail that daily_news_admin.py alone shows ever
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
from src.data_access.daily_news import daily_news_store, editorial_story_store
from src.models.daily_news_models import (
    EditorialStory,
    NewsSourceReference,
    NewsStateTransition,
    NewsStory,
    NewsStoryStatus,
    SourceClass,
)
from src.ui.pages.daily_news import (
    _MIXED_SUBTITLE,
    _OFFICIAL_SUBTITLE,
    _SOURCE_CLASS_LABELS,
    _elapsed_seconds,
    _is_recent,
    _page_subtitle,
    _recent_stories,
)

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


def _editorial_story(published_at_offset: timedelta = timedelta(hours=1), **overrides) -> EditorialStory:
    published_at = (datetime.now(timezone.utc) - published_at_offset).isoformat()
    defaults = dict(
        id="editorial-oracle-abc", headline="Oracle Corporation reports strong AI cloud demand", publisher="CNBC",
        source_url="https://www.cnbc.com/2026/09/11/oracle-ai-cloud.html", published_at=published_at,
        retrieved_at=published_at, excerpt="Oracle Corporation said AI cloud demand drove revenue higher.",
        matched_companies=("Oracle Corporation",), matched_themes=("ai-buildout",),
        source_feed_id="cnbc-technology-rss",
    )
    defaults.update(overrides)
    return EditorialStory(**defaults)


# --- Unified Daily News feed (design/DECISIONS.md): issuer + editorial ---
# stories interleaved into one reverse-chronological list. Both fixtures
# are seeded into the same tmp_path cache_dir (daily_news_store for
# issuer, editorial_story_store for editorial) since get_settings() is
# patched to the same Settings(cache_dir=tmp_path) for both lanes.


def test_mixed_fixture_renders_as_one_globally_newest_first_list(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [
        _story(id="s-newest", company_name="NVIDIA", published_at_offset=timedelta(hours=1)),
        _story(id="s-oldest", company_name="Intel Corp.", headline="Intel Reports Quarterly Results", published_at_offset=timedelta(hours=3)),
    ])
    editorial_story_store.upsert_new_stories(tmp_path, [
        _editorial_story(published_at_offset=timedelta(hours=2)),
    ])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    markdown_text = " ".join(m.value for m in at.markdown)
    newest_idx = markdown_text.index("NVIDIA Announces Financial Results")
    middle_idx = markdown_text.index("Oracle Corporation reports strong AI cloud demand")
    oldest_idx = markdown_text.index("Intel Reports Quarterly Results")
    assert newest_idx < middle_idx < oldest_idx


def test_both_item_types_appear_in_one_page_render(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story()])
    editorial_story_store.upsert_new_stories(tmp_path, [_editorial_story()])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "NVIDIA Announces Financial Results" in markdown_text
    assert "Oracle Corporation reports strong AI cloud demand" in markdown_text


def test_company_news_and_market_news_labels_appear_on_the_correct_card_types(tmp_path):
    mixed_dir = tmp_path / "mixed"
    daily_news_store.upsert_new_stories(mixed_dir, [_story()])
    editorial_story_store.upsert_new_stories(mixed_dir, [_editorial_story()])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(mixed_dir)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    markdown_text = " ".join(m.value for m in at.markdown)
    assert "Company news" in markdown_text
    assert "Market news" in markdown_text

    # Bound to the correct card: an issuer-only fixture (separate cache
    # dir, no editorial content at all) must never show "Market news".
    issuer_only_dir = tmp_path / "issuer_only"
    daily_news_store.upsert_new_stories(issuer_only_dir, [_story()])
    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(issuer_only_dir)):
        issuer_only_at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        issuer_only_at.run()
    issuer_only_text = " ".join(m.value for m in issuer_only_at.markdown)
    assert "Company news" in issuer_only_text
    assert "Market news" not in issuer_only_text


def test_editorial_coverage_heading_never_renders_in_the_unified_feed(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story()])
    editorial_story_store.upsert_new_stories(tmp_path, [_editorial_story()])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    all_text = " ".join(m.value for m in at.markdown)
    assert "Editorial Coverage" not in all_text


def test_translation_unavailable_issuer_story_stays_intact_alongside_an_editorial_story(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story(
        id="newsitem-korean-1", eeva_summary=None,
        translation_unavailable=True, original_title="삼성전자 신규시설투자 결정",
    )])
    editorial_story_store.upsert_new_stories(tmp_path, [_editorial_story()])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    markdown_text = " ".join(m.value for m in at.markdown)
    caption_text = " ".join(str(c.value) for c in at.caption)
    assert "삼성전자 신규시설투자 결정" in markdown_text
    assert "Translation unavailable" in caption_text
    assert "Oracle Corporation reports strong AI cloud demand" in markdown_text


def test_all_companies_includes_a_valid_theme_only_editorial_story(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story()])
    editorial_story_store.upsert_new_stories(tmp_path, [
        _editorial_story(id="editorial-theme-only", matched_companies=(), matched_themes=("memory",)),
    ])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    markdown_text = " ".join(m.value for m in at.markdown)
    assert "Oracle Corporation reports strong AI cloud demand" in markdown_text


def test_selected_company_includes_matching_issuer_and_editorial_stories(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [
        _story(id="s-nvidia", company_name="NVIDIA"),
        _story(id="s-intel", company_name="Intel Corp.", headline="Intel Reports Quarterly Results"),
    ])
    editorial_story_store.upsert_new_stories(tmp_path, [
        _editorial_story(id="editorial-nvidia-match", matched_companies=("NVIDIA",)),
    ])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        at.selectbox[0].select("NVIDIA").run()

    markdown_text = " ".join(m.value for m in at.markdown)
    assert "NVIDIA Announces Financial Results" in markdown_text
    assert "Oracle Corporation reports strong AI cloud demand" in markdown_text
    assert "Intel Reports Quarterly Results" not in markdown_text


def test_selected_company_excludes_editorial_stories_matching_only_another_company_or_theme(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story(id="s-nvidia", company_name="NVIDIA")])
    editorial_story_store.upsert_new_stories(tmp_path, [
        _editorial_story(id="editorial-other-company", matched_companies=("Corning Inc.",), matched_themes=()),
        _editorial_story(id="editorial-theme-only", headline="Memory market-wide coverage", matched_companies=(), matched_themes=("memory",)),
    ])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        at.selectbox[0].select("NVIDIA").run()

    markdown_text = " ".join(m.value for m in at.markdown)
    assert "NVIDIA Announces Financial Results" in markdown_text
    assert "Oracle Corporation reports strong AI cloud demand" not in markdown_text
    assert "Memory market-wide coverage" not in markdown_text


def test_issuer_fallback_is_issuer_only_and_never_waives_editorial_freshness(tmp_path):
    """The issuer stale-feed fallback (a company with persisted issuer
    stories older than 7 days) must never be satisfied or widened by
    editorial stories: a stale (>72h) editorial story matching that same
    company must stay hidden even while the issuer fallback is active."""
    daily_news_store.upsert_new_stories(tmp_path, [
        _story(id="s-intel-stale", company_name="Intel Corp.", headline="Intel Reports Quarterly Results", published_at_offset=timedelta(days=30)),
    ])
    editorial_story_store.upsert_new_stories(tmp_path, [
        _editorial_story(id="editorial-intel-stale", headline="Old Intel market coverage",
                          matched_companies=("Intel Corp.",), published_at_offset=timedelta(days=10)),
    ])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        at.selectbox[0].select("Intel Corp.").run()

    markdown_text = " ".join(m.value for m in at.markdown)
    assert (
        "No Intel Corp. official updates were published in the last 7 days. "
        "Showing the latest available official updates."
    ) in markdown_text
    assert "Intel Reports Quarterly Results" in markdown_text
    assert "Old Intel market coverage" not in markdown_text  # stale editorial, never surfaced by the issuer fallback


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


# --- Source-attribution pass: label mapping / subtitle strategy ----------


def test_every_source_class_maps_to_its_exact_approved_label():
    assert _SOURCE_CLASS_LABELS[SourceClass.OFFICIAL_COMPANY] == "Official company source"
    assert _SOURCE_CLASS_LABELS[SourceClass.REGULATORY_FILING] == "Regulatory filing"
    assert _SOURCE_CLASS_LABELS[SourceClass.PRESS_RELEASE_WIRE] == "Press-release wire"
    assert _SOURCE_CLASS_LABELS[SourceClass.INDEPENDENT_JOURNALISM] == "Independent journalism"
    # Every real SourceClass member has an approved label — no category
    # silently falls back to its raw enum value.
    assert set(_SOURCE_CLASS_LABELS) == set(SourceClass)


def test_page_subtitle_is_official_only_when_every_visible_story_is_official():
    stories = [_story(company_name="NVIDIA"), _story(id="newsitem-amd", company_name="Advanced Micro Devices")]
    assert _page_subtitle(stories) == _OFFICIAL_SUBTITLE


def test_page_subtitle_is_mixed_when_any_visible_story_is_non_official():
    official = _story(company_name="NVIDIA")
    non_official = _story(
        id="newsitem-cnbc-x", company_name="NVIDIA",
        sources=(NewsSourceReference(
            publisher="CNBC", source_class=SourceClass.INDEPENDENT_JOURNALISM,
            url="https://www.cnbc.com/x", title="H", published_at=official.sources[0].published_at,
            retrieved_at=official.sources[0].published_at, original_language="English",
        ),),
    )
    assert _page_subtitle([official, non_official]) == _MIXED_SUBTITLE


def test_page_subtitle_defaults_to_official_only_with_no_visible_stories():
    """The conservative default: an empty visible set contains nothing
    that contradicts the official-only claim."""
    assert _page_subtitle([]) == _OFFICIAL_SUBTITLE


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
    assert "Showing tracked coverage from the past 7 days." in all_text


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


def test_public_card_shows_the_source_type_label_but_never_radar_terminology(tmp_path):
    """Source-attribution pass (design/DECISIONS.md): supersedes the
    former test of the same shape, which asserted "Official company
    source" was never shown — that was the pre-attribution-pass design;
    the approved behavior now is the opposite, the source-type label is
    always visible. Radar-internal jargon must still never appear."""
    daily_news_store.upsert_new_stories(tmp_path, [_story()])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    content_only = [m.value for m in at.markdown if not m.value.strip().startswith("<style")]
    all_text = " ".join(content_only) + " ".join(str(c.value) for c in at.caption)
    assert "Official company source" in all_text
    for forbidden in ("RETRIEVAL_FAILED", "EXTRACTED", "PENDING", "confidence", "matched_rules", "Review processing"):
        assert forbidden not in all_text


def test_official_only_visible_stories_render_the_official_subtitle(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story()])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    all_text = " ".join(m.value for m in at.markdown)
    assert _OFFICIAL_SUBTITLE in all_text
    assert _MIXED_SUBTITLE not in all_text
    # EDINET-safety-review correction (design/DECISIONS.md): the static
    # freshness caption must read as a neutral coverage statement, not a
    # second, contradictory official-only claim.
    assert "Showing tracked coverage from the past 7 days." in all_text
    assert "Showing official company updates from the past 7 days." not in all_text


def test_mixed_source_classes_render_the_generalized_subtitle(tmp_path):
    official = _story(id="newsitem-nvidia-abc123", company_name="NVIDIA")
    non_official = _story(
        id="newsitem-cnbc-xyz", company_name="NVIDIA", headline="CNBC coverage of NVIDIA",
        sources=(NewsSourceReference(
            publisher="CNBC", source_class=SourceClass.INDEPENDENT_JOURNALISM,
            url="https://www.cnbc.com/x", title="CNBC coverage of NVIDIA",
            published_at=official.sources[0].published_at, retrieved_at=official.sources[0].published_at,
            original_language="English",
        ),),
    )
    daily_news_store.upsert_new_stories(tmp_path, [official, non_official])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    all_text = " ".join(m.value for m in at.markdown)
    assert _MIXED_SUBTITLE in all_text
    assert _OFFICIAL_SUBTITLE not in all_text
    # Category distinction preserved per-card — never a blanket "editorial" claim.
    assert "Official company source" in all_text
    assert "Independent journalism" in all_text
    # EDINET-safety-review correction (design/DECISIONS.md): the static
    # freshness caption must read as a neutral coverage statement, not a
    # second, contradictory official-only claim.
    assert "Showing tracked coverage from the past 7 days." in all_text
    assert "Showing official company updates from the past 7 days." not in all_text


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


def test_selected_company_with_only_stale_stories_shows_notice_and_fallback_stories(tmp_path):
    # Intel has only a stale story — appears in the selector (it has a
    # persisted PUBLISHED story) but has nothing recent once selected.
    # Stale-feed fallback pass: this must no longer show an empty state
    # indistinguishable from "Intel has never published anything" — it
    # shows the neutral notice plus Intel's own latest available story,
    # still carrying its real (stale) published date.
    daily_news_store.upsert_new_stories(tmp_path, [
        _story(
            id="s-intel-stale", company_name="Intel Corp.", headline="Intel Reports Quarterly Results",
            published_at_offset=timedelta(days=30),
        ),
        _story(id="s-nvidia-fresh", company_name="NVIDIA"),
    ])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        at.selectbox[0].select("Intel Corp.").run()

    markdown_text = " ".join(m.value for m in at.markdown)
    assert "No recent updates for Intel Corp. in the last 7 days." not in markdown_text
    assert (
        "No Intel Corp. official updates were published in the last 7 days. "
        "Showing the latest available official updates."
    ) in markdown_text
    assert "Intel Reports Quarterly Results" in markdown_text
    assert "NVIDIA Announces Financial Results" not in markdown_text  # scoped to Intel only, not NVIDIA


def test_selected_company_with_recent_stories_shows_no_fallback_notice(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [
        _story(id="s-intel-fresh", company_name="Intel Corp.", headline="Intel Reports Quarterly Results"),
    ])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        at.selectbox[0].select("Intel Corp.").run()

    markdown_text = " ".join(m.value for m in at.markdown)
    assert "Intel Reports Quarterly Results" in markdown_text
    assert "Showing the latest available official updates." not in markdown_text
    assert "No recent updates for Intel Corp. in the last 7 days." not in markdown_text


def test_stories_for_company_returns_empty_list_when_company_has_no_persisted_stories():
    # The real dropdown can never actually offer a company with zero
    # persisted stories (_company_options only lists companies that
    # already have one) — this proves the pure input/output mapping
    # render()'s own "true empty state" branch depends on directly,
    # the same boundary-logic-as-pure-function pattern already used for
    # _is_recent/_recent_stories above.
    from src.ui.pages.daily_news import _stories_for_company

    only_nvidia = [_story(id="s-nvidia", company_name="NVIDIA")]
    assert _stories_for_company(only_nvidia, "Intel Corp.") == []


def test_all_companies_never_shows_a_fallback_story_from_a_stale_company(tmp_path):
    # A company with only stale stories must never leak into the global
    # "All companies" feed via the per-company fallback path — that
    # fallback is scoped strictly to a single selected company.
    daily_news_store.upsert_new_stories(tmp_path, [
        _story(
            id="s-intel-stale", company_name="Intel Corp.", headline="Intel Reports Quarterly Results",
            published_at_offset=timedelta(days=30),
        ),
        _story(id="s-nvidia-fresh", company_name="NVIDIA"),
    ])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    markdown_text = " ".join(m.value for m in at.markdown)
    assert "NVIDIA Announces Financial Results" in markdown_text
    assert "Intel Reports Quarterly Results" not in markdown_text
    assert "Showing the latest available official updates." not in markdown_text


def test_recency_is_controlled_by_published_at_never_retrieved_at_or_discovery_time(tmp_path):
    # A story just discovered/retrieved "now" but whose publisher-claimed
    # published_at is 30 days old must still be excluded from the 7-day
    # window — recency is never inferred from retrieved_at, created_at,
    # or discovery/state-transition time.
    from src.ui.pages.daily_news import _is_recent

    now = datetime.now(timezone.utc)
    old_published_at = (now - timedelta(days=30)).isoformat()
    fresh_retrieved_at = now.isoformat()
    story = _story(
        sources=(
            NewsSourceReference(
                publisher="Intel Corp.", source_class=SourceClass.OFFICIAL_COMPANY,
                url="https://www.intc.com/news-events/press-releases/detail/old-release",
                title="Old Release", published_at=old_published_at, retrieved_at=fresh_retrieved_at,
                original_language="English",
            ),
        ),
        state_history=[NewsStateTransition(status=NewsStoryStatus.PUBLISHED, at=fresh_retrieved_at)],
    )
    assert not _is_recent(story, now)


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


def test_image_bearing_story_renders_as_a_plain_text_only_card(tmp_path):
    # Source-image rendering is disabled: a story with a fully valid,
    # allowlisted image_url/image_alt must still render as an ordinary
    # text-only card — no <img>, no image wrapper/column/header class,
    # regardless of what's stored.
    daily_news_store.upsert_new_stories(tmp_path, [_image_bearing_story()])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    card_markdown = [m.value for m in at.markdown if "rail-logo" not in m.value and not m.value.strip().startswith("<style")]
    card_text = " ".join(card_markdown)
    assert "<img" not in card_text
    for forbidden_class in ("er-news-card-content", "er-news-card-text", "er-news-card-thumb", "er-card-header", "er-card-thumb"):
        assert forbidden_class not in card_text
    assert "https://iprsoftwaremedia.com/photo.jpg" not in card_text
    assert "A photo of the announcement" not in card_text
    # Metadata, headline, summary, and link are all still present.
    assert "NVIDIA · NVIDIA ·" in card_text
    assert "NVIDIA Announces Financial Results" in card_text
    assert "NVIDIA reported strong quarterly results." in card_text
    assert "Read original source" in card_text
    assert "https://nvidianews.nvidia.com/news/results" in card_text


def test_card_without_an_image_renders_unchanged(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story()])  # default _story() has no image_url

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    card_markdown = [m.value for m in at.markdown if "rail-logo" not in m.value and not m.value.strip().startswith("<style")]
    card_text = " ".join(card_markdown)
    assert "<img" not in card_text
    assert "er-news-card-content" not in card_text
    assert "er-card-header" not in card_text
    assert "NVIDIA Announces Financial Results" in card_text
    assert "Read original source" in card_text


def test_translation_unavailable_image_bearing_story_still_shows_original_title_and_caption(tmp_path):
    # The translation-unavailable path must also fall through to the
    # plain text-only card when the story happens to carry image data.
    story = _story(
        sources=(
            NewsSourceReference(
                publisher="NVIDIA", source_class=SourceClass.OFFICIAL_COMPANY,
                url="https://nvidianews.nvidia.com/news/results", title="NVIDIA Announces Financial Results",
                published_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
                retrieved_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
                original_language="English",
                image_url="https://iprsoftwaremedia.com/photo.jpg", image_alt="A photo",
            ),
        ),
        eeva_summary=None, translation_unavailable=True, original_title="삼성전자 신규시설투자 결정",
    )
    daily_news_store.upsert_new_stories(tmp_path, [story])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    card_markdown = [m.value for m in at.markdown if "rail-logo" not in m.value and not m.value.strip().startswith("<style")]
    card_text = " ".join(card_markdown)
    caption_text = " ".join(str(c.value) for c in at.caption)
    assert "<img" not in card_text
    assert "삼성전자 신규시설투자 결정" in card_text
    assert "Translation unavailable" in caption_text


def test_old_story_remains_persisted_but_hidden_from_the_page(tmp_path):
    stale = _story(id="stale-story", published_at_offset=timedelta(days=30))
    daily_news_store.upsert_new_stories(tmp_path, [stale])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    markdown_text = " ".join(m.value for m in at.markdown)
    assert "NVIDIA Announces Financial Results" not in markdown_text  # hidden from the page
    assert "stale-story" in daily_news_store.load_stories(tmp_path)  # still persisted, never deleted


# --- Image-layout CSS is unreachable now that rendering is disabled —
# confirm the dead rules were actually removed, not just unused. ----------

_CSS_PATH = Path(__file__).parent.parent / "assets" / "styles.css"


def _css_text() -> str:
    return _CSS_PATH.read_text(encoding="utf-8")


def test_all_daily_news_image_layout_css_has_been_removed():
    css = _css_text()
    for removed_class in (
        ".er-card-header", ".er-card-thumb", ".er-news-card-content", ".er-news-card-text", ".er-news-card-thumb",
    ):
        assert removed_class not in css
