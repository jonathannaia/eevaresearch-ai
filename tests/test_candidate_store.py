"""candidate_store — persisted CandidateSignal read/write, merge
semantics, and corrupt-data resilience. No network."""
from __future__ import annotations

from datetime import datetime, timezone

from src.data_access.dart import candidate_store
from src.models.models import (
    CandidateSignal,
    CandidateStatus,
    ExcerptQuality,
    ExtractionState,
    FilingEvent,
    StateTransition,
    Translation,
    TranslationState,
)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _filing(rcept_no: str) -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm="신규시설투자등", rcept_dt="20260810", flr_nm="삼성전자",
    )


def _candidate(rcept_no: str, status: CandidateStatus = CandidateStatus.CANDIDATE_DETECTED) -> CandidateSignal:
    return CandidateSignal(
        id=f"cand-{rcept_no}", filing=_filing(rcept_no), matched_rules=["capex_or_facility_investment:facility_investment:신규시설투자"],
        confidence="Moderate", status=status, state_history=[StateTransition(status=status, at=_iso())],
    )


def test_load_candidates_empty_when_no_store(tmp_path):
    assert candidate_store.load_candidates(tmp_path) == {}


def test_upsert_and_load_round_trip(tmp_path):
    candidate = _candidate("20260810000001")
    candidate_store.upsert_new_candidates(tmp_path, [candidate])

    loaded = candidate_store.load_candidates(tmp_path)

    assert "cand-20260810000001" in loaded
    assert loaded["cand-20260810000001"].filing.report_nm == "신규시설투자등"
    assert loaded["cand-20260810000001"].status == CandidateStatus.CANDIDATE_DETECTED


def test_upsert_does_not_overwrite_an_existing_candidates_processing_state(tmp_path):
    candidate = _candidate("20260810000001")
    candidate_store.upsert_new_candidates(tmp_path, [candidate])

    advanced = candidate_store.load_candidates(tmp_path)["cand-20260810000001"]
    advanced.status = CandidateStatus.NEEDS_REVIEW
    candidate_store.update_candidate(tmp_path, advanced)

    # Re-"detecting" the same candidate (as a re-scan would) must not
    # revert its already-advanced processing state.
    candidate_store.upsert_new_candidates(tmp_path, [_candidate("20260810000001")])

    result = candidate_store.load_candidates(tmp_path)["cand-20260810000001"]
    assert result.status == CandidateStatus.NEEDS_REVIEW


def test_update_candidate_preserves_other_candidates(tmp_path):
    candidate_store.upsert_new_candidates(tmp_path, [_candidate("20260810000001"), _candidate("20260810000002")])

    one = candidate_store.load_candidates(tmp_path)["cand-20260810000001"]
    one.status = CandidateStatus.PROCESSING_DEFERRED
    candidate_store.update_candidate(tmp_path, one)

    store = candidate_store.load_candidates(tmp_path)
    assert store["cand-20260810000001"].status == CandidateStatus.PROCESSING_DEFERRED
    assert store["cand-20260810000002"].status == CandidateStatus.CANDIDATE_DETECTED


def test_round_trips_full_processing_state_including_translations_and_history(tmp_path):
    candidate = _candidate("20260810000001")
    candidate.extraction_state = ExtractionState.EXTRACTED
    candidate.translation_state = TranslationState.TRANSLATED
    candidate.excerpt_quality = ExcerptQuality.TABLE_HEAVY
    candidate.excerpt_original = "신규시설투자등 결정"
    candidate.title_translation = Translation(
        translated_text="New facility investment decision", provider="DeepL",
        source_lang="ko", target_lang="en", translated_at=_iso(),
    )
    candidate.state_history.append(StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_iso(), detail="ready"))
    candidate_store.update_candidate(tmp_path, candidate)

    result = candidate_store.load_candidates(tmp_path)["cand-20260810000001"]

    assert result.extraction_state == ExtractionState.EXTRACTED
    assert result.translation_state == TranslationState.TRANSLATED
    assert result.excerpt_quality == ExcerptQuality.TABLE_HEAVY
    assert result.title_translation.translated_text == "New facility investment decision"
    assert len(result.state_history) == 2


def test_load_candidates_handles_corrupt_file_without_raising(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "dart_candidates.json").write_text("{not valid json", encoding="utf-8")
    assert candidate_store.load_candidates(tmp_path) == {}


def test_load_candidates_skips_one_corrupt_entry_without_losing_the_rest(tmp_path):
    candidate_store.upsert_new_candidates(tmp_path, [_candidate("20260810000001")])
    import json
    path = tmp_path / "dart_candidates.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["cand-broken"] = {"missing": "required fields"}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    store = candidate_store.load_candidates(tmp_path)

    assert "cand-20260810000001" in store
    assert "cand-broken" not in store
