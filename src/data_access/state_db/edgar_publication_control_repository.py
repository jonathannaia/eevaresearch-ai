"""The autonomous EDGAR 424B5 publication control — sqlite3.

One row (id = 1), restrict-only, read by the Radar worker before any
autonomous publication. It can only take publication away: the gate in
src/logic/edgar_publication_gate.py independently requires the cohort
match and the deployment setting, so flipping this row on is never
sufficient to publish.

Every failure resolves to DISABLED. An absent row, a malformed value, a
closed connection, a missing table on a database that has not migrated
yet — all of them return False. That is the whole point of reading it
through this function rather than inline: there is exactly one place
where a database problem could be mistaken for permission, and here it
cannot be.

Independently implemented per backend by the same no-dialect-abstraction
convention the rest of these packages follow."""
from __future__ import annotations

from dataclasses import dataclass

import sqlite3


@dataclass(frozen=True)
class EdgarPublicationControl:
    enabled: bool
    reason: str | None
    updated_by: str | None
    updated_at: str


def get_control(conn) -> EdgarPublicationControl | None:
    """The raw row, or None when absent. Callers deciding whether to
    publish should use is_publication_enabled() instead — this is for
    operators and tests that need to see what is actually stored."""
    row = conn.execute(
        "SELECT enabled, reason, updated_by, updated_at FROM edgar_publication_control WHERE id = 1"
    ).fetchone()
    if row is None:
        return None
    return EdgarPublicationControl(
        enabled=bool(row["enabled"]),
        reason=row["reason"],
        updated_by=row["updated_by"],
        updated_at=row["updated_at"],
    )


def is_publication_enabled(conn) -> bool:
    """Fail-closed. Any exception -- no table, no row, closed
    connection, unreadable value -- is an answer of False, never a
    raise, because a caller that has to wrap this in its own try is a
    caller that can get the direction wrong."""
    try:
        control = get_control(conn)
    except Exception:  # noqa: BLE001 - a control that cannot be read is a control that is off
        return False
    return bool(control and control.enabled)


def close(conn) -> None:
    """Closes an OWNED connection. Idempotent and exception-safe — see
    the factory wrapper's own close() for the ownership rule."""
    try:
        conn.close()
    except Exception:  # noqa: BLE001 - cleanup never changes the outcome
        return


def set_control(conn, *, enabled: bool, reason: str | None, updated_by: str | None, now: str) -> None:
    """Upserts the single row. No commit here -- the caller owns the
    transaction, as everywhere else in this package."""
    conn.execute(
        """
        INSERT INTO edgar_publication_control (id, enabled, reason, updated_by, updated_at)
        VALUES (1, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            enabled = excluded.enabled,
            reason = excluded.reason,
            updated_by = excluded.updated_by,
            updated_at = excluded.updated_at
        """,
        (enabled, reason, updated_by, now),
    )
