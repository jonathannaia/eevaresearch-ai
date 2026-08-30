"""Retry eligibility for Radar Inbox's document-retrieval retries — pure,
Streamlit-free, no I/O. Two independent gates live here:

- retry_eligibility()/is_retryable() — the *manual* "Process now"/"Retry
  processing" button's own short (RETRY_COOLDOWN_SECONDS) cooldown.
- automatic_retry_eligible() — each source's own run_pipeline() worker-
  tick selection of stale RETRIEVAL_FAILED/PARSE_FAILED candidates for an
  unattended retry, gated by a much longer escalating backoff
  (AUTOMATIC_RETRY_BACKOFF_MULTIPLIER x the scan interval x attempts
  used) specifically so a whole backlog of old failures can't all become
  eligible on the same tick and spike request volume.

Both share the same MAX_RETRY_ATTEMPTS budget — a manual click and an
automatic tick both represent one real attempt against the same
regulator endpoint, so they draw from one pool, not two.

Derived entirely from CandidateSignal.state_history, which is already a
complete, timestamped, append-only audit trail (see models.py) — no new
persisted field needed. Each processing attempt (automatic or manual)
appends exactly one QUEUED_FOR_PROCESSING transition, so counting those
is an exact attempt count.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.models.models import CandidateSignal, CandidateStatus

RETRY_COOLDOWN_SECONDS = 60
MAX_RETRY_ATTEMPTS = 3

# Automatic (unattended, worker-tick) retry — deliberately separate,
# much longer timing than the manual button's flat RETRY_COOLDOWN_SECONDS
# above. Escalating rather than flat so a candidate that keeps failing
# doesn't get hammered every single tick.
AUTOMATIC_RETRY_BACKOFF_MULTIPLIER = 3
# Independent of DEFAULT_MAX_CANDIDATES_PER_SCAN/max_candidates_to_process
# (the new/deferred-candidate budget) — a backlog of old failures must
# never be able to compete with or shrink that budget.
AUTOMATIC_RETRY_MAX_PER_TICK_PER_SOURCE = 2

# The three real, reachable failure states a manual retry applies to.
# CandidateStatus.TRANSLATION_UNAVAILABLE exists on the enum but the
# pipeline never sets it as a candidate's status — a translation failure
# with a successful extraction still lands at NEEDS_REVIEW with
# translation_state=UNAVAILABLE (see radar_pipeline.process_candidate),
# so that combination is checked separately below rather than as a status
# membership test.
_RETRYABLE_STATUSES = frozenset({CandidateStatus.RETRIEVAL_FAILED, CandidateStatus.PARSE_FAILED})


@dataclass(frozen=True)
class RetryEligibility:
    eligible: bool
    reason: str
    attempts_used: int
    cooldown_remaining_seconds: int


def is_retryable(candidate: CandidateSignal) -> bool:
    from src.models.models import TranslationState

    if candidate.status in _RETRYABLE_STATUSES:
        return True
    return candidate.status == CandidateStatus.NEEDS_REVIEW and candidate.translation_state == TranslationState.UNAVAILABLE


def attempts_used(candidate: CandidateSignal) -> int:
    return sum(1 for t in candidate.state_history if t.status == CandidateStatus.QUEUED_FOR_PROCESSING)


def _last_attempt_at(candidate: CandidateSignal) -> datetime | None:
    queued_at = [t.at for t in candidate.state_history if t.status == CandidateStatus.QUEUED_FOR_PROCESSING]
    if not queued_at:
        return None
    try:
        latest = max(queued_at)
        parsed = datetime.fromisoformat(latest)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def retry_eligibility(candidate: CandidateSignal, now: datetime | None = None) -> RetryEligibility:
    """Never raises — a candidate with malformed history is simply
    treated as having no prior attempts, so the safe default is to allow
    (rather than silently and permanently block) a manual retry."""
    used = attempts_used(candidate)
    if used >= MAX_RETRY_ATTEMPTS:
        return RetryEligibility(eligible=False, reason="Retry limit reached", attempts_used=used, cooldown_remaining_seconds=0)

    now = now or datetime.now(timezone.utc)
    last_attempt = _last_attempt_at(candidate)
    if last_attempt is not None:
        elapsed = (now - last_attempt).total_seconds()
        remaining = RETRY_COOLDOWN_SECONDS - elapsed
        if remaining > 0:
            return RetryEligibility(
                eligible=False, reason="Cooldown active", attempts_used=used,
                cooldown_remaining_seconds=int(remaining) + 1,
            )

    return RetryEligibility(eligible=True, reason="", attempts_used=used, cooldown_remaining_seconds=0)


def automatic_retry_eligible(candidate: CandidateSignal, scan_interval_minutes: int, now: datetime | None = None) -> bool:
    """Whether a source's own run_pipeline() may pick up this candidate
    for an automatic, unattended retry this tick. Never eligible for
    anything but RETRIEVAL_FAILED/PARSE_FAILED (a cached EXTRACTED result
    is never re-fetched due to age — that invariant is also enforced
    structurally in each source's document_service.py). Fails open (True)
    only when state_history is malformed/absent an attempt timestamp,
    matching retry_eligibility()'s own stated policy above; a candidate
    with zero recorded attempts is never expected to reach a FAILED
    status in the first place, so that case returns False rather than
    guessing."""
    if candidate.status not in _RETRYABLE_STATUSES:
        return False

    used = attempts_used(candidate)
    if used == 0 or used >= MAX_RETRY_ATTEMPTS:
        return False

    now = now or datetime.now(timezone.utc)
    last_attempt = _last_attempt_at(candidate)
    if last_attempt is None:
        return True

    required_minutes = AUTOMATIC_RETRY_BACKOFF_MULTIPLIER * scan_interval_minutes * used
    elapsed_minutes = (now - last_attempt).total_seconds() / 60
    return elapsed_minutes >= required_minutes
