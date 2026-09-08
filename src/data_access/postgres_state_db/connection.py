"""Postgres connection lifecycle + transaction helper for the isolated
Durable-State Phase 4B local-test package. Independent from
src/data_access/state_db/connection.py — no import relationship, no
shared code, per this phase's explicit no-dialect-abstraction
constraint. Uses Psycopg v3 (see requirements.txt) with dict-row access
configured at connect time so every `row["col"]` call in this package's
repositories mirrors sqlite3.Row's own name-based access unchanged."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row


def connect(dsn: str) -> psycopg.Connection:
    """Opens a connection to `dsn` with dict-style row access. Runs no
    schema of its own — see schema.migrate() for that, called
    separately, matching state_db/connection.py's own connect()/
    migrate() separation. No connect_in_memory() equivalent exists for
    this package: Postgres has no private-to-one-connection in-memory
    database the way SQLite's ':memory:' does, so every test against
    this package uses a real, disposable, local Postgres instance
    instead (see design/DECISIONS.md's Phase 4B-0/4B-1 records)."""
    return psycopg.connect(dsn, row_factory=dict_row)


@contextmanager
def transaction(conn: psycopg.Connection) -> Iterator[psycopg.Connection]:
    """One logical write operation per transaction — the identical
    external contract to state_db/connection.py's transaction() (commit
    on clean exit, rollback and re-raise on exception), independently
    implemented and independently verified against real Psycopg
    behavior rather than assumed to transfer for free from the SQLite
    version. Nesting is not supported — each call is one top-level
    transaction, matching every write operation in this package being a
    single logical unit (one candidate upsert, one review update, one
    migration step).

    Psycopg connections default to autocommit=False, so a transaction is
    already implicitly open the moment this connection is used; this
    context manager's role is only to make the commit/rollback boundary
    explicit and exception-safe — the same role state_db/connection.py's
    transaction() plays for SQLite, not a different mechanism."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
