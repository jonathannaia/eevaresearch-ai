"""Shadow mode writes the agent's own tables and nothing else — enforced,
not merely intended.

Blocker E3 removed the candidate and public-store writes from the
model-driving session, so in this release there is no code path from a
session to a reader-visible row. That is the primary defence, and it is
a structural one. This module is the second: a check that fails loudly if
a future change re-wires such a path, and a connection-level guard that
makes the property testable as a fact about the database rather than as a
fact about the source tree.

`AGENT_TABLE_PREFIX` is the whole rule. A table the agent may write is
one this release created (Postgres V24 / SQLite V22); everything else —
candidates, candidate status, reviewed_at, published_by, state
transitions, Signals inputs, Verified Updates, and every other existing
public or research entity — is off limits while the resolved mode is
anything other than `publish` with the kill switch off.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

AGENT_TABLE_PREFIX = "agent_"

# Written by the migration machinery, not by the agent, and never
# reader-visible. Denying them would break migrate() under the guard.
_INFRASTRUCTURE_TABLES = frozenset({"schema_version", "sqlite_sequence"})

_WRITE_ACTIONS = frozenset({
    sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
    sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_DROP_INDEX,
})


class ShadowWriteViolation(RuntimeError):
    """A write outside the agent tables was attempted without publish
    authority. Always a bug, never a condition to handle."""


def is_agent_table(table: str) -> bool:
    return (table or "").lower().startswith(AGENT_TABLE_PREFIX)


def assert_write_allowed(table: str, *, resolved) -> None:
    """The call every write path outside the agent tables must make.
    `resolved` is a ResolvedMode from src.logic.agent_mode."""
    if is_agent_table(table):
        return
    if getattr(resolved, "may_write_outside_agent_tables", False):
        return
    mode = getattr(resolved, "mode", "off")
    kill = getattr(resolved, "kill_switch_on", False)
    raise ShadowWriteViolation(
        f"refusing to write {table!r}: mode={mode} kill_switch_on={kill}. "
        "Only the agent tables are writable outside publish mode."
    )


@contextmanager
def forbid_non_agent_writes(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Denies every non-agent write on this SQLite connection for the
    duration of the block, at the statement level — so it catches raw SQL
    a repository might issue, not only calls that remembered to ask.

    SQLite only: it is the local and test backend, and the authorizer hook
    has no Postgres equivalent that could be applied per-connection
    without a role change. Postgres deployments rely on the structural
    guarantee plus the row-hash invariant tests, which prove the same
    property against a real database.

    A denied statement surfaces as sqlite3.DatabaseError ("not authorized")
    from the cursor, because an authorizer callback cannot raise through
    the C layer. Callers that want the typed error should use
    assert_write_allowed at the seam instead."""
    def authorizer(action: int, arg1, arg2, db_name, trigger) -> int:
        if action not in _WRITE_ACTIONS:
            return sqlite3.SQLITE_OK
        table = arg1 or ""
        if is_agent_table(table) or table in _INFRASTRUCTURE_TABLES:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    conn.set_authorizer(authorizer)
    try:
        yield conn
    finally:
        conn.set_authorizer(None)
