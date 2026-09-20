"""The worker loop itself: one tick against a durable queue.

This file replaces the selection tests written for the worker's original
design, which chose candidates in EXTRACTED/TRANSLATED status and ran
them immediately. That filter matched zero of 300 production candidates,
and it had no queue, so a crash lost the work. The eligibility rule and
the queue both changed in the Agent Observability and Shadow Mode
release, and these tests pin the new behaviour.

The session runner is always a stub returning a recorded outcome: no
model session, no credential, no network call, no CLI process."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.state_db import agent_repository as sqlite_agent
from src.data_access.state_db import connection, schema
from src.logic import agent_scheduler
from src.models.agent_records import AgentControl
from src.models.models import (
    CandidateSignal,
    CandidateStatus,
    ExcerptQuality,
    ExtractionState,
    FilingEvent,
    TranslationState,
)

import scripts.research_agent_worker as worker

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
CIK = "0001045810"  # NVIDIA


class _Issuer:
    """The registry only carries identifiers for the EDINET entries; EDGAR
    and DART are resolved lazily. Both paths are exercised below."""
    def __init__(self, issuer_id="edgar:NVDA", identifiers=None, primary_ticker="NVDA"):
        self.issuer_id = issuer_id
        self.identifiers = identifiers or {}
        self.primary_ticker = primary_ticker


ISSUERS = (_Issuer(identifiers={"SEC EDGAR": CIK}),)


@dataclass
class _Outcome:
    packet_id: str | None
    error: str | None = None


def _filing(rcept_no: str, rcept_dt: str = "20260901") -> FilingEvent:
    return FilingEvent(
        source_name="SEC EDGAR", corp_code=CIK, corp_name="NVIDIA", stock_code="NVDA", report_nm="10-Q",
        rcept_no=rcept_no, rcept_dt=rcept_dt, flr_nm="NVIDIA", pblntf_ty="A",
        retrieved_at="2026-09-01T00:00:00+00:00",
    )


def _candidate(candidate_id="cand-1", *, status=CandidateStatus.NEEDS_REVIEW,
               extraction=ExtractionState.EXTRACTED, excerpt="Revenue rose.", rcept_dt="20260901") -> CandidateSignal:
    return CandidateSignal(
        id=candidate_id, filing=_filing(f"acc-{candidate_id}", rcept_dt), matched_rules=["r"], confidence="High",
        status=status, extraction_state=extraction, translation_state=TranslationState.NOT_REQUESTED,
        excerpt_quality=ExcerptQuality.USABLE_TEXT, excerpt_original=excerpt,
    )


@pytest.fixture
def store():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    return backend_factory._AgentStoreRepository(conn=conn, module=sqlite_agent)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(cache_dir=tmp_path, db_backend="sqlite", research_agent_mode="shadow",
                    research_agent_service_token="t" * 32)


@pytest.fixture
def stub_candidates(monkeypatch):
    """Stands in for the candidate repositories. The worker only ever
    reads them, so a list is a faithful stand-in."""
    holder: dict[str, list] = {"rows": [], "skipped": []}

    def fake_load(_settings):
        return [(c, "SEC EDGAR", 1) for c in holder["rows"]], list(holder["skipped"])

    monkeypatch.setattr(worker, "load_candidates", fake_load)
    return holder


def _runner(packet_id="pkt-1", *, decision="AUTO_PUBLISHED", raises=None):
    calls: list[str] = []

    async def run(_settings, scope, **_kwargs):
        calls.append(scope.candidate_id)
        if raises is not None:
            raise raises
        return _Outcome(packet_id=packet_id)

    run.calls = calls  # type: ignore[attr-defined]
    return run


@pytest.fixture
def stub_packet(monkeypatch):
    state = {"decision": "AUTO_PUBLISHED"}

    @dataclass
    class _Stored:
        decision: str | None

    monkeypatch.setattr(worker.packet_store, "load_packet",
                        lambda _cache, packet_id: _Stored(decision=state["decision"]))
    return state


# --- the tick enqueues, then works the queue -------------------------------------

def test_a_tick_enqueues_eligible_candidates_and_runs_one(settings, store, stub_candidates, stub_packet):
    stub_candidates["rows"] = [_candidate("cand-1")]
    runner = _runner()
    report = worker.run_one_tick(settings, store=store, session_runner=runner, now=NOW, instance="w1", issuers=ISSUERS)
    assert report.mode == "shadow" and report.halted == ""
    assert report.enqueued_new + report.enqueued_backlog == 1
    assert report.started == ("cand-1",) and runner.calls == ["cand-1"]
    assert store.count_jobs_by_state() == {"done": 1}


def test_ineligible_candidates_are_never_enqueued_or_run(settings, store, stub_candidates, stub_packet):
    stub_candidates["rows"] = [
        _candidate("published", status=CandidateStatus.PUBLISHED),
        _candidate("unextracted", extraction=ExtractionState.PENDING),
        _candidate("no-excerpt", excerpt=None),
    ]
    runner = _runner()
    report = worker.run_one_tick(settings, store=store, session_runner=runner, now=NOW, instance="w1", issuers=ISSUERS)
    assert report.started == () and runner.calls == []
    assert store.count_jobs_by_state() == {}


def test_a_second_tick_over_the_same_candidate_does_not_duplicate_the_job(settings, store, stub_candidates, stub_packet):
    stub_candidates["rows"] = [_candidate("cand-1")]
    worker.run_one_tick(settings, store=store, session_runner=_runner(), now=NOW, instance="w1", issuers=ISSUERS)
    second = worker.run_one_tick(settings, store=store, session_runner=_runner(), now=NOW + timedelta(hours=1),
                                 instance="w1", issuers=ISSUERS)
    assert second.enqueued_new + second.enqueued_backlog == 0
    assert sum(store.count_jobs_by_state().values()) == 1


def test_only_max_sessions_per_tick_run_however_long_the_queue_is(settings, store, stub_candidates, stub_packet):
    stub_candidates["rows"] = [_candidate(f"cand-{i}") for i in range(10)]
    runner = _runner()
    report = worker.run_one_tick(settings, store=store, session_runner=runner, now=NOW, instance="w1", issuers=ISSUERS)
    assert len(report.started) == agent_scheduler.MAX_SESSIONS_PER_TICK
    assert len(runner.calls) == agent_scheduler.MAX_SESSIONS_PER_TICK


# --- outcomes -----------------------------------------------------------------------

def test_a_retryable_decision_goes_back_to_pending_behind_a_backoff(settings, store, stub_candidates, stub_packet):
    stub_candidates["rows"] = [_candidate("cand-1")]
    stub_packet["decision"] = "FAILED_RETRIEVAL"
    worker.run_one_tick(settings, store=store, session_runner=_runner(), now=NOW, instance="w1", issuers=ISSUERS)
    assert store.count_jobs_by_state() == {"pending": 1}
    job_id = agent_scheduler.job_id_for("cand-1", 1, worker.POLICY_VERSION, "shadow")
    job = store.get_job(job_id)
    assert job.last_error_code == "FAILED_RETRIEVAL" and job.next_attempt_at > NOW.isoformat()


def test_a_session_that_raises_never_stops_the_tick_and_records_only_the_type(
    settings, store, stub_candidates, stub_packet, capsys,
):
    stub_candidates["rows"] = [_candidate("cand-1")]
    runner = _runner(raises=RuntimeError("connection to sk-secret-value failed"))
    report = worker.run_one_tick(settings, store=store, session_runner=runner, now=NOW, instance="w1", issuers=ISSUERS)
    assert report.outcomes[0][2] == "error:RuntimeError"
    job = store.get_job(agent_scheduler.job_id_for("cand-1", 1, worker.POLICY_VERSION, "shadow"))
    assert job.last_error_code == "RuntimeError"
    assert "sk-secret-value" not in capsys.readouterr().out


def test_a_session_that_saved_no_packet_is_retried(settings, store, stub_candidates, stub_packet):
    stub_candidates["rows"] = [_candidate("cand-1")]
    runner = _runner(packet_id=None)
    worker.run_one_tick(settings, store=store, session_runner=runner, now=NOW, instance="w1", issuers=ISSUERS)
    job = store.get_job(agent_scheduler.job_id_for("cand-1", 1, worker.POLICY_VERSION, "shadow"))
    assert job.state == "pending" and job.last_error_code == "no_packet"


def test_an_unresolvable_issuer_kills_the_job_rather_than_retrying_forever(
    settings, store, stub_candidates, stub_packet,
):
    unknown = _candidate("cand-1")
    unknown.filing.corp_code = "9999999999"
    stub_candidates["rows"] = [unknown]
    runner = _runner()
    report = worker.run_one_tick(settings, store=store, session_runner=runner, now=NOW, instance="w1", issuers=ISSUERS)
    assert runner.calls == []
    assert store.count_jobs_by_state() == {"dead": 1}
    assert report.outcomes[0][2] == "dead:issuer_unresolved"


# --- caps and holds --------------------------------------------------------------------

def test_the_daily_cap_halts_the_tick_before_any_session(settings, store, stub_candidates, stub_packet, monkeypatch):
    stub_candidates["rows"] = [_candidate("cand-1")]
    monkeypatch.setattr(agent_scheduler, "sessions_remaining_today", lambda *a, **k: 0)
    runner = _runner()
    report = worker.run_one_tick(settings, store=store, session_runner=runner, now=NOW, instance="w1", issuers=ISSUERS)
    assert "daily session cap" in report.halted and runner.calls == []
    # ...but the queue was still filled, so the work is not lost.
    assert store.count_jobs_by_state() == {"pending": 1}


def test_an_emergency_override_stops_the_next_tick(settings, store, stub_candidates, stub_packet):
    stub_candidates["rows"] = [_candidate("cand-1")]
    store.set_mode_override(mode="off", reason="paging", updated_by="oncall", now=NOW.isoformat())
    runner = _runner()
    report = worker.run_one_tick(settings, store=store, session_runner=runner, now=NOW, instance="w1", issuers=ISSUERS)
    assert report.mode == "off" and "mode is off" in report.halted
    assert runner.calls == [] and store.count_jobs_by_state() == {}


def test_an_override_can_never_elevate_the_mode(settings, store, stub_candidates, stub_packet):
    """Nothing in the database can raise the agent's authority, including
    a row that names a higher mode."""
    store.set_mode_override(mode="publish", reason="please", updated_by="someone", now=NOW.isoformat())
    stub_candidates["rows"] = [_candidate("cand-1")]
    report = worker.run_one_tick(settings, store=store, session_runner=_runner(), now=NOW, instance="w1", issuers=ISSUERS)
    assert report.mode == "shadow"


def test_a_configured_publish_mode_runs_as_shadow_and_never_publishes(store, stub_candidates, stub_packet, tmp_path):
    settings = Settings(cache_dir=tmp_path, db_backend="sqlite", research_agent_mode="publish",
                        research_agent_service_token="t" * 32)
    stub_candidates["rows"] = [_candidate("cand-1")]
    report = worker.run_one_tick(settings, store=store, session_runner=_runner(), now=NOW, instance="w1", issuers=ISSUERS)
    assert report.mode == "shadow"
    resolved = worker.resolve_effective_mode(settings, store)
    assert resolved.may_write_outside_agent_tables is False
    assert resolved.status_lines()[:2] == (
        "Configured: publish", "Effective: shadow (publishing unavailable in this release)",
    )


def test_an_unreadable_control_row_never_widens_anything(settings, store, stub_candidates, stub_packet):
    class Broken:
        def __getattr__(self, name):
            return getattr(store, name)

        def get_control(self):
            raise RuntimeError("control table unavailable")

    assert worker.resolve_effective_mode(settings, Broken()).mode == "shadow"


# --- leases ------------------------------------------------------------------------------

def test_a_tick_reclaims_leases_abandoned_by_a_crashed_worker(settings, store, stub_candidates, stub_packet):
    stub_candidates["rows"] = [_candidate("cand-1")]
    job = agent_scheduler.plan_enqueue(
        [(_candidate("cand-1"), "SEC EDGAR", 1)], mode="shadow", now=NOW.isoformat(), known_after=None,
    ).jobs[0]
    store.enqueue_job(job)
    store.claim_job(mode="shadow", now=NOW.isoformat(),
                    lease_expires_at=(NOW - timedelta(minutes=1)).isoformat(), worker_instance="crashed")
    report = worker.run_one_tick(settings, store=store, session_runner=_runner(), now=NOW, instance="w1", issuers=ISSUERS)
    assert report.reclaimed == 1 and report.started == ("cand-1",)


# --- startup self-check --------------------------------------------------------------------

def test_the_runtime_probe_only_looks_for_the_cli_and_never_runs_it(monkeypatch):
    monkeypatch.setattr(worker.shutil, "which", lambda name: "/usr/local/bin/claude")
    assert worker.probe_agent_runtime() == ""
    monkeypatch.setattr(worker.shutil, "which", lambda name: None)
    assert "not on PATH" in worker.probe_agent_runtime()


def test_the_self_check_reads_the_real_schema_version_and_tables(settings, store):
    from src.logic import agent_mode
    resolved = agent_mode.resolve_mode(settings)
    check = worker.run_self_check(settings, resolved, store=store, probe=lambda: "")
    # SQLite is refused for a shadow run, but the tables and version are
    # genuinely read from the database rather than assumed.
    assert any("postgres" in p for p in check.problems)
    assert not any("missing agent tables" in p for p in check.problems)
    version, tables = store.describe_database()
    assert version >= 22 and "agent_decisions" in tables


def test_an_off_worker_passes_the_self_check_without_touching_a_database(tmp_path):
    from src.logic import agent_mode
    settings = Settings(cache_dir=tmp_path, db_backend="json", research_agent_mode="off")
    check = worker.run_self_check(settings, agent_mode.resolve_mode(settings), probe=lambda: "no cli")
    assert check.ok is True


# --- secret hygiene ---------------------------------------------------------------------------

SECRET = "sk-live-0123456789abcdef-super-secret"


def test_the_worker_instance_identifier_is_derived_only_from_host_and_pid():
    assert SECRET not in worker.worker_instance()
    assert worker.worker_instance().count(":") == 1


def test_no_tick_output_or_stored_row_can_contain_a_credential(store, stub_candidates, stub_packet, tmp_path, capsys):
    settings = Settings(cache_dir=tmp_path, db_backend="sqlite", research_agent_mode="shadow",
                        research_agent_service_token=SECRET, dart_api_key=SECRET,
                        state_db_url=f"postgresql://u:{SECRET}@host/db")
    stub_candidates["rows"] = [_candidate("cand-1")]
    worker.run_one_tick(settings, store=store, session_runner=_runner(), now=NOW, instance="w1", issuers=ISSUERS)
    assert SECRET not in capsys.readouterr().out

    rows = store.conn.execute("SELECT * FROM agent_jobs").fetchall()
    assert rows and all(SECRET not in str(tuple(r)) for r in rows)
    for table in ("agent_runs", "agent_audit_events", "agent_packets", "agent_decisions"):
        stored = store.conn.execute(f"SELECT * FROM {table}").fetchall()
        assert all(SECRET not in str(tuple(r)) for r in stored), table


def test_a_heartbeat_row_stores_the_instance_but_no_payload(store):
    claim = agent_scheduler.acquire_single_runner(store, worker_instance="host:42", mode="shadow", now=NOW)
    rows = store.conn.execute("SELECT * FROM agent_audit_events WHERE run_id = ?", (claim.run_id,)).fetchall()
    assert [r["inputs_json"] for r in rows] == [None]
    assert rows[0]["outcome"] == "host:42"


# --- issuer resolution ---------------------------------------------------------------------

def test_an_issuer_resolves_from_the_registry_when_it_carries_an_identifier():
    assert worker._issuer_id_for(_filing("acc-1"), ISSUERS) == "edgar:NVDA"


def test_an_issuer_also_resolves_from_the_lazily_populated_resolver_cache():
    """EDGAR and DART identifiers are not in the registry — they are
    resolved at runtime and cached. Consulting only the registry would
    leave every EDGAR candidate unresolvable and kill every job."""
    registry_only = (_Issuer(identifiers={}),)
    assert worker._issuer_id_for(_filing("acc-1"), registry_only) is None
    assert worker._issuer_id_for(_filing("acc-1"), registry_only, resolved={"edgar:NVDA": CIK}) == "edgar:NVDA"


def test_a_cik_matches_regardless_of_leading_zero_padding():
    assert worker._issuer_id_for(_filing("acc-1"), (), resolved={"edgar:NVDA": "1045810"}) == "edgar:NVDA"


def test_an_unknown_corp_code_resolves_to_nothing_rather_than_a_wrong_issuer():
    assert worker._issuer_id_for(_filing("acc-1"), (), resolved={"edgar:OTHER": "9999999"}) is None


def test_a_missing_resolver_cache_never_halts_the_tick(settings, monkeypatch):
    monkeypatch.setattr(worker.backend_factory, "get_identifier_repository",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no cache")))
    assert worker.resolved_identifiers_for(settings, "SEC EDGAR", ISSUERS) == {}
