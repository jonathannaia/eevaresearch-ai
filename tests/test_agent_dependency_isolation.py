"""Autonomous Research Agent — dependency isolation (design/
AUTONOMOUS_RESEARCH_AGENT_PRODUCTION_READINESS_2026_09_17.md, §7.2 item 3).

The agent runtime (claude-agent-sdk, which bundles a platform-specific
native CLI binary, and mcp) must never be installed by the dashboard /
Render web service or the Radar worker while the agent stays disabled.
Those services install requirements.txt; the agent pins live only in
requirements-agent.txt, pinned exactly (never loosened), and CI installs
the agent file so the agent suites keep running there."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_PINS = ("claude-agent-sdk==0.2.155", "mcp==2.2.0")


def _requirement_lines(path: Path) -> list[str]:
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#")[0].strip()
        if line:
            lines.append(line)
    return lines


def _package_name(line: str) -> str:
    for separator in ("==", ">=", "<=", "~=", "<", ">", "[", ";"):
        line = line.split(separator)[0]
    return line.strip().lower().replace("_", "-")


def test_dashboard_requirements_exclude_the_agent_runtime():
    names = {_package_name(line) for line in _requirement_lines(REPO_ROOT / "requirements.txt")}
    assert "claude-agent-sdk" not in names
    assert "mcp" not in names


def test_agent_requirements_extend_the_dashboard_set_with_exact_pins():
    lines = _requirement_lines(REPO_ROOT / "requirements-agent.txt")
    assert lines[0] == "-r requirements.txt"
    assert sorted(lines[1:]) == sorted(AGENT_PINS)


def test_ci_installs_the_agent_requirements_so_agent_suites_can_import_them():
    workflow = (REPO_ROOT / ".github" / "workflows" / "manual-scan.yml").read_text(encoding="utf-8")
    assert "pip install -r requirements-agent.txt" in workflow
