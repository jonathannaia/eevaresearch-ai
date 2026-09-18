"""Autonomous Research Agent — structural scope guard for the MCP tool
surface (design §2.7, §8.4, §12 Phase 2), modeled on
tests/test_company_discovery_scope_guard.py. AST-based (not a text
search — these modules' docstrings *describe* forbidden imports, which
would false-positive a grep).

Enforces, structurally rather than by convention:
- nothing under src/mcp_agent imports Company Discovery, a pipeline
  write path, or a raw network/shell/database client;
- no hard-coded remote host anywhere under src/mcp_agent (URLs are
  built only from allowlisted/registered domains);
- the Streamlit app and every UI page never import the agent package or
  either SDK — the agent runs only in its own worker/server processes;
- the server's tool set is exactly the budget allowlist, one module per
  tool, each exposing run() and a NAME equal to its filename."""
from __future__ import annotations

import ast
import re
from pathlib import Path

from src.mcp_agent import server
from src.mcp_agent.budgets import ALLOWED_TOOL_NAMES

REPO_ROOT = Path(__file__).parent.parent
AGENT_DIR = REPO_ROOT / "src" / "mcp_agent"
TOOLS_DIR = AGENT_DIR / "tools"

FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "src.data_access.company_discovery",
    "src.data_access.dart.radar_pipeline",
    "src.data_access.edgar.edgar_pipeline",
    "src.data_access.edinet.edinet_pipeline",
    "requests", "httpx", "urllib.request", "urllib3", "aiohttp",
    "subprocess", "socket", "shlex",
    "sqlite3", "psycopg", "boto3", "feedparser",
    "streamlit",
)
AGENT_ONLY_IMPORT_PREFIXES: tuple[str, ...] = ("src.mcp_agent", "claude_agent_sdk", "mcp")
_HOST_LITERAL = re.compile(r"https?://[A-Za-z0-9]")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _agent_files() -> list[Path]:
    return sorted(AGENT_DIR.rglob("*.py"))


def _matches(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(p + ".") for p in prefixes)


def test_agent_package_has_files_to_guard():
    assert len(_agent_files()) >= 12


def test_agent_package_never_imports_company_discovery_pipeline_writes_or_raw_clients():
    offenders = [
        f"{path.relative_to(REPO_ROOT)}: imports {name!r}"
        for path in _agent_files() for name in sorted(_imports(path)) if _matches(name, FORBIDDEN_IMPORT_PREFIXES)
    ]
    assert not offenders, offenders


def test_agent_package_has_no_hard_coded_remote_hosts():
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{n}: {line.strip()[:80]}"
        for path in _agent_files() for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _HOST_LITERAL.search(line)
    ]
    assert not offenders, offenders


def test_streamlit_app_and_ui_pages_never_import_the_agent_or_either_sdk():
    ui_files = [REPO_ROOT / "app.py", *sorted((REPO_ROOT / "src" / "ui").rglob("*.py"))]
    offenders = [
        f"{path.relative_to(REPO_ROOT)}: imports {name!r}"
        for path in ui_files if path.exists() for name in sorted(_imports(path)) if _matches(name, AGENT_ONLY_IMPORT_PREFIXES)
    ]
    assert not offenders, offenders


def test_server_tool_set_is_exactly_the_budget_allowlist():
    assert frozenset(server.TOOL_NAMES) == ALLOWED_TOOL_NAMES
    assert len(server.TOOL_NAMES) == 10 == len(set(server.TOOL_NAMES))


def test_one_module_per_tool_each_exposing_run_and_a_matching_name():
    for tool_name in server.TOOL_NAMES:
        path = TOOLS_DIR / f"{tool_name}.py"
        assert path.exists(), f"missing tool module {path.name}"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        assert "run" in functions, f"{path.name} has no run()"
        names = {
            target.id: node.value.value
            for node in tree.body if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
            for target in node.targets if isinstance(target, ast.Name)
        }
        assert names.get("NAME") == tool_name, f"{path.name}: NAME must equal the tool name"


def test_no_unexpected_tool_modules_exist():
    public = {p.stem for p in TOOLS_DIR.glob("*.py") if not p.name.startswith("_")}
    assert public == set(server.TOOL_NAMES), sorted(public ^ set(server.TOOL_NAMES))
