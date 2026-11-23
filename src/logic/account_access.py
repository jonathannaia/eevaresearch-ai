"""Paid professional access, phase 1 — the access decision.

Pure: no Streamlit, no I/O, no network, no clock of its own. It takes a
Settings snapshot, an email, an already-read entitlement and an explicit
`now`, and returns an allow/deny decision with a reason. The caller does
the reading; this module does the deciding. Same discipline as
src/ui/beta_gate.py, which it is designed to sit beside rather than
replace — that module keeps its own tested contract and its own call
site.

HOW THIS DIFFERS FROM THE OPEN BETA. The beta gate treats an empty
allowlist as "let everyone in" (app.py's own call site decides that, and
deliberately). Entitlements invert it: access is affirmative, so no row
means no access and an unreadable store means no access. That inversion
is the entire point, and it is also why it cannot simply be switched on.

WHY ENFORCEMENT IS OFF BY DEFAULT. Turning this on while the
entitlements table is empty would lock out every existing user at the
moment of deploy. So `access_enforcement_enabled` is off unless
explicitly set, the deploy that adds the table changes nothing, and the
order of operations is: migrate, backfill real accounts, verify, then
enable. The same deploy-dormant pattern this codebase already uses for
the research-agent worker and the EDGAR publication gate.

WHY ADMINS BYPASS EVERYTHING. An administrator is allowed in before
enforcement, before the allowlist and before the entitlement is even
consulted. This is not a convenience. `admin_emails` comes from the
environment, so it keeps working when the database does not — which
means the one person who can repair a broken entitlement store can
always still reach the page that repairs it. Without that, a database
incident plus enforcement would lock the operator out of their own
remedy.

DENIAL REASONS ARE NOT INTERCHANGEABLE. "We are reviewing your request",
"your trial ended" and "this account was removed" are three different
things to tell somebody, and flattening them into one refusal is how a
product teaches people that it is broken. The reason enum exists so the
UI can say the true one."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from src.config.settings import Settings
from src.models.account_access import (
    ACCESS_GRANTING_STATUSES,
    AccessStatus,
    AccessTier,
    AccountEntitlement,
)


class AccessReason(str, Enum):
    """Why access was granted or refused. Every value is reachable, and
    every denial value says something different to the person."""

    # Allow
    ENFORCEMENT_DISABLED = "ENFORCEMENT_DISABLED"
    ADMIN = "ADMIN"
    BREAK_GLASS_ALLOWLIST = "BREAK_GLASS_ALLOWLIST"
    ENTITLED = "ENTITLED"

    # Deny
    SIGN_IN_REQUIRED = "SIGN_IN_REQUIRED"
    NO_ENTITLEMENT = "NO_ENTITLEMENT"
    REQUEST_PENDING = "REQUEST_PENDING"
    REQUEST_DENIED = "REQUEST_DENIED"
    ACCESS_REVOKED = "ACCESS_REVOKED"
    APPROVED_NOT_ACTIVATED = "APPROVED_NOT_ACTIVATED"
    ENTITLEMENT_EXPIRED = "ENTITLEMENT_EXPIRED"
    ENTITLEMENT_UNREADABLE = "ENTITLEMENT_UNREADABLE"
    ENTITLEMENT_MALFORMED = "ENTITLEMENT_MALFORMED"


#: Reasons that let somebody through. Kept as a set so that adding one
#: is an edit here, in a diff, rather than a new `or` in a conditional.
_ALLOWING_REASONS: frozenset[AccessReason] = frozenset({
    AccessReason.ENFORCEMENT_DISABLED,
    AccessReason.ADMIN,
    AccessReason.BREAK_GLASS_ALLOWLIST,
    AccessReason.ENTITLED,
})


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: AccessReason
    tier: AccessTier | None = None

    def __post_init__(self) -> None:
        """`allowed` and `reason` cannot disagree. A decision object that
        says "allowed" for a denial reason would be a silent security
        bug, so it is not constructible."""
        expected = self.reason in _ALLOWING_REASONS
        if self.allowed is not expected:
            raise ValueError(
                f"AccessDecision(allowed={self.allowed}) contradicts reason {self.reason.value!r}"
            )


def _normalize(email: str | None) -> str:
    return (email or "").strip().lower()


def _has_expired(expires_at: str | None, now: str) -> bool:
    """True when `expires_at` is set and `now` has reached it.

    Compares parsed datetimes rather than raw strings: both are ISO-8601
    today, but string ordering silently breaks on a trailing 'Z' versus
    '+00:00' or a different sub-second precision, and an expiry check
    that quietly stops working is worse than one that errors.

    An unparseable value counts as expired. The alternative is treating
    a corrupt timestamp as "never expires", which turns a data problem
    into indefinite free access."""
    if not expires_at:
        return False
    try:
        expiry = datetime.fromisoformat(expires_at)
        current = datetime.fromisoformat(now)
    except ValueError:
        return True
    if (expiry.tzinfo is None) != (current.tzinfo is None):
        # Mixing naive and aware datetimes raises on comparison. Rather
        # than guess a timezone for the naive one, fail closed.
        return True
    return current >= expiry


def evaluate_account_access(
    settings: Settings,
    email: str | None,
    entitlement: AccountEntitlement | None,
    now: str,
    *,
    entitlement_read_failed: bool = False,
) -> AccessDecision:
    """The whole decision, in the order the checks must happen.

    `entitlement_read_failed` is how the caller reports that it could
    not read the store at all, which is deliberately NOT the same signal
    as `entitlement=None`. None means "we looked and there is nothing",
    which is a real answer about a real account. A read failure means we
    do not know, and not knowing must not be answered with "no
    entitlement" — the person would be told their request was never
    received. Both deny; they say different things."""
    normalized_email = _normalize(email)

    # 1. No identity, no decision to make.
    if not normalized_email:
        return AccessDecision(False, AccessReason.SIGN_IN_REQUIRED)

    # 2. Administrators first — before enforcement, before the
    #    allowlist, before the store is consulted. See module docstring:
    #    this is what keeps the operator's remedy reachable during an
    #    entitlement-store outage.
    if normalized_email in settings.admin_emails:
        return AccessDecision(True, AccessReason.ADMIN)

    # 3. Dormant by default. Until this is explicitly enabled, the
    #    entitlement table is recorded state and nothing more, and the
    #    open beta's own gate in app.py remains the only gate.
    if not settings.access_enforcement_enabled:
        return AccessDecision(True, AccessReason.ENFORCEMENT_DISABLED)

    # 4. The environment allowlist survives as break-glass only: a way
    #    to restore one person's access without a database write, for
    #    the case where the database write is the thing that is broken.
    if normalized_email in settings.private_beta_allowed_emails:
        return AccessDecision(True, AccessReason.BREAK_GLASS_ALLOWLIST)

    # 5. We could not read the store. Not an answer about this account.
    if entitlement_read_failed:
        return AccessDecision(False, AccessReason.ENTITLEMENT_UNREADABLE)

    # 6. We read the store and this account is not in it.
    if entitlement is None:
        return AccessDecision(False, AccessReason.NO_ENTITLEMENT)

    if entitlement.status is AccessStatus.REQUESTED:
        return AccessDecision(False, AccessReason.REQUEST_PENDING)
    if entitlement.status is AccessStatus.DENIED:
        return AccessDecision(False, AccessReason.REQUEST_DENIED)
    if entitlement.status is AccessStatus.REVOKED:
        return AccessDecision(False, AccessReason.ACCESS_REVOKED)
    if entitlement.status is AccessStatus.APPROVED:
        # Approved is not yet active. Today an admin moves the row on;
        # once billing exists this is the state between "you may buy
        # this" and "you have".
        return AccessDecision(False, AccessReason.APPROVED_NOT_ACTIVATED)
    if entitlement.status is AccessStatus.EXPIRED:
        return AccessDecision(False, AccessReason.ENTITLEMENT_EXPIRED)

    if entitlement.status not in ACCESS_GRANTING_STATUSES:
        # Unreachable while AccessStatus has exactly the members handled
        # above. Present so that adding a status without deciding what
        # it means denies access instead of falling through to a grant.
        return AccessDecision(False, AccessReason.ENTITLEMENT_MALFORMED)

    # 7. Active — the only status that grants anything.
    if _has_expired(entitlement.expires_at, now):
        # The stored status still says ACTIVE; the clock says otherwise,
        # and the clock wins. Nothing sweeps expiries on a schedule, so
        # evaluating expiry at decision time is what makes a trial
        # actually end rather than end whenever a job next runs.
        return AccessDecision(False, AccessReason.ENTITLEMENT_EXPIRED, tier=entitlement.tier)
    if entitlement.tier is None:
        # Active with no tier is contradictory: the app would not know
        # what to show. Treated as malformed rather than guessing TRIAL.
        return AccessDecision(False, AccessReason.ENTITLEMENT_MALFORMED)

    return AccessDecision(True, AccessReason.ENTITLED, tier=entitlement.tier)
