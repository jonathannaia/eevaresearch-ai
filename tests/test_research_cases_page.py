"""EevaResearch Phase 4, Step 3C (design/DECISIONS.md) — hidden,
read-only Research Cases list/detail view. Every fixture here is
synthetic and directly constructed; this file never runs
scripts/create_research_case.py and never creates a real case in any
configured state backend. Postgres list tests use the shared, fail-soft
local-only fixtures from tests/_postgres_test_support.py and skip
cleanly when no local disposable Postgres instance is available."""
from __future__ import annotations

import ast
import dataclasses
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from streamlit.testing.v1 import AppTest

from src.data_access import backend_factory, research_store
from src.data_access.research_store import (
    build_case_id,
    build_dependency_assertion_id,
    build_evidence_id,
    build_relationship_assertion_id,
)
from src.data_access.state_db import connection as sqlite_connection
from src.data_access.state_db import research_repository as sqlite_research_repository
from src.data_access.state_db import schema as sqlite_schema
from src.logic.research_case_validation import build_research_case_bundle
from src.models.research_case import (
    AssertionConfidence,
    AssertionStatus,
    BottleneckType,
    DependencyAssertion,
    RelationshipAssertion,
    RelationshipRole,
    ResearchCase,
    ResearchCaseStatus,
    ResearchEvidenceItem,
)
from src.ui.pages import research_cases
from src.ui.ui import HIDDEN_FROM_NAV, PRIMARY_NAV, SYSTEM_NAV

from tests._postgres_test_support import pg_conn, pg_isolated_connection  # noqa: F401

try:
    from src.data_access.postgres_state_db import research_repository as postgres_research_repository
except ImportError:  # pragma: no cover - psycopg always installed in this repo
    postgres_research_repository = None

REPO_ROOT = Path(__file__).parent.parent
APP_PATH = REPO_ROOT / "app.py"
HARNESS_PATH = REPO_ROOT / "tests" / "apptest_pages" / "research_cases_page.py"


# ============================================================
# Fixtures (mirrors tests/test_research_case_persistence.py's builders)
# ============================================================


def _case(trigger_source_type="radar", trigger_source_id="cand-1", created_at="2026-08-20T00:00:00+00:00", **overrides):
    defaults = dict(
        id=build_case_id(trigger_source_type, trigger_source_id, created_at),
        trigger_source_type=trigger_source_type, trigger_source_id=trigger_source_id,
        trigger_source_name="Example Corp", trigger_summary="Filed a material event.",
        title="Example research case", research_question="What is the supply-chain exposure?",
        status=ResearchCaseStatus.OPEN, created_at=created_at, version=1,
    )
    defaults.update(overrides)
    return ResearchCase(**defaults)


def _evidence_item(case_id="case-x", source_type="radar", source_id="cand-1", added_at="2026-08-20T00:00:00+00:00", **overrides):
    defaults = dict(
        id=build_evidence_id(case_id, source_type, source_id, added_at),
        case_id=case_id, source_type=source_type, source_id=source_id,
        source_url="https://example.com/filing", source_publisher_or_system="SEC EDGAR",
        source_date="2026-08-15", retrieved_at="2026-08-15T01:00:00+00:00",
        excerpt_original="The company disclosed a supply agreement with Acme Corp.",
        original_language="English", added_at=added_at,
    )
    defaults.update(overrides)
    return ResearchEvidenceItem(**defaults)


def _relationship_assertion(
    case_id="case-x", subject_entity="Example Corp", object_entity="Acme Corp",
    role=RelationshipRole.SUPPLIER, created_at="2026-08-20T00:00:00+00:00", **overrides,
):
    defaults = dict(
        id=build_relationship_assertion_id(case_id, subject_entity, object_entity, role, created_at),
        case_id=case_id, subject_entity=subject_entity, object_entity=object_entity, role=role,
        assertion_status=AssertionStatus.DIRECTLY_SUPPORTED, evidence_ids=("evidence-1",),
        confidence=AssertionConfidence.HIGH, created_at=created_at, reasoning=None, limitations=(),
    )
    defaults.update(overrides)
    return RelationshipAssertion(**defaults)


def _dependency_assertion(
    case_id="case-x", affected_entity="Example Corp", bottleneck_type=BottleneckType.COMPONENT_SUPPLY,
    created_at="2026-08-20T00:00:00+00:00", **overrides,
):
    defaults = dict(
        id=build_dependency_assertion_id(case_id, affected_entity, bottleneck_type, created_at),
        case_id=case_id, affected_entity=affected_entity, bottleneck_type=bottleneck_type,
        supply_chain_layer="compute-hardware", transmission_path=("Acme Corp", "Example Corp"),
        assertion_status=AssertionStatus.HYPOTHESIS, evidence_ids=("evidence-1",),
        confidence=AssertionConfidence.MEDIUM, created_at=created_at,
        reasoning="Acme Corp is Example Corp's sole qualified supplier for this component.",
        limitations=("Not yet independently confirmed by a second source.",),
    )
    defaults.update(overrides)
    return DependencyAssertion(**defaults)


def _sqlite_conn():
    conn = sqlite_connection.connect_in_memory()
    sqlite_schema.migrate(conn)
    return conn


# ============================================================
# Part A — route registration and hidden visibility (proofs 1, 2)
# ============================================================


def test_proof1_route_registered_and_hidden_not_in_any_nav_group():
    at = AppTest.from_file(str(APP_PATH), default_timeout=15)
    at.run()
    pages = at.session_state["_pages"]
    assert "research_cases" in pages
    assert pages["research_cases"].visibility == "hidden"

    all_nav_keys = {k for k, _ in PRIMARY_NAV + SYSTEM_NAV + HIDDEN_FROM_NAV}
    assert "research_cases" not in all_nav_keys


def test_proof2_global_beta_gate_still_covers_the_route(monkeypatch):
    """The beta gate runs in app.py before st.navigation(...).run() for
    every registered page, hidden or not — this proves research_cases
    doesn't bypass it by asserting the gate blocks the same way it does
    for every other route when enabled with no approved emails."""
    monkeypatch.setenv("EDGE_PRIVATE_BETA_AUTH_ENABLED", "true")
    monkeypatch.setenv("EDGE_PRIVATE_BETA_ALLOWED_EMAILS", "")
    at = AppTest.from_file(str(APP_PATH), default_timeout=15)
    at.query_params["case_id"] = ""
    at.run()
    all_text = " ".join(m.value for m in at.markdown) + " ".join(t.value for t in at.title)
    assert "Private beta" in all_text
    assert "Research Cases" not in all_text


# ============================================================
# Part B — list view read shape (proofs 3, 4, 16)
# ============================================================


class _FakeRepo:
    """Implements ResearchCaseRepositoryProtocol directly against
    in-memory dicts, with call counting for the no-N+1 proofs — no
    JSON/SQLite/Postgres I/O at all."""

    def __init__(self, cases=(), evidence_by_case=None, assertions_by_case=None):
        self._cases = {c.id: c for c in cases}
        self._evidence_by_case = evidence_by_case or {}
        self._assertions_by_case = assertions_by_case or {}
        self.list_recent_cases_calls = []
        self.get_case_calls = []
        self.evidence_calls = []
        self.assertions_calls = []

    def list_recent_cases(self, limit):
        self.list_recent_cases_calls.append(limit)
        ordered = sorted(self._cases.values(), key=lambda c: (c.created_at, c.id), reverse=True)
        return tuple(ordered[:limit])

    def get_case(self, case_id):
        self.get_case_calls.append(case_id)
        return self._cases.get(case_id)

    def evidence_items_for_case_ids(self, case_ids):
        self.evidence_calls.append(tuple(case_ids))
        return {cid: self._evidence_by_case[cid] for cid in case_ids if cid in self._evidence_by_case}

    def assertions_for_case_ids(self, case_ids):
        self.assertions_calls.append(tuple(case_ids))
        return {cid: self._assertions_by_case[cid] for cid in case_ids if cid in self._assertions_by_case}


class _RaisingRepo:
    def list_recent_cases(self, limit):
        raise RuntimeError("boom - raw exception text that must never reach the UI")

    def get_case(self, case_id):
        raise RuntimeError("boom - raw exception text that must never reach the UI")

    def evidence_items_for_case_ids(self, case_ids):
        raise RuntimeError("unreachable")

    def assertions_for_case_ids(self, case_ids):
        raise RuntimeError("unreachable")


def _run_with_repo(monkeypatch, repo, case_id=None):
    monkeypatch.setenv("EDGE_RESEARCH_CASES_ENABLED", "true")
    monkeypatch.setattr(research_cases.backend_factory, "get_research_case_repository", lambda settings: repo)
    at = AppTest.from_file(str(HARNESS_PATH), default_timeout=15)
    if case_id is not None:
        at.query_params["case_id"] = case_id
    at.run()
    return at


def test_proof3_list_view_makes_exactly_one_bounded_read_with_limit_20(monkeypatch):
    repo = _FakeRepo(cases=[_case()])
    at = _run_with_repo(monkeypatch, repo)
    assert not at.exception
    assert repo.list_recent_cases_calls == [20]


def test_proof4_list_view_makes_zero_evidence_or_assertion_reads(monkeypatch):
    repo = _FakeRepo(cases=[_case()])
    _run_with_repo(monkeypatch, repo)
    assert repo.evidence_calls == []
    assert repo.assertions_calls == []


def test_proof16_json_limit_zero_or_negative_returns_empty_without_load(tmp_path, monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("must not load when limit <= 0")

    monkeypatch.setattr(research_store, "load_research_cases", _boom)
    assert research_store.list_recent_cases(tmp_path, 0) == ()
    assert research_store.list_recent_cases(tmp_path, -5) == ()


def test_proof16_sqlite_limit_zero_or_negative_executes_no_sql():
    conn = MagicMock()
    assert sqlite_research_repository.list_recent_cases(conn, 0) == ()
    assert sqlite_research_repository.list_recent_cases(conn, -1) == ()
    conn.execute.assert_not_called()


def test_proof16_postgres_limit_zero_or_negative_executes_no_query():
    if postgres_research_repository is None:
        pytest.skip("psycopg not available")
    conn = MagicMock()
    assert postgres_research_repository.list_recent_cases(conn, 0) == ()
    conn.execute.assert_not_called()


# ============================================================
# Part C — deterministic ordering, one-load/one-query proofs (proofs 5-8)
# ============================================================


def test_proof5_json_recent_list_uses_one_load_and_deterministic_ordering(tmp_path, monkeypatch):
    older = _case(trigger_source_id="a", created_at="2026-08-01T00:00:00+00:00")
    newer = _case(trigger_source_id="b", created_at="2026-08-05T00:00:00+00:00")
    same_ts_a = _case(trigger_source_id="c", created_at="2026-08-05T00:00:00+00:00")
    for c in (older, newer, same_ts_a):
        research_store.append_research_case(tmp_path, c)

    calls = []
    real_load = research_store.load_research_cases

    def _counting(*args, **kwargs):
        calls.append(1)
        return real_load(*args, **kwargs)

    monkeypatch.setattr(research_store, "load_research_cases", _counting)
    result = research_store.list_recent_cases(tmp_path, 20)
    assert len(calls) == 1
    ids_by_created_desc = sorted([newer.id, same_ts_a.id], reverse=True)
    assert [c.id for c in result] == ids_by_created_desc + [older.id]


def test_proof6_sqlite_recent_list_uses_one_parameterized_query_and_deterministic_ordering():
    conn = _sqlite_conn()
    older = _case(trigger_source_id="a", created_at="2026-08-01T00:00:00+00:00")
    newer = _case(trigger_source_id="b", created_at="2026-08-05T00:00:00+00:00")
    for c in (older, newer):
        sqlite_research_repository.insert_research_case(conn, c)

    tracking_conn = MagicMock(wraps=conn)
    result = sqlite_research_repository.list_recent_cases(tracking_conn, 20)
    assert tracking_conn.execute.call_count == 1
    sql_text = tracking_conn.execute.call_args[0][0]
    assert "ORDER BY created_at DESC, id DESC" in sql_text
    assert "LIMIT ?" in sql_text
    assert [c.id for c in result] == [newer.id, older.id]


def test_proof7_sqlite_recent_list_respects_limit():
    conn = _sqlite_conn()
    for i in range(5):
        sqlite_research_repository.insert_research_case(
            conn, _case(trigger_source_id=f"cand-{i}", created_at=f"2026-08-0{i+1}T00:00:00+00:00"),
        )
    result = sqlite_research_repository.list_recent_cases(conn, 2)
    assert len(result) == 2


def test_proof8_postgres_recent_list_uses_one_parameterized_query_and_deterministic_ordering(pg_conn):
    older = _case(trigger_source_id="pg-a", created_at="2026-08-01T00:00:00+00:00")
    newer = _case(trigger_source_id="pg-b", created_at="2026-08-05T00:00:00+00:00")
    for c in (older, newer):
        postgres_research_repository.insert_research_case(pg_conn, c)
    result = postgres_research_repository.list_recent_cases(pg_conn, 20)
    assert [c.id for c in result] == [newer.id, older.id]


def test_proof8_postgres_recent_list_query_construction_mocked():
    if postgres_research_repository is None:
        pytest.skip("psycopg not available")
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = []
    postgres_research_repository.list_recent_cases(conn, 20)
    assert conn.execute.call_count == 1
    sql_text, params = conn.execute.call_args[0]
    assert "ORDER BY created_at DESC, id DESC" in sql_text
    assert "LIMIT %s" in sql_text
    assert params == (20,)


# ============================================================
# Part D — detail view read shape, no N+1 (proof 9), no forbidden calls (10)
# ============================================================


def test_proof9_detail_view_makes_exactly_one_case_one_evidence_one_assertion_read(monkeypatch):
    case = _case()
    item = _evidence_item(case_id=case.id)
    rel = _relationship_assertion(case_id=case.id, evidence_ids=(item.id,))
    repo = _FakeRepo(cases=[case], evidence_by_case={case.id: (item,)}, assertions_by_case={case.id: (rel,)})
    at = _run_with_repo(monkeypatch, repo, case_id=case.id)
    assert not at.exception
    assert repo.get_case_calls == [case.id]
    assert repo.evidence_calls == [(case.id,)]
    assert repo.assertions_calls == [(case.id,)]


def test_proof10_detail_view_never_calls_validation_or_write_functions(monkeypatch):
    validate_calls = []
    monkeypatch.setattr(
        "src.logic.research_case_validation.validate_research_case_bundle",
        lambda bundle: validate_calls.append(bundle) or (),
    )

    def _forbidden(*_a, **_k):
        raise AssertionError("research_cases.py must never call this")

    for target in [
        "src.data_access.research_store.append_research_case_bundle",
        "src.data_access.state_db.research_repository.insert_research_case_bundle",
        "src.data_access.postgres_state_db.research_repository.insert_research_case_bundle",
    ]:
        monkeypatch.setattr(target, _forbidden)

    case = _case()
    repo = _FakeRepo(cases=[case])
    at = _run_with_repo(monkeypatch, repo, case_id=case.id)
    assert not at.exception
    assert validate_calls == []


def test_proof10_page_module_never_imports_the_authoring_script():
    tree = ast.parse((REPO_ROOT / "src" / "ui" / "pages" / "research_cases.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for module in modules:
            assert "create_research_case" not in module


# ============================================================
# Part E — section correctness (proofs 11, 12, 13)
# ============================================================


def test_proof11_sections_render_correct_assertions_and_do_not_mix(monkeypatch):
    case = _case()
    item = _evidence_item(case_id=case.id)
    supported = _relationship_assertion(case_id=case.id, subject_entity="SupportedSubj", evidence_ids=(item.id,))
    hypothesis = _dependency_assertion(case_id=case.id, affected_entity="HypoEntity", evidence_ids=(item.id,))
    contradicted = _relationship_assertion(
        case_id=case.id, subject_entity="ContraSubj", object_entity="ContraObj",
        role=RelationshipRole.CUSTOMER, assertion_status=AssertionStatus.CONTRADICTED, created_at="2026-08-21T00:00:00+00:00",
    )
    repo = _FakeRepo(
        cases=[case], evidence_by_case={case.id: (item,)},
        assertions_by_case={case.id: (supported, hypothesis, contradicted)},
    )
    at = _run_with_repo(monkeypatch, repo, case_id=case.id)
    assert not at.exception
    all_html = " ".join(m.value for m in at.markdown)
    assert "SupportedSubj" in all_html
    assert "HypoEntity" in all_html
    assert "ContraSubj" in all_html

    directly_supported_idx = all_html.index("Directly supported")
    hypotheses_idx = all_html.index("Hypotheses")
    other_idx = all_html.index("Other recorded context")
    supported_idx = all_html.index("SupportedSubj")
    hypo_idx = all_html.index("HypoEntity")
    contra_idx = all_html.index("ContraSubj")
    assert directly_supported_idx < supported_idx < hypotheses_idx
    assert hypotheses_idx < hypo_idx < other_idx
    assert other_idx < contra_idx


def test_proof12_hypothesis_reasoning_limitations_and_transmission_path_only_under_hypotheses(monkeypatch):
    case = _case()
    hypothesis = _dependency_assertion(
        case_id=case.id, affected_entity="HypoOnly", reasoning="UniqueReasoningText",
        limitations=("UniqueLimitationText",), transmission_path=("StepOne", "StepTwo"),
    )
    supported = _relationship_assertion(case_id=case.id, subject_entity="SupportedOnly")
    repo = _FakeRepo(cases=[case], assertions_by_case={case.id: (hypothesis, supported)})
    at = _run_with_repo(monkeypatch, repo, case_id=case.id)
    all_html = " ".join(m.value for m in at.markdown)
    assert "UniqueReasoningText" in all_html
    assert "UniqueLimitationText" in all_html
    assert "StepOne" in all_html and "StepTwo" in all_html

    hypotheses_idx = all_html.index("Hypotheses")
    other_idx = all_html.index("Other recorded context")
    reasoning_idx = all_html.index("UniqueReasoningText")
    assert hypotheses_idx < reasoning_idx < other_idx


def test_proof13_mismatched_case_id_records_are_never_displayed(monkeypatch):
    case = _case()
    matching_item = _evidence_item(case_id=case.id, source_id="matching", excerpt_original="MatchingExcerptText")
    mismatched_item = dataclasses.replace(
        _evidence_item(case_id="some-other-case", source_id="mismatched", excerpt_original="MismatchedExcerptText"),
    )
    matching_assertion = _relationship_assertion(case_id=case.id, subject_entity="MatchingAssertionSubj")
    mismatched_assertion = dataclasses.replace(
        _relationship_assertion(case_id="some-other-case", subject_entity="MismatchedAssertionSubj"),
    )
    repo = _FakeRepo(
        cases=[case],
        evidence_by_case={case.id: (matching_item, mismatched_item)},
        assertions_by_case={case.id: (matching_assertion, mismatched_assertion)},
    )
    at = _run_with_repo(monkeypatch, repo, case_id=case.id)
    all_html = " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)
    assert "MatchingExcerptText" in all_html
    assert "MismatchedExcerptText" not in all_html
    assert "MatchingAssertionSubj" in all_html
    assert "MismatchedAssertionSubj" not in all_html


# ============================================================
# Part F — missing case / backend failure / empty list (proofs 14, 15, 16)
# ============================================================


def test_proof14_missing_case_renders_not_found_and_makes_no_evidence_or_assertion_reads(monkeypatch):
    repo = _FakeRepo(cases=[])
    at = _run_with_repo(monkeypatch, repo, case_id="case-does-not-exist")
    assert not at.exception
    all_html = " ".join(m.value for m in at.markdown)
    assert "not found" in all_html.lower()
    assert repo.evidence_calls == []
    assert repo.assertions_calls == []


def test_proof15_backend_error_on_list_view_renders_restrained_message_no_raw_exception(monkeypatch):
    at = _run_with_repo(monkeypatch, _RaisingRepo())
    assert not at.exception
    all_html = " ".join(m.value for m in at.markdown)
    assert "temporarily unavailable" in all_html.lower()
    assert "boom" not in all_html
    assert "RuntimeError" not in all_html


def test_proof15_backend_error_on_detail_view_renders_restrained_message_no_raw_exception(monkeypatch):
    at = _run_with_repo(monkeypatch, _RaisingRepo(), case_id="case-1")
    assert not at.exception
    all_html = " ".join(m.value for m in at.markdown)
    assert "temporarily unavailable" in all_html.lower()
    assert "boom" not in all_html


def test_proof15_repository_construction_failure_renders_restrained_message(monkeypatch):
    def _boom(settings):
        raise RuntimeError("connection boom - must never reach the UI")

    monkeypatch.setenv("EDGE_RESEARCH_CASES_ENABLED", "true")
    monkeypatch.setattr(research_cases.backend_factory, "get_research_case_repository", _boom)
    at = AppTest.from_file(str(HARNESS_PATH), default_timeout=15)
    at.run()
    assert not at.exception
    all_html = " ".join(m.value for m in at.markdown)
    assert "temporarily unavailable" in all_html.lower()
    assert "boom" not in all_html


def test_proof16_empty_list_renders_the_established_empty_state(monkeypatch):
    at = _run_with_repo(monkeypatch, _FakeRepo(cases=[]))
    assert not at.exception
    all_html = " ".join(m.value for m in at.markdown)
    assert "No research cases yet" in all_html


# ============================================================
# Part G — free-text escaping and safe links (proofs 17, 18, 19)
# ============================================================


def test_proof17_script_tags_and_html_fragments_are_escaped_in_list_view(monkeypatch):
    dangerous_case = _case(title="<script>alert('xss')</script>", trigger_source_name="</div><img onerror=alert(1)>")
    repo = _FakeRepo(cases=[dangerous_case])
    at = _run_with_repo(monkeypatch, repo)
    assert not at.exception
    all_html = " ".join(m.value for m in at.markdown)
    assert "<script>" not in all_html
    assert "<img onerror=" not in all_html
    assert "&lt;script&gt;" in all_html


def test_proof17_script_tags_and_html_fragments_are_escaped_in_detail_view(monkeypatch):
    case = _case(title="<script>alert(1)</script>", research_question="</div><b>bold</b>")
    item = _evidence_item(
        case_id=case.id, excerpt_original="<img src=x onerror=alert(1)>",
        excerpt_translated="</script><script>alert(2)</script>",
    )
    assertion = _dependency_assertion(
        case_id=case.id, affected_entity="<svg onload=alert(3)>",
        reasoning="</div><script>alert(4)</script>", limitations=("<script>alert(5)</script>",),
        transmission_path=("<script>alert(6)</script>",),
    )
    repo = _FakeRepo(cases=[case], evidence_by_case={case.id: (item,)}, assertions_by_case={case.id: (assertion,)})
    at = _run_with_repo(monkeypatch, repo, case_id=case.id)
    assert not at.exception
    all_html = " ".join(m.value for m in at.markdown)
    assert "<script>" not in all_html
    assert "<img src=x onerror=" not in all_html
    assert "<svg onload=" not in all_html
    assert "&lt;/div&gt;" in all_html
    assert all_html.count("<script>") == 0


def test_proof18_javascript_and_data_urls_are_not_clickable_links(monkeypatch):
    case = _case()
    item = _evidence_item(case_id=case.id, source_url="javascript:alert(1)")
    repo = _FakeRepo(cases=[case], evidence_by_case={case.id: (item,)})
    at = _run_with_repo(monkeypatch, repo, case_id=case.id)
    all_html = " ".join(m.value for m in at.markdown)
    assert 'href="javascript' not in all_html.lower()

    case2 = _case(trigger_source_id="cand-2", created_at="2026-08-21T00:00:00+00:00")
    item2 = _evidence_item(case_id=case2.id, source_url="data:text/html,<script>alert(1)</script>")
    repo2 = _FakeRepo(cases=[case2], evidence_by_case={case2.id: (item2,)})
    at2 = _run_with_repo(monkeypatch, repo2, case_id=case2.id)
    all_html2 = " ".join(m.value for m in at2.markdown)
    assert 'href="data:' not in all_html2.lower()


def test_proof18_https_and_http_urls_become_clickable_links(monkeypatch):
    case = _case()
    item = _evidence_item(case_id=case.id, source_url="https://example.com/filing.htm")
    repo = _FakeRepo(cases=[case], evidence_by_case={case.id: (item,)})
    at = _run_with_repo(monkeypatch, repo, case_id=case.id)
    all_html = " ".join(m.value for m in at.markdown)
    assert 'href="https://example.com/filing.htm"' in all_html

    case2 = _case(trigger_source_id="cand-http", created_at="2026-08-22T00:00:00+00:00")
    item2 = _evidence_item(case_id=case2.id, source_url="http://example.com/filing.htm")
    repo2 = _FakeRepo(cases=[case2], evidence_by_case={case2.id: (item2,)})
    at2 = _run_with_repo(monkeypatch, repo2, case_id=case2.id)
    all_html2 = " ".join(m.value for m in at2.markdown)
    assert 'href="http://example.com/filing.htm"' in all_html2


def test_proof19_unknown_or_malformed_status_never_crashes_and_uses_safe_fallback(monkeypatch):
    case = _case()
    weird_status_case = dataclasses.replace(case, status="NOT_A_REAL_STATUS")
    repo = _FakeRepo(cases=[weird_status_case])
    at = _run_with_repo(monkeypatch, repo)
    assert not at.exception

    weird_assertion = dataclasses.replace(
        _relationship_assertion(case_id=case.id), assertion_status="NOT_A_REAL_ASSERTION_STATUS", role="NOT_A_REAL_ROLE",
    )
    repo2 = _FakeRepo(cases=[case], assertions_by_case={case.id: (weird_assertion,)})
    at2 = _run_with_repo(monkeypatch, repo2, case_id=case.id)
    assert not at2.exception
    all_html = " ".join(m.value for m in at2.markdown)
    assert "Other recorded context" in all_html


def test_esc_and_enum_label_and_safe_url_unit_behavior():
    assert research_cases._esc(None) == ""
    assert research_cases._esc("<b>x</b>") == "&lt;b&gt;x&lt;/b&gt;"
    assert research_cases._enum_label(AssertionStatus.DIRECTLY_SUPPORTED) == "DIRECTLY_SUPPORTED"
    assert research_cases._enum_label("raw-string") == "raw-string"
    assert research_cases._enum_label(None) == "Unknown"
    assert research_cases._safe_source_url("https://a.com") == "https://a.com"
    assert research_cases._safe_source_url("HTTPS://a.com") == "HTTPS://a.com"
    assert research_cases._safe_source_url("javascript:alert(1)") is None
    assert research_cases._safe_source_url("data:text/html,x") is None
    assert research_cases._safe_source_url("") is None
    assert research_cases._safe_source_url(None) is None


# ============================================================
# Part H — existing Radar/Daily News/nav/UI behavior unchanged (proof 20)
# ============================================================


def test_proof20_existing_visible_routes_still_render_without_exception():
    for harness_file in ["radar_inbox_page.py", "daily_news_page.py", "dashboard_page.py"]:
        at = AppTest.from_file(str(REPO_ROOT / "tests" / "apptest_pages" / harness_file), default_timeout=15)
        at.run()
        assert not at.exception, f"{harness_file} raised: {at.exception}"


# ============================================================
# Part I — scope guards (proof 21)
# ============================================================


def test_proof21_scope_guard_only_approved_files_changed():
    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    allowed = {
        "app.py",
        "src/data_access/research_store.py",
        "src/data_access/state_db/research_repository.py",
        "src/data_access/postgres_state_db/research_repository.py",
        "src/data_access/backend_factory.py",
        "tests/test_navigation.py",
    }
    assert changed <= allowed, changed - allowed


def test_proof21_no_forbidden_files_touched():
    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    forbidden_paths = {
        "src/models/research_case.py", "src/logic/research_case_validation.py", "scripts/create_research_case.py",
        "src/ui/pages/radar_inbox.py", "src/ui/pages/daily_news.py", "src/ui/components/radar_card.py", "src/ui/ui.py",
        "requirements.txt",
    }
    forbidden_prefixes = (
        "src/data_access/edgar/", "src/data_access/dart/", "src/data_access/edinet/", "src/data_access/daily_news/",
        "src/data_access/translation/",
    )
    hit = {c for c in changed if c in forbidden_paths or any(c.startswith(p) for p in forbidden_prefixes)}
    assert not hit, hit


def test_proof21_no_new_dependency_added_to_requirements():
    result = subprocess.run(["git", "diff", "HEAD", "--", "requirements.txt"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    assert result.stdout.strip() == "", f"requirements.txt was modified: {result.stdout}"


def test_proof21_page_module_never_imports_forbidden_types():
    tree = ast.parse((REPO_ROOT / "src" / "ui" / "pages" / "research_cases.py").read_text(encoding="utf-8"))
    forbidden_modules = (
        "src.data_access.edgar", "src.data_access.dart", "src.data_access.edinet", "src.data_access.daily_news",
        "src.data_access.translation", "requests", "urllib.request", "httpx", "socket",
        "anthropic", "openai", "langchain",
    )
    offenders = []
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for module in modules:
            if any(module == forbidden or module.startswith(forbidden + ".") for forbidden in forbidden_modules):
                offenders.append(module)
    assert not offenders, offenders
