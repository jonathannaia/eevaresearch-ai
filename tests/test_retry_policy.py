"""retry_policy — pure functions over CandidateSignal.state_history, no
mocks or I/O needed."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.data_access.dart import retry_policy
from src.models.models import CandidateSignal, CandidateStatus, FilingEvent, StateTransition, TranslationState

_NOW = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)


def _filing() -> FilingEvent:
    return FilingEvent(
        rcept_no="20260810000001", corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm="신규시설투자등", rcept_dt="20260810", flr_nm="삼성전자",
    )


def _candidate(status: CandidateStatus, queued_ats: list[datetime], translation_state: TranslationState = TranslationState.NOT_REQUESTED) -> CandidateSignal:
    history = [StateTransition(status=CandidateStatus.QUEUED_FOR_PROCESSING, at=at.isoformat()) for at in queued_ats]
    return CandidateSignal(
        id="cand-20260810000001", filing=_filing(), matched_rules=["capex_or_facility_investment:facility_investment:신규시설투자"],
        confidence="Moderate", status=status, translation_state=translation_state, state_history=history,
    )


def test_attempts_used_counts_queued_transitions():
    candidate = _candidate(CandidateStatus.NEEDS_REVIEW, [_NOW - timedelta(hours=2), _NOW - timedelta(hours=1)])
    assert retry_policy.attempts_used(candidate) == 2


def test_attempts_used_zero_for_never_processed_candidate():
    candidate = _candidate(CandidateStatus.CANDIDATE_DETECTED, [])
    assert retry_policy.attempts_used(candidate) == 0


def test_is_retryable_true_for_retrieval_failed():
    assert retry_policy.is_retryable(_candidate(CandidateStatus.RETRIEVAL_FAILED, []))


def test_is_retryable_true_for_parse_failed():
    assert retry_policy.is_retryable(_candidate(CandidateStatus.PARSE_FAILED, []))


def test_is_retryable_true_for_needs_review_with_unavailable_translation():
    candidate = _candidate(CandidateStatus.NEEDS_REVIEW, [], translation_state=TranslationState.UNAVAILABLE)
    assert retry_policy.is_retryable(candidate)


def test_is_retryable_false_for_needs_review_with_successful_translation():
    candidate = _candidate(CandidateStatus.NEEDS_REVIEW, [], translation_state=TranslationState.TRANSLATED)
    assert not retry_policy.is_retryable(candidate)


def test_is_retryable_false_for_candidate_detected():
    assert not retry_policy.is_retryable(_candidate(CandidateStatus.CANDIDATE_DETECTED, []))


def test_retry_eligible_when_no_prior_attempts():
    candidate = _candidate(CandidateStatus.RETRIEVAL_FAILED, [])
    result = retry_policy.retry_eligibility(candidate, now=_NOW)
    assert result.eligible
    assert result.attempts_used == 0


def test_retry_blocked_just_inside_cooldown():
    just_inside = _NOW - timedelta(seconds=retry_policy.RETRY_COOLDOWN_SECONDS - 1)
    candidate = _candidate(CandidateStatus.RETRIEVAL_FAILED, [just_inside])
    result = retry_policy.retry_eligibility(candidate, now=_NOW)
    assert not result.eligible
    assert result.reason == "Cooldown active"
    assert result.cooldown_remaining_seconds > 0


def test_retry_eligible_just_outside_cooldown():
    just_outside = _NOW - timedelta(seconds=retry_policy.RETRY_COOLDOWN_SECONDS + 1)
    candidate = _candidate(CandidateStatus.RETRIEVAL_FAILED, [just_outside])
    result = retry_policy.retry_eligibility(candidate, now=_NOW)
    assert result.eligible


def test_retry_blocked_at_max_attempts():
    attempts = [_NOW - timedelta(days=1) - timedelta(seconds=i) for i in range(retry_policy.MAX_RETRY_ATTEMPTS)]
    candidate = _candidate(CandidateStatus.RETRIEVAL_FAILED, attempts)
    result = retry_policy.retry_eligibility(candidate, now=_NOW)
    assert not result.eligible
    assert result.reason == "Retry limit reached"
    assert result.attempts_used == retry_policy.MAX_RETRY_ATTEMPTS


def test_retry_eligible_one_below_max_attempts_and_past_cooldown():
    attempts = [_NOW - timedelta(days=1) - timedelta(seconds=i) for i in range(retry_policy.MAX_RETRY_ATTEMPTS - 1)]
    candidate = _candidate(CandidateStatus.RETRIEVAL_FAILED, attempts)
    result = retry_policy.retry_eligibility(candidate, now=_NOW)
    assert result.eligible
    assert result.attempts_used == retry_policy.MAX_RETRY_ATTEMPTS - 1
