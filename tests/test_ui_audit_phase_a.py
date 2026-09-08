"""UI/product audit Phase A (design/DECISIONS.md) — the five approved,
low-risk cleanups: dead-code removal, Research's permanently-unavailable
"Saved threads" expander, Company's em-dash placeholder tables, Themes'
duplicated per-subtheme boilerplate line, and Home's simplification to
hero + 3-step + one CTA. Every test here is a pure rendering/content
check via the existing AppTest harnesses — no data loading, no
navigation, no auth, no worker, no database code is touched by this
phase, and none of that is exercised here."""
from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

HARNESS_DIR = Path(__file__).parent / "apptest_pages"
REPO_ROOT = Path(__file__).parent.parent


# --- Dead-code removal ---

def test_dead_pages_and_components_are_actually_gone():
    assert not (REPO_ROOT / "src" / "ui" / "pages" / "capital_rotation.py").exists()
    assert not (REPO_ROOT / "src" / "ui" / "components" / "market_brief.py").exists()
    assert not (REPO_ROOT / "src" / "ui" / "components" / "market_pulse.py").exists()
    assert not (REPO_ROOT / "tests" / "apptest_pages" / "capital_rotation_page.py").exists()


def test_dead_pages_are_not_referenced_anywhere_in_src_or_app():
    """Guards against a stale import surviving the deletion — if anything
    still imported one of these, that file would fail to even parse/run
    long before this test, but this makes the "nothing references them"
    property explicit and load-bearing rather than incidental."""
    import ast

    forbidden_modules = {
        "src.ui.pages.capital_rotation",
        "src.ui.components.market_brief",
        "src.ui.components.market_pulse",
    }
    offenders = []
    for path in (REPO_ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in forbidden_modules:
                offenders.append(f"{path}: imports {node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in forbidden_modules:
                        offenders.append(f"{path}: imports {alias.name}")
    assert offenders == []




# --- Home: hero + 3-step + one primary CTA ---

def test_home_page_shows_exactly_three_steps():
    at = AppTest.from_file(str(HARNESS_DIR / "home_page.py"), default_timeout=10)
    at.run()
    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "Step 1" in all_text
    assert "Step 2" in all_text
    assert "Step 3" in all_text
    assert "Step 4" not in all_text


def test_home_page_has_exactly_one_open_dashboard_link():
    # get_page() returns None in this isolated per-page AppTest harness
    # (st.session_state["_pages"] is only populated by app.py's own
    # _build_pages(), which this harness deliberately doesn't run — same
    # limitation every other page_link-using page in this app already
    # has in isolation), so the actual st.page_link call never renders
    # here regardless of this fix. Checked at the source level instead:
    # exactly one "Open Dashboard" page_link call site exists in the
    # page's own code (previously two — one after the hero, one as a
    # closing CTA at the bottom of the old, longer page).
    source = (REPO_ROOT / "src" / "ui" / "pages" / "home.py").read_text(encoding="utf-8")
    assert source.count('label="Open Dashboard"') == 1


def test_home_page_no_longer_duplicates_evidence_legend_theme_grid_or_limits():
    at = AppTest.from_file(str(HARNESS_DIR / "home_page.py"), default_timeout=10)
    at.run()
    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "Every claim carries a label" not in all_text
    assert "Five themes" not in all_text
    assert "What this tool does not do" not in all_text
    assert "It does not give financial advice." not in all_text


def test_home_page_links_directly_to_disclaimer_instead_of_an_in_page_anchor():
    at = AppTest.from_file(str(HARNESS_DIR / "home_page.py"), default_timeout=10)
    at.run()
    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    # The old in-page anchor target no longer exists on this page.
    assert 'id="what-this-tool-wont-do"' not in all_text
    assert "#what-this-tool-wont-do" not in all_text
    # Same page_link-in-isolation limitation as the Dashboard-link test
    # above — checked at the source level: a real st.page_link call to
    # the "disclaimer" page (not a raw <a href="#..."> anchor) exists
    # exactly once.
    source = (REPO_ROOT / "src" / "ui" / "pages" / "home.py").read_text(encoding="utf-8")
    assert 'get_page("disclaimer")' in source
    assert source.count('label="What this tool won\'t do"') == 1
