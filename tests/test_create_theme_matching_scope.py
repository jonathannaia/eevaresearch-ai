"""EevaResearch — Phase A3 (design/DECISIONS.md). Tests for
scripts/create_theme_matching_scope.py — the private, operator-only
authoring tool for exactly one insert-only ThemeMatchingScope attached
to an existing, non-archived parent ResearchTheme. Every fixture is
synthetic and locally constructed; no real network, worker, scan, or
LLM call anywhere in this file."""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from scripts import create_theme_matching_scope as scope_mod
from src.config.settings import Settings
from src.data_access import backend_factory
from src.models.theme_research import (
    ResearchTheme,
    ThemeCategory,
    ThemeStatus,
    ThemeVisibility,
)

from tests._postgres_test_support import pg_isolated_dsn  # noqa: F401

REPO_ROOT = Path(__file__).parent.parent
_SCRIPT_PATH = REPO_ROOT / "scripts" / "create_theme_matching_scope.py"


def _theme(theme_id="theme-1", visibility=ThemeVisibility.INTERNAL, **overrides):
    defaults = dict(
        id=theme_id, category=ThemeCategory.BOTTLENECK, status=ThemeStatus.NEW, visibility=visibility,
        title="T", key_question="q", hypothesis="h", working_thesis="w", why_it_matters="y",
        what_could_change_the_view="c", what_to_watch_next="n",
        created_at="2026-09-01T00:00:00+00:00", updated_at="2026-09-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    return ResearchTheme(**defaults)


def _seed_theme(settings, **overrides):
    curator = backend_factory.get_theme_curator_repository(settings)
    theme = _theme(**overrides)
    curator.insert_theme(theme)
    return theme


def _set_valid_content(monkeypatch, theme_id="theme-1", **overrides):
    defaults = dict(
        _SCOPE_THEME_ID=theme_id,
        _SCOPE_SECTOR_TAGS=("ai-buildout", "memory"),
        _SCOPE_SECTOR_SUBTAGS=("compute-accelerators", "dram", "hbm"),
        _SCOPE_ALLOWED_RULE_CATEGORIES=("material_agreement", "financing_or_debt", "other_material_event"),
        _SCOPE_REQUIRED_KEYWORDS=("capacity", "wafer", "fab"),
        _SCOPE_EXCLUDED_KEYWORDS=("share repurchase", "stock buyback"),
    )
    defaults.update(overrides)
    for name, value in defaults.items():
        monkeypatch.setattr(scope_mod, name, value)


# ============================================================
# Disabled / dry-run / missing --confirm — zero writes
# ============================================================


def test_authoring_disabled_by_default_returns_none():
    assert scope_mod.build_authored_scope(False) is None


def test_dry_run_without_confirm_persists_nothing(tmp_path, monkeypatch):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    theme = _seed_theme(settings)
    _set_valid_content(monkeypatch, theme_id=theme.id)
    monkeypatch.setattr(scope_mod, "AUTHORING_ENABLED", True)

    def _forbidden(*_a, **_k):
        raise AssertionError("must not persist during a dry run")

    monkeypatch.setattr(scope_mod, "persist_scope", _forbidden)
    exit_code = scope_mod.main(["--backend", "json", "--cache-dir", str(tmp_path)])
    assert exit_code == 0

    matching_repo = backend_factory.get_theme_matching_repository(settings)
    assert matching_repo.get_scope(theme.id) is None


def test_missing_confirm_leaves_backend_empty(tmp_path, monkeypatch):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    theme = _seed_theme(settings)
    _set_valid_content(monkeypatch, theme_id=theme.id)
    monkeypatch.setattr(scope_mod, "AUTHORING_ENABLED", True)
    scope_mod.main(["--backend", "json", "--cache-dir", str(tmp_path)])

    matching_repo = backend_factory.get_theme_matching_repository(settings)
    assert matching_repo.get_scope(theme.id) is None


# ============================================================
# Successful attachment to a valid internal draft
# ============================================================


def test_successful_attachment_to_internal_parent(tmp_path, monkeypatch):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    theme = _seed_theme(settings, visibility=ThemeVisibility.INTERNAL)
    _set_valid_content(monkeypatch, theme_id=theme.id)
    monkeypatch.setattr(scope_mod, "AUTHORING_ENABLED", True)

    exit_code = scope_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    assert exit_code == 0

    matching_repo = backend_factory.get_theme_matching_repository(settings)
    stored = matching_repo.get_scope(theme.id)
    assert stored is not None
    assert stored.sector_tags == ("ai-buildout", "memory")


@pytest.mark.parametrize("visibility", [ThemeVisibility.INTERNAL, ThemeVisibility.READY_TO_PUBLISH, ThemeVisibility.PUBLISHED])
def test_eligible_parent_visibilities_accepted(tmp_path, monkeypatch, visibility):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    theme = _seed_theme(settings, visibility=visibility)
    _set_valid_content(monkeypatch, theme_id=theme.id)
    monkeypatch.setattr(scope_mod, "AUTHORING_ENABLED", True)

    exit_code = scope_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    assert exit_code == 0
    matching_repo = backend_factory.get_theme_matching_repository(settings)
    assert matching_repo.get_scope(theme.id) is not None


# ============================================================
# Archived / missing parent rejection
# ============================================================


def test_archived_parent_rejected(tmp_path, monkeypatch):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    theme = _seed_theme(settings, visibility=ThemeVisibility.ARCHIVED)
    _set_valid_content(monkeypatch, theme_id=theme.id)
    monkeypatch.setattr(scope_mod, "AUTHORING_ENABLED", True)

    exit_code = scope_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    assert exit_code == 1
    matching_repo = backend_factory.get_theme_matching_repository(settings)
    assert matching_repo.get_scope(theme.id) is None


def test_missing_parent_rejected(tmp_path, monkeypatch):
    _set_valid_content(monkeypatch, theme_id="theme-does-not-exist")
    monkeypatch.setattr(scope_mod, "AUTHORING_ENABLED", True)

    exit_code = scope_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    assert exit_code == 1
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    matching_repo = backend_factory.get_theme_matching_repository(settings)
    assert matching_repo.get_scope("theme-does-not-exist") is None


def test_validate_parent_theme_pure_function_covers_both_cases():
    theme_internal = _theme(visibility=ThemeVisibility.INTERNAL)
    theme_archived = _theme(visibility=ThemeVisibility.ARCHIVED)
    assert scope_mod.validate_parent_theme(theme_internal) == ()
    assert scope_mod.validate_parent_theme(theme_archived) != ()
    assert scope_mod.validate_parent_theme(None) != ()


# ============================================================
# Duplicate rejection
# ============================================================


def test_duplicate_scope_rejected(tmp_path, monkeypatch):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    theme = _seed_theme(settings)
    _set_valid_content(monkeypatch, theme_id=theme.id)
    monkeypatch.setattr(scope_mod, "AUTHORING_ENABLED", True)

    first = scope_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    assert first == 0
    second = scope_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    assert second == 1

    matching_repo = backend_factory.get_theme_matching_repository(settings)
    stored = matching_repo.get_scope(theme.id)
    assert stored.sector_tags == ("ai-buildout", "memory")  # unchanged, not overwritten


# ============================================================
# Content validation: empty required lists, sentinel, oversized
# ============================================================


def test_empty_sector_tags_rejected(monkeypatch):
    _set_valid_content(monkeypatch, _SCOPE_SECTOR_TAGS=())
    scope = scope_mod.build_authored_scope(True)
    errors = scope_mod.validate_scope_content(scope)
    assert any("sector_tags" in e for e in errors)


def test_empty_allowed_rule_categories_rejected(monkeypatch):
    _set_valid_content(monkeypatch, _SCOPE_ALLOWED_RULE_CATEGORIES=())
    scope = scope_mod.build_authored_scope(True)
    errors = scope_mod.validate_scope_content(scope)
    assert any("allowed_matched_rule_categories" in e for e in errors)


def test_empty_required_keywords_rejected(monkeypatch):
    _set_valid_content(monkeypatch, _SCOPE_REQUIRED_KEYWORDS=())
    scope = scope_mod.build_authored_scope(True)
    errors = scope_mod.validate_scope_content(scope)
    assert any("required_keywords" in e for e in errors)


def test_empty_subtags_and_excluded_keywords_are_allowed(monkeypatch):
    _set_valid_content(monkeypatch, _SCOPE_SECTOR_SUBTAGS=(), _SCOPE_EXCLUDED_KEYWORDS=())
    scope = scope_mod.build_authored_scope(True)
    errors = scope_mod.validate_scope_content(scope)
    assert errors == ()


def test_placeholder_sentinel_in_theme_id_rejected(tmp_path, monkeypatch):
    _set_valid_content(monkeypatch, theme_id="REPLACE_ME")
    monkeypatch.setattr(scope_mod, "AUTHORING_ENABLED", True)
    exit_code = scope_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    assert exit_code == 1


def test_placeholder_sentinel_in_keyword_rejected(monkeypatch):
    _set_valid_content(monkeypatch, _SCOPE_REQUIRED_KEYWORDS=("REPLACE_ME",))
    scope = scope_mod.build_authored_scope(True)
    assert scope_mod.contains_placeholder_sentinel(scope) is True


def test_oversized_list_rejected(monkeypatch):
    _set_valid_content(monkeypatch, _SCOPE_REQUIRED_KEYWORDS=tuple(f"kw{i}" for i in range(scope_mod._MAX_LIST_SIZE + 1)))
    scope = scope_mod.build_authored_scope(True)
    errors = scope_mod.validate_scope_content(scope)
    assert any("required_keywords" in e and "exceeding" in e for e in errors)


def test_oversized_entry_rejected(monkeypatch):
    _set_valid_content(monkeypatch, _SCOPE_REQUIRED_KEYWORDS=("x" * (scope_mod._MAX_ENTRY_LENGTH + 1),))
    scope = scope_mod.build_authored_scope(True)
    errors = scope_mod.validate_scope_content(scope)
    assert any("exceeds the maximum length" in e for e in errors)


# ============================================================
# Normalization / dedup
# ============================================================


def test_sector_tags_deduplicated_and_lowercased(monkeypatch):
    _set_valid_content(monkeypatch, _SCOPE_SECTOR_TAGS=("AI-Buildout", "ai-buildout", " memory "))
    scope = scope_mod.build_authored_scope(True)
    assert scope.sector_tags == ("ai-buildout", "memory")


def test_required_keywords_deduplicated_preserving_case(monkeypatch):
    _set_valid_content(monkeypatch, _SCOPE_REQUIRED_KEYWORDS=("Capacity", "capacity", " Wafer "))
    scope = scope_mod.build_authored_scope(True)
    # Dedup is exact-match after strip, so differently-cased duplicates
    # are NOT collapsed (case is preserved for readability) — only
    # exact, stripped duplicates collapse.
    assert scope.required_keywords == ("Capacity", "capacity", "Wafer")


# ============================================================
# Canonical semiconductor pilot taxonomy — real, reachable values
# ============================================================


def test_default_module_constants_match_approved_semiconductor_taxonomy():
    assert scope_mod._SCOPE_SECTOR_TAGS == ("ai-buildout", "memory")
    assert scope_mod._SCOPE_SECTOR_SUBTAGS == (
        "compute-accelerators", "dram", "hbm", "semiconductor-test",
        "power-cooling", "interconnect", "interconnect-switching", "optical-components",
    )
    assert scope_mod._SCOPE_ALLOWED_RULE_CATEGORIES == (
        "capex_or_facility_investment", "material_agreement", "financing_or_debt",
        "supply_or_sales_contract", "other_material_event",
    )
    assert scope_mod._SCOPE_REQUIRED_KEYWORDS == (
        "capacity", "wafer", "fab", "foundry", "packaging", "hbm", "dram", "allocation",
        "lead time", "yield", "node", "supply agreement", "capacity expansion",
    )
    assert scope_mod._SCOPE_EXCLUDED_KEYWORDS == (
        "share repurchase", "stock buyback", "dividend declaration",
        "annual meeting of stockholders", "proxy statement", "executive compensation",
    )


def test_excluded_keywords_never_collide_with_required_keywords():
    required_lower = {k.lower() for k in scope_mod._SCOPE_REQUIRED_KEYWORDS}
    for excluded in scope_mod._SCOPE_EXCLUDED_KEYWORDS:
        assert excluded.lower() not in required_lower


# ============================================================
# Failure isolation — no partial state
# ============================================================


def test_rejected_attempt_leaves_no_scope_state(tmp_path, monkeypatch):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    theme = _seed_theme(settings, visibility=ThemeVisibility.ARCHIVED)
    _set_valid_content(monkeypatch, theme_id=theme.id)
    monkeypatch.setattr(scope_mod, "AUTHORING_ENABLED", True)

    matching_repo = backend_factory.get_theme_matching_repository(settings)
    before = matching_repo.list_active_scopes()
    scope_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    after = matching_repo.list_active_scopes()
    assert before == after == ()


# ============================================================
# Backend parity
# ============================================================


def test_sqlite_backend_creates_scope(tmp_path, monkeypatch):
    settings = Settings(db_backend="sqlite", state_db_path=tmp_path / "state.db")
    theme = _seed_theme(settings)
    _set_valid_content(monkeypatch, theme_id=theme.id)
    monkeypatch.setattr(scope_mod, "AUTHORING_ENABLED", True)

    exit_code = scope_mod.main(["--confirm", "--backend", "sqlite", "--sqlite-path", str(tmp_path / "state.db")])
    assert exit_code == 0
    matching_repo = backend_factory.get_theme_matching_repository(settings)
    assert matching_repo.get_scope(theme.id) is not None


def test_postgres_backend_creates_scope(monkeypatch, pg_isolated_dsn):
    settings = Settings(db_backend="postgres", state_db_url=pg_isolated_dsn)
    from src.data_access.postgres_state_db import schema as postgres_schema
    from src.data_access.postgres_state_db import connection as postgres_connection

    conn = postgres_connection.connect(pg_isolated_dsn)
    postgres_schema.migrate(conn)
    theme = _theme(theme_id="pg-theme-1")
    from src.data_access.postgres_state_db import theme_repository as postgres_themes
    postgres_themes.insert_theme(conn, theme)
    conn.close()

    _set_valid_content(monkeypatch, theme_id="pg-theme-1")
    monkeypatch.setattr(scope_mod, "AUTHORING_ENABLED", True)
    exit_code = scope_mod.main(["--confirm", "--backend", "postgres", "--postgres-url", pg_isolated_dsn])
    assert exit_code == 0

    matching_repo = backend_factory.get_theme_matching_repository(settings)
    assert matching_repo.get_scope("pg-theme-1") is not None


# ============================================================
# No public visibility / no extra records
# ============================================================


def test_no_public_visibility_effect(tmp_path, monkeypatch):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    theme = _seed_theme(settings, visibility=ThemeVisibility.INTERNAL)
    _set_valid_content(monkeypatch, theme_id=theme.id)
    monkeypatch.setattr(scope_mod, "AUTHORING_ENABLED", True)
    scope_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])

    theme_repo = backend_factory.get_theme_repository(settings)
    assert theme_repo.list_published_themes() == ()
    assert theme_repo.evidence_for_theme(theme.id) == ()
    assert theme_repo.company_map_for_theme(theme.id) == ()


def test_never_calls_theme_evidence_company_map_or_decision_inserts(tmp_path, monkeypatch):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    theme = _seed_theme(settings)
    _set_valid_content(monkeypatch, theme_id=theme.id)
    curator = backend_factory.get_theme_curator_repository(settings)

    def _forbidden(*_a, **_k):
        raise AssertionError("must never be called by this script")

    monkeypatch.setattr(type(curator), "insert_theme", _forbidden)
    monkeypatch.setattr(type(curator), "insert_evidence_item", _forbidden)
    monkeypatch.setattr(type(curator), "insert_company_map_entry", _forbidden)
    monkeypatch.setattr(type(curator), "set_visibility", _forbidden)
    monkeypatch.setattr(backend_factory, "get_theme_curator_repository", lambda s: curator)

    matching_repo = backend_factory.get_theme_matching_repository(settings)

    def _forbidden_decision(*_a, **_k):
        raise AssertionError("must never be called by this script")

    monkeypatch.setattr(type(matching_repo), "insert_review_decision", _forbidden_decision)
    monkeypatch.setattr(type(matching_repo), "insert_match", _forbidden_decision)
    monkeypatch.setattr(backend_factory, "get_theme_matching_repository", lambda s: matching_repo)

    scope = scope_mod.build_authored_scope(True)
    created = scope_mod.persist_scope(scope, "json", cache_dir=tmp_path)
    assert created is True


# ============================================================
# Forbidden imports / dependencies — AST guards
# ============================================================


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_never_imports_create_theme_or_theme_draft_modules():
    imported = _imported_module_names(_SCRIPT_PATH)
    assert "scripts.create_theme" not in imported
    assert "scripts.create_internal_theme_draft" not in imported


def test_never_imports_worker_scan_network_or_llm_modules():
    imported = _imported_module_names(_SCRIPT_PATH)
    forbidden_substrings = ("radar_worker", "edgar", "dart", "edinet", "openai", "anthropic", "requests", "httpx", "urllib")
    offenders = [m for m in imported if any(f in m.lower() for f in forbidden_substrings)]
    assert not offenders, offenders


def test_never_references_set_visibility_evidence_or_company_map_types():
    tree = ast.parse(_SCRIPT_PATH.read_text(encoding="utf-8"), filename="create_theme_matching_scope.py")
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in ("ThemeEvidenceItem", "ThemeCompanyMapEntry", "ResearchTheme"):
            offenders.append(node.id)
        if isinstance(node, ast.Attribute) and node.attr == "set_visibility":
            offenders.append("set_visibility")
    assert not offenders, offenders


def test_no_update_versioning_deletion_batch_or_publish_capability():
    exported = {name for name in dir(scope_mod) if not name.startswith("_")}
    forbidden_substrings = ("update", "delete", "replace", "upsert", "overwrite", "merge", "publish", "version", "batch", "edit")
    offenders = [name for name in exported if any(f in name.lower() for f in forbidden_substrings)]
    assert not offenders, offenders


# ============================================================
# Scope guard — no other file touched
# ============================================================


def test_no_other_files_touched():
    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    allowed = {
        "scripts/create_internal_theme_draft.py", "scripts/create_theme_matching_scope.py",
        "tests/test_create_internal_theme_draft.py", "tests/test_create_theme_matching_scope.py",
    }
    assert changed <= allowed, changed - allowed
