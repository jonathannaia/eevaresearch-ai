"""Daily News Slice 1's own scope guard — proves independence from
Radar Inbox at both the import level (AST-based, not prose substring
matching — several of these modules' own docstrings *describe* what
they don't import, which would false-positive a naive text search) and
the git-diff level (this phase never touches a Radar-owned file),
mirroring the same discipline test_ui_audit_phase_r1.py/
test_ui_audit_phase_t1.py already established for earlier phases."""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_DAILY_NEWS_SOURCE_FILES = (
    REPO_ROOT / "src" / "models" / "daily_news_models.py",
    REPO_ROOT / "src" / "data_access" / "daily_news" / "feed_registry.py",
    REPO_ROOT / "src" / "data_access" / "daily_news" / "rss_atom_client.py",
    REPO_ROOT / "src" / "data_access" / "daily_news" / "canonical_url.py",
    REPO_ROOT / "src" / "data_access" / "daily_news" / "summary_grounding.py",
    REPO_ROOT / "src" / "data_access" / "daily_news" / "dedup.py",
    REPO_ROOT / "src" / "data_access" / "daily_news" / "daily_news_store.py",
    REPO_ROOT / "src" / "data_access" / "daily_news" / "daily_news_pipeline.py",
    REPO_ROOT / "src" / "data_access" / "daily_news" / "daily_news_backend.py",
    REPO_ROOT / "scripts" / "run_daily_news_discovery.py",
    REPO_ROOT / "scripts" / "import_daily_news_json_to_db.py",
    REPO_ROOT / "src" / "ui" / "pages" / "daily_news.py",
    REPO_ROOT / "src" / "ui" / "pages" / "daily_news_admin.py",
)

_FORBIDDEN_IMPORT_PREFIXES = (
    "src.data_access.dart",
    "src.data_access.edgar",
    "src.data_access.edinet",
    "scripts.radar_worker",
    "src.ui.components.radar_card",
    "src.ui.components.radar_status",
    "src.models.models",
)

_FORBIDDEN_RADAR_PATHS = {
    "scripts/radar_worker.py",
    "src/data_access/dart/candidate_store.py",
    "src/data_access/dart/radar_pipeline.py",
    "src/data_access/dart/radar_service.py",
    "src/data_access/dart/retry_policy.py",
    "src/data_access/dart/scan_service.py",
    "src/data_access/dart/document_service.py",
    "src/data_access/edgar/edgar_pipeline.py",
    "src/data_access/edgar/scan_service.py",
    "src/data_access/edinet/edinet_pipeline.py",
    "src/data_access/edinet/scan_service.py",
    "src/data_access/translation/deepl_provider.py",
    "src/data_access/translation/translation_service.py",
    "src/ui/pages/radar_inbox.py",
    "src/ui/components/radar_card.py",
    "src/ui/components/radar_status.py",
    "src/models/models.py",
}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_daily_news_modules_never_import_from_radar_packages():
    offenders = []
    for path in _DAILY_NEWS_SOURCE_FILES:
        for module in _imported_modules(path):
            if any(module == forbidden or module.startswith(forbidden + ".") for forbidden in _FORBIDDEN_IMPORT_PREFIXES):
                offenders.append(f"{path.name}: imports {module!r}")
    assert not offenders, offenders


def test_daily_news_phase_never_modifies_a_radar_owned_file():
    """Runs against `git diff HEAD` — only meaningful in a real checkout
    with this phase's changes staged/unstaged; skips quietly otherwise
    rather than false-failing outside a git context."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    assert not (changed & _FORBIDDEN_RADAR_PATHS), changed & _FORBIDDEN_RADAR_PATHS


def _string_literals(path: Path) -> set[str]:
    """Every string-constant node in the file, excluding module/class/
    function docstrings — a docstring is itself an ast.Constant string,
    so leaving it in would defeat the whole point of using AST here
    instead of raw text search."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstring_node_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if (node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                docstring_node_ids.add(id(node.body[0].value))
    return {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstring_node_ids
    }


def test_daily_news_never_reads_radar_json_stores_by_filename():
    """AST-based, not prose substring matching — several of these
    modules' own docstrings *mention* Radar's JSON filenames by name
    while explaining that this store is separate from them, which a
    naive text search would misread as a reference. Checking actual
    string-literal constants instead of raw file text avoids that."""
    forbidden_filenames = {
        "dart_candidates.json", "edgar_candidates.json", "edinet_candidates.json",
        "dart_filing_events.json", "edgar_filing_events.json", "edinet_filing_events.json",
    }
    offenders = []
    for path in _DAILY_NEWS_SOURCE_FILES:
        hit = _string_literals(path) & forbidden_filenames
        if hit:
            offenders.append(f"{path.name}: references {hit}")
    assert not offenders, offenders
