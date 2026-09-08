"""candidate_backfill — pure/local logic over tmp_path only, zero network
dependency anywhere in the tested path (confirmed by test 7)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.data_access.dart import candidate_store
from src.logic.candidate_backfill import (
    BackfillRecord,
    CandidateBackfillError,
    backfill_candidates,
)
from src.models.models import CandidateSignal, CandidateStatus, FilingEvent, StateTransition


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dart_filing(rcept_no: str, **overrides) -> FilingEvent:
    defaults = dict(
        rcept_no=rcept_no, corp_code="00164779", corp_name="SK Hynix", stock_code="000660",
        report_nm="주요사항보고서(자기주식취득결정)", rcept_dt="20260819", flr_nm="SK하이닉스",
        theme_slug="memory", subtheme_slug="dram", source_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
        retrieved_at=_now_iso(), source_name="OpenDART / DART",
    )
    defaults.update(overrides)
    return FilingEvent(**defaults)


def _edgar_filing(rcept_no: str, corp_code: str = "0000002488", **overrides) -> FilingEvent:
    defaults = dict(
        rcept_no=rcept_no, corp_code=corp_code, corp_name="Advanced Micro Devices", stock_code="AMD",
        report_nm="8-K", rcept_dt="2026-08-17", flr_nm="Advanced Micro Devices", pblntf_ty="8-K",
        theme_slug="ai-buildout", subtheme_slug="compute-accelerators",
        source_url=f"https://www.sec.gov/Archives/edgar/data/{corp_code}/{rcept_no.replace('-', '')}/",
        retrieved_at=_now_iso(), source_name="SEC EDGAR", original_language="English",
    )
    defaults.update(overrides)
    return FilingEvent(**defaults)


def _bare_candidate(candidate_id: str, filing: FilingEvent, matched_rules: list[str], confidence: str = "Moderate") -> CandidateSignal:
    return CandidateSignal(
        id=candidate_id, filing=filing, matched_rules=matched_rules, confidence=confidence,
        status=CandidateStatus.CANDIDATE_DETECTED,
        state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at=_now_iso())],
    )


def _dart_record(rcept_no: str = "20260819000254") -> BackfillRecord:
    filing = _dart_filing(rcept_no)
    candidate = _bare_candidate(f"cand-{rcept_no}", filing, ["financing:capital_raise_or_treasury_stock:자기주식취득"])
    return BackfillRecord(source="OpenDART / DART", candidate=candidate)


def _edgar_record(rcept_no: str, corp_code: str = "0000002488") -> BackfillRecord:
    filing = _edgar_filing(rcept_no, corp_code)
    candidate = _bare_candidate(f"edgar-cand-{rcept_no}", filing, ["material_agreement:8-K item 1.01"])
    return BackfillRecord(source="SEC EDGAR", candidate=candidate)


def _four_edgar_records() -> list[BackfillRecord]:
    return [
        _edgar_record("0001193125-26-354029", "0000002488"),
        _edgar_record("0001193125-26-356217", "0001835632"),
        _edgar_record("0001437749-26-028480", "0000732026"),
        _edgar_record("0001753926-26-001459", "0001819994"),
    ]


# --- 1. Successful mixed-source import (the real shape) ---

def test_successful_mixed_source_import(tmp_path):
    records = [_dart_record()] + _four_edgar_records()

    result = backfill_candidates(tmp_path, records)

    assert set(result.created_candidate_ids) == {r.candidate.id for r in records}
    assert result.already_present_candidate_ids == ()

    dart_candidates = candidate_store.load_candidates(tmp_path)
    assert "cand-20260819000254" in dart_candidates
    assert dart_candidates["cand-20260819000254"].status == CandidateStatus.CANDIDATE_DETECTED

    edgar_candidates = candidate_store.load_candidates(tmp_path, "edgar_candidates.json")
    assert len(edgar_candidates) == 4
    for r in _four_edgar_records():
        assert r.candidate.id in edgar_candidates

    dart_fe = json.loads((tmp_path / "dart_filing_events.json").read_text())
    assert len(dart_fe["filing_events"]) == 1
    assert len(dart_fe["candidate_signals"]) == 1
    assert dart_fe["seen_receipt_numbers"] == ["20260819000254"]

    edgar_fe = json.loads((tmp_path / "edgar_filing_events.json").read_text())
    assert len(edgar_fe["filing_events"]) == 4
    assert len(edgar_fe["candidate_signals"]) == 4
    assert len(edgar_fe["seen_keys"]) == 4


# --- 2. Empty input is a no-op ---

def test_empty_input_is_a_safe_noop(tmp_path):
    result = backfill_candidates(tmp_path, [])
    assert result == type(result)(created_candidate_ids=(), already_present_candidate_ids=())
    assert list(tmp_path.iterdir()) == []


# --- 3. Idempotent second run ---

def test_idempotent_second_run_produces_no_duplicates_and_no_file_changes(tmp_path):
    records = [_dart_record()] + _four_edgar_records()
    backfill_candidates(tmp_path, records)

    before_bytes = {p: p.read_bytes() for p in tmp_path.iterdir()}

    second = backfill_candidates(tmp_path, records)

    assert second.created_candidate_ids == ()
    assert set(second.already_present_candidate_ids) == {r.candidate.id for r in records}

    after_bytes = {p: p.read_bytes() for p in tmp_path.iterdir()}
    assert after_bytes == before_bytes  # byte-identical — no-op on repeat


# --- 4. Validation failures — each rejects before any write ---

def _assert_dir_untouched(tmp_path, action):
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(ValueError):
        action()
    assert list(tmp_path.iterdir()) == []  # still nothing written


def test_rejects_unsupported_source(tmp_path):
    record = _dart_record()
    bad = BackfillRecord(source="EDINET", candidate=record.candidate)
    _assert_dir_untouched(tmp_path, lambda: backfill_candidates(tmp_path, [bad]))


def test_rejects_source_filing_mismatch(tmp_path):
    record = _dart_record()
    bad = BackfillRecord(source="SEC EDGAR", candidate=record.candidate)  # filing.source_name is still DART
    _assert_dir_untouched(tmp_path, lambda: backfill_candidates(tmp_path, [bad]))


def test_rejects_non_candidate_detected_status(tmp_path):
    filing = _dart_filing("20260819000254")
    candidate = _bare_candidate("cand-20260819000254", filing, ["x"])
    candidate.status = CandidateStatus.NEEDS_REVIEW
    bad = BackfillRecord(source="OpenDART / DART", candidate=candidate)
    _assert_dir_untouched(tmp_path, lambda: backfill_candidates(tmp_path, [bad]))


def test_rejects_non_empty_reviewed_note(tmp_path):
    filing = _dart_filing("20260819000254")
    candidate = _bare_candidate("cand-20260819000254", filing, ["x"])
    candidate.reviewed_note = "someone reviewed this already"
    bad = BackfillRecord(source="OpenDART / DART", candidate=candidate)
    _assert_dir_untouched(tmp_path, lambda: backfill_candidates(tmp_path, [bad]))


def test_rejects_populated_excerpt(tmp_path):
    filing = _dart_filing("20260819000254")
    candidate = _bare_candidate("cand-20260819000254", filing, ["x"])
    candidate.excerpt_original = "some excerpt text"
    bad = BackfillRecord(source="OpenDART / DART", candidate=candidate)
    _assert_dir_untouched(tmp_path, lambda: backfill_candidates(tmp_path, [bad]))


def test_rejects_invalid_candidate_id_prefix(tmp_path):
    filing = _dart_filing("20260819000254")
    candidate = _bare_candidate("edgar-cand-20260819000254", filing, ["x"])  # wrong prefix for DART
    bad = BackfillRecord(source="OpenDART / DART", candidate=candidate)
    _assert_dir_untouched(tmp_path, lambda: backfill_candidates(tmp_path, [bad]))


def test_rejects_duplicate_ids_in_input(tmp_path):
    record = _dart_record()
    _assert_dir_untouched(tmp_path, lambda: backfill_candidates(tmp_path, [record, record]))


# --- 5. Simulated cross-source failure: DART succeeds, EDGAR fails -> full rollback ---

def test_cross_source_failure_rolls_back_all_four_files(tmp_path, monkeypatch):
    # Seed one pre-existing record per source so "preserved unchanged"
    # is actually exercised by the rollback, not just "file didn't exist."
    existing_dart = _dart_record("20260101000001")
    existing_edgar = _edgar_record("0000000000-26-000001", "0000009999")
    backfill_candidates(tmp_path, [existing_dart, existing_edgar])

    before_bytes = {
        p: p.read_bytes()
        for p in [
            tmp_path / "dart_filing_events.json", tmp_path / "dart_candidates.json",
            tmp_path / "edgar_filing_events.json", tmp_path / "edgar_candidates.json",
        ]
    }

    real_upsert = candidate_store.upsert_new_candidates

    def _failing_upsert(cache_dir, new_candidates, filename=candidate_store._CACHE_FILENAME):
        if filename == "edgar_candidates.json":
            raise RuntimeError("simulated EDGAR persistence failure")
        return real_upsert(cache_dir, new_candidates, filename)

    monkeypatch.setattr("src.logic.candidate_backfill.candidate_store.upsert_new_candidates", _failing_upsert)

    new_dart = _dart_record("20260819000254")
    new_edgar = _edgar_record("0001193125-26-354029")

    with pytest.raises(CandidateBackfillError) as exc_info:
        backfill_candidates(tmp_path, [new_dart, new_edgar])

    assert "simulated EDGAR persistence failure" in str(exc_info.value.__cause__)

    after_bytes = {
        p: p.read_bytes()
        for p in [
            tmp_path / "dart_filing_events.json", tmp_path / "dart_candidates.json",
            tmp_path / "edgar_filing_events.json", tmp_path / "edgar_candidates.json",
        ]
    }
    assert after_bytes == before_bytes  # DART's completed writes were rolled back too

    dart_candidates = candidate_store.load_candidates(tmp_path)
    assert "cand-20260819000254" not in dart_candidates  # never left half-committed
    assert "cand-20260101000001" in dart_candidates  # pre-existing record still intact


# --- 6. Simulated rollback failure — verify the error preserves both contexts ---

def test_rollback_failure_error_includes_both_original_and_rollback_context(tmp_path, monkeypatch):
    def _failing_upsert(*args, **kwargs):
        raise RuntimeError("simulated primary failure")

    def _failing_restore(*args, **kwargs):
        raise OSError("simulated rollback failure")

    monkeypatch.setattr("src.logic.candidate_backfill.candidate_store.upsert_new_candidates", _failing_upsert)
    monkeypatch.setattr("src.logic.candidate_backfill._restore_files", _failing_restore)

    with pytest.raises(CandidateBackfillError) as exc_info:
        backfill_candidates(tmp_path, [_dart_record()])

    message = str(exc_info.value)
    assert "simulated primary failure" in message
    assert "simulated rollback failure" in message
    assert "inconsistent state" in message


# --- 7. No network dependency anywhere in the tested path ---

def test_module_imports_no_network_capable_client():
    import src.logic.candidate_backfill as module
    source = open(module.__file__, encoding="utf-8").read()
    for forbidden in ("EdgarClient", "DartClient", "EdinetClient", "requests.", "import requests"):
        assert forbidden not in source
