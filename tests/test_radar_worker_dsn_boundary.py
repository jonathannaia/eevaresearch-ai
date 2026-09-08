"""Durable-State Phase 4M-0 (correction) — the worker-only environment
boundary. `EDGE_RADAR_WORKER_DB_BACKEND`/`EDGE_RADAR_WORKER_STATE_DB_PATH`/
`EDGE_RADAR_WORKER_STATE_DB_URL` (and the matching `Settings` fields
`radar_worker_db_backend`/`radar_worker_state_db_path`/
`radar_worker_state_db_url`) belong exclusively to
`scripts/radar_worker.py`'s own process. The Streamlit dashboard
(`app.py`, `src/data_access/container.py`, and every file under
`src/ui/`) must never access these fields — an earlier draft of
Phase 4M-0's Radar Inbox status expander violated this by building a
worker-shaped `Settings` object from within the dashboard process; this
file guards against that regressing.

Structural (AST-based), not string-matching: a docstring explaining
*why* a field must not be touched legitimately mentions its name in
prose, which a naive substring guard would flag as a false positive
(the same class of problem already documented for the DECISIONS.md
prose-scanning guards elsewhere in this suite). Parsing real attribute
access (`ast.Attribute` nodes) sidesteps that entirely: a docstring
string literal produces no such node."""
from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKER_ONLY_FIELDS = frozenset({
    "radar_worker_db_backend", "radar_worker_state_db_path", "radar_worker_state_db_url",
})


def _dashboard_files() -> list[Path]:
    files = [_REPO_ROOT / "app.py", _REPO_ROOT / "src" / "data_access" / "container.py"]
    files += sorted((_REPO_ROOT / "src" / "ui").rglob("*.py"))
    return [f for f in files if f.exists()]


def _attribute_names_accessed(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}


def test_no_dashboard_or_ui_file_accesses_worker_only_settings_fields():
    offenders = []
    for path in _dashboard_files():
        accessed = _attribute_names_accessed(path) & _WORKER_ONLY_FIELDS
        if accessed:
            offenders.append(f"{path.relative_to(_REPO_ROOT)}: accesses {sorted(accessed)}")
    assert offenders == [], offenders


def test_radar_worker_script_does_consume_the_worker_only_fields():
    """Sanity/symmetry check: the fields exist to be used somewhere —
    confirms this guard isn't trivially passing because nothing anywhere
    references them at all."""
    accessed = _attribute_names_accessed(_REPO_ROOT / "scripts" / "radar_worker.py")
    assert _WORKER_ONLY_FIELDS <= accessed


def test_radar_inbox_worker_status_reads_the_dashboards_own_backend_fields_only():
    """The corrected read path: the status expander must read
    settings.db_backend/settings.state_db_url — the same, already
    existing (Phase 4B) dashboard Postgres/SQLite bridge every other
    read in this module uses — never build a worker-shaped Settings
    object of its own."""
    accessed = _attribute_names_accessed(_REPO_ROOT / "src" / "ui" / "pages" / "radar_inbox.py")
    assert "db_backend" in accessed
    assert not (accessed & _WORKER_ONLY_FIELDS)
