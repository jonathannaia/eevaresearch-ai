"""Taking an autonomously published Signal back off the public surface.

Withdrawal is a status change, never a delete. The candidate row, its
excerpt, its evidence and its entire `state_transitions` history are
retained — what changes is that the candidate leaves
`signal_promotion._ELIGIBLE_STATUSES`, so every public read stops
returning it on the next query. No page, card, query or feed has to
remember to exclude withdrawn items, which is exactly the kind of thing
that gets forgotten in one place and leaks.

Two deliberate restrictions:

  * only an AUTONOMOUSLY published candidate can be withdrawn here. A
    human-published Signal is somebody's editorial decision and is not
    this mechanism's to reverse; `review_actions` already owns that.
  * withdrawal is idempotent. Withdrawing twice changes nothing and
    records nothing the second time, so an operator who is unsure
    whether the first attempt landed can simply run it again.

Pure: no I/O. Callers supply the candidate and persist the result.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.logic.signal_promotion import PUBLISHED_BY_AUTONOMOUS_EDGAR
from src.models.models import CandidateSignal, CandidateStatus, StateTransition

WITHDRAWAL_ACTOR = "operator"

OUTCOME_WITHDRAWN = "withdrawn"
OUTCOME_ALREADY_WITHDRAWN = "already_withdrawn"
OUTCOME_NOT_AUTONOMOUS = "not_autonomous"
OUTCOME_NOT_PUBLISHED = "not_published"


@dataclass(frozen=True)
class WithdrawalOutcome:
    """`candidate` is the value to persist, or the unchanged input when
    nothing was done — so a caller can write unconditionally without
    checking, and a no-op write stays a no-op."""

    outcome: str
    candidate: CandidateSignal
    changed: bool


def withdraw_autonomous_signal(
    candidate: CandidateSignal, *, reason: str, mode: str, now: str,
) -> WithdrawalOutcome:
    """Marks one autonomously published candidate WITHDRAWN.

    `reason` and `mode` are recorded in the transition's own detail, so
    why it was withdrawn and under what operating mode stay attached to
    the record permanently rather than living in someone's memory."""
    if candidate.status is CandidateStatus.WITHDRAWN:
        return WithdrawalOutcome(OUTCOME_ALREADY_WITHDRAWN, candidate, False)
    if (candidate.published_by or "") != PUBLISHED_BY_AUTONOMOUS_EDGAR:
        return WithdrawalOutcome(OUTCOME_NOT_AUTONOMOUS, candidate, False)
    if candidate.status is not CandidateStatus.PUBLISHED:
        return WithdrawalOutcome(OUTCOME_NOT_PUBLISHED, candidate, False)

    detail = f"actor={WITHDRAWAL_ACTOR}; mode={mode}; reason={reason}"
    candidate.status = CandidateStatus.WITHDRAWN
    candidate.state_history = [
        *candidate.state_history,
        StateTransition(status=CandidateStatus.WITHDRAWN, at=now, detail=detail),
    ]
    return WithdrawalOutcome(OUTCOME_WITHDRAWN, candidate, True)
