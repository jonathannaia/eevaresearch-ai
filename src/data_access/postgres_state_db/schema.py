"""Explicit, forward-only schema versioning + idempotent migration
runner — the isolated Postgres counterpart to
src/data_access/state_db/schema.py, mirroring its table design and
migration control flow exactly. Only genuine Postgres differences are
noted inline: `BIGINT GENERATED ALWAYS AS IDENTITY` in place of SQLite's
`INTEGER PRIMARY KEY AUTOINCREMENT`, and `to_regclass()`-based schema
detection in place of a `sqlite_master` query. No PostgreSQL extension
is used anywhere in this schema.

`migrate()` is safe to call on a brand-new (or brand-new-schema) database
or an already-migrated one — it only ever moves forward from the current
recorded version, never re-applies a step already recorded as done, and
never migrates backward. See state_db/schema.py's own docstring for the
full per-table design rationale (composite filing_events key matching
scan_service.dedup_key(), candidates.version as the optimistic-concurrency
column, state_transitions as an append-only audit trail, the shared
resolved_identifiers shape for EDGAR/DART) — reproduced identically here,
not redesigned."""
from __future__ import annotations

import psycopg

CURRENT_SCHEMA_VERSION = 2

_V1_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE filing_events (
        source_name TEXT NOT NULL DEFAULT 'OpenDART / DART',
        corp_code TEXT NOT NULL,
        rcept_no TEXT NOT NULL,
        corp_name TEXT NOT NULL,
        stock_code TEXT NOT NULL,
        report_nm TEXT NOT NULL,
        rcept_dt TEXT NOT NULL,
        flr_nm TEXT NOT NULL,
        pblntf_ty TEXT NOT NULL DEFAULT '',
        pblntf_detail_ty TEXT NOT NULL DEFAULT '',
        theme_slug TEXT NOT NULL DEFAULT '',
        subtheme_slug TEXT,
        source_url TEXT NOT NULL DEFAULT '',
        retrieved_at TEXT NOT NULL DEFAULT '',
        original_language TEXT NOT NULL DEFAULT 'Korean',
        is_demo INTEGER NOT NULL DEFAULT 0,
        primary_document TEXT NOT NULL DEFAULT '',
        ordinance_code TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (source_name, corp_code, rcept_no)
    )
    """,
    """
    CREATE TABLE candidates (
        id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        filing_corp_code TEXT NOT NULL,
        filing_rcept_no TEXT NOT NULL,
        matched_rules_json TEXT NOT NULL DEFAULT '[]',
        confidence TEXT NOT NULL,
        status TEXT NOT NULL,
        extraction_state TEXT NOT NULL,
        translation_state TEXT NOT NULL,
        excerpt_quality TEXT NOT NULL,
        excerpt_original TEXT,
        title_translation_json TEXT,
        excerpt_translation_json TEXT,
        reviewed_at TEXT,
        reviewed_note TEXT NOT NULL DEFAULT '',
        materiality_assessment TEXT NOT NULL DEFAULT 'Not assessed',
        version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (source, filing_corp_code, filing_rcept_no)
            REFERENCES filing_events (source_name, corp_code, rcept_no)
    )
    """,
    "CREATE INDEX idx_candidates_source ON candidates (source)",
    "CREATE INDEX idx_candidates_status ON candidates (status)",
    """
    CREATE TABLE state_transitions (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        candidate_id TEXT NOT NULL REFERENCES candidates (id),
        status TEXT NOT NULL,
        at TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX idx_state_transitions_candidate ON state_transitions (candidate_id, id)",
    """
    CREATE TABLE resolved_identifiers (
        source TEXT NOT NULL,
        lookup_key TEXT NOT NULL,
        identifier TEXT NOT NULL,
        display_name TEXT NOT NULL,
        resolution_method TEXT NOT NULL,
        retrieved_at TEXT NOT NULL,
        PRIMARY KEY (source, lookup_key)
    )
    """,
)

# Durable-State Phase 4M-0 — isolated Postgres counterpart to
# state_db/schema.py's own provider_scan_status table, identical shape
# (no Postgres-specific column types are needed here). See that
# module's own comment for the full per-column design rationale.
_V2_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE provider_scan_status (
        provider TEXT PRIMARY KEY,
        cursor_value TEXT,
        started_at TEXT,
        completed_at TEXT,
        last_successful_at TEXT,
        items_discovered INTEGER NOT NULL DEFAULT 0,
        candidates_created INTEGER NOT NULL DEFAULT 0,
        skipped_unresolved_count INTEGER NOT NULL DEFAULT 0,
        failure_code TEXT,
        updated_at TEXT NOT NULL
    )
    """,
)

# Forward-only migration steps, keyed by the version they move TO.
# Adding schema version 3 later means appending a new (3, (...statements...))
# entry here — existing entries are never edited or removed.
_MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (1, _V1_STATEMENTS),
    (2, _V2_STATEMENTS),
)


def get_schema_version(conn: psycopg.Connection) -> int:
    """0 if the schema_version table doesn't exist yet in the current
    schema (a brand-new database or a freshly created isolation schema)
    — never raises for that case, since "not yet migrated" is an
    expected, normal state, not a failure. Uses to_regclass() — the
    Postgres-native "does this relation exist, resolved against the
    current search_path" check — rather than SQLite's sqlite_master
    query."""
    row = conn.execute("SELECT to_regclass('schema_version') AS reg").fetchone()
    if row["reg"] is None:
        return 0
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    return row["version"] if row is not None else 0


def migrate(conn: psycopg.Connection) -> int:
    """Applies every migration step after the database's (or isolation
    schema's) current recorded version, in order, each inside its own
    transaction. Safe to call repeatedly: a schema already at
    CURRENT_SCHEMA_VERSION is a no-op. Returns the resulting schema
    version. Control flow copied unchanged from state_db/schema.py's
    migrate() — only the DDL text and version-detection query differ."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = conn.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()
    if row["n"] == 0:
        conn.execute("INSERT INTO schema_version (version) VALUES (0)")
        conn.commit()

    current = get_schema_version(conn)
    for target_version, statements in _MIGRATIONS:
        if target_version <= current:
            continue
        try:
            for statement in statements:
                conn.execute(statement)
            conn.execute("UPDATE schema_version SET version = %s", (target_version,))
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()
            current = target_version
    return current
