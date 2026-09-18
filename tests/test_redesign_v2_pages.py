"""Redesign v2 — Signals and Research Theses page behaviour that is new
to this pass: real-timestamp date grouping, no non-functional escalation
controls, the real-count category filter, all four evidence directions
always rendered (Contradicts never collapsed), tracked-company codes
beside mapped companies, and "New" only when the authored status is
NEW. Through the same AppTest harnesses the existing page suites use;
every repository is a monkeypatched in-memory fake."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.config.tracked_companies import TrackedCompany
from src.models.daily_news_models import (
    EditorialStory,
    NewsMaterialityTier,
    NewsSourceReference,
    NewsStory,
    NewsStoryStatus,
    SourceClass,
)
from src.models.theme_research import (
    CompanyRole,
    EvidenceDirection,
    ResearchTheme,
    ThemeCategory,
    ThemeCompanyMapEntry,
    ThemeEvidenceItem,
    ThemeStatus,
    ThemeVisibility,
)
from src.ui.pages import daily_news, themes_research

_SIGNALS = Path(__file__).parent / "apptest_pages" / "daily_news_page.py"
_THESES = Path(__file__).parent / "apptest_pages" / "themes_research_page.py"


def _text(at: AppTest) -> str:
    return " ".join(m.value for m in at.main.get("markdown"))


# --- Signals ------------------------------------------------------------------

def _story(story_id: str, published_at: datetime, company: str = "NVIDIA") -> NewsStory:
    return NewsStory(
        id=story_id, company_name=company, ticker="NVDA", theme_slug="ai-buildout", headline=f"Headline {story_id}",
        eeva_summary=f"Summary for {story_id}.", is_fallback_summary=False, translation_unavailable=False,
        original_title=None, status=NewsStoryStatus.PUBLISHED, materiality_tier=NewsMaterialityTier.HIGH_SIGNAL,
        sources=(NewsSourceReference(
            publisher="NVIDIA Newsroom", source_class=SourceClass.OFFICIAL_COMPANY, url=f"https://example.com/{story_id}",
            title=f"Headline {story_id}", published_at=published_at.isoformat(), retrieved_at=published_at.isoformat(),
            original_language="English",
        ),),
    )


class _NewsRepo:
    def __init__(self, stories):
        self._stories = {s.id: s for s in stories}

    def load_stories(self):
        return dict(self._stories)


def _run_signals(monkeypatch, stories, tmp_path) -> AppTest:
    monkeypatch.setattr(daily_news.daily_news_backend, "get_daily_news_repository", lambda settings: _NewsRepo(stories))
    monkeypatch.setattr(daily_news, "get_visible_editorial_stories", lambda settings: ())
    monkeypatch.setattr(daily_news, "get_editorial_stories_for_company", lambda settings, name: ())
    monkeypatch.setattr(daily_news, "_company_options", lambda: ["All companies", "NVIDIA"])
    daily_news._high_signal_count_cached.clear()
    with patch("src.ui.pages.daily_news.get_settings", return_value=Settings(cache_dir=tmp_path)), \
         patch("src.ui.ui._nav_badge_counts", return_value={}), \
         patch("src.ui.ui._latest_filings_refresh_label", return_value=None):
        at = AppTest.from_file(str(_SIGNALS), default_timeout=15)
        at.run()
    assert not at.exception, at.exception
    return at


def test_signals_groups_items_by_the_real_eastern_publication_date(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    today_story = _story("s-today", now - timedelta(hours=1))
    older_story = _story("s-older", now - timedelta(days=3))
    at = _run_signals(monkeypatch, [today_story, older_story], tmp_path)
    groups = [m.value for m in at.main.get("markdown") if 'class="er-date-group"' in m.value]
    assert len(groups) == 2
    assert "Today" in groups[0] and "Today" not in groups[1]
    text = _text(at)
    assert text.index("Headline s-today") < text.index("Headline s-older")


def test_signals_renders_no_non_functional_escalation_controls(monkeypatch, tmp_path):
    at = _run_signals(monkeypatch, [_story("s-1", datetime.now(timezone.utc) - timedelta(hours=2))], tmp_path)
    labels = {b.label for b in at.button} | {pl.label for pl in at.main.get("page_link")}
    for forbidden in ("Not relevant", "Add to thesis", "Create signal"):
        assert not any(label.startswith(forbidden) for label in labels), forbidden
    text = _text(at)
    assert "Read original source →" in text and "https://example.com/s-1" in text


def test_signals_count_line_reflects_the_real_high_signal_count(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    at = _run_signals(monkeypatch, [_story("a", now - timedelta(hours=1)), _story("b", now - timedelta(hours=5))], tmp_path)
    assert "2 high signals · tracked coverage" in _text(at)


# --- Research theses ---------------------------------------------------------------

def _theme(theme_id: str, title: str, category: ThemeCategory, status: ThemeStatus, updated_at: str) -> ResearchTheme:
    return ResearchTheme(
        id=theme_id, category=category, status=status, visibility=ThemeVisibility.PUBLISHED, title=title,
        key_question="Are buybacks displacing capacity investment?", hypothesis="h", working_thesis="Working thesis text.",
        why_it_matters="w", what_could_change_the_view="c", what_to_watch_next="n",
        created_at="2026-08-01T00:00:00+00:00", updated_at=updated_at,
    )


def _evidence(theme_id: str, n: int, direction: EvidenceDirection, company: str = "Tokyo Electron"):
    return tuple(
        ThemeEvidenceItem(
            id=f"{theme_id}-{direction.value}-{i}", theme_id=theme_id, date="2026-09-01", company=company,
            source_name="EDINET", source_url="https://disclosure2.edinet-fsa.go.jp/", fact=f"fact {i}", relevance="r",
            direction=direction,
        )
        for i in range(n)
    )


class _ThemeRepo:
    def __init__(self, themes, evidence, company_map):
        self._themes, self._evidence, self._company_map = themes, evidence, company_map

    def list_published_themes(self):
        return list(self._themes)

    def get_published_theme(self, theme_id):
        return next((t for t in self._themes if t.id == theme_id), None)

    def evidence_for_theme(self, theme_id):
        return self._evidence.get(theme_id, ())

    def company_map_for_theme(self, theme_id):
        return self._company_map.get(theme_id, ())


def _run_theses(monkeypatch, repo, tracked=()) -> AppTest:
    monkeypatch.setattr(themes_research.backend_factory, "get_theme_repository", lambda settings: repo)
    monkeypatch.setattr(themes_research, "get_tracked_companies", lambda: tuple(tracked))
    with patch("src.ui.ui._nav_badge_counts", return_value={}), \
         patch("src.ui.ui._latest_filings_refresh_label", return_value=None), \
         patch("src.ui.ui._has_published_themes", return_value=True):
        at = AppTest.from_file(str(_THESES), default_timeout=15)
        at.run()
    assert not at.exception, at.exception
    return at


def test_theses_card_renders_all_four_directions_from_real_counts_including_contradicts(monkeypatch):
    theme = _theme("t-1", "Japan buybacks alongside capacity investment", ThemeCategory.SECOND_ORDER_EFFECT, ThemeStatus.MONITORING, "2026-09-14T04:08:00+00:00")
    evidence = _evidence("t-1", 12, EvidenceDirection.SUPPORTS) + _evidence("t-1", 1, EvidenceDirection.MIXED) \
        + _evidence("t-1", 2, EvidenceDirection.CONTRADICTS) + _evidence("t-1", 5, EvidenceDirection.CONTEXT)
    at = _run_theses(monkeypatch, _ThemeRepo([theme], {"t-1": evidence}, {"t-1": ()}))
    card = next(m.value for m in at.main.get("markdown") if 'class="er-evbar"' in m.value)
    for label, n in (("Supports", 12), ("Mixed", 1), ("Contradicts", 2), ("Context", 5)):
        assert f'{label} <span class="er-mono">{n}</span>' in card, label
    assert card.count("er-evbar-seg") == 4
    assert "20 items" in card
    assert "3 items need review (mixed or contradicting)" in _text(at)


def test_theses_new_badge_appears_only_for_an_authored_new_status(monkeypatch):
    new_theme = _theme("t-new", "A new thesis", ThemeCategory.BOTTLENECK, ThemeStatus.NEW, "2026-09-10T00:00:00+00:00")
    monitoring = _theme("t-mon", "A monitored thesis", ThemeCategory.BOTTLENECK, ThemeStatus.MONITORING, "2026-09-12T00:00:00+00:00")
    at = _run_theses(monkeypatch, _ThemeRepo([new_theme, monitoring], {}, {}))
    cards = [m.value for m in at.main.get("markdown") if 'class="er-split-meta">Updated' in m.value]
    assert len(cards) == 2
    # Recently updated first: the MONITORING thesis (Sep 12) precedes the NEW one (Sep 10).
    assert "Monitoring" in cards[0] and ">New<" not in cards[0]
    assert ">New<" in cards[1]


def test_theses_category_filter_shows_real_counts_and_narrows_the_list(monkeypatch):
    bottleneck = _theme("t-b", "Bottleneck thesis", ThemeCategory.BOTTLENECK, ThemeStatus.MONITORING, "2026-09-10T00:00:00+00:00")
    second = _theme("t-s", "Second-order thesis", ThemeCategory.SECOND_ORDER_EFFECT, ThemeStatus.UPDATED, "2026-09-11T00:00:00+00:00")
    at = _run_theses(monkeypatch, _ThemeRepo([bottleneck, second], {}, {}))
    control = at.segmented_control[0]
    assert list(control.options) == ["All 2", "Bottleneck 1", "Demand shift", "Second-order effect 1"]
    assert "Bottleneck thesis" in _text(at) and "Second-order thesis" in _text(at)
    control.set_value("Bottleneck 1").run()
    assert not at.exception
    assert "Bottleneck thesis" in _text(at) and "Second-order thesis" not in _text(at)


def test_theses_company_chips_carry_real_tracked_company_codes_only(monkeypatch):
    theme = _theme("t-c", "Codes thesis", ThemeCategory.DEMAND_SHIFT, ThemeStatus.MONITORING, "2026-09-10T00:00:00+00:00")
    company_map = (
        ThemeCompanyMapEntry(id="m1", theme_id="t-c", company_name="Tokyo Electron", role=CompanyRole.ENABLER),
        ThemeCompanyMapEntry(id="m2", theme_id="t-c", company_name="Untracked Co", role=CompanyRole.EXPOSED),
    )
    tracked = [TrackedCompany(name="Tokyo Electron", exchange="TSE", krx_code="80350", source="EDINET", themes=("memory",))]
    at = _run_theses(monkeypatch, _ThemeRepo([theme], {}, {"t-c": company_map}), tracked)
    text = _text(at)
    assert 'Tokyo Electron <span class="er-mono er-mono-muted">8035</span>' in text
    assert "Untracked Co</span>" in text and "Untracked Co <span" not in text


def test_theses_page_renders_no_new_thesis_or_add_to_thesis_controls(monkeypatch):
    theme = _theme("t-x", "Only thesis", ThemeCategory.BOTTLENECK, ThemeStatus.MONITORING, "2026-09-10T00:00:00+00:00")
    at = _run_theses(monkeypatch, _ThemeRepo([theme], {}, {}))
    labels = {b.label for b in at.button} | {pl.label for pl in at.main.get("page_link")}
    assert not any(label.startswith(("New thesis", "Add to thesis")) for label in labels)
    # The harness registers no pages, so the Open link cannot render here;
    # its label/target is pinned by source text in test_themes_research_page.
    assert "Only thesis" in _text(at)
