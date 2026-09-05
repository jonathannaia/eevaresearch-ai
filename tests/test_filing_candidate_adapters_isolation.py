"""Daily News Filing-Event Shadow Adapter, Batch 2a — cross-cutting
isolation proof for all three adapter modules. Mechanical (AST-based),
not prose: proves no adapter imports worker/pipeline/scan-client/
database/translation/UI/network code, and proves no real runtime module
imports any of the three adapters. Zero network calls, zero I/O."""
from __future__ import annotations

import ast
from pathlib import Path

from src.data_access.daily_news import feed_registry, source_registry

_REPO_ROOT = Path(__file__).parent.parent

_ADAPTER_PATHS = (
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "edgar_filing_candidate_adapter.py",
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "dart_filing_candidate_adapter.py",
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "edinet_filing_candidate_adapter.py",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _imported_names(path: Path) -> set[str]:
    """Every NAME actually imported (e.g. `FilingEvent` from `from
    src.models.models import FilingEvent`) — distinct from
    _imported_modules(), which only reports the module path. Used to
    prove a specific type (CandidateSignal) is never imported, even
    though the module it lives in (src.models.models) is legitimately
    imported for FilingEvent."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
    return names


# ============================================================
# Adapters import no worker/pipeline/scan-client/database/translation/
# UI/network code
# ============================================================

_FORBIDDEN_IMPORT_SUBSTRINGS = (
    "daily_news_worker", "daily_news_backend", "daily_news_pipeline", "rss_atom_client",
    "daily_news_store", "daily_news_scan_status_repository",
    "state_db", "postgres_state_db",
    "translation_service", "deepl_provider",
    "streamlit", "src.ui",
    # Source-specific scan clients/pipelines/services — an adapter
    # consumes an already-produced FilingEvent, it never fetches one.
    "edgar.client", "edgar.scan_service", "edgar.edgar_pipeline", "edgar.discovery_service",
    "edgar.document_service", "edgar.document_extractor",
    "dart.client", "dart.scan_service", "dart.radar_pipeline", "dart.discovery_service",
    "dart.document_service", "dart.document_extractor", "dart.candidate_store",
    "edinet.client", "edinet.scan_service", "edinet.edinet_pipeline", "edinet.discovery_service",
    "edinet.document_service", "edinet.document_extractor",
)

_FORBIDDEN_IO_PRIMITIVES = ("requests", "sqlite3", "psycopg", "urllib.request", "http.client")


def test_no_adapter_imports_worker_pipeline_scan_client_database_translation_ui_or_network_code():
    for path in _ADAPTER_PATHS:
        imported = _imported_modules(path)
        offenders = [m for m in imported if any(forbidden in m for forbidden in _FORBIDDEN_IMPORT_SUBSTRINGS)]
        assert not offenders, (path.name, offenders)


def test_no_adapter_imports_a_network_or_database_io_primitive():
    for path in _ADAPTER_PATHS:
        offenders = [m for m in _imported_modules(path) if m in _FORBIDDEN_IO_PRIMITIVES]
        assert not offenders, (path.name, offenders)


def test_no_adapter_imports_candidate_signal_by_name():
    # EDGAR correction's own explicit constraint: matched_rules is
    # accepted as a plain tuple[str, ...], decoupled from the
    # CandidateSignal type that happens to carry it in the real
    # (unwired) system — src.models.models (which also defines
    # CandidateSignal) is legitimately imported for FilingEvent, but the
    # name CandidateSignal itself must never be imported by any adapter.
    for path in _ADAPTER_PATHS:
        assert "CandidateSignal" not in _imported_names(path), path.name


def test_edinet_adapter_reuses_material_event_shadow_by_import_never_by_redefinition():
    # Reuse (import) is expected for the EDINET adapter — this test
    # proves the import is read-only usage of the real, existing
    # function, never a local redefinition/copy of its logic.
    edinet_adapter_path = _REPO_ROOT / "src" / "data_access" / "daily_news" / "edinet_filing_candidate_adapter.py"
    source = edinet_adapter_path.read_text(encoding="utf-8")
    assert "def is_eligible_extraordinary_report" not in source  # never redefined locally
    assert any("material_event_shadow" in m for m in _imported_modules(edinet_adapter_path))


# ============================================================
# No real runtime module imports any of the three adapters
# ============================================================

_RUNTIME_PATHS = (
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "daily_news_pipeline.py",
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "daily_news_backend.py",
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "daily_news_store.py",
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "feed_registry.py",
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "rss_atom_client.py",
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "source_registry.py",
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "source_candidates.py",
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "filing_event_models.py",
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "policy_disclosure_models.py",
    _REPO_ROOT / "scripts" / "daily_news_worker.py",
    _REPO_ROOT / "src" / "ui" / "pages" / "daily_news.py",
    _REPO_ROOT / "src" / "ui" / "pages" / "daily_news_admin.py",
    _REPO_ROOT / "src" / "data_access" / "edgar" / "edgar_pipeline.py",
    _REPO_ROOT / "src" / "data_access" / "dart" / "radar_pipeline.py",
    _REPO_ROOT / "src" / "data_access" / "edinet" / "edinet_pipeline.py",
    _REPO_ROOT / "src" / "data_access" / "edinet" / "material_event_shadow.py",
)

_ADAPTER_MODULE_NAME_FRAGMENTS = (
    "edgar_filing_candidate_adapter", "dart_filing_candidate_adapter", "edinet_filing_candidate_adapter",
)


def test_no_real_runtime_module_imports_any_filing_candidate_adapter():
    for path in _RUNTIME_PATHS:
        offenders = [
            module for module in _imported_modules(path)
            if any(fragment in module for fragment in _ADAPTER_MODULE_NAME_FRAGMENTS)
        ]
        assert not offenders, (path.name, offenders)


def test_edgar_rules_dart_rules_edinet_rules_are_never_imported_by_the_adapters_backwards():
    # One-directional dependency: the adapters import FROM the rule
    # modules, never the reverse.
    rule_paths = (
        _REPO_ROOT / "src" / "data_access" / "edgar" / "edgar_rules.py",
        _REPO_ROOT / "src" / "data_access" / "dart" / "dart_rules.py",
        _REPO_ROOT / "src" / "data_access" / "edinet" / "edinet_rules.py",
    )
    for path in rule_paths:
        offenders = [
            module for module in _imported_modules(path)
            if any(fragment in module for fragment in _ADAPTER_MODULE_NAME_FRAGMENTS)
        ]
        assert not offenders, (path.name, offenders)


# ============================================================
# Existing runtime source/feed registries remain unchanged
# ============================================================


def test_runtime_source_registry_and_pilot_feeds_are_unaffected_by_this_batch():
    assert len(source_registry.RUNTIME_SOURCE_REGISTRY) == 20
    assert len(feed_registry.PILOT_FEEDS) == 20
    assert source_registry.find_registry_violations(source_registry.RUNTIME_SOURCE_REGISTRY) == ()
