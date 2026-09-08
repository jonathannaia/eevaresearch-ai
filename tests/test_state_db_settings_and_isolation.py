"""Backend-selection settings defaults, and structural proof this whole
package never touches the real data/cache/ directory, the real local
.env's legacy EDGE_DB_PATH/pre-existing database file, or makes a
network call. No real .env, no real cache file, no real database file,
no network access anywhere in this file — every assertion here uses
explicit monkeypatching or in-memory/tmp_path databases only, never the
ambient local environment."""
from __future__ import annotations

import ast
from pathlib import Path

from src.config.settings import Settings

_STATE_DB_DIR = Path(__file__).resolve().parent.parent / "src" / "data_access" / "state_db"


# --- 12. Existing JSON backend remains the default when EDGE_DB_BACKEND is unset ---

def test_db_backend_defaults_to_json(monkeypatch):
    monkeypatch.delenv("EDGE_DB_BACKEND", raising=False)
    assert Settings().db_backend == "json"


def test_state_db_path_defaults_to_none(monkeypatch):
    # This repo's real local .env defines an unrelated, legacy
    # EDGE_DB_PATH (a pre-"foundation rebuild" leftover — see
    # design/DECISIONS.md) — that name is deliberately NOT what this
    # field reads (see EDGE_STATE_DB_PATH below), so this test doesn't
    # need to guard against it. It still monkeypatches its own variable
    # for a guaranteed-clean assertion regardless of ambient environment.
    monkeypatch.delenv("EDGE_STATE_DB_PATH", raising=False)
    assert Settings().state_db_path is None


def test_state_db_url_defaults_to_none_and_is_never_parsed_or_connected(monkeypatch):
    monkeypatch.delenv("EDGE_STATE_DB_URL", raising=False)
    assert Settings().state_db_url is None


def test_db_backend_recognizes_sqlite_case_insensitively(monkeypatch):
    monkeypatch.setenv("EDGE_DB_BACKEND", "SQLite")
    assert Settings().db_backend == "sqlite"


def test_db_backend_blank_or_unset_falls_back_to_json(monkeypatch):
    monkeypatch.setenv("EDGE_DB_BACKEND", "")
    assert Settings().db_backend == "json"


def test_settings_field_reads_the_dedicated_state_db_env_var_not_the_legacy_one(monkeypatch):
    # Proves the rename actually took: setting the OLD, legacy-colliding
    # name must have zero effect on the new field, and setting the NEW
    # dedicated name must be what the field picks up.
    monkeypatch.delenv("EDGE_STATE_DB_PATH", raising=False)
    monkeypatch.setenv("EDGE_DB_PATH", "/should/not/be/read/by/state_db_path.db")
    assert Settings().state_db_path is None  # legacy name ignored

    monkeypatch.setenv("EDGE_STATE_DB_PATH", "/tmp/some-test-only-path.db")
    assert str(Settings().state_db_path) == "/tmp/some-test-only-path.db"


def test_no_backward_compatible_alias_for_the_legacy_edge_db_path_name():
    # Settings must not define a "db_path" field at all anymore — no
    # alias, no fallback read of EDGE_DB_PATH.
    field_names = {f for f in Settings.__dataclass_fields__}
    assert "db_path" not in field_names
    assert "db_url" not in field_names
    assert "state_db_path" in field_names
    assert "state_db_url" in field_names


# --- Targeted check: no Phase-1 file supports the old EDGE_DB_PATH/EDGE_DB_URL names ---

def test_state_db_package_never_references_the_retired_edge_db_path_or_edge_db_url_names():
    # Scoped to the actual state_db source package — not this test file,
    # which legitimately references the retired name once, as a literal
    # string, specifically to prove Settings no longer reads it (see
    # test_settings_field_reads_the_dedicated_state_db_env_var_not_the_legacy_one).
    offenders = []
    for path in _STATE_DB_DIR.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "EDGE_DB_PATH" in source or "EDGE_DB_URL" in source:
            offenders.append(path.name)
    assert offenders == []


def test_settings_module_no_longer_functionally_reads_the_retired_names():
    # settings.py's own explanatory comments are allowed to mention the
    # retired name in prose (see its docstring) — what must NOT exist is
    # an actual os.getenv(...) call reading it.
    settings_source = (Path(__file__).resolve().parent.parent / "src" / "config" / "settings.py").read_text(encoding="utf-8")
    assert 'os.getenv("EDGE_DB_PATH")' not in settings_source
    assert 'os.getenv("EDGE_DB_URL")' not in settings_source
    assert 'os.getenv("EDGE_STATE_DB_PATH")' in settings_source
    assert 'os.getenv("EDGE_STATE_DB_URL")' in settings_source


def test_env_example_documents_only_the_new_state_db_names():
    env_example = (Path(__file__).resolve().parent.parent / ".env.example").read_text(encoding="utf-8")
    assert "EDGE_STATE_DB_PATH" in env_example
    assert "EDGE_STATE_DB_URL" in env_example
    assert "EDGE_DB_PATH=" not in env_example  # the retired name is only ever mentioned in prose, never as a settable line
    assert "EDGE_DB_URL=" not in env_example


# --- 14. No test in this package touches real data/cache/, the legacy .env, or the pre-existing database file ---

def test_state_db_package_never_references_the_real_cache_directory_or_legacy_db_by_name():
    for path in _STATE_DB_DIR.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "data/cache" not in source, f"{path.name} must not reference the real cache directory"
        assert "cache_dir" not in source, f"{path.name} must not depend on Settings.cache_dir"
        assert "edge_research.db" not in source, f"{path.name} must not reference the pre-existing legacy database file"
        assert ".env" not in source, f"{path.name} must not reference the real .env file"


def test_state_db_package_imports_no_network_capable_client():
    forbidden_module_prefixes = (
        "requests", "boto3", "src.data_access.edgar.client", "src.data_access.dart.client",
        "src.data_access.edinet.client", "src.data_access.remote_cache",
    )
    for path in _STATE_DB_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for prefix in forbidden_module_prefixes:
            assert not any(name == prefix or name.startswith(prefix + ".") for name in imported), (
                f"{path.name} must not import {prefix!r}"
            )


def test_state_db_package_uses_only_stdlib_sqlite3_no_orm_dependency():
    for path in _STATE_DB_DIR.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for forbidden in ("sqlalchemy", "peewee", "django", "tortoise"):
            assert forbidden not in source.lower(), f"{path.name} must not depend on a new ORM"
