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

CURRENT_SCHEMA_VERSION = 9

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

# Evidence-packet foundation, Phase 1 (design/DECISIONS.md) — isolated
# Postgres counterpart to state_db/schema.py's own _V3_STATEMENTS,
# identical column set (see that module's comment for the full
# rationale). Additive, nullable columns only — never touches existing
# rows.
_V3_STATEMENTS: tuple[str, ...] = (
    "ALTER TABLE filing_events ADD COLUMN filed_at TEXT",
    "ALTER TABLE candidates ADD COLUMN excerpt_supplemental TEXT",
    "ALTER TABLE candidates ADD COLUMN excerpt_retrieved_at TEXT",
    "ALTER TABLE candidates ADD COLUMN flag_reason_json TEXT",
    "ALTER TABLE candidates ADD COLUMN evidence_location_json TEXT",
)

# Evidence-packet foundation, Phase 2, Step 2 (design/DECISIONS.md) —
# isolated Postgres counterpart to state_db/schema.py's own
# _V4_STATEMENTS. One additive, nullable column.
_V4_STATEMENTS: tuple[str, ...] = (
    "ALTER TABLE candidates ADD COLUMN evidence_source_member TEXT",
)

# Radar evidence-packet foundation, Phase 3, Step 2 (design/DECISIONS.md)
# — isolated Postgres counterpart to state_db/schema.py's own
# _V5_STATEMENTS (see that module's comment for the full immutability/
# stable-id rationale). One new, wholly additive, append-only table —
# no update path exists anywhere in this codebase for it.
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

# EevaResearch Phase 4, Step 1 (design/DECISIONS.md) — isolated Postgres
# counterpart to state_db/schema.py's own _V6_STATEMENTS (see that
# module's comment for the full immutability/no-hard-FK rationale).
# Three new, wholly additive, append-only tables — no update path exists
# anywhere in this codebase for any of them.
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

# EevaResearch — Evidence-First Themes MVP (design/DECISIONS.md).
# Isolated Postgres counterpart to state_db/schema.py's own
# _V7_STATEMENTS (see that module's comment for the full FK/visibility-
# update rationale — themes deliberately DO carry a real foreign key to
# research_themes(id), unlike the Research Case family above).
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

# EevaResearch — Phase A1 (design/DECISIONS.md). Isolated Postgres
# counterpart to state_db/schema.py's own _V8_STATEMENTS (see that
# module's comment for the full FK/insert-only/immutability rationale).
_V8_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE theme_matching_scopes (
        theme_id TEXT PRIMARY KEY REFERENCES research_themes (id),
        sector_tags_json TEXT NOT NULL,
        sector_subtags_json TEXT NOT NULL,
        allowed_matched_rule_categories_json TEXT NOT NULL,
        required_keywords_json TEXT NOT NULL,
        excluded_keywords_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE research_case_theme_matches (
        id TEXT PRIMARY KEY,
        case_id TEXT NOT NULL REFERENCES research_cases (id),
        theme_id TEXT NOT NULL REFERENCES research_themes (id),
        confidence TEXT NOT NULL,
        direction TEXT NOT NULL,
        matched_sector_tag TEXT,
        matched_rule_categories_json TEXT NOT NULL,
        matched_keywords_json TEXT NOT NULL,
        rationale TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX idx_research_case_theme_matches_case_theme ON research_case_theme_matches (case_id, theme_id)",
    "CREATE INDEX idx_research_case_theme_matches_theme_id ON research_case_theme_matches (theme_id)",
    """
    CREATE TABLE theme_match_review_decisions (
        id TEXT PRIMARY KEY,
        match_id TEXT NOT NULL REFERENCES research_case_theme_matches (id),
        decision TEXT NOT NULL,
        reviewer_note TEXT,
        reviewed_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_theme_match_review_decisions_match_id ON theme_match_review_decisions (match_id)",
)

# EevaResearch — Citrini-style Theme research workspace vertical slice
# (design/DECISIONS.md). See state_db/schema.py's own V9 comment for
# the full rationale — identical shape here.
_V9_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE theme_research_notes (
        id TEXT PRIMARY KEY,
        theme_id TEXT NOT NULL REFERENCES research_themes (id),
        note_type TEXT NOT NULL,
        content TEXT NOT NULL,
        confidence TEXT,
        disconfirming_condition TEXT,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_theme_research_notes_theme_id_created_at ON theme_research_notes (theme_id, created_at)",
)

# Forward-only migration steps, keyed by the version they move TO.
# Adding schema version 10 later means appending a new (10, (...statements...))
# entry here — existing entries are never edited or removed.
_MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (1, _V1_STATEMENTS),
    (2, _V2_STATEMENTS),
    (3, _V3_STATEMENTS),
    (4, _V4_STATEMENTS),
    (5, _V5_STATEMENTS),
    (6, _V6_STATEMENTS),
    (7, _V7_STATEMENTS),
    (8, _V8_STATEMENTS),
    (9, _V9_STATEMENTS),
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
