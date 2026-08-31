"""state_db.candidate_repository — candidate/filing-event round-trip,
idempotent upsert, optimistic-locking review updates, and state-history
append semantics. Mirrors the intent of tests/test_candidate_store.py's
existing JSON-backed assertions against the SQLite backend — a separate
file rather than a shared parametrization, since the two backends have
different call signatures (a connection vs. a cache_dir) and refactoring
the existing JSON test file to share fixtures is out of this phase's
scope. In-memory SQLite only."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.data_access.state_db import candidate_repository, connection, filing_event_repository, schema
from src.data_access.state_db.signal_repository import SqliteSignalRepository
from src.models.models import (
    CandidateSignal,
    CandidateStatus,
    EvidenceLocation,
    FilingEvent,
    FlagReason,
    LocationKind,
    StateTransition,
    Translation,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    return conn


def _edgar_filing(rcept_no: str = "0001193125-26-354029", corp_code: str = "0000002488", **overrides) -> FilingEvent:
    defaults = dict(
        rcept_no=rcept_no, corp_code=corp_code, corp_name="Advanced Micro Devices", stock_code="AMD",
        report_nm="8-K", rcept_dt="2026-08-17", flr_nm="Advanced Micro Devices", pblntf_ty="8-K",
        theme_slug="ai-buildout", subtheme_slug="compute-accelerators",
        source_url=f"https://www.sec.gov/Archives/edgar/data/{corp_code}/{rcept_no}/",
        retrieved_at=_now(), source_name="SEC EDGAR", original_language="English",
        primary_document="ex99.htm",
    )
    defaults.update(overrides)
    return FilingEvent(**defaults)


def _dart_filing(rcept_no: str = "20260819000254", **overrides) -> FilingEvent:
    defaults = dict(
        rcept_no=rcept_no, corp_code="00164779", corp_name="SK Hynix", stock_code="000660",
        report_nm="주요사항보고서", rcept_dt="20260819", flr_nm="SK하이닉스",
        theme_slug="memory", source_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
        retrieved_at=_now(), source_name="OpenDART / DART",
    )
    defaults.update(overrides)
    return FilingEvent(**defaults)


def _candidate(candidate_id: str, filing: FilingEvent, **overrides) -> CandidateSignal:
    defaults = dict(
        id=candidate_id, filing=filing, matched_rules=["material_agreement:8-K item 1.01"],
        confidence="Moderate", status=CandidateStatus.CANDIDATE_DETECTED,
        state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at=_now())],
    )
    defaults.update(overrides)
    return CandidateSignal(**defaults)


# --- 4. Candidate insert/upsert idempotency by existing candidate ID ---

def test_upsert_new_candidates_inserts_once():
    conn = _conn()
    filing = _edgar_filing()
    candidate = _candidate("edgar-cand-0001193125-26-354029", filing)
    result = candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [candidate])
    assert list(result.keys()) == ["edgar-cand-0001193125-26-354029"]


def test_upsert_new_candidates_does_not_overwrite_existing_processing_state():
    conn = _conn()
    filing = _edgar_filing()
    candidate = _candidate("edgar-cand-0001193125-26-354029", filing)
    candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [candidate])

    # A later scan re-detects the "same" filing (e.g. a repeat scan) —
    # upserting it again must never touch the already-published status.
    version = candidate_repository.get_candidate_version(conn, candidate.id)
    published = _candidate(candidate.id, filing, status=CandidateStatus.PUBLISHED, reviewed_at=_now())
    candidate_repository.update_candidate(conn, published, expected_version=version)

    candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [candidate])  # same detected-state candidate again
    reloaded = candidate_repository.get_candidate(conn, candidate.id)
    assert reloaded.status == CandidateStatus.PUBLISHED  # untouched by the repeat upsert


def test_upsert_new_candidates_is_source_scoped():
    conn = _conn()
    edgar_candidate = _candidate("edgar-cand-1", _edgar_filing("1"))
    dart_candidate = _candidate("cand-1", _dart_filing("1"))
    candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [edgar_candidate])
    candidate_repository.upsert_new_candidates(conn, "OpenDART / DART", [dart_candidate])
    assert set(candidate_repository.load_candidates(conn, "SEC EDGAR").keys()) == {"edgar-cand-1"}
    assert set(candidate_repository.load_candidates(conn, "OpenDART / DART").keys()) == {"cand-1"}


# --- 5. Filing-event source-aware dedup semantics (through the candidate path) ---

def test_two_candidates_same_accession_different_source_do_not_collide():
    # A pathological but real structural check: (corp_code, rcept_no)
    # alone must never be treated as globally unique — source_name is
    # part of the identity.
    conn = _conn()
    edgar_candidate = _candidate("edgar-cand-X", _edgar_filing(rcept_no="X", corp_code="0000000009", source_name="SEC EDGAR"))
    dart_candidate = _candidate("cand-X", _dart_filing(rcept_no="X", corp_code="0000000009"))
    candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [edgar_candidate])
    candidate_repository.upsert_new_candidates(conn, "OpenDART / DART", [dart_candidate])
    assert candidate_repository.get_candidate(conn, "edgar-cand-X").filing.source_name == "SEC EDGAR"
    assert candidate_repository.get_candidate(conn, "cand-X").filing.source_name == "OpenDART / DART"


# --- 7. Full round-trip, including translations, excerpts, and materiality ---

def test_round_trips_full_processing_state_including_translations_and_history():
    conn = _conn()
    filing = _dart_filing()
    candidate = _candidate(
        "cand-20260819000254", filing,
        confidence="High", status=CandidateStatus.PUBLISHED,
        excerpt_original="자기주식취득 결정...", reviewed_at=_now(), reviewed_note="Approved — real buyback.",
        title_translation=Translation(
            translated_text="Decision on treasury stock acquisition", provider="DeepL",
            source_lang="ko", target_lang="en", translated_at=_now(),
        ),
        excerpt_translation=Translation(
            translated_text="Decision to acquire treasury stock...", provider="DeepL",
            source_lang="ko", target_lang="en", translated_at=_now(), model="deepl-pro",
        ),
        materiality_assessment="Material — real, large buyback.",
        state_history=[
            StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at=_now()),
            StateTransition(status=CandidateStatus.EXTRACTED, at=_now(), detail="extraction ok"),
            StateTransition(status=CandidateStatus.PUBLISHED, at=_now(), detail="approved"),
        ],
    )
    candidate_repository.upsert_new_candidates(conn, "OpenDART / DART", [candidate])
    reloaded = candidate_repository.get_candidate(conn, candidate.id)

    assert reloaded.filing == filing
    assert reloaded.confidence == "High"
    assert reloaded.status == CandidateStatus.PUBLISHED
    assert reloaded.excerpt_original == candidate.excerpt_original
    assert reloaded.reviewed_note == candidate.reviewed_note
    assert reloaded.title_translation == candidate.title_translation
    assert reloaded.excerpt_translation == candidate.excerpt_translation
    assert reloaded.materiality_assessment == candidate.materiality_assessment
    assert [t.status for t in reloaded.state_history] == [
        CandidateStatus.CANDIDATE_DETECTED, CandidateStatus.EXTRACTED, CandidateStatus.PUBLISHED,
    ]


# --- Evidence-packet foundation, Phase 1: new optional field round-trip ---


def test_round_trips_evidence_packet_phase1_fields():
    conn = _conn()
    filing = _edgar_filing(filed_at=None)  # EDGAR: no time component available, stays None
    candidate = _candidate(
        "edgar-cand-phase1", filing, status=CandidateStatus.NEEDS_REVIEW,
        excerpt_original="First extraction.", excerpt_supplemental="Second, different extraction.",
        excerpt_retrieved_at="2026-08-20T00:00:00+00:00",
        flag_reason=FlagReason(
            category="financing_or_debt", matched_terms=("financing_or_debt:8-K item 2.03",),
            score_inputs=("confidence=Moderate", "category_matches=1"),
            human_readable_reason="Matched 1 detection rule(s) in category 'financing_or_debt'.",
            source_detail="REVIEW: fallback [rules: edgar.fallback.review]",
        ),
        evidence_location=EvidenceLocation(kind=LocationKind.SECTION, section="Item 2.03"),
    )
    candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [candidate])
    reloaded = candidate_repository.get_candidate(conn, candidate.id)

    assert reloaded.excerpt_original == "First extraction."
    assert reloaded.excerpt_supplemental == "Second, different extraction."
    assert reloaded.excerpt_retrieved_at == "2026-08-20T00:00:00+00:00"
    assert reloaded.flag_reason == candidate.flag_reason
    assert reloaded.evidence_location == candidate.evidence_location
    assert reloaded.filing.filed_at is None


def test_round_trips_filing_event_filed_at_for_edinet():
    conn = _conn()
    filing = _edgar_filing(
        rcept_no="S100AAAA", corp_code="E02778", corp_name="SoftBank Group", source_name="EDINET",
        original_language="Japanese", filed_at="2026-08-17T09:00:00",
    )
    candidate = _candidate("edinet-cand-phase1", filing)
    candidate_repository.upsert_new_candidates(conn, "EDINET", [candidate])
    reloaded = candidate_repository.get_candidate(conn, candidate.id)
    assert reloaded.filing.filed_at == "2026-08-17T09:00:00"


def test_pre_phase1_row_missing_new_columns_still_loads_with_none_defaults():
    """Simulates a candidate row written before this migration existed —
    inserted with the exact pre-Phase-1 column list — then confirms the
    v3 ALTER TABLE migration (already applied by _conn()) makes the new
    columns readable as NULL/None rather than raising, matching the
    additive/backward-compatible migration contract."""
    conn = _conn()
    filing = _edgar_filing(rcept_no="pre-phase1-1")
    filing_event_repository.upsert_filing_event(conn, filing)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO candidates (
            id, source, filing_corp_code, filing_rcept_no, matched_rules_json, confidence, status,
            extraction_state, translation_state, excerpt_quality, excerpt_original,
            title_translation_json, excerpt_translation_json, reviewed_at, reviewed_note,
            materiality_assessment, version, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            "cand-pre-phase1", filing.source_name, filing.corp_code, filing.rcept_no,
            "[]", "Moderate", "Needs review", "Extracted", "Not requested", "Unknown",
            "Pre-existing excerpt.", None, None, None, "", "Not assessed", now, now,
        ),
    )
    conn.commit()

    reloaded = candidate_repository.get_candidate(conn, "cand-pre-phase1")
    assert reloaded is not None
    assert reloaded.excerpt_original == "Pre-existing excerpt."
    assert reloaded.excerpt_supplemental is None
    assert reloaded.excerpt_retrieved_at is None
    assert reloaded.flag_reason is None
    assert reloaded.evidence_location is None
    assert reloaded.evidence_source_member is None  # Phase 2, Step 2's v4 column — also predates this row


# --- Evidence-packet foundation, Phase 2, Step 2: evidence_source_member ---


def test_round_trips_evidence_source_member_phase2_step2():
    conn = _conn()
    filing = _edgar_filing(
        rcept_no="S100BBBB", corp_code="E02778", corp_name="SoftBank Group", source_name="EDINET",
        original_language="Japanese",
    )
    candidate = _candidate(
        "edinet-cand-phase2-step2", filing, status=CandidateStatus.NEEDS_REVIEW,
        excerpt_original="ZIP-sourced evidence text.", excerpt_retrieved_at="2026-08-20T00:00:00+00:00",
        evidence_source_member="PublicDoc/0101.pdf",
    )
    candidate_repository.upsert_new_candidates(conn, "EDINET", [candidate])
    reloaded = candidate_repository.get_candidate(conn, candidate.id)

    assert reloaded.evidence_source_member == "PublicDoc/0101.pdf"
    assert reloaded.excerpt_original == "ZIP-sourced evidence text."  # untouched by the new field


def test_evidence_source_member_stays_none_when_not_set():
    conn = _conn()
    filing = _edgar_filing(rcept_no="0001193125-26-999999")
    candidate = _candidate("edgar-cand-no-member", filing, excerpt_original="Bare EDGAR excerpt.")
    candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [candidate])
    reloaded = candidate_repository.get_candidate(conn, candidate.id)
    assert reloaded.evidence_source_member is None


# --- 8. State transitions append and load in expected order ---

def test_state_transitions_load_in_append_order():
    conn = _conn()
    filing = _edgar_filing()
    candidate = _candidate(
        "edgar-cand-order-test", filing,
        state_history=[
            StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="2026-08-17T00:00:00+00:00"),
            StateTransition(status=CandidateStatus.EXTRACTED, at="2026-08-17T01:00:00+00:00"),
        ],
    )
    candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [candidate])
    version = candidate_repository.get_candidate_version(conn, candidate.id)

    updated = _candidate(
        candidate.id, filing, status=CandidateStatus.PUBLISHED,
        state_history=candidate.state_history + [
            StateTransition(status=CandidateStatus.PUBLISHED, at="2026-08-17T02:00:00+00:00", detail="published"),
        ],
    )
    outcome = candidate_repository.update_candidate(conn, updated, expected_version=version)
    assert outcome.status == "updated"
    history = outcome.current.state_history
    assert [t.at for t in history] == ["2026-08-17T00:00:00+00:00", "2026-08-17T01:00:00+00:00", "2026-08-17T02:00:00+00:00"]


def test_updating_a_candidate_never_duplicates_already_stored_transitions():
    conn = _conn()
    filing = _edgar_filing()
    candidate = _candidate("edgar-cand-no-dup", filing)
    candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [candidate])
    version = candidate_repository.get_candidate_version(conn, candidate.id)

    # Caller resubmits the SAME state_history it already had, unchanged.
    same_history_update = _candidate(candidate.id, filing, status=CandidateStatus.CANDIDATE_DETECTED, state_history=candidate.state_history)
    outcome = candidate_repository.update_candidate(conn, same_history_update, expected_version=version)
    assert len(outcome.current.state_history) == 1  # not duplicated


# --- 11. Optimistic-lock conflict rejects the stale update, preserves the newer record ---

def test_stale_expected_version_is_rejected_and_newer_record_preserved():
    conn = _conn()
    filing = _edgar_filing()
    candidate = _candidate("edgar-cand-race", filing)
    candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [candidate])
    v1 = candidate_repository.get_candidate_version(conn, candidate.id)

    # First reviewer publishes.
    published = _candidate(candidate.id, filing, status=CandidateStatus.PUBLISHED, reviewed_note="First reviewer")
    first = candidate_repository.update_candidate(conn, published, expected_version=v1)
    assert first.status == "updated"

    # A second writer, still holding the STALE v1 it read before the
    # first update landed, tries to write a conflicting decision.
    stale_dismiss = _candidate(candidate.id, filing, status=CandidateStatus.DISMISSED, reviewed_note="Second reviewer — stale")
    second = candidate_repository.update_candidate(conn, stale_dismiss, expected_version=v1)

    assert second.status == "conflict"
    assert second.current.status == CandidateStatus.PUBLISHED  # the first reviewer's decision, untouched
    assert second.current.reviewed_note == "First reviewer"

    on_disk = candidate_repository.get_candidate(conn, candidate.id)
    assert on_disk.status == CandidateStatus.PUBLISHED
    assert on_disk.reviewed_note == "First reviewer"


def test_update_of_a_nonexistent_candidate_is_reported_not_found():
    conn = _conn()
    ghost = _candidate("edgar-cand-ghost", _edgar_filing(rcept_no="ghost", corp_code="0000000099"))
    outcome = candidate_repository.update_candidate(conn, ghost, expected_version=1)
    assert outcome.status == "not_found"
    assert outcome.current is None


def test_correct_version_update_succeeds_and_increments_version():
    conn = _conn()
    filing = _edgar_filing()
    candidate = _candidate("edgar-cand-v", filing)
    candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [candidate])
    v1 = candidate_repository.get_candidate_version(conn, candidate.id)
    assert v1 == 1

    published = _candidate(candidate.id, filing, status=CandidateStatus.PUBLISHED)
    outcome = candidate_repository.update_candidate(conn, published, expected_version=v1)
    assert outcome.status == "updated"
    assert candidate_repository.get_candidate_version(conn, candidate.id) == 2


# --- Durable-State Phase 3B-0: batch-atomicity repair for
# upsert_new_candidates() (see filing_event_repository._upsert_filing_
# event_no_transaction and this module's own upsert_new_candidates
# docstring). Confirmed by execution, not just static reading, that the
# pre-fix nested transaction(conn) call inside the per-candidate loop
# caused an earlier iteration's pending writes to commit early, so a
# later failure in the same batch left partial rows — including a
# filing_events row with no corresponding candidate. All tests below use
# only in-memory SQLite and synthetic fixtures. ---

def _rig_mid_batch_candidate_failure(monkeypatch, fail_on_call: int = 2):
    """Forces _insert_candidate to raise RuntimeError on the Nth call
    within a batch, real behavior otherwise. Returns nothing — installs
    the monkeypatch directly."""
    real_insert = candidate_repository._insert_candidate
    call_count = {"n": 0}

    def _boom(conn_arg, candidate, now):
        call_count["n"] += 1
        if call_count["n"] == fail_on_call:
            raise RuntimeError("synthetic mid-batch failure")
        return real_insert(conn_arg, candidate, now)

    monkeypatch.setattr(candidate_repository, "_insert_candidate", _boom)


def test_successful_two_candidate_batch_persists_both_together(monkeypatch):
    conn = _conn()
    filing_a = _edgar_filing(rcept_no="AAA", corp_code="0000000001")
    filing_b = _edgar_filing(rcept_no="BBB", corp_code="0000000002")
    candidate_a = _candidate("edgar-cand-AAA", filing_a)
    candidate_b = _candidate("edgar-cand-BBB", filing_b)

    result = candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [candidate_a, candidate_b])

    assert set(result.keys()) == {"edgar-cand-AAA", "edgar-cand-BBB"}
    assert candidate_repository.get_candidate(conn, "edgar-cand-AAA") is not None
    assert candidate_repository.get_candidate(conn, "edgar-cand-BBB") is not None
    assert len(filing_event_repository.load_filing_events(conn, "SEC EDGAR")) == 2


def test_mid_batch_candidate_failure_leaves_zero_partial_rows(monkeypatch):
    conn = _conn()
    filing_a = _edgar_filing(rcept_no="AAA", corp_code="0000000001")
    filing_b = _edgar_filing(rcept_no="BBB", corp_code="0000000002")
    candidate_a = _candidate("edgar-cand-AAA", filing_a)
    candidate_b = _candidate("edgar-cand-BBB", filing_b)
    _rig_mid_batch_candidate_failure(monkeypatch, fail_on_call=2)

    with pytest.raises(RuntimeError, match="synthetic mid-batch failure"):
        candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [candidate_a, candidate_b])

    # Confirmed by execution: before the repair, this left 1 candidates
    # row, 2 filing_events rows (one orphaned), and 1 state_transitions
    # row. After the repair, the whole batch — including candidate A,
    # which was processed successfully before candidate B's failure —
    # rolls back completely.
    assert conn.execute("SELECT COUNT(*) AS n FROM candidates").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM filing_events").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM state_transitions").fetchone()["n"] == 0


def test_preexisting_rows_survive_a_later_failed_batch(monkeypatch):
    conn = _conn()
    pre_filing = _edgar_filing(rcept_no="PRE", corp_code="0000000099")
    pre_candidate = _candidate("edgar-cand-PRE", pre_filing)
    candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [pre_candidate])

    filing_a = _edgar_filing(rcept_no="AAA", corp_code="0000000001")
    filing_b = _edgar_filing(rcept_no="BBB", corp_code="0000000002")
    candidate_a = _candidate("edgar-cand-AAA", filing_a)
    candidate_b = _candidate("edgar-cand-BBB", filing_b)
    _rig_mid_batch_candidate_failure(monkeypatch, fail_on_call=2)

    with pytest.raises(RuntimeError, match="synthetic mid-batch failure"):
        candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [candidate_a, candidate_b])

    # The pre-existing row from the earlier, separate, successful call
    # is untouched; the later failed batch contributed nothing at all.
    assert candidate_repository.get_candidate(conn, "edgar-cand-PRE") is not None
    assert conn.execute("SELECT COUNT(*) AS n FROM candidates").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM filing_events").fetchone()["n"] == 1
    assert conn.execute("SELECT rcept_no FROM filing_events").fetchone()["rcept_no"] == "PRE"


def test_standalone_filing_event_upsert_still_commits_atomically_and_independently():
    conn = _conn()
    filing = _edgar_filing(rcept_no="STANDALONE", corp_code="0000000001")

    inserted = filing_event_repository.upsert_filing_event(conn, filing)

    assert inserted is True
    assert filing_event_repository.filing_event_exists(conn, "SEC EDGAR", "0000000001", "STANDALONE")
    # A second call for the same identity is a true no-op, not a duplicate.
    assert filing_event_repository.upsert_filing_event(conn, filing) is False
    assert len(filing_event_repository.load_filing_events(conn, "SEC EDGAR")) == 1


def test_identical_batch_rerun_does_not_duplicate_rows():
    conn = _conn()
    filing_a = _edgar_filing(rcept_no="AAA", corp_code="0000000001")
    candidate_a = _candidate("edgar-cand-AAA", filing_a)

    candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [candidate_a])
    candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [candidate_a])  # identical re-run

    assert conn.execute("SELECT COUNT(*) AS n FROM candidates").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM filing_events").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM state_transitions").fetchone()["n"] == 1


def test_batch_upsert_opens_exactly_one_transaction_for_the_whole_batch(monkeypatch):
    """No hidden commit: the per-candidate filing-event helper invoked by
    upsert_new_candidates() must never open its own transaction context —
    proven two ways: (1) exactly one transaction() context is entered for
    a whole multi-candidate batch, and (2) the helper's own bytecode
    contains no reference to `transaction` at all."""
    conn = _conn()
    filing_a = _edgar_filing(rcept_no="AAA", corp_code="0000000001")
    filing_b = _edgar_filing(rcept_no="BBB", corp_code="0000000002")
    candidate_a = _candidate("edgar-cand-AAA", filing_a)
    candidate_b = _candidate("edgar-cand-BBB", filing_b)

    real_transaction = candidate_repository.transaction
    call_count = {"n": 0}

    @contextmanager
    def _counting_transaction(conn_arg):
        call_count["n"] += 1
        with real_transaction(conn_arg) as c:
            yield c

    monkeypatch.setattr(candidate_repository, "transaction", _counting_transaction)

    candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [candidate_a, candidate_b])

    assert call_count["n"] == 1
    assert "transaction" not in filing_event_repository._upsert_filing_event_no_transaction.__code__.co_names


def test_candidate_from_repaired_batch_can_be_published_and_derives_expected_signal():
    conn = _conn()
    filing = _edgar_filing(rcept_no="SIGTEST", corp_code="0000000001")
    candidate = _candidate("edgar-cand-SIGTEST", filing)
    candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [candidate])
    version = candidate_repository.get_candidate_version(conn, candidate.id)

    published = _candidate(candidate.id, filing, status=CandidateStatus.PUBLISHED, reviewed_at=_now(), reviewed_note="ok")
    outcome = candidate_repository.update_candidate(conn, published, expected_version=version)
    assert outcome.status == "updated"

    signal_repo = SqliteSignalRepository(conn)
    assert [s.id for s in signal_repo.get_all_signals()] == ["signal-edgar-cand-SIGTEST"]


def test_non_published_candidate_from_repaired_batch_yields_no_signal():
    conn = _conn()
    filing = _edgar_filing(rcept_no="NOSIG", corp_code="0000000001")
    candidate = _candidate("edgar-cand-NOSIG", filing)
    candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [candidate])

    signal_repo = SqliteSignalRepository(conn)
    assert signal_repo.get_all_signals() == []


# --- Source guard: this phase's modified/new files must never reference
# real local state (same pattern established in Phase 2A/2B/3A). ---

_PHASE3B0_FILES = (
    Path(__file__).resolve(),
    Path(__file__).resolve().parent.parent / "src" / "data_access" / "state_db" / "candidate_repository.py",
    Path(__file__).resolve().parent.parent / "src" / "data_access" / "state_db" / "filing_event_repository.py",
    Path(__file__).resolve().parent.parent / "design" / "DECISIONS.md",
)
_FORBIDDEN_REAL_STATE_REFERENCES = (
    "data/cache",
    "data/edge_research.db",
    ".streamlit/secrets.toml",
    "signal-cand-20260819000254",
    "signal-edgar-cand-0001193125-26-354029",
    "signal-edgar-cand-0001193125-26-356217",
)


def _source_excluding_this_guards_own_string_list(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if path.name == "test_state_db_candidate_repository.py":
        start = text.index("_FORBIDDEN_REAL_STATE_REFERENCES = (")
        end = text.index(")\n", start) + len(")\n")
        return text[:start] + text[end:]
    if path.name == "DECISIONS.md":
        marker = "## Durable-State Phase 3B-0"
        if marker not in text:
            return ""
        return text[text.index(marker):]
    return text


def test_phase3b0_files_never_reference_real_local_state_or_real_signal_ids():
    offenders = []
    for path in _PHASE3B0_FILES:
        if not path.exists():
            continue
        source = _source_excluding_this_guards_own_string_list(path)
        for forbidden in _FORBIDDEN_REAL_STATE_REFERENCES:
            if forbidden in source:
                offenders.append(f"{path.name}: contains {forbidden!r}")
    assert not offenders, offenders
