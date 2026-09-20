"""Agent modes, and the property the whole release rests on: in shadow
mode nothing outside the agent tables changes.

The no-write tests are deliberately empirical. They hash every
non-agent table before and after a full evaluation is persisted and
require the hashes to be identical — so they would catch a write made
through a path no source-level assertion knows about.

No model session, credential, network call or live agent is involved."""
from __future__ import annotations

import hashlib
import sqlite3

import pytest

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.state_db import agent_repository as sqlite_agent
from src.data_access.state_db import connection, schema
from src.logic import agent_mode, agent_persistence
from src.logic.agent_write_guard import (
    AGENT_TABLE_PREFIX,
    ShadowWriteViolation,
    assert_write_allowed,
    forbid_non_agent_writes,
    is_agent_table,
)
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
from src.models.agent_records import EFFECTIVE_NO_ACTION, AgentControl

AT = "2026-09-20T00:00:00+00:00"


def _settings(mode: str | None = None, *, kill: bool = False) -> Settings:
    extra = {} if mode is None else {"research_agent_mode": mode}
    return Settings(research_agent_publication_kill_switch_enabled=kill, **extra)


# --- resolution: every input may restrict, none may widen ----------------------

@pytest.mark.parametrize("configured,expected", [
    ("off", "off"), ("OFF", "off"), (" shadow ", "shadow"), ("shadow", "shadow"),
])
def test_a_recognized_mode_resolves_to_itself(configured, expected):
    assert agent_mode.resolve_mode(_settings(configured)).mode == expected


@pytest.mark.parametrize("configured", ["", "  ", "on", "enabled", "true", "1", "Shadow-mode", "publsh", "live"])
def test_anything_unrecognized_fails_closed_to_off(configured):
    resolved = agent_mode.resolve_mode(_settings(configured))
    assert resolved.mode == "off" and agent_mode.ENV_VAR in resolved.reason


def test_an_unset_mode_is_off():
    resolved = agent_mode.resolve_mode(_settings())
    assert resolved.mode == "off"


def test_publish_is_unreachable_in_this_release_however_it_is_configured():
    """Publishing ships with E6/E7/E8 and the V25 migration. Until then
    the ceiling holds even against an operator who sets it deliberately."""
    resolved = agent_mode.resolve_mode(_settings("publish"))
    assert resolved.mode == agent_mode.RELEASE_MAX_MODE == "shadow"
    assert "not part of this release" in resolved.reason
    assert resolved.may_write_outside_agent_tables is False


def test_the_database_override_can_narrow_the_environment():
    control = AgentControl(mode_override="off", updated_at=AT, reason="paging on duplicate packets")
    resolved = agent_mode.resolve_mode(_settings("shadow"), control)
    assert resolved.mode == "off"
    assert "paging on duplicate packets" in resolved.reason


def test_the_database_override_can_never_widen_the_environment():
    """The one control in this release is restrictive by construction: no
    row in a table can turn the agent on."""
    control = AgentControl(mode_override="publish", updated_at=AT, reason="please start")
    resolved = agent_mode.resolve_mode(_settings("off"), control)
    assert resolved.mode == "off" and "ignored" in resolved.reason


def test_an_override_cannot_lift_a_shadow_run_to_publishing():
    control = AgentControl(mode_override="publish", updated_at=AT, reason="")
    assert agent_mode.resolve_mode(_settings("shadow"), control).mode == "shadow"


def test_no_control_row_leaves_the_environment_mode_intact():
    assert agent_mode.resolve_mode(_settings("shadow"), None).mode == "shadow"


# --- the kill switch is a separate fact, not folded into the mode ---------------

def test_the_kill_switch_is_recorded_beside_the_mode_not_inside_it():
    resolved = agent_mode.resolve_mode(_settings("shadow", kill=True))
    assert (resolved.mode, resolved.kill_switch_on) == ("shadow", True)


def test_no_mode_in_this_release_may_write_outside_the_agent_tables():
    for mode in ("off", "shadow", "publish"):
        for kill in (True, False):
            assert agent_mode.resolve_mode(_settings(mode, kill=kill)).may_write_outside_agent_tables is False


# --- the guard ------------------------------------------------------------------

@pytest.mark.parametrize("table", [
    "agent_runs", "agent_jobs", "agent_packets", "agent_evidence", "agent_decisions",
    "agent_audit_events", "agent_control",
])
def test_the_agent_tables_are_writable_in_shadow_mode(table):
    assert is_agent_table(table) and table.startswith(AGENT_TABLE_PREFIX)
    assert_write_allowed(table, resolved=agent_mode.resolve_mode(_settings("shadow")))


@pytest.mark.parametrize("table", [
    "candidates", "candidate_status", "state_transitions", "verified_updates", "signals",
    "signal_inputs", "filing_events", "research_cases", "user_preferences", "companies",
])
def test_every_existing_entity_is_refused_in_shadow_mode(table):
    resolved = agent_mode.resolve_mode(_settings("shadow"))
    with pytest.raises(ShadowWriteViolation) as excinfo:
        assert_write_allowed(table, resolved=resolved)
    assert table in str(excinfo.value) and "mode=shadow" in str(excinfo.value)


def test_a_table_merely_containing_the_prefix_is_not_an_agent_table():
    with pytest.raises(ShadowWriteViolation):
        assert_write_allowed("legacy_agent_notes", resolved=agent_mode.resolve_mode(_settings("shadow")))


# --- the empirical invariant ------------------------------------------------------

def _non_agent_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows if not is_agent_table(r[0])]


def _fingerprint(conn: sqlite3.Connection) -> dict[str, str]:
    """A hash of every row of every non-agent table — the thing that must
    not move. Row order is normalized so the hash reflects content only."""
    out = {}
    for table in _non_agent_tables(conn):
        rows = sorted(repr(tuple(r)) for r in conn.execute(f'SELECT * FROM "{table}"').fetchall())
        out[table] = hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()
    return out


def _seed_existing_data(conn: sqlite3.Connection) -> None:
    """Real rows in the tables the agent must not touch. An empty database
    would make the invariant vacuous: every hash would be the hash of
    nothing, and a deletion would be indistinguishable from a no-op."""
    conn.execute(
        "INSERT INTO filing_events (source_name, corp_code, rcept_no, corp_name, stock_code, report_nm, rcept_dt, pblntf_ty, flr_nm, retrieved_at) "
        "VALUES ('SEC EDGAR', '0001640147', 'acc-1', 'NVIDIA', 'NVDA', '10-Q', '20260901', 'A', 'NVIDIA', '2026-09-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO candidates (id, source, filing_corp_code, filing_rcept_no, confidence, status, extraction_state, "
        "translation_state, excerpt_quality, excerpt_original, reviewed_at, published_by, version, created_at, updated_at) "
        "VALUES ('cand-1', 'SEC EDGAR', '0001640147', 'acc-1', 'High', 'NEEDS_REVIEW', 'EXTRACTED', 'NOT_REQUIRED', "
        "'GOOD', 'Revenue rose to $30.0 billion.', NULL, NULL, 1, '2026-09-01T00:00:00+00:00', '2026-09-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO state_transitions (candidate_id, status, at, detail) "
        "VALUES ('cand-1', 'EXTRACTED', '2026-09-01T00:00:00+00:00', 'radar pipeline')"
    )
    conn.execute(
        "INSERT INTO user_preferences (email, theme_preference, updated_at) "
        "VALUES ('someone@example.test', 'dark', '2026-09-01T00:00:00+00:00')"
    )
    conn.commit()


@pytest.fixture
def db():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    _seed_existing_data(conn)
    return conn


def _persist_a_full_evaluation(store, *, mode: str, kill_switch_on: bool, packet_id: str = "pkt-1") -> None:
    evidence = EvidenceRecord(
        evidence_id="ev-1", session_id="sess-1", issuer_id="NVDA", source_tier=SourceTier.ISSUER_IR,
        source_name="NVIDIA IR", source_url="https://example.test/a", source_document_id="doc-1",
        source_date="2026-09-01", excerpt_or_locator="Revenue rose to $30.0 billion.", excerpt_sha256="sha-1",
        retrieved_at="2026-09-20T00:00:00+00:00", confidence="High", freshness_status=FreshnessStatus.CURRENT,
    )
    claim = Claim(
        claim_id="c-1", issuer_id="NVDA", claim_type=ClaimType.DIRECT_REPORTED_FACT,
        claim_category=ClaimCategory.DIRECT_FACT, headline="Revenue rose to $30.0 billion",
        statement="Revenue rose to $30.0 billion.", what_this_does_not_establish="It does not establish demand.",
        evidence_ids=("ev-1",), factual_context_evidence_ids=(),
    )
    proposal = ClaimProposal(session_id="sess-1", candidate_id="cand-1", claims=(claim,), retrieved_evidence_ids=("ev-1",))
    policy = PolicyDecision(
        decision=PublicationDecision.AUTO_PUBLISHED, reasons=("all_rows_passed",),
        row_results=(RowResult(row=11, condition="every claim is supported by the stored excerpt", passed=True),),
        content_hash="hash-1",
    )
    packet = agent_persistence.build_packet(
        packet_id=packet_id, session_id="sess-1", candidate_id="cand-1", source="SEC EDGAR",
        seed_document_id="acc-1", proposal=proposal, content_hash="hash-1",
        created_at="2026-09-20T00:01:00+00:00", issuer_id="NVDA", evidence=(evidence,),
    )
    decision = agent_persistence.build_decision(
        packet_id=packet_id, policy=policy, mode=mode, kill_switch_on=kill_switch_on,
        quote_verified=True, decided_at="2026-09-20T00:02:00+00:00",
    )
    agent_persistence.persist_evaluation(store, packet=packet, decision=decision)


def test_a_shadow_evaluation_leaves_every_non_agent_table_byte_identical(db):
    store = backend_factory._AgentStoreRepository(conn=db, module=sqlite_agent)
    before = _fingerprint(db)
    empty = hashlib.sha256(b"").hexdigest()
    assert [t for t, h in before.items() if h != empty] == [
        "candidates", "filing_events", "schema_version", "state_transitions", "user_preferences",
    ], "the seeded rows must be in the fingerprint, or this proves nothing"
    _persist_a_full_evaluation(store, mode="shadow", kill_switch_on=False)
    assert _fingerprint(db) == before
    # ...and the agent's own tables did receive the work.
    assert store.get_packet("pkt-1") is not None and store.get_decision("pkt-1") is not None


def test_the_same_holds_with_the_kill_switch_on(db):
    store = backend_factory._AgentStoreRepository(conn=db, module=sqlite_agent)
    before = _fingerprint(db)
    _persist_a_full_evaluation(store, mode="shadow", kill_switch_on=True)
    assert _fingerprint(db) == before


def test_the_whole_persist_path_runs_under_a_connection_that_denies_other_writes(db):
    """Statement-level proof: the authorizer would reject a write to any
    non-agent table, and persisting an evaluation never attempts one."""
    store = backend_factory._AgentStoreRepository(conn=db, module=sqlite_agent)
    with forbid_non_agent_writes(db):
        _persist_a_full_evaluation(store, mode="shadow", kill_switch_on=False)
    assert store.get_decision("pkt-1").effective_decision == EFFECTIVE_NO_ACTION


@pytest.mark.parametrize("statement", [
    "DELETE FROM candidates",
    "UPDATE candidates SET status = 'PUBLISHED'",
    "UPDATE candidates SET reviewed_at = '2026-09-20T00:00:00+00:00'",
    "UPDATE candidates SET published_by = 'autonomous_agent'",
    "INSERT INTO state_transitions (candidate_id, status, at) VALUES ('cand-1', 'PUBLISHED', 'now')",
    "UPDATE user_preferences SET theme_preference = 'light'",
])
def test_the_denying_connection_really_does_deny(db, statement):
    """Guards that never fire are worth nothing, so prove this one bites —
    on exactly the writes the release forbids by name."""
    with forbid_non_agent_writes(db):
        with pytest.raises(sqlite3.DatabaseError):
            db.execute(statement)
        db.execute("DELETE FROM agent_audit_events")  # an agent table still works
    assert db.execute("SELECT status, reviewed_at, published_by FROM candidates").fetchone()[:3] == (
        "NEEDS_REVIEW", None, None,
    )


def test_the_guard_is_lifted_when_the_block_ends(db):
    with forbid_non_agent_writes(db):
        pass
    db.execute("DELETE FROM state_transitions")  # must not raise


# --- the recorded representation --------------------------------------------------

def test_shadow_records_no_action_never_review_required(db):
    """REVIEW_REQUIRED is a policy outcome about the content. Shadow mode
    is the absence of an action, and conflating them would make the review
    page unable to tell 'the matrix withheld this' from 'the agent was
    not allowed to act'."""
    store = backend_factory._AgentStoreRepository(conn=db, module=sqlite_agent)
    _persist_a_full_evaluation(store, mode="shadow", kill_switch_on=False)
    decision = store.get_decision("pkt-1")
    assert decision.effective_decision == EFFECTIVE_NO_ACTION != "REVIEW_REQUIRED"
    assert decision.policy_decision == "AUTO_PUBLISHED"


def test_both_holds_together_keep_the_complete_context(db):
    store = backend_factory._AgentStoreRepository(conn=db, module=sqlite_agent)
    _persist_a_full_evaluation(store, mode="shadow", kill_switch_on=True)
    decision = store.get_decision("pkt-1")
    assert decision.blocked_by == "kill_switch"
    assert decision.effective_decision == EFFECTIVE_NO_ACTION
    assert decision.mode == "shadow"
    assert decision.kill_switch_on is True
    assert decision.policy_decision == "AUTO_PUBLISHED"
    assert (decision.candidate_status_written, decision.published) == (False, False)
