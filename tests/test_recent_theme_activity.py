"""Recent Theme Activity — Dashboard replacement for the Market Map tile
grid (design/DECISIONS.md). Two layers of coverage:

  1. Pure logic (src.logic.recent_theme_activity.build_recent_theme_activity)
     — window qualification, theme_slug grouping, the exact three-level
     ordering rule, the 5-row bound, and fail-closed unresolvable-theme
     handling. Fast, deterministic, `now` injected explicitly.
  2. Rendering (src.ui.components.recent_theme_activity, via the same
     auth-bypassing Dashboard AppTest harness every other Dashboard test
     in this suite already uses) — real per-row content, the conditional
     "View ->" link, the single component-level "Browse all themes ->"
     link (corrective pass, design/DECISIONS.md — a per-row "Explore
     <theme> in Themes ->" link was removed: every taxonomy theme_slug
     row led to the identical unfiltered Themes index, since no
     taxonomy-slug-aware route exists there), the empty state, and
     confirmation the Dashboard no longer imports or renders the deleted
     Market Map component.

No network call, worker, scheduler, or database migration is exercised
by any test here — every fixture is written through the same real
repositories the app itself uses (backend_factory.
get_filing_event_repository's JSON-file format, daily_news_backend.
get_daily_news_repository().upsert_new_stories()), the same discipline
tests/test_dashboard_data_integrity.py already documents for itself."""
from __future__ import annotations

import json
import tempfile
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access.container import get_repositories
from src.data_access.daily_news import daily_news_backend
from src.logic.recent_theme_activity import ThemeActivityItem, build_recent_theme_activity
from src.models.daily_news_models import NewsSourceReference, NewsStateTransition, NewsStory, NewsStoryStatus, SourceClass
from src.models.models import FilingEvent

REPO_ROOT = Path(__file__).parent.parent
HARNESS_DIR = Path(__file__).parent / "apptest_pages"
DASHBOARD_HARNESS = HARNESS_DIR / "dashboard_page.py"
_NEW_FILES = (
    REPO_ROOT / "src" / "logic" / "recent_theme_activity.py",
    REPO_ROOT / "src" / "ui" / "components" / "recent_theme_activity.py",
)


def _now_iso(offset: timedelta = timedelta()) -> str:
    return (datetime.now(timezone.utc) - offset).isoformat()


# ============================== PURE LOGIC ==============================


def _item(theme_slug: str, company: str, days_ago: int, *, item_type: str = "Filing", source_url: str | None = "https://x/1") -> ThemeActivityItem:
    now = datetime(2026, 9, 11, tzinfo=timezone.utc)
    return ThemeActivityItem(
        theme_slug=theme_slug, company_name=company, item_type=item_type,
        timestamp=now - timedelta(days=days_ago), display_date=f"{days_ago}d ago", source_url=source_url,
    )


_NOW = datetime(2026, 9, 11, tzinfo=timezone.utc)
_NAMES = {"ai-buildout": "AI Buildout", "space": "Space", "memory": "Memory"}


def test_only_items_within_the_window_qualify():
    items = [_item("ai-buildout", "NVIDIA", 1), _item("ai-buildout", "Intel", 20)]  # 20 days ago: outside the 14-day window
    rows = build_recent_theme_activity(items, _NAMES, now=_NOW, window_days=14)
    assert len(rows) == 1
    assert rows[0].count == 1
    assert rows[0].most_recent.company_name == "NVIDIA"


def test_items_are_grouped_by_theme_slug():
    items = [_item("ai-buildout", "NVIDIA", 1), _item("ai-buildout", "AMD", 3), _item("space", "Rocket Lab", 2)]
    rows = build_recent_theme_activity(items, _NAMES, now=_NOW, window_days=14)
    by_slug = {r.theme_slug: r for r in rows}
    assert by_slug["ai-buildout"].count == 2
    assert by_slug["space"].count == 1


def test_ordering_is_recency_then_count_then_name():
    # Space's most-recent item is more recent than AI Buildout's -> Space first.
    items = [_item("ai-buildout", "NVIDIA", 5), _item("space", "Rocket Lab", 1)]
    rows = build_recent_theme_activity(items, _NAMES, now=_NOW, window_days=14)
    assert [r.theme_slug for r in rows] == ["space", "ai-buildout"]


def test_ordering_tiebreak_by_count_when_timestamps_tie():
    same_moment = 2
    items = [
        _item("ai-buildout", "NVIDIA", same_moment), _item("ai-buildout", "AMD", 6),  # ai-buildout: 2 items, most-recent tied
        _item("space", "Rocket Lab", same_moment),  # space: 1 item, most-recent tied with ai-buildout's
    ]
    rows = build_recent_theme_activity(items, _NAMES, now=_NOW, window_days=14)
    assert [r.theme_slug for r in rows] == ["ai-buildout", "space"]  # higher count wins the timestamp tie


def test_ordering_tiebreak_alphabetical_when_timestamp_and_count_both_tie():
    items = [_item("space", "Rocket Lab", 2), _item("memory", "SK Hynix", 2)]
    rows = build_recent_theme_activity(items, _NAMES, now=_NOW, window_days=14)
    assert [r.theme_slug for r in rows] == ["memory", "space"]  # "Memory" < "Space" alphabetically


def test_bounded_to_at_most_five_rows():
    names = {f"theme-{i}": f"Theme {i}" for i in range(8)}
    items = [_item(f"theme-{i}", f"Company {i}", i) for i in range(8)]  # 8 distinct themes, all within the window
    rows = build_recent_theme_activity(items, names, now=_NOW, window_days=14, max_rows=5)
    assert len(rows) == 5


def test_unresolvable_theme_slug_is_omitted_not_fabricated():
    items = [_item("ai-buildout", "NVIDIA", 1), _item("unregistered-theme", "Mystery Corp", 1)]
    rows = build_recent_theme_activity(items, _NAMES, now=_NOW, window_days=14)
    assert [r.theme_slug for r in rows] == ["ai-buildout"]
    assert not any(r.theme_slug == "unregistered-theme" for r in rows)


def test_empty_input_yields_empty_output():
    assert build_recent_theme_activity([], _NAMES, now=_NOW) == []


def test_no_qualifying_items_within_window_yields_empty_output():
    items = [_item("ai-buildout", "NVIDIA", 30)]
    assert build_recent_theme_activity(items, _NAMES, now=_NOW, window_days=14) == []


# ============================== NO NEW INTEGRATIONS ==============================


def test_recent_theme_activity_introduces_no_quote_provider_network_or_secret_dependency():
    forbidden_substrings = (
        "yfinance", "alpha_vantage", "alphavantage", "polygon.io", "iexcloud", "finnhub",
        "marketstack", "twelvedata", "tiingo", "requests.get(", "requests.post(", "httpx.",
        "urllib.request", "boto3", "threading.Thread", "subprocess", "multiprocessing",
        "EDGE_RADAR_LIVE_SCAN_ENABLED", "os.environ",
    )
    for path in _NEW_FILES:
        source = path.read_text(encoding="utf-8")
        for forbidden in forbidden_substrings:
            assert forbidden not in source, f"{path.name} unexpectedly contains {forbidden!r}"


def test_recent_theme_activity_files_compute_no_price_or_return_values():
    for path in _NEW_FILES:
        source = path.read_text(encoding="utf-8")
        assert "fmt_pct(" not in source
        assert "fmt_currency(" not in source


def test_recent_theme_activity_never_uses_priority_briefing_insight_heatmap_or_performance_language():
    source = (REPO_ROOT / "src" / "ui" / "components" / "recent_theme_activity.py").read_text(encoding="utf-8")
    # Check only the literal user-facing strings, not the module's own
    # explanatory docstring/comments (which legitimately discuss, by
    # name, the market map/heatmap language this component must avoid).
    literal_strings = [line for line in source.splitlines() if "st.markdown(" in line or "st.link_button(" in line or "st.page_link(" in line]
    joined = " ".join(literal_strings).lower()
    for forbidden in ("priority queue", "briefing", "insight", "market map", "heatmap", "performance", "momentum", "top mover"):
        assert forbidden not in joined


# ============================== RENDERING ==============================


def _settings(tmp_path) -> Settings:
    return Settings(db_backend="json", cache_dir=tmp_path)


def _seed_filing_event(cache_dir, filing: FilingEvent, filename: str) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {"seen_receipt_numbers": [filing.rcept_no], "filing_events": [asdict(filing)], "candidate_signals": []}
    (cache_dir / filename).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _news_story(company_name: str, theme_slug: str, headline: str, published_at: str, source_url: str) -> NewsStory:
    return NewsStory(
        id=f"newsitem-{company_name.lower()}-abc123", company_name=company_name, ticker="TCK", theme_slug=theme_slug,
        headline=headline, eeva_summary="Summary text.", is_fallback_summary=False, translation_unavailable=False,
        original_title=None,
        sources=(
            NewsSourceReference(
                publisher=company_name, source_class=SourceClass.OFFICIAL_COMPANY, url=source_url,
                title=headline, published_at=published_at, retrieved_at=published_at,
                original_language="English", excerpt_original="Summary text.",
            ),
        ),
        status=NewsStoryStatus.PUBLISHED,
        state_history=[NewsStateTransition(status=NewsStoryStatus.PUBLISHED, at=published_at)],
    )


def _run_dashboard(settings: Settings) -> AppTest:
    with patch("src.ui.pages.dashboard.get_settings", return_value=settings):
        at = AppTest.from_file(str(DASHBOARD_HARNESS), default_timeout=15)
        at.run()
    return at


def _text(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown if not m.value.startswith("<style>"))


def test_dashboard_recent_theme_activity_row_shows_all_required_fields(tmp_path):
    filing = FilingEvent(
        rcept_no="0000320193-26-000099", corp_code="0000320193", corp_name="NVIDIA", stock_code="NVDA",
        report_nm="Test 8-K Filing", rcept_dt="2026-09-05", flr_nm="NVIDIA",
        source_name="SEC EDGAR", source_url="https://example.invalid/filing", theme_slug="ai-buildout",
        filed_at=_now_iso(timedelta(days=1)),
    )
    _seed_filing_event(tmp_path, filing, "edgar_filing_events.json")
    settings = _settings(tmp_path)

    at = _run_dashboard(settings)
    assert not at.exception
    all_text = _text(at)

    assert "Recent Theme Activity" in all_text
    assert "Where tracked coverage has moved recently" in all_text
    rta_start = all_text.index("Recent Theme Activity")
    chunk = all_text[rta_start:rta_start + 600]
    assert "AI Buildout" in chunk
    assert "1 item in the last 14 days" in chunk
    assert "NVIDIA" in chunk
    assert "Filing" in chunk

    assert any(b.label == "View →" for b in at.get("link_button"))
    # get_page("themes") only resolves a real Page object when run
    # through app.py's own st.navigation entry point — this isolated
    # per-page AppTest harness never populates st.session_state["_pages"],
    # so the "Browse all themes" page_link is never rendered here (the
    # same pre-existing, documented limitation the former Market Map
    # tests and tests/test_dashboard_data_integrity.py already work
    # around). Verified by direct source inspection instead, below; a
    # real rendering-level proof (exactly one link, regardless of row
    # count) lives in test_exactly_one_browse_all_themes_link_regardless_of_row_count.
    component_source = (REPO_ROOT / "src" / "ui" / "components" / "recent_theme_activity.py").read_text(encoding="utf-8")
    assert 'get_page("themes")' in component_source
    assert 'label="Browse all themes →"' in component_source
    # Corrective pass: no per-theme-specific "Explore <theme> in Themes"
    # label may ever be generated again — that was the exact defect this
    # correction fixes.
    assert "Explore {row.theme_name} in Themes" not in component_source
    assert "f\"Explore " not in component_source


def test_dashboard_view_link_never_points_at_the_raw_edinet_api_host(tmp_path):
    """EDINET-safety fix (design/DECISIONS.md): an EDINET-sourced item's
    "View ->" button must never resolve to the raw, key-required
    api.edinet-fsa.go.jp endpoint — it must resolve to the public
    disclosure portal root instead. One EDINET filing fixture renders on
    three Dashboard sections at once (Recently Updated, Recent Theme
    Activity, Regional Brief's Japan tab), so this single test proves
    all three surfaces consistently — the global `all_text` check below
    would fail if any of them leaked the raw API host, and the explicit
    per-surface anchor checks confirm each one specifically."""
    filing = FilingEvent(
        rcept_no="S100Z0OT", corp_code="E37584", corp_name="ispace, inc.", stock_code="93480",
        report_nm="臨時報告書", rcept_dt="2026-09-10", flr_nm="株式会社ispace",
        source_name="EDINET", source_url="https://api.edinet-fsa.go.jp/api/v2/documents/S100Z0OT",
        theme_slug="space", filed_at=_now_iso(timedelta(days=1)), original_language="Japanese",
    )
    _seed_filing_event(tmp_path, filing, "edinet_filing_events.json")
    settings = _settings(tmp_path)

    at = _run_dashboard(settings)
    assert not at.exception
    all_text = _text(at)
    assert "api.edinet-fsa.go.jp" not in all_text

    # Recent Theme Activity's own "View ->" button.
    view_buttons = [b for b in at.get("link_button") if b.label == "View →"]
    assert len(view_buttons) == 1
    assert view_buttons[0].url == "https://disclosure2.edinet-fsa.go.jp/"

    # Recently Updated's own anchor ("View source ↗") and Regional
    # Brief's own anchor ("View source document ↗") are plain markdown
    # <a> tags, not link_button widgets — confirmed directly in the
    # rendered markdown HTML.
    assert 'href="https://disclosure2.edinet-fsa.go.jp/"' in all_text


def test_dashboard_view_link_absent_without_a_source_url(tmp_path):
    story = _news_story("SK Hynix", "memory", "Headline text", _now_iso(timedelta(days=1)), source_url="")
    daily_news_backend.get_daily_news_repository(_settings(tmp_path)).upsert_new_stories([story])
    settings = _settings(tmp_path)

    at = _run_dashboard(settings)
    assert not at.exception
    all_text = _text(at)
    assert "Recent Theme Activity" in all_text
    assert "Memory" in all_text
    assert not any(b.label == "View →" for b in at.get("link_button"))


def test_dashboard_empty_state_renders_no_heading_or_shell(tmp_path):
    settings = _settings(tmp_path)  # no filings, no Daily News stories seeded at all
    at = _run_dashboard(settings)
    assert not at.exception
    all_text = _text(at)
    assert "Recent Theme Activity" not in all_text
    assert "Where tracked coverage has moved recently" not in all_text


def _sign_in_as(monkeypatch, email: str = "tester@example.test") -> None:
    """Simulates a real authenticated user for AppTest — same technique
    tests/test_dashboard_data_integrity.py's own _sign_in_as uses
    (duplicated here rather than imported, matching this codebase's own
    established precedent of each test file keeping its own copy of a
    small private test helper rather than sharing one across files).
    AppTest has no public API for a logged-in st.user, so this
    monkeypatches the private streamlit.user_info._get_user_info seam
    every st.user access funnels through."""
    import streamlit.user_info as user_info_module

    monkeypatch.setattr(user_info_module, "_get_user_info", lambda: {"is_logged_in": True, "email": email})
    monkeypatch.delenv("EDGE_PRIVATE_BETA_ALLOWED_EMAILS", raising=False)


def test_exactly_one_browse_all_themes_link_regardless_of_row_count(tmp_path, monkeypatch):
    """Corrective pass (design/DECISIONS.md): proves, at the real
    rendering level — not just by source inspection — that exactly one
    Themes-bound page_link renders for the whole component, even with
    multiple qualifying theme rows. get_page("themes") only resolves a
    real Page object when run through app.py's own st.navigation entry
    point (the isolated per-page Dashboard harness never populates
    st.session_state["_pages"]), so this test runs through app.py
    instead, with the mandatory Google sign-in gate simulated first —
    the same pattern tests/test_dashboard_data_integrity.py's own
    test_theme_health_link_targets_the_specific_published_theme uses."""
    filing_ai = FilingEvent(
        rcept_no="0000320193-26-000101", corp_code="0000320193", corp_name="NVIDIA", stock_code="NVDA",
        report_nm="AI Buildout Filing", rcept_dt="2026-09-05", flr_nm="NVIDIA",
        source_name="SEC EDGAR", source_url="https://example.invalid/filing3", theme_slug="ai-buildout",
        filed_at=_now_iso(timedelta(days=1)),
    )
    _seed_filing_event(tmp_path, filing_ai, "edgar_filing_events.json")
    settings = _settings(tmp_path)
    story = _news_story("SK Hynix", "memory", "Headline text", _now_iso(timedelta(days=2)), source_url="https://example.invalid/story")
    daily_news_backend.get_daily_news_repository(settings).upsert_new_stories([story])

    with patch("src.ui.pages.dashboard.get_settings", return_value=settings), \
         patch("src.ui.pages.dashboard.get_repositories", return_value=get_repositories(settings)):
        _sign_in_as(monkeypatch)
        at = AppTest.from_file(str(REPO_ROOT / "app.py"), default_timeout=15)
        at.run()
        at.run()  # second run: dashboard becomes the default page

    assert not at.exception
    all_text = " ".join(m.value for m in at.main.get("markdown") if not m.value.startswith("<style>"))
    assert "AI Buildout" in all_text
    assert "Memory" in all_text  # two distinct qualifying rows present

    themes_links = [pl for pl in at.main.get("page_link") if (pl.label or "").strip() == "Browse all themes →"]
    assert len(themes_links) == 1
    assert not any("Explore" in (pl.label or "") for pl in at.main.get("page_link"))


def test_dashboard_no_longer_imports_or_renders_market_map():
    dashboard_source = (REPO_ROOT / "src" / "ui" / "pages" / "dashboard.py").read_text(encoding="utf-8")
    assert "render_market_map" not in dashboard_source
    assert "ui.components.market_map" not in dashboard_source
    assert "group_companies_by_theme" not in dashboard_source
    assert not (REPO_ROOT / "src" / "ui" / "components" / "market_map.py").exists()

    settings = Settings(db_backend="json", cache_dir=Path(tempfile.mkdtemp()))
    at = _run_dashboard(settings)
    assert not at.exception
    all_text = _text(at)
    assert "Company and theme map" not in all_text
    assert "Investigate" not in all_text


def test_dashboard_other_sections_still_render(tmp_path):
    filing = FilingEvent(
        rcept_no="0000320193-26-000100", corp_code="0000320193", corp_name="NVIDIA", stock_code="NVDA",
        report_nm="Another Filing", rcept_dt="2026-09-05", flr_nm="NVIDIA",
        source_name="SEC EDGAR", source_url="https://example.invalid/filing2", theme_slug="ai-buildout",
        filed_at=_now_iso(timedelta(days=1)),
    )
    _seed_filing_event(tmp_path, filing, "edgar_filing_events.json")
    settings = _settings(tmp_path)

    at = _run_dashboard(settings)
    assert not at.exception
    all_text = _text(at)
    assert "Recently Updated" in all_text
    assert "Regional Brief" in all_text
