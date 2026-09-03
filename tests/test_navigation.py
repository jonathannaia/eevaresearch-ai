"""Focused app-level routing regression tests, against app.py's real
`st.navigation` entry point — not the isolated per-page harnesses in
tests/apptest_pages/ (those prove a page renders in isolation; these prove
something about the *routing/registration* layer itself).

Context (see design/DECISIONS.md's navigation-bug-repair entry): a live
browser reproduction found that `st.Page` objects — and the
`with_chrome(...)` closures wrapped inside them — were rebuilt from scratch
on every single script rerun. That is directly observable here: two
consecutive `AppTest.run()` calls, both landing in the same "is dashboard
default" phase, used to hand back a *different* Python object for the same
logical page each time. `app.py`'s `_build_pages` is now wrapped in
`st.cache_resource`, keyed only on `dashboard_is_default`, specifically so
this is no longer true.

AppTest limitation (confirmed via `inspect.signature`/docstring on this
Streamlit version): `AppTest.switch_page()` only supports *file-based*
pages ("a path of the page to switch to, relative to the main script's
location"). This app's pages are registered as `st.Page(callable, ...)`
around ordinary Python functions, not separate page files, so
`switch_page()` does not apply here — AppTest has no way to simulate a
user clicking a specific `st.page_link` and landing on that target page.
What CAN be verified through the real entry point: that `app.py` runs
clean and lands on its expected default page, that every registered page's
`st.Page` object stays identical across reruns within the same
default-phase (the property the cache fix establishes), and — via the
existing tests/apptest_pages/*.py harnesses, which call the exact same
`with_chrome(render_fn, key)` callables app.py registers — that every
route's own render path is exception-free. Live click-through verification
(a real browser actually clicking each sidebar link) was performed
separately during development; it is out of reach for this repo's existing
AppTest-only convention and is not simulated here.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.ui.ui import HIDDEN_FROM_NAV, PRIMARY_NAV, SYSTEM_NAV

APP_PATH = Path(__file__).parent.parent / "app.py"

_ALL_REGISTERED_KEYS = ["home"] + [k for k, _ in PRIMARY_NAV + SYSTEM_NAV + HIDDEN_FROM_NAV] + [
    "disclaimer", "daily_news_admin", "research_cases", "theme_workspace",
]


@pytest.fixture(autouse=True)
def _guard_against_live_calls(monkeypatch):
    """Belt-and-suspenders: nothing in this file clicks a scan/process
    button (AppTest can't simulate the page_link click these tests are
    about anyway), but every live-call boundary is monkeypatched to raise
    if anything ever does reach it, so this suite can never make a real
    network call."""

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("Test attempted a live call — this suite must stay network-free.")

    for module_path, attr in [
        ("src.data_access.dart.radar_service", "run_scan"),
        ("src.data_access.dart.radar_service", "process_candidate_now"),
        ("src.data_access.edgar.edgar_service", "run_scan"),
        ("src.data_access.edgar.edgar_service", "process_candidate_now"),
        ("src.data_access.edinet.edinet_service", "run_scan"),
        ("src.data_access.edinet.edinet_service", "process_candidate_now"),
    ]:
        monkeypatch.setattr(f"{module_path}.{attr}", _forbidden, raising=True)


def test_app_entry_point_runs_without_exception():
    at = AppTest.from_file(str(APP_PATH), default_timeout=15)
    at.run()
    assert not at.exception


def test_app_lands_on_home_on_first_visit_then_dashboard_thereafter():
    # `st.Page` doesn't expose `default` publicly on this Streamlit version
    # (only the private `_default`) — used here only to assert this app's
    # own registration behavior, not as a documented public API.
    at = AppTest.from_file(str(APP_PATH), default_timeout=15)
    at.run()  # first-ever run of this session: home is default
    pages = at.session_state["_pages"]
    assert pages["home"]._default is True
    assert pages["dashboard"]._default is False

    at.run()  # every rerun after the first: dashboard takes over as default
    pages = at.session_state["_pages"]
    assert pages["home"]._default is False
    assert pages["dashboard"]._default is True


def test_every_registered_page_key_present_with_no_change_to_labels_or_order():
    """Regression guard for the navigation-cleanup pass (design/DECISIONS.md)
    — asserts the exact visible WORKSPACE/SYSTEM nav tables app.py reads
    from, that Coverage/Signals/Methodology/About are still registered
    (just no longer linked from any visible sidebar group), and that
    every expected dict key exists post-registration."""
    assert [k for k, _ in PRIMARY_NAV] == ["dashboard", "radar_inbox", "themes", "daily_news"]
    assert [label for _, label in PRIMARY_NAV] == ["Dashboard", "Filings", "Themes", "Daily News"]
    assert [k for k, _ in SYSTEM_NAV] == ["coverage"]
    assert [label for _, label in SYSTEM_NAV] == ["Methodology & Coverage"]
    # Evidence-First Themes MVP (design/DECISIONS.md): "themes" moved
    # from here into PRIMARY_NAV above (now the new public research
    # page). The legacy demo ticker/theme/subtheme browser that used to
    # occupy this slot, and Watchlists/Research (canned-demo-answer
    # chat)/Company, were removed entirely (reader-facing data-integrity
    # pass, design/DECISIONS.md) rather than kept as hidden routes — none
    # had any live real data of their own.
    assert [k for k, _ in HIDDEN_FROM_NAV] == ["signals", "methodology", "about"]

    at = AppTest.from_file(str(APP_PATH), default_timeout=15)
    at.run()
    pages = at.session_state["_pages"]
    assert set(pages.keys()) == set(_ALL_REGISTERED_KEYS)


def test_page_objects_stay_identical_across_reruns_within_the_same_default_phase():
    """The actual regression test for the navigation-bug-repair fix: before
    it, `app.py` rebuilt a brand-new `st.Page` (and a brand-new wrapped
    callable) on every single rerun. `st.cache_resource` now makes
    `_build_pages` hand back the *same* objects for the same
    `dashboard_is_default` value — this asserts that stays true two reruns
    in a row, once the session is past its first visit."""
    at = AppTest.from_file(str(APP_PATH), default_timeout=15)
    at.run()  # first visit — dashboard_is_default=False phase
    at.run()  # now stably in the dashboard_is_default=True phase
    pages_a = at.session_state["_pages"]

    at.run()  # another rerun, same phase (dashboard already the default)
    pages_b = at.session_state["_pages"]

    for key in _ALL_REGISTERED_KEYS:
        assert pages_a[key] is pages_b[key], f"'{key}' page object was rebuilt across reruns in the same phase"


@pytest.mark.parametrize("harness_file", ["dashboard_page.py", "radar_inbox_page.py", "daily_news_page.py", "coverage_page.py", "signals_page.py", "methodology_page.py", "about_page.py"])
def test_every_visible_route_renders_through_its_registered_render_callable(harness_file):
    """Every harness here calls the exact same `with_chrome(render_fn, key)`
    callable app.py registers as that route's `st.Page` — not a
    reimplementation. This is the practical substitute for AppTest's
    unavailable page_link-click simulation (see module docstring): it
    confirms each route's own render path is exception-free, even though it
    can't simulate a user clicking there from another page."""
    harness_dir = Path(__file__).parent / "apptest_pages"
    at = AppTest.from_file(str(harness_dir / harness_file), default_timeout=15)
    at.run()
    assert not at.exception, f"{harness_file} raised: {at.exception}"
