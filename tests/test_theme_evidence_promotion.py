"""EevaResearch — Citrini-style Theme research workspace vertical slice
(design/DECISIONS.md). Tests for the pure
src.logic.theme_evidence_promotion.build_evidence_from_accepted_match
function, plus the new ThemeMatchingRepositoryProtocol.get_match seam
(JSON/SQLite/skip-soft-Postgres). Every fixture is synthetic and
locally constructed; no real network, worker, scan, or LLM call
anywhere in this file."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.state_db import connection as sqlite_connection
from src.data_access.state_db import schema as sqlite_schema
from src.data_access.state_db import theme_matching_repository as sqlite_matching
from src.logic.theme_evidence_promotion import build_evidence_from_accepted_match
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, StateTransition
from src.models.theme_matching import MatchConfidence, MatchReviewStatus, ResearchCaseThemeMatch, ThemeMatchReviewDecision
from src.models.theme_research import EvidenceDirection

from tests._postgres_test_support import pg_conn, pg_isolated_connection  # noqa: F401

try:
    from src.data_access.postgres_state_db import theme_matching_repository as postgres_matching
except ImportError:  # pragma: no cover - psycopg always installed in this repo
    postgres_matching = None

REPO_ROOT = Path(__file__).parent.parent
_MODULE_PATH = REPO_ROOT / "src" / "logic" / "theme_evidence_promotion.py"


def _candidate(**overrides):
    filing = FilingEvent(
        rcept_no="acc-1", corp_code="0000320193", corp_name="TSMC", stock_code="TSM",
        report_nm="8-K", rcept_dt="2026-08-15", flr_nm="TSMC", source_name="SEC EDGAR",
        source_url="https://example.com/filing", retrieved_at="2026-08-15T01:00:00+00:00",
        original_language="English", theme_slug="ai-buildout",
    )
    defaults = dict(
        id="edgar-cand-1", filing=filing, matched_rules=["material_agreement:1.01"],
        confidence="High", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="TSMC capacity expansion.",
        state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="2026-08-15T00:00:00+00:00")],
    )
    defaults.update(overrides)
    return CandidateSignal(**defaults)


def _match(**overrides):
    defaults = dict(
        id="theme-match-abc", case_id="case-1", theme_id="theme-x", confidence=MatchConfidence.MEDIUM,
        direction=EvidenceDirection.CONTEXT, matched_sector_tag="ai-buildout",
        matched_rule_categories=("material_agreement",), matched_keywords=("capacity",),
        rationale="r", created_at="2026-08-15T00:00:00+00:00",
    )
    defaults.update(overrides)
    return ResearchCaseThemeMatch(**defaults)


def _decision(match_id="theme-match-abc", status=MatchReviewStatus.ACCEPTED, **overrides):
    defaults = dict(
        id="theme-match-review-1", match_id=match_id, decision=status,
        reviewer_note="Confirmed.", reviewed_at="2026-08-16T00:00:00+00:00",
    )
    defaults.update(overrides)
    return ThemeMatchReviewDecision(**defaults)


# ============================================================
# build_evidence_from_accepted_match — happy path
# ============================================================


def test_builds_evidence_for_accepted_decision():
    candidate, match, decision = _candidate(), _match(), _decision()
    evidence = build_evidence_from_accepted_match(
        candidate, match, decision, EvidenceDirection.SUPPORTS,
        "TSMC disclosed a new capacity expansion agreement.",
        "Directly supports the binding-constraint hypothesis for compute capacity.",
    )
    assert evidence is not None
    assert evidence.theme_id == "theme-x"
    assert evidence.direction is EvidenceDirection.SUPPORTS
    assert evidence.company == "TSMC"
    assert evidence.source_url == "https://example.com/filing"
    assert evidence.date == "2026-08-15"


def test_evidence_id_is_deterministic():
    candidate, match, decision = _candidate(), _match(), _decision()
    a = build_evidence_from_accepted_match(candidate, match, decision, EvidenceDirection.SUPPORTS, "f", "r")
    b = build_evidence_from_accepted_match(candidate, match, decision, EvidenceDirection.SUPPORTS, "f", "r")
    assert a.id == b.id


def test_context_direction_still_allowed():
    candidate, match, decision = _candidate(), _match(), _decision()
    evidence = build_evidence_from_accepted_match(candidate, match, decision, EvidenceDirection.CONTEXT, "f", "r")
    assert evidence.direction is EvidenceDirection.CONTEXT


def test_contradicts_and_mixed_directions_allowed():
    candidate, match, decision = _candidate(), _match(), _decision()
    for direction in (EvidenceDirection.CONTRADICTS, EvidenceDirection.MIXED):
        evidence = build_evidence_from_accepted_match(candidate, match, decision, direction, "f", "r")
        assert evidence.direction is direction


# ============================================================
# Rejection paths — never raises, returns None
# ============================================================


def test_pending_decision_rejected():
    candidate, match = _candidate(), _match()
    decision = _decision(status=MatchReviewStatus.PENDING_REVIEW)
    assert build_evidence_from_accepted_match(candidate, match, decision, EvidenceDirection.SUPPORTS, "f", "r") is None


def test_rejected_decision_rejected():
    candidate, match = _candidate(), _match()
    decision = _decision(status=MatchReviewStatus.REJECTED)
    assert build_evidence_from_accepted_match(candidate, match, decision, EvidenceDirection.SUPPORTS, "f", "r") is None


def test_decision_belonging_to_a_different_match_rejected():
    candidate, match = _candidate(), _match()
    decision = _decision(match_id="theme-match-different")
    assert build_evidence_from_accepted_match(candidate, match, decision, EvidenceDirection.SUPPORTS, "f", "r") is None


def test_blank_fact_or_relevance_rejected():
    candidate, match, decision = _candidate(), _match(), _decision()
    assert build_evidence_from_accepted_match(candidate, match, decision, EvidenceDirection.SUPPORTS, "  ", "r") is None
    assert build_evidence_from_accepted_match(candidate, match, decision, EvidenceDirection.SUPPORTS, "f", "  ") is None


def test_malformed_candidate_never_raises():
    match, decision = _match(), _decision()
    assert build_evidence_from_accepted_match(None, match, decision, EvidenceDirection.SUPPORTS, "f", "r") is None
    assert build_evidence_from_accepted_match(object(), match, decision, EvidenceDirection.SUPPORTS, "f", "r") is None


def test_invalid_direction_type_rejected():
    candidate, match, decision = _candidate(), _match(), _decision()
    assert build_evidence_from_accepted_match(candidate, match, decision, "Supports", "f", "r") is None


def test_module_has_no_io_worker_or_llm_dependency():
    """AST-based: no data_access import, no network/LLM library, no
    clock read."""
    source = _MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename="theme_evidence_promotion.py")
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders.extend(a.name for a in node.names if "data_access" in a.name or "requests" in a.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if "data_access" in node.module or "requests" in node.module or "openai" in node.module or "anthropic" in node.module:
                offenders.append(node.module)
    assert not offenders, offenders

    clock_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr in ("now", "today", "utcnow") and isinstance(n.func.value, ast.Name) and n.func.value.id in ("datetime", "date")
    ]
    assert not clock_calls


# ============================================================
# get_match — JSON/SQLite/Postgres parity
# ============================================================


def test_json_get_match_found_and_missing(tmp_path):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    matching_repo = backend_factory.get_theme_matching_repository(settings)
    match = _match()
    matching_repo.insert_match(match)
    assert matching_repo.get_match(match.id) == match
    assert matching_repo.get_match("theme-match-missing") is None


def _sqlite_conn():
    conn = sqlite_connection.connect_in_memory()
    sqlite_schema.migrate(conn)
    return conn


def test_sqlite_get_match_found_and_missing():
    from src.data_access.state_db import research_repository as sqlite_research
    from src.data_access.state_db import theme_repository as sqlite_themes
    from src.models.research_case import ResearchCase, ResearchCaseStatus
    from src.models.theme_research import ResearchTheme, ThemeCategory, ThemeStatus, ThemeVisibility

    conn = _sqlite_conn()
    sqlite_themes.insert_theme(conn, ResearchTheme(
        id="theme-x", category=ThemeCategory.BOTTLENECK, status=ThemeStatus.NEW, visibility=ThemeVisibility.INTERNAL,
        title="T", key_question="q", hypothesis="h", working_thesis="w", why_it_matters="y",
        what_could_change_the_view="c", what_to_watch_next="n",
        created_at="2026-09-01T00:00:00+00:00", updated_at="2026-09-01T00:00:00+00:00",
    ))
    sqlite_research.insert_research_case(conn, ResearchCase(
        id="case-1", trigger_source_type="radar", trigger_source_id="edgar-cand-1", trigger_source_name="TSMC",
        trigger_summary="8-K", title="t", research_question="q", status=ResearchCaseStatus.OPEN,
        created_at="2026-08-15T00:00:00+00:00", version=1,
    ))
    match = _match()
    sqlite_matching.insert_match(conn, match)
    assert sqlite_matching.get_match(conn, match.id) == match
    assert sqlite_matching.get_match(conn, "theme-match-missing") is None


def test_postgres_get_match_found_and_missing(pg_conn):
    from src.data_access.postgres_state_db import research_repository as postgres_research
    from src.data_access.postgres_state_db import theme_repository as postgres_themes
    from src.models.research_case import ResearchCase, ResearchCaseStatus
    from src.models.theme_research import ResearchTheme, ThemeCategory, ThemeStatus, ThemeVisibility

    postgres_themes.insert_theme(pg_conn, ResearchTheme(
        id="pg-theme-gm", category=ThemeCategory.BOTTLENECK, status=ThemeStatus.NEW, visibility=ThemeVisibility.INTERNAL,
        title="T", key_question="q", hypothesis="h", working_thesis="w", why_it_matters="y",
        what_could_change_the_view="c", what_to_watch_next="n",
        created_at="2026-09-01T00:00:00+00:00", updated_at="2026-09-01T00:00:00+00:00",
    ))
    postgres_research.insert_research_case(pg_conn, ResearchCase(
        id="pg-case-gm", trigger_source_type="radar", trigger_source_id="edgar-cand-1", trigger_source_name="TSMC",
        trigger_summary="8-K", title="t", research_question="q", status=ResearchCaseStatus.OPEN,
        created_at="2026-08-15T00:00:00+00:00", version=1,
    ))
    match = _match(id="theme-match-pg", case_id="pg-case-gm", theme_id="pg-theme-gm")
    postgres_matching.insert_match(pg_conn, match)
    assert postgres_matching.get_match(pg_conn, match.id) == match
    assert postgres_matching.get_match(pg_conn, "theme-match-missing") is None
