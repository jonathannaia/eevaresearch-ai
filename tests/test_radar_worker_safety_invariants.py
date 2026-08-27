"""Durable-State Phase 4M-0 — reinforcing structural proofs of the core
safety invariants requested for re-verification: the worker can never
set PUBLISHED/MONITORING/DISMISSED (structurally, not just by
convention), and the dashboard never triggers a scan on page render.
AST-based import/attribute checks, not string matching — mirrors the
established precedent in tests/test_theme_registry_loader.py /
test_theme_matching.py ("an AST-based structural test proves
theme_matching.py imports none of review_actions, signal_promotion,
backend_factory, container, or any SignalRepository")."""
from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RADAR_WORKER = _REPO_ROOT / "scripts" / "radar_worker.py"
_RADAR_INBOX = _REPO_ROOT / "src" / "ui" / "pages" / "radar_inbox.py"
_APP = _REPO_ROOT / "app.py"


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_radar_worker_never_imports_review_actions_or_a_signal_repository():
    """The worker never sets PUBLISHED/MONITORING/DISMISSED — proven
    structurally: it has no import path to the one function
    (`record_review_decision`) allowed to set them, and no import path
    to construct any SignalRepository at all."""
    imported = _imported_module_names(_RADAR_WORKER)
    forbidden = {
        "src.logic.review_actions",
        "src.data_access.interfaces",
        "src.data_access.live.radar_signal_repository",
        "src.data_access.state_db.signal_repository",
        "src.data_access.postgres_state_db.signal_repository",
    }
    assert not (imported & forbidden), imported & forbidden


def test_radar_worker_never_references_the_three_human_only_statuses():
    """Belt-and-suspenders on top of the import check above: no
    CandidateStatus.PUBLISHED/MONITORING/DISMISSED attribute access
    appears anywhere in the worker's own source at all — it doesn't even
    import CandidateStatus."""
    tree = ast.parse(_RADAR_WORKER.read_text(encoding="utf-8"))
    forbidden_attrs = {"PUBLISHED", "MONITORING", "DISMISSED"}
    offenders = [node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr in forbidden_attrs]
    assert offenders == []
    assert "src.models.models" not in _imported_module_names(_RADAR_WORKER)


def test_dashboard_never_imports_or_calls_the_radar_worker_module():
    """The Streamlit process must never itself run a scan loop or the
    worker's own main()/run_one_tick — confirmed for both the page most
    likely to be tempted (radar_inbox.py) and the app entry point."""
    for path in (_RADAR_INBOX, _APP):
        imported = _imported_module_names(path)
        assert "scripts.radar_worker" not in imported
        assert not any(name.startswith("scripts.radar_worker") for name in imported)


def test_dashboard_never_imports_the_resolver_bootstrap_script_either():
    """Identifier resolution stays a separate, manual, operator-run step
    — the dashboard must never be able to trigger it implicitly."""
    for path in (_RADAR_INBOX, _APP):
        imported = _imported_module_names(path)
        assert "scripts.resolve_tracked_identifiers" not in imported
