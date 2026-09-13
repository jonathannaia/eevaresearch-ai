"""Open-beta feedback (design/DECISIONS.md) — the isolated Postgres
counterpart to state_db/feedback_repository.py, identical shape and
identical upsert semantics, independently implemented (no shared code,
per this package's existing no-dialect-abstraction constraint). See
that module's own docstring for the full design rationale."""
from __future__ import annotations

import psycopg

from src.data_access.postgres_state_db.connection import transaction
from src.models.feedback_submission import (
    FeedbackPrimaryInterest,
    FeedbackRole,
    FeedbackSubmission,
    FeedbackTrackingWorkflow,
)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _row_to_submission(row: dict) -> FeedbackSubmission:
    return FeedbackSubmission(
        email=row["email"],
        display_name=row["display_name"],
        submitted_at=row["submitted_at"],
        role=FeedbackRole(row["role"]),
        tracking_workflow=FeedbackTrackingWorkflow(row["tracking_workflow"]),
        tracking_workflow_other=row["tracking_workflow_other"],
        primary_interest=FeedbackPrimaryInterest(row["primary_interest"]),
        weekly_value_feedback=row["weekly_value_feedback"],
    )


def get_submission(conn: psycopg.Connection, email: str) -> FeedbackSubmission | None:
    row = conn.execute(
        "SELECT * FROM feedback_submissions WHERE email = %s", (_normalize_email(email),),
    ).fetchone()
    return _row_to_submission(row) if row is not None else None


def list_submissions(conn: psycopg.Connection) -> list[FeedbackSubmission]:
    """Most-recently-submitted first."""
    rows = conn.execute("SELECT * FROM feedback_submissions ORDER BY submitted_at DESC").fetchall()
    return [_row_to_submission(row) for row in rows]


def submit_feedback(
    conn: psycopg.Connection,
    email: str,
    display_name: str | None,
    role: FeedbackRole,
    tracking_workflow: FeedbackTrackingWorkflow,
    tracking_workflow_other: str | None,
    primary_interest: FeedbackPrimaryInterest,
    weekly_value_feedback: str | None,
    now: str,
) -> None:
    normalized_email = _normalize_email(email)
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO feedback_submissions (
                email, display_name, submitted_at, role, tracking_workflow,
                tracking_workflow_other, primary_interest, weekly_value_feedback
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (email) DO UPDATE SET
                display_name = excluded.display_name,
                submitted_at = excluded.submitted_at,
                role = excluded.role,
                tracking_workflow = excluded.tracking_workflow,
                tracking_workflow_other = excluded.tracking_workflow_other,
                primary_interest = excluded.primary_interest,
                weekly_value_feedback = excluded.weekly_value_feedback
            """,
            (
                normalized_email, display_name, now, role.value, tracking_workflow.value,
                tracking_workflow_other, primary_interest.value, weekly_value_feedback,
            ),
        )
