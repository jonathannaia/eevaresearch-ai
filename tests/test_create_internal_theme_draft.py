"""EevaResearch — Phase A3 (design/DECISIONS.md). Tests for
scripts/create_internal_theme_draft.py — the private, operator-only
authoring tool for exactly one evidence-free, internal ResearchTheme
draft. Every fixture is synthetic and locally constructed; no real
network, worker, scan, or LLM call anywhere in this file."""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts import create_internal_theme_draft as draft_mod
from src.config.settings import Settings
from src.data_access import backend_factory
from src.models.theme_research import ThemeCategory, ThemeStatus, ThemeVisibility

from tests._postgres_test_support import pg_isolated_dsn  # noqa: F401

REPO_ROOT = Path(__file__).parent.parent
_SCRIPT_PATH = REPO_ROOT / "scripts" / "create_internal_theme_draft.py"


def _set_valid_content(monkeypatch, **overrides):
    defaults = dict(
        _THEME_CREATED_AT="2026-09-01T00:00:00Z",
        _THEME_TITLE="AI Infrastructure: Where Is the Binding Constraint?",
        _THEME_KEY_QUESTION="Where is capacity becoming a binding constraint?",
        _THEME_HYPOTHESIS="Potential constraints may emerge across infrastructure inputs.",
        _THEME_WORKING_THESIS="Internal draft. Collecting official-source context for human review.",
        _THEME_WHY_IT_MATTERS="To be determined after reviewed evidence identifies a constraint.",
        _THEME_WHAT_COULD_CHANGE_THE_VIEW="To be determined from reviewed evidence.",
        _THEME_WHAT_TO_WATCH_NEXT="Official company disclosures relevant to capacity and supply.",
    )
    defaults.update(overrides)
    for name, value in defaults.items():
        monkeypatch.setattr(draft_mod, name, value)


# ============================================================
# Disabled / dry-run / missing --confirm — zero writes
# ============================================================


def test_authoring_disabled_by_default_returns_none():
    assert draft_mod.build_authored_theme_draft(False) is None


def test_build_returns_none_when_disabled_even_with_valid_content(monkeypatch):
    _set_valid_content(monkeypatch)
    assert draft_mod.build_authored_theme_draft(enable_authoring=False) is None


def test_dry_run_without_confirm_persists_nothing(tmp_path, monkeypatch, capsys):
    _set_valid_content(monkeypatch)
    monkeypatch.setattr(draft_mod, "AUTHORING_ENABLED", True)

    def _forbidden(*_a, **_k):
        raise AssertionError("must not persist during a dry run")

    monkeypatch.setattr(draft_mod, "persist_theme_draft", _forbidden)
    exit_code = draft_mod.main(["--backend", "json", "--cache-dir", str(tmp_path)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Dry run" in out


def test_missing_confirm_leaves_backend_empty(tmp_path, monkeypatch):
    _set_valid_content(monkeypatch)
    monkeypatch.setattr(draft_mod, "AUTHORING_ENABLED", True)
    draft_mod.main(["--backend", "json", "--cache-dir", str(tmp_path)])

    repo = backend_factory.get_theme_curator_repository(Settings(db_backend="json", cache_dir=tmp_path))
    theme_id = draft_mod.build_theme_id(draft_mod._THEME_TITLE, draft_mod._THEME_CREATED_AT)
    assert repo.get_theme(theme_id) is None


# ============================================================
# Successful creation — internal-only, no other record types
# ============================================================


def test_successful_creation_is_internal_only(tmp_path, monkeypatch):
    _set_valid_content(monkeypatch)
    theme = draft_mod.build_authored_theme_draft(True)
    assert theme.visibility is ThemeVisibility.INTERNAL

    created = draft_mod.persist_theme_draft(theme, "json", cache_dir=tmp_path)
    assert created is True

    curator = backend_factory.get_theme_curator_repository(Settings(db_backend="json", cache_dir=tmp_path))
    stored = curator.get_theme(theme.id)
    assert stored is not None
    assert stored.visibility is ThemeVisibility.INTERNAL

    theme_repo = backend_factory.get_theme_repository(Settings(db_backend="json", cache_dir=tmp_path))
    assert theme_repo.list_published_themes() == ()
    assert theme_repo.get_published_theme(theme.id) is None


def test_never_calls_evidence_company_map_scope_match_or_decision_inserts(tmp_path, monkeypatch):
    _set_valid_content(monkeypatch)
    theme = draft_mod.build_authored_theme_draft(True)

    settings = Settings(db_backend="json", cache_dir=tmp_path)
    curator = backend_factory.get_theme_curator_repository(settings)

    def _forbidden(*_a, **_k):
        raise AssertionError("must never be called by this script")

    # JsonThemeCuratorRepository is a frozen dataclass — patch the class,
    # not the instance.
    monkeypatch.setattr(type(curator), "insert_evidence_item", _forbidden)
    monkeypatch.setattr(type(curator), "insert_company_map_entry", _forbidden)
    monkeypatch.setattr(type(curator), "set_visibility", _forbidden)

    matching_repo_forbidden = MagicMock(side_effect=AssertionError("must never construct a matching repository"))
    monkeypatch.setattr(backend_factory, "get_theme_matching_repository", matching_repo_forbidden)

    monkeypatch.setattr(backend_factory, "get_theme_curator_repository", lambda s: curator)
    created = draft_mod.persist_theme_draft(theme, "json", cache_dir=tmp_path)
    assert created is True


def test_cannot_publish_edit_update_or_delete():
    exported = {name for name in dir(draft_mod) if not name.startswith("_")}
    forbidden_substrings = ("update", "delete", "replace", "upsert", "overwrite", "merge", "publish", "set_visibility", "edit")
    offenders = [name for name in exported if any(f in name.lower() for f in forbidden_substrings)]
    assert not offenders, offenders


# ============================================================
# Timestamp validation — Section C
# ============================================================


def test_timestamp_with_z_suffix_accepted(monkeypatch):
    _set_valid_content(monkeypatch, _THEME_CREATED_AT="2026-09-01T00:00:00Z")
    theme = draft_mod.build_authored_theme_draft(True)
    assert draft_mod.validate_theme_draft_content(theme) == ()


def test_timestamp_with_explicit_offset_accepted(monkeypatch):
    _set_valid_content(monkeypatch, _THEME_CREATED_AT="2026-09-01T00:00:00+00:00")
    theme = draft_mod.build_authored_theme_draft(True)
    assert draft_mod.validate_theme_draft_content(theme) == ()


def test_timestamp_with_nonzero_offset_accepted(monkeypatch):
    _set_valid_content(monkeypatch, _THEME_CREATED_AT="2026-09-01T09:00:00+09:00")
    theme = draft_mod.build_authored_theme_draft(True)
    assert draft_mod.validate_theme_draft_content(theme) == ()


@pytest.mark.parametrize("bad_timestamp", ["", "   ", "not-a-date", "2026-13-40T00:00:00Z"])
def test_malformed_or_blank_timestamp_rejected(monkeypatch, tmp_path, bad_timestamp):
    _set_valid_content(monkeypatch, _THEME_CREATED_AT=bad_timestamp)
    monkeypatch.setattr(draft_mod, "AUTHORING_ENABLED", True)
    theme = draft_mod.build_authored_theme_draft(True)
    errors = draft_mod.validate_theme_draft_content(theme)
    assert any("created_at" in e for e in errors)

    exit_code = draft_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    assert exit_code == 1
    curator = backend_factory.get_theme_curator_repository(Settings(db_backend="json", cache_dir=tmp_path))
    assert curator.get_theme(theme.id) is None


def test_date_only_timestamp_rejected(monkeypatch, tmp_path):
    _set_valid_content(monkeypatch, _THEME_CREATED_AT="2026-09-01")
    monkeypatch.setattr(draft_mod, "AUTHORING_ENABLED", True)
    theme = draft_mod.build_authored_theme_draft(True)
    errors = draft_mod.validate_theme_draft_content(theme)
    assert any("created_at" in e for e in errors)

    exit_code = draft_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    assert exit_code == 1


def test_timezone_naive_timestamp_rejected(monkeypatch, tmp_path):
    _set_valid_content(monkeypatch, _THEME_CREATED_AT="2026-09-01T00:00:00")
    monkeypatch.setattr(draft_mod, "AUTHORING_ENABLED", True)
    theme = draft_mod.build_authored_theme_draft(True)
    errors = draft_mod.validate_theme_draft_content(theme)
    assert any("created_at" in e for e in errors)

    exit_code = draft_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    assert exit_code == 1


def test_parse_iso8601_utc_datetime_never_calls_clock_or_network():
    tree = ast.parse(_SCRIPT_PATH.read_text(encoding="utf-8"), filename="create_internal_theme_draft.py")
    offenders = [
        n.func.attr for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr in ("now", "today", "utcnow") and isinstance(n.func.value, ast.Name) and n.func.value.id in ("datetime", "date")
    ]
    assert not offenders, offenders


# ============================================================
# Sentinel / blank / oversized rejection
# ============================================================


def test_placeholder_sentinel_rejected(monkeypatch, tmp_path):
    _set_valid_content(monkeypatch, _THEME_TITLE="REPLACE_ME")
    monkeypatch.setattr(draft_mod, "AUTHORING_ENABLED", True)
    exit_code = draft_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    assert exit_code == 1


def test_blank_field_rejected(monkeypatch):
    _set_valid_content(monkeypatch, _THEME_WORKING_THESIS="   ")
    theme = draft_mod.build_authored_theme_draft(True)
    errors = draft_mod.validate_theme_draft_content(theme)
    assert any("working_thesis" in e for e in errors)


def test_oversized_field_rejected(monkeypatch):
    _set_valid_content(monkeypatch, _THEME_HYPOTHESIS="x" * (draft_mod._MAX_FIELD_LENGTH + 1))
    theme = draft_mod.build_authored_theme_draft(True)
    errors = draft_mod.validate_theme_draft_content(theme)
    assert any("hypothesis" in e and "exceeds" in e for e in errors)


def test_wrong_visibility_rejected_defensively(monkeypatch):
    import dataclasses
    _set_valid_content(monkeypatch)
    theme = draft_mod.build_authored_theme_draft(True)
    tampered = dataclasses.replace(theme, visibility=ThemeVisibility.PUBLISHED)
    errors = draft_mod.validate_theme_draft_content(tampered)
    assert any("visibility" in e for e in errors)


# ============================================================
# Duplicate rejection / deterministic id
# ============================================================


def test_duplicate_theme_id_rejected(tmp_path, monkeypatch):
    _set_valid_content(monkeypatch)
    theme = draft_mod.build_authored_theme_draft(True)
    assert draft_mod.persist_theme_draft(theme, "json", cache_dir=tmp_path) is True
    assert draft_mod.persist_theme_draft(theme, "json", cache_dir=tmp_path) is False


def test_theme_id_is_deterministic_and_matches_established_convention(monkeypatch):
    from src.data_access.theme_store import build_theme_id
    _set_valid_content(monkeypatch)
    theme = draft_mod.build_authored_theme_draft(True)
    assert theme.id == build_theme_id(draft_mod._THEME_TITLE, draft_mod._THEME_CREATED_AT)


# ============================================================
# Backend parity
# ============================================================


def test_sqlite_backend_creates_internal_theme(tmp_path, monkeypatch):
    _set_valid_content(monkeypatch)
    theme = draft_mod.build_authored_theme_draft(True)
    created = draft_mod.persist_theme_draft(theme, "sqlite", sqlite_path=tmp_path / "state.db")
    assert created is True

    curator = backend_factory.get_theme_curator_repository(Settings(db_backend="sqlite", state_db_path=tmp_path / "state.db"))
    stored = curator.get_theme(theme.id)
    assert stored.visibility is ThemeVisibility.INTERNAL


def test_postgres_backend_creates_internal_theme(monkeypatch, pg_isolated_dsn):
    _set_valid_content(monkeypatch)
    theme = draft_mod.build_authored_theme_draft(True)
    created = draft_mod.persist_theme_draft(theme, "postgres", postgres_url=pg_isolated_dsn)
    assert created is True

    curator = backend_factory.get_theme_curator_repository(Settings(db_backend="postgres", state_db_url=pg_isolated_dsn))
    stored = curator.get_theme(theme.id)
    assert stored.visibility is ThemeVisibility.INTERNAL


# ============================================================
# Output wording — Section B
# ============================================================


def test_dry_run_output_states_internal_and_not_publishable(monkeypatch, tmp_path, capsys):
    _set_valid_content(monkeypatch)
    monkeypatch.setattr(draft_mod, "AUTHORING_ENABLED", True)
    draft_mod.main(["--backend", "json", "--cache-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert "Internal evidence-collection draft" in out
    assert "not a published research conclusion" in out
    assert "/themes" in out
    assert "reviewed-evidence workflow" in out


def test_success_output_states_internal_and_not_publishable(monkeypatch, tmp_path, capsys):
    _set_valid_content(monkeypatch)
    monkeypatch.setattr(draft_mod, "AUTHORING_ENABLED", True)
    draft_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert "Internal evidence-collection draft" in out
    assert "cannot publish" in out
    assert "/themes" in out


def test_output_does_not_claim_create_theme_is_exclusive_publication_path(monkeypatch, tmp_path, capsys):
    _set_valid_content(monkeypatch)
    monkeypatch.setattr(draft_mod, "AUTHORING_ENABLED", True)
    draft_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert "create_theme.py" not in out
    assert "exclusive" not in out.lower()
    assert "sole" not in out.lower()


def test_module_docstring_states_the_real_invariant_not_create_theme_exclusivity():
    source = _SCRIPT_PATH.read_text(encoding="utf-8")
    # A *negated* mention ("not described as the sole or exclusive
    # mechanism, because it isn't") is the correct, desired framing —
    # what must never appear is a positive claim of exclusivity.
    assert "is the sole" not in source
    assert "is the exclusive" not in source
    assert "separate, future reviewed-evidence workflow" in source
    assert "cannot publish" in source.lower()


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


def test_never_imports_create_theme_module():
    imported = _imported_module_names(_SCRIPT_PATH)
    assert "scripts.create_theme" not in imported
    assert "create_theme" not in imported


def test_never_imports_worker_scan_network_or_llm_modules():
    imported = _imported_module_names(_SCRIPT_PATH)
    forbidden_substrings = ("radar_worker", "edgar", "dart", "edinet", "openai", "anthropic", "requests", "httpx", "urllib")
    offenders = [m for m in imported if any(f in m.lower() for f in forbidden_substrings)]
    assert not offenders, offenders


def test_never_imports_theme_matching_types():
    imported = _imported_module_names(_SCRIPT_PATH)
    offenders = [m for m in imported if "theme_matching" in m]
    assert not offenders, offenders


def test_never_references_set_visibility_or_evidence_or_company_map_types():
    tree = ast.parse(_SCRIPT_PATH.read_text(encoding="utf-8"), filename="create_internal_theme_draft.py")
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in ("ThemeEvidenceItem", "ThemeCompanyMapEntry", "ThemeMatchReviewDecision"):
            offenders.append(node.id)
        if isinstance(node, ast.Attribute) and node.attr == "set_visibility":
            offenders.append("set_visibility")
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
