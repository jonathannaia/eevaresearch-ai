"""Admin Users v1 (design/DECISIONS.md) — owner-only, read-only list of
authenticated accounts that have signed in to EevaResearch, for
private-beta operations only (not a public analytics product). Hidden-
but-reachable (app.py: visibility="hidden", never in PRIMARY_NAV/
SYSTEM_NAV/HIDDEN_FROM_NAV) — src/ui/ui.py:render_sidebar() adds a
manual link only for an admin, but that link's absence is not the
access boundary.

The real boundary is this page's own is_admin() check below, run
before any repository is constructed or any user row is read — a
signed-in non-admin, including one who reaches this page by a direct/
deep URL, sees only a generic "Access denied." and never triggers a
query, never sees the allowlist, its size, or any user data. This one
check guards both sections of this page — the user list and the
open-beta feedback section below — since both are read here, after the
same single guard.

User list shows exactly: email, display name, first seen, last seen,
sign-in count — the same minimal fields src/models/user_account.py's
own model carries. No IP address, device/browser fingerprint, page-view
or event history, role/status, or token/cookie/session data exists to
show, by construction — this page cannot render what the underlying
table doesn't have.

Open-beta feedback section (design/DECISIONS.md) — the admin view of
src/models/feedback_submission.py's own minimal record; see
src/ui/pages/feedback.py for the submission side. Same construction-
after-is_admin() discipline as the user list above.

Access requests section (paid professional access, phase 1) — the
approve/deny queue for src/models/account_access.py's entitlements, and
the only place in the product where access is granted or removed. Same
single is_admin() boundary as the two sections above: no repository is
constructed and no entitlement is read until it passes.

This section is the operational counterpart to the user list rather
than an extension of it. The user list says who signed in and holds no
status by construction; entitlements say who may, and live in their own
table for exactly that reason. Both are read here because an
administrator needs both to make a decision.

Every decision is conditional on the status the page was rendered
with, so two administrators working the same queue cannot both apply a
decision — the second sees a conflict instead of silently overwriting
the first. The actor on every write is the signed-in admin's own email
from st.user, never a form value."""
from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone

import streamlit as st

from src.config.settings import get_settings
from src.data_access import backend_factory
from src.models.account_access import AccessStatus, AccessTier, EntitlementConflictError
from src.ui.ui import is_admin


def _esc(value: object) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


def render() -> None:
    st.markdown('<div class="er-page-title">Admin — Users</div>', unsafe_allow_html=True)

    if not is_admin():
        st.error("Access denied.")
        return

    st.markdown(
        '<div class="er-muted">Accounts that have signed in via Google OIDC — private-beta '
        "operations only, not a public analytics view.</div>",
        unsafe_allow_html=True,
    )

    search = st.text_input("Search by email or name", value="", placeholder="e.g. name@example.com")

    # Constructed here, only after the is_admin() check above passes —
    # not via get_repositories()/AppContext, so no other page's ordinary
    # get_repositories() call ever constructs a UserAccount repository
    # it doesn't need (see src/data_access/container.py's own comment).
    try:
        repository = backend_factory.get_user_account_repository(get_settings())
        users = repository.list_users(search=search or None)
    except Exception:  # noqa: BLE001 — never leak a raw connection/config error into the UI
        st.info("Could not read the user list. Please try again later.")
        users = None

    if users is not None:
        if not users:
            st.write("No users match that search." if search else "No users yet.")
        else:
            for account in users:
                with st.container(border=True, key=f"user-{account.email}"):
                    st.markdown(f"**{account.display_name or account.email}**")
                    if account.display_name:
                        st.caption(account.email)
                    st.write(
                        f"First seen: {account.first_seen_at} · Last seen: {account.last_seen_at} · "
                        f"Sign-ins: {account.sign_in_count}"
                    )

    _render_access_requests(get_settings())

    st.divider()
    st.markdown('<div class="er-page-title" style="font-size:1.1rem;">Open-beta feedback</div>', unsafe_allow_html=True)

    # Constructed here, only after the same is_admin() check above —
    # never via get_repositories()/AppContext, same isolation discipline
    # as the user-account repository above.
    try:
        feedback_repository = backend_factory.get_feedback_repository(get_settings())
        submissions = feedback_repository.list_submissions()
    except Exception:  # noqa: BLE001 — never leak a raw connection/config error into the UI
        st.info("Could not read feedback submissions. Please try again later.")
        return

    if not submissions:
        st.write("No feedback submitted yet.")
        return

    for submission in submissions:
        with st.container(border=True, key=f"feedback-{submission.email}"):
            st.markdown(f"**{_esc(submission.display_name or submission.email)}**")
            if submission.display_name:
                st.caption(submission.email)
            st.write(f"Submitted: {submission.submitted_at}")
            st.write(f"Role: {_esc(submission.role.value)} · Primary interest: {_esc(submission.primary_interest.value)}")
            workflow_line = f"Tracking workflow: {_esc(submission.tracking_workflow.value)}"
            if submission.tracking_workflow_other:
                workflow_line += f" — {_esc(submission.tracking_workflow_other)}"
            st.write(workflow_line)
            if submission.weekly_value_feedback:
                st.write(f"What would make this worth checking every week: {_esc(submission.weekly_value_feedback)}")


def _format_entitlement_line(entitlement) -> str:
    parts = [f"Status: **{entitlement.status.value}**"]
    if entitlement.tier:
        parts.append(f"Tier: {entitlement.tier.value}")
    if entitlement.expires_at:
        parts.append(f"Expires: {entitlement.expires_at}")
    if entitlement.decided_by:
        parts.append(f"Decided by: {_esc(entitlement.decided_by)}")
    return " · ".join(parts)


def _render_access_requests(settings) -> None:
    """Pending access requests, with the approve/deny decision.

    Approving moves the row straight to ACTIVE. APPROVED exists in the
    model for the state between "may buy" and "has bought", which has no
    meaning until billing does — so using it here would leave people
    approved and still locked out.

    Every decision is passed `expected_status=REQUESTED`, so two admins
    working the same queue cannot both apply a decision: the second gets
    a visible conflict instead of silently overwriting the first. The
    actor recorded is the signed-in admin's own email, taken from
    st.user and never from a form field."""
    st.divider()
    st.markdown('<div class="er-page-title" style="font-size:1.1rem;">Access requests</div>', unsafe_allow_html=True)

    actor = (st.user.get("email") or "").strip().lower()

    try:
        repository = backend_factory.get_account_entitlement_repository(settings)
        pending = repository.list_entitlements(statuses=(AccessStatus.REQUESTED,))
    except Exception:  # noqa: BLE001 — never leak a raw connection/config error into the UI
        st.info("Could not read access requests. Please try again later.")
        return

    _render_direct_grant(settings, repository, actor)

    if not pending:
        st.write("No pending access requests.")
    for entitlement in pending:
        with st.container(border=True, key=f"access-request-{entitlement.email}"):
            st.markdown(f"**{_esc(entitlement.email)}**")
            if entitlement.request:
                detail_bits = [
                    bit for bit in (
                        _esc(entitlement.request.organisation),
                        _esc(entitlement.request.role),
                    ) if bit
                ]
                if detail_bits:
                    st.caption(" · ".join(detail_bits))
                st.write(_esc(entitlement.request.reason))
            else:
                st.caption("No request details on file.")
            st.caption(f"Requested: {entitlement.requested_at}")

            tier_label = st.selectbox(
                "Grant",
                options=[AccessTier.TRIAL.value, AccessTier.PAID.value, AccessTier.COMPLIMENTARY.value],
                key=f"tier-{entitlement.email}",
                help=f"A trial expires after {settings.access_trial_days} days. Paid and complimentary do not expire.",
            )
            approve_col, deny_col = st.columns(2)
            approve = approve_col.button("Approve", key=f"approve-{entitlement.email}", type="primary")
            deny = deny_col.button("Deny", key=f"deny-{entitlement.email}")

            if not (approve or deny):
                continue

            now = datetime.now(timezone.utc)
            tier = AccessTier(tier_label)
            # Only a trial gets an expiry. Giving paid access one would
            # cut somebody off mid-subscription on a date nobody agreed
            # to; billing will set its own period end later.
            expires_at = (
                (now + timedelta(days=settings.access_trial_days)).isoformat()
                if approve and tier is AccessTier.TRIAL else None
            )
            try:
                repository.set_status(
                    email=entitlement.email,
                    status=AccessStatus.ACTIVE if approve else AccessStatus.DENIED,
                    tier=tier if approve else None,
                    actor=actor,
                    now=now.isoformat(),
                    expires_at=expires_at,
                    detail=f"{'approved' if approve else 'denied'} from admin users page",
                    expected_status=AccessStatus.REQUESTED,
                )
            except EntitlementConflictError:
                # Somebody else decided this one first. Say so plainly
                # rather than reporting a success that did not happen.
                st.warning("This request was already decided by someone else. Reloading.")
            except Exception:  # noqa: BLE001 — never leak a raw storage error into the UI
                st.error("Could not record that decision. Please try again.")
            else:
                st.success(f"{'Approved' if approve else 'Denied'} {entitlement.email}.")
            st.rerun()

    st.markdown('<div class="er-muted" style="margin-top:0.75rem;">All entitlements</div>', unsafe_allow_html=True)
    try:
        everyone = repository.list_entitlements()
    except Exception:  # noqa: BLE001 — never leak a raw connection/config error into the UI
        st.info("Could not read the entitlement list. Please try again later.")
        return

    if not everyone:
        st.write("No entitlements yet.")
        return

    for entitlement in everyone:
        with st.container(border=True, key=f"entitlement-{entitlement.email}"):
            st.markdown(f"**{_esc(entitlement.email)}**")
            st.write(_format_entitlement_line(entitlement))
            if entitlement.status is AccessStatus.ACTIVE:
                if st.button("Revoke", key=f"revoke-{entitlement.email}"):
                    try:
                        repository.set_status(
                            email=entitlement.email, status=AccessStatus.REVOKED, tier=None,
                            actor=actor, now=datetime.now(timezone.utc).isoformat(),
                            detail="revoked from admin users page",
                            expected_status=AccessStatus.ACTIVE,
                        )
                    except EntitlementConflictError:
                        st.warning("This entitlement changed since the page loaded. Reloading.")
                    except Exception:  # noqa: BLE001 — never leak a raw storage error into the UI
                        st.error("Could not revoke that entitlement. Please try again.")
                    else:
                        st.success(f"Revoked {entitlement.email}.")
                    st.rerun()


def _render_direct_grant(settings, repository, actor: str) -> None:
    """Granting access to an account that never filled in the form.

    Needed for more than convenience: the rollout for enforcement is
    migrate, backfill, verify, enable, and the BACKFILL step is exactly
    this. Every existing open-beta account has no entitlement row, and
    the approve/deny queue above only shows accounts that asked. Without
    this form, backfilling would mean running SQL by hand against
    production — so enabling enforcement would be gated on shell access
    to the database, which is the worst possible dependency for the one
    step that decides whether everybody keeps working.

    The resulting row carries no request, which is how a direct grant
    stays distinguishable from an approval in the audit trail."""
    with st.expander("Grant access directly", expanded=False):
        st.caption(
            "For an account that has not asked — you, an advisor, or an existing user being "
            "backfilled before access enforcement is switched on."
        )
        with st.form("direct_grant_form", clear_on_submit=True):
            email_raw = st.text_input("Email", placeholder="name@example.com")
            tier_label = st.selectbox(
                "Tier",
                options=[AccessTier.COMPLIMENTARY.value, AccessTier.TRIAL.value, AccessTier.PAID.value],
                help=f"A trial expires after {settings.access_trial_days} days. Paid and complimentary do not expire.",
            )
            note = st.text_input("Note (recorded in the audit trail)", placeholder="e.g. existing beta user")
            granted = st.form_submit_button("Grant access")

        if not granted:
            return

        email = (email_raw or "").strip().lower()
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            # Not an attempt at real address validation — only enough to
            # stop an obvious typo becoming an entitlement row nobody
            # can sign in as, which would look granted and never work.
            st.error("Enter a full email address.")
            return

        tier = AccessTier(tier_label)
        now = datetime.now(timezone.utc)
        expires_at = (
            (now + timedelta(days=settings.access_trial_days)).isoformat()
            if tier is AccessTier.TRIAL else None
        )
        try:
            repository.set_status(
                email=email, status=AccessStatus.ACTIVE, tier=tier, actor=actor,
                now=now.isoformat(), expires_at=expires_at,
                detail=(note or "").strip() or "granted directly from admin users page",
            )
        except Exception:  # noqa: BLE001 — never leak a raw storage error into the UI
            st.error("Could not grant access. Please try again.")
        else:
            st.success(f"Granted {tier.value} access to {email}.")
            st.rerun()
