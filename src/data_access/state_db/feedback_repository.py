"""Open-beta feedback (design/DECISIONS.md) — SQLite persistence for
`feedback_submissions` (schema.py's own V17). `submit_feedback` is the
only write path, called from src/ui/pages/feedback.py's own submit
handler after server-side validation there; `get_submission`/
`list_submissions` are the read paths src/ui/pages/admin_users.py's
"Open-beta feedback" section uses.

Upsert semantics for `submit_feedback`: a genuinely new (normalized)
email creates one row. An existing email's row is fully replaced —
every field, including `submitted_at`, is overwritten with the new
submission — a signed-in user always has at most one row, always their
latest answer, never a second/stale row alongside it."""
from __future__ import annotations

import sqlite3

from src.data_access.state_db.connection import transaction
from src.models.feedback_submission import (
    FeedbackPrimaryInterest,
    FeedbackRole,
    FeedbackSubmission,
    FeedbackTrackingWorkflow,
)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _row_to_submission(row: sqlite3.Row) -> FeedbackSubmission:
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


def get_submission(conn: sqlite3.Connection, email: str) -> FeedbackSubmission | None:
    row = conn.execute(
        "SELECT * FROM feedback_submissions WHERE email = ?", (_normalize_email(email),),
    ).fetchone()
    return _row_to_submission(row) if row is not None else None


def list_submissions(conn: sqlite3.Connection) -> list[FeedbackSubmission]:
    """Most-recently-submitted first."""
    rows = conn.execute("SELECT * FROM feedback_submissions ORDER BY submitted_at DESC").fetchall()
    return [_row_to_submission(row) for row in rows]


def submit_feedback(
    conn: sqlite3.Connection,
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
