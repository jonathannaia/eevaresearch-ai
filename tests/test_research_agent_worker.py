"""Autonomous Research Agent, Phase 4 — the dormant orchestrator
(scripts/research_agent_worker.py, design §8.3/§11/§12). Selection,
idempotency, retry rules, and the tick loop against a seeded JSON
candidate store and an injected session runner. No SDK, no network."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import scripts.research_agent_worker as worker
from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.agent_audit import build_audit_event
from src.data_access.dart import candidate_store
from src.mcp_agent import packet_store
from src.mcp_agent.agent_session import SessionOutcome
from src.mcp_agent.contracts import Claim, ClaimCategory, ClaimProposal, ClaimType, EvidenceRecord, FreshnessStatus, SourceTier
from src.models.issuer import CoverageState, Issuer
from src.models.models import CandidateSignal, CandidateStatus, FilingEvent, StateTransition

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
AT = NOW.isoformat()
CIK = "1769628"
ISSUERS = (Issuer(issuer_id="coreweave", legal_name="CoreWeave, Inc.", country_or_jurisdiction="US", coverage_state=CoverageState.SEED, identifiers={"SEC EDGAR": CIK}),)


def _settings(tmp_path) -> Settings:
    return Settings(cache_dir=tmp_path, db_backend="json", research_agent_service_token="secret")


def _filing(rcept_no: str, corp_code: str = CIK, rcept_dt: str = "2026-09-10") -> FilingEvent:
    return FilingEvent(rcept_no=rcept_no, corp_code=corp_code, corp_name="CoreWeave, Inc.", stock_code="CRWV", report_nm="10-Q", rcept_dt=rcept_dt,
                       flr_nm="CoreWeave, Inc.", pblntf_ty="10-Q", source_name="SEC EDGAR", original_language="English", primary_document="crwv-10q.htm",
                       source_url=f"https://www.sec.gov/Archives/edgar/data/{corp_code}/{rcept_no}/", retrieved_at=AT)


def _candidate(candidate_id: str, rcept_no: str, status: CandidateStatus = CandidateStatus.EXTRACTED, **filing_overrides) -> CandidateSignal:
    return CandidateSignal(id=candidate_id, filing=_filing(rcept_no, **filing_overrides), matched_rules=["periodic_report:10-Q"], confidence="Moderate",
                           status=status, state_history=[StateTransition(status, AT)])


def _seed(tmp_path, *candidates: CandidateSignal) -> None:
    candidate_store.save_candidates(tmp_path, {c.id: c for c in candidates}, "edgar_candidates.json")


def _started(candidate_id: str, at: datetime):
    return build_audit_event(session_id=f"s-{at.isoformat()}", candidate_id=candidate_id, event_type="session_started", inputs={}, output_summary="", at=at.isoformat())


def _decided_packet(tmp_path, candidate_id: str, decision: str) -> str:
    evidence = EvidenceRecord("ev-1", "s", "coreweave", SourceTier.SEC_EDGAR, "SEC EDGAR", "https://www.sec.gov/x", "0001", "2026-09-10", "Item 2", "abc", AT, "high", FreshnessStatus.CURRENT)
    claim = Claim("c1", ClaimType.DIRECT_REPORTED_FACT, ClaimCategory.DIRECT_FACT, "h", "coreweave", "s", ("ev-1",), "w")
    packet = packet_store.build_packet(packet_id=f"pk-{candidate_id}-{decision}", session_id="s", candidate_id=candidate_id, issuer_id="coreweave", source_name="SEC EDGAR",
                                       saved_at=AT, proposal=ClaimProposal("s", candidate_id, (claim,), ("ev-1",)), evidence=(evidence,), evidence_text={"ev-1": "t"})
    packet_store.save_packet(tmp_path, packet)
    packet_store.record_decision(tmp_path, packet.packet_id, decision, ("r",), AT)
    return packet.packet_id


# --- is_eligible ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", [CandidateStatus.NEW_FILING_EVENT, CandidateStatus.QUEUED_FOR_PROCESSING, CandidateStatus.RETRIEVAL_IN_PROGRESS, CandidateStatus.PUBLISHED, CandidateStatus.NOT_MATERIAL])
def test_only_already_retrieved_candidates_are_eligible(tmp_path, status):
    ok, reason = worker.is_eligible(_settings(tmp_path), _candidate("c", "0001", status), now=NOW, events=())
    assert not ok and reason.startswith("status:")
    assert worker.is_eligible(_settings(tmp_path), _candidate("c", "0001", CandidateStatus.EXTRACTED), now=NOW, events=()) == (True, "eligible")


@pytest.mark.parametrize("decision", ["AUTO_PUBLISHED", "VERIFIED_DRAFT", "REVIEW_REQUIRED", "INSUFFICIENT_EVIDENCE", "NOT_MATERIAL", "DUPLICATE"])
def test_a_decided_candidate_is_never_run_again(tmp_path, decision):
    _decided_packet(tmp_path, "c", decision)
    assert worker.is_eligible(_settings(tmp_path), _candidate("c", "0001"), now=NOW, events=()) == (False, "already_decided")


def test_only_failed_retrieval_is_retryable_and_only_within_attempt_and_backoff_limits(tmp_path):
    _decided_packet(tmp_path, "c", "FAILED_RETRIEVAL")
    settings, candidate = _settings(tmp_path), _candidate("c", "0001")
    assert worker.is_eligible(settings, candidate, now=NOW, events=()) == (True, "eligible")
    assert worker.is_eligible(settings, candidate, now=NOW, events=(_started("c", NOW - timedelta(minutes=10)),)) == (False, "backoff")
    assert worker.is_eligible(settings, candidate, now=NOW, events=(_started("c", NOW - timedelta(minutes=61)),)) == (True, "eligible")
    exhausted = tuple(_started("c", NOW - timedelta(days=d)) for d in (3, 2, 1))
    assert worker.is_eligible(settings, candidate, now=NOW, events=exhausted) == (False, "max_attempts")


# --- select_candidates ----------------------------------------------------------------------

def test_select_caps_sessions_per_tick_newest_first_and_builds_a_fixed_scope(tmp_path):
    _seed(tmp_path, _candidate("c-old", "0001", rcept_dt="2026-09-01"), _candidate("c-mid", "0002", rcept_dt="2026-09-05"), _candidate("c-new", "0003", rcept_dt="2026-09-10"))
    selected, skipped = worker.select_candidates(_settings(tmp_path), now=NOW, issuers=ISSUERS, events=())
    assert [c.id for c, _ in selected] == ["c-new", "c-mid"] and skipped == []
    scope = selected[0][1]
    assert (scope.candidate_id, scope.issuer_id, scope.source_name, scope.seed_document_id) == ("c-new", "coreweave", "SEC EDGAR", "0003")
    assert scope.session_id.startswith("agent-") and scope.started_at == AT


def test_select_skips_unresolvable_issuers_and_decided_candidates(tmp_path):
    _seed(tmp_path, _candidate("c-unknown", "0001", corp_code="9999999"), _candidate("c-done", "0002"), _candidate("c-ok", "0003"))
    _decided_packet(tmp_path, "c-done", "REVIEW_REQUIRED")
    selected, skipped = worker.select_candidates(_settings(tmp_path), now=NOW, issuers=ISSUERS, events=())
    assert [c.id for c, _ in selected] == ["c-ok"]
    assert set(skipped) == {"c-unknown:issuer_unresolved", "c-done:already_decided"}


def test_select_survives_a_broken_source_store(tmp_path, monkeypatch):
    _seed(tmp_path, _candidate("c-ok", "0001"))
    original = backend_factory.get_candidate_repository

    def flaky(settings, source):
        if source == "EDINET":
            raise RuntimeError("store unavailable")
        return original(settings, source)

    monkeypatch.setattr(backend_factory, "get_candidate_repository", flaky)
    selected, skipped = worker.select_candidates(_settings(tmp_path), now=NOW, issuers=ISSUERS, events=())
    assert [c.id for c, _ in selected] == ["c-ok"] and skipped == ["EDINET:load_error:RuntimeError"]


# --- run_one_tick ------------------------------------------------------------------------------

def _outcome(candidate_id: str, packet_id: str | None, reported: str | None) -> SessionOutcome:
    return SessionOutcome("s", candidate_id, {"packet_id": packet_id}, None, (), (), packet_id, reported, {}, None)


def test_tick_reports_the_authoritative_decision_from_the_packet_store_not_the_model(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "get_all_issuers", lambda: ISSUERS)
    _seed(tmp_path, _candidate("c-1", "0001"))
    settings = _settings(tmp_path)
    # Seed the packet AFTER selection would see the candidate as undecided
    # is not possible here, so simulate the session having saved+decided
    # REVIEW_REQUIRED while the model's own report claims AUTO_PUBLISHED.
    calls = []

    async def runner(settings_, scope, *, seed_title=None):
        calls.append((scope.candidate_id, seed_title))
        packet_id = _decided_packet(tmp_path, scope.candidate_id, "REVIEW_REQUIRED")
        return _outcome(scope.candidate_id, packet_id, "AUTO_PUBLISHED")

    report = worker.run_one_tick(settings, session_runner=runner, now=NOW)
    assert calls == [("c-1", "10-Q")] and report.started == ("c-1",)
    assert report.outcomes == (("c-1", "pk-c-1-REVIEW_REQUIRED", "REVIEW_REQUIRED"),)


def test_tick_survives_a_raising_session_runner_and_is_idempotent_afterwards(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "get_all_issuers", lambda: ISSUERS)
    _seed(tmp_path, _candidate("c-1", "0001"), _candidate("c-2", "0002"))
    settings = _settings(tmp_path)

    async def runner(settings_, scope, *, seed_title=None):
        if scope.candidate_id == "c-2":
            raise RuntimeError("cli unavailable")
        _decided_packet(tmp_path, scope.candidate_id, "NOT_MATERIAL")
        return _outcome(scope.candidate_id, f"pk-{scope.candidate_id}-NOT_MATERIAL", "NOT_MATERIAL")

    report = worker.run_one_tick(settings, session_runner=runner, now=NOW)
    assert set(report.started) == {"c-1", "c-2"}
    assert ("c-2", None, "error:RuntimeError") in report.outcomes and ("c-1", "pk-c-1-NOT_MATERIAL", "NOT_MATERIAL") in report.outcomes
    # c-1 is now decided; nothing is re-run for it.
    selected, skipped = worker.select_candidates(settings, now=NOW, issuers=ISSUERS, events=())
    assert [c.id for c, _ in selected] == ["c-2"] and "c-1:already_decided" in skipped
