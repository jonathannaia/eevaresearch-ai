"""The isolated Postgres counterpart to state_db/user_preferences_repository.py
(schema.py's own V23) — identical shape and upsert semantics,
independently implemented (no shared code, per this package's
no-dialect-abstraction constraint)."""
from __future__ import annotations

import psycopg

from src.data_access.postgres_state_db.connection import transaction
from src.models.user_preferences import UserPreferences, is_theme_preference


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def get_preferences(conn: psycopg.Connection, email: str) -> UserPreferences | None:
    """The read runs inside its own transaction and ends it, so the
    connection is never left idle in transaction holding a lock."""
    with transaction(conn):
        row = conn.execute(
            "SELECT email, theme_preference, updated_at FROM user_preferences WHERE email = %s", (_normalize_email(email),),
        ).fetchone()
    if row is None:
        return None
    return UserPreferences(email=row["email"], theme_preference=row["theme_preference"], updated_at=row["updated_at"])


def set_theme_preference(conn: psycopg.Connection, email: str, theme_preference: str, now: str) -> None:
    if not is_theme_preference(theme_preference):
        raise ValueError(f"unknown theme preference: {theme_preference!r}")
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO user_preferences (email, theme_preference, updated_at) VALUES (%s, %s, %s)
            ON CONFLICT (email) DO UPDATE SET theme_preference = EXCLUDED.theme_preference, updated_at = EXCLUDED.updated_at
            """,
            (_normalize_email(email), theme_preference, now),
        )
