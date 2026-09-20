"""Runtime-safety blockers E1, E3 and E4, plus the MCP server's own
refusal path, which had no test at all.

Nothing here starts a model session, spawns the CLI, opens a socket or
reads a credential from anywhere but a constructed Settings object."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest

from src.config.settings import Settings
from src.mcp_agent import agent_session, server
from src.mcp_agent.contracts import SessionScope

REPO_ROOT = Path(__file__).parent.parent
TOKEN = "s3cr3t-service-token-value-0123456789"


def _scope() -> SessionScope:
    return SessionScope(session_id="sess-1", candidate_id="cand-1", issuer_id="coreweave",
                        source_name="SEC EDGAR", seed_document_id="acc-1", started_at="2026-09-20T00:00:00+00:00")


def _options(settings: Settings):
    scope = _scope()
    return agent_session.build_options(settings, scope, agent_session.SessionHooks(scope=scope, cache_dir=settings.cache_dir))


# --- E1: no secret may reach the CLI command line -------------------------------

def test_the_stdio_mcp_config_carries_only_the_non_secret_session_scope(tmp_path):
    settings = Settings(cache_dir=tmp_path, research_agent_service_token=TOKEN, dart_api_key="dart-key-secret",
                        edinet_subscription_key="edinet-key-secret", state_db_url="postgresql://u:pw@host/db")
    env = _options(settings).mcp_servers[agent_session.SERVER_KEY]["env"]
    assert set(env) == {
        "EEVA_AGENT_SESSION_ID", "EEVA_AGENT_CANDIDATE_ID", "EEVA_AGENT_ISSUER_ID",
        "EEVA_AGENT_SOURCE_NAME", "EEVA_AGENT_SEED_DOCUMENT_ID",
    }
    assert env["EEVA_AGENT_SESSION_ID"] == "sess-1" and env["EEVA_AGENT_SOURCE_NAME"] == "SEC EDGAR"


@pytest.mark.parametrize("secret", [TOKEN, "dart-key-secret", "edinet-key-secret", "postgresql://u:pw@host/db"])
def test_no_secret_appears_anywhere_in_the_serialized_mcp_config(tmp_path, secret):
    """The SDK serializes this structure verbatim into --mcp-config on the
    CLI's argv, which is world-readable through /proc/<pid>/cmdline."""
    settings = Settings(cache_dir=tmp_path, research_agent_service_token=TOKEN, dart_api_key="dart-key-secret",
                        edinet_subscription_key="edinet-key-secret", state_db_url="postgresql://u:pw@host/db")
    serialized = json.dumps(_options(settings).mcp_servers, default=str)
    assert secret not in serialized


def test_the_options_never_copy_the_process_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SOME_UNRELATED_SECRET", "must-not-be-copied")
    settings = Settings(cache_dir=tmp_path, research_agent_service_token=TOKEN)
    serialized = json.dumps(_options(settings).mcp_servers, default=str)
    assert "must-not-be-copied" not in serialized


def test_the_token_reaches_the_child_through_the_inherited_environment(tmp_path, monkeypatch):
    monkeypatch.delenv(server.ENV_PRESENTED_TOKEN, raising=False)
    settings = Settings(cache_dir=tmp_path, research_agent_service_token=TOKEN)
    agent_session.present_service_token(settings)
    assert os.environ[server.ENV_PRESENTED_TOKEN] == TOKEN


def test_presenting_a_token_that_does_not_exist_raises_instead_of_starting(tmp_path, monkeypatch):
    monkeypatch.delenv(server.ENV_PRESENTED_TOKEN, raising=False)
    with pytest.raises(ValueError):
        agent_session.present_service_token(Settings(cache_dir=tmp_path, research_agent_service_token=None))
    assert server.ENV_PRESENTED_TOKEN not in os.environ


def test_build_options_still_refuses_without_a_configured_token(tmp_path):
    with pytest.raises(ValueError):
        _options(Settings(cache_dir=tmp_path, research_agent_service_token=None))


# --- the MCP server's refusal path (previously untested) ---------------------------

def _env(**overrides) -> dict[str, str]:
    env = {
        server.ENV_SESSION_ID: "sess-1", server.ENV_CANDIDATE_ID: "cand-1", server.ENV_ISSUER_ID: "coreweave",
        server.ENV_SOURCE_NAME: "SEC EDGAR", server.ENV_SEED_DOCUMENT_ID: "acc-1",
        server.ENV_PRESENTED_TOKEN: TOKEN,
    }
    env.update({k: v for k, v in overrides.items() if v is not None})
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
    return env


def test_the_server_refuses_when_no_token_is_configured(tmp_path):
    with pytest.raises(server.ServerRefused) as excinfo:
        server.build_context_from_env(_env(), settings=Settings(cache_dir=tmp_path, research_agent_service_token=None))
    assert "token" in str(excinfo.value)


def test_the_server_refuses_when_no_token_is_presented(tmp_path):
    settings = Settings(cache_dir=tmp_path, research_agent_service_token=TOKEN)
    with pytest.raises(server.ServerRefused):
        server.build_context_from_env(_env(**{server.ENV_PRESENTED_TOKEN: None}), settings=settings)


@pytest.mark.parametrize("presented", ["wrong-token", TOKEN + "x", TOKEN[:-1], "", TOKEN.upper()])
def test_the_server_refuses_a_mismatched_token(tmp_path, presented):
    settings = Settings(cache_dir=tmp_path, research_agent_service_token=TOKEN)
    with pytest.raises(server.ServerRefused):
        server.build_context_from_env(_env(**{server.ENV_PRESENTED_TOKEN: presented}), settings=settings)


def test_the_server_accepts_the_exact_token_and_builds_the_scope(tmp_path):
    settings = Settings(cache_dir=tmp_path, research_agent_service_token=TOKEN)
    ctx = server.build_context_from_env(_env(), settings=settings)
    assert (ctx.scope.session_id, ctx.scope.candidate_id, ctx.scope.source_name) == ("sess-1", "cand-1", "SEC EDGAR")


@pytest.mark.parametrize("missing", [
    "EEVA_AGENT_SESSION_ID", "EEVA_AGENT_CANDIDATE_ID", "EEVA_AGENT_ISSUER_ID", "EEVA_AGENT_SEED_DOCUMENT_ID",
])
def test_the_server_refuses_a_malformed_scope(tmp_path, missing):
    settings = Settings(cache_dir=tmp_path, research_agent_service_token=TOKEN)
    with pytest.raises(server.ServerRefused) as excinfo:
        server.build_context_from_env(_env(**{missing: None}), settings=settings)
    assert "scope" in str(excinfo.value)


def test_the_server_refuses_an_unknown_source(tmp_path):
    settings = Settings(cache_dir=tmp_path, research_agent_service_token=TOKEN)
    with pytest.raises(server.ServerRefused) as excinfo:
        server.build_context_from_env(_env(**{server.ENV_SOURCE_NAME: "Bloomberg"}), settings=settings)
    assert "source_name" in str(excinfo.value)


# --- E3: the session holds no path to a candidate row or the public store ------------

def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_the_publication_tool_no_longer_imports_the_public_store_or_candidate_writes():
    imports = _imports(REPO_ROOT / "src" / "mcp_agent" / "tools" / "request_publication_decision.py")
    assert not any("verified_update_store" in name for name in imports)
    assert not any("backend_factory" in name for name in imports)


def test_the_publication_tool_writes_only_its_own_decision():
    source = (REPO_ROOT / "src" / "mcp_agent" / "tools" / "request_publication_decision.py").read_text(encoding="utf-8")
    for forbidden in ("_write_candidate_status", "append_verified_updates", "update_candidate", "reviewed_at"):
        assert forbidden not in source, forbidden
    assert "packet_store.record_decision" in source


def test_the_moved_writes_exist_but_nothing_in_the_release_calls_them():
    moved = REPO_ROOT / "src" / "mcp_agent" / "publication_writes.py"
    source = moved.read_text(encoding="utf-8")
    assert "_write_candidate_status" in source and "_verified_updates" in source
    # A docstring may point at the module; nothing may import or call it.
    candidates = [p for p in (REPO_ROOT / "src").rglob("*.py") if p != moved]
    candidates += list((REPO_ROOT / "scripts").glob("*.py"))
    callers = [path for path in candidates if any(
        name.endswith("publication_writes") or ".publication_writes" in name for name in _imports(path)
    )]
    assert callers == [], f"publish-mode writes are wired up in {callers}"


# --- E4: evidence comes from the stored candidate, never a fetch ---------------------

def test_the_excerpt_tools_read_the_stored_candidate_and_never_an_adapter_fetch():
    for name in ("get_filing_evidence_excerpt.py", "retrieve_filing_table_or_locator.py"):
        source = (REPO_ROOT / "src" / "mcp_agent" / "tools" / name).read_text(encoding="utf-8")
        assert "fetch_stored_excerpt" in source, name
        assert "SOURCE_ADAPTERS" not in source, name


def test_no_agent_tool_can_reach_an_adapter_document_service():
    tools = REPO_ROOT / "src" / "mcp_agent" / "tools"
    for path in tools.glob("*.py"):
        if path.name == "_common.py":
            continue
        imports = _imports(path)
        assert not any("document_service" in name for name in imports), path.name
