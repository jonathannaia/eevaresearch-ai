"""EevaResearch — Evidence-First Themes MVP (design/DECISIONS.md).
Focused tests for src/ui/pages/themes_research.py. Every fixture is
synthetic and directly constructed; this file never touches a real
backend, the network, the authoring script, or a real current date."""
from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.data_access import theme_store
from src.models.theme_research import (
    CompanyRole,
    EvidenceDirection,
    ResearchTheme,
    ThemeCategory,
    ThemeCompanyMapEntry,
    ThemeEvidenceItem,
    ThemeStatus,
    ThemeVisibility,
)
from src.ui.pages import themes_research

REPO_ROOT = Path(__file__).parent.parent
HARNESS_PATH = REPO_ROOT / "tests" / "apptest_pages" / "themes_research_page.py"


def _theme(title="Test theme", created_at="2026-08-20T00:00:00+00:00", **overrides):
    defaults = dict(
        id=theme_store.build_theme_id(title, created_at),
        category=ThemeCategory.BOTTLENECK, status=ThemeStatus.NEW, visibility=ThemeVisibility.PUBLISHED,
        title=title, key_question="What is happening?", hypothesis="One sentence hypothesis.",
        working_thesis="The working thesis.", why_it_matters="Why it matters.",
        what_could_change_the_view="What could change.", what_to_watch_next="What to watch.",
        created_at=created_at, updated_at=created_at,
    )
    defaults.update(overrides)
    return ResearchTheme(**defaults)


def _evidence(theme_id, source_url="https://example.com/filing", date="2026-08-15", **overrides):
    defaults = dict(
        id=theme_store.build_theme_evidence_id(theme_id, source_url, date),
        theme_id=theme_id, date=date, company="Acme Corp", source_name="SEC EDGAR",
        source_url=source_url, fact="Observed fact.", relevance="Why it is relevant.",
        direction=EvidenceDirection.SUPPORTS,
    )
    defaults.update(overrides)
    return ThemeEvidenceItem(**defaults)


def _company_map_entry(theme_id, company_name="Acme Corp", role=CompanyRole.EXPOSED, **overrides):
    defaults = dict(
        id=theme_store.build_theme_company_map_id(theme_id, company_name, role),
        theme_id=theme_id, company_name=company_name, role=role, note=None,
    )
    defaults.update(overrides)
    return ThemeCompanyMapEntry(**defaults)


class _FakeRepo:
    def __init__(self, themes=(), evidence_by_theme=None, company_map_by_theme=None):
        self._themes = {t.id: t for t in themes if t.visibility == ThemeVisibility.PUBLISHED}
        self._all_themes = {t.id: t for t in themes}
        self._evidence_by_theme = evidence_by_theme or {}
        self._company_map_by_theme = company_map_by_theme or {}
        self.list_calls = 0
        self.get_calls: list[str] = []
        self.evidence_calls: list[str] = []
        self.company_map_calls: list[str] = []

    def list_published_themes(self):
        self.list_calls += 1
        return tuple(sorted(self._themes.values(), key=lambda t: (t.updated_at, t.id), reverse=True))

    def get_published_theme(self, theme_id):
        self.get_calls.append(theme_id)
        return self._themes.get(theme_id)

    def evidence_for_theme(self, theme_id):
        self.evidence_calls.append(theme_id)
        return self._evidence_by_theme.get(theme_id, ())

    def company_map_for_theme(self, theme_id):
        self.company_map_calls.append(theme_id)
        return self._company_map_by_theme.get(theme_id, ())


class _RaisingRepo:
    def list_published_themes(self):
        raise RuntimeError("boom - raw exception text that must never reach the UI")

    def get_published_theme(self, theme_id):
        raise RuntimeError("boom - raw exception text that must never reach the UI")

    def evidence_for_theme(self, theme_id):
        raise RuntimeError("unreachable")

    def company_map_for_theme(self, theme_id):
        raise RuntimeError("unreachable")


def _run_with_repo(monkeypatch, repo, theme_id=None):
    monkeypatch.setattr(themes_research.backend_factory, "get_theme_repository", lambda settings: repo)
    at = AppTest.from_file(str(HARNESS_PATH), default_timeout=15)
    if theme_id is not None:
        at.query_params["theme_id"] = theme_id
    at.run()
    return at


# ============================================================
# Empty state
# ============================================================


def test_empty_state_renders_when_zero_published_themes(monkeypatch):
    at = _run_with_repo(monkeypatch, _FakeRepo(themes=()))
    assert not at.exception
    all_html = " ".join(m.value for m in at.markdown)
    assert "No active themes yet" in all_html
    assert "Themes are published when multiple official sources" in all_html


# ============================================================
# Published-only visibility
# ============================================================


def test_published_theme_renders_in_index(monkeypatch):
    theme = _theme()
    at = _run_with_repo(monkeypatch, _FakeRepo(themes=[theme]))
    assert not at.exception
    all_html = " ".join(m.value for m in at.markdown)
    assert "Test theme" in all_html
    assert "One sentence hypothesis." in all_html


@pytest.mark.parametrize("visibility", [ThemeVisibility.INTERNAL, ThemeVisibility.READY_TO_PUBLISH, ThemeVisibility.ARCHIVED])
def test_non_published_themes_never_render_in_index(monkeypatch, visibility):
    theme = _theme(visibility=visibility)
    repo = _FakeRepo(themes=[theme])
    at = _run_with_repo(monkeypatch, repo)
    all_html = " ".join(m.value for m in at.markdown)
    assert theme.title not in all_html
    assert "No active themes yet" in all_html


@pytest.mark.parametrize("visibility", [ThemeVisibility.INTERNAL, ThemeVisibility.READY_TO_PUBLISH, ThemeVisibility.ARCHIVED])
def test_non_published_detail_id_behaves_as_not_found(monkeypatch, visibility):
    theme = _theme(visibility=visibility)

    class _RepoWithHiddenTheme(_FakeRepo):
        def get_published_theme(self, theme_id):
            self.get_calls.append(theme_id)
            return None  # a repository correctly enforcing visibility server-side

    repo = _RepoWithHiddenTheme(themes=[theme])
    at = _run_with_repo(monkeypatch, repo, theme_id=theme.id)
    assert not at.exception
    all_html = " ".join(m.value for m in at.markdown)
    assert "not found" in all_html.lower()
    assert theme.title not in all_html
    assert repo.evidence_calls == []
    assert repo.company_map_calls == []


def test_published_detail_id_renders(monkeypatch):
    theme = _theme()
    item = _evidence(theme.id)
    entry = _company_map_entry(theme.id)
    repo = _FakeRepo(themes=[theme], evidence_by_theme={theme.id: (item,)}, company_map_by_theme={theme.id: (entry,)})
    at = _run_with_repo(monkeypatch, repo, theme_id=theme.id)
    assert not at.exception
    all_html = " ".join(m.value for m in at.markdown)
    assert theme.title in all_html
    assert theme.working_thesis in all_html
    assert theme.why_it_matters in all_html
    assert theme.what_could_change_the_view in all_html
    assert theme.what_to_watch_next in all_html
    assert "Informational research only; not investment advice." in all_html


# ============================================================
# Category/status label exactness
# ============================================================


def test_only_approved_category_and_status_labels_render(monkeypatch):
    theme = _theme(category=ThemeCategory.SECOND_ORDER_EFFECT, status=ThemeStatus.MONITORING)
    at = _run_with_repo(monkeypatch, _FakeRepo(themes=[theme]))
    all_html = " ".join(m.value for m in at.markdown)
    assert "Second-order effect" in all_html
    assert "Monitoring" in all_html


# ============================================================
# Company map grouping — omit empty groups
# ============================================================


def test_company_map_groups_by_role_and_omits_empty_groups(monkeypatch):
    theme = _theme()
    exposed = _company_map_entry(theme.id, company_name="Exposed Co", role=CompanyRole.EXPOSED)
    driver = _company_map_entry(theme.id, company_name="Driver Co", role=CompanyRole.DEMAND_DRIVER)
    repo = _FakeRepo(themes=[theme], company_map_by_theme={theme.id: (exposed, driver)})
    at = _run_with_repo(monkeypatch, repo, theme_id=theme.id)
    all_html = " ".join(m.value for m in at.markdown)
    assert "Demand driver" in all_html
    assert "Exposed company" in all_html
    assert "Constraint owner" not in all_html
    assert "Enabler" not in all_html
    assert "Disconfirming force" not in all_html


# ============================================================
# Evidence ledger fields and safe source links
# ============================================================


def test_evidence_ledger_shows_all_required_fields(monkeypatch):
    theme = _theme()
    item = _evidence(theme.id, company="Specific Co", source_name="SEC EDGAR", fact="A specific fact.", relevance="A specific relevance.")
    repo = _FakeRepo(themes=[theme], evidence_by_theme={theme.id: (item,)})
    at = _run_with_repo(monkeypatch, repo, theme_id=theme.id)
    all_html = " ".join(m.value for m in at.markdown)
    assert item.date in all_html
    assert "Specific Co" in all_html
    assert "SEC EDGAR" in all_html
    assert "A specific fact." in all_html
    assert "A specific relevance." in all_html
    assert "Supports" in all_html


def test_https_and_http_source_urls_become_clickable_links(monkeypatch):
    theme = _theme()
    item = _evidence(theme.id, source_url="https://example.com/real-filing")
    repo = _FakeRepo(themes=[theme], evidence_by_theme={theme.id: (item,)})
    at = _run_with_repo(monkeypatch, repo, theme_id=theme.id)
    all_html = " ".join(m.value for m in at.markdown)
    assert 'href="https://example.com/real-filing"' in all_html

    theme2 = _theme(title="Theme2", created_at="2026-08-21T00:00:00+00:00")
    item2 = _evidence(theme2.id, source_url="http://example.com/real-filing")
    repo2 = _FakeRepo(themes=[theme2], evidence_by_theme={theme2.id: (item2,)})
    at2 = _run_with_repo(monkeypatch, repo2, theme_id=theme2.id)
    all_html2 = " ".join(m.value for m in at2.markdown)
    assert 'href="http://example.com/real-filing"' in all_html2


@pytest.mark.parametrize("unsafe_url", ["javascript:alert(1)", "data:text/html,<script>alert(1)</script>", "", "not-a-url"])
def test_unsafe_or_malformed_source_urls_are_never_clickable(monkeypatch, unsafe_url):
    theme = _theme()
    item = _evidence(theme.id, source_url=unsafe_url)
    repo = _FakeRepo(themes=[theme], evidence_by_theme={theme.id: (item,)})
    at = _run_with_repo(monkeypatch, repo, theme_id=theme.id)
    all_html = " ".join(m.value for m in at.markdown)
    assert 'href="javascript' not in all_html.lower()
    assert 'href="data:' not in all_html.lower()


# ============================================================
# HTML escaping / malicious-string injection (matches research_cases.py style)
# ============================================================


def test_script_tags_and_html_fragments_are_escaped_in_index(monkeypatch):
    theme = _theme(title="<script>alert('xss')</script>", hypothesis="</div><img onerror=alert(1) src=x>")
    at = _run_with_repo(monkeypatch, _FakeRepo(themes=[theme]))
    assert not at.exception
    all_html = " ".join(m.value for m in at.markdown)
    assert "<script>" not in all_html
    assert "<img onerror=" not in all_html
    assert "&lt;script&gt;" in all_html


def test_script_tags_and_html_fragments_are_escaped_in_detail(monkeypatch):
    theme = _theme(
        title="<script>alert(1)</script>", key_question="</div><b>bold</b>",
        working_thesis="<svg onload=alert(2)>", why_it_matters="<script>alert(3)</script>",
    )
    item = _evidence(theme.id, company="<script>alert(4)</script>", fact="<img src=x onerror=alert(5)>")
    entry = _company_map_entry(theme.id, company_name="<script>alert(6)</script>", note="<script>alert(7)</script>")
    repo = _FakeRepo(themes=[theme], evidence_by_theme={theme.id: (item,)}, company_map_by_theme={theme.id: (entry,)})
    at = _run_with_repo(monkeypatch, repo, theme_id=theme.id)
    assert not at.exception
    all_html = " ".join(m.value for m in at.markdown)
    assert "<script>" not in all_html
    assert "<svg onload=" not in all_html
    assert "<img src=x onerror=" not in all_html
    assert "&lt;/div&gt;" in all_html


# ============================================================
# No internal Research Case / Radar terminology leakage
# ============================================================


_FORBIDDEN_INTERNAL_TERMS = (
    "Research Case", "case_id", "CandidateSignal", "candidate_id", "NEEDS_REVIEW",
    "factory_rejected", "validate_research_case_bundle", "validation_rejected", "write_rejected",
)


def test_no_internal_terminology_leaks_in_index(monkeypatch):
    theme = _theme()
    at = _run_with_repo(monkeypatch, _FakeRepo(themes=[theme]))
    all_html = " ".join(m.value for m in at.markdown)
    for term in _FORBIDDEN_INTERNAL_TERMS:
        assert term not in all_html


def test_no_internal_terminology_leaks_in_detail(monkeypatch):
    theme = _theme()
    item = _evidence(theme.id)
    entry = _company_map_entry(theme.id)
    repo = _FakeRepo(themes=[theme], evidence_by_theme={theme.id: (item,)}, company_map_by_theme={theme.id: (entry,)})
    at = _run_with_repo(monkeypatch, repo, theme_id=theme.id)
    all_html = " ".join(m.value for m in at.markdown)
    for term in _FORBIDDEN_INTERNAL_TERMS:
        assert term not in all_html


def test_page_module_never_imports_research_case_or_candidate_types():
    source = (REPO_ROOT / "src" / "ui" / "pages" / "themes_research.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="themes_research.py")
    forbidden_modules = ("src.models.research_case", "src.models.models", "src.logic.research_case_validation")
    offenders = []
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for module in modules:
            if any(module == f or module.startswith(f + ".") for f in forbidden_modules):
                offenders.append(module)
    assert not offenders, offenders


def test_page_module_never_imports_curator_seam_or_authoring_script():
    """AST-based, not substring-based, so the module's own docstring
    prose explaining that it never imports these names can't
    false-positive this check the way a naive substring scan would."""
    source = (REPO_ROOT / "src" / "ui" / "pages" / "themes_research.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="themes_research.py")
    forbidden_names = ("get_theme_curator_repository", "create_theme", "insert_theme", "append_theme", "set_theme_visibility")

    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)

    used_names = {
        n.id for n in ast.walk(tree) if isinstance(n, ast.Name)
    } | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }

    for name in forbidden_names:
        assert name not in imported_names
        assert name not in used_names


# ============================================================
# Failure handling
# ============================================================


def test_backend_error_on_index_renders_restrained_message(monkeypatch):
    at = _run_with_repo(monkeypatch, _RaisingRepo())
    assert not at.exception
    all_html = " ".join(m.value for m in at.markdown)
    assert "temporarily unavailable" in all_html.lower()
    assert "boom" not in all_html


def test_backend_error_on_detail_renders_restrained_message(monkeypatch):
    at = _run_with_repo(monkeypatch, _RaisingRepo(), theme_id="theme-1")
    assert not at.exception
    all_html = " ".join(m.value for m in at.markdown)
    assert "temporarily unavailable" in all_html.lower()
    assert "boom" not in all_html


def test_repository_construction_failure_renders_restrained_message(monkeypatch):
    def _boom(settings):
        raise RuntimeError("connection boom - must never reach the UI")

    monkeypatch.setattr(themes_research.backend_factory, "get_theme_repository", _boom)
    at = AppTest.from_file(str(HARNESS_PATH), default_timeout=15)
    at.run()
    assert not at.exception
    all_html = " ".join(m.value for m in at.markdown)
    assert "temporarily unavailable" in all_html.lower()
    assert "boom" not in all_html


def test_missing_theme_makes_no_evidence_or_company_map_reads(monkeypatch):
    repo = _FakeRepo(themes=[])
    at = _run_with_repo(monkeypatch, repo, theme_id="theme-does-not-exist")
    assert not at.exception
    assert repo.evidence_calls == []
    assert repo.company_map_calls == []


def test_mismatched_theme_id_records_are_never_displayed(monkeypatch):
    theme = _theme()
    matching_item = _evidence(theme.id, source_url="https://example.com/matching")
    mismatched_item = dataclasses.replace(
        _evidence("some-other-theme", source_url="https://example.com/mismatched"), fact="MismatchedFactText",
    )
    matching_item = dataclasses.replace(matching_item, fact="MatchingFactText")
    repo = _FakeRepo(themes=[theme], evidence_by_theme={theme.id: (matching_item, mismatched_item)})
    at = _run_with_repo(monkeypatch, repo, theme_id=theme.id)
    all_html = " ".join(m.value for m in at.markdown)
    assert "MatchingFactText" in all_html
    assert "MismatchedFactText" not in all_html


# ============================================================
# Existing visible routes still render (no regression)
# ============================================================


def test_existing_visible_routes_still_render_without_exception():
    for harness_file in ["radar_inbox_page.py", "daily_news_page.py", "dashboard_page.py"]:
        at = AppTest.from_file(str(REPO_ROOT / "tests" / "apptest_pages" / harness_file), default_timeout=15)
        at.run()
        assert not at.exception, f"{harness_file} raised: {at.exception}"
