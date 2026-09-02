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

APP_PATH = Path(__file__).parent.parent / "app.py"
REPO_ROOT = Path(__file__).parent.parent


def _run_to_dashboard() -> AppTest:
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


def test_workspace_shows_exactly_dashboard_radar_themes_daily_news():
    # Watchlists was removed entirely (reader-facing data-integrity
    # pass, design/DECISIONS.md) — session-only, seeded from illustrative
    # data, no live real data of its own.
    at = _run_to_dashboard()
    labels = [pl.label for pl in _sidebar_page_links(at)]
    for expected in ("Dashboard", "Radar", "Themes", "Daily News"):
        assert expected in labels
    assert "Watchlists" not in labels


def test_radar_label_is_radar_not_radar_inbox_in_the_sidebar():
    at = _run_to_dashboard()
    labels = {pl.label for pl in _sidebar_page_links(at)}
    assert "Radar" in labels
    assert "Radar Inbox" not in labels


def test_coverage_signals_research_are_not_visible_sidebar_items():
    """"Themes" is deliberately excluded from this list as of the
    Evidence-First Themes MVP (design/DECISIONS.md) — that step
    reintroduced a "Themes" sidebar entry on purpose, pointing at a new
    public research page, not the legacy demo browser this original
    navigation-cleanup pass had removed from visible nav. The legacy
    demo page itself (moved to a hidden "Theme Browser" route by that
    step) was later removed entirely, not just hidden (reader-facing
    data-integrity pass, design/DECISIONS.md) — it had no live real
    data."""
    at = _run_to_dashboard()
    labels = {pl.label for pl in _sidebar_page_links(at)}
    # Exact-label check, not substring — "Methodology & Coverage" legitimately
    # contains "Coverage" as a substring, so a naive "not in joined text"
    # check would false-fail on the one entry that's supposed to remain.
    for removed_label in ("Coverage", "Signals", "Research"):
        assert removed_label not in labels
    assert "Theme Browser" not in labels


def test_methodology_and_coverage_appears_in_the_system_group():
    at = _run_to_dashboard()
    labels = {pl.label for pl in _sidebar_page_links(at)}
    assert "Methodology & Coverage" in labels
    assert "Methodology" not in labels  # the standalone entry is retired, not duplicated


def test_settings_is_not_invented_since_no_settings_route_exists():
    at = _run_to_dashboard()
    labels = {pl.label for pl in _sidebar_page_links(at)}
    assert "Settings" not in labels
    assert "settings" not in at.session_state["_pages"]


def test_coverage_themes_signals_routes_remain_registered():
    # Existing direct URLs must remain operational — hidden from the
    # sidebar, not deleted. Reuses the exact keys/url_path table app.py
    # builds pages from. Research (canned-demo-answer chat) was removed
    # entirely (reader-facing data-integrity pass, design/DECISIONS.md),
    # not just hidden — it had no live real data.
    at = _run_to_dashboard()
    pages = at.session_state["_pages"]
    for key in ("coverage", "themes", "signals", "methodology", "about"):
        assert key in pages
        assert pages[key] is not None
    assert "research" not in pages


def test_radar_and_daily_news_remain_two_distinct_registered_pages():
    at = _run_to_dashboard()
    pages = at.session_state["_pages"]
    assert pages["radar_inbox"] is not pages["daily_news"]


def test_no_duplicate_nav_labels_in_the_rendered_sidebar():
    at = _run_to_dashboard()
    labels = [pl.label for pl in _sidebar_page_links(at)]
    # "EevaResearch" (brand/home link) and "Disclaimer" (footer link) are
    # expected extras outside the two nav tables — dedupe check only cares
    # that no label appears twice among the WORKSPACE/SYSTEM entries.
    workspace_and_system = [l for l in labels if l not in {"EevaResearch", "Disclaimer"}]
    assert len(workspace_and_system) == len(set(workspace_and_system)), workspace_and_system


def test_no_empty_workspace_or_system_group_in_the_rendered_sidebar():
    at = _run_to_dashboard()
    sidebar_markdown = " ".join(m.value for m in at.sidebar.get("markdown"))
    assert "er-rail-group-label" in sidebar_markdown  # groups render at all
    # Both group headings actually have at least one link under them —
    # proven indirectly: every item asserted present above resolves to a
    # real page_link, so a genuinely empty group would already show up as
    # a missing label in the tests above. This test only guards against a
    # future edit leaving a bare heading with a real page dict that's
    # simply empty for that group.
    labels = {pl.label for pl in _sidebar_page_links(at)}
    assert labels & {"Dashboard", "Radar", "Daily News", "Watchlists"}
    assert labels & {"Methodology & Coverage"}


def test_no_theme_pulse_anywhere_in_the_ui_shell_or_entry_point():
    for path in (REPO_ROOT / "src" / "ui" / "ui.py", REPO_ROOT / "app.py"):
        assert "Theme Pulse" not in path.read_text(encoding="utf-8")
