"""Agent Review — src/ui/pages/agent_review.py through the per-page
AppTest harness, the same isolation convention the other admin-page
tests use.

The two things this file exists to prove are the access boundary and the
counterfactual/actual distinction. Everything else — filters, paging,
the health strip — is detail on top of those.

`is_admin` and the repository factory are patched where agent_review.py
uses them, not where they are defined. No model session, credential,
network call or live agent is involved."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.state_db import agent_repository as sqlite_agent
from src.data_access.state_db import schema
from src.logic import agent_persistence
from src.logic.publication_policy import PolicyDecision, PublicationDecision, RowResult
from src.mcp_agent.contracts import (
    Claim,
    ClaimCategory,
    ClaimProposal,
    ClaimType,
    EvidenceRecord,
    FreshnessStatus,
    SourceTier,
)
from src.models.agent_records import HEARTBEAT_EVENT, AgentAuditRow, AgentJob, AgentRun
from src.ui.pages import agent_review

_HARNESS = Path(__file__).parent / "apptest_pages" / "agent_review_page.py"
NOW = datetime.now(timezone.utc)


# --- fixtures -------------------------------------------------------------------

def _store():
    """AppTest runs the script on its own thread, so this connection is
    opened with check_same_thread=False; it is otherwise identical to
    connection.connect_in_memory()."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    schema.migrate(conn)
    return backend_factory._AgentStoreRepository(conn=conn, module=sqlite_agent)


def _seed_decision(store, *, packet_id="pkt-1", candidate_id="cand-1", issuer_id="edgar:NVDA",
                   policy=PublicationDecision.AUTO_PUBLISHED, kill_switch_on=False,
                   decided_at=None, source="SEC EDGAR", statement="Revenue rose to $30.0 billion.") -> None:
    decided_at = decided_at or NOW.isoformat()
    evidence = EvidenceRecord(
        evidence_id=f"ev-{packet_id}", session_id="sess-1", issuer_id=issuer_id, source_tier=SourceTier.SEC_EDGAR,
        source_name="SEC EDGAR", source_url="https://example.test/a", source_document_id="acc-1",
        source_date="2026-09-01", excerpt_or_locator=statement, excerpt_sha256="sha-1",
        retrieved_at=decided_at, confidence="High", freshness_status=FreshnessStatus.CURRENT,
    )
    claim = Claim(
        claim_id="c-1", issuer_id=issuer_id, claim_type=ClaimType.DIRECT_REPORTED_FACT,
        claim_category=ClaimCategory.DIRECT_FACT, headline="Revenue rose to $30.0 billion",
        statement=statement, what_this_does_not_establish="It does not establish future demand.",
        evidence_ids=(f"ev-{packet_id}",), factual_context_evidence_ids=(),
    )
    proposal = ClaimProposal(session_id="sess-1", candidate_id=candidate_id, claims=(claim,),
                             retrieved_evidence_ids=(f"ev-{packet_id}",))
    policy_decision = PolicyDecision(
        decision=policy, reasons=("all_rows_passed",),
        row_results=tuple(
            RowResult(row=n, condition=f"condition {n}", passed=True) for n in range(12)
        ),
        content_hash=f"hash-{packet_id}",
    )
    packet = agent_persistence.build_packet(
        packet_id=packet_id, session_id="sess-1", candidate_id=candidate_id, source=source,
        seed_document_id="acc-1", proposal=proposal, content_hash=f"hash-{packet_id}",
        created_at=decided_at, issuer_id=issuer_id, evidence=(evidence,),
        issuer_resolution={"resolution_confidence": "exact", "tracked_company_name": "NVIDIA"},
    )
    row = agent_persistence.build_decision(
        packet_id=packet_id, policy=policy_decision, mode="shadow", kill_switch_on=kill_switch_on,
        quote_verified=True, decided_at=decided_at,
    )
    agent_persistence.persist_evaluation(store, packet=packet, decision=row, events=(
        AgentAuditRow(session_id="sess-1", event_type="publication_decision_made", created_at=decided_at,
                      packet_id=packet_id, candidate_id=candidate_id, tool_name="request_publication_decision",
                      outcome=policy.value),
    ))


def _run(store, *, admin=True, enabled=True, query=None, construct=None):
    construct = construct or MagicMock(side_effect=lambda settings: _NoClose(store))
    settings = Settings(agent_review_page_enabled=enabled, research_agent_mode="shadow")
    at = AppTest.from_file(str(_HARNESS), default_timeout=30)
    if query:
        at.query_params.update(query)
    with patch("src.ui.pages.agent_review.is_admin", return_value=admin), \
         patch("src.ui.pages.agent_review.get_settings", return_value=settings), \
         patch.object(backend_factory, "get_agent_store_repository", construct):
        at.run()
    return at, construct


class _NoClose:
    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def close(self):
        return None


def _text(at: AppTest) -> str:
    return (
        " ".join(m.value for m in at.main.get("markdown") if not m.value.startswith("<style>"))
        + " ".join(c.value for c in at.main.get("caption"))
        + " ".join(e.value for e in at.main.get("error"))
        + " ".join(i.value for i in at.main.get("info"))
        + " ".join(w.value for w in at.main.get("warning"))
        + " ".join(str(j.value) for j in at.main.get("json"))
    )


# --- the access boundary ----------------------------------------------------------

def test_a_disabled_feature_flag_shows_nothing_and_builds_no_repository():
    at, construct = _run(_store(), enabled=False)
    assert not at.exception
    assert "not enabled on this deployment" in _text(at)
    construct.assert_not_called()


def test_a_non_admin_is_denied_and_builds_no_repository():
    at, construct = _run(_store(), admin=False)
    assert not at.exception
    assert agent_review.ACCESS_DENIED in _text(at)
    construct.assert_not_called()


def test_a_non_admin_on_a_disabled_deployment_never_reaches_the_admin_check():
    at, construct = _run(_store(), admin=False, enabled=False)
    assert not at.exception
    construct.assert_not_called()


def test_a_denied_visitor_sees_no_decision_data_at_all():
    """Not just 'no repository': no packet id, candidate id or decision
    word may appear, including through a deep link to a detail view."""
    store = _store()
    _seed_decision(store)
    for kwargs in ({"admin": False}, {"enabled": False}):
        at, construct = _run(store, query={"packet": "pkt-1"}, **kwargs)
        text = _text(at)
        assert "pkt-1" not in text and "cand-1" not in text
        assert "AUTO_PUBLISHED" not in text
        construct.assert_not_called()


def test_no_query_runs_in_either_denied_path():
    """The strongest form: a store that raises on any read at all. If the
    page touched it, the page would break — it must not touch it."""
    class Landmine:
        def __getattr__(self, name):
            raise AssertionError(f"denied path queried the store: {name}")

    for kwargs in ({"admin": False}, {"enabled": False}):
        at, _ = _run(_store(), construct=MagicMock(side_effect=lambda s: Landmine()), **kwargs)
        assert not at.exception


# --- the list ------------------------------------------------------------------------

def test_an_admin_sees_the_decision_list():
    store = _store()
    _seed_decision(store)
    at, construct = _run(store)
    assert not at.exception
    text = _text(at)
    assert "cand-1" in text and "1 decision(s)" in text
    construct.assert_called_once()


def test_the_list_separates_what_the_policy_would_have_done_from_what_happened():
    """The whole point of shadow mode. These three labels must appear
    together and carry different values."""
    store = _store()
    _seed_decision(store)
    text = _text(_run(store)[0])
    assert "Policy decision (would have)" in text
    assert "Actual action" in text
    assert "Publication held by" in text
    assert "AUTO_PUBLISHED" in text and "NO_ACTION" in text and "shadow_mode" in text


def test_the_kill_switch_is_named_as_the_hold_when_both_apply():
    store = _store()
    _seed_decision(store, kill_switch_on=True)
    text = _text(_run(store)[0])
    assert "kill_switch" in text and "NO_ACTION" in text


def test_the_empty_state_says_so_rather_than_showing_an_empty_table():
    at, _ = _run(_store())
    assert "No agent decisions match these filters yet." in _text(at)


# --- filters ---------------------------------------------------------------------------

@pytest.mark.parametrize("key,value,expected_visible", [
    ("policy", "AUTO_PUBLISHED", True),
    ("policy", "DUPLICATE", False),
    ("effective", "NO_ACTION", True),
    ("effective", "AUTO_PUBLISHED", False),
    ("issuer", "edgar:NVDA", True),
    ("issuer", "edgar:MU", False),
    ("event", "publication_decision_made", True),
    ("blocked", "1", True),
])
def test_each_filter_narrows_the_list(key, value, expected_visible):
    store = _store()
    _seed_decision(store)
    # A second issuer, so "edgar:MU" is a real option rather than an
    # unknown value the page would ignore.
    _seed_decision(store, packet_id="pkt-2", candidate_id="cand-2", issuer_id="edgar:MU")
    text = _text(_run(store, query={key: value})[0])
    assert ("cand-1" in text) is expected_visible, (key, value, text[:400])


def test_the_date_range_filter_excludes_decisions_outside_it():
    store = _store()
    old = (NOW - timedelta(days=30)).isoformat()
    _seed_decision(store, packet_id="pkt-old", candidate_id="cand-old", decided_at=old)
    _seed_decision(store, packet_id="pkt-new", candidate_id="cand-new")

    recent = (NOW - timedelta(days=1)).strftime("%Y-%m-%d")
    text = _text(_run(store, query={"from": recent})[0])
    assert "cand-new" in text and "cand-old" not in text


def test_the_candidate_status_filter_reads_candidates_read_only():
    """Candidate status lives outside the agent tables, so the filter
    resolves matching ids first and passes them into the agent query —
    which keeps paging correct and the decision list agent-only."""
    store = _store()
    _seed_decision(store)
    with patch("src.ui.pages.agent_review._candidates_by_id", return_value={}) as loader:
        text = _text(_run(store, query={"cstatus": "PUBLISHED"})[0])
    loader.assert_called_once()
    assert "No agent decisions match these filters yet." in text


def test_an_unknown_filter_value_is_ignored_rather_than_breaking_the_page():
    store = _store()
    _seed_decision(store)
    at, _ = _run(store, query={"policy": "NOT_A_DECISION", "page": "banana"})
    assert not at.exception and "cand-1" in _text(at)


# --- pagination and query parameters ------------------------------------------------------

def test_a_long_list_is_paginated():
    store = _store()
    for i in range(agent_review.PAGE_SIZE + 5):
        _seed_decision(store, packet_id=f"pkt-{i:03d}", candidate_id=f"cand-{i:03d}",
                       decided_at=(NOW - timedelta(minutes=i)).isoformat())
    text = _text(_run(store)[0])
    assert f"{agent_review.PAGE_SIZE + 5} decision(s)" in text and "page 1 of 2" in text
    assert "cand-000" in text and "cand-029" not in text


def test_the_second_page_shows_the_rest():
    store = _store()
    for i in range(agent_review.PAGE_SIZE + 5):
        _seed_decision(store, packet_id=f"pkt-{i:03d}", candidate_id=f"cand-{i:03d}",
                       decided_at=(NOW - timedelta(minutes=i)).isoformat())
    text = _text(_run(store, query={"page": "2"})[0])
    assert "page 2 of 2" in text and "cand-029" in text and "cand-000" not in text


def test_filters_survive_in_the_query_parameters():
    store = _store()
    _seed_decision(store)
    at, _ = _run(store, query={"policy": "AUTO_PUBLISHED", "blocked": "1", "issuer": "edgar:NVDA"})
    assert not at.exception
    # AppTest's query_params returns each value as a list.
    def one(key):
        value = at.query_params[key]
        return value[0] if isinstance(value, list) else value

    assert one("policy") == "AUTO_PUBLISHED"
    assert one("blocked") == "1"
    assert one("issuer") == "edgar:NVDA"


def test_a_cleared_filter_is_removed_from_the_query_parameters():
    store = _store()
    _seed_decision(store)
    at, _ = _run(store, query={"policy": "NOT_A_DECISION"})
    assert "policy" not in at.query_params


# --- health -----------------------------------------------------------------------------------

def test_the_health_strip_shows_configured_and_effective_mode_separately():
    store = _store()
    _seed_decision(store)
    text = _text(_run(store)[0])
    assert "Configured" in text and "Effective" in text and "Kill switch" in text


def test_a_configured_publish_mode_is_shown_as_downgraded_not_as_publishing():
    store = _store()
    settings = Settings(agent_review_page_enabled=True, research_agent_mode="publish")
    at = AppTest.from_file(str(_HARNESS), default_timeout=30)
    with patch("src.ui.pages.agent_review.is_admin", return_value=True), \
         patch("src.ui.pages.agent_review.get_settings", return_value=settings), \
         patch.object(backend_factory, "get_agent_store_repository",
                      MagicMock(side_effect=lambda s: _NoClose(store))):
        at.run()
    text = _text(at)
    assert "publishing unavailable in this release" in text
    assert "Configured mode is publish" in text


def test_a_stale_heartbeat_raises_a_banner():
    store = _store()
    stale = (NOW - timedelta(minutes=120)).isoformat()
    store.start_run(AgentRun(run_id="run-a", worker_instance="w1", mode="shadow", started_at=stale,
                             status="running", policy_version="v"))
    store.append_audit_events([AgentAuditRow(session_id="run-a", event_type=HEARTBEAT_EVENT,
                                             created_at=stale, run_id="run-a", outcome="w1")])
    text = _text(_run(store)[0])
    assert "heartbeat is" in text and "stale after" in text


def test_a_completed_run_is_not_reported_as_stale():
    store = _store()
    old = (NOW - timedelta(days=3)).isoformat()
    store.start_run(AgentRun(run_id="run-a", worker_instance="w1", mode="shadow", started_at=old,
                             status="running", policy_version="v"))
    store.complete_run("run-a", status="completed", completed_at=old)
    assert "stale after" not in _text(_run(store)[0])


def test_many_dead_jobs_raise_a_banner():
    store = _store()
    for i in range(6):
        job = AgentJob(job_id=f"job-{i}", candidate_id=f"cand-{i}", source="SEC EDGAR", candidate_version=1,
                       policy_version="v", mode="shadow", state="pending", attempts=3,
                       enqueue_reason="backlog", created_at=NOW.isoformat(), updated_at=NOW.isoformat())
        store.enqueue_job(job)
        store.finish_job(job.job_id, state="dead", now=NOW.isoformat(), last_error_code="issuer_unresolved")
    text = _text(_run(store)[0])
    assert "dead and" in text and "failed job(s) in the last 24h" in text


def test_an_emergency_override_is_surfaced_with_its_reason():
    store = _store()
    store.set_mode_override(mode="off", reason="row 11 false negatives", updated_by="oncall", now=NOW.isoformat())
    text = _text(_run(store)[0])
    assert "emergency override is in force" in text and "row 11 false negatives" in text


# --- detail ---------------------------------------------------------------------------------------

def test_the_detail_view_shows_everything_a_reviewer_needs():
    store = _store()
    _seed_decision(store)
    text = _text(_run(store, query={"packet": "pkt-1"})[0])
    for expected in (
        "Policy decision (would have)", "Actual action", "Publication held by",
        "Revenue rose to $30.0 billion",                 # claim headline and statement
        "It does not establish future demand.",          # what this does not establish
        "Row 0", "Row 11",                               # the full policy matrix
        "all_rows_passed",                               # rationale
        "acc-1",                                         # original filing / seed document
        "sha-1",                                         # stored evidence hash
        "publication_decision_made",                     # audit trail
        "NVIDIA",                                        # issuer resolution
    ):
        assert expected in text, expected


def test_the_detail_view_shows_rows_zero_through_eleven():
    store = _store()
    _seed_decision(store)
    text = _text(_run(store, query={"packet": "pkt-1"})[0])
    for row in range(12):
        assert f"Row {row}" in text, row


def test_a_missing_packet_says_so_rather_than_raising():
    at, _ = _run(_store(), query={"packet": "pkt-does-not-exist"})
    assert not at.exception and "no longer exists" in _text(at)


def test_the_detail_view_carries_no_control_that_changes_anything():
    store = _store()
    _seed_decision(store)
    at, _ = _run(store, query={"packet": "pkt-1"})
    assert at.main.get("button") == []
    assert at.main.get("form_submit_button") == []


def test_the_list_view_carries_no_control_that_changes_anything():
    store = _store()
    _seed_decision(store)
    at, _ = _run(store)
    assert at.main.get("button") == []
    assert at.main.get("form_submit_button") == []


# --- failure handling -------------------------------------------------------------------------------

def test_an_unreachable_store_never_leaks_a_connection_string():
    secret = "sk-live-0123456789"

    def explode(_settings):
        raise RuntimeError(f"could not connect to postgresql://u:{secret}@host/db")

    at, _ = _run(_store(), construct=MagicMock(side_effect=explode))
    text = _text(at)
    assert not at.exception
    assert secret not in text and "RuntimeError" in text
