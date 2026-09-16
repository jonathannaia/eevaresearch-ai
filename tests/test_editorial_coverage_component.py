"""Editorial Coverage Dashboard/Daily-News component — AppTest rendering,
fixture-driven via monkeypatching get_editorial_story_repository (the
one live-call seam), zero real network/file I/O. Reuses the existing
tests/apptest_pages/daily_news_page.py harness — the same page the
issuer-lane tests already exercise, proving both lanes render together
without interfering."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access.daily_news import daily_news_store
from src.models.daily_news_models import EditorialStory, NewsMaterialityTier
from src.ui.components import editorial_coverage
from src.ui.pages import daily_news

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_HARNESS = REPO_ROOT / "tests" / "apptest_pages" / "daily_news_page.py"


class _StubRepository:
    def __init__(self, stories: dict[str, EditorialStory]):
        self._stories = stories

    def load_stories(self) -> dict[str, EditorialStory]:
        return self._stories


def _story(
    story_id: str = "editorial-abc",
    headline: str = "Oracle Corporation reports strong AI cloud demand",
    excerpt: str | None = "Oracle Corporation said AI cloud demand drove revenue higher.",
    matched_companies: tuple[str, ...] = ("Oracle Corporation",),
    matched_themes: tuple[str, ...] = ("ai-buildout",),
    source_feed_id: str = "cnbc-technology-rss",
    publisher: str = "CNBC",
    # High Signals / Watchlist tier navigation (design/DAILY_NEWS_HIGH_
    # SIGNALS_WATCHLIST_IMPLEMENTATION_PLAN_2026_09_16.md) — High Signals
    # is now the default page view a bare AppTest run lands on (no tier
    # query param set, matching this file's own _run_daily_news_page()),
    # so this shared fixture defaults to High-Signal tier too, mirroring
    # the exact same default-fixture approach already used in
    # tests/test_daily_news_page.py's own _story()/_editorial_story().
    # Callers that need Watchlist, materiality_tier=None, or Background
    # behavior pass their own explicit override, which always wins.
    materiality_tier: NewsMaterialityTier | None = NewsMaterialityTier.HIGH_SIGNAL,
) -> EditorialStory:
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return EditorialStory(
        id=story_id, headline=headline, publisher=publisher,
        source_url="https://www.cnbc.com/2026/09/11/oracle-ai-cloud.html",
        published_at=now, retrieved_at=now, excerpt=excerpt,
        matched_companies=matched_companies, matched_themes=matched_themes,
        source_feed_id=source_feed_id, materiality_tier=materiality_tier,
    )


def _settings(tmp_path) -> Settings:
    return Settings(db_backend="json", cache_dir=tmp_path)


def _run_daily_news_page(tmp_path, monkeypatch, editorial_stories: dict[str, EditorialStory]) -> AppTest:
    monkeypatch.setattr(daily_news, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(
        editorial_coverage.daily_news_backend, "get_editorial_story_repository",
        lambda settings: _StubRepository(editorial_stories),
    )
    at = AppTest.from_file(str(DASHBOARD_HARNESS), default_timeout=20)
    at.run()
    return at


def _main_text(at: AppTest) -> str:
    return " ".join(m.value for m in at.main.get("markdown") if not m.value.startswith("<style>"))


# --- Zero-match behavior --------------------------------------------


def test_zero_visible_editorial_stories_renders_nothing(tmp_path, monkeypatch):
    at = _run_daily_news_page(tmp_path, monkeypatch, {})
    assert not at.exception
    all_text = _main_text(at)
    assert "Editorial Coverage" not in all_text


# --- Qualifying-story rendering ---------------------------------------


def test_one_story_renders_the_exact_required_anatomy(tmp_path, monkeypatch):
    at = _run_daily_news_page(tmp_path, monkeypatch, {"editorial-abc": _story()})
    assert not at.exception
    all_text = _main_text(at)

    # Unified Daily News feed (design/DECISIONS.md): no separate
    # "Editorial Coverage" section heading — a compact "Market news"
    # item-type label carries the provenance distinction per-card instead.
    assert "Editorial Coverage" not in all_text
    assert "Market news" in all_text
    assert "Oracle Corporation reports strong AI cloud demand" in all_text
    assert "CNBC" in all_text
    assert "Editorial" in all_text
    assert "Oracle Corporation said AI cloud demand drove revenue higher." in all_text
    assert "Excerpt provided by CNBC" in all_text  # source-description attribution
    assert "Oracle Corporation" in all_text  # company tag
    assert "AI Buildout" in all_text  # theme display label

    markdown_text = " ".join(m.value for m in at.markdown)
    assert "https://www.cnbc.com/2026/09/11/oracle-ai-cloud.html" in markdown_text


def test_excerpt_attribution_label_appears_immediately_above_the_excerpt(tmp_path, monkeypatch):
    at = _run_daily_news_page(tmp_path, monkeypatch, {"editorial-abc": _story()})
    all_text = _main_text(at)
    label_index = all_text.index("Excerpt provided by CNBC")
    excerpt_index = all_text.index("Oracle Corporation said AI cloud demand drove revenue higher.")
    assert label_index < excerpt_index


def test_missing_excerpt_renders_no_excerpt_text_no_fallback_sentence_and_no_attribution_label(tmp_path, monkeypatch):
    at = _run_daily_news_page(tmp_path, monkeypatch, {"editorial-abc": _story(excerpt=None)})
    all_text = _main_text(at)
    assert "Excerpt provided by" not in all_text
    assert "Oracle Corporation reports strong AI cloud demand" in all_text
    # No fallback sentence of any kind — the issuer lane's own fallback
    # text must never leak into an editorial card.
    assert "published this update through its official Investor Relations channel" not in all_text


def test_theme_only_story_renders_with_no_company_tag(tmp_path, monkeypatch):
    theme_only = _story(matched_companies=(), matched_themes=("memory",))
    at = _run_daily_news_page(tmp_path, monkeypatch, {"editorial-abc": theme_only})
    all_text = _main_text(at)
    assert "Market news" in all_text
    assert "Memory" in all_text


def test_multi_company_multi_theme_tags_all_render(tmp_path, monkeypatch):
    multi = _story(matched_companies=("Oracle Corporation", "Corning Inc."), matched_themes=("ai-buildout", "photonics"))
    at = _run_daily_news_page(tmp_path, monkeypatch, {"editorial-abc": multi})
    all_text = _main_text(at)
    assert "Oracle Corporation" in all_text
    assert "Corning Inc." in all_text
    assert "AI Buildout" in all_text
    assert "Photonics" in all_text


def test_never_shows_investment_or_why_it_matters_framing(tmp_path, monkeypatch):
    at = _run_daily_news_page(tmp_path, monkeypatch, {"editorial-abc": _story()})
    section_text = _main_text(at).lower()
    for forbidden in ("why it matters", "recommend", "forecast", "we believe", "outlook is"):
        assert forbidden not in section_text


# --- Placement and issuer-lane coexistence (unified Daily News feed,
# design/DECISIONS.md) -----------------------------------------------


def test_daily_news_heading_renders_once_before_any_editorial_card(tmp_path, monkeypatch):
    """Exactly one "Signals" heading (product-naming separation, design/
    DECISIONS.md — renamed from "Daily News") — no separate "Editorial
    Coverage" heading exists any more — and it renders before the
    editorial card content that follows it in the unified feed."""
    daily_news_store.upsert_new_stories(tmp_path, [])  # ensure cache_dir exists, issuer side stays empty
    at = _run_daily_news_page(tmp_path, monkeypatch, {"editorial-abc": _story()})
    assert not at.exception
    all_text = _main_text(at)
    assert "Editorial Coverage" not in all_text
    daily_news_title_index = all_text.index("Signals")
    headline_index = all_text.index("Oracle Corporation reports strong AI cloud demand")
    assert daily_news_title_index < headline_index


def test_editorial_only_content_suppresses_the_issuer_empty_state(tmp_path, monkeypatch):
    """Unified feed (design/DECISIONS.md): when there are zero issuer
    stories but at least one visible editorial story, the page must show
    that editorial content directly — never the issuer-specific "No
    recent company updates" empty state, which would incorrectly imply
    there is nothing to show at all."""
    at = _run_daily_news_page(tmp_path, monkeypatch, {"editorial-abc": _story()})
    all_text = _main_text(at)
    assert "No recent company updates in the last 7 days." not in all_text
    assert "Editorial Coverage" not in all_text
    assert "Oracle Corporation reports strong AI cloud demand" in all_text


# ============================================================
# Government / Public Sector Daily News lane (design/DECISIONS.md) —
# badge label derived from source_feed_id at render time.
# ============================================================


def test_spaceforce_source_renders_public_sector_badge(tmp_path, monkeypatch):
    story = _story(
        story_id="editorial-sf", headline="US Space Force selects Texas as preferred location for third DARC site",
        excerpt=None, matched_companies=(), matched_themes=(),
        source_feed_id="spaceforce-news-rss", publisher="U.S. Space Force",
    )
    at = _run_daily_news_page(tmp_path, monkeypatch, {"editorial-sf": story})
    assert not at.exception
    all_text = _main_text(at)
    assert "Public sector" in all_text
    assert "Market news" not in all_text
    assert "Policy" not in all_text


def test_nist_source_renders_policy_badge(tmp_path, monkeypatch):
    story = _story(
        story_id="editorial-nist", headline="NIST Awards Funding to Advance Domestic Semiconductor Manufacturing",
        excerpt=None, matched_companies=(), matched_themes=(),
        source_feed_id="nist-news-rss", publisher="National Institute of Standards and Technology (NIST)",
    )
    at = _run_daily_news_page(tmp_path, monkeypatch, {"editorial-nist": story})
    assert not at.exception
    all_text = _main_text(at)
    assert "Policy" in all_text
    assert "Market news" not in all_text
    assert "Public sector" not in all_text


def test_existing_cnbc_source_still_renders_market_news_badge(tmp_path, monkeypatch):
    at = _run_daily_news_page(tmp_path, monkeypatch, {"editorial-abc": _story()})
    assert not at.exception
    all_text = _main_text(at)
    assert "Market news" in all_text
    assert "Public sector" not in all_text
    assert "Policy" not in all_text


def test_unknown_source_feed_id_defaults_to_market_news_badge(tmp_path, monkeypatch):
    story = _story(story_id="editorial-unknown", source_feed_id="some-future-source-rss")
    at = _run_daily_news_page(tmp_path, monkeypatch, {"editorial-unknown": story})
    assert not at.exception
    assert "Market news" in _main_text(at)


def test_all_three_badges_render_distinctly_in_one_mixed_feed(tmp_path, monkeypatch):
    cnbc_story = _story(story_id="editorial-cnbc")
    spaceforce_story = _story(
        story_id="editorial-sf", headline="US Space Force selects Texas as preferred location for third DARC site",
        excerpt=None, matched_companies=(), matched_themes=(), source_feed_id="spaceforce-news-rss",
        publisher="U.S. Space Force",
    )
    nist_story = _story(
        story_id="editorial-nist", headline="NIST Awards Funding to Advance Domestic Semiconductor Manufacturing",
        excerpt=None, matched_companies=(), matched_themes=(), source_feed_id="nist-news-rss",
        publisher="National Institute of Standards and Technology (NIST)",
    )
    at = _run_daily_news_page(
        tmp_path, monkeypatch,
        {"editorial-cnbc": cnbc_story, "editorial-sf": spaceforce_story, "editorial-nist": nist_story},
    )
    assert not at.exception
    all_text = _main_text(at)
    assert "Market news" in all_text
    assert "Public sector" in all_text
    assert "Policy" in all_text
