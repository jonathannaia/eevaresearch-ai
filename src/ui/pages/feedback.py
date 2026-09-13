"""Open-beta feedback (design/DECISIONS.md) — a short, authenticated,
signed-in-identity-only product-feedback form. Hidden-but-reachable
(app.py: visibility="hidden"), linked only from a small secondary CTA on
Dashboard — the full form is never embedded on Home or Dashboard
itself. Reachable only past app.py's own mandatory Google sign-in gate:
this page carries no guard of its own beyond that, because no registered
page is ever reached without it (see app.py's own module docstring —
_build_pages()/st.navigation() never run for an unauthenticated visitor).

Identity is taken exclusively from st.user — never a form field — so it
cannot be spoofed by editing a widget, the same convention app.py's own
sign-in-tracking hook already uses for src/models/user_account.py.
Upsert-on-email: a signed-in user who has already submitted sees their
prior answers pre-filled, and resubmitting replaces that one row rather
than creating a second (see src/data_access/state_db/
feedback_repository.py's own docstring).

This is a product-feedback pulse-check, not an access-request gate — it
does not affect anyone's ability to use the open beta, and it makes no
promise about a reply, a timeline, or how the feedback will be used
beyond product development."""
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from src.config.settings import Settings, get_settings
from src.data_access import backend_factory
from src.models.feedback_submission import (
    MAX_TRACKING_WORKFLOW_OTHER_LENGTH,
    MAX_WEEKLY_VALUE_FEEDBACK_LENGTH,
    FeedbackPrimaryInterest,
    FeedbackRole,
    FeedbackSubmission,
    FeedbackTrackingWorkflow,
)

_GENERIC_SAVE_ERROR = "Could not save your feedback. Please try again later."


def _validate_submission(
    email: str,
    display_name: str | None,
    role_value: str,
    workflow_value: str,
    workflow_other_raw: str,
    interest_value: str,
    weekly_value_feedback_raw: str,
    now: str,
) -> tuple[FeedbackSubmission | None, str | None]:
    """Pure: no Streamlit, no I/O — testable directly, without driving
    the page through AppTest. Returns (submission, None) on success, or
    (None, error_message) on failure. `email` must already be the
    signed-in identity — this function never trusts a caller-supplied
    email over it (see render() below, which never reads email from a
    widget at all)."""
    if not email:
        return None, _GENERIC_SAVE_ERROR

    try:
        role = FeedbackRole(role_value)
        tracking_workflow = FeedbackTrackingWorkflow(workflow_value)
        primary_interest = FeedbackPrimaryInterest(interest_value)
    except ValueError:
        return None, _GENERIC_SAVE_ERROR

    workflow_other = workflow_other_raw.strip()
    if tracking_workflow is FeedbackTrackingWorkflow.OTHER:
        if not workflow_other:
            return None, "Please tell us what you use."
        if len(workflow_other) > MAX_TRACKING_WORKFLOW_OTHER_LENGTH:
            return None, f"Please keep that under {MAX_TRACKING_WORKFLOW_OTHER_LENGTH} characters."
    else:
        # Reject a nonempty Other detail for a non-Other workflow rather
        # than persisting stale/inconsistent data — the form itself never
        # renders this field unless Other is selected, but this function
        # enforces it regardless of caller.
        workflow_other = None

    weekly_value_feedback = weekly_value_feedback_raw.strip()
    if len(weekly_value_feedback) > MAX_WEEKLY_VALUE_FEEDBACK_LENGTH:
        return None, f"Please keep that under {MAX_WEEKLY_VALUE_FEEDBACK_LENGTH} characters."

    submission = FeedbackSubmission(
        email=email,
        display_name=display_name,
        submitted_at=now,
        role=role,
        tracking_workflow=tracking_workflow,
        tracking_workflow_other=workflow_other,
        primary_interest=primary_interest,
        weekly_value_feedback=weekly_value_feedback or None,
    )
    return submission, None


def _save(settings: Settings, submission: FeedbackSubmission) -> bool:
    try:
        repository = backend_factory.get_feedback_repository(settings)
        repository.submit_feedback(
            email=submission.email,
            display_name=submission.display_name,
            role=submission.role,
            tracking_workflow=submission.tracking_workflow,
            tracking_workflow_other=submission.tracking_workflow_other,
            primary_interest=submission.primary_interest,
            weekly_value_feedback=submission.weekly_value_feedback,
            now=submission.submitted_at,
        )
    except Exception:  # noqa: BLE001 — never leak a raw connection/config error into the UI
        return False
    return True


def render() -> None:
    st.markdown('<div class="er-page-title">Help us build this right</div>', unsafe_allow_html=True)
    st.write(
        "Two minutes. This does not affect your access — the open beta is available "
        "to every signed-in Google account."
    )

    email = (st.user.get("email") or "").strip().lower()
    display_name = st.user.get("name")
    settings = get_settings()

    existing: FeedbackSubmission | None = None
    if email:
        try:
            existing = backend_factory.get_feedback_repository(settings).get_submission(email)
        except Exception:  # noqa: BLE001 — a read failure here must not block a first-time submission
            existing = None

    role_options = [choice.value for choice in FeedbackRole]
    workflow_options = [choice.value for choice in FeedbackTrackingWorkflow]
    interest_options = [choice.value for choice in FeedbackPrimaryInterest]

    role_index = role_options.index(existing.role.value) if existing else 0
    workflow_index = workflow_options.index(existing.tracking_workflow.value) if existing else 0
    interest_index = interest_options.index(existing.primary_interest.value) if existing else 0

    role_choice = st.selectbox("What best describes you?", role_options, index=role_index)
    workflow_choice = st.selectbox(
        "How do you currently track company filings and news?", workflow_options, index=workflow_index,
    )

    workflow_other_raw = ""
    if workflow_choice == FeedbackTrackingWorkflow.OTHER.value:
        workflow_other_raw = st.text_input(
            "What do you use?",
            value=(existing.tracking_workflow_other or "") if existing else "",
        )

    interest_choice = st.selectbox("What are you most interested in?", interest_options, index=interest_index)

    weekly_value_feedback_raw = st.text_area(
        "What would make this worth checking every week? (optional)",
        value=(existing.weekly_value_feedback or "") if existing else "",
    )

    button_label = "Update feedback" if existing else "Send feedback"
    if st.button(button_label):
        submission, error = _validate_submission(
            email, display_name, role_choice, workflow_choice, workflow_other_raw,
            interest_choice, weekly_value_feedback_raw, datetime.now(timezone.utc).isoformat(),
        )
        if error is not None:
            st.error(error)
            return
        if not _save(settings, submission):
            st.error(_GENERIC_SAVE_ERROR)
            return
        if existing is not None:
            st.success("Thanks — your feedback has been updated.")
        else:
            st.success("Thanks — this has been recorded.")
