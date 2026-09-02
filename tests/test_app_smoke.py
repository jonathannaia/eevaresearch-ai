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
    "research_page.py",
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
    # Demo status lives in the sidebar (see test_sidebar_status_renders), not
    # per-page — with_chrome only guarantees the footer.
    at = AppTest.from_file(str(HARNESS_DIR / harness_file), default_timeout=10)
    at.run()
    all_html = " ".join(m.value for m in at.markdown)
    assert "Not investment advice" in all_html
    assert "does not provide investment advice" not in all_html


def test_company_page_receives_demo_symbol_via_query_params():
    at = AppTest.from_file(str(HARNESS_DIR / "company_page.py"), default_timeout=10)
    at.query_params["symbol"] = "DEMO"
    at.run()
    assert not at.exception
    all_markdown = " ".join(m.value for m in at.markdown)
    assert "Nova Aperture Systems" in all_markdown


def test_company_page_unknown_symbol_shows_empty_state_not_exception():
    at = AppTest.from_file(str(HARNESS_DIR / "company_page.py"), default_timeout=10)
    at.query_params["symbol"] = "NOTREAL"
    at.run()
    assert not at.exception
    all_markdown = " ".join(m.value for m in at.markdown)
    assert "No ticker found" in all_markdown


def test_company_page_shows_demo_evidence_with_no_fabricated_source():
    at = AppTest.from_file(str(HARNESS_DIR / "company_page.py"), default_timeout=10)
    at.query_params["symbol"] = "DEMO"
    at.run()
    assert not at.exception
    all_markdown = " ".join(m.value for m in at.markdown)
    assert "EevaResearch Demo Data" in all_markdown
    assert "no external source" in all_markdown


def test_sidebar_status_renders():
    at = AppTest.from_file(str(HARNESS_DIR / "sidebar_rail.py"), default_timeout=10)
    at.run()
    assert not at.exception
    all_html = " ".join(m.value for m in at.markdown)
    assert "EevaResearch" in all_html
    assert "Demo environment" in all_html
    # Every visible WORKSPACE + SYSTEM nav item renders as a real
    # st.page_link (navigation-cleanup pass, design/DECISIONS.md) — a
    # subset check, not exact equality, since the sidebar also renders a
    # few other page_links (the brand/home link, the footer's Disclaimer
    # link) that aren't part of either nav table.
    nav_link_labels = {pl.label for pl in at.get("page_link")}
    expected = {label for _, label in PRIMARY_NAV + SYSTEM_NAV}
    missing = expected - nav_link_labels
    assert not missing, f"missing nav items: {missing}"


def test_watchlists_page_renders_without_exception():
    # A primary Workspace nav destination (navigation-cleanup pass,
    # design/DECISIONS.md) — the add-a-ticker entry point independent of
    # any specific company page.
    at = AppTest.from_file(str(HARNESS_DIR / "watchlists_page.py"), default_timeout=10)
    at.run()
    assert not at.exception


