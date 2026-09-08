"""SQLite connection lifecycle + transaction helper. Standard-library
`sqlite3` only — no new dependency. Every connection this module hands
out has foreign-key enforcement turned on explicitly, since SQLite does
not persist that setting in the database file itself; it must be set on
every connection."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def connect(db_path: Path) -> sqlite3.Connection:
    """Opens (creating if needed) a SQLite database at `db_path`, with
    foreign-key enforcement on and row access by column name. Does not
    create or run any schema — see schema.migrate() for that, called
    separately so a caller can inspect a connection before deciding to
    migrate it (used by the idempotent-migration tests)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def connect_in_memory() -> sqlite3.Connection:
    """A fresh, private in-memory database — used by tests that don't
    need a real file on disk. Never shared across connections (SQLite's
    ':memory:' database is private to the connection that opened it)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One logical write operation per transaction. Commits on clean
    exit; rolls back and re-raises on any exception — a write must fail
    clearly, never partially apply and never silently drop data (this
    repo's existing fail-safe discipline, extended to the database
    backend). Nesting is not supported — each call is one top-level
    transaction, matching every write operation in this phase being a
    single logical unit (one candidate upsert, one review update, one
    migration step)."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
