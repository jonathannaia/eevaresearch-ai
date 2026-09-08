"""Durable-State Phase 4H-1 — scripts/hosted_signals_preview.py bootstrap
tests.

Every test here uses mocks/monkeypatching only. No real DSN, database,
Docker, network call, Streamlit server, `.env` file, or secret is ever
used — the module's own internal collaborators (backend_factory,
with_chrome, empty_state, signals.render) are patched at their call
boundaries, so no Postgres connection is ever attempted and no live
Streamlit rendering context is ever required. This file never imports or
calls app.py, container.get_repositories(), Docker, or any network API.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts import hosted_signals_preview as bootstrap
from src.config.settings import Settings

_LEAK_MARKERS = ("SHOULD_NOT_LEAK_HOST", "SHOULD_NOT_LEAK_DSN", "SHOULD_NOT_LEAK_PASSWORD")
_FAKE_DSN = "postgresql://fake-only-for-this-test/db"


# --- _read_preview_dsn: the one environment read in the whole module ---

def test_missing_dsn_env_var_returns_none(monkeypatch):
    monkeypatch.delenv(bootstrap._PREVIEW_DSN_ENV_VAR, raising=False)
    assert bootstrap._read_preview_dsn() is None


def test_present_dsn_env_var_is_read_verbatim(monkeypatch):
    monkeypatch.setenv(bootstrap._PREVIEW_DSN_ENV_VAR, _FAKE_DSN)
    assert bootstrap._read_preview_dsn() == _FAKE_DSN


# --- Missing-DSN path: static unavailable state, nothing constructed ---

def test_missing_dsn_renders_static_unavailable_state_and_builds_nothing(monkeypatch):
    monkeypatch.delenv(bootstrap._PREVIEW_DSN_ENV_VAR, raising=False)

    with patch.object(bootstrap, "with_chrome", side_effect=lambda fn, *a, **k: fn) as mock_with_chrome, \
         patch.object(bootstrap, "empty_state") as mock_empty_state, \
         patch.object(bootstrap, "_build_hosted_signal_repository") as mock_build, \
         patch("src.ui.pages.signals.render") as mock_render:
        bootstrap._run_preview()

    mock_build.assert_not_called()
    mock_render.assert_not_called()
    mock_with_chrome.assert_called_once_with(bootstrap._render_hosted_unavailable, "signals", show_sidebar=False)
    mock_empty_state.assert_called_once_with(
        bootstrap._UNAVAILABLE_TITLE, bootstrap._UNAVAILABLE_DETAIL, key=bootstrap._UNAVAILABLE_KEY,
    )


# --- Construction-failure path: static unavailable state, no leakage ---

def test_construction_failure_renders_static_unavailable_state_with_no_leaked_markers(monkeypatch, capsys):
    monkeypatch.setenv(bootstrap._PREVIEW_DSN_ENV_VAR, _FAKE_DSN)
    leaky_exc = RuntimeError(
        "connection failed: SHOULD_NOT_LEAK_HOST=hosted-db.example.internal "
        "SHOULD_NOT_LEAK_DSN=postgresql://user:pw@hosted-db.example.internal/db "
        "SHOULD_NOT_LEAK_PASSWORD=hunter2"
    )

    with patch.object(bootstrap, "_build_hosted_signal_repository", side_effect=leaky_exc), \
         patch.object(bootstrap, "with_chrome", side_effect=lambda fn, *a, **k: fn), \
         patch.object(bootstrap, "empty_state") as mock_empty_state, \
         patch("src.ui.pages.signals.render") as mock_render:
        bootstrap._run_preview()

    mock_render.assert_not_called()
    mock_empty_state.assert_called_once_with(
        bootstrap._UNAVAILABLE_TITLE, bootstrap._UNAVAILABLE_DETAIL, key=bootstrap._UNAVAILABLE_KEY,
    )

    rendered_args = " ".join(str(a) for a in mock_empty_state.call_args.args)
    rendered_kwargs = " ".join(f"{k}={v}" for k, v in mock_empty_state.call_args.kwargs.items())
    captured = capsys.readouterr()
    for marker in _LEAK_MARKERS:
        assert marker not in rendered_args
        assert marker not in rendered_kwargs
        assert marker not in captured.out
        assert marker not in captured.err


# --- Success path: explicit Settings, existing factory, injected render ---

def test_successful_construction_uses_only_preview_dsn_and_injects_repository_into_render(monkeypatch):
    monkeypatch.setenv(bootstrap._PREVIEW_DSN_ENV_VAR, _FAKE_DSN)
    monkeypatch.delenv("EDGE_DB_BACKEND", raising=False)
    monkeypatch.delenv("EDGE_STATE_DB_URL", raising=False)

    fake_repo = MagicMock(name="fake_hosted_signal_repository")
    captured: dict = {}

    def _fake_get_signal_repository(settings):
        captured["settings"] = settings
        return fake_repo

    with patch.object(bootstrap.backend_factory, "get_signal_repository", side_effect=_fake_get_signal_repository) as mock_get_repo, \
         patch.object(bootstrap, "with_chrome") as mock_with_chrome, \
         patch("src.ui.pages.signals.render") as mock_render:
        bootstrap._run_preview()

        mock_get_repo.assert_called_once()
        settings_used = captured["settings"]
        assert isinstance(settings_used, Settings)
        assert settings_used.db_backend == "postgres"
        assert settings_used.state_db_url == _FAKE_DSN

        mock_with_chrome.assert_called_once()
        call_args = mock_with_chrome.call_args
        rendered_fn = call_args.args[0]
        assert call_args.args[1] == "signals"
        assert call_args.kwargs == {"show_sidebar": False}

        # Invoked while the signals.render patch above is still active —
        # this is the closure with_chrome would have wrapped and called.
        rendered_fn()
        mock_render.assert_called_once_with(signal_repository=fake_repo)


def test_ambient_edge_db_backend_and_state_db_url_cannot_affect_the_bootstrap(monkeypatch):
    """A non-empty EDGE_DB_BACKEND/EDGE_STATE_DB_URL in the operator's
    shell — set for an unrelated reason — must never change what this
    bootstrap constructs or connects to."""
    monkeypatch.setenv(bootstrap._PREVIEW_DSN_ENV_VAR, _FAKE_DSN)
    monkeypatch.setenv("EDGE_DB_BACKEND", "sqlite")
    monkeypatch.setenv("EDGE_STATE_DB_URL", "postgresql://poison-should-never-be-used/db")

    captured: dict = {}

    def _fake_get_signal_repository(settings):
        captured["settings"] = settings
        return MagicMock(name="fake_hosted_signal_repository")

    with patch.object(bootstrap.backend_factory, "get_signal_repository", side_effect=_fake_get_signal_repository), \
         patch.object(bootstrap, "with_chrome"), \
         patch("src.ui.pages.signals.render"):
        bootstrap._run_preview()

    settings_used = captured["settings"]
    assert settings_used.db_backend == "postgres"
    assert settings_used.state_db_url == _FAKE_DSN


# --- Structural guards on the script's own source ---

def test_bootstrap_script_imports_nothing_from_app_container_or_get_settings():
    source = Path(bootstrap.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imported.add(f"{module}.{alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)

    assert "src.config.settings.get_settings" not in imported
    assert not any(name == "app" or name.startswith("app.") for name in imported)
    assert not any("container" in name for name in imported)
    assert not any("get_repositories" in name for name in imported)


def test_bootstrap_script_reads_exactly_one_environment_variable():
    """Structural proof, not just behavioral: exactly one os.environ.get/
    os.getenv call exists anywhere in the file, and its argument is the
    _PREVIEW_DSN_ENV_VAR name — never a hardcoded different variable."""
    source = Path(bootstrap.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    env_calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_os_environ_get = (
            isinstance(func, ast.Attribute) and func.attr == "get"
            and isinstance(func.value, ast.Attribute) and func.value.attr == "environ"
            and isinstance(func.value.value, ast.Name) and func.value.value.id == "os"
        )
        is_os_getenv = (
            isinstance(func, ast.Attribute) and func.attr == "getenv"
            and isinstance(func.value, ast.Name) and func.value.id == "os"
        )
        if is_os_environ_get or is_os_getenv:
            env_calls.append(node)

    assert len(env_calls) == 1
    arg = env_calls[0].args[0]
    assert isinstance(arg, ast.Name)
    assert arg.id == "_PREVIEW_DSN_ENV_VAR"


def test_bootstrap_script_does_not_start_streamlit_server_or_set_bind_settings():
    source = Path(bootstrap.__file__).read_text(encoding="utf-8")
    for token in ("bootstrap.run", "server.address", "server.port", "st.set_page_config"):
        assert token not in source


# --- Repository-root sys.path bootstrap (fixes ModuleNotFoundError: No
# module named 'src' when Streamlit executes this file from scripts/) ---
#
# These reload the file fresh via importlib (bypassing sys.modules and
# pytest.ini's own pythonpath=. setting) so they exercise the script's
# own sys.path-fixing code, not this repo's test-runner configuration.

def _exec_launcher_fresh(module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, bootstrap.__file__)
    fresh_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh_module)
    return fresh_module


def test_importing_launcher_succeeds_when_repo_root_absent_from_sys_path(monkeypatch):
    repo_root = str(Path(bootstrap.__file__).resolve().parent.parent)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != repo_root])
    assert repo_root not in sys.path

    _exec_launcher_fresh("hosted_signals_preview_fresh_import_test_1")

    assert repo_root in sys.path


def test_launcher_derives_repo_root_from_file_location_not_cwd(monkeypatch):
    real_repo_root = str(Path(bootstrap.__file__).resolve().parent.parent)
    decoy_cwd = "/definitely/not/the/repo/root"
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != real_repo_root])
    monkeypatch.setattr(os, "getcwd", lambda: decoy_cwd)

    _exec_launcher_fresh("hosted_signals_preview_fresh_import_test_2")

    assert real_repo_root in sys.path
    assert decoy_cwd not in sys.path


def test_repo_root_bootstrap_does_not_duplicate_an_already_present_path(monkeypatch):
    repo_root = str(Path(bootstrap.__file__).resolve().parent.parent)
    if repo_root not in sys.path:
        monkeypatch.setattr(sys, "path", [repo_root, *sys.path])
    before_count = sys.path.count(repo_root)

    _exec_launcher_fresh("hosted_signals_preview_fresh_import_test_3")

    assert sys.path.count(repo_root) == before_count
