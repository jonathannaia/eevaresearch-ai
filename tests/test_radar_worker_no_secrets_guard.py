"""Durable-State Phase 4M-0 — no secret, DSN, real host, or test
credential was added to any tracked file in this phase. Same discipline
as test_backend_factory_postgres.py's own guard, applied to this
phase's own new/changed files: real local state paths, non-loopback
IPv4 literals, and suspicious hosted-provider keywords are all
forbidden; every DSN appearing in this phase's own files is a synthetic
placeholder (`<dsn>`, `<postgres DSN>`) or a deliberately-unreachable
loopback target used only to prove sanitized error handling."""
from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

_PHASE_4M0_FILES = (
    _REPO_ROOT / "scripts" / "radar_worker.py",
    _REPO_ROOT / "scripts" / "resolve_tracked_identifiers.py",
    _REPO_ROOT / "design" / "RADAR_WORKER_DEPLOYMENT.md",
    _REPO_ROOT / "src" / "data_access" / "state_db" / "scan_status_repository.py",
    _REPO_ROOT / "src" / "data_access" / "postgres_state_db" / "scan_status_repository.py",
    # radar_inbox.py is deliberately excluded here: it's a large,
    # mostly-pre-existing file (this phase only added the status
    # expander to it) already covered by test_backend_factory_phase2b.py's
    # own _PHASE2B_FILES guard for the forbidden-reference class that
    # actually applies to it; scanning the whole file here would false-
    # positive against long-standing, legitimate prose (e.g. "add the
    # missing configuration to your local .env"), the same class of
    # problem already documented for DECISIONS.md's own guards.
    _REPO_ROOT / "tests" / "test_radar_worker.py",
    _REPO_ROOT / "tests" / "test_resolve_tracked_identifiers.py",
    _REPO_ROOT / "tests" / "test_radar_inbox_worker_status.py",
    _REPO_ROOT / "tests" / "test_backend_factory_scan_status.py",
    _REPO_ROOT / "tests" / "test_state_db_scan_status_repository.py",
    _REPO_ROOT / "tests" / "test_state_db_postgres_scan_status_repository.py",
    _REPO_ROOT / "tests" / "test_radar_worker_dsn_boundary.py",
    _REPO_ROOT / "tests" / "test_radar_worker_safety_invariants.py",
    _REPO_ROOT / "tests" / "test_radar_worker_no_secrets_guard.py",
)

_FORBIDDEN_REAL_STATE_REFERENCES = (
    ".env",
    ".env.example",
    ".streamlit/secrets.toml",
    "data/cache",
    "data/edge_research.db",
)

_ALLOWED_HOST_LITERALS = frozenset({"127.0.0.1", "localhost"})
_SUSPICIOUS_HOST_KEYWORDS = (
    "neon.tech", "supabase", "turso", "amazonaws", "azure", "render.com", "railway.app", ".internal",
)
_IPV4_PATTERN = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")


def _contains_literal(source: str, needle: str) -> bool:
    return re.search(re.escape(needle) + r"(?![A-Za-z0-9_])", source) is not None


def _source_excluding_this_guards_own_constants(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if path.name == "test_radar_worker_no_secrets_guard.py":
        start = text.index("_PHASE_4M0_FILES = (")
        end = text.index("\n\n\ndef test_")
        return text[:start] + text[end:]
    return text


def test_phase4m0_files_never_reference_real_local_state_or_non_loopback_hosts():
    offenders = []
    for path in _PHASE_4M0_FILES:
        source = _source_excluding_this_guards_own_constants(path)
        for forbidden in _FORBIDDEN_REAL_STATE_REFERENCES:
            if _contains_literal(source, forbidden):
                offenders.append(f"{path.name}: contains forbidden reference {forbidden!r}")
        for ip in _IPV4_PATTERN.findall(source):
            if ip not in _ALLOWED_HOST_LITERALS:
                offenders.append(f"{path.name}: contains non-loopback IPv4 literal {ip!r}")
        # Case-sensitive, not source.lower(): every real hosted-provider
        # domain/suffix this guard watches for is always written
        # lowercase in practice, whereas lowercasing the source first
        # produced a false positive against a real, conventionally-
        # uppercase Python enum member once autonomous Theme candidate
        # detection started referencing it directly in this file — see
        # design/DECISIONS.md.
        for keyword in _SUSPICIOUS_HOST_KEYWORDS:
            if keyword in source:
                offenders.append(f"{path.name}: contains suspicious hosted-provider keyword {keyword!r}")
    assert not offenders, offenders
