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




# --- Home: hero + capability list + one primary CTA ---
# (Homepage rewrite, Batch 1, design/DECISIONS.md — supersedes the
# earlier Phase A "hero + 3-step" content; the 3-step "how to use it"
# section was removed and replaced with a precise capability list.
# Homepage correction, design/DECISIONS.md: the standalone Fact/
# Interpretation/Inference/Uncertainty "How claims are labeled" block
# this rewrite originally added was itself removed by product decision —
# Eeva does not display or market visible claim labels on Home. The
# underlying claim-type model, evidence_chip component, and Methodology's
# own "The four labels" section are unaffected; this is Home-copy-only.)

def test_home_page_no_longer_shows_a_capability_list():
    """Home is a redirect now, not a landing page — the capability list it
    used to carry duplicated About, which is where the surviving copy
    lives. See tests/test_root_route_redirect.py for the redirect itself."""
    at = AppTest.from_file(str(HARNESS_DIR / "home_page.py"), default_timeout=10)
    at.run()
    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "What Eeva does today" not in all_text
    assert "Cross-market primary sources" not in all_text


def test_home_page_does_not_show_claim_labels():
    at = AppTest.from_file(str(HARNESS_DIR / "home_page.py"), default_timeout=10)
    at.run()
    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "How claims are labeled" not in all_text
    assert "Fact" not in all_text
    assert "Interpretation" not in all_text
    assert "Inference" not in all_text
    assert "Uncertainty" not in all_text


def test_home_page_has_no_cta_at_all():
    """The "Explore the research →" CTA is gone with the landing page: the
    root now opens the research app directly, so there is nothing to
    invite the reader into."""
    source = (REPO_ROOT / "src" / "ui" / "pages" / "home.py").read_text(encoding="utf-8")
    # The module docstring names the retired CTA to explain what went and
    # why; the assertion is about code.
    code = source.split('"""', 2)[2] if source.count('"""') >= 2 else source
    assert "Explore the research" not in code
    assert "page_link" not in code


def test_home_page_no_longer_duplicates_evidence_legend_theme_grid_or_limits():
    at = AppTest.from_file(str(HARNESS_DIR / "home_page.py"), default_timeout=10)
    at.run()
    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "Every claim carries a label" not in all_text
    assert "Five themes" not in all_text
    assert "What this tool does not do" not in all_text
    assert "It does not give financial advice." not in all_text


def test_home_page_carries_no_disclaimer_link_or_in_page_anchor():
    """Disclaimer stays reachable from Methodology's cross-link and the
    page footer, unchanged; it simply no longer has a second entry point
    on a page that renders nothing."""
    at = AppTest.from_file(str(HARNESS_DIR / "home_page.py"), default_timeout=10)
    at.run()
    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert 'id="what-this-tool-wont-do"' not in all_text
    assert "#what-this-tool-wont-do" not in all_text
    source = (REPO_ROOT / "src" / "ui" / "pages" / "home.py").read_text(encoding="utf-8")
    assert 'get_page("disclaimer")' not in source
