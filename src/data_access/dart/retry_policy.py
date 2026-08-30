"""Manual retry eligibility for Radar Inbox's "Process now"/"Retry
processing" actions — pure, Streamlit-free, no I/O. The user was explicit
that automatic retry of failed candidates on every scan risks hammering
DART/DeepL; this module is the guard behind the *manual* action instead,
so repeated clicks can't do the same thing by another route.

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
