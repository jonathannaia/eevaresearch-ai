"""Paid professional access, phase 1 — the isolated Postgres
counterpart to state_db/account_entitlement_repository.py, identical
shape and identical semantics, independently implemented (no shared
code, per this package's existing no-dialect-abstraction constraint).
See that module's own docstring for the full design rationale: absence
means no access, every status change leaves a trace in the same
transaction, and writes refuse to clobber a decision rather than
silently winning a race.

Persists `account_entitlements` and `account_access_events` —
postgres_state_db/schema.py's own V28.

Two invariants shape every function here.

ABSENCE MEANS NO ACCESS. `get_entitlement` returns None for an account
with no row, and None is a denial, not a blank entitlement. No function
in this module ever creates a row as a side effect of a read, and there
is no "default entitlement" to fall back to.

EVERY STATUS CHANGE LEAVES A TRACE. Each write path appends to
`account_access_events` in the SAME transaction as the entitlement
update, so a granted entitlement without a corresponding event is not a
state this module can produce. The event table is append-only: nothing
here updates or deletes a row in it.

Writes are deliberately not idempotent-by-overwrite. `request_access`
refuses to clobber an existing decision, and `set_status` records the
status it moved FROM, so a double-click produces one visible no-op
rather than a second silent grant."""
from __future__ import annotations

import psycopg

from src.data_access.postgres_state_db.connection import transaction
from src.models.account_access import (
    AccessEvent,
    AccessRequest,
    AccessStatus,
    AccessTier,
    AccountEntitlement,
    EntitlementConflictError,
)

_SELECT = "SELECT * FROM account_entitlements"


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _parse_status(raw: str) -> AccessStatus:
    """An unrecognized stored status is not coerced to a default. Access
    must never be granted because a value failed to parse, so anything
    unknown raises and the caller's own failure path decides — which, in
    the gate, is denial."""
    try:
        return AccessStatus(raw)
    except ValueError as exc:
        raise ValueError(f"unrecognized access status in storage: {raw!r}") from exc


def _parse_tier(raw: str | None) -> AccessTier | None:
    if raw is None or raw == "":
        return None
    try:
        return AccessTier(raw)
    except ValueError as exc:
        raise ValueError(f"unrecognized access tier in storage: {raw!r}") from exc


def _row_to_entitlement(row: dict) -> AccountEntitlement:
    reason = row["request_reason"]
    request = AccessRequest(
        reason=reason,
        organisation=row["request_organisation"],
        role=row["request_role"],
    ) if reason else None
    return AccountEntitlement(
        email=row["email"],
        status=_parse_status(row["status"]),
        tier=_parse_tier(row["tier"]),
        requested_at=row["requested_at"],
        decided_at=row["decided_at"],
        decided_by=row["decided_by"],
        expires_at=row["expires_at"],
        updated_at=row["updated_at"],
        request=request,
    )


def _row_to_event(row: dict) -> AccessEvent:
    return AccessEvent(
        email=row["email"],
        from_status=_parse_status(row["from_status"]) if row["from_status"] else None,
        to_status=_parse_status(row["to_status"]),
        tier=_parse_tier(row["tier"]),
        at=row["at"],
        actor=row["actor"],
        detail=row["detail"],
    )


def _append_event(conn: psycopg.Connection, event: AccessEvent) -> None:
    """Called only from inside an open transaction in this module, never
    on its own — an event and the state change it describes commit
    together or not at all."""
    conn.execute(
        """
        INSERT INTO account_access_events (email, from_status, to_status, tier, at, actor, detail)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            event.email,
            event.from_status.value if event.from_status else None,
            event.to_status.value,
            event.tier.value if event.tier else None,
            event.at,
            event.actor,
            event.detail,
        ),
    )


def get_entitlement(conn: psycopg.Connection, email: str) -> AccountEntitlement | None:
    row = conn.execute(f"{_SELECT} WHERE email = %s", (_normalize_email(email),)).fetchone()
    return _row_to_entitlement(row) if row is not None else None


def list_entitlements(
    conn: psycopg.Connection, statuses: tuple[AccessStatus, ...] | None = None
) -> list[AccountEntitlement]:
    """Most-recently-updated first. `statuses`, when given, filters to
    exactly those values; an EMPTY tuple returns nothing rather than
    everything, so a caller that computed an empty filter never
    accidentally lists every account."""
    if statuses is None:
        rows = conn.execute(f"{_SELECT} ORDER BY updated_at DESC").fetchall()
    elif not statuses:
        return []
    else:
        placeholders = ", ".join("%s" for _ in statuses)
        rows = conn.execute(
            f"{_SELECT} WHERE status IN ({placeholders}) ORDER BY updated_at DESC",
            tuple(status.value for status in statuses),
        ).fetchall()
    return [_row_to_entitlement(row) for row in rows]


def list_events(conn: psycopg.Connection, email: str) -> list[AccessEvent]:
    """Oldest first — this is a history, and a history reads forwards."""
    rows = conn.execute(
        "SELECT * FROM account_access_events WHERE email = %s ORDER BY at ASC, id ASC",
        (_normalize_email(email),),
    ).fetchall()
    return [_row_to_event(row) for row in rows]


def request_access(
    conn: psycopg.Connection, email: str, request: AccessRequest, now: str
) -> AccountEntitlement:
    """Records a new access request, or re-states a pending or denied
    one.

    Refuses to overwrite an entitlement that is approved, active or
    revoked: a request arriving over one of those means either a bug or
    a stale form submission, and resetting a live account to REQUESTED
    would remove access somebody is paying for. A previously DENIED
    account may request again — people's circumstances change, and
    silently discarding the second request would leave them with no way
    to ask."""
    normalized_email = _normalize_email(email)
    with transaction(conn):
        existing_row = conn.execute(
            "SELECT status FROM account_entitlements WHERE email = %s", (normalized_email,)
        ).fetchone()
        existing_status = _parse_status(existing_row["status"]) if existing_row else None
        if existing_status is not None and existing_status not in (AccessStatus.REQUESTED, AccessStatus.DENIED):
            raise EntitlementConflictError(
                f"access request refused: account already has status {existing_status.value!r}"
            )

        if existing_row is None:
            conn.execute(
                """
                INSERT INTO account_entitlements (
                    email, status, tier, requested_at, decided_at, decided_by, expires_at,
                    request_reason, request_organisation, request_role, updated_at
                ) VALUES (%s, %s, NULL, %s, NULL, NULL, NULL, %s, %s, %s, %s)
                """,
                (
                    normalized_email, AccessStatus.REQUESTED.value, now,
                    request.reason, request.organisation, request.role, now,
                ),
            )
        else:
            # A re-request clears the earlier decision rather than
            # leaving a denial stamped on a row that is pending again.
            conn.execute(
                """
                UPDATE account_entitlements
                SET status = %s, tier = NULL, requested_at = %s, decided_at = NULL, decided_by = NULL,
                    expires_at = NULL, request_reason = %s, request_organisation = %s, request_role = %s,
                    updated_at = %s
                WHERE email = %s
                """,
                (
                    AccessStatus.REQUESTED.value, now,
                    request.reason, request.organisation, request.role, now, normalized_email,
                ),
            )

        _append_event(conn, AccessEvent(
            email=normalized_email, from_status=existing_status, to_status=AccessStatus.REQUESTED,
            tier=None, at=now, actor=normalized_email, detail="access requested",
        ))

    entitlement = get_entitlement(conn, normalized_email)
    assert entitlement is not None  # just written inside the transaction above
    return entitlement


def set_status(
    conn: psycopg.Connection,
    email: str,
    status: AccessStatus,
    *,
    tier: AccessTier | None,
    actor: str,
    now: str,
    expires_at: str | None = None,
    detail: str = "",
    expected_status: AccessStatus | None = None,
) -> AccountEntitlement:
    """The single write path for every administrative decision —
    approve, activate, deny, revoke, expire.

    `expected_status`, when given, makes the write conditional on the
    row still being in that state, so two admins acting on the same
    queue cannot both apply a decision. The loser gets an
    EntitlementConflictError instead of a silent overwrite.

    Creates a row if none exists, which is how an admin grants access to
    somebody who never filled in the form (the owner, an advisor). That
    path still writes an event, so a grant with no request is visible as
    exactly that rather than indistinguishable from an approval."""
    normalized_email = _normalize_email(email)
    with transaction(conn):
        existing_row = conn.execute(
            "SELECT status FROM account_entitlements WHERE email = %s", (normalized_email,)
        ).fetchone()
        existing_status = _parse_status(existing_row["status"]) if existing_row else None

        if expected_status is not None and existing_status is not expected_status:
            raise EntitlementConflictError(
                f"expected status {expected_status.value!r}, found "
                f"{existing_status.value if existing_status else 'no entitlement'!r}"
            )

        if existing_row is None:
            conn.execute(
                """
                INSERT INTO account_entitlements (
                    email, status, tier, requested_at, decided_at, decided_by, expires_at,
                    request_reason, request_organisation, request_role, updated_at
                ) VALUES (%s, %s, %s, NULL, %s, %s, %s, NULL, NULL, NULL, %s)
                """,
                (
                    normalized_email, status.value, tier.value if tier else None,
                    now, actor, expires_at, now,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE account_entitlements
                SET status = %s, tier = %s, decided_at = %s, decided_by = %s, expires_at = %s, updated_at = %s
                WHERE email = %s
                """,
                (
                    status.value, tier.value if tier else None, now, actor,
                    expires_at, now, normalized_email,
                ),
            )

        _append_event(conn, AccessEvent(
            email=normalized_email, from_status=existing_status, to_status=status,
            tier=tier, at=now, actor=actor, detail=detail,
        ))

    entitlement = get_entitlement(conn, normalized_email)
    assert entitlement is not None  # just written inside the transaction above
    return entitlement
