"""Admin Users v1 (design/DECISIONS.md) — the isolated Postgres
counterpart to state_db/user_account_repository.py, identical shape and
identical upsert semantics, independently implemented (no shared code,
per this package's existing no-dialect-abstraction constraint). See
that module's own docstring for the full design rationale."""
from __future__ import annotations

import psycopg

from src.data_access.postgres_state_db.connection import transaction
from src.models.user_account import UserAccount


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _row_to_account(row: dict) -> UserAccount:
    return UserAccount(
        email=row["email"],
        display_name=row["display_name"],
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        sign_in_count=row["sign_in_count"],
    )


def get_user(conn: psycopg.Connection, email: str) -> UserAccount | None:
    row = conn.execute(
        "SELECT * FROM user_accounts WHERE email = %s", (_normalize_email(email),),
    ).fetchone()
    return _row_to_account(row) if row is not None else None


def list_users(conn: psycopg.Connection, search: str | None = None) -> list[UserAccount]:
    """Most-recently-seen first. `search`, when non-blank, matches a
    case-insensitive substring of either email or display name — a
    NULL display_name never matches a non-empty search term."""
    normalized_search = (search or "").strip().lower()
    if normalized_search:
        pattern = f"%{normalized_search}%"
        rows = conn.execute(
            """
            SELECT * FROM user_accounts
            WHERE email LIKE %s OR lower(COALESCE(display_name, '')) LIKE %s
            ORDER BY last_seen_at DESC
            """,
            (pattern, pattern),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM user_accounts ORDER BY last_seen_at DESC").fetchall()
    return [_row_to_account(row) for row in rows]


def record_sign_in(conn: psycopg.Connection, email: str, display_name: str | None, now: str) -> None:
    normalized_email = _normalize_email(email)
    with transaction(conn):
        row = conn.execute(
            "SELECT display_name FROM user_accounts WHERE email = %s", (normalized_email,),
        ).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO user_accounts (email, display_name, first_seen_at, last_seen_at, sign_in_count)
                VALUES (%s, %s, %s, %s, 1)
                """,
                (normalized_email, display_name, now, now),
            )
            return
        merged_display_name = row["display_name"] or display_name
        conn.execute(
            """
            UPDATE user_accounts
            SET display_name = %s, last_seen_at = %s, sign_in_count = sign_in_count + 1
            WHERE email = %s
            """,
            (merged_display_name, now, normalized_email),
        )
