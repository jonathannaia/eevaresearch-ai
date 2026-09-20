"""Agent Review scope guard — structural, not behavioural.

The dashboard must never import the agent runtime. That rule already
existed (tests/test_research_agent_worker_scope_guard.py forbids
src.mcp_agent, claude_agent_sdk, mcp and the worker anywhere under
src/ui), and this page is the first UI surface that reads agent data, so
it is the first real chance to break it.

The subtlety is transitive. src/logic/publication_policy.py and
src/logic/agent_scheduler.py both look like innocuous logic modules, but
both reach src.mcp_agent — the policy imports the tool contracts, and
the scheduler imports the policy. Importing either from the page would
pull the whole agent runtime, including the Agent SDK, into the
dashboard process. The shared liveness constants therefore live in
src/logic/agent_health.py, which depends on nothing but the record
models.

This file walks the real import graph rather than the page's own import
list, because a one-line import of a module that itself imports the
runtime is exactly the mistake that would otherwise pass."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
PAGE = REPO_ROOT / "src" / "ui" / "pages" / "agent_review.py"
HEALTH = REPO_ROOT / "src" / "logic" / "agent_health.py"

FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "src.mcp_agent", "scripts.research_agent_worker", "scripts.agent_control", "claude_agent_sdk", "mcp",
)


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _matches(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(p + ".") for p in prefixes)


def _module_path(name: str) -> Path | None:
    candidate = REPO_ROOT / Path(*name.split("."))
    for path in (candidate.with_suffix(".py"), candidate / "__init__.py"):
        if path.exists():
            return path
    return None


def _transitive_imports(start: Path) -> set[str]:
    """The real graph: what this file pulls in, and what those pull in."""
    seen_files, seen_names = {start}, set()
    queue = [start]
    while queue:
        for name in _imports(queue.pop()):
            seen_names.add(name)
            path = _module_path(name)
            if path is not None and path not in seen_files:
                seen_files.add(path)
                queue.append(path)
    return seen_names


def test_the_page_imports_no_agent_runtime_directly():
    offenders = sorted(n for n in _imports(PAGE) if _matches(n, FORBIDDEN_PREFIXES))
    assert offenders == [], offenders


def test_the_page_imports_no_agent_runtime_transitively_either():
    """The one that actually bites: a single import of a module that
    itself reaches src.mcp_agent would drag the Agent SDK into the
    dashboard process."""
    offenders = sorted(n for n in _transitive_imports(PAGE) if _matches(n, FORBIDDEN_PREFIXES))
    assert offenders == [], offenders


@pytest.mark.parametrize("module", ["src.logic.publication_policy", "src.logic.agent_scheduler"])
def test_the_modules_that_look_safe_but_are_not_stay_out_of_the_page(module):
    """Both of these reach src.mcp_agent. Naming them keeps the reason
    visible to whoever is tempted to import one for a constant."""
    assert not any(_matches(n, (module,)) for n in _transitive_imports(PAGE)), module
    assert any(_matches(n, ("src.mcp_agent",)) for n in _transitive_imports(_module_path(module)))


def test_the_health_module_is_genuinely_dependency_light():
    """It exists so the page has somewhere safe to get the liveness
    constants from; if it grew a runtime dependency it would stop
    serving that purpose."""
    offenders = sorted(n for n in _transitive_imports(HEALTH) if _matches(n, FORBIDDEN_PREFIXES))
    assert offenders == [], offenders


def test_the_scheduler_takes_the_heartbeat_constant_from_the_health_module():
    """Not the other way round — that direction would re-create the
    dependency this whole file exists to prevent."""
    scheduler = REPO_ROOT / "src" / "logic" / "agent_scheduler.py"
    assert "src.logic.agent_health" in _imports(scheduler)
    assert "src.logic.agent_scheduler" not in _imports(HEALTH)


def test_the_page_never_writes_anything():
    """Read-only by construction: no write method of any repository, and
    no Streamlit control that could submit one."""
    source = PAGE.read_text(encoding="utf-8")
    for forbidden in (
        "save_packet", "record_decision", "append_audit_events", "enqueue_job", "claim_job", "finish_job",
        "set_mode_override", "start_run", "complete_run", "update_candidate", "save_candidates",
        "reclaim_expired_leases", "st.button", "st.form", "st.download_button",
    ):
        assert forbidden not in source, forbidden


def test_the_page_uses_only_semantic_theme_tokens_and_no_hard_coded_colour():
    """A literal colour would be correct in exactly one theme."""
    import re

    source = PAGE.read_text(encoding="utf-8")
    literals = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)", source)
    assert literals == [], literals
    assert "var(--surface" in source and "var(--text" in source and "var(--border" in source


def test_the_page_never_borrows_the_evidence_palette_for_generic_status():
    """--ev-supports / --ev-contradicts mean 'this evidence supports or
    contradicts a claim'. Using them to colour a job state or a mode
    would quietly overload a reader's learned meaning."""
    source = PAGE.read_text(encoding="utf-8")
    # var(...) is what actually renders; the prose above the helper may
    # name these tokens to explain why they are off limits.
    assert "var(--ev-" not in source
    assert "var(--cat-" not in source


def test_the_page_is_registered_as_a_hidden_route():
    app = (REPO_ROOT / "app.py").read_text(encoding="utf-8")
    assert 'pages["agent_review"]' in app
    assert 'url_path="agent-review"' in app
    assert 'visibility="hidden"' in app.split('pages["agent_review"]')[1][:400]


def test_the_route_is_known_to_the_theme_bootstrap():
    """ROUTE_PATHS drives the pre-paint theme script; a missing entry
    means this page flashes the wrong theme on first load."""
    from src.ui.theme import ROUTE_PATHS

    assert "/agent-review" in ROUTE_PATHS


def test_the_page_is_absent_from_every_navigation_list():
    from src.ui.ui import HIDDEN_FROM_NAV, PRIMARY_NAV, SYSTEM_NAV

    keys = {key for key, _ in PRIMARY_NAV + SYSTEM_NAV + HIDDEN_FROM_NAV}
    assert "agent_review" not in keys
