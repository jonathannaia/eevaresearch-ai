"""Paid professional access, phase 1 — SQLite persistence for
`account_entitlements` and `account_access_events` (schema.py's own
V26). Direct sibling of
test_account_entitlement_repository_postgres.py, which runs the same
behaviour against the real local disposable container.

The invariants under test, in order of how much they matter:

1. Absence means no access. No read path invents a row.
2. Every status change leaves an event, in the same transaction.
3. A write refuses to clobber a decision somebody already made.
"""
from __future__ import annotations

import pytest

from src.data_access.state_db import connection, schema
from src.data_access.state_db.account_entitlement_repository import (
    get_entitlement,
    list_entitlements,
    list_events,
    request_access,
    set_status,
)
from src.models.account_access import (
    AccessRequest,
    AccessStatus,
    AccessTier,
    EntitlementConflictError,
)

_T1 = "2026-06-01T10:00:00+00:00"
_T2 = "2026-06-02T10:00:00+00:00"
_T3 = "2026-06-03T10:00:00+00:00"
_USER = "analyst@example.test"
_ADMIN = "owner@example.test"


def _conn():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    return conn


def _request(reason: str = "Tracking Korean memory capex disclosures.") -> AccessRequest:
    return AccessRequest(reason=reason, organisation="Example Capital", role="analyst")


# --- 1. absence means no access --------------------------------------------

def test_an_unknown_account_has_no_entitlement():
    assert get_entitlement(_conn(), "nobody@example.test") is None


def test_reading_an_unknown_account_does_not_create_a_row():
    """A read that writes would hand everyone who ever signed in a row,
    and a row is the thing access is granted from."""
    conn = _conn()
    get_entitlement(conn, "nobody@example.test")
    assert list_entitlements(conn) == []


def test_an_empty_status_filter_returns_nothing_not_everything():
    """A caller that computed an empty filter must not get the whole
    table. The failure mode is a review queue silently listing every
    account as pending."""
    conn = _conn()
    request_access(conn, _USER, _request(), _T1)
    assert list_entitlements(conn, statuses=()) == []
    assert len(list_entitlements(conn, statuses=None)) == 1


# --- 2. requesting ---------------------------------------------------------

def test_a_request_creates_a_pending_entitlement_with_no_tier():
    conn = _conn()
    entitlement = request_access(conn, "Analyst@Example.Test", _request(), _T1)

    assert entitlement.email == _USER, "email is normalized"
    assert entitlement.status is AccessStatus.REQUESTED
    assert entitlement.tier is None, "a pending request must not look like a trial"
    assert entitlement.requested_at == _T1
    assert entitlement.decided_at is None and entitlement.decided_by is None
    assert entitlement.expires_at is None
    assert entitlement.request is not None
    assert entitlement.request.reason == _request().reason
    assert entitlement.request.organisation == "Example Capital"
    assert entitlement.request.role == "analyst"


def test_optional_request_fields_round_trip_as_none():
    conn = _conn()
    entitlement = request_access(conn, _USER, AccessRequest(reason="Evaluating."), _T1)
    assert entitlement.request.organisation is None and entitlement.request.role is None


def test_a_request_over_an_active_entitlement_is_refused():
    """Resetting a live account to REQUESTED would remove access
    somebody may be paying for. A stale form submission must not be
    able to do that."""
    conn = _conn()
    request_access(conn, _USER, _request(), _T1)
    set_status(conn, _USER, AccessStatus.ACTIVE, tier=AccessTier.PAID, actor=_ADMIN, now=_T2)

    with pytest.raises(EntitlementConflictError):
        request_access(conn, _USER, _request(), _T3)

    assert get_entitlement(conn, _USER).status is AccessStatus.ACTIVE, "unchanged"


def test_a_denied_account_may_request_again_and_the_denial_is_cleared():
    """People's circumstances change. Silently discarding the second
    request would leave them with no way to ask."""
    conn = _conn()
    request_access(conn, _USER, _request(), _T1)
    set_status(conn, _USER, AccessStatus.DENIED, tier=None, actor=_ADMIN, now=_T2)

    again = request_access(conn, _USER, _request("Now with a named fund."), _T3)
    assert again.status is AccessStatus.REQUESTED
    assert again.decided_at is None and again.decided_by is None, "the stale denial is cleared"
    assert again.request.reason == "Now with a named fund."


def test_re_requesting_while_pending_replaces_the_details_without_a_second_row():
    conn = _conn()
    request_access(conn, _USER, _request("First attempt."), _T1)
    request_access(conn, _USER, _request("More detail."), _T2)

    assert len(list_entitlements(conn)) == 1
    assert get_entitlement(conn, _USER).request.reason == "More detail."


# --- 3. deciding -----------------------------------------------------------

def test_approving_sets_tier_decider_and_expiry():
    conn = _conn()
    request_access(conn, _USER, _request(), _T1)
    entitlement = set_status(
        conn, _USER, AccessStatus.ACTIVE, tier=AccessTier.TRIAL, actor=_ADMIN,
        now=_T2, expires_at="2026-06-16T10:00:00+00:00", detail="approved",
    )
    assert entitlement.status is AccessStatus.ACTIVE
    assert entitlement.tier is AccessTier.TRIAL
    assert entitlement.decided_by == _ADMIN and entitlement.decided_at == _T2
    assert entitlement.expires_at == "2026-06-16T10:00:00+00:00"
    assert entitlement.requested_at == _T1, "the original request time survives the decision"


def test_an_admin_can_grant_access_to_somebody_who_never_asked():
    """The owner, an advisor, press. The resulting row has no request
    attached, which is how a direct grant stays distinguishable from an
    approval."""
    conn = _conn()
    entitlement = set_status(
        conn, "advisor@example.test", AccessStatus.ACTIVE, tier=AccessTier.COMPLIMENTARY,
        actor=_ADMIN, now=_T1, detail="advisor",
    )
    assert entitlement.status is AccessStatus.ACTIVE
    assert entitlement.request is None and entitlement.requested_at is None


def test_revoking_clears_the_tier():
    conn = _conn()
    set_status(conn, _USER, AccessStatus.ACTIVE, tier=AccessTier.PAID, actor=_ADMIN, now=_T1)
    revoked = set_status(conn, _USER, AccessStatus.REVOKED, tier=None, actor=_ADMIN, now=_T2)
    assert revoked.status is AccessStatus.REVOKED and revoked.tier is None


# --- 4. concurrent decisions ----------------------------------------------

def test_a_conditional_write_loses_cleanly_when_the_status_moved():
    """Two admins working the same queue. The second must see a conflict
    rather than silently overwriting the first — re-approving over a
    revocation would undo a deliberate removal."""
    conn = _conn()
    request_access(conn, _USER, _request(), _T1)
    set_status(
        conn, _USER, AccessStatus.ACTIVE, tier=AccessTier.PAID, actor=_ADMIN, now=_T2,
        expected_status=AccessStatus.REQUESTED,
    )

    with pytest.raises(EntitlementConflictError):
        set_status(
            conn, _USER, AccessStatus.DENIED, tier=None, actor="second@example.test", now=_T3,
            expected_status=AccessStatus.REQUESTED,
        )
    assert get_entitlement(conn, _USER).status is AccessStatus.ACTIVE


def test_a_conditional_write_against_a_missing_row_is_refused():
    conn = _conn()
    with pytest.raises(EntitlementConflictError):
        set_status(
            conn, _USER, AccessStatus.ACTIVE, tier=AccessTier.PAID, actor=_ADMIN, now=_T1,
            expected_status=AccessStatus.REQUESTED,
        )
    assert get_entitlement(conn, _USER) is None


def test_a_refused_write_leaves_no_event_behind():
    """A conflict must not leave a half-written audit trail implying
    something happened."""
    conn = _conn()
    with pytest.raises(EntitlementConflictError):
        set_status(
            conn, _USER, AccessStatus.ACTIVE, tier=AccessTier.PAID, actor=_ADMIN, now=_T1,
            expected_status=AccessStatus.REQUESTED,
        )
    assert list_events(conn, _USER) == []


# --- 5. the audit trail ---------------------------------------------------

def test_every_status_change_appends_one_event_in_order():
    conn = _conn()
    request_access(conn, _USER, _request(), _T1)
    set_status(conn, _USER, AccessStatus.ACTIVE, tier=AccessTier.TRIAL, actor=_ADMIN, now=_T2, detail="approved")
    set_status(conn, _USER, AccessStatus.REVOKED, tier=None, actor=_ADMIN, now=_T3, detail="left the fund")

    events = list_events(conn, _USER)
    assert [(e.from_status, e.to_status) for e in events] == [
        (None, AccessStatus.REQUESTED),
        (AccessStatus.REQUESTED, AccessStatus.ACTIVE),
        (AccessStatus.ACTIVE, AccessStatus.REVOKED),
    ]
    assert [e.at for e in events] == [_T1, _T2, _T3], "oldest first — a history reads forwards"
    assert events[0].actor == _USER, "the requester is the actor on their own request"
    assert events[1].actor == _ADMIN and events[1].tier is AccessTier.TRIAL
    assert events[2].detail == "left the fund"


def test_an_entitlement_can_never_exist_without_an_event():
    """The guarantee that makes "why does this account have access"
    answerable from the record rather than reconstructed."""
    conn = _conn()
    request_access(conn, _USER, _request(), _T1)
    set_status(conn, "advisor@example.test", AccessStatus.ACTIVE, tier=AccessTier.COMPLIMENTARY, actor=_ADMIN, now=_T2)

    for entitlement in list_entitlements(conn):
        assert list_events(conn, entitlement.email), entitlement.email


def test_events_are_scoped_to_one_account():
    conn = _conn()
    request_access(conn, _USER, _request(), _T1)
    request_access(conn, "other@example.test", _request(), _T2)
    assert {e.email for e in list_events(conn, _USER)} == {_USER}


# --- 6. unrecognized stored values ----------------------------------------

def test_an_unrecognized_stored_status_raises_rather_than_defaulting():
    """Access must never be granted because a value failed to parse. The
    caller's own failure path decides, and in the gate that path is
    denial."""
    conn = _conn()
    request_access(conn, _USER, _request(), _T1)
    conn.execute("UPDATE account_entitlements SET status = 'superuser' WHERE email = ?", (_USER,))
    conn.commit()

    with pytest.raises(ValueError, match="unrecognized access status"):
        get_entitlement(conn, _USER)


def test_an_unrecognized_stored_tier_raises():
    conn = _conn()
    set_status(conn, _USER, AccessStatus.ACTIVE, tier=AccessTier.PAID, actor=_ADMIN, now=_T1)
    conn.execute("UPDATE account_entitlements SET tier = 'unlimited' WHERE email = ?", (_USER,))
    conn.commit()

    with pytest.raises(ValueError, match="unrecognized access tier"):
        get_entitlement(conn, _USER)


# --- 7. listing -----------------------------------------------------------

def test_the_review_queue_lists_only_pending_requests():
    conn = _conn()
    request_access(conn, _USER, _request(), _T1)
    request_access(conn, "pending2@example.test", _request(), _T2)
    set_status(conn, _USER, AccessStatus.ACTIVE, tier=AccessTier.PAID, actor=_ADMIN, now=_T3)

    pending = list_entitlements(conn, statuses=(AccessStatus.REQUESTED,))
    assert [e.email for e in pending] == ["pending2@example.test"]


def test_entitlements_list_most_recently_updated_first():
    conn = _conn()
    request_access(conn, "first@example.test", _request(), _T1)
    request_access(conn, "second@example.test", _request(), _T2)
    assert [e.email for e in list_entitlements(conn)] == ["second@example.test", "first@example.test"]
