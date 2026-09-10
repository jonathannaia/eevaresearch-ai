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
query, never sees the allowlist, its size, or any user data.

Shows exactly: email, display name, first seen, last seen, sign-in
count — the same minimal fields src/models/user_account.py's own model
carries. No IP address, device/browser fingerprint, page-view or event
history, role/status, or token/cookie/session data exists to show, by
construction — this page cannot render what the underlying table
doesn't have."""
from __future__ import annotations

import streamlit as st

from src.config.settings import get_settings
from src.data_access import backend_factory
from src.ui.ui import is_admin


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
        return

    if not users:
        st.write("No users match that search." if search else "No users yet.")
        return

    for account in users:
        with st.container(border=True, key=f"user-{account.email}"):
            st.markdown(f"**{account.display_name or account.email}**")
            if account.display_name:
                st.caption(account.email)
            st.write(
                f"First seen: {account.first_seen_at} · Last seen: {account.last_seen_at} · "
                f"Sign-ins: {account.sign_in_count}"
            )
