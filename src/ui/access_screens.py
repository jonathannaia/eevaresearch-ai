"""Paid professional access, phase 1 — what somebody sees instead of
the app when they are not entitled.

Each denial reason gets its own screen, because they are not the same
message. "We are reviewing your request", "your trial has ended" and
"this account's access was removed" tell a person three different
things about what to do next, and collapsing them into one refusal is
how a product teaches people it is broken. The copy here is deliberately
plain: no apology theatre, no invented timeline, and no promise about a
reply that nobody has committed to keeping.

Identity comes only from the caller, which takes it from st.user —
never from a form field, so it cannot be changed by editing a widget.
The request form collects nothing that is not self-reported and
nothing that is used as an authorization input: the reason text exists
to inform a human decision.

Rendering is separated from persistence. `render_request_form` returns
the submitted AccessRequest (or None) and writes nothing; the caller
owns the repository, the clock and the error handling. That is what
makes these screens testable without a database and keeps a form
re-render from ever re-writing a row."""
from __future__ import annotations

import streamlit as st

from src.logic.account_access import AccessDecision, AccessReason
from src.models.account_access import AccessRequest

_MAX_REASON_CHARS = 1000
_MAX_SHORT_FIELD_CHARS = 200

#: Reasons where asking again is the right next step. Everything else
#: is either already in a queue or a decision somebody made on purpose,
#: and re-offering the form would invite people to spam it.
REQUESTABLE_REASONS: frozenset[AccessReason] = frozenset({
    AccessReason.NO_ENTITLEMENT,
    AccessReason.REQUEST_DENIED,
    AccessReason.ENTITLEMENT_EXPIRED,
})


def _title(text: str) -> None:
    st.markdown(f'<div class="er-page-title">{text}</div>', unsafe_allow_html=True)


def _muted(text: str) -> None:
    st.markdown(f'<div class="er-muted">{text}</div>', unsafe_allow_html=True)


def render_status_message(decision: AccessDecision) -> None:
    """The explanation half of the screen. Never renders a form."""
    if decision.reason is AccessReason.REQUEST_PENDING:
        _title("Access requested")
        st.info("Your request is with us. You will be able to sign in here once it is approved.")
        _muted("Nothing further is needed from you.")
        return

    if decision.reason is AccessReason.APPROVED_NOT_ACTIVATED:
        _title("Almost there")
        st.info("Your access has been approved but is not active yet.")
        _muted("Sign in again shortly, or contact us if this does not change.")
        return

    if decision.reason is AccessReason.ACCESS_REVOKED:
        _title("Access ended")
        st.warning("This account no longer has access to EevaResearch.")
        _muted("Contact us if you believe this is a mistake.")
        return

    if decision.reason is AccessReason.ENTITLEMENT_EXPIRED:
        _title("Your access has ended")
        tier_note = f" Your {decision.tier.value} access has lapsed." if decision.tier else ""
        st.warning(f"This account's access period is over.{tier_note}")
        _muted("You can ask for access again below.")
        return

    if decision.reason is AccessReason.REQUEST_DENIED:
        _title("Access not granted")
        st.warning("A previous request for this account was not approved.")
        _muted("You are welcome to ask again below, with any further detail.")
        return

    if decision.reason in (AccessReason.ENTITLEMENT_UNREADABLE, AccessReason.ENTITLEMENT_MALFORMED):
        # Deliberately does not say "you have no access": we do not know
        # that. Telling somebody their request was never received, when
        # in fact a database read failed, is a worse error than saying
        # nothing specific. Nothing technical is exposed either.
        _title("Access check unavailable")
        st.error("We could not confirm this account's access just now.")
        _muted("This is a problem on our side, not with your account. Please try again shortly.")
        return

    # NO_ENTITLEMENT, and the fallback for any reason added later
    # without its own screen — asking is always a safe next step.
    _title("Request access to EevaResearch")
    _muted(
        "EevaResearch is an evidence-first filing-intelligence tool for frontier-technology "
        "sectors. Access is granted account by account."
    )


def render_request_form(email: str, *, submitted_already: bool = False) -> AccessRequest | None:
    """Renders the request form and returns what was submitted, or None
    on any render where the button was not pressed or validation failed.

    Writes nothing and reads nothing. `submitted_already` lets the
    caller suppress the form after a successful write in the same
    session, so a rerun cannot resubmit."""
    if submitted_already:
        st.success("Request sent. You will be able to sign in here once it is approved.")
        return None

    with st.form("access_request_form", clear_on_submit=False):
        st.caption(f"Signing in as {email}")
        reason_raw = st.text_area(
            "What would you use EevaResearch for?",
            max_chars=_MAX_REASON_CHARS,
            placeholder="A sentence or two is plenty.",
        )
        organisation_raw = st.text_input(
            "Organisation (optional)", max_chars=_MAX_SHORT_FIELD_CHARS, placeholder="Company or fund"
        )
        role_raw = st.text_input(
            "Your role (optional)", max_chars=_MAX_SHORT_FIELD_CHARS, placeholder="e.g. analyst"
        )
        submitted = st.form_submit_button("Request access")

    if not submitted:
        return None

    reason = (reason_raw or "").strip()
    if not reason:
        # The one required field. Validated here rather than relying on
        # the widget, because an empty reason gives the reviewer nothing
        # to decide on and would sit in the queue unanswerable.
        st.error("Please tell us briefly what you would use it for.")
        return None

    return AccessRequest(
        reason=reason,
        organisation=(organisation_raw or "").strip() or None,
        role=(role_raw or "").strip() or None,
    )
