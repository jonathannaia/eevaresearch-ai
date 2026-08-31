"""Explicit, forward-only schema versioning + idempotent migration
runner. `migrate()` is safe to call on a brand-new database or an
already-migrated one — it only ever moves a database forward from its
current recorded version, never re-applies a step already recorded as
done, and never migrates backward.

Table design notes:

- `filing_events`: composite primary key `(source_name, corp_code,
  rcept_no)` — `source_name` is the real `FilingEvent.source_name` field
  itself (not a separate denormalized copy), and the three-part shape
  matches `scan_service.dedup_key()` exactly per source (DART/EDGAR's
  `f"{source}:{cik}:{accession_no}"`, EDINET's analogous
  `(edinet_code, docID)` pair reusing the same corp_code/rcept_no column
  slots), so source isolation is structural: two sources can never
  collide on this key even if corp_code/rcept_no values coincidentally
  matched.
- `candidates`: primary key is the existing natural id
  (`cand-{rcept_no}` / `edgar-cand-{accession_no}` / EDINET's analogous
  id) — the same idempotency key `candidate_store.py` already uses.
  Its own `source` column is a deliberate denormalized copy of the
  parent filing's `source_name` (kept in sync only at insert time, never
  independently updated) so a source-scoped candidate query never needs
  a join — the same read-shape `candidate_store.load_candidates(...,
  filename)`'s per-source files already provide today. References its
  parent filing_events row via the composite key above. `matched_rules`
  is stored as a JSON-encoded text column (a variable-length list of
  strings) rather than a child table — this phase is a faithful
  round-trip of the existing dataclass shape, not a redesign of it.
  `version` is the optimistic-concurrency column used by
  candidate_repository.update_candidate().
- `state_transitions`: one row per StateTransition, in append order
  (ordered by the autoincrement `id`, which SQLite guarantees is
  monotonically increasing per insert) — replacing the JSON store's
  "rewrite the whole candidate to append one history entry" with a
  genuine append-only insert.
- `resolved_identifiers`: one shared table for both EDGAR's ResolvedCik
  and DART's ResolvedCorpCode caches, which already have an identical
  shape once you rename cik/corp_code -> identifier and company_name/
  corp_name -> display_name (verified by reading both dataclasses
  directly — see cik_resolver.py/corp_code_resolver.py). EDINET's own
  code-resolver cache is deliberately NOT included in this phase: it
  spans two separate cache files and a richer, EDINET-specific record
  shape (filer_name/filer_name_en/securities_code and more), and EDINET's
  five live tracked companies don't consult it at runtime anyway (their
  identifiers are hardcoded in tracked_companies.py, already
  live-verified — see that module's own docstring) — faithfully
  representing it was not achievable within this phase's scope, so it's
  left out rather than approximated.
"""
from __future__ import annotations

import sqlite3

CURRENT_SCHEMA_VERSION = 4

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
        id INTEGER PRIMARY KEY AUTOINCREMENT,
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

# Durable-State Phase 4M-0 — one row per provider (per-provider
# granularity, not per-issuer, by explicit decision). Combines the
# cursor (`cursor_value`) and the scan-status fields into a single table
# since a worker tick always reads/writes both together for one
# provider. `provider` is the same source-name string used everywhere
# else in this codebase ("SEC EDGAR" / "OpenDART / DART" / "EDINET").
# `failure_code` is a short, sanitized internal reason string — never a
# raw exception message, matching this codebase's existing
# BackendConfigurationError discipline (see backend_factory.py).
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

# Evidence-packet foundation, Phase 1 (design/DECISIONS.md) — additive
# columns only, every one nullable with no default required (a nullable
# TEXT column with no DEFAULT clause is NULL for every pre-existing row
# automatically), so this migration never touches existing data. See
# src/models/models.py for what each new field represents:
# FilingEvent.filed_at (full timestamp, only ever set for EDINET), and
# CandidateSignal.excerpt_supplemental/excerpt_retrieved_at/
# flag_reason (JSON-encoded FlagReason)/evidence_location (JSON-encoded
# EvidenceLocation) — the same JSON-text-column convention already used
# for title_translation_json/excerpt_translation_json above.
_V3_STATEMENTS: tuple[str, ...] = (
    "ALTER TABLE filing_events ADD COLUMN filed_at TEXT",
    "ALTER TABLE candidates ADD COLUMN excerpt_supplemental TEXT",
    "ALTER TABLE candidates ADD COLUMN excerpt_retrieved_at TEXT",
    "ALTER TABLE candidates ADD COLUMN flag_reason_json TEXT",
    "ALTER TABLE candidates ADD COLUMN evidence_location_json TEXT",
)

# Evidence-packet foundation, Phase 2, Step 2 (design/DECISIONS.md) — one
# additive, nullable column: CandidateSignal.evidence_source_member, the
# safe archive-relative ZIP-member path/name EDINET's bounded ZIP
# extraction (Phase 2, Step 1) selected, when it selected one. NULL for
# every pre-existing row and for every non-EDINET-ZIP candidate.
_V4_STATEMENTS: tuple[str, ...] = (
    "ALTER TABLE candidates ADD COLUMN evidence_source_member TEXT",
)

# Forward-only migration steps, keyed by the version they move TO.
# Adding schema version 5 later means appending a new (5, (...statements...))
# entry here — existing entries are never edited or removed.
_MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (1, _V1_STATEMENTS),
    (2, _V2_STATEMENTS),
    (3, _V3_STATEMENTS),
    (4, _V4_STATEMENTS),
)


def get_schema_version(conn: sqlite3.Connection) -> int:
    """0 if the schema_version table doesn't exist yet (a brand-new
    database) — never raises for that case, since "not yet migrated" is
    an expected, normal state, not a failure."""
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
    ).fetchone()
    if exists is None:
        return 0
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    return row["version"] if row is not None else 0


def migrate(conn: sqlite3.Connection) -> int:
    """Applies every migration step after the database's current
    recorded version, in order, each inside its own transaction. Safe to
    call repeatedly: a database already at CURRENT_SCHEMA_VERSION is a
    no-op. Returns the resulting schema version."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    if conn.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()["n"] == 0:
        conn.execute("INSERT INTO schema_version (version) VALUES (0)")
        conn.commit()

    current = get_schema_version(conn)
    for target_version, statements in _MIGRATIONS:
        if target_version <= current:
            continue
        try:
            for statement in statements:
                conn.execute(statement)
            conn.execute("UPDATE schema_version SET version = ?", (target_version,))
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()
            current = target_version
    return current
