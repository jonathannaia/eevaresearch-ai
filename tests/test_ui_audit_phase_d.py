"""UI/product audit Phase D (design/DECISIONS.md) — "calm editorial
research website" pass on Themes, Signals, Coverage, and the sidebar:
concise theme headers before tab content, an explanatory empty state (plus
a clearly display-only example card) for Signals, a Coverage table that
doesn't show an all-empty column, and readable sentence-case sidebar
chrome. Every test here is a pure rendering/content/order check via
AppTest, or a direct unit check on a pure function — no data loading,
navigation, auth, worker, or database code is touched by this phase, and
none of that is exercised here."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.models.issuer import CoverageState, Issuer, LifecycleState

HARNESS_DIR = Path(__file__).parent / "apptest_pages"
REPO_ROOT = Path(__file__).parent.parent


def _flatten(node, out: list[tuple[str, str | None]]) -> None:
    cls = type(node).__name__
    if cls not in ("Block", "SpecialBlock", "Column", "UnknownElement"):
        out.append((cls, getattr(node, "label", None) or getattr(node, "value", None)))
    children = getattr(node, "children", None)
    if children:
        for k in sorted(children.keys(), key=lambda x: (isinstance(x, str), x)):
            _flatten(children[k], out)


def _ordered(at) -> list[tuple[str, str | None]]:
    out: list[tuple[str, str | None]] = []
    _flatten(at.main, out)
    return out


def _text_excluding_stylesheet(at) -> str:
    return " ".join(m.value for m in at.markdown if not m.value.startswith("<style>"))


# ============================== SIGNALS ==============================

def _run_signals_empty():
    tmp_path = Path(tempfile.mkdtemp())
    settings = Settings(cache_dir=tmp_path)
    with patch("src.data_access.container.get_settings", return_value=settings):
        at = AppTest.from_file(str(HARNESS_DIR / "signals_page.py"), default_timeout=15)
        at.run()
    return at


def test_signals_empty_state_explains_what_a_signal_is_and_offers_next_actions():
    at = _run_signals_empty()
    assert not at.exception
    all_text = _text_excluding_stylesheet(at)
    assert "No eligible signals yet" in all_text
    assert "Signals appear when a filing matches a tracked theme and meets the confidence threshold" in all_text
    # Internal implementation detail should not leak into this primary
    # empty state's own explanatory copy.
    for internal_detail in ("worker", "pipeline", "scan_service", "candidate_store"):
        assert internal_detail not in all_text.lower()


def test_signals_empty_state_actions_are_wired_at_source_level():
    """Same isolated-harness get_page() limitation as Themes above."""
    source = (REPO_ROOT / "src" / "ui" / "pages" / "signals.py").read_text(encoding="utf-8")
    assert 'action_label="Review Radar Inbox →"' in source
    assert 'action_page=get_page("radar_inbox")' in source
    assert 'action_label_2="Explore Themes →"' in source
    assert 'action_page_2=get_page("themes")' in source


def test_signals_example_card_is_unmistakably_labeled_and_uses_fictional_content():
    at = _run_signals_empty()
    all_text = _text_excluding_stylesheet(at)
    assert "What a Signal looks like" in all_text
    assert ":gray-badge[Example]" in all_text  # demo_badge()'s own st.badge serialization
    assert "Example — not a live result" in all_text
    assert "Fictional Robotics Co." in all_text
    # Doesn't read as, or borrow copy from, any real filing/company
    # elsewhere in the app.
    for real_marker in ("SK Hynix", "Marvell", "Advanced Micro Devices", "AMD", "SEC EDGAR", "OpenDART"):
        assert real_marker not in all_text


def test_signals_example_card_never_touches_repository_filters_or_counts():
    """Static proof of decoupling, not just an observed absence: the
    example-card function takes no arguments and its source never
    references the signal repository, filter state, or the "N of M
    signals shown" count line — it cannot influence any of them because
    it has no path to reach them."""
    source = (REPO_ROOT / "src" / "ui" / "pages" / "signals.py").read_text(encoding="utf-8")
    start = source.index("def _render_example_signal_card")
    end = source.index("\ndef render(")
    body = source[start:end]
    assert "def _render_example_signal_card() -> None:" in body
    # Strip the function's own docstring first — it explains this safety
    # property in prose (using these same words), which would otherwise
    # false-positive against a check for them in the actual code.
    docstring_end = body.index('"""', body.index('"""') + 3) + 3
    code_only = body[docstring_end:]
    for forbidden in ("signal_repository", "ctx.", "filtered", "get_all_signals", "signals_shown", "st.session_state"):
        assert forbidden not in code_only

    at = _run_signals_empty()
    all_text = _text_excluding_stylesheet(at)
    # The "N of M signals shown" caption only renders past the early
    # return in the empty branch — proof the example card didn't get
    # counted into it (it isn't even reached).
    assert "signals shown" not in all_text


# ============================== COVERAGE ==============================

def _issuer(supply_chain_layers=()) -> Issuer:
    return Issuer(
        issuer_id="test-issuer-1", legal_name="Test Issuer Co.", country_or_jurisdiction="United States",
        coverage_state=CoverageState.SEED, lifecycle_state=LifecycleState.ACTIVE,
        primary_ticker="TEST", primary_exchange="NASDAQ", themes=("ai-buildout",),
        supply_chain_layers=supply_chain_layers,
    )


def test_seed_row_omits_layer_column_when_excluded_and_includes_it_when_not():
    from src.ui.pages.coverage import _seed_row

    without_layer = _seed_row(_issuer(), include_layer_column=False)
    assert "Layer(s)" not in without_layer

    with_layer = _seed_row(_issuer(supply_chain_layers=("compute-accelerators",)), include_layer_column=True)
    assert with_layer["Layer(s)"] == "compute-accelerators"
    # Same relative column position either way (Theme(s) then Layer(s)
    # then Coverage) — dict insertion order is what st.dataframe uses.
    keys = list(with_layer.keys())
    assert keys.index("Theme(s)") < keys.index("Layer(s)") < keys.index("Coverage")


def test_coverage_page_hides_empty_layer_column_with_a_quiet_note():
    at = AppTest.from_file(str(HARNESS_DIR / "coverage_page.py"), default_timeout=15)
    at.run()
    assert not at.exception
    seed_table = at.dataframe[0].value
    assert "Layer(s)" not in seed_table.columns
    captions = [c.value for c in at.caption]
    assert "Layer assignments aren't available for the current issuer set yet." in captions
    # Live-count expander labels are untouched by this phase.
    expander_titles = {e.label for e in at.expander}
    assert "25 discovery proposals — not active coverage" in expander_titles
    assert "4 known category conflicts" in expander_titles


# ============================== SIDEBAR ==============================
#
# The two Phase D regression tests that used to live here (asserting the
# separate "My watchlists" sidebar group header stayed sentence-case)
# tested a UI element that no longer exists: the navigation-cleanup pass
# (design/DECISIONS.md) promoted Watchlists into a primary WORKSPACE
# destination and retired that separate group header as a now-duplicate
# label — see tests/test_navigation.py and tests/test_ui_audit_navigation_cleanup.py
# for the current sidebar-structure regression coverage.
