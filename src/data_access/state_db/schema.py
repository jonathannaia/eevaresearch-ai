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

CURRENT_SCHEMA_VERSION = 7

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

# Radar evidence-packet foundation, Phase 3, Step 2 (design/DECISIONS.md)
# — one new, wholly additive, append-only table for persisted prior-
# disclosure comparison results (see
# src.data_access.comparison_store.ComparisonRecord). No column here is
# ever updated after insert — there is deliberately no
# update_comparison_record function anywhere in this codebase, only
# append/load — so no CHECK/trigger is needed to enforce immutability;
# it is enforced by which functions exist, exactly like
# state_transitions' own "insert-only" convention above. `id` is a
# deterministic, content-addressed stable id (see
# comparison_store.build_comparison_record_id) — a duplicate INSERT is
# rejected by this PRIMARY KEY constraint rather than silently
# overwriting anything. Categories/limitations are stored as JSON-text
# columns, the same convention `candidates.matched_rules_json` already
# uses for a variable-length list of strings.
_V5_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE comparison_results (
        id TEXT PRIMARY KEY,
        current_candidate_id TEXT NOT NULL,
        current_source_name TEXT NOT NULL,
        current_corp_code TEXT NOT NULL,
        current_document_id TEXT NOT NULL,
        prior_candidate_id TEXT,
        prior_document_id TEXT,
        prior_filed_at TEXT,
        comparison_status TEXT NOT NULL,
        comparison_basis TEXT NOT NULL,
        added_categories_json TEXT NOT NULL,
        removed_categories_json TEXT NOT NULL,
        prior_excerpt TEXT,
        current_excerpt TEXT,
        limitations_json TEXT NOT NULL,
        computed_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_comparison_results_current_candidate_id ON comparison_results (current_candidate_id)",
)

# EevaResearch Phase 4, Step 1 (design/DECISIONS.md) — three new, wholly
# additive, append-only tables for the immutable Research Case model
# family (see src.models.research_case / src.data_access.research_store).
# No update/replace/upsert/delete function exists anywhere in this
# codebase for any of these three tables — insert and read only, exactly
# the same discipline comparison_results (V5, above) already established.
# No hard foreign key to any existing Radar/Daily News table, or between
# these three tables themselves — same "plain indexed column, no FK"
# choice comparison_results.current_candidate_id already made, avoiding
# any insert-ordering assumption across a multi-record case bundle.
# evidence_ids/limitations/transmission_path are stored as JSON-text
# columns, the same convention comparison_results' own *_json columns
# already use for a variable-length list of strings.
_V6_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE research_cases (
        id TEXT PRIMARY KEY,
        trigger_source_type TEXT NOT NULL,
        trigger_source_id TEXT NOT NULL,
        trigger_source_name TEXT NOT NULL,
        trigger_summary TEXT NOT NULL,
        title TEXT NOT NULL,
        research_question TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        version INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE research_evidence_items (
        id TEXT PRIMARY KEY,
        case_id TEXT NOT NULL,
        source_type TEXT NOT NULL,
        source_id TEXT NOT NULL,
        source_url TEXT NOT NULL,
        source_publisher_or_system TEXT NOT NULL,
        source_date TEXT NOT NULL,
        retrieved_at TEXT NOT NULL,
        excerpt_original TEXT NOT NULL,
        original_language TEXT NOT NULL,
        added_at TEXT NOT NULL,
        excerpt_translated TEXT,
        translation_provider TEXT
    )
    """,
    "CREATE INDEX idx_research_evidence_items_case_id ON research_evidence_items (case_id)",
    """
    CREATE TABLE research_assertions (
        id TEXT PRIMARY KEY,
        case_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        subject_entity TEXT,
        object_entity TEXT,
        role TEXT,
        affected_entity TEXT,
        bottleneck_type TEXT,
        supply_chain_layer TEXT,
        transmission_path_json TEXT,
        assertion_status TEXT NOT NULL,
        evidence_ids_json TEXT NOT NULL,
        confidence TEXT NOT NULL,
        created_at TEXT NOT NULL,
        reasoning TEXT,
        limitations_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_research_assertions_case_id ON research_assertions (case_id)",
)

# EevaResearch — Evidence-First Themes MVP (design/DECISIONS.md). Three
# new tables for the public, curated ResearchTheme model family (see
# src.models.theme_research / src.data_access.theme_store) — wholly
# separate from the internal research_cases/research_evidence_items/
# research_assertions family above, and from the legacy demo Theme/
# Subtheme model (which has no table of its own; it's seed-data only).
# Unlike the Research Case family, theme_evidence_items/
# theme_company_map_entries DO carry a real foreign key to
# research_themes (id) — themes are manually curated, low-volume
# records where referential integrity is cheap and worth having; this
# is a deliberate difference from the Research Case family's own
# no-hard-FK choice, not an oversight. research_themes.visibility is
# the one field ever updated after insert (a publish/archive
# transition) — see theme_repository.py's own set_theme_visibility()
# docstring.
_V7_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE research_themes (
        id TEXT PRIMARY KEY,
        category TEXT NOT NULL,
        status TEXT NOT NULL,
        visibility TEXT NOT NULL,
        title TEXT NOT NULL,
        key_question TEXT NOT NULL,
        hypothesis TEXT NOT NULL,
        working_thesis TEXT NOT NULL,
        why_it_matters TEXT NOT NULL,
        what_could_change_the_view TEXT NOT NULL,
        what_to_watch_next TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_research_themes_visibility_updated_at ON research_themes (visibility, updated_at)",
    """
    CREATE TABLE theme_evidence_items (
        id TEXT PRIMARY KEY,
        theme_id TEXT NOT NULL REFERENCES research_themes (id),
        date TEXT NOT NULL,
        company TEXT NOT NULL,
        source_name TEXT NOT NULL,
        source_url TEXT NOT NULL,
        fact TEXT NOT NULL,
        relevance TEXT NOT NULL,
        direction TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_theme_evidence_items_theme_id_date ON theme_evidence_items (theme_id, date)",
    """
    CREATE TABLE theme_company_map_entries (
        id TEXT PRIMARY KEY,
        theme_id TEXT NOT NULL REFERENCES research_themes (id),
        company_name TEXT NOT NULL,
        role TEXT NOT NULL,
        note TEXT
    )
    """,
    "CREATE INDEX idx_theme_company_map_entries_theme_id_role ON theme_company_map_entries (theme_id, role)",
)

# Forward-only migration steps, keyed by the version they move TO.
# Adding schema version 8 later means appending a new (8, (...statements...))
# entry here — existing entries are never edited or removed.
_MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (1, _V1_STATEMENTS),
    (2, _V2_STATEMENTS),
    (3, _V3_STATEMENTS),
    (4, _V4_STATEMENTS),
    (5, _V5_STATEMENTS),
    (6, _V6_STATEMENTS),
    (7, _V7_STATEMENTS),
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
