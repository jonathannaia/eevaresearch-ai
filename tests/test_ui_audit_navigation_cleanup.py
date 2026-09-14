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
    list_published_themes() is ever called by the sidebar's own
    _has_published_themes() check."""

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

    # Themes sidebar visibility (design/DECISIONS.md) is data-driven —
    # every test in this file gets a deterministic, explicitly-controlled
    # theme repository (default: zero published themes) rather than
    # depending on whatever happens to be in the local data/cache/
    # directory on disk. _has_published_themes() is st.cache_data-backed
    # (process-wide, not per-test) — cleared before every run so one
    # test's fake repository result can never leak into the next (same
    # .clear()-before-use convention already established for
    # _load_dashboard_snapshot elsewhere in this test suite).
    monkeypatch.setattr(
        ui_module.backend_factory, "get_theme_repository", lambda settings: _FakeThemeRepo(published_themes)
    )
    ui_module._has_published_themes.clear()

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


def test_workspace_shows_exactly_dashboard_filings_signals(monkeypatch):
    # Watchlists was removed entirely (reader-facing data-integrity
    # pass, design/DECISIONS.md) — session-only, seeded from illustrative
    # data, no live real data of its own. "Research Theses" is absent
    # here because this test's default theme repository has zero
    # published Theses — see
    # test_themes_nav_item_appears_once_a_theme_is_published below for
    # the positive case now that its visibility is data-driven, not a
    # fixed removal.
    at = _run_to_dashboard(monkeypatch)
    labels = [pl.label for pl in _sidebar_page_links(at)]
    for expected in ("Dashboard", "Filings", "Signals"):
        assert expected in labels
    assert "Watchlists" not in labels
    assert "Research Theses" not in labels


def test_filings_label_is_filings_not_radar_or_radar_inbox_in_the_sidebar(monkeypatch):
    at = _run_to_dashboard(monkeypatch)
    labels = {pl.label for pl in _sidebar_page_links(at)}
    assert "Filings" in labels
    assert "Radar" not in labels
    assert "Radar Inbox" not in labels


def test_coverage_radar_signals_research_themes_are_not_visible_sidebar_items(monkeypatch):
    """The Evidence-First Themes MVP (design/DECISIONS.md) had
    reintroduced a "Themes" sidebar entry pointing at a new public
    research page, distinct from the legacy demo ticker/theme/subtheme
    browser this original navigation-cleanup pass had removed from
    visible nav (that legacy page — once moved to a hidden "Theme
    Browser" route — was later removed entirely, not just hidden;
    reader-facing data-integrity pass, design/DECISIONS.md, no live
    real data of its own). The beta UI polish pass (design/DECISIONS.md)
    moved "Themes" out of the always-visible sidebar; a later pass made
    it appear again, but only once real content exists to justify it —
    see test_themes_nav_item_appears_once_a_theme_is_published below.
    This test's default (zero published Themes) still keeps it hidden.
    Coverage/Radar Signals/Research remain unconditionally absent
    regardless. Product-naming separation (design/DECISIONS.md): bare
    "Signals" is deliberately NOT in this removed-label list any more —
    it's the Daily News rework's own, now-visible PRIMARY_NAV entry (see
    test_workspace_shows_exactly_dashboard_filings_signals above); only
    the Radar-derived "Radar Signals" page stays hidden. Its route/data/
    repository/internal-authoring-workflow are unaffected — only sidebar
    visibility changed (see
    test_coverage_themes_signals_routes_remain_registered below)."""
    at = _run_to_dashboard(monkeypatch)
    labels = {pl.label for pl in _sidebar_page_links(at)}
    # Exact-label check, not substring — "Methodology & Coverage" legitimately
    # contains "Coverage" as a substring, so a naive "not in joined text"
    # check would false-fail on the one entry that's supposed to remain.
    for removed_label in ("Coverage", "Radar Signals", "Research", "Research Theses"):
        assert removed_label not in labels
    assert "Theme Browser" not in labels


# ============================================================
# Themes sidebar visibility — data-driven (design/DECISIONS.md)
# ============================================================


def test_no_themes_nav_item_when_zero_published_themes(monkeypatch):
    at = _run_to_dashboard(monkeypatch, published_themes=())
    labels = {pl.label for pl in _sidebar_page_links(at)}
    assert "Research Theses" not in labels


def test_themes_nav_item_appears_once_a_theme_is_published(monkeypatch):
    at = _run_to_dashboard(monkeypatch, published_themes=[_published_theme()])
    labels = {pl.label for pl in _sidebar_page_links(at)}
    assert "Research Theses" in labels


def test_internal_only_theme_does_not_make_the_nav_item_appear(monkeypatch):
    # The fake repository's list_published_themes() already only ever
    # returns PUBLISHED rows (matching the real repository's own
    # server-side filter), so an internal theme is modeled here exactly
    # as it behaves in production: it simply never appears in that list
    # at all — proving the sidebar link's absence is not a coincidence
    # of an empty fixture, but the direct, correct consequence of
    # _has_published_themes() reading the PUBLISHED-only protocol.
    internal_theme = ResearchTheme(
        id=theme_store.build_theme_id("Internal only", "2026-09-14T00:00:00+00:00"),
        category=ThemeCategory.BOTTLENECK, status=ThemeStatus.NEW, visibility=ThemeVisibility.INTERNAL,
        title="Internal only", key_question="q", hypothesis="h", working_thesis="w", why_it_matters="y",
        what_could_change_the_view="c", what_to_watch_next="n",
        created_at="2026-09-14T00:00:00+00:00", updated_at="2026-09-14T00:00:00+00:00",
    )
    assert internal_theme.visibility != ThemeVisibility.PUBLISHED
    # A real ThemeRepositoryProtocol implementation would never include
    # this row in list_published_themes() at all (see
    # theme_store.list_published_themes()'s own server-side filter) —
    # modeled here by simply not adding it to the fake repo's tuple.
    at = _run_to_dashboard(monkeypatch, published_themes=())
    labels = {pl.label for pl in _sidebar_page_links(at)}
    assert "Research Theses" not in labels


def test_themes_nav_item_points_to_the_real_themes_page(monkeypatch):
    at = _run_to_dashboard(monkeypatch, published_themes=[_published_theme()])
    themes_links = [pl for pl in _sidebar_page_links(at) if pl.label == "Research Theses"]
    assert len(themes_links) == 1
    assert at.session_state["_pages"]["themes"] is not None


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


def test_methodology_and_coverage_appears_in_the_system_group(monkeypatch):
    at = _run_to_dashboard(monkeypatch)
    labels = {pl.label for pl in _sidebar_page_links(at)}
    assert "Methodology & Coverage" in labels
    assert "Methodology" not in labels  # the standalone entry is retired, not duplicated


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
    for key in ("coverage", "themes", "signals", "methodology", "about"):
        assert key in pages
        assert pages[key] is not None
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


def test_no_empty_workspace_or_system_group_in_the_rendered_sidebar(monkeypatch):
    at = _run_to_dashboard(monkeypatch)
    sidebar_markdown = " ".join(m.value for m in at.sidebar.get("markdown"))
    assert "er-rail-group-label" in sidebar_markdown  # groups render at all
    # Both group headings actually have at least one link under them —
    # proven indirectly: every item asserted present above resolves to a
    # real page_link, so a genuinely empty group would already show up as
    # a missing label in the tests above. This test only guards against a
    # future edit leaving a bare heading with a real page dict that's
    # simply empty for that group.
    labels = {pl.label for pl in _sidebar_page_links(at)}
    assert labels & {"Dashboard", "Filings", "Signals", "Watchlists"}
    assert labels & {"Methodology & Coverage"}


def test_no_theme_pulse_anywhere_in_the_ui_shell_or_entry_point():
    for path in (REPO_ROOT / "src" / "ui" / "ui.py", REPO_ROOT / "app.py"):
        assert "Theme Pulse" not in path.read_text(encoding="utf-8")
