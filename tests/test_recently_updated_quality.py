"""Dashboard "Recently Updated" — Signals quality pass (2026-09-18).

Pins the three prominent-result fixes against real items observed in the
local issuer-lane cache: a Background issuer story never reaches the
preview, a legacy (untiered) story is tiered at read time and never
written back, and one joint announcement published by several issuers
renders as one row with combined issuer labels."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.config.settings import Settings
from src.data_access.daily_news import daily_news_store
from src.models.daily_news_models import (
    NewsMaterialityTier, NewsSourceReference, NewsStateTransition, NewsStory, NewsStoryStatus, SourceClass,
)
from src.ui.components import recently_updated


def _settings(tmp_path) -> Settings:
    return Settings(db_backend="json", cache_dir=tmp_path)


def _story(
    story_id: str, company_name: str, ticker: str, headline: str, published_at: datetime,
    tier: NewsMaterialityTier | None, url: str | None = None, excerpt: str | None = None,
) -> NewsStory:
    at = published_at.isoformat()
    return NewsStory(
        id=story_id, company_name=company_name, ticker=ticker, theme_slug="ai-buildout",
        headline=headline, eeva_summary=None, is_fallback_summary=False,
        translation_unavailable=False, original_title=None,
        sources=(
            NewsSourceReference(
                publisher=company_name, source_class=SourceClass.OFFICIAL_COMPANY,
                url=url or f"https://newsroom.example.com/{story_id}", title=headline,
                published_at=at, retrieved_at=at, original_language="English", excerpt_original=excerpt,
            ),
        ),
        status=NewsStoryStatus.PUBLISHED,
        state_history=[NewsStateTransition(status=NewsStoryStatus.PUBLISHED, at=at)],
        materiality_tier=tier,
    )


def _titles(tmp_path, now):
    return [row.title for row in recently_updated._select_recently_updated_rows(_settings(tmp_path), now)]


def test_background_issuer_story_never_reaches_the_dashboard_preview(tmp_path):
    now = datetime.now(timezone.utc)
    daily_news_store.upsert_new_stories(tmp_path, [
        _story("bg", "NVIDIA", "NVDA", "Sparks Fly: NVIDIA Accelerates Local AI at IFA 2026", now, NewsMaterialityTier.BACKGROUND),
        _story("hs", "NVIDIA", "NVDA", "NVIDIA Announces Financial Results for Second Quarter Fiscal 2027",
               now - timedelta(hours=2), NewsMaterialityTier.HIGH_SIGNAL),
        _story("wl", "Quanta Services, Inc.", "PWR", "Quanta Services Announces Quarterly Cash Dividend",
               now - timedelta(hours=3), NewsMaterialityTier.WATCHLIST),
    ])

    assert _titles(tmp_path, now) == [
        "NVIDIA Announces Financial Results for Second Quarter Fiscal 2027",
        "Quanta Services Announces Quarterly Cash Dividend",
    ]


def test_legacy_untiered_stories_are_tiered_at_read_time_and_never_written_back(tmp_path):
    now = datetime.now(timezone.utc)
    daily_news_store.upsert_new_stories(tmp_path, [
        _story("gaming", "NVIDIA", "NVDA", "‘NBA 2K27’ With NVIDIA DLSS 5 Leads 26 New Games Coming to GeForce NOW", now, None),
        _story("blog", "SK Hynix", "000660", "[AI Ecosystem] The real bottleneck: Data, not compute", now - timedelta(minutes=5), None),
        _story("event", "Rockwell Automation", "ROK", "Rockwell Automation to Present at Morgan Stanley 14th Annual Laguna Conference",
               now - timedelta(minutes=10), None),
        _story("results", "Marvell Technology, Inc.", "MRVL",
               "Marvell Technology, Inc. Reports Second Quarter of Fiscal Year 2027 Financial Results", now - timedelta(hours=1), None),
    ])

    assert _titles(tmp_path, now) == ["Marvell Technology, Inc. Reports Second Quarter of Fiscal Year 2027 Financial Results"]
    stored = daily_news_store.load_stories(tmp_path)
    assert {story_id: s.materiality_tier for story_id, s in stored.items()} == {
        "gaming": None, "blog": None, "event": None, "results": None,
    }


def test_one_joint_announcement_from_two_issuers_renders_as_one_row(tmp_path):
    now = datetime.now(timezone.utc)
    headline = "AMD, Cisco and HUMAIN Expand Saudi Arabia’s AI Infrastructure as AMD Instinct Systems Go Live"
    daily_news_store.upsert_new_stories(tmp_path, [
        _story("amd", "Advanced Micro Devices", "AMD", headline, now, NewsMaterialityTier.WATCHLIST,
               url="https://ir.amd.com/news-events/press-releases/detail/1298/amd-cisco-and-humain"),
        _story("cisco", "Cisco Systems, Inc.", "CSCO", headline, now, NewsMaterialityTier.WATCHLIST,
               url="https://newsroom.cisco.com/c/r/newsroom/en/us/a/y2026/m08/amd-cisco-and-humain.html"),
        _story("other", "NVIDIA", "NVDA", "NVIDIA and MediaTek Deepen Long-Standing Partnership to Build AI Edge to Cloud Computing Platforms",
               now - timedelta(hours=1), NewsMaterialityTier.WATCHLIST),
    ])

    rows = recently_updated._select_recently_updated_rows(_settings(tmp_path), now)

    assert [row.title for row in rows] == [
        headline, "NVIDIA and MediaTek Deepen Long-Standing Partnership to Build AI Edge to Cloud Computing Platforms",
    ]
    # Each issuer label is kept whole — "Cisco Systems, Inc." is never
    # split on its own comma; order follows the display sort's tie-break.
    assert rows[0].company_name in (
        "Cisco Systems, Inc., Advanced Micro Devices", "Advanced Micro Devices, Cisco Systems, Inc.",
    )
    assert rows[1].company_name == "NVIDIA"


def test_different_headlines_from_different_issuers_are_never_merged(tmp_path):
    now = datetime.now(timezone.utc)
    daily_news_store.upsert_new_stories(tmp_path, [
        _story("a", "NVIDIA", "NVDA", "AWS and NVIDIA to Deliver 2 Million Additional GPUs and Next-Generation Infrastructure", now,
               NewsMaterialityTier.HIGH_SIGNAL),
        _story("b", "Cisco Systems, Inc.", "CSCO", "Cisco Reports Fourth Quarter Earnings", now - timedelta(minutes=1),
               NewsMaterialityTier.HIGH_SIGNAL),
    ])

    rows = recently_updated._select_recently_updated_rows(_settings(tmp_path), now)

    assert [(row.company_name, row.title) for row in rows] == [
        ("NVIDIA", "AWS and NVIDIA to Deliver 2 Million Additional GPUs and Next-Generation Infrastructure"),
        ("Cisco Systems, Inc.", "Cisco Reports Fourth Quarter Earnings"),
    ]
