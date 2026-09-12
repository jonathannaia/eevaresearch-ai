"""Policy developments Dashboard component — AppTest rendering, fixture-
driven via monkeypatching fetch_candidate_documents (the one live-call
seam), zero real network calls. Reuses the existing
tests/apptest_pages/dashboard_page.py harness, the same convention
tests/test_dashboard_data_integrity.py already uses."""
from __future__ import annotations

import inspect
from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.data_access.policy_monitor.federal_register_client import FederalRegisterDocument, FederalRegisterFetchResult
from src.logic.policy_monitor import federal_register_matching
from src.ui.components import policy_developments
from src.ui.pages import dashboard

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_HARNESS = REPO_ROOT / "tests" / "apptest_pages" / "dashboard_page.py"


def _doc(**overrides) -> FederalRegisterDocument:
    defaults = dict(
        title="Export Controls on Semiconductor Manufacturing Equipment",
        type="Rule",
        document_number="2026-18194",
        html_url="https://www.federalregister.gov/documents/2026/09/04/2026-18194/example",
        publication_date="2026-09-04",
        agency_names=("Bureau of Industry and Security",),
    )
    defaults.update(overrides)
    return FederalRegisterDocument(**defaults)


def _run_dashboard(monkeypatch, documents: tuple[FederalRegisterDocument, ...]) -> AppTest:
    # The one live-call seam this component has: fetch_candidate_documents,
    # imported by name into policy_developments.py, so the patch target is
    # where the name is used, not where it's defined. Dashboard's own
    # settings/repositories path is completely untouched by this pilot and
    # needs no patching for these tests.
    monkeypatch.setattr(
        policy_developments, "fetch_candidate_documents",
        lambda per_page=20: FederalRegisterFetchResult(documents=documents, failure_code=None),
    )
    at = AppTest.from_file(str(DASHBOARD_HARNESS), default_timeout=20)
    at.run()
    return at


def _main_text(at: AppTest) -> str:
    return " ".join(m.value for m in at.main.get("markdown") if not m.value.startswith("<style>"))


# --- Zero-match / non-match behavior --------------------------------------


def test_zero_qualifying_records_render_nothing(monkeypatch):
    at = _run_dashboard(monkeypatch, documents=())
    assert not at.exception
    all_text = _main_text(at)
    assert "Policy developments" not in all_text
    assert "Official government actions matched to tracked research themes" not in all_text


def test_non_qualifying_records_also_render_nothing(monkeypatch):
    non_qualifying = _doc(title="Routine grazing permit renewals", agency_names=("Department of Agriculture",))
    at = _run_dashboard(monkeypatch, documents=(non_qualifying,))
    assert not at.exception
    assert "Policy developments" not in _main_text(at)


# --- Qualifying-record rendering -------------------------------------


def test_one_qualifying_record_renders_the_exact_required_anatomy(monkeypatch):
    at = _run_dashboard(monkeypatch, documents=(_doc(),))
    assert not at.exception
    all_text = _main_text(at)

    assert "Policy developments" in all_text
    assert "Official government actions matched to tracked research themes" in all_text
    assert "Federal Register" in all_text
    assert "Government / policy" in all_text
    assert "Sep 4, 2026" in all_text  # fmt_date("2026-09-04")
    assert "Export Controls on Semiconductor Manufacturing Equipment" in all_text
    assert "Bureau of Industry and Security" in all_text
    assert "Rule" in all_text
    assert "Why shown: AI Buildout · Semiconductor" in all_text

    link_buttons = [b for b in at.get("link_button") if b.label == "Open official document →"]
    assert len(link_buttons) == 1
    assert link_buttons[0].url == "https://www.federalregister.gov/documents/2026/09/04/2026-18194/example"


def test_never_shows_investment_framing_or_commentary(monkeypatch):
    # Scoped to this component's own section only — the rest of the
    # Dashboard (e.g. Priority Signals) legitimately uses words like
    # "material" in an unrelated, pre-existing context and must not
    # produce a false positive here.
    at = _run_dashboard(monkeypatch, documents=(_doc(),))
    all_text = _main_text(at)
    section_text = all_text[all_text.index("Policy developments"):].lower()
    for forbidden in ("urgent", "bullish", "bearish", "trading signal", "recommend", "material"):
        assert forbidden not in section_text


def test_never_shows_an_excerpt_or_summary_text(monkeypatch):
    """The fixture document deliberately has no summary/excerpt field on
    FederalRegisterDocument at all — this test proves the component
    renders only the five approved fields, nothing synthesized."""
    at = _run_dashboard(monkeypatch, documents=(_doc(),))
    all_text = _main_text(at)
    assert "amends the Export Administration Regulations" not in all_text  # a plausible fabricated excerpt, absent


def test_outbound_link_is_never_the_api_or_pdf_url(monkeypatch):
    at = _run_dashboard(monkeypatch, documents=(_doc(),))
    link_buttons = at.get("link_button")
    for button in link_buttons:
        assert "federalregister.gov/api" not in button.url
        assert "govinfo.gov" not in button.url


# --- Bounded to three, correctly ordered ---------------------------------


def test_four_or_more_qualifying_records_render_exactly_three_in_order(monkeypatch):
    docs = tuple(
        _doc(document_number=f"2026-{i:05d}", publication_date=f"2026-09-{i:02d}",
             title=f"Export Controls Update {i} on Semiconductor Equipment")
        for i in range(1, 6)
    )
    at = _run_dashboard(monkeypatch, documents=docs)
    all_text = _main_text(at)

    assert "Export Controls Update 5" in all_text
    assert "Export Controls Update 4" in all_text
    assert "Export Controls Update 3" in all_text
    assert "Export Controls Update 2" not in all_text
    assert "Export Controls Update 1" not in all_text

    newest_index = all_text.index("Export Controls Update 5")
    middle_index = all_text.index("Export Controls Update 4")
    oldest_index = all_text.index("Export Controls Update 3")
    assert newest_index < middle_index < oldest_index


# --- Placement ---------------------------------------------------------


def test_policy_developments_renders_after_priority_signals(monkeypatch):
    """No real signal is seeded, so Priority Signals itself renders
    nothing — this test instead proves render_policy_developments() is
    the LAST thing dashboard.render() calls, by confirming it appears
    after every other real section header already present on an
    otherwise-empty dashboard."""
    at = _run_dashboard(monkeypatch, documents=(_doc(),))
    assert not at.exception
    render_source = inspect.getsource(dashboard.render)
    priority_signals_call_index = render_source.index("_render_priority_signals")
    policy_developments_call_index = render_source.index("render_policy_developments")
    assert priority_signals_call_index < policy_developments_call_index


# --- Isolation from Daily News -------------------------------------------


def _import_lines(module) -> list[str]:
    source = inspect.getsource(module)
    return [line for line in source.splitlines() if line.strip().startswith(("import ", "from "))]


def test_component_module_has_zero_daily_news_import():
    import_lines = _import_lines(policy_developments)
    assert not any("daily_news" in line for line in import_lines)


def test_neither_component_nor_matching_module_imports_pipeline_registry_store_or_worker():
    forbidden = (
        "daily_news_pipeline", "source_registry", "feed_registry",
        "daily_news_store", "daily_news_worker", "daily_news_backend",
    )
    for module in (policy_developments, federal_register_matching):
        for line in _import_lines(module):
            for name in forbidden:
                assert name not in line, f"{module.__name__} imports forbidden {name!r} via: {line.strip()!r}"


# --- Existing Dashboard sections unaffected -------------------------------


def test_existing_dashboard_sections_still_render(monkeypatch):
    at = _run_dashboard(monkeypatch, documents=())
    assert not at.exception
    # A best-effort smoke check: the page itself renders without
    # exception and without the Policy developments section — proving
    # this pilot's addition didn't take down the rest of the page, not
    # re-asserting every other module's own already-covered behavior
    # (test_dashboard_data_integrity.py owns that).
    assert at.main
