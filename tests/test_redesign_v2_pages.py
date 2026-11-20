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
from src.logic import formatting
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


def _run_signals(monkeypatch, stories, tmp_path, now: datetime | None = None) -> AppTest:
    """`now` freezes the two clock routes that drive date grouping and
    the issuer freshness window -- not every clock read on the page.

    Those two routes both have to be frozen to the same instant or the
    page sees an incoherent "now": formatting.today_local() decides the
    Today/Yesterday labels, and _recent_stories() applies the issuer
    lane's 7-day freshness window. Freezing only the first leaves the
    window live, which drops every fixed-date fixture as months-old and
    yields zero groups.

    Deliberately NOT frozen: unrelated UI chrome. with_chrome's own
    topbar calls today_local() for its date caption
    (src/ui/ui.py::_render_topbar), so that caption still follows the
    real wall clock during these tests. It is left alone because it is
    not what these tests are about, and it cannot reach any assertion
    here -- it renders class="er-topbar-date", which the
    class="er-date-group" filter excludes, and its date format can
    never contain the literals "Today", "Yesterday" or "Headline".

    Both patches delegate to the real production function with `now`
    passed through the injectable parameter each already exposes for
    exactly this purpose, so no production logic is bypassed or
    reimplemented — the genuine UTC -> Eastern conversion and the
    genuine window comparison both still run, against a fixed instant.
    The story side is untouched either way: local_date() keeps deriving
    each item's own Eastern date from its own real timestamp. Omitted
    (the default), no patch is installed at all, so every other test
    here behaves exactly as before."""
    if now is not None:
        real_recent_stories = daily_news._recent_stories
        monkeypatch.setattr(daily_news, "today_local", lambda: formatting.today_local(now=now))
        monkeypatch.setattr(
            daily_news, "_recent_stories",
            lambda stories, now=None, _frozen=now, _real=real_recent_stories: _real(stories, now=_frozen),
        )
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


# Fixed reference instants for the date-group tests. All three are on
# 2026-03-17, which is EDT (UTC-4), so the UTC-to-Eastern shift is a
# plain -4h and each expected label can be read straight off the clock.
#
# These tests used to derive their timestamps from datetime.now() with
# relative offsets ("one hour ago"), which encoded a false assumption:
# that one hour ago is always the same Eastern calendar day. It is not,
# for the first hour of every Eastern day -- between 00:00 and 00:59
# Eastern, one hour ago is the *previous* Eastern date, so the page
# correctly labelled the story "Yesterday" and the test's "Today"
# assertion failed. That was a defect in the fixture, not in the page:
# grouping a story published at 23:30 Eastern under the 16th while the
# clock reads 00:30 on the 17th is the honest answer, and is exactly
# what _render_grouped's docstring promises. The boundary is now pinned
# by a test instead of being avoided.
_NOW_JUST_AFTER_EASTERN_MIDNIGHT = datetime(2026, 3, 17, 4, 30, tzinfo=timezone.utc)   # 00:30 EDT
_NOW_MID_MORNING_EASTERN = datetime(2026, 3, 17, 14, 0, tzinfo=timezone.utc)           # 10:00 EDT
# 22:00 EDT on Mar 16 -- the UTC date (Mar 17) and the Eastern date
# (Mar 16) differ at this instant. The two constants above cannot tell
# the two timezones apart on the render-time side, because at 04:30Z and
# 14:00Z the UTC and Eastern dates coincide; only an evening-Eastern
# instant separates them.
_NOW_LATE_EVENING_EASTERN = datetime(2026, 3, 17, 2, 0, tzinfo=timezone.utc)           # 22:00 EDT Mar 16


def test_signals_groups_items_by_the_real_eastern_publication_date(monkeypatch, tmp_path):
    """The original assertion, now deterministic: render-time instant is
    fixed well clear of the midnight boundary (10:00 Eastern) and both
    story timestamps are explicit absolute instants rather than offsets
    from the wall clock."""
    today_story = _story("s-today", datetime(2026, 3, 17, 13, 0, tzinfo=timezone.utc))   # 09:00 EDT, same Eastern day
    older_story = _story("s-older", datetime(2026, 3, 14, 14, 0, tzinfo=timezone.utc))   # 10:00 EDT, Mar 14
    at = _run_signals(monkeypatch, [today_story, older_story], tmp_path, now=_NOW_MID_MORNING_EASTERN)
    groups = [m.value for m in at.main.get("markdown") if 'class="er-date-group"' in m.value]
    assert len(groups) == 2
    assert "Today" in groups[0] and "Today" not in groups[1]
    assert "Tue, Mar 17" in groups[0]
    assert "Sat, Mar 14" in groups[1]
    text = _text(at)
    assert text.index("Headline s-today") < text.index("Headline s-older")


def test_signals_groups_a_prior_eastern_day_story_as_yesterday_just_after_eastern_midnight(monkeypatch, tmp_path):
    """The boundary case the old fixture tripped over, asserted rather
    than avoided. Render time is 00:30 Eastern. A story published 23:30
    Eastern the previous day must group under "Yesterday", and one
    published 00:15 Eastern the same day under "Today" -- so the first
    hour of an Eastern day is handled by calendar date, not by elapsed
    time."""
    prior_day = _story("s-prior", datetime(2026, 3, 17, 3, 30, tzinfo=timezone.utc))   # 23:30 EDT Mar 16
    after_midnight = _story("s-after", datetime(2026, 3, 17, 4, 15, tzinfo=timezone.utc))  # 00:15 EDT Mar 17
    at = _run_signals(
        monkeypatch, [after_midnight, prior_day], tmp_path, now=_NOW_JUST_AFTER_EASTERN_MIDNIGHT,
    )
    groups = [m.value for m in at.main.get("markdown") if 'class="er-date-group"' in m.value]

    assert len(groups) == 2
    assert "Today · Tue, Mar 17" in groups[0]
    assert "Yesterday · Mon, Mar 16" in groups[1]
    text = _text(at)
    assert text.index("Headline s-after") < text.index("Headline s-prior")


def test_signals_date_groups_use_the_eastern_date_not_the_utc_date(monkeypatch, tmp_path):
    """Both stories carry the same *UTC* date (2026-03-17) but fall on
    different *Eastern* dates, so a UTC-keyed grouping would emit one
    divider and an Eastern-keyed one emits two. This is the distinction
    itself, not a restatement of the boundary case: it fails if the
    grouping key ever reverts to a UTC-date truncation."""
    prior_day = _story("s-prior", datetime(2026, 3, 17, 3, 30, tzinfo=timezone.utc))
    after_midnight = _story("s-after", datetime(2026, 3, 17, 4, 15, tzinfo=timezone.utc))
    assert prior_day.sources[0].published_at[:10] == after_midnight.sources[0].published_at[:10] == "2026-03-17"

    at = _run_signals(
        monkeypatch, [after_midnight, prior_day], tmp_path, now=_NOW_JUST_AFTER_EASTERN_MIDNIGHT,
    )
    groups = [m.value for m in at.main.get("markdown") if 'class="er-date-group"' in m.value]

    assert len(groups) == 2, "one shared UTC date must still split into two Eastern dates"
    assert "Mar 17" in groups[0] and "Mar 16" in groups[1]


def test_signals_today_label_uses_the_eastern_render_date_not_the_utc_render_date(monkeypatch, tmp_path):
    """Covers the render-time half of the Eastern/UTC distinction, which
    the other date tests cannot reach.

    Render time is 22:00 EDT on Mar 16, an instant whose UTC date is
    already Mar 17. Eastern "today" is therefore Mar 16, so a story
    published that Eastern day must read "Today · Mon, Mar 16". Were
    today_local() to truncate in UTC instead, today would be Mar 17 and
    the same story would be mislabelled "Yesterday · Mon, Mar 16" --
    so this test, unlike the ones above, fails under that mutation."""
    same_eastern_day = _story("s-evening", datetime(2026, 3, 16, 18, 0, tzinfo=timezone.utc))  # 14:00 EDT Mar 16
    at = _run_signals(monkeypatch, [same_eastern_day], tmp_path, now=_NOW_LATE_EVENING_EASTERN)
    groups = [m.value for m in at.main.get("markdown") if 'class="er-date-group"' in m.value]

    assert len(groups) == 1
    assert "Today · Mon, Mar 16" in groups[0]
    assert "Yesterday" not in groups[0]


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
