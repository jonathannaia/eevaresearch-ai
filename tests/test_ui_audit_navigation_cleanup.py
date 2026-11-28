"""Navigation-cleanup pass — visible-sidebar-simplification regression
tests (design/DECISIONS.md). Complements tests/test_navigation.py's own
registration-level checks (every key still built, exact PRIMARY_NAV/
SYSTEM_NAV/HIDDEN_FROM_NAV tables) with checks against the actual
*rendered* sidebar: which page_links appear, under which group, with what
label text — run through app.py's real entry point (not an isolated
single-page harness, which never populates st.session_state["_pages"]) so
`pages.get(...)` resolves to the real registered Page objects.
"""
from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.data_access import theme_store
from src.models.theme_research import ResearchTheme, ThemeCategory, ThemeStatus, ThemeVisibility
from src.ui import ui as ui_module

APP_PATH = Path(__file__).parent.parent / "app.py"
REPO_ROOT = Path(__file__).parent.parent


def _published_theme(title="Test theme", created_at="2026-09-14T00:00:00+00:00") -> ResearchTheme:
    return ResearchTheme(
        id=theme_store.build_theme_id(title, created_at),
        category=ThemeCategory.BOTTLENECK, status=ThemeStatus.NEW, visibility=ThemeVisibility.PUBLISHED,
        title=title, key_question="q", hypothesis="h", working_thesis="w", why_it_matters="y",
        what_could_change_the_view="c", what_to_watch_next="n", created_at=created_at, updated_at=created_at,
    )


class _FakeThemeRepo:
    """Minimal stand-in for ThemeRepositoryProtocol — only
    list_published_themes() is ever called by the sidebar, now by
    _published_theme_count() for the Theses count badge."""

    def __init__(self, published_themes=()):
        self._published_themes = tuple(published_themes)

    def list_published_themes(self):
        return self._published_themes

    def get_published_theme(self, theme_id):
        raise AssertionError("sidebar visibility must never call get_published_theme()")

    def evidence_for_theme(self, theme_id):
        raise AssertionError("sidebar visibility must never call evidence_for_theme()")

    def company_map_for_theme(self, theme_id):
        raise AssertionError("sidebar visibility must never call company_map_for_theme()")


def _run_to_dashboard(monkeypatch, published_themes=()) -> AppTest:
    # Mandatory Google sign-in gate (design/DECISIONS.md): app.py now
    # stops at a sign-in screen before st.navigation() for any
    # unauthenticated visitor, so reaching Dashboard's real sidebar first
    # requires simulating an authenticated user — same technique as
    # tests/test_app_auth_gate.py (see that file's module docstring for
    # why AppTest needs this private-function monkeypatch at all).
    import streamlit.user_info as user_info_module

    monkeypatch.setattr(
        user_info_module, "_get_user_info", lambda: {"is_logged_in": True, "email": "tester@example.test"}
    )
    monkeypatch.delenv("EDGE_PRIVATE_BETA_ALLOWED_EMAILS", raising=False)

    # Every test in this file gets a deterministic, explicitly-controlled
    # theme repository (default: zero published Theses) rather than
    # depending on whatever happens to be in the local data/cache/
    # directory on disk. Since UI redesign item 1 this no longer decides
    # whether the Theses nav item exists — it is a static PRIMARY_NAV
    # entry now — only whether a count badge renders beside it.
    # _published_theme_count() is st.cache_data-backed (process-wide, not
    # per-test) — cleared before every run so one test's fake repository
    # result can never leak into the next (same .clear()-before-use
    # convention already established for _load_dashboard_snapshot
    # elsewhere in this test suite).
    monkeypatch.setattr(
        ui_module.backend_factory, "get_theme_repository", lambda settings: _FakeThemeRepo(published_themes)
    )
    ui_module._published_theme_count.clear()

    # First run of a fresh AppTest session lands on Home (show_sidebar=
    # False, per app.py) — a second run flips dashboard_is_default and
    # renders Dashboard, which does have the sidebar.
    at = AppTest.from_file(str(APP_PATH), default_timeout=15)
    at.run()
    at.run()
    assert not at.exception
    return at


def _sidebar_page_links(at: AppTest):
    return list(at.sidebar.get("page_link"))


def test_the_rail_shows_exactly_the_six_redesign_destinations(monkeypatch):
    """UI redesign item 1: Today · Feed · Companies · Themes · Theses ·
    Coverage, and nothing else. Every label removed by an earlier pass
    stays removed."""
    at = _run_to_dashboard(monkeypatch)
    labels = [pl.label for pl in _sidebar_page_links(at)]
    nav = [l for l in labels if l not in {"EevaResearch", "Disclaimer"}]
    assert nav == ["Today", "Feed", "Companies", "Themes", "Theses", "Coverage"]
    for gone in ("Watchlists", "Research", "Theme Browser", "Radar Signals", "Dashboard", "Signals"):
        assert gone not in labels, gone


def test_theses_is_always_in_the_rail_even_with_nothing_published(monkeypatch):
    """The former _has_published_themes() gate is gone: a fixed six-item
    rail cannot have an item that appears and disappears, so Theses is
    always linked and themes_research.py's own index empty-state is what
    a visitor sees when nothing is published yet."""
    at = _run_to_dashboard(monkeypatch, published_themes=())
    labels = [pl.label for pl in _sidebar_page_links(at)]
    assert "Theses" in labels
    # ...and no "0" badge beside it: _nav_badge_counts() drops any count
    # that is not greater than zero.
    assert not any("er-rail-count" in m.value for m in at.sidebar.get("markdown"))


def test_theses_count_badge_appears_once_theses_are_published(monkeypatch):
    at = _run_to_dashboard(monkeypatch, published_themes=[_published_theme(), _published_theme("Second", "2026-09-15T00:00:00+00:00")])
    assert "Theses" in [pl.label for pl in _sidebar_page_links(at)]
    badges = [m.value for m in at.sidebar.get("markdown") if "er-rail-count" in m.value]
    assert badges == ['<span class="er-rail-count">2</span>'], badges


def test_filings_left_the_rail_but_the_route_and_its_label_survive(monkeypatch):
    """Filings (radar_inbox) moved to HIDDEN_FROM_NAV in item 1 — the
    six-item rail has no slot for it and item 2 is what merges its
    filings into the Feed. The rendered rail must not link it, and the
    route must still exist: the command palette's "Open Filings" action
    is the interim path to it."""
    at = _run_to_dashboard(monkeypatch)
    labels = {pl.label for pl in _sidebar_page_links(at)}
    assert "Filings" not in labels
    assert "Radar" not in labels and "Radar Inbox" not in labels
    assert at.session_state["_pages"]["radar_inbox"] is not None
    assert ("radar_inbox", "Filings") in ui_module.HIDDEN_FROM_NAV


def test_the_two_theme_nav_items_point_at_two_different_pages(monkeypatch):
    """The naming trap this repository sets: "Themes" is the content-tag
    index (route key "theme_index") and "Theses" is the curated research
    narratives (route key "themes", page themes_research.py, internally
    also called "theme"). Getting them the wrong way round would put the
    wrong page behind both labels, which no label-only assertion would
    catch — so this asserts the two route keys resolve to two distinct
    registered pages."""
    at = _run_to_dashboard(monkeypatch)
    pages = at.session_state["_pages"]
    assert pages["theme_index"] is not None
    assert pages["themes"] is not None
    assert pages["theme_index"] is not pages["themes"]
    assert dict(ui_module.PRIMARY_NAV)["theme_index"] == "Themes"
    assert dict(ui_module.PRIMARY_NAV)["themes"] == "Theses"


# ============================================================
# The Theses page itself (its nav presence is covered above)
# ============================================================


def test_public_themes_page_still_shows_published_only_content(monkeypatch):
    """The sidebar link itself just points at the existing "themes" page
    object (proven above); this test confirms that page object's own
    render path still enforces published-only content unchanged. Full
    click-through from a live sidebar link is not simulated here — this
    repo's AppTest convention deliberately doesn't attempt that (see
    tests/test_navigation.py's own module docstring: `switch_page()`
    only supports file-based pages, and this app's dynamically-built
    `st.Page` objects don't qualify) — so, matching every other page
    content test in this codebase, this exercises the same
    `with_chrome(themes_research.render, "themes")` callable app.py
    itself registers, via the isolated per-page harness. Deeper
    published-vs-internal filtering behavior is already covered
    exhaustively by tests/test_themes_research_page.py; this is only a
    focused confirmation for this navigation change specifically."""
    from src.ui.pages import themes_research

    published = _published_theme(title="Published Theme")
    internal = ResearchTheme(
        id=theme_store.build_theme_id("Internal Theme", "2026-09-14T00:00:00+00:00"),
        category=ThemeCategory.BOTTLENECK, status=ThemeStatus.NEW, visibility=ThemeVisibility.INTERNAL,
        title="Internal Theme", key_question="q", hypothesis="h", working_thesis="w", why_it_matters="y",
        what_could_change_the_view="c", what_to_watch_next="n",
        created_at="2026-09-14T00:00:00+00:00", updated_at="2026-09-14T00:00:00+00:00",
    )

    class _RepoWithBothVisibilities:
        def list_published_themes(self):
            return (published,)  # a real repository already excludes `internal`

        def get_published_theme(self, theme_id):
            return published if theme_id == published.id else None

        def evidence_for_theme(self, theme_id):
            return ()

        def company_map_for_theme(self, theme_id):
            return ()

    # No is_admin() patch: themes_research.py no longer gates on it at
    # all (Research Theses admin-gate removal, design/DECISIONS.md) —
    # this test proves the real content renders with zero such gate.
    monkeypatch.setattr(
        themes_research.backend_factory, "get_theme_repository", lambda settings: _RepoWithBothVisibilities()
    )
    harness = REPO_ROOT / "tests" / "apptest_pages" / "themes_research_page.py"
    at = AppTest.from_file(str(harness), default_timeout=15)
    at.run()
    assert not at.exception
    all_html = " ".join(m.value for m in at.markdown)
    assert "Published Theme" in all_html
    assert "Internal Theme" not in all_html


def test_coverage_is_the_last_rail_item_and_methodology_is_not_duplicated(monkeypatch):
    """"Methodology & Coverage" shortened to plain "Coverage" (UI redesign
    item 1) — same route, same page. Methodology keeps its own hidden
    route, reachable from Coverage's cross-link and the footer, and must
    not appear as a second rail item."""
    at = _run_to_dashboard(monkeypatch)
    labels = [pl.label for pl in _sidebar_page_links(at)]
    assert "Coverage" in labels
    assert "Methodology & Coverage" not in labels
    assert "Methodology" not in labels
    nav = [l for l in labels if l not in {"EevaResearch", "Disclaimer"}]
    assert nav[-1] == "Coverage"


def test_settings_is_not_invented_since_no_settings_route_exists(monkeypatch):
    at = _run_to_dashboard(monkeypatch)
    labels = {pl.label for pl in _sidebar_page_links(at)}
    assert "Settings" not in labels
    assert "settings" not in at.session_state["_pages"]


def test_coverage_themes_signals_routes_remain_registered(monkeypatch):
    # Existing direct URLs must remain operational — hidden from the
    # sidebar, not deleted. Reuses the exact keys/url_path table app.py
    # builds pages from. Research (canned-demo-answer chat) was removed
    # entirely (reader-facing data-integrity pass, design/DECISIONS.md),
    # not just hidden — it had no live real data.
    at = _run_to_dashboard(monkeypatch)
    pages = at.session_state["_pages"]
    for key in ("coverage", "themes", "theme_index", "companies", "radar_inbox", "signals", "methodology", "about"):
        assert key in pages, key
        assert pages[key] is not None, key
    assert "research" not in pages


def test_radar_and_daily_news_remain_two_distinct_registered_pages(monkeypatch):
    at = _run_to_dashboard(monkeypatch)
    pages = at.session_state["_pages"]
    assert pages["radar_inbox"] is not pages["daily_news"]


def test_no_duplicate_nav_labels_in_the_rendered_sidebar(monkeypatch):
    at = _run_to_dashboard(monkeypatch)
    labels = [pl.label for pl in _sidebar_page_links(at)]
    # "EevaResearch" (brand/home link) and "Disclaimer" (footer link) are
    # expected extras outside the two nav tables — dedupe check only cares
    # that no label appears twice among the WORKSPACE/SYSTEM entries.
    workspace_and_system = [l for l in labels if l not in {"EevaResearch", "Disclaimer"}]
    assert len(workspace_and_system) == len(set(workspace_and_system)), workspace_and_system


def test_the_rail_renders_no_group_label_at_all(monkeypatch):
    """UI redesign item 1 replaced the WORKSPACE/SYSTEM group headings
    with one flat six-item list, as the mockup rail shows it. This
    guards the opposite of what it used to: a heading reappearing above
    a five-item and a one-item group is the regression now. The Admin
    group label is separate (admins only) and is not rendered here —
    this session's tester is not an admin."""
    at = _run_to_dashboard(monkeypatch)
    sidebar_markdown = " ".join(m.value for m in at.sidebar.get("markdown"))
    assert "er-rail-group-label" not in sidebar_markdown
    # Every one of the six is a real page_link, so no "flat list" claim
    # can be satisfied by a group that silently rendered empty.
    labels = {pl.label for pl in _sidebar_page_links(at)}
    assert {"Today", "Feed", "Companies", "Themes", "Theses", "Coverage"} <= labels


def test_no_theme_pulse_anywhere_in_the_ui_shell_or_entry_point():
    for path in (REPO_ROOT / "src" / "ui" / "ui.py", REPO_ROOT / "app.py"):
        assert "Theme Pulse" not in path.read_text(encoding="utf-8")
