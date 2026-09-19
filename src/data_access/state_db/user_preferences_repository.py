"""SQLite persistence for `user_preferences` (schema.py's own V21) —
the signed-in account's saved theme choice. `set_theme_preference` is
the only write path (the sidebar/command-palette theme switch); an
unknown value is rejected before any SQL runs, and the table's own
CHECK constraint rejects it again at the database."""
from __future__ import annotations

import sqlite3

from src.data_access.state_db.connection import transaction
from src.models.user_preferences import UserPreferences, is_theme_preference


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def get_preferences(conn: sqlite3.Connection, email: str) -> UserPreferences | None:
    row = conn.execute(
        "SELECT email, theme_preference, updated_at FROM user_preferences WHERE email = ?", (_normalize_email(email),),
    ).fetchone()
    if row is None:
        return None
    return UserPreferences(email=row["email"], theme_preference=row["theme_preference"], updated_at=row["updated_at"])


def set_theme_preference(conn: sqlite3.Connection, email: str, theme_preference: str, now: str) -> None:
    if not is_theme_preference(theme_preference):
        raise ValueError(f"unknown theme preference: {theme_preference!r}")
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO user_preferences (email, theme_preference, updated_at) VALUES (?, ?, ?)
            ON CONFLICT (email) DO UPDATE SET theme_preference = excluded.theme_preference, updated_at = excluded.updated_at
            """,
            (_normalize_email(email), theme_preference, now),
        )
