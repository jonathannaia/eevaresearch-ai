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
    ui_module._has_published_themes.clear()
    ui_module._published_theme_count.clear()
    yield
    ui_module._has_published_themes.clear()
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


def test_sidebar_groups_render_in_order_and_workspace_items_stay_real_page_links():
    at = _run_rail()
    md = _sidebar_markdown(at)
    assert md[0] == '<div class="er-rail-brand">'
    assert md.index('<div class="er-rail-group-label">Workspace</div>') < md.index('<div class="er-rail-group-label">System</div>')
    labels = {pl.label: pl.page for pl in at.sidebar.get("page_link")}
    for key, label in ui_module.PRIMARY_NAV + ui_module.SYSTEM_NAV:
        assert labels[label] == key


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
    assert md.index('<div class="er-rail-account">') > md.index('<div class="er-rail-group-label">System</div>')
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
    assert "<span>Workspace</span>" in topbar and '<span class="er-crumb-current">Dashboard</span>' in topbar
    today = today_local()
    assert f"{today:%b} {today.day}, {today.year}" in topbar


def test_breadcrumb_labels_cover_every_registered_nav_key():
    for key, label in ui_module.PRIMARY_NAV + ui_module.SYSTEM_NAV + ui_module.HIDDEN_FROM_NAV:
        assert ui_module._BREADCRUMB_LABELS[key] == label
    assert ui_module._BREADCRUMB_GROUPS["admin_users"] == "Admin"
    assert ui_module._BREADCRUMB_GROUPS["coverage"] == "System"
