"""Autonomous Research Agent, Phase 4 — scope guard for the worker and
the Agent SDK session wrapper (design §8.3, §8.4, §12 Phase 4), modeled
on tests/test_daily_news_scope_guard.py / test_company_discovery_scope_
guard.py. Structural (AST) plus behavioral: the worker is dormant by
default, fails closed without its service credential, and can never
become an ingestion path."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.models.models import CandidateSignal, CandidateStatus, FilingEvent

REPO_ROOT = Path(__file__).parent.parent
AGENT_SESSION = REPO_ROOT / "src" / "mcp_agent" / "agent_session.py"
SERVER = REPO_ROOT / "src" / "mcp_agent" / "server.py"
WORKER = REPO_ROOT / "scripts" / "research_agent_worker.py"

UI_FORBIDDEN_PREFIXES: tuple[str, ...] = ("scripts.research_agent_worker", "src.mcp_agent", "claude_agent_sdk", "mcp")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _matches(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(p + ".") for p in prefixes)


def _code_files() -> list[Path]:
    return [REPO_ROOT / "app.py", *sorted((REPO_ROOT / "src").rglob("*.py")), *sorted((REPO_ROOT / "scripts").rglob("*.py"))]


def test_app_and_ui_never_import_the_worker_agent_package_or_either_sdk():
    ui_files = [REPO_ROOT / "app.py", *sorted((REPO_ROOT / "src" / "ui").rglob("*.py"))]
    offenders = [
        f"{p.relative_to(REPO_ROOT)}: imports {name!r}"
        for p in ui_files if p.exists() for name in sorted(_imports(p)) if _matches(name, UI_FORBIDDEN_PREFIXES)
    ]
    assert not offenders, offenders


def test_claude_agent_sdk_is_imported_only_by_the_session_wrapper():
    offenders = [
        f"{p.relative_to(REPO_ROOT)}" for p in _code_files()
        if p != AGENT_SESSION and any(_matches(n, ("claude_agent_sdk",)) for n in _imports(p))
    ]
    assert not offenders, offenders
    assert any(_matches(n, ("claude_agent_sdk",)) for n in _imports(AGENT_SESSION))


def test_mcp_package_is_imported_only_by_the_server():
    offenders = [
        f"{p.relative_to(REPO_ROOT)}" for p in _code_files()
        if p != SERVER and any(_matches(n, ("mcp",)) for n in _imports(p))
    ]
    assert not offenders, offenders
    assert any(_matches(n, ("mcp",)) for n in _imports(SERVER))


def test_worker_is_dormant_by_default_and_never_ticks(monkeypatch):
    monkeypatch.delenv("EDGE_RESEARCH_AGENT_LIVE_ENABLED", raising=False)
    import scripts.research_agent_worker as worker

    def _never(*args, **kwargs):
        raise AssertionError("run_one_tick was called while the worker is dormant")

    monkeypatch.setattr(worker, "run_one_tick", _never)
    assert worker.main([]) == 0


def test_worker_refuses_to_run_live_without_its_service_credential(monkeypatch):
    monkeypatch.setenv("EDGE_RESEARCH_AGENT_LIVE_ENABLED", "1")
    monkeypatch.delenv("EDGE_RESEARCH_AGENT_SERVICE_TOKEN", raising=False)
    import scripts.research_agent_worker as worker

    def _never(*args, **kwargs):
        raise AssertionError("run_one_tick was called without a service token")

    monkeypatch.setattr(worker, "run_one_tick", _never)
    assert worker.main([]) == 2


def test_worker_can_never_become_an_ingestion_path():
    """Eligibility moved into src/logic/agent_scheduler.py and narrowed to
    a single status, so this guard now asks the rule directly. It is a
    stronger guarantee than the original EXTRACTED/TRANSLATED filter: a
    candidate is only eligible once the pipelines have already retrieved
    its text AND stored the excerpt, which is what makes the agent
    incapable of being a retrieval path."""
    from src.logic import agent_scheduler
    from src.models.models import ExcerptQuality, ExtractionState, TranslationState

    filing = FilingEvent(
        source_name="SEC EDGAR", corp_code="0001045810", corp_name="NVIDIA", stock_code="NVDA",
        report_nm="10-Q", rcept_no="acc-1", rcept_dt="20260901", flr_nm="NVIDIA", pblntf_ty="A",
        retrieved_at="2026-09-01T00:00:00+00:00",
    )

    def _with(status):
        return CandidateSignal(
            id="c", filing=filing, matched_rules=["r"], confidence="High", status=status,
            extraction_state=ExtractionState.EXTRACTED, translation_state=TranslationState.NOT_REQUESTED,
            excerpt_quality=ExcerptQuality.USABLE_TEXT, excerpt_original="text",
        )

    for status in (CandidateStatus.NEW_FILING_EVENT, CandidateStatus.CANDIDATE_DETECTED,
                   CandidateStatus.QUEUED_FOR_PROCESSING, CandidateStatus.RETRIEVAL_IN_PROGRESS,
                   CandidateStatus.EXTRACTION_PENDING):
        assert agent_scheduler.is_eligible(_with(status)) is False, status

    eligible = [s for s in CandidateStatus if agent_scheduler.is_eligible(_with(s))]
    assert eligible == [CandidateStatus.NEEDS_REVIEW]
    assert agent_scheduler.MAX_SESSIONS_PER_TICK <= 2 and agent_scheduler.MAX_ATTEMPTS_PER_JOB <= 3
    assert agent_scheduler.MAX_SESSIONS_PER_DAY <= 50


@pytest.mark.parametrize("module_path", [AGENT_SESSION, SERVER, WORKER])
def test_agent_runtime_modules_never_import_company_discovery_or_pipeline_writes(module_path):
    forbidden = ("src.data_access.company_discovery", "src.data_access.dart.radar_pipeline", "src.data_access.edgar.edgar_pipeline", "src.data_access.edinet.edinet_pipeline")
    offenders = sorted(n for n in _imports(module_path) if _matches(n, forbidden))
    assert not offenders, offenders
