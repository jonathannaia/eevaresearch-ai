"""The durable agent store (Postgres V24 / SQLite V22) behind
backend_factory.get_agent_store_repository().

Every test runs against both backends. Postgres uses the disposable,
loopback-only container fixture and skips (never fails) when it is absent.
No model session, credential or network call is involved anywhere."""
from __future__ import annotations

import pytest

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.backend_factory import BackendConfigurationError
from src.data_access.postgres_state_db import agent_repository as pg_agent
from src.data_access.postgres_state_db import schema as postgres_schema
from src.data_access.state_db import agent_repository as sqlite_agent
from src.data_access.state_db import connection, schema
from src.models.agent_records import (
    AgentAuditRow,
    AgentDecisionRow,
    AgentEvidenceRow,
    AgentJob,
    AgentPacket,
    AgentRun,
    more_restrictive,
    resolve_blocked_by,
)
from tests._postgres_test_support import pg_isolated_connection  # noqa: F401 (fixture import)


@pytest.fixture(params=["sqlite", "postgres"])
def store(request):
    if request.param == "sqlite":
        conn = connection.connect_in_memory()
        schema.migrate(conn)
        yield backend_factory._AgentStoreRepository(conn=conn, module=sqlite_agent)
    else:
        conn = request.getfixturevalue("pg_isolated_connection")
        postgres_schema.migrate(conn)
        yield backend_factory._AgentStoreRepository(conn=conn, module=pg_agent)


def _job(**overrides) -> AgentJob:
    fields = dict(
        job_id="job-1", candidate_id="cand-1", source="SEC EDGAR", candidate_version=1, policy_version="v1",
        mode="shadow", created_at="2026-09-20T00:00:00+00:00", updated_at="2026-09-20T00:00:00+00:00",
        enqueue_reason="new",
    )
    fields.update(overrides)
    return AgentJob(**fields)


def _packet(**overrides) -> AgentPacket:
    fields = dict(
        packet_id="pkt-1", session_id="sess-1", candidate_id="cand-1", source="SEC EDGAR",
        seed_document_id="acc-1", proposal_json='{"claims": []}', content_hash="hash-1",
        created_at="2026-09-20T00:01:00+00:00", job_id="job-1", run_id="run-1", issuer_id="NVDA",
    )
    fields.update(overrides)
    fields.setdefault("evidence", (
        AgentEvidenceRow(packet_id=fields["packet_id"], evidence_id="ev-1", source_tier="ISSUER_IR",
                         source_name="NVIDIA IR", source_url="https://example.test/a",
                         source_document_id="doc-1", source_date="2026-09-01",
                         excerpt_or_locator="the quoted sentence", excerpt_sha256="sha-1"),
    ))
    return AgentPacket(**fields)


def _decision(**overrides) -> AgentDecisionRow:
    fields = dict(
        packet_id="pkt-1", policy_decision="AUTO_PUBLISHED", effective_decision="NO_ACTION", mode="shadow",
        kill_switch_on=False, policy_version="v1", decided_at="2026-09-20T00:02:00+00:00",
        blocked_by="shadow_mode", quote_verified=True, reasons_json='["row 10 passed"]',
        row_results_json='[[0, "kill switch", true, ""]]',
    )
    fields.update(overrides)
    return AgentDecisionRow(**fields)


# --- factory selection -------------------------------------------------------

def test_json_backend_is_refused_outright(tmp_path):
    with pytest.raises(BackendConfigurationError) as excinfo:
        backend_factory.get_agent_store_repository(Settings(db_backend="json", cache_dir=tmp_path))
    assert "JSON agent persistence is refused" in str(excinfo.value)


def test_sqlite_backend_builds_a_working_store(tmp_path):
    store = backend_factory.get_agent_store_repository(
        Settings(db_backend="sqlite", state_db_path=tmp_path / "state.db", cache_dir=tmp_path)
    )
    try:
        assert store.latest_run() is None
        assert store.count_jobs_by_state() == {}
    finally:
        store.close()


# --- runs and heartbeat --------------------------------------------------------

def test_run_round_trips_and_latest_run_is_the_heartbeat(store):
    store.start_run(AgentRun(run_id="run-1", worker_instance="w1", mode="shadow",
                             started_at="2026-09-20T00:00:00+00:00", policy_version="v1"))
    assert store.latest_run().status == "running"
    store.complete_run("run-1", status="completed", completed_at="2026-09-20T00:05:00+00:00",
                       considered=7, sessions=2, decisions=2, errors=0, cost_usd="0.42")
    latest = store.latest_run()
    assert (latest.status, latest.completed_at, latest.considered, latest.sessions) == (
        "completed", "2026-09-20T00:05:00+00:00", 7, 2)
    assert latest.cost_usd == "0.42"


# --- jobs: idempotency, leases, retry ---------------------------------------------

def test_enqueue_is_idempotent_on_candidate_version_policy_and_mode(store):
    assert store.enqueue_job(_job()) is True
    assert store.enqueue_job(_job(job_id="job-2")) is False  # same key
    assert store.enqueue_job(_job(job_id="job-3", candidate_version=2)) is True
    assert store.enqueue_job(_job(job_id="job-4", policy_version="v2")) is True
    assert store.enqueue_job(_job(job_id="job-5", mode="publish")) is True
    assert store.count_jobs_by_state() == {"pending": 4}


def test_claim_leases_one_job_oldest_first_and_counts_the_attempt(store):
    store.enqueue_job(_job(job_id="old", candidate_id="c-old", created_at="2026-09-19T00:00:00+00:00",
                           updated_at="2026-09-19T00:00:00+00:00", enqueue_reason="backlog"))
    store.enqueue_job(_job(job_id="new", candidate_id="c-new", created_at="2026-09-20T00:00:00+00:00",
                           updated_at="2026-09-20T00:00:00+00:00"))
    claimed = store.claim_job(mode="shadow", now="2026-09-20T01:00:00+00:00",
                              lease_expires_at="2026-09-20T01:10:00+00:00", worker_instance="w1")
    assert (claimed.job_id, claimed.state, claimed.attempts) == ("old", "leased", 1)
    assert claimed.worker_instance == "w1"
    assert store.count_jobs_by_state() == {"leased": 1, "pending": 1}


def test_claim_never_returns_a_job_from_another_mode(store):
    store.enqueue_job(_job(mode="publish"))
    assert store.claim_job(mode="shadow", now="t1", lease_expires_at="t2", worker_instance="w1") is None


def test_claim_respects_the_backoff_window(store):
    store.enqueue_job(_job())
    store.finish_job("job-1", state="pending", now="2026-09-20T00:10:00+00:00",
                     last_error_code="session_timeout", next_attempt_at="2026-09-20T01:00:00+00:00")
    assert store.claim_job(mode="shadow", now="2026-09-20T00:30:00+00:00",
                           lease_expires_at="x", worker_instance="w1") is None
    assert store.claim_job(mode="shadow", now="2026-09-20T01:00:00+00:00",
                           lease_expires_at="x", worker_instance="w1") is not None


def test_an_expired_lease_is_reclaimed_and_a_live_one_is_not(store):
    store.enqueue_job(_job())
    store.claim_job(mode="shadow", now="2026-09-20T00:00:00+00:00",
                    lease_expires_at="2026-09-20T00:10:00+00:00", worker_instance="w1")
    assert store.reclaim_expired_leases(now="2026-09-20T00:05:00+00:00") == 0
    assert store.reclaim_expired_leases(now="2026-09-20T00:11:00+00:00") == 1
    assert store.get_job("job-1").state == "pending"
    assert store.get_job("job-1").worker_instance is None


def test_terminal_states_and_error_codes_round_trip(store):
    store.enqueue_job(_job())
    store.finish_job("job-1", state="dead", now="t9", last_error_code="no_structured_output")
    job = store.get_job("job-1")
    assert (job.state, job.last_error_code, job.lease_expires_at) == ("dead", "no_structured_output", None)


# --- packets, evidence, decisions ----------------------------------------------------

def test_packet_evidence_and_decision_round_trip(store):
    store.save_packet(_packet())
    store.record_decision(_decision())
    packet = store.get_packet("pkt-1")
    assert (packet.candidate_id, packet.issuer_id, packet.content_hash) == ("cand-1", "NVDA", "hash-1")
    assert len(packet.evidence) == 1
    assert packet.evidence[0].excerpt_or_locator == "the quoted sentence"
    decision = store.get_decision("pkt-1")
    assert (decision.policy_decision, decision.effective_decision) == ("AUTO_PUBLISHED", "NO_ACTION")
    assert (decision.blocked_by, decision.mode, decision.kill_switch_on) == ("shadow_mode", "shadow", False)
    assert decision.quote_verified is True
    assert (decision.candidate_status_written, decision.published) == (False, False)


def test_a_kill_switched_shadow_decision_keeps_both_facts(store):
    store.save_packet(_packet())
    store.record_decision(_decision(blocked_by="kill_switch", kill_switch_on=True))
    decision = store.get_decision("pkt-1")
    assert decision.blocked_by == "kill_switch"
    assert decision.mode == "shadow" and decision.kill_switch_on is True


def test_audit_events_are_ordered_and_scoped_to_their_packet(store):
    store.save_packet(_packet())
    store.append_audit_events([
        AgentAuditRow(session_id="sess-1", event_type="session_started", created_at="t1", packet_id="pkt-1"),
        AgentAuditRow(session_id="sess-1", event_type="evidence_retrieved", created_at="t2", packet_id="pkt-1",
                      tool_name="search_validated_evidence", inputs_json='{"q": 1}', outcome="ok"),
        AgentAuditRow(session_id="sess-2", event_type="session_started", created_at="t3", packet_id="pkt-other"),
    ])
    events = store.audit_events_for_packet("pkt-1")
    assert [e.event_type for e in events] == ["session_started", "evidence_retrieved"]
    assert events[1].tool_name == "search_validated_evidence"


# --- review-page reads -----------------------------------------------------------------

def _seed_three(store) -> None:
    for n, (decision, effective, blocked, issuer) in enumerate([
        ("AUTO_PUBLISHED", "NO_ACTION", "shadow_mode", "NVDA"),
        ("REVIEW_REQUIRED", "NO_ACTION", "kill_switch", "INTC"),
        ("NOT_MATERIAL", "NO_ACTION", None, "NVDA"),
    ], start=1):
        store.save_packet(_packet(packet_id=f"pkt-{n}", issuer_id=issuer, job_id=None,
                                  created_at=f"2026-09-2{n}T00:00:00+00:00"))
        store.record_decision(_decision(packet_id=f"pkt-{n}", policy_decision=decision,
                                        effective_decision=effective, blocked_by=blocked,
                                        kill_switch_on=blocked == "kill_switch",
                                        decided_at=f"2026-09-2{n}T01:00:00+00:00"))


def test_list_is_newest_first_and_filters_combine(store):
    _seed_three(store)
    rows = store.list_decisions()
    assert [r.packet_id for r in rows] == ["pkt-3", "pkt-2", "pkt-1"]
    assert [r.packet_id for r in store.list_decisions(policy_decision="AUTO_PUBLISHED")] == ["pkt-1"]
    assert [r.packet_id for r in store.list_decisions(blocked_only=True)] == ["pkt-2", "pkt-1"]
    assert [r.packet_id for r in store.list_decisions(issuer_id="NVDA")] == ["pkt-3", "pkt-1"]
    assert [r.packet_id for r in store.list_decisions(decided_from="2026-09-22T00:00:00+00:00")] == ["pkt-3", "pkt-2"]
    assert [r.packet_id for r in store.list_decisions(issuer_id="NVDA", blocked_only=True)] == ["pkt-1"]


def test_list_paginates(store):
    _seed_three(store)
    assert [r.packet_id for r in store.list_decisions(limit=2)] == ["pkt-3", "pkt-2"]
    assert [r.packet_id for r in store.list_decisions(limit=2, offset=2)] == ["pkt-1"]


def test_counts_group_by_effective_decision_and_respect_a_window(store):
    _seed_three(store)
    assert store.count_decisions() == {"NO_ACTION": 3}
    assert store.count_decisions(since="2026-09-22T00:00:00+00:00") == {"NO_ACTION": 2}


def test_packets_since_counts_the_daily_cap_window(store):
    _seed_three(store)
    assert store.count_packets_since(since="2026-09-22T00:00:00+00:00") == 2


# --- emergency control -------------------------------------------------------------------

def test_control_starts_empty_and_an_override_upserts_in_place(store):
    assert store.get_control() is None
    store.set_mode_override(mode="off", reason="incident 1", updated_by="operator", now="t1")
    control = store.get_control()
    assert (control.mode_override, control.reason, control.updated_by) == ("off", "incident 1", "operator")
    store.set_mode_override(mode="shadow", reason="resumed", updated_by="operator", now="t2")
    control = store.get_control()
    assert (control.mode_override, control.updated_at) == ("shadow", "t2")


# --- pure helpers --------------------------------------------------------------------------

def test_the_override_can_only_narrow_the_environment_mode():
    assert more_restrictive("publish", "off") == "off"
    assert more_restrictive("shadow", "publish") == "shadow"
    assert more_restrictive("off", "shadow") == "off"
    assert more_restrictive("shadow", "shadow") == "shadow"


def test_kill_switch_outranks_shadow_mode_as_the_blocking_reason():
    assert resolve_blocked_by(mode="shadow", kill_switch_on=True) == "kill_switch"
    assert resolve_blocked_by(mode="shadow", kill_switch_on=False) == "shadow_mode"
    assert resolve_blocked_by(mode="publish", kill_switch_on=True) == "kill_switch"
    assert resolve_blocked_by(mode="publish", kill_switch_on=False) is None
