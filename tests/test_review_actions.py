"""record_review_decision — pure logic over the real candidate_store API,
tmp_path only, zero network dependency. Mirrors test_signal_promotion.py's
fixture style."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.data_access.dart import candidate_store
from src.logic.review_actions import record_review_decision
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, StateTransition


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _filing(**overrides) -> FilingEvent:
    defaults = dict(
        rcept_no="20260812000001", corp_code="00164779", corp_name="SK Hynix", stock_code="000660",
        report_nm="조회공시요구(풍문또는보도)에대한답변(미확정)", rcept_dt="20260812", flr_nm="SK 하이닉스",
        theme_slug="memory", source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260812000001",
        retrieved_at=_now_iso(),
    )
    defaults.update(overrides)
    return FilingEvent(**defaults)


def _candidate(candidate_id: str = "cand-review-1", **overrides) -> CandidateSignal:
    filing = overrides.pop("filing", _filing())
    defaults = dict(
        id=candidate_id, filing=filing, matched_rules=["market_rumor_response:x:풍문"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="본문 발췌.",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    defaults.update(overrides)
    return CandidateSignal(**defaults)


_REVIEWER_STATUSES = (CandidateStatus.PUBLISHED, CandidateStatus.MONITORING, CandidateStatus.DISMISSED)
_NON_REVIEWER_STATUSES = (
    CandidateStatus.NEW_FILING_EVENT, CandidateStatus.CANDIDATE_DETECTED, CandidateStatus.QUEUED_FOR_PROCESSING,
    CandidateStatus.RETRIEVAL_IN_PROGRESS, CandidateStatus.EXTRACTION_PENDING, CandidateStatus.EXTRACTED,
    CandidateStatus.TRANSLATION_PENDING, CandidateStatus.TRANSLATED, CandidateStatus.NEEDS_REVIEW,
    CandidateStatus.PROCESSING_DEFERRED, CandidateStatus.PARSE_FAILED, CandidateStatus.RETRIEVAL_FAILED,
    CandidateStatus.TRANSLATION_UNAVAILABLE, CandidateStatus.NOT_MATERIAL,
)


@pytest.mark.parametrize("status", _REVIEWER_STATUSES)
def test_record_review_decision_sets_status_reviewed_fields_and_appends_transition(tmp_path, status):
    candidate = _candidate()
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})

    updated = record_review_decision(tmp_path, candidate.id, candidate_store._CACHE_FILENAME, status, note="Reviewer note here.")

    assert updated is not None
    assert updated.status == status
    assert updated.reviewed_at is not None
    assert updated.reviewed_note == "Reviewer note here."
    assert len(updated.state_history) == 2  # original NEEDS_REVIEW entry + this one appended
    assert updated.state_history[-1].status == status
    assert updated.state_history[-1].detail == "Reviewer note here."


@pytest.mark.parametrize("status", _REVIEWER_STATUSES)
def test_record_review_decision_default_detail_when_note_empty(tmp_path, status):
    candidate = _candidate()
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})

    updated = record_review_decision(tmp_path, candidate.id, candidate_store._CACHE_FILENAME, status)

    assert updated.reviewed_note == ""
    assert updated.state_history[-1].detail == f"Reviewer decision: {status.value}"


def test_record_review_decision_preserves_prior_state_history_order(tmp_path):
    candidate = _candidate(state_history=[
        StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="2026-08-01T00:00:00+00:00"),
        StateTransition(status=CandidateStatus.EXTRACTED, at="2026-08-02T00:00:00+00:00", detail="Extracted successfully."),
        StateTransition(status=CandidateStatus.NEEDS_REVIEW, at="2026-08-03T00:00:00+00:00"),
    ])
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})

    updated = record_review_decision(tmp_path, candidate.id, candidate_store._CACHE_FILENAME, CandidateStatus.PUBLISHED, note="Looks solid.")

    assert len(updated.state_history) == 4
    assert [t.status for t in updated.state_history[:3]] == [
        CandidateStatus.CANDIDATE_DETECTED, CandidateStatus.EXTRACTED, CandidateStatus.NEEDS_REVIEW,
    ]
    assert updated.state_history[1].detail == "Extracted successfully."  # untouched
    assert updated.state_history[-1].status == CandidateStatus.PUBLISHED
    assert updated.state_history[-1].detail == "Looks solid."


def test_record_review_decision_persists_through_the_real_candidate_store(tmp_path):
    candidate = _candidate()
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})

    record_review_decision(tmp_path, candidate.id, candidate_store._CACHE_FILENAME, CandidateStatus.MONITORING, note="Watching for confirmation.")

    reloaded = candidate_store.load_candidates(tmp_path)[candidate.id]
    assert reloaded.status == CandidateStatus.MONITORING
    assert reloaded.reviewed_note == "Watching for confirmation."
    assert reloaded.reviewed_at is not None
    assert reloaded.state_history[-1].status == CandidateStatus.MONITORING
    assert reloaded.state_history[-1].detail == "Watching for confirmation."


def test_record_review_decision_unknown_id_returns_none_and_writes_nothing(tmp_path):
    candidate = _candidate()
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})
    before = (tmp_path / candidate_store._CACHE_FILENAME).read_text(encoding="utf-8")

    result = record_review_decision(tmp_path, "cand-does-not-exist", candidate_store._CACHE_FILENAME, CandidateStatus.PUBLISHED)

    assert result is None
    after = (tmp_path / candidate_store._CACHE_FILENAME).read_text(encoding="utf-8")
    assert after == before  # byte-identical — no write


def test_record_review_decision_unknown_id_no_cache_file_at_all(tmp_path):
    result = record_review_decision(tmp_path, "cand-does-not-exist", candidate_store._CACHE_FILENAME, CandidateStatus.PUBLISHED)
    assert result is None
    assert not (tmp_path / candidate_store._CACHE_FILENAME).exists()


@pytest.mark.parametrize("status", _NON_REVIEWER_STATUSES)
def test_record_review_decision_invalid_status_raises_and_writes_nothing(tmp_path, status):
    candidate = _candidate()
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})
    before = (tmp_path / candidate_store._CACHE_FILENAME).read_text(encoding="utf-8")

    with pytest.raises(ValueError):
        record_review_decision(tmp_path, candidate.id, candidate_store._CACHE_FILENAME, status)

    after = (tmp_path / candidate_store._CACHE_FILENAME).read_text(encoding="utf-8")
    assert after == before  # byte-identical — no write, even though the candidate exists


def test_record_review_decision_changing_a_published_decision_appends_not_overwrites(tmp_path):
    candidate = _candidate()
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})

    published = record_review_decision(tmp_path, candidate.id, candidate_store._CACHE_FILENAME, CandidateStatus.PUBLISHED, note="Initial approval.")
    assert published.status == CandidateStatus.PUBLISHED
    assert len(published.state_history) == 2

    monitored = record_review_decision(tmp_path, candidate.id, candidate_store._CACHE_FILENAME, CandidateStatus.MONITORING, note="Actually, watching first.")
    assert monitored.status == CandidateStatus.MONITORING
    assert len(monitored.state_history) == 3
    assert [t.status for t in monitored.state_history] == [
        CandidateStatus.NEEDS_REVIEW, CandidateStatus.PUBLISHED, CandidateStatus.MONITORING,
    ]
    assert monitored.state_history[1].detail == "Initial approval."  # earlier decision preserved verbatim

    dismissed = record_review_decision(tmp_path, candidate.id, candidate_store._CACHE_FILENAME, CandidateStatus.DISMISSED, note="No longer relevant.")
    assert dismissed.status == CandidateStatus.DISMISSED
    assert len(dismissed.state_history) == 4
    assert [t.status for t in dismissed.state_history] == [
        CandidateStatus.NEEDS_REVIEW, CandidateStatus.PUBLISHED, CandidateStatus.MONITORING, CandidateStatus.DISMISSED,
    ]

    reloaded = candidate_store.load_candidates(tmp_path)[candidate.id]
    assert reloaded.status == CandidateStatus.DISMISSED
    assert len(reloaded.state_history) == 4


def test_record_review_decision_does_not_modify_unrelated_fields(tmp_path):
    candidate = _candidate(confidence="High", matched_rules=["earnings:x:실적"])
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})

    updated = record_review_decision(tmp_path, candidate.id, candidate_store._CACHE_FILENAME, CandidateStatus.PUBLISHED)

    assert updated.confidence == "High"
    assert updated.matched_rules == ["earnings:x:실적"]
    assert updated.filing.corp_name == "SK Hynix"
    assert updated.excerpt_original == "본문 발췌."
