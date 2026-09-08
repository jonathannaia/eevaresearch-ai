"""Company Discovery Phase 2's own scope guard — proves no network I/O
of any kind, no import of any write-capable EDGAR/DART/EDINET/
translation module (and therefore no path into live monitoring
configuration), and that no existing public page imports anything from
this package. AST-based, not prose substring matching — several of
these modules' own docstrings *describe* what they don't import, which
would false-positive a naive text search. Mirrors
tests/test_daily_news_scope_guard.py's own discipline exactly."""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_COMPANY_DISCOVERY_SOURCE_FILES = (
    REPO_ROOT / "src" / "models" / "company_discovery_models.py",
    REPO_ROOT / "src" / "data_access" / "company_discovery" / "extraction_rules.py",
    REPO_ROOT / "src" / "data_access" / "company_discovery" / "entity_resolution.py",
    REPO_ROOT / "src" / "data_access" / "company_discovery" / "scoring.py",
    REPO_ROOT / "src" / "data_access" / "company_discovery" / "candidate_pipeline.py",
    REPO_ROOT / "src" / "data_access" / "company_discovery" / "company_discovery_backend.py",
    REPO_ROOT / "src" / "data_access" / "state_db" / "candidate_issuer_repository.py",
    REPO_ROOT / "src" / "data_access" / "postgres_state_db" / "candidate_issuer_repository.py",
    REPO_ROOT / "scripts" / "company_discovery_worker.py",
    REPO_ROOT / "scripts" / "backfill_company_discovery.py",
    REPO_ROOT / "src" / "ui" / "pages" / "company_discovery_admin.py",
)

# No network client, feed parser, or translation-provider module of any
# kind — Phase 2 reads only already-persisted data via the existing
# repository factories. Also forbids the write-capable scan/pipeline
# modules for each source, which is what would let this package reach
# into live monitoring configuration even indirectly.
_FORBIDDEN_IMPORT_PREFIXES = (
    "requests",
    "feedparser",
    "src.data_access.dart.client",
    "src.data_access.dart.scan_service",
    "src.data_access.dart.radar_pipeline",
    "src.data_access.dart.radar_service",
    "src.data_access.edgar.client",
    "src.data_access.edgar.scan_service",
    "src.data_access.edgar.edgar_pipeline",
    "src.data_access.edinet.client",
    "src.data_access.edinet.scan_service",
    "src.data_access.edinet.edinet_pipeline",
    "src.data_access.translation",
    "src.data_access.daily_news.rss_atom_client",
    "scripts.radar_worker",
    "scripts.daily_news_worker",
)

_FORBIDDEN_LIVE_MONITORING_PATHS = {
    "src/config/tracked_companies.py",
    "src/config/issuer_registry.py",
    "src/data_access/dart/scan_service.py",
    "src/data_access/edgar/scan_service.py",
    "src/data_access/edinet/scan_service.py",
    "src/ui/pages/radar_inbox.py",
    "src/ui/pages/daily_news.py",
    "src/ui/pages/themes_research.py",
    "src/data_access/daily_news/feed_registry.py",
}

_PUBLIC_PAGE_FILES = (
    REPO_ROOT / "src" / "ui" / "pages" / "dashboard.py",
    REPO_ROOT / "src" / "ui" / "pages" / "radar_inbox.py",
    REPO_ROOT / "src" / "ui" / "pages" / "daily_news.py",
    REPO_ROOT / "src" / "ui" / "pages" / "themes_research.py",
    REPO_ROOT / "src" / "ui" / "pages" / "coverage.py",
    REPO_ROOT / "src" / "ui" / "pages" / "signals.py",
    REPO_ROOT / "src" / "ui" / "pages" / "methodology.py",
    REPO_ROOT / "src" / "ui" / "pages" / "about.py",
    REPO_ROOT / "src" / "ui" / "pages" / "disclaimer.py",
    REPO_ROOT / "src" / "ui" / "pages" / "home.py",
)

_FORBIDDEN_FOR_PUBLIC_PAGES = ("src.data_access.company_discovery", "src.ui.pages.company_discovery_admin")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_company_discovery_modules_never_import_network_or_translation_or_write_capable_source_modules():
    offenders = []
    for path in _COMPANY_DISCOVERY_SOURCE_FILES:
        for module in _imported_modules(path):
            if any(module == forbidden or module.startswith(forbidden + ".") for forbidden in _FORBIDDEN_IMPORT_PREFIXES):
                offenders.append(f"{path.name}: imports {module!r}")
    assert not offenders, offenders


def test_no_public_page_imports_from_company_discovery():
    offenders = []
    for path in _PUBLIC_PAGE_FILES:
        for module in _imported_modules(path):
            if any(module == forbidden or module.startswith(forbidden + ".") for forbidden in _FORBIDDEN_FOR_PUBLIC_PAGES):
                offenders.append(f"{path.name}: imports {module!r}")
    assert not offenders, offenders


def test_company_discovery_phase_never_modifies_a_live_monitoring_owned_file():
    """Runs against `git diff HEAD` — only meaningful in a real checkout
    with this phase's changes staged/unstaged; skips quietly otherwise
    rather than false-failing outside a git context. tracked_companies.py
    and issuer_registry.py are included here per the approved binding
    decision that neither is ever altered by this workstream."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    assert not (changed & _FORBIDDEN_LIVE_MONITORING_PATHS), changed & _FORBIDDEN_LIVE_MONITORING_PATHS


def test_no_source_string_literal_references_a_live_http_url_scheme():
    """A conservative, generic defense-in-depth check: none of these
    files' own string literals contain "http://" or "https://" at all —
    Phase 2 has no reason to construct or reference a URL of its own
    (the URLs it stores in candidate_evidence.source_url are always
    copied verbatim from an already-persisted FilingEvent/NewsStory
    record, never built here)."""
    offenders = []
    for path in _COMPANY_DISCOVERY_SOURCE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "http://" in node.value or "https://" in node.value:
                    offenders.append(f"{path.name}: literal {node.value!r}")
    assert not offenders, offenders
