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
after-is_admin() discipline as the user list above."""
from __future__ import annotations

import html

import streamlit as st

from src.config.settings import get_settings
from src.data_access import backend_factory
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
