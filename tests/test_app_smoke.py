"""AppTest-based smoke tests — each registered page is tested deliberately
and separately (AppTest simulates one running page per test), rather than
attempting cross-page session-state flows in a single run, matching
AppTest's real limitations for multipage apps built with callable-based
st.Page objects. Pure models/repositories/helpers are tested elsewhere,
independent of any Streamlit runtime.
"""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.ui.ui import PRIMARY_NAV, SYSTEM_NAV

HARNESS_DIR = Path(__file__).parent / "apptest_pages"

PRIMARY_PAGES = [
    "home_page.py",
    "dashboard_page.py",
    "radar_inbox_page.py",
    "signals_page.py",
    "methodology_page.py",
    "disclaimer_page.py",
    "about_page.py",
]


@pytest.mark.parametrize("harness_file", PRIMARY_PAGES)
def test_primary_page_renders_without_exception(harness_file):
    at = AppTest.from_file(str(HARNESS_DIR / harness_file), default_timeout=10)
    at.run()
    assert not at.exception, f"{harness_file} raised: {at.exception}"


_FULL_FOOTER_PAGES = ["methodology_page.py", "disclaimer_page.py"]
_COMPACT_FOOTER_PAGES = [p for p in PRIMARY_PAGES if p not in _FULL_FOOTER_PAGES]


@pytest.mark.parametrize("harness_file", _FULL_FOOTER_PAGES)
def test_full_footer_page_renders_full_footer(harness_file):
    # Methodology/Disclaimer keep the long-form footer (UX-refinement
    # follow-up) — everywhere else gets the compact one-liner instead, see
    # test_other_pages_render_compact_footer below.
    at = AppTest.from_file(str(HARNESS_DIR / harness_file), default_timeout=10)
    at.run()
    all_html = " ".join(m.value for m in at.markdown)
    assert "does not provide investment advice" in all_html
    assert "EevaResearch AI v" in all_html


@pytest.mark.parametrize("harness_file", _COMPACT_FOOTER_PAGES)
def test_other_pages_render_compact_footer(harness_file):
    at = AppTest.from_file(str(HARNESS_DIR / harness_file), default_timeout=10)
    at.run()
    all_html = " ".join(m.value for m in at.markdown)
    assert "Not investment advice" in all_html
    assert "does not provide investment advice" not in all_html


def test_sidebar_status_renders():
    """The previous blanket "Demo environment · sample data" status was
    removed (reader-facing data-integrity pass, design/DECISIONS.md) —
    several pages are real and live now, so no single truthful word
    describes the whole app at once; nothing replaced it."""
    at = AppTest.from_file(str(HARNESS_DIR / "sidebar_rail.py"), default_timeout=10)
    at.run()
    assert not at.exception
    all_html = " ".join(m.value for m in at.markdown)
    assert "EevaResearch" in all_html
    assert "Demo environment" not in all_html
    # Every visible WORKSPACE + SYSTEM nav item renders as a real
    # st.page_link (navigation-cleanup pass, design/DECISIONS.md) — a
    # subset check, not exact equality, since the sidebar also renders a
    # few other page_links (the brand/home link, the footer's Disclaimer
    # link) that aren't part of either nav table.
    nav_link_labels = {pl.label for pl in at.get("page_link")}
    expected = {label for _, label in PRIMARY_NAV + SYSTEM_NAV}
    missing = expected - nav_link_labels
    assert not missing, f"missing nav items: {missing}"


# ============================================================
# Application-shell dark/dim pass (Perplexity-inspired sidebar layout) —
# brand+search at top, nav unchanged in the middle, account control
# anchored to the bottom. All exercised through the same real
# render_sidebar() call the sidebar_rail.py harness above already uses —
# not a separate/duplicated rendering path.
# ============================================================


def test_brand_block_renders_first_at_the_sidebar_top():
    at = AppTest.from_file(str(HARNESS_DIR / "sidebar_rail.py"), default_timeout=10)
    at.run()
    assert not at.exception
    sidebar_markdown = list(at.sidebar.get("markdown"))
    assert sidebar_markdown, "sidebar rendered no markdown elements at all"
    # The brand wrapper div must be the very first thing rendered in the
    # sidebar — before any nav group label or nav item.
    assert sidebar_markdown[0].value == '<div class="er-rail-brand">'
    brand_block_values = " ".join(m.value for m in sidebar_markdown[:4])
    assert "er-rail-logo" in brand_block_values
    assert "EevaResearch" in brand_block_values or "er-rail-word" in brand_block_values


def test_search_trigger_is_compact_with_an_accessible_tooltip_and_reaches_the_real_search():
    at = AppTest.from_file(str(HARNESS_DIR / "sidebar_rail.py"), default_timeout=10)
    at.run()
    assert not at.exception
    search_buttons = [b for b in at.sidebar.get("button") if b.key == "cmdk-trigger"]
    assert len(search_buttons) == 1, "expected exactly one search trigger in the sidebar"
    button = search_buttons[0]
    # Icon-only (Material Symbols shorthand, not the spelled-out "Search…
    # ⌘K" label default rendering uses elsewhere) — the accessible label/
    # tooltip an icon-only control needs comes from `help`, not the label.
    assert button.label == ":material/search:"
    assert button.help == "Search (⌘K)"
    # Same widget key as every other rendering of this trigger
    # (src/ui/components/command_palette.py's own PALETTE_TRIGGER_KEY) —
    # proof this is the identical existing search mechanism (same button,
    # same on_click -> _open_palette() -> the real st.dialog search UI),
    # never a second/new search implementation.
    from src.ui.components.command_palette import PALETTE_TRIGGER_KEY

    assert button.key == PALETTE_TRIGGER_KEY == "cmdk-trigger"


def test_nav_destinations_and_routing_are_unchanged_by_the_shell_pass():
    at = AppTest.from_file(str(HARNESS_DIR / "sidebar_rail.py"), default_timeout=10)
    at.run()
    assert not at.exception
    nav_links = {pl.label: pl.page for pl in at.sidebar.get("page_link")}
    for key, label in PRIMARY_NAV + SYSTEM_NAV:
        assert label in nav_links, f"missing nav destination: {label}"
        assert nav_links[label] == key, f"{label} points at {nav_links[label]!r}, expected {key!r}"


def test_account_control_is_present_and_rendered_after_navigation():
    at = AppTest.from_file(str(HARNESS_DIR / "sidebar_rail.py"), default_timeout=10)
    at.run()
    assert not at.exception
    popovers = at.sidebar.get("popover")
    assert len(popovers) == 1, "expected exactly one account popover in the sidebar"
    account_popover = popovers[0]
    assert account_popover.proto.popover.help == "Account"
    # The popover's own wrapping st.container carries the real widget key
    # (sidebar-account-{nav_key}) — checked via its CSS class further
    # down in this same test (the "er-rail-account" wrapper div check),
    # since Block.key on the popover element itself reflects the
    # popover's own (unset) key, not its container's.

    # "Rendered after navigation": the account block's own wrapper div
    # must come after the Workspace/System nav group labels in source
    # order, not interleaved with or before them.
    sidebar_markdown_values = [m.value for m in at.sidebar.get("markdown")]
    workspace_idx = sidebar_markdown_values.index('<div class="er-rail-group-label">Workspace</div>')
    system_idx = sidebar_markdown_values.index('<div class="er-rail-group-label">System</div>')
    account_idx = sidebar_markdown_values.index('<div class="er-rail-account">')
    assert workspace_idx < account_idx
    assert system_idx < account_idx


def test_account_control_shows_not_signed_in_when_no_user_session():
    at = AppTest.from_file(str(HARNESS_DIR / "sidebar_rail.py"), default_timeout=10)
    at.run()
    assert not at.exception
    account_popover = at.sidebar.get("popover")[0]
    assert account_popover.proto.popover.label == "?"
    captions = [c.value for c in at.sidebar.get("caption")]
    assert "Not signed in" in captions


def test_dark_theme_tokens_are_loaded_into_the_page():
    """load_css() (called by with_chrome()/every real page render, and
    directly by the sidebar_rail.py harness) injects assets/styles.css
    verbatim into an st.markdown(unsafe_allow_html=True) call — this
    confirms the actual dark-token stylesheet reached this render, not
    just that the file on disk has the right values (already checked by
    tests/test_visual_theme_redesign.py)."""
    at = AppTest.from_file(str(HARNESS_DIR / "sidebar_rail.py"), default_timeout=10)
    at.run()
    assert not at.exception
    style_blocks = [m.value for m in at.get("markdown") if m.value.startswith("<style>")]
    assert style_blocks, "no <style> block was rendered at all"
    css_in_page = style_blocks[0]
    assert "--bg: #0B0C0E;" in css_in_page
    assert "--accent: #6FD3BC;" in css_in_page
    assert 'base = "light"' not in css_in_page  # sanity: this is CSS, not the toml, but guards against a copy/paste mixup


