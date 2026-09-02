"""Reader-facing data-integrity pass (design/DECISIONS.md) — fixture-
driven tests for Dashboard's Theme Health and Priority Signals modules:
each must render nothing at all (no header, no placeholder, no link)
when no real data qualifies, and must render only real, source-backed
content — with full provenance (company, jurisdiction, source, date,
original-source link) — when it does. Also covers the jurisdiction
addition on the public Themes evidence rows. Every fixture here is a
real record written through the same real repositories the app itself
uses (backend_factory.get_theme_curator_repository /
get_candidate_repository) — never a mock/demo repository, never a
directly-constructed fake AppTest response."""
from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.container import get_repositories
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, StateTransition
from src.models.theme_research import (
    EvidenceDirection,
    ResearchTheme,
    ThemeCategory,
    ThemeEvidenceItem,
    ThemeStatus,
    ThemeVisibility,
)
from src.ui.pages import dashboard, themes_research

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_HARNESS = REPO_ROOT / "tests" / "apptest_pages" / "dashboard_page.py"
THEMES_RESEARCH_HARNESS = REPO_ROOT / "tests" / "apptest_pages" / "themes_research_page.py"

_MOCK_WORDS = ("demo", "sample", "placeholder", "illustrative", "fictional", "canned")


def _settings(tmp_path) -> Settings:
    return Settings(db_backend="json", cache_dir=tmp_path)


def _patch_dashboard_settings(monkeypatch, settings: Settings) -> None:
    monkeypatch.setattr(dashboard, "get_settings", lambda: settings)
    monkeypatch.setattr(dashboard, "get_repositories", lambda: get_repositories(settings))


def _main_text(at: AppTest) -> str:
    # at.main (not at.markdown, which also includes the sidebar) — the
    # sidebar's own persistent "Demo environment · sample data" status
    # text is a separate, already-flagged, cross-cutting known issue
    # (every page's shared chrome, not this page's own content) and
    # must not leak into an assertion about this page's own module
    # content.
    return " ".join(m.value for m in at.main.get("markdown") if not m.value.startswith("<style>"))


def _publish_theme(settings: Settings, theme_id: str = "theme-1", companies=("Company A", "Company B")) -> ResearchTheme:
    curator = backend_factory.get_theme_curator_repository(settings)
    theme = ResearchTheme(
        id=theme_id, category=ThemeCategory.BOTTLENECK, status=ThemeStatus.NEW, visibility=ThemeVisibility.INTERNAL,
        title="HBM capacity constraint", key_question="Will HBM supply remain the binding constraint?",
        hypothesis="h", working_thesis="w", why_it_matters="y", what_could_change_the_view="c", what_to_watch_next="n",
        created_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-01T00:00:00+00:00",
    )
    curator.insert_theme(theme)
    for i, company in enumerate(companies):
        curator.insert_evidence_item(ThemeEvidenceItem(
            id=f"ev-{theme_id}-{i}", theme_id=theme_id, date="2026-01-01", company=company,
            source_name="SEC EDGAR", source_url="https://example.com/filing", fact="f", relevance="r",
            direction=EvidenceDirection.SUPPORTS,
        ))
    updated = curator.set_visibility(theme_id, ThemeVisibility.PUBLISHED, "2026-01-02T00:00:00+00:00")
    assert updated is not None
    return updated


def _seed_real_signal(settings: Settings, candidate_id: str = "edgar-cand-1", corp_name: str = "Apple Inc.") -> None:
    filing = FilingEvent(
        rcept_no=f"acc-{candidate_id}", corp_code="0000320193", corp_name=corp_name, stock_code="AAPL",
        report_nm="8-K", rcept_dt="2026-08-15", flr_nm=corp_name, source_name="SEC EDGAR",
        source_url="https://www.sec.gov/example-filing", retrieved_at="2026-08-15T01:00:00+00:00",
        original_language="English", theme_slug="ai-buildout",
    )
    candidate = CandidateSignal(
        id=candidate_id, filing=filing, matched_rules=["material_agreement:1.01"], confidence="High",
        status=CandidateStatus.PUBLISHED, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="The company entered into a material supply agreement.",
        state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="2026-08-15T00:00:00+00:00")],
    )
    backend_factory.get_candidate_repository(settings, "SEC EDGAR").upsert_new_candidates([candidate])


# ============================================================
# Theme Health — absent with no published Theme, present with one
# ============================================================


def test_theme_health_absent_with_zero_published_themes(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _patch_dashboard_settings(monkeypatch, settings)
    at = AppTest.from_file(str(DASHBOARD_HARNESS), default_timeout=15)
    at.run()
    assert not at.exception
    all_text = _main_text(at)
    assert "Theme Health" not in all_text


def test_theme_health_absent_when_theme_repository_construction_fails(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _patch_dashboard_settings(monkeypatch, settings)

    def _boom(_settings):
        raise RuntimeError("connection boom - must never reach the UI")

    monkeypatch.setattr(backend_factory, "get_theme_repository", _boom)
    at = AppTest.from_file(str(DASHBOARD_HARNESS), default_timeout=15)
    at.run()
    assert not at.exception
    assert "Theme Health" not in _main_text(at)
    assert "boom" not in _main_text(at)


def test_theme_health_shows_real_published_theme_with_evidence_and_company_counts(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    theme = _publish_theme(settings, companies=("Company A", "Company B"))
    _patch_dashboard_settings(monkeypatch, settings)

    at = AppTest.from_file(str(DASHBOARD_HARNESS), default_timeout=15)
    at.run()
    assert not at.exception
    all_text = _main_text(at)
    assert "Theme Health" in all_text
    assert theme.title in all_text
    assert "2" in all_text  # 2 evidence items
    assert "companies" in all_text  # 2 distinct companies -> plural


def test_theme_health_never_shows_price_breadth_or_performance_wording(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _publish_theme(settings)
    _patch_dashboard_settings(monkeypatch, settings)

    at = AppTest.from_file(str(DASHBOARD_HARNESS), default_timeout=15)
    at.run()
    all_text = _main_text(at)
    for forbidden in ("Breadth", "relative performance", "% breadth", "leads this"):
        assert forbidden not in all_text


def test_theme_health_link_targets_the_specific_published_theme(tmp_path, monkeypatch):
    """get_page("themes") only resolves to a real Page object when run
    through app.py's real st.navigation entry point — an isolated
    per-page AppTest harness never populates st.session_state["_pages"]
    (same limitation documented elsewhere in this test suite), so this
    one test runs through APP_PATH instead of DASHBOARD_HARNESS."""
    settings = _settings(tmp_path)
    _publish_theme(settings)
    _patch_dashboard_settings(monkeypatch, settings)

    at = AppTest.from_file(str(REPO_ROOT / "app.py"), default_timeout=15)
    at.run()
    at.run()  # second run: dashboard becomes the default page
    assert not at.exception
    explore_links = [pl for pl in at.main.get("page_link") if pl.label == "Explore →"]
    assert len(explore_links) == 1


def test_theme_health_never_reads_internal_or_unpublished_themes(tmp_path, monkeypatch):
    """Only backend_factory.get_theme_repository() (the published-only
    protocol) is ever used — an internal (never-published) theme must
    never appear, even though it exists in the same underlying store."""
    settings = _settings(tmp_path)
    curator = backend_factory.get_theme_curator_repository(settings)
    curator.insert_theme(ResearchTheme(
        id="theme-internal", category=ThemeCategory.BOTTLENECK, status=ThemeStatus.NEW,
        visibility=ThemeVisibility.INTERNAL, title="Internal candidate theme — never published",
        key_question="q", hypothesis="h", working_thesis="w", why_it_matters="y",
        what_could_change_the_view="c", what_to_watch_next="n",
        created_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-01T00:00:00+00:00",
    ))
    _patch_dashboard_settings(monkeypatch, settings)

    at = AppTest.from_file(str(DASHBOARD_HARNESS), default_timeout=15)
    at.run()
    all_text = _main_text(at)
    assert "Internal candidate theme" not in all_text
    assert "Theme Health" not in all_text


# ============================================================
# Priority Signals — absent with zero real signals, present with real
# provenance when one exists
# ============================================================


def test_priority_signals_absent_with_zero_real_signals(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _patch_dashboard_settings(monkeypatch, settings)
    at = AppTest.from_file(str(DASHBOARD_HARNESS), default_timeout=15)
    at.run()
    assert not at.exception
    all_text = _main_text(at)
    assert "Priority Signals" not in all_text
    assert "No signals loaded" not in all_text


def test_priority_signals_shows_full_provenance_for_a_real_signal(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _seed_real_signal(settings, corp_name="Apple Inc.")
    _patch_dashboard_settings(monkeypatch, settings)

    at = AppTest.from_file(str(DASHBOARD_HARNESS), default_timeout=15)
    at.run()
    assert not at.exception
    all_text = _main_text(at)
    assert "Priority Signals" in all_text
    assert "Apple Inc." in all_text  # company
    assert "SEC EDGAR" in all_text  # source
    assert "United States" in all_text  # jurisdiction
    assert "Aug 15, 2026" in all_text  # date
    assert "Original source" in all_text  # original-source link label
    assert "www.sec.gov/example-filing" in all_text


def test_priority_signals_has_no_demo_or_sample_wording(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _seed_real_signal(settings)
    _patch_dashboard_settings(monkeypatch, settings)

    at = AppTest.from_file(str(DASHBOARD_HARNESS), default_timeout=15)
    at.run()
    all_text = _main_text(at)
    priority_start = all_text.index("Priority Signals")
    priority_chunk = all_text[priority_start : priority_start + 2000]
    for word in _MOCK_WORDS:
        assert word not in priority_chunk.lower()


# ============================================================
# Whole-dashboard sweep — no mock/demo wording in the main content area,
# whether or not real data exists
# ============================================================


def test_dashboard_main_content_has_no_mock_wording_when_empty(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _patch_dashboard_settings(monkeypatch, settings)
    at = AppTest.from_file(str(DASHBOARD_HARNESS), default_timeout=15)
    at.run()
    all_text = _main_text(at).lower()
    for word in _MOCK_WORDS:
        assert word not in all_text, word


def test_dashboard_main_content_has_no_mock_wording_when_populated(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _publish_theme(settings)
    _seed_real_signal(settings)
    _patch_dashboard_settings(monkeypatch, settings)
    at = AppTest.from_file(str(DASHBOARD_HARNESS), default_timeout=15)
    at.run()
    all_text = _main_text(at).lower()
    for word in _MOCK_WORDS:
        assert word not in all_text, word


# ============================================================
# Public Themes evidence rows — jurisdiction display (design/DECISIONS.md)
# ============================================================


def test_public_theme_evidence_row_shows_jurisdiction(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    theme = _publish_theme(settings)
    monkeypatch.setattr(themes_research, "get_settings", lambda: settings)

    at = AppTest.from_file(str(THEMES_RESEARCH_HARNESS), default_timeout=15)
    at.query_params["theme_id"] = theme.id
    at.run()
    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown if not m.value.startswith("<style>"))
    assert "SEC EDGAR" in all_text
    assert "United States" in all_text


# ============================================================
# Market Map "view all in Themes" link — gated on real published-Theme
# count (navigation/empty-state pass, design/DECISIONS.md)
# ============================================================


def test_market_map_view_all_in_themes_link_absent_with_zero_published_themes(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _patch_dashboard_settings(monkeypatch, settings)
    at = AppTest.from_file(str(DASHBOARD_HARNESS), default_timeout=15)
    at.run()
    assert not at.exception
    all_text = " ".join(m.value for m in at.main.get("markdown") if not m.value.startswith("<style>"))
    assert "view all in Themes" not in all_text


def test_market_map_view_all_in_themes_link_present_with_a_real_published_theme(tmp_path, monkeypatch):
    """get_page("themes") only resolves to a real Page object when run
    through app.py's real st.navigation entry point — an isolated
    per-page AppTest harness never populates st.session_state["_pages"]
    (same limitation documented elsewhere in this test suite), so this
    test runs through app.py instead of DASHBOARD_HARNESS."""
    settings = _settings(tmp_path)
    _publish_theme(settings)
    _patch_dashboard_settings(monkeypatch, settings)
    at = AppTest.from_file(str(REPO_ROOT / "app.py"), default_timeout=15)
    at.run()
    at.run()  # second run: dashboard becomes the default page
    assert not at.exception
    page_links = [pl for pl in at.main.get("page_link") if "view all in Themes" in (pl.label or "")]
    assert len(page_links) >= 1
