"""Redesign v2 — application shell (src/ui/ui.py): grouped sidebar with
real-count badges, real refresh data only, real identity, and a top bar
whose breadcrumb comes from the actual route. Rendered through the same
sidebar_rail.py / dashboard_page.py AppTest harnesses every other shell
test uses; every count/timestamp source is patched at the ui module
boundary so nothing here reads a live store."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from src.ui import ui as ui_module

_RAIL = Path(__file__).parent / "apptest_pages" / "sidebar_rail.py"
_DASHBOARD = Path(__file__).parent / "apptest_pages" / "dashboard_page.py"


@pytest.fixture(autouse=True)
def _clear_shell_caches():
    # _has_published_themes() is gone (UI redesign item 1): the Theses
    # nav item is static now, so nothing asks whether anything is
    # published before drawing the rail. Only the count badge is cached.
    ui_module._published_theme_count.clear()
    yield
    ui_module._published_theme_count.clear()


def _sidebar_markdown(at: AppTest) -> list[str]:
    return [m.value for m in at.sidebar.get("markdown")]


def _run_rail(**patches) -> AppTest:
    with patch.object(ui_module, "_nav_badge_counts", return_value=patches.get("badges", {})), \
         patch.object(ui_module, "_latest_filings_refresh_label", return_value=patches.get("refresh")):
        at = AppTest.from_file(str(_RAIL), default_timeout=10)
        at.run()
    assert not at.exception, at.exception
    return at


def test_sidebar_renders_one_flat_six_item_nav_with_no_group_labels():
    """UI redesign item 1: Today · Feed · Companies · Themes · Theses ·
    Coverage, in that order, as real st.page_links — and no
    "Workspace"/"System" group label above any of them any more."""
    at = _run_rail()
    md = _sidebar_markdown(at)
    assert md[0] == '<div class="er-rail-brand">'
    assert not any("er-rail-group-label" in m for m in md)

    # The brand wordmark is also a page_link, so compare on the nav
    # labels themselves rather than on every link in the rail.
    nav_labels = [label for _, label in ui_module.PRIMARY_NAV + ui_module.SYSTEM_NAV]
    assert nav_labels == ["Today", "Feed", "Companies", "Themes", "Theses", "Coverage"]
    links = [pl for pl in at.sidebar.get("page_link") if pl.label in set(nav_labels)]
    assert [pl.label for pl in links] == nav_labels, "nav order must match the rail's own order"
    labels = {pl.label: pl.page for pl in links}
    for key, label in ui_module.PRIMARY_NAV + ui_module.SYSTEM_NAV:
        assert labels[label] == key


def test_filings_is_not_in_the_rail_but_is_still_a_registered_route():
    """Filings (radar_inbox) left the rail in item 1 because the six-item
    nav has no slot for it and item 2 is what merges it into the Feed.
    It must still be a real registered route — the command palette's
    "Open Filings" action is the interim path to it — so this asserts the
    route survived the move rather than the page being dropped."""
    at = _run_rail()
    assert "Filings" not in [pl.label for pl in at.sidebar.get("page_link")]
    assert ("radar_inbox", "Filings") in ui_module.HIDDEN_FROM_NAV


def test_count_badges_render_only_when_backed_by_a_real_positive_count():
    with_counts = _run_rail(badges={"daily_news": 5})
    assert '<span class="er-rail-count">5</span>' in _sidebar_markdown(with_counts)
    without = _run_rail(badges={})
    assert not any("er-rail-count" in m for m in _sidebar_markdown(without))


def test_refresh_block_is_omitted_without_durable_scan_data_and_shown_with_it():
    absent = _run_rail(refresh=None)
    assert not any("er-rail-status-block" in m for m in _sidebar_markdown(absent))
    present = _run_rail(refresh="Filings refreshed 20:18 EDT")
    block = next(m for m in _sidebar_markdown(present) if "er-rail-status-block" in m)
    assert "Live data" in block and "Filings refreshed 20:18 EDT" in block


def test_refresh_label_is_none_on_the_json_backend():
    # No worker scan status exists on the JSON backend — never a render-time stamp.
    assert ui_module._latest_filings_refresh_label() is None


def test_account_control_still_renders_last_with_the_not_signed_in_state():
    at = _run_rail()
    md = _sidebar_markdown(at)
    # The group labels this used to anchor on are gone (item 1), so the
    # account block's "renders last" contract is asserted directly: it is
    # the final markdown the sidebar emits.
    # (The block closes with its own "</div>", so check the opener's
    # position rather than the very last string.)
    assert md.index('<div class="er-rail-account">') == len(md) - 2, md[-4:]
    popovers = at.sidebar.get("popover")
    assert len(popovers) == 1 and popovers[0].proto.popover.label == "?"
    assert "Not signed in" in [c.value for c in at.sidebar.get("caption")]
    assert not any("er-rail-identity" in m for m in md)


def test_topbar_breadcrumb_comes_from_the_real_route_and_carries_the_eastern_date():
    from src.logic.formatting import today_local

    with patch.object(ui_module, "_nav_badge_counts", return_value={}), \
         patch.object(ui_module, "_latest_filings_refresh_label", return_value=None):
        at = AppTest.from_file(str(_DASHBOARD), default_timeout=15)
        at.run()
    assert not at.exception, at.exception
    topbar = next(m.value for m in at.main.get("markdown") if 'class="er-topbar"' in m.value)
    # "Today" since item 1 relabelled the dashboard route; the breadcrumb
    # group is still Workspace (PRIMARY_NAV -> Workspace in
    # _BREADCRUMB_GROUPS), and the route key is still "dashboard".
    assert "<span>Workspace</span>" in topbar and '<span class="er-crumb-current">Today</span>' in topbar
    today = today_local()
    assert f"{today:%b} {today.day}, {today.year}" in topbar


def test_breadcrumb_labels_cover_every_registered_nav_key():
    for key, label in ui_module.PRIMARY_NAV + ui_module.SYSTEM_NAV + ui_module.HIDDEN_FROM_NAV:
        assert ui_module._BREADCRUMB_LABELS[key] == label
    assert ui_module._BREADCRUMB_GROUPS["admin_users"] == "Admin"
    assert ui_module._BREADCRUMB_GROUPS["coverage"] == "System"


def test_every_nav_item_has_its_own_line_icon():
    """A nav key with no `.st-key-navitem-{key} ... a::before` rule is
    not iconless: the shared ::before paints a 16x16 box from
    background-color with no mask, so it renders as a SOLID SQUARE next
    to the line icons on every other row. Companies and Themes shipped
    that way in the six-item nav.

    Also checks each icon really is a line icon — stroke-only, no fill —
    so a filled glyph cannot slip in and read as another solid block."""
    import re
    from pathlib import Path

    css = (Path(__file__).parent.parent / "assets" / "styles.css").read_text(encoding="utf-8")
    keys = [k for k, _ in ui_module.PRIMARY_NAV + ui_module.SYSTEM_NAV]
    for key in keys:
        rule = f'[class*="st-key-navitem-{key}"] [data-testid="stPageLink"] a::before'
        assert rule in css, f"{key} has no icon and renders as a solid square"
        body = css.split(rule, 1)[1].split("}", 1)[0]
        assert "mask-image" in body, key
        assert "fill%3D%22none%22" in body, f"{key}'s icon is not stroke-only"
        assert "stroke%3D%22currentColor%22" in body, key
        # An exact-class selector (.st-key-navitem-{key}) stops matching
        # the instant the item becomes the current page, because the
        # active container's key is "navitem-{key}-active" — a different
        # class token. That is how every active item came to paint a
        # solid accent square instead of its icon.
        assert f'.st-key-navitem-{key} [data-testid="stPageLink"] a::before' not in css, (
            f"{key}'s icon uses an exact-class selector and will vanish when active"
        )
