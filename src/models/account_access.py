"""Account access entitlements — who may use EevaResearch, and on what
basis. The authorization record that `src/logic/account_access.py` turns
into an allow/deny decision.

WHY THIS IS NOT A COLUMN ON user_accounts. src/models/user_account.py
deliberately carries no role or status: its docstring says so, and that
restraint is what lets src/ui/pages/admin_users.py promise it "cannot
render what the underlying table doesn't have". Access is a separate
concern with a separate lifecycle, a separate audit trail, and a
separate reason to exist, so it gets its own table and its own model.
A user_accounts row says somebody signed in; an AccountEntitlement row
says somebody may.

ABSENCE MEANS NO ACCESS. There is no "default entitlement". An account
with no row is not entitled, and every code path that cannot read a
row must behave as if there is none. That is the whole point of making
access affirmative rather than inferred from an empty allowlist.

WHAT IS DELIBERATELY NOT HERE. No payment data: no card details, no
Stripe identifiers, no amounts, no invoices. Billing is a later,
separately-reviewed phase and will bring its own schema; guessing its
shape now would mean migrating a live production table twice. No IP
address, device fingerprint or page-view history either — the same
minimum this codebase already holds itself to for user_accounts.
Request fields hold only what somebody voluntarily typed in order to
ask for access."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EntitlementConflictError(Exception):
    """A write would have overwritten a decision somebody already made.

    Raised rather than silently winning, because both outcomes of a race
    here are wrong in a way the caller must see: re-requesting over an
    approval would downgrade a paying account, and re-approving over a
    revocation would undo a deliberate removal.

    Defined here, in the model layer, rather than in either repository.
    The three backends implement their own SQL with no shared code, but
    they must raise the SAME exception type — a caller writing
    `except EntitlementConflictError` has no way to know which backend
    is configured, and a per-backend class would mean the admin page's
    conflict handling silently stopped working the day production moved
    from SQLite to Postgres."""


class AccessStatus(str, Enum):
    """Where an account sits in the access lifecycle.

    The ordinary path is REQUESTED -> APPROVED -> ACTIVE. APPROVED and
    ACTIVE are kept distinct because they will stop being the same thing
    once payment exists: approved-but-unpaid is a real state, and
    collapsing it into ACTIVE now would mean splitting it later under
    a live billing flow.

    Only ACTIVE grants access on its own. Every other value is a denial
    with a different explanation, which is what lets the UI tell someone
    "we are reviewing this" rather than a flat refusal."""

    REQUESTED = "requested"
    APPROVED = "approved"
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    DENIED = "denied"


class AccessTier(str, Enum):
    """What an active account is entitled to.

    TRIAL and PAID differ in what the app shows, not in whether it
    opens. COMPLIMENTARY is for accounts that should never be billed —
    the owner, advisors, press — and exists so that "not paying" never
    has to be recorded as a lapsed trial."""

    TRIAL = "trial"
    PAID = "paid"
    COMPLIMENTARY = "complimentary"


#: Statuses from which an approval decision is still outstanding, i.e.
#: the ones the admin review queue is built from.
PENDING_STATUSES: frozenset[AccessStatus] = frozenset({AccessStatus.REQUESTED})

#: The only status that grants access by itself. Expressed as a set so
#: that adding a second one later is a deliberate edit here, visible in
#: a diff, rather than a new `or` buried in a conditional.
ACCESS_GRANTING_STATUSES: frozenset[AccessStatus] = frozenset({AccessStatus.ACTIVE})


@dataclass(frozen=True)
class AccessRequest:
    """What somebody voluntarily told us when asking for access.

    Every field is optional except `reason`, and none is verified. Treat
    all three as self-reported free text: they inform a human decision
    and must never be used as an authorization input."""

    reason: str
    organisation: str | None = None
    role: str | None = None


@dataclass(frozen=True)
class AccountEntitlement:
    """One account's current access state.

    `tier` is None until an account is actually granted something, so a
    pending or denied request cannot be mistaken for a trial. `expires_at`
    is None for entitlements that do not lapse (COMPLIMENTARY, and PAID
    while a subscription is current); an ISO-8601 UTC timestamp
    otherwise. Expiry is evaluated against a caller-supplied clock, never
    a module-level `now()`, so the decision stays testable and pure."""

    email: str
    status: AccessStatus
    tier: AccessTier | None
    requested_at: str | None
    decided_at: str | None
    decided_by: str | None
    expires_at: str | None
    updated_at: str
    request: AccessRequest | None = None


@dataclass(frozen=True)
class AccessEvent:
    """One append-only line of access history.

    Written on every status change and never updated or deleted, so
    "why does this person have access" is always answerable from the
    record rather than reconstructed. `actor` is the admin email that
    made the change, or a system identifier for an automatic transition
    such as an expiry."""

    email: str
    from_status: AccessStatus | None
    to_status: AccessStatus
    tier: AccessTier | None
    at: str
    actor: str
    detail: str = ""


#: Actor recorded for transitions nobody performed by hand.
SYSTEM_ACTOR = "system"
