"""Admin Users v1 (design/DECISIONS.md) — SQLite persistence for
`user_accounts` (schema.py's own V15). `record_sign_in` is the only
write path, called at most once per authenticated browser session from
app.py's own mandatory sign-in gate; `get_user`/`list_users` are the
read paths `src/ui/pages/admin_users.py` uses.

Upsert semantics for `record_sign_in`: a genuinely new (normalized)
email creates one row with `sign_in_count=1` and
`first_seen_at == last_seen_at == now`. An existing email increments
`sign_in_count` and updates `last_seen_at`; `display_name` may backfill
from None to a real value, but a real, already-stored name is never
overwritten with None — a later sign-in that doesn't carry a `name`
claim (or a provider that omits it) must not erase one already on file."""
from __future__ import annotations

import sqlite3

from src.data_access.state_db.connection import transaction
from src.models.user_account import UserAccount


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _row_to_account(row: sqlite3.Row) -> UserAccount:
    return UserAccount(
        email=row["email"],
        display_name=row["display_name"],
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        sign_in_count=row["sign_in_count"],
    )


def get_user(conn: sqlite3.Connection, email: str) -> UserAccount | None:
    row = conn.execute(
        "SELECT * FROM user_accounts WHERE email = ?", (_normalize_email(email),),
    ).fetchone()
    return _row_to_account(row) if row is not None else None


def list_users(conn: sqlite3.Connection, search: str | None = None) -> list[UserAccount]:
    """Most-recently-seen first. `search`, when non-blank, matches a
    case-insensitive substring of either email or display name — a
    NULL display_name never matches a non-empty search term."""
    normalized_search = (search or "").strip().lower()
    if normalized_search:
        pattern = f"%{normalized_search}%"
        rows = conn.execute(
            """
            SELECT * FROM user_accounts
            WHERE email LIKE ? OR lower(COALESCE(display_name, '')) LIKE ?
            ORDER BY last_seen_at DESC
            """,
            (pattern, pattern),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM user_accounts ORDER BY last_seen_at DESC").fetchall()
    return [_row_to_account(row) for row in rows]


def record_sign_in(conn: sqlite3.Connection, email: str, display_name: str | None, now: str) -> None:
    normalized_email = _normalize_email(email)
    with transaction(conn):
        row = conn.execute(
            "SELECT display_name FROM user_accounts WHERE email = ?", (normalized_email,),
        ).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO user_accounts (email, display_name, first_seen_at, last_seen_at, sign_in_count)
                VALUES (?, ?, ?, ?, 1)
                """,
                (normalized_email, display_name, now, now),
            )
            return
        merged_display_name = row["display_name"] or display_name
        conn.execute(
            """
            UPDATE user_accounts
            SET display_name = ?, last_seen_at = ?, sign_in_count = sign_in_count + 1
            WHERE email = ?
            """,
            (merged_display_name, now, normalized_email),
        )
