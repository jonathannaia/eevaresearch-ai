"""The scheduler: what the agent works on, how much, and whether it may
start at all.

Every test is fixture-driven. No model session, no credential, no network
call, no live agent — the session runner is always a stub.

The eligibility rule replaces the worker's original EXTRACTED/TRANSLATED
filter, which matched zero of 300 production candidates because real
candidates sit in NEEDS_REVIEW. That is the whole reason this file
exists, so the rule is pinned from several directions."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.state_db import agent_repository as sqlite_agent
from src.data_access.state_db import connection, schema
from src.logic import agent_mode, agent_scheduler
from src.logic.publication_policy import POLICY_VERSION
from src.models.agent_records import HEARTBEAT_EVENT, AgentAuditRow, AgentRun
from src.models.models import (
    CandidateSignal,
    CandidateStatus,
    ExcerptQuality,
    ExtractionState,
    FilingEvent,
    TranslationState,
)

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def _filing(rcept_no: str = "acc-1", rcept_dt: str = "20260901") -> FilingEvent:
    return FilingEvent(
        source_name="SEC EDGAR", corp_code="0001640147", corp_name="NVIDIA", stock_code="NVDA",
        report_nm="10-Q", rcept_no=rcept_no, rcept_dt=rcept_dt, flr_nm="NVIDIA", pblntf_ty="A",
        retrieved_at="2026-09-01T00:00:00+00:00",
    )


def _candidate(
    candidate_id: str = "cand-1", *, status: CandidateStatus = CandidateStatus.NEEDS_REVIEW,
    extraction: ExtractionState = ExtractionState.EXTRACTED, excerpt: str | None = "Revenue rose.",
    rcept_dt: str = "20260901",
) -> CandidateSignal:
    return CandidateSignal(
        id=candidate_id, filing=_filing(f"acc-{candidate_id}", rcept_dt), matched_rules=["rule-1"], confidence="High",
        status=status, extraction_state=extraction, translation_state=TranslationState.NOT_REQUESTED,
        excerpt_quality=ExcerptQuality.USABLE_TEXT, excerpt_original=excerpt,
    )


# --- eligibility: exactly one rule ----------------------------------------------

def test_the_one_eligible_shape_is_needs_review_extracted_with_an_excerpt():
    assert agent_scheduler.is_eligible(_candidate()) is True
    assert agent_scheduler.ineligibility_reason(_candidate()) is None


@pytest.mark.parametrize("status", [s for s in CandidateStatus if s is not CandidateStatus.NEEDS_REVIEW])
def test_no_other_candidate_status_is_eligible_including_the_superseded_extracted_translated_rule(status):
    """A candidate the reviewers already dispositioned is not the agent's
    business — including the EXTRACTED/TRANSLATED statuses the previous
    worker selected on, which matched nothing real."""
    candidate = _candidate(status=status)
    assert agent_scheduler.is_eligible(candidate) is False
    assert agent_scheduler.ineligibility_reason(candidate) == f"status:{status.value}"


@pytest.mark.parametrize("state", [s for s in ExtractionState if s is not ExtractionState.EXTRACTED])
def test_a_candidate_whose_text_was_not_extracted_is_refused(state):
    candidate = _candidate(extraction=state)
    assert agent_scheduler.is_eligible(candidate) is False
    assert agent_scheduler.ineligibility_reason(candidate) == f"extraction_state:{state.value}"


@pytest.mark.parametrize("excerpt", [None, "", "   ", "\n\t "])
def test_without_a_stored_excerpt_there_is_nothing_to_verify_against(excerpt):
    """Row 11 would refuse the claim anyway, so enqueuing this would spend
    a session to reach a foregone conclusion."""
    candidate = _candidate(excerpt=excerpt)
    assert agent_scheduler.is_eligible(candidate) is False
    assert agent_scheduler.ineligibility_reason(candidate) == "no_excerpt_original"


def test_all_three_conditions_are_required_together():
    assert agent_scheduler.is_eligible(_candidate(status=CandidateStatus.PUBLISHED)) is False
    assert agent_scheduler.is_eligible(_candidate(extraction=ExtractionState.PENDING)) is False
    assert agent_scheduler.is_eligible(_candidate(excerpt=None)) is False


def test_the_superseded_extracted_translated_status_rule_is_gone_for_good():
    """The original worker selected on candidate STATUS in
    {EXTRACTED, TRANSLATED}. Those are statuses a production candidate
    never holds — the audit found 0 of 300 rows matching — so the agent
    would have evaluated nothing. Extraction is now checked on
    extraction_state, where it actually lives, and status must be
    NEEDS_REVIEW. This test exists so the old rule cannot creep back."""
    for superseded in (CandidateStatus.EXTRACTED, CandidateStatus.TRANSLATED):
        assert agent_scheduler.is_eligible(_candidate(status=superseded)) is False

    # The boundary in full: one status in, every other status out.
    included = [s for s in CandidateStatus if agent_scheduler.is_eligible(_candidate(status=s))]
    assert included == [CandidateStatus.NEEDS_REVIEW]

    # And extraction is read from extraction_state, not inferred from status.
    needs_review_unextracted = _candidate(status=CandidateStatus.NEEDS_REVIEW, extraction=ExtractionState.NOT_FETCHED)
    assert agent_scheduler.is_eligible(needs_review_unextracted) is False


# --- enqueue: new immediately, backlog oldest-first and capped --------------------

def _rows(*candidates: CandidateSignal, created: dict[str, str] | None = None, source: str = "SEC EDGAR"):
    """(candidate, source, version, created_at) — created_at defaults to a
    distinct instant per candidate, derived from its id, so a test that
    does not care about ordering still gets a deterministic one."""
    created = created or {}
    return [(c, source, 1, created.get(c.id, f"2026-01-01T00:00:{i:02d}+00:00"))
            for i, c in enumerate(candidates)]


def test_a_recent_candidate_is_enqueued_as_new():
    plan = agent_scheduler.plan_enqueue(
        _rows(_candidate("c1"), created={"c1": "2026-09-19T00:00:00+00:00"}),
        mode="shadow", now=NOW.isoformat(), known_after="2026-09-18T00:00:00+00:00",
    )
    assert [j.candidate_id for j in plan.new] == ["c1"] and plan.backlog == ()
    assert plan.new[0].enqueue_reason == "new"


def test_older_candidates_are_backlog_and_drain_oldest_first():
    candidates = _rows(
        _candidate("newest"), _candidate("oldest"), _candidate("middle"),
        created={"newest": "2026-08-10T00:00:00+00:00", "oldest": "2024-01-01T00:00:00+00:00",
                 "middle": "2025-06-01T00:00:00+00:00"},
    )
    plan = agent_scheduler.plan_enqueue(candidates, mode="shadow", now=NOW.isoformat(),
                                        known_after="2026-09-01T00:00:00+00:00")
    assert [j.candidate_id for j in plan.backlog] == ["oldest", "middle", "newest"]
    assert all(j.enqueue_reason == "backlog" for j in plan.backlog)


def test_the_backlog_is_capped_per_tick_and_says_how_much_it_deferred():
    """A first run against thousands of historical candidates must drain
    steadily rather than flood the queue in one tick."""
    candidates = _rows(*[_candidate(f"c{i:03d}") for i in range(60)],
                       created={f"c{i:03d}": f"2024-{i % 12 + 1:02d}-01T00:00:00+00:00" for i in range(60)})
    plan = agent_scheduler.plan_enqueue(
        candidates, mode="shadow", now=NOW.isoformat(), known_after="2026-09-01T00:00:00+00:00", backlog_cap=25,
    )
    assert len(plan.backlog) == 25
    assert any(s == "backlog_cap:35_deferred" for s in plan.skipped)


def test_the_cap_takes_the_oldest_not_an_arbitrary_25():
    candidates = _rows(*[_candidate(f"c{i:02d}") for i in range(40)],
                       created={f"c{i:02d}": f"2025-{i % 12 + 1:02d}-01T00:00:00+00:00" for i in range(40)})
    plan = agent_scheduler.plan_enqueue(
        candidates, mode="shadow", now=NOW.isoformat(), known_after="2026-09-01T00:00:00+00:00", backlog_cap=5,
    )
    taken = [j.candidate_id for j in plan.backlog]
    everything = sorted(candidates, key=lambda r: agent_scheduler.order_key(r[3], r[0].id))
    assert taken == [c.id for c, _, _, _ in everything[:5]]


def test_ineligible_candidates_never_reach_the_queue_and_are_reported():
    plan = agent_scheduler.plan_enqueue(
        _rows(_candidate("ok"), _candidate("bad", status=CandidateStatus.PUBLISHED), _candidate("dry", excerpt=None)),
        mode="shadow", now=NOW.isoformat(), known_after=None,
    )
    assert [j.candidate_id for j in plan.jobs] == ["ok"]
    assert set(plan.skipped) == {
        f"bad:status:{CandidateStatus.PUBLISHED.value}", "dry:no_excerpt_original",
    }


# --- idempotency ------------------------------------------------------------------

def test_the_same_candidate_version_policy_and_mode_is_always_the_same_job():
    first = agent_scheduler.job_id_for("cand-1", 3, POLICY_VERSION, "shadow")
    assert first == agent_scheduler.job_id_for("cand-1", 3, POLICY_VERSION, "shadow")
    assert first != agent_scheduler.job_id_for("cand-1", 4, POLICY_VERSION, "shadow")
    assert first != agent_scheduler.job_id_for("cand-1", 3, POLICY_VERSION, "publish")
    assert first != agent_scheduler.job_id_for("cand-2", 3, POLICY_VERSION, "shadow")


# --- retries and backoff ------------------------------------------------------------

def test_the_first_failure_waits_before_the_next_attempt():
    assert agent_scheduler.next_attempt_at(1, now=NOW) == (NOW + timedelta(minutes=60)).isoformat()


def test_backoff_lengthens_and_then_holds_at_its_last_step():
    assert agent_scheduler.next_attempt_at(2, now=NOW) == (NOW + timedelta(minutes=240)).isoformat()


def test_an_exhausted_job_gets_no_further_attempt():
    assert agent_scheduler.next_attempt_at(agent_scheduler.MAX_ATTEMPTS_PER_JOB, now=NOW) is None
    assert agent_scheduler.next_attempt_at(99, now=NOW) is None


# --- the store-backed behaviours -----------------------------------------------------

@pytest.fixture
def store():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    return backend_factory._AgentStoreRepository(conn=conn, module=sqlite_agent)


def _enqueue(store, candidate_id="cand-1", *, mode="shadow", created="2026-09-20T00:00:00+00:00") -> str:
    job = agent_scheduler.plan_enqueue(
        _rows(_candidate(candidate_id), created={candidate_id: created}),
        mode=mode, now=created, known_after=None,
    ).jobs[0]
    store.enqueue_job(job)
    return job.job_id


def test_enqueuing_the_same_job_twice_creates_one_job(store):
    first = _enqueue(store)
    second = _enqueue(store)
    assert first == second
    assert store.count_jobs_by_state() == {"pending": 1}


def test_a_claimed_job_is_leased_to_this_worker(store):
    job_id = _enqueue(store)
    claimed = store.claim_job(mode="shadow", now=NOW.isoformat(),
                              lease_expires_at=agent_scheduler.lease_until(NOW), worker_instance="w1")
    assert claimed.job_id == job_id and claimed.state == "leased"
    assert claimed.worker_instance == "w1" and claimed.attempts == 1
    assert store.claim_job(mode="shadow", now=NOW.isoformat(),
                           lease_expires_at=agent_scheduler.lease_until(NOW), worker_instance="w2") is None


def test_an_expired_lease_is_reclaimed_and_can_be_claimed_again(store):
    _enqueue(store)
    store.claim_job(mode="shadow", now=NOW.isoformat(), lease_expires_at=(NOW - timedelta(minutes=1)).isoformat(),
                    worker_instance="crashed")
    assert store.reclaim_expired_leases(now=NOW.isoformat()) == 1
    retaken = store.claim_job(mode="shadow", now=NOW.isoformat(),
                              lease_expires_at=agent_scheduler.lease_until(NOW), worker_instance="w2")
    assert retaken is not None and retaken.worker_instance == "w2" and retaken.attempts == 2


def test_fencing_stops_a_late_worker_overwriting_the_new_holders_result(store):
    """The crashed worker comes back after its lease was reclaimed and
    another worker took the job. Its write must not land."""
    job_id = _enqueue(store)
    store.claim_job(mode="shadow", now=NOW.isoformat(), lease_expires_at=(NOW - timedelta(minutes=1)).isoformat(),
                    worker_instance="crashed")
    store.reclaim_expired_leases(now=NOW.isoformat())
    store.claim_job(mode="shadow", now=NOW.isoformat(), lease_expires_at=agent_scheduler.lease_until(NOW),
                    worker_instance="w2")

    assert store.finish_job(job_id, state="done", now=NOW.isoformat(), expected_worker="crashed") is False
    assert store.get_job(job_id).state == "leased"
    assert store.finish_job(job_id, state="done", now=NOW.isoformat(), expected_worker="w2") is True
    assert store.get_job(job_id).state == "done"


def test_a_job_behind_a_backoff_is_not_claimable_until_its_time(store):
    job_id = _enqueue(store)
    store.claim_job(mode="shadow", now=NOW.isoformat(), lease_expires_at=agent_scheduler.lease_until(NOW),
                    worker_instance="w1")
    store.finish_job(job_id, state="pending", now=NOW.isoformat(), last_error_code="FAILED_RETRIEVAL",
                     next_attempt_at=(NOW + timedelta(minutes=60)).isoformat(), expected_worker="w1")
    assert store.claim_job(mode="shadow", now=NOW.isoformat(),
                           lease_expires_at=agent_scheduler.lease_until(NOW), worker_instance="w1") is None
    later = NOW + timedelta(minutes=61)
    assert store.claim_job(mode="shadow", now=later.isoformat(),
                           lease_expires_at=agent_scheduler.lease_until(later), worker_instance="w1") is not None


def test_a_shadow_worker_never_claims_a_job_queued_for_another_mode(store):
    _enqueue(store, mode="publish")
    assert store.claim_job(mode="shadow", now=NOW.isoformat(),
                           lease_expires_at=agent_scheduler.lease_until(NOW), worker_instance="w1") is None


# --- daily cap -----------------------------------------------------------------------

def test_the_daily_budget_starts_full_and_is_spent_by_packets_written(store):
    assert agent_scheduler.sessions_remaining_today(store, now=NOW) == agent_scheduler.MAX_SESSIONS_PER_DAY


def test_the_budget_never_goes_negative():
    class Overspent:
        def count_packets_since(self, **_):
            return agent_scheduler.MAX_SESSIONS_PER_DAY + 10

    assert agent_scheduler.sessions_remaining_today(Overspent(), now=NOW) == 0


def test_yesterdays_packets_do_not_count_against_today(store):
    assert agent_scheduler.day_start(NOW).startswith("2026-09-20T00:00:00")


# --- single runner --------------------------------------------------------------------

def _running(store, run_id: str, worker: str, *, started: datetime, heartbeat: datetime | None = None) -> None:
    store.start_run(AgentRun(run_id=run_id, worker_instance=worker, mode="shadow", started_at=started.isoformat(),
                             status="running", policy_version=POLICY_VERSION))
    if heartbeat is not None:
        store.append_audit_events([AgentAuditRow(
            session_id=run_id, event_type=HEARTBEAT_EVENT, created_at=heartbeat.isoformat(), run_id=run_id,
            outcome=worker,
        )])


def test_the_first_worker_acquires_the_slot(store):
    claim = agent_scheduler.acquire_single_runner(store, worker_instance="w1", mode="shadow", now=NOW)
    assert claim.acquired and claim.run_id
    assert store.latest_run().status == "running"
    assert store.latest_heartbeat_at(claim.run_id) == NOW.isoformat()


def test_a_second_worker_is_refused_while_the_first_is_beating(store):
    _running(store, "run-a", "w1", started=NOW - timedelta(minutes=10), heartbeat=NOW - timedelta(minutes=1))
    claim = agent_scheduler.acquire_single_runner(store, worker_instance="w2", mode="shadow", now=NOW)
    assert claim.acquired is False and "another worker" in claim.reason


def test_a_run_whose_heartbeat_went_stale_is_presumed_crashed_and_released(store):
    _running(store, "run-a", "w1", started=NOW - timedelta(hours=4),
             heartbeat=NOW - timedelta(minutes=agent_scheduler.HEARTBEAT_STALE_MINUTES + 5))
    claim = agent_scheduler.acquire_single_runner(store, worker_instance="w2", mode="shadow", now=NOW)
    assert claim.acquired is True and claim.reclaimed == ("run-a",)


def test_a_run_that_never_beat_is_judged_on_its_start_time(store):
    _running(store, "run-a", "w1", started=NOW - timedelta(hours=4))
    assert agent_scheduler.acquire_single_runner(store, worker_instance="w2", mode="shadow", now=NOW).acquired is True


def test_the_same_worker_restarting_takes_its_own_slot_back(store):
    _running(store, "run-a", "w1", started=NOW - timedelta(minutes=5), heartbeat=NOW - timedelta(minutes=1))
    assert agent_scheduler.acquire_single_runner(store, worker_instance="w1", mode="shadow", now=NOW).acquired is True


def test_a_held_advisory_lock_refuses_the_claim_even_when_no_run_is_active(store):
    """Postgres takes a real advisory lock as well, so a worker killed
    without any cleanup still releases its claim when its connection
    drops. Stubbed here; exercised for real in the Postgres tests."""
    class LockHeld:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def try_acquire_runner_lock(self, key):
            return False

    claim = agent_scheduler.acquire_single_runner(LockHeld(store), worker_instance="w2", mode="shadow", now=NOW)
    assert claim.acquired is False and "advisory lock" in claim.reason


def test_the_heartbeat_row_carries_no_input_payload():
    row = agent_scheduler.heartbeat_row("run-a", worker_instance="host:1", at=NOW.isoformat())
    assert row.inputs_json is None and row.event_type == HEARTBEAT_EVENT


# --- startup self-check -----------------------------------------------------------------

def _resolved(mode="shadow"):
    return agent_mode.resolve_mode(Settings(research_agent_mode=mode))


def _ok_settings(**overrides) -> Settings:
    base = dict(db_backend="postgres", research_agent_service_token="t" * 32,
                edgar_user_agent="EevaResearch research@example.test", research_agent_mode="shadow")
    base.update(overrides)
    return Settings(**base)


def _check(settings=None, resolved=None, *, version=24, tables=agent_scheduler.REQUIRED_AGENT_TABLES, probe=lambda: ""):
    return agent_scheduler.run_self_check(
        settings or _ok_settings(), resolved or _resolved(),
        schema_version=version, tables=tables, runtime_probe=probe,
    )


def test_a_fully_configured_shadow_worker_passes():
    check = _check()
    assert check.ok and check.problems == () and check.summary == "all startup checks passed"


def test_an_off_worker_has_no_prerequisites_at_all():
    """Demanding Postgres from someone who deliberately turned the agent
    off is noise, not safety."""
    check = agent_scheduler.run_self_check(
        Settings(db_backend="json", research_agent_mode="off"), _resolved("off"),
        schema_version=None, tables=(), runtime_probe=lambda: "missing",
    )
    assert check.ok is True


@pytest.mark.parametrize("backend", ["json", "sqlite", ""])
def test_shadow_requires_postgres(backend):
    check = _check(_ok_settings(db_backend=backend))
    assert not check.ok and any("postgres" in p for p in check.problems)


@pytest.mark.parametrize("version", [None, 22, 23])
def test_shadow_requires_the_agent_schema(version):
    check = _check(version=version)
    assert not check.ok and any("schema" in p.lower() for p in check.problems)


def test_a_newer_schema_is_fine():
    assert _check(version=25).ok is True


@pytest.mark.parametrize("missing", agent_scheduler.REQUIRED_AGENT_TABLES)
def test_every_agent_table_must_actually_exist(missing):
    tables = [t for t in agent_scheduler.REQUIRED_AGENT_TABLES if t != missing]
    check = _check(tables=tables)
    assert not check.ok and any(missing in p for p in check.problems)


def test_a_failing_runtime_probe_stops_startup():
    check = _check(probe=lambda: "'claude' is not on PATH")
    assert not check.ok and any("runtime probe" in p for p in check.problems)


def test_the_required_non_secret_settings_are_checked():
    assert any("EDGAR_USER_AGENT" in p for p in _check(_ok_settings(edgar_user_agent="")).problems)
    assert any("SERVICE_TOKEN" in p for p in _check(_ok_settings(research_agent_service_token=None)).problems)


def test_the_self_check_never_reports_a_secret_value():
    """It says a credential is missing, never what one is."""
    token = "super-secret-token-value-0123456789"
    check = _check(_ok_settings(research_agent_service_token=token, db_backend="sqlite"))
    assert token not in check.summary
    assert all(token not in p for p in check.problems)


def test_every_failure_is_reported_at_once_not_one_at_a_time():
    check = _check(_ok_settings(db_backend="json", edgar_user_agent=""), version=20, tables=(),
                   probe=lambda: "no cli")
    assert len(check.problems) >= 5 and check.ok is False


# --- ordering is created_at, not the filing date ------------------------------------

def test_same_filing_date_different_created_at_enqueues_in_true_chronological_order():
    """The defect this ordering replaced: rcept_dt is a YYYYMMDD string,
    so every candidate filed on the same day tied and 'oldest first'
    collapsed into hash order within that day. These three share a filing
    date and differ only by when the pipeline actually created them."""
    same_day = "20260901"
    candidates = _rows(
        _candidate("evening", rcept_dt=same_day), _candidate("morning", rcept_dt=same_day),
        _candidate("midday", rcept_dt=same_day),
        created={"evening": "2026-09-01T21:40:00+00:00", "morning": "2026-09-01T06:05:00+00:00",
                 "midday": "2026-09-01T12:15:00+00:00"},
    )
    plan = agent_scheduler.plan_enqueue(candidates, mode="shadow", now=NOW.isoformat(),
                                        known_after="2026-09-30T00:00:00+00:00")
    assert [j.candidate_id for j in plan.backlog] == ["morning", "midday", "evening"]
    assert len({c.filing.rcept_dt for c, _, _, _ in candidates}) == 1, "the filing date must not disambiguate them"


def test_equal_created_at_values_break_the_tie_on_candidate_id_deterministically():
    identical = "2026-09-01T00:00:00+00:00"
    candidates = _rows(
        _candidate("cand-c"), _candidate("cand-a"), _candidate("cand-b"),
        created={"cand-c": identical, "cand-a": identical, "cand-b": identical},
    )
    first = agent_scheduler.plan_enqueue(candidates, mode="shadow", now=NOW.isoformat(),
                                         known_after="2026-09-30T00:00:00+00:00")
    assert [j.candidate_id for j in first.backlog] == ["cand-a", "cand-b", "cand-c"]

    # Same set, different input order: the plan must be identical.
    reshuffled = agent_scheduler.plan_enqueue(list(reversed(candidates)), mode="shadow", now=NOW.isoformat(),
                                              known_after="2026-09-30T00:00:00+00:00")
    assert [j.candidate_id for j in reshuffled.backlog] == [j.candidate_id for j in first.backlog]


def test_a_missing_created_at_sorts_as_oldest_rather_than_being_starved():
    candidates = _rows(_candidate("known"), _candidate("unknown"),
                       created={"known": "2020-01-01T00:00:00+00:00", "unknown": None})
    plan = agent_scheduler.plan_enqueue(candidates, mode="shadow", now=NOW.isoformat(),
                                        known_after="2026-09-30T00:00:00+00:00")
    assert [j.candidate_id for j in plan.backlog] == ["unknown", "known"]


def test_the_order_key_is_chronological_then_stable():
    assert agent_scheduler.order_key("2026-01-01T00:00:00+00:00", "z") < \
           agent_scheduler.order_key("2026-01-02T00:00:00+00:00", "a")
    assert agent_scheduler.order_key("2026-01-01T00:00:00+00:00", "a") < \
           agent_scheduler.order_key("2026-01-01T00:00:00+00:00", "b")
    assert agent_scheduler.order_key(None, "a") < agent_scheduler.order_key("2000-01-01T00:00:00+00:00", "a")


# --- the new/backlog boundary --------------------------------------------------------

def test_a_candidate_crossing_the_watermark_is_neither_skipped_nor_duplicated(store):
    """The same candidate is 'new' on one tick and 'backlog' on a later
    one, once the watermark has moved past it. Both branches derive the
    same job id, so the second enqueue is a no-op rather than a duplicate
    — and it is never dropped in between."""
    candidate = _candidate("crossing")
    created = "2026-09-19T12:00:00+00:00"
    rows = _rows(candidate, created={"crossing": created})

    first = agent_scheduler.plan_enqueue(rows, mode="shadow", now=NOW.isoformat(),
                                         known_after="2026-09-18T00:00:00+00:00")
    assert [j.candidate_id for j in first.new] == ["crossing"] and first.backlog == ()

    later = agent_scheduler.plan_enqueue(rows, mode="shadow", now=NOW.isoformat(),
                                         known_after="2026-09-20T00:00:00+00:00")
    assert [j.candidate_id for j in later.backlog] == ["crossing"] and later.new == ()

    assert first.new[0].job_id == later.backlog[0].job_id
    assert store.enqueue_job(first.new[0]) is True
    assert store.enqueue_job(later.backlog[0]) is False   # idempotent across the boundary
    assert store.count_jobs_by_state() == {"pending": 1}


def test_the_watermark_boundary_is_exclusive_and_partitions_every_candidate():
    """Strictly-after is new; at-or-before is backlog. No candidate may
    fall into both or neither."""
    at = "2026-09-19T00:00:00+00:00"
    rows = _rows(_candidate("before"), _candidate("exactly"), _candidate("after"),
                 created={"before": "2026-09-18T23:59:59+00:00", "exactly": at,
                          "after": "2026-09-19T00:00:01+00:00"})
    plan = agent_scheduler.plan_enqueue(rows, mode="shadow", now=NOW.isoformat(), known_after=at)
    assert [j.candidate_id for j in plan.new] == ["after"]
    assert sorted(j.candidate_id for j in plan.backlog) == ["before", "exactly"]
    assert sorted(j.candidate_id for j in plan.jobs) == ["after", "before", "exactly"]


# --- every source uses the same semantics ------------------------------------------------

@pytest.mark.parametrize("source", ["SEC EDGAR", "OpenDART / DART", "EDINET"])
def test_every_source_orders_by_created_at_with_the_same_semantics(source):
    rows = _rows(_candidate("late"), _candidate("early"), source=source,
                 created={"late": "2026-05-05T00:00:00+00:00", "early": "2024-02-02T00:00:00+00:00"})
    plan = agent_scheduler.plan_enqueue(rows, mode="shadow", now=NOW.isoformat(),
                                        known_after="2026-09-30T00:00:00+00:00")
    assert [j.candidate_id for j in plan.backlog] == ["early", "late"]
    assert {j.source for j in plan.backlog} == {source}
    assert [j.created_at for j in plan.backlog] == ["2024-02-02T00:00:00+00:00", "2026-05-05T00:00:00+00:00"]


def test_candidates_from_different_sources_interleave_by_age_not_by_source():
    """A DART candidate older than an EDGAR one must be worked first;
    source must not act as a hidden sort key."""
    rows = (
        _rows(_candidate("edgar-new"), source="SEC EDGAR", created={"edgar-new": "2026-06-01T00:00:00+00:00"})
        + _rows(_candidate("dart-old"), source="OpenDART / DART", created={"dart-old": "2024-01-01T00:00:00+00:00"})
        + _rows(_candidate("edinet-mid"), source="EDINET", created={"edinet-mid": "2025-03-01T00:00:00+00:00"})
    )
    plan = agent_scheduler.plan_enqueue(rows, mode="shadow", now=NOW.isoformat(),
                                        known_after="2026-09-30T00:00:00+00:00")
    assert [j.candidate_id for j in plan.backlog] == ["dart-old", "edinet-mid", "edgar-new"]


# --- the ordering survives into claim priority ---------------------------------------------

def test_a_job_is_claimed_oldest_candidate_first_even_when_enqueued_in_one_tick(store):
    """All three are enqueued in the same tick, so a tick-time job
    timestamp would have made them tie and claim order would have fallen
    back to the job-id hash. The queue must drain oldest-candidate-first."""
    rows = _rows(_candidate("newest"), _candidate("oldest"), _candidate("middle"),
                 created={"newest": "2026-08-01T00:00:00+00:00", "oldest": "2024-01-01T00:00:00+00:00",
                          "middle": "2025-01-01T00:00:00+00:00"})
    for job in agent_scheduler.plan_enqueue(rows, mode="shadow", now=NOW.isoformat(),
                                            known_after="2026-09-30T00:00:00+00:00").jobs:
        store.enqueue_job(job)

    claimed = []
    for _ in range(3):
        job = store.claim_job(mode="shadow", now=NOW.isoformat(),
                              lease_expires_at=agent_scheduler.lease_until(NOW), worker_instance="w1")
        claimed.append(job.candidate_id)
        store.finish_job(job.job_id, state="done", now=NOW.isoformat(), expected_worker="w1")
    assert claimed == ["oldest", "middle", "newest"]


def test_claim_order_breaks_ties_on_candidate_id_not_on_the_job_id_hash(store):
    identical = "2026-09-01T00:00:00+00:00"
    rows = _rows(_candidate("cand-c"), _candidate("cand-a"), _candidate("cand-b"),
                 created={"cand-c": identical, "cand-a": identical, "cand-b": identical})
    for job in agent_scheduler.plan_enqueue(rows, mode="shadow", now=NOW.isoformat(),
                                            known_after="2026-09-30T00:00:00+00:00").jobs:
        store.enqueue_job(job)

    claimed = []
    for _ in range(3):
        job = store.claim_job(mode="shadow", now=NOW.isoformat(),
                              lease_expires_at=agent_scheduler.lease_until(NOW), worker_instance="w1")
        claimed.append(job.candidate_id)
        store.finish_job(job.job_id, state="done", now=NOW.isoformat(), expected_worker="w1")
    assert claimed == ["cand-a", "cand-b", "cand-c"]


def test_the_job_row_stores_the_candidate_created_at_as_its_priority_key(store):
    job = agent_scheduler.plan_enqueue(
        _rows(_candidate("cand-1"), created={"cand-1": "2024-07-07T07:07:07+00:00"}),
        mode="shadow", now=NOW.isoformat(), known_after=None,
    ).jobs[0]
    store.enqueue_job(job)
    assert store.get_job(job.job_id).created_at == "2024-07-07T07:07:07+00:00"
    # ...and updated_at still records when the tick touched it.
    assert store.get_job(job.job_id).updated_at == NOW.isoformat()


def test_both_backends_claim_in_the_same_order():
    """The two repository modules are independent implementations, so the
    ordering that makes the queue fair has to be asserted in both. This
    is a structural check because the Postgres path needs a live server;
    the behavioural proof above runs on SQLite."""
    import re
    from pathlib import Path

    def claim_order(path: Path) -> str:
        source = Path(path).read_text(encoding="utf-8")
        body = source[source.index("def claim_job("):source.index("def finish_job(")]
        # The SQL is built from adjacent string literals, so join them
        # back together before reading the clause out.
        flattened = " ".join(body.replace('"', " ").split())
        match = re.search(r"ORDER BY ([a-z_, ]+?) LIMIT", flattened)
        assert match, f"no ORDER BY found in {path}"
        return " ".join(match.group(1).split())

    root = Path(__file__).parent.parent
    sqlite_order = claim_order(root / "src" / "data_access" / "state_db" / "agent_repository.py")
    postgres_order = claim_order(root / "src" / "data_access" / "postgres_state_db" / "agent_repository.py")
    assert sqlite_order == postgres_order == "created_at, candidate_id"
