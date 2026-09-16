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
    NewsMaterialityTier,
    NewsSourceReference,
    NewsStateTransition,
    NewsStory,
    NewsStoryStatus,
    SourceClass,
)
from src.ui.pages.daily_news import (
    _DEFAULT_TIER_QUERY_VALUE,
    _HIGH_SIGNALS_QUERY_VALUE,
    _NO_HIGH_SIGNAL_EMPTY_STATE,
    _NO_WATCHLIST_EMPTY_STATE,
    _SOURCE_CLASS_LABELS,
    _SUBTITLE,
    _TIER_QUERY_PARAM,
    _WATCHLIST_QUERY_VALUE,
    _elapsed_seconds,
    _is_recent,
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
        # High Signals / Watchlist tier navigation (design/DAILY_NEWS_
        # HIGH_SIGNALS_WATCHLIST_IMPLEMENTATION_PLAN_2026_09_16.md) —
        # High Signals is now the default view a bare AppTest run lands
        # on, so this shared fixture defaults to High-Signal tier too,
        # keeping every pre-existing, tier-agnostic test (company
        # selection, freshness, translation, image handling, dedup, ...)
        # visible without individually adding a `tier=watchlist` query
        # param. Any test that needs a different tier (or the true
        # materiality_tier=None default) passes its own override, which
        # always wins over this default (defaults.update(overrides)).
        materiality_tier=NewsMaterialityTier.HIGH_SIGNAL,
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
        # Same rationale as _story()'s own materiality_tier default above.
        materiality_tier=NewsMaterialityTier.HIGH_SIGNAL,
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


def test_subtitle_is_the_one_fixed_approved_sentence_regardless_of_source_mix():
    """Product-naming separation (design/DECISIONS.md): the former
    official-only-vs-mixed subtitle distinction (_OFFICIAL_SUBTITLE/
    _MIXED_SUBTITLE via _page_subtitle()) was replaced by one fixed,
    approved sentence — the per-card source-type label already carries
    the official/editorial distinction, so the page subtitle itself no
    longer needs to."""
    assert _SUBTITLE == (
        "Material disclosures and developments across AI infrastructure "
        "and global technology supply chains."
    )


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
    assert _SUBTITLE in all_text
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


def test_official_only_visible_stories_render_the_fixed_subtitle(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story()])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    all_text = " ".join(m.value for m in at.markdown)
    assert _SUBTITLE in all_text
    # EDINET-safety-review correction (design/DECISIONS.md): the static
    # freshness caption must read as a neutral coverage statement, not a
    # second, contradictory official-only claim.
    assert "Showing tracked coverage from the past 7 days." in all_text
    assert "Showing official company updates from the past 7 days." not in all_text


def test_mixed_source_classes_render_the_same_fixed_subtitle(tmp_path):
    """Product-naming separation (design/DECISIONS.md): the subtitle no
    longer varies by source mix — proves the same fixed sentence renders
    whether visible coverage is official-only or mixed, and that the
    per-card source-type label (never a blanket "editorial" claim) is
    what actually carries the category distinction."""
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
    assert _SUBTITLE in all_text
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
    # System-wide company-matched-news fix (design/DECISIONS.md): the
    # selector now lists the full Daily News company universe (every
    # tracked company plus Daily-News-only stubs), not only companies
    # with an existing persisted issuer story — Intel Corp./NVIDIA are
    # both real tracked companies and so are present alongside every
    # other one, in the same sorted order daily_news_company_names()
    # itself returns.
    from src.data_access.daily_news.company_aliases import daily_news_company_names

    select_options = at.selectbox[0].options
    assert select_options == ["All companies"] + list(daily_news_company_names())
    assert "Intel Corp." in select_options
    assert "NVIDIA" in select_options


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
    # Intel has only a stale story and nothing recent once selected —
    # Intel is selectable regardless (the selector now lists the full
    # Daily News company universe, not only companies with an existing
    # persisted issuer story; see the system-wide company-matched-news
    # fix, design/DECISIONS.md). Stale-feed fallback pass: this must no
    # longer show an empty state
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
    # The real dropdown CAN now offer a company with zero persisted
    # issuer stories (the selector lists the full Daily News company
    # universe — see the system-wide company-matched-news fix, design/
    # DECISIONS.md); this proves the pure input/output mapping render()'s
    # own "true empty state" branch depends on directly, the same
    # boundary-logic-as-pure-function pattern already used for
    # _is_recent/_recent_stories above. See
    # test_selecting_a_company_with_no_coverage_at_all_shows_the_honest_empty_state
    # below for the real, now-reachable UI path.
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


# ============================================================
# System-wide company-matched-news fix (design/DECISIONS.md) —
# generic, multi-company coverage: the per-company editorial query
# bypasses the global cross-company cap, the selector offers the full
# Daily News company universe (including Daily-News-only stubs like
# HPE), stale-official cards are unmistakably labeled Historical, and
# the "no updates" notice never obscures real current editorial
# coverage. Companies used below span the U.S., Japan, and South Korea,
# and both a real tracked company and a Daily-News-only stub — none of
# this is Amazon-specific.
# ============================================================


def test_a_story_crowded_out_of_the_global_top_twenty_still_appears_for_its_company(tmp_path):
    # The core fix (requirement 1), exercised at the UI level: Oracle's
    # own matched story is ranked outside the shared "All companies"
    # top-20 by 25 newer, unrelated Intel stories, yet still appears when
    # Oracle Corporation is selected directly.
    editorial_story_store.upsert_new_stories(tmp_path, [
        _editorial_story(
            id="oracle-crowded-out", published_at_offset=timedelta(hours=50),
            headline="Oracle Corporation reports strong AI cloud demand",
            matched_companies=("Oracle Corporation",), matched_themes=(),
        ),
    ])
    crowd = [
        _editorial_story(
            id=f"intel-crowd-{n}-{i}", published_at_offset=timedelta(hours=i),
            headline=f"Intel Corp. update {n}-{i}", publisher=f"Source{n}",
            source_feed_id=f"src-crowd-{n}", matched_companies=("Intel Corp.",), matched_themes=(),
        )
        for n in range(5) for i in range(5)
    ]
    editorial_story_store.upsert_new_stories(tmp_path, crowd)

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        all_companies_text = " ".join(m.value for m in at.markdown)
        assert "Oracle Corporation reports strong AI cloud demand" not in all_companies_text  # crowded out globally

        at.selectbox[0].select("Oracle Corporation").run()

    company_text = " ".join(m.value for m in at.markdown)
    assert "Oracle Corporation reports strong AI cloud demand" in company_text


def test_daily_news_only_stub_company_is_selectable_and_shows_its_own_official_coverage(tmp_path):
    # Hewlett Packard Enterprise Company is a Daily-News-only
    # issuer_registry.DISCOVERY_STUBS entry, never in tracked_companies.py
    # — proves the universe/selector genuinely includes it, not only
    # real tracked companies.
    daily_news_store.upsert_new_stories(tmp_path, [
        _story(
            id="s-hpe", company_name="Hewlett Packard Enterprise Company",
            headline="HPE Announces New AI Server Platform",
        ),
    ])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        assert "Hewlett Packard Enterprise Company" in at.selectbox[0].options
        at.selectbox[0].select("Hewlett Packard Enterprise Company").run()

    markdown_text = " ".join(m.value for m in at.markdown)
    assert "HPE Announces New AI Server Platform" in markdown_text


def test_daily_news_only_stub_company_with_zero_coverage_shows_the_honest_empty_state(tmp_path):
    # No issuer store, no editorial store seeded at all — a company with
    # genuinely zero content in either lane is now reachable via the
    # selector (the universe includes every company, not only ones with
    # an existing persisted story) and must show a true, honest empty
    # state, never an error or a misleading notice.
    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        assert "Hewlett Packard Enterprise Company" in at.selectbox[0].options
        at.selectbox[0].select("Hewlett Packard Enterprise Company").run()

    markdown_text = " ".join(m.value for m in at.markdown)
    assert "No recent official or qualified market coverage for Hewlett Packard Enterprise Company right now." in markdown_text


def test_historical_badge_appears_on_stale_fallback_issuer_cards(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [
        _story(
            id="s-intel-stale", company_name="Intel Corp.", headline="Intel Reports Quarterly Results",
            published_at_offset=timedelta(days=30),
        ),
    ])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        at.selectbox[0].select("Intel Corp.").run()

    markdown_text = " ".join(m.value for m in at.markdown)
    assert "Historical — not from the last 7 days" in markdown_text


def test_historical_badge_never_appears_on_a_fresh_issuer_card(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [
        _story(id="s-intel-fresh", company_name="Intel Corp.", headline="Intel Reports Quarterly Results"),
    ])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        at.selectbox[0].select("Intel Corp.").run()

    markdown_text = " ".join(m.value for m in at.markdown)
    assert "Historical" not in markdown_text


def test_historical_badge_never_appears_on_the_all_companies_view(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story()])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    markdown_text = " ".join(m.value for m in at.markdown)
    assert "Historical" not in markdown_text


def test_no_updates_notice_is_suppressed_when_fresh_editorial_coverage_exists(tmp_path):
    # Requirement 5: a quiet official IR feed must never imply "no
    # company news" when real, current editorial coverage exists —
    # Intel's official feed is stale, but Intel has fresh, correctly
    # matched editorial coverage, so the page-level notice is suppressed
    # (the per-card Historical label still discloses the official
    # card's own age).
    daily_news_store.upsert_new_stories(tmp_path, [
        _story(
            id="s-intel-stale", company_name="Intel Corp.", headline="Intel Reports Quarterly Results",
            published_at_offset=timedelta(days=30),
        ),
    ])
    editorial_story_store.upsert_new_stories(tmp_path, [
        _editorial_story(
            id="editorial-intel-fresh", headline="Intel Corp. unveils new foundry roadmap",
            matched_companies=("Intel Corp.",), matched_themes=(), published_at_offset=timedelta(hours=2),
        ),
    ])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        at.selectbox[0].select("Intel Corp.").run()

    markdown_text = " ".join(m.value for m in at.markdown)
    assert "Showing the latest available official updates." not in markdown_text
    assert "Intel Reports Quarterly Results" in markdown_text  # historical card still shown
    assert "Historical — not from the last 7 days" in markdown_text  # still unmistakably labeled
    assert "Intel Corp. unveils new foundry roadmap" in markdown_text  # fresh editorial coverage shown


def test_no_updates_notice_still_shows_when_no_editorial_coverage_exists_either(tmp_path):
    # The counterpart to the test above: with no fresh editorial
    # coverage at all, the page-level notice is exactly as before.
    daily_news_store.upsert_new_stories(tmp_path, [
        _story(
            id="s-intel-stale", company_name="Intel Corp.", headline="Intel Reports Quarterly Results",
            published_at_offset=timedelta(days=30),
        ),
    ])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        at.selectbox[0].select("Intel Corp.").run()

    markdown_text = " ".join(m.value for m in at.markdown)
    assert "Showing the latest available official updates." in markdown_text


def test_multi_company_story_appears_on_both_matched_companies_pages(tmp_path):
    editorial_story_store.upsert_new_stories(tmp_path, [
        _editorial_story(
            id="joint-story", headline="Amazon and Alphabet both announce new AI data center investment",
            matched_companies=("Amazon.com, Inc.", "Alphabet Inc."), matched_themes=(),
        ),
    ])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        at.selectbox[0].select("Amazon.com, Inc.").run()
        amazon_text = " ".join(m.value for m in at.markdown)

        at2 = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at2.run()
        at2.selectbox[0].select("Alphabet Inc.").run()
        google_text = " ".join(m.value for m in at2.markdown)

    assert "Amazon and Alphabet both announce new AI data center investment" in amazon_text
    assert "Amazon and Alphabet both announce new AI data center investment" in google_text


def test_japan_and_korea_tracked_companies_are_selectable_with_matched_editorial_coverage(tmp_path):
    # Parameterized across regions — not U.S.-only. Samsung Electronics
    # (South Korea) and Murata Manufacturing Co., Ltd. (Japan) are both
    # real tracked companies with their own established mechanical
    # aliases; no company-specific code path is involved.
    editorial_story_store.upsert_new_stories(tmp_path, [
        _editorial_story(
            id="samsung-story", headline="Samsung posts strong memory chip demand",
            matched_companies=("Samsung Electronics",), matched_themes=(),
        ),
        _editorial_story(
            id="murata-story", headline="Murata expands capacitor production capacity",
            matched_companies=("Murata Manufacturing Co., Ltd.",), matched_themes=(),
        ),
    ])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        assert "Samsung Electronics" in at.selectbox[0].options
        assert "Murata Manufacturing Co., Ltd." in at.selectbox[0].options

        at.selectbox[0].select("Samsung Electronics").run()
        samsung_text = " ".join(m.value for m in at.markdown)

        at2 = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at2.run()
        at2.selectbox[0].select("Murata Manufacturing Co., Ltd.").run()
        murata_text = " ".join(m.value for m in at2.markdown)

    assert "Samsung posts strong memory chip demand" in samsung_text
    assert "Murata expands capacitor production capacity" not in samsung_text
    assert "Murata expands capacitor production capacity" in murata_text
    assert "Samsung posts strong memory chip demand" not in murata_text


def test_all_companies_view_editorial_cap_and_content_are_unaffected(tmp_path):
    # Regression: the "All companies" path must stay byte-for-byte
    # unchanged — still reads visible_editorial (the cross-company-capped
    # list), not the new per-company query.
    editorial_story_store.upsert_new_stories(tmp_path, [_editorial_story()])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    markdown_text = " ".join(m.value for m in at.markdown)
    assert "Oracle Corporation reports strong AI cloud demand" in markdown_text
    assert "Market news" in markdown_text


# ============================================================
# Signals materiality classification (design/DECISIONS.md) — tier-based
# presentation: High Signal default feed, Watchlist secondary section,
# Background behind an explicit control, and the exact empty state.
# ============================================================


def test_high_signal_story_renders_in_the_default_section_with_its_badge(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [
        _story(materiality_tier=NewsMaterialityTier.HIGH_SIGNAL, materiality_reasons=("primary_disclosure",)),
    ])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "High Signal" in markdown_text
    assert "NVIDIA Announces Financial Results" in markdown_text
    assert _NO_HIGH_SIGNAL_EMPTY_STATE not in markdown_text


def test_watchlist_story_renders_in_watchlist_view_with_its_badge(tmp_path):
    """High Signals / Watchlist tier navigation — Watchlist is no longer
    part of the default page load (that is now High Signals only, see
    the URL-state tests below); this test explicitly opens
    ?tier=watchlist, its own real, intended context."""
    daily_news_store.upsert_new_stories(tmp_path, [
        _story(materiality_tier=NewsMaterialityTier.WATCHLIST, materiality_reasons=("on_taxonomy_no_anchor:x",)),
    ])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.query_params["tier"] = "watchlist"
        at.run()

    assert not at.exception
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "Watchlist" in markdown_text
    assert "NVIDIA Announces Financial Results" in markdown_text


def test_background_story_is_absent_from_high_signals(tmp_path):
    """Background must never appear in High Signals — not behind an
    expander, not anywhere — verified by construction (the Watchlist/
    Background render branch is never reached at all while High Signals
    is active), not merely by an added exclusion check."""
    daily_news_store.upsert_new_stories(tmp_path, [
        _story(materiality_tier=NewsMaterialityTier.BACKGROUND, materiality_reasons=("off_taxonomy_no_anchor",)),
    ])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.query_params["tier"] = "high_signal"
        at.run()

    assert not at.exception
    assert len(at.expander) == 0
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "NVIDIA Announces Financial Results" not in markdown_text
    assert _NO_HIGH_SIGNAL_EMPTY_STATE in markdown_text


def test_background_story_is_reachable_only_via_the_collapsed_toggle_inside_watchlist(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [
        _story(materiality_tier=NewsMaterialityTier.BACKGROUND, materiality_reasons=("off_taxonomy_no_anchor",)),
    ])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.query_params["tier"] = "watchlist"
        at.run()

    assert not at.exception
    # AppTest renders every st.expander's contents regardless of its
    # collapsed/expanded visual state (Streamlit doesn't conditionally
    # skip building collapsed content), so this proves the Background
    # item is retained (never deleted) and specifically reachable inside
    # the "Show Background" control, not the primary Watchlist list. The
    # expander's own label is a distinct element type, not a markdown
    # node — checked via at.expander, not markdown_text.
    assert any(exp.label.startswith("Show Background") for exp in at.expander)
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "NVIDIA Announces Financial Results" in markdown_text
    assert _NO_WATCHLIST_EMPTY_STATE in markdown_text  # the primary Watchlist list itself is empty


def test_no_background_control_rendered_within_watchlist_when_there_are_zero_background_items(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story(materiality_tier=NewsMaterialityTier.WATCHLIST)])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.query_params["tier"] = "watchlist"
        at.run()

    assert len(at.expander) == 0


def test_legacy_unclassified_story_defaults_to_watchlist_for_display_only(tmp_path):
    """A story persisted before materiality_tier existed (None, the
    field's own real default — see NewsMaterialityTier's own docstring)
    must still render safely: shown in Watchlist (a safe display
    default), never silently hidden like Background, never overclaimed
    as High Signal. Explicitly overrides the shared _story() fixture's
    own new High-Signal default (see that function's own comment) back
    to the true, real persisted default this test is about."""
    story = _story(materiality_tier=None)
    assert story.materiality_tier is None  # the actual persisted/default value — never mutated by this test
    daily_news_store.upsert_new_stories(tmp_path, [story])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.query_params["tier"] = "watchlist"
        at.run()

    assert not at.exception
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "Watchlist" in markdown_text
    assert "NVIDIA Announces Financial Results" in markdown_text
    assert len(at.expander) == 0


def test_no_high_signal_items_shows_the_exact_approved_empty_state(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story(materiality_tier=NewsMaterialityTier.WATCHLIST)])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()  # no tier query param — the default view is High Signals

    markdown_text = " ".join(m.value for m in at.markdown)
    assert "No High Signals match the current filters." in markdown_text


def test_no_watchlist_items_shows_the_exact_approved_empty_state(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story(materiality_tier=NewsMaterialityTier.HIGH_SIGNAL)])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.query_params["tier"] = "watchlist"
        at.run()

    markdown_text = " ".join(m.value for m in at.markdown)
    assert "No Watchlist items match the current filters." in markdown_text


def test_editorial_high_signal_story_renders_with_its_badge_in_the_default_section(tmp_path):
    editorial_story_store.upsert_new_stories(tmp_path, [
        _editorial_story(materiality_tier=NewsMaterialityTier.HIGH_SIGNAL, materiality_reasons=("primary_disclosure",)),
    ])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "High Signal" in markdown_text
    assert "Oracle Corporation reports strong AI cloud demand" in markdown_text


def _seed_mixed_tier_items(tmp_path) -> None:
    daily_news_store.upsert_new_stories(tmp_path, [
        _story(
            id="newsitem-nvidia-high", materiality_tier=NewsMaterialityTier.HIGH_SIGNAL,
            headline="NVIDIA files 8-K disclosing material agreement",
        ),
    ])
    editorial_story_store.upsert_new_stories(tmp_path, [
        _editorial_story(
            id="editorial-watch", materiality_tier=NewsMaterialityTier.WATCHLIST,
            headline="Solo developer gets CUDA running on AMD GPUs",
        ),
    ])


def test_high_signals_view_shows_only_high_signal_items(tmp_path):
    """High Signals / Watchlist tier navigation — supersedes the former
    single-page "each render in their own section" test: only one tier
    section renders per page view now. High Signals must show the
    High-Signal item and never the Watchlist item."""
    _seed_mixed_tier_items(tmp_path)

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.query_params["tier"] = "high_signal"
        at.run()

    assert not at.exception
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "NVIDIA files 8-K disclosing material agreement" in markdown_text
    assert "Solo developer gets CUDA running on AMD GPUs" not in markdown_text
    # "Watchlist" alone also appears in unrelated global chrome (sidebar/
    # CSS comments) — the section heading itself is the precise check.
    assert "Watchlist (" not in markdown_text
    assert len(at.expander) == 0  # Background is unreachable while High Signals is active


def test_watchlist_view_shows_only_watchlist_and_none_tier_items(tmp_path):
    """Watchlist must show the Watchlist item and never the High-Signal
    item — the exact counterpart of the High Signals test above."""
    _seed_mixed_tier_items(tmp_path)

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.query_params["tier"] = "watchlist"
        at.run()

    assert not at.exception
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "Solo developer gets CUDA running on AMD GPUs" in markdown_text
    assert "NVIDIA files 8-K disclosing material agreement" not in markdown_text
    assert _NO_HIGH_SIGNAL_EMPTY_STATE not in markdown_text


# ============================================================
# High Signals / Watchlist URL-state navigation (design/DAILY_NEWS_HIGH_
# SIGNALS_WATCHLIST_IMPLEMENTATION_PLAN_2026_09_16.md) — reading,
# validating, normalizing, and switching the canonical `tier` query
# parameter.
# ============================================================


def test_bare_page_load_defaults_to_high_signals_and_normalizes_the_url(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story()])  # High-Signal tier by the shared fixture's own default

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()  # no tier query param set at all

    assert not at.exception
    assert at.query_params.get(_TIER_QUERY_PARAM) == [_HIGH_SIGNALS_QUERY_VALUE]
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "NVIDIA Announces Financial Results" in markdown_text


def test_tier_high_signal_query_param_opens_high_signals_directly(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story()])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.query_params["tier"] = "high_signal"
        at.run()

    assert not at.exception
    assert at.query_params.get(_TIER_QUERY_PARAM) == [_HIGH_SIGNALS_QUERY_VALUE]
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "NVIDIA Announces Financial Results" in markdown_text


def test_tier_watchlist_query_param_opens_watchlist_directly(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story(materiality_tier=NewsMaterialityTier.WATCHLIST)])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.query_params["tier"] = "watchlist"
        at.run()

    assert not at.exception
    assert at.query_params.get(_TIER_QUERY_PARAM) == [_WATCHLIST_QUERY_VALUE]
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "NVIDIA Announces Financial Results" in markdown_text


def test_invalid_tier_value_normalizes_to_high_signals(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story()])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.query_params["tier"] = "not-a-real-tier"
        at.run()

    assert not at.exception
    assert at.query_params.get(_TIER_QUERY_PARAM) == [_HIGH_SIGNALS_QUERY_VALUE]
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "NVIDIA Announces Financial Results" in markdown_text


def test_repeated_or_ambiguously_resolved_tier_value_normalizes_to_high_signals(tmp_path):
    """AppTest's own `query_params` is a plain dict — it cannot literally
    represent a repeated query-string key ("?tier=a&tier=b"), so a truly
    repeated URL cannot be constructed through this harness. What IS
    directly verified: _resolve_active_tier_view() performs no special-
    case handling based on repetition at all — it only ever validates
    whatever single string st.query_params.get("tier", "") resolves to
    (Streamlit's own QueryParams.get() is documented to resolve a
    repeated key to its last occurrence) against the two valid values.
    This test exercises that same single-value validation path with an
    arbitrary, clearly-invalid string standing in for "whatever a
    repeated key resolved to" — functionally identical to the invalid-
    value case above, which is the real guarantee this requirement
    reduces to given the harness's own limitation."""
    daily_news_store.upsert_new_stories(tmp_path, [_story()])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.query_params["tier"] = "watchlist-then-overwritten-to-something-else"
        at.run()

    assert not at.exception
    assert at.query_params.get(_TIER_QUERY_PARAM) == [_HIGH_SIGNALS_QUERY_VALUE]


def test_switching_the_segmented_control_updates_the_canonical_tier_url(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [
        _story(
            id="newsitem-nvidia-high", materiality_tier=NewsMaterialityTier.HIGH_SIGNAL,
            headline="NVIDIA High Signal headline",
        ),
        _story(
            id="newsitem-nvidia-watch", materiality_tier=NewsMaterialityTier.WATCHLIST,
            headline="NVIDIA Watchlist headline",
        ),
    ])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.query_params["tier"] = "high_signal"
        at.run()
        assert at.query_params.get(_TIER_QUERY_PARAM) == [_HIGH_SIGNALS_QUERY_VALUE]
        markdown_before = " ".join(m.value for m in at.markdown)
        assert "NVIDIA High Signal headline" in markdown_before
        assert "NVIDIA Watchlist headline" not in markdown_before

        at.segmented_control[0].set_value("Watchlist").run()

    assert not at.exception
    assert at.query_params.get(_TIER_QUERY_PARAM) == [_WATCHLIST_QUERY_VALUE]
    markdown_after = " ".join(m.value for m in at.markdown)
    assert "NVIDIA Watchlist headline" in markdown_after
    assert "NVIDIA High Signal headline" not in markdown_after  # the High-Signal item, now hidden


def test_company_filter_applies_within_the_active_tier_without_leaking_other_tiers(tmp_path):
    """The one existing filter (company selector) must keep working
    identically inside each tier view — no reordering of the existing
    filter pipeline, per the implementation plan's own §1.5 reasoning."""
    daily_news_store.upsert_new_stories(tmp_path, [
        _story(
            id="newsitem-nvidia-high", company_name="NVIDIA", materiality_tier=NewsMaterialityTier.HIGH_SIGNAL,
            headline="NVIDIA High Signal headline",
        ),
        _story(
            id="newsitem-oracle-watch", company_name="Oracle Corporation", ticker="ORCL",
            materiality_tier=NewsMaterialityTier.WATCHLIST, headline="Oracle Watchlist headline",
        ),
    ])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.query_params["tier"] = "watchlist"
        at.run()
        at.selectbox[0].select("NVIDIA").run()

    # Selecting NVIDIA while viewing Watchlist must show neither company's
    # item (NVIDIA's own only item is High-Signal, out of this tier;
    # Oracle's Watchlist item belongs to a different company) — proving
    # the company filter and the tier filter both apply, never one
    # overriding or leaking past the other.
    assert not at.exception
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "NVIDIA High Signal headline" not in markdown_text
    assert "Oracle Watchlist headline" not in markdown_text


def test_company_filter_shows_the_matching_item_within_the_active_tier(tmp_path):
    """Positive counterpart to the no-leak test above: selecting the
    company that genuinely has a Watchlist item, while viewing
    Watchlist, must show it."""
    daily_news_store.upsert_new_stories(tmp_path, [
        _story(
            id="newsitem-nvidia-high", company_name="NVIDIA", materiality_tier=NewsMaterialityTier.HIGH_SIGNAL,
            headline="NVIDIA High Signal headline",
        ),
        _story(
            id="newsitem-oracle-watch", company_name="Oracle Corporation", ticker="ORCL",
            materiality_tier=NewsMaterialityTier.WATCHLIST, headline="Oracle Watchlist headline",
        ),
    ])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.query_params["tier"] = "watchlist"
        at.run()
        at.selectbox[0].select("Oracle Corporation").run()

    assert not at.exception
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "Oracle Watchlist headline" in markdown_text
    assert "NVIDIA High Signal headline" not in markdown_text


# ============================================================
# Dashboard/Signals quality fix (design/
# DASHBOARD_SIGNAL_QUALITY_FIX_DESIGN.md) — non-English translation
# control and cross-language localized-duplicate collapsing.
# ============================================================


def _french_story(**overrides) -> NewsStory:
    published_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    return _story(
        id="newsitem-meta-fr", company_name="Meta Platforms, Inc.", ticker="META",
        headline="Meta lance Meta One", eeva_summary=None,
        sources=(
            NewsSourceReference(
                publisher="Meta Platforms, Inc.", source_class=SourceClass.OFFICIAL_COMPANY,
                url="https://about.fb.com/fr/news/meta-one", title="Meta lance Meta One",
                published_at=published_at, retrieved_at=published_at,
                original_language="French", excerpt_original=None,
            ),
        ),
        **overrides,
    )


def test_french_item_with_no_translation_shows_explicit_language_and_translate_control(tmp_path):
    """No translation has been attempted yet — the card must show an
    explicit source-language indicator and a clearly-labeled Translate
    control, never silently render as if it were English/untranslated
    with no signal at all."""
    daily_news_store.upsert_new_stories(tmp_path, [_french_story()])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    markdown_text = " ".join(m.value for m in at.markdown)
    caption_text = " ".join(str(c.value) for c in at.caption)
    assert "Meta lance Meta One" in markdown_text  # native title, preserved
    assert "Source language: French" in caption_text
    assert "Title translation" not in markdown_text  # nothing translated yet
    translate_buttons = [b for b in at.button if b.label == "Translate"]
    assert len(translate_buttons) == 1


def test_french_item_translate_click_shows_clearly_labeled_title_translation(tmp_path):
    """Clicking Translate must show the translated headline, explicitly
    labeled as a title translation, with the native text preserved
    above it — never presented as source-original text, and never a
    generated summary/interpretation."""
    daily_news_store.upsert_new_stories(tmp_path, [_french_story()])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    translate_button = [b for b in at.button if b.label == "Translate"][0]
    with patch(
        "src.ui.pages.daily_news.translation_service.translate_cached_with_outcome"
    ) as mock_translate:
        from src.data_access.translation.translation_service import TranslationAttempt
        from src.models.models import Translation

        mock_translate.return_value = TranslationAttempt(
            translation=Translation(
                translated_text="Meta Launches Meta One", provider="DeepL",
                source_lang="fr", target_lang="en", translated_at=datetime.now(timezone.utc).isoformat(),
            ),
        )
        translate_button.click()
        with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
            at.run()

    assert not at.exception
    mock_translate.assert_called_once()
    markdown_text = " ".join(m.value for m in at.markdown)
    assert "Meta lance Meta One" in markdown_text  # native text still preserved
    assert "Title translation: Meta Launches Meta One" in markdown_text


def test_english_item_shows_no_language_indicator_or_translate_control(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story()])

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    caption_text = " ".join(str(c.value) for c in at.caption)
    assert "Source language:" not in caption_text
    assert not any(b.label == "Translate" for b in at.button)


def test_meta_english_french_pair_shows_one_preferred_canonical_card(tmp_path):
    """The Meta English/French localized-release pair, already persisted
    as two separate NewsStory records (e.g. by a completed discovery
    run that already translated and cached the French title), must
    collapse to one visible card on this page — the French alternate
    stays fully intact in the underlying store, it is simply not
    rendered here. select_canonical_stories() must read the already-
    cached translation only — it must never itself call the translation
    provider during this render (see the render-time-purity test
    below, which proves this directly at the function level)."""
    english = _story(
        id="newsitem-meta-en", company_name="Meta Platforms, Inc.", ticker="META",
        headline="Meta Launches Meta One", eeva_summary=None,
        sources=(
            NewsSourceReference(
                publisher="Meta Platforms, Inc.", source_class=SourceClass.OFFICIAL_COMPANY,
                url="https://about.fb.com/news/meta-one", title="Meta Launches Meta One",
                published_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
                retrieved_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
                original_language="English", excerpt_original=None,
            ),
        ),
    )
    french = _french_story()
    daily_news_store.upsert_new_stories(tmp_path, [english, french])

    # Simulate discovery having already translated and cached the
    # French title (the only way select_canonical_stories can ever see
    # a match — it never populates the cache itself).
    from src.data_access.translation import translation_service
    from src.models.models import Translation

    class _SeedProvider:
        name = "DeepL"

        def translate(self, text: str, source_lang: str, target_lang: str) -> str:
            return "Meta Launches Meta One"

    translation_service.translate_cached_with_outcome(
        _SeedProvider(), document_id="localization-dedup:Meta Platforms, Inc.:French",
        text="Meta lance Meta One", cache_dir=tmp_path, source_lang="FR",
    )

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        with patch(
            "src.ui.pages.daily_news.translation_service.translate_cached_with_outcome"
        ) as mock_translate_during_render:
            at = AppTest.from_file(str(_HARNESS), default_timeout=10)
            at.run()

    assert not at.exception
    mock_translate_during_render.assert_not_called()
    markdown_text = " ".join(m.value for m in at.markdown)
    assert markdown_text.count("Meta Launches Meta One") + markdown_text.count("Meta lance Meta One") == 1
    # The underlying store is untouched — both records still exist.
    assert len(daily_news_store.load_stories(tmp_path)) == 2
