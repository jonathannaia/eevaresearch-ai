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


# automatic_retry_eligible — unattended worker-tick retry, bounded
# backoff schedule (AUTOMATIC_RETRY_BACKOFF_SCHEDULE_MINUTES), entirely
# separate from the manual cooldown above. RETRIEVAL_FAILED only.

_FIRST_BACKOFF, _SECOND_BACKOFF = retry_policy.AUTOMATIC_RETRY_BACKOFF_SCHEDULE_MINUTES


def test_automatic_retry_not_eligible_before_first_backoff_window():
    just_inside = _NOW - timedelta(minutes=_FIRST_BACKOFF - 1)
    candidate = _candidate(CandidateStatus.RETRIEVAL_FAILED, [just_inside])
    assert not retry_policy.automatic_retry_eligible(candidate, now=_NOW)


def test_automatic_retry_eligible_once_first_backoff_window_elapses():
    just_outside = _NOW - timedelta(minutes=_FIRST_BACKOFF + 1)
    candidate = _candidate(CandidateStatus.RETRIEVAL_FAILED, [just_outside])
    assert retry_policy.automatic_retry_eligible(candidate, now=_NOW)


def test_automatic_retry_withheld_after_second_failure_until_second_backoff_window():
    # Two prior attempts (used=2) — required wait is now the *second*
    # schedule entry, not the first. Elapsed just past the first
    # window's threshold but still short of the second's must NOT be
    # eligible — this is exactly the "not every hourly tick" guarantee.
    first_attempt = _NOW - timedelta(minutes=_SECOND_BACKOFF + _FIRST_BACKOFF)
    second_attempt = _NOW - timedelta(minutes=_FIRST_BACKOFF + 1)
    candidate = _candidate(CandidateStatus.RETRIEVAL_FAILED, [first_attempt, second_attempt])
    assert not retry_policy.automatic_retry_eligible(candidate, now=_NOW)


def test_automatic_retry_eligible_after_second_backoff_window_elapses():
    first_attempt = _NOW - timedelta(minutes=_SECOND_BACKOFF * 2)
    second_attempt = _NOW - timedelta(minutes=_SECOND_BACKOFF + 1)
    candidate = _candidate(CandidateStatus.RETRIEVAL_FAILED, [first_attempt, second_attempt])
    assert retry_policy.automatic_retry_eligible(candidate, now=_NOW)


def test_automatic_retry_blocked_at_max_attempts_regardless_of_staleness():
    ancient = [_NOW - timedelta(days=10) - timedelta(seconds=i) for i in range(retry_policy.MAX_RETRY_ATTEMPTS)]
    candidate = _candidate(CandidateStatus.RETRIEVAL_FAILED, ancient)
    assert not retry_policy.automatic_retry_eligible(candidate, now=_NOW)


def test_automatic_retry_never_eligible_for_parse_failed():
    # The key narrowing: PARSE_FAILED is excluded even when it would
    # otherwise be well past the first backoff window.
    just_outside = _NOW - timedelta(minutes=_FIRST_BACKOFF + 1)
    candidate = _candidate(CandidateStatus.PARSE_FAILED, [just_outside])
    assert not retry_policy.automatic_retry_eligible(candidate, now=_NOW)


def test_automatic_retry_not_eligible_for_needs_review():
    candidate = _candidate(CandidateStatus.NEEDS_REVIEW, [_NOW - timedelta(days=1)])
    assert not retry_policy.automatic_retry_eligible(candidate, now=_NOW)


def test_automatic_retry_not_eligible_for_candidate_detected():
    candidate = _candidate(CandidateStatus.CANDIDATE_DETECTED, [])
    assert not retry_policy.automatic_retry_eligible(candidate, now=_NOW)


def test_automatic_retry_not_eligible_with_zero_recorded_attempts():
    # A FAILED status with no QUEUED_FOR_PROCESSING history at all is
    # malformed/unexpected — fails closed (not eligible) rather than
    # guessing an attempt happened.
    candidate = _candidate(CandidateStatus.RETRIEVAL_FAILED, [])
    assert not retry_policy.automatic_retry_eligible(candidate, now=_NOW)
