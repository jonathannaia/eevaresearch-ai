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

CURRENT_SCHEMA_VERSION = 13

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

# EevaResearch — Phase A1 (design/DECISIONS.md). Three new, wholly
# internal tables backing deterministic, rule-based Research-Case-to-
# Theme matching (see src.models.theme_matching /
# src.logic.research_case_theme_matching) — never read by the public
# Themes UI or ThemeRepositoryProtocol. Unlike the Research Case family
# (V6, deliberately no hard FK), these DO carry real foreign keys —
# matching a manually-curated, low-volume record set where referential
# integrity is cheap, the same choice already made for the Themes MVP's
# own V7 tables. `theme_matching_scopes` is keyed by theme_id itself
# (one scope per theme, no separate id) and is insert-only in this
# phase — no update/replace path exists; a curator wanting to revise a
# scope is out of scope until a later, separately approved model change
# adds real revisioning. `research_case_theme_matches` and
# `theme_match_review_decisions` are both insert-only and immutable —
# "pending review" is derived purely from a match having no decision
# row, never a mutable status column.
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
    # Belt-and-suspenders: `id` is already sha256(case_id|theme_id), so
    # this unique index is mathematically implied by the primary key
    # today — kept explicit so the invariant survives even if the ID
    # algorithm in research_case_theme_matching.py ever changes.
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
    # Serves both "decision history for one match" and the pending-
    # queue anti-join (WHERE NOT EXISTS (... WHERE match_id = ...)).
    "CREATE INDEX idx_theme_match_review_decisions_match_id ON theme_match_review_decisions (match_id)",
)

# EevaResearch — Citrini-style Theme research workspace vertical slice
# (design/DECISIONS.md). One new table backing ThemeResearchNote — a
# single, unified, insert-only research log covering hypotheses (with
# their own confidence and disconfirming condition), curator decisions,
# and watch items. Carries a real FK to research_themes, matching the
# same choice already made for the Themes MVP's own V7 tables.
# confidence/disconfirming_condition are nullable — only ever populated
# for note_type == 'Hypothesis'.
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

# Translation reliability workstream (design/DECISIONS.md; see
# src/models/models.py's CandidateSignal docstring for the full field
# rationale) — five additive, nullable columns persisting the translation
# retry state introduced in commit a13ddd0 (previously JSON-backend-only).
# translation_retry_count gets an explicit NOT NULL DEFAULT 0 to match
# CandidateSignal.translation_retry_count's own dataclass default exactly
# — every pre-existing row reads back as 0, not None. The four TEXT
# columns are left with no DEFAULT clause, so a pre-existing row reads
# back as NULL/None, matching CandidateSignal's own None defaults for
# translation_failure_category/_reason/_at/translation_next_retry_at.
_V10_STATEMENTS: tuple[str, ...] = (
    "ALTER TABLE candidates ADD COLUMN translation_failure_category TEXT",
    "ALTER TABLE candidates ADD COLUMN translation_failure_reason TEXT",
    "ALTER TABLE candidates ADD COLUMN translation_failure_at TEXT",
    "ALTER TABLE candidates ADD COLUMN translation_retry_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE candidates ADD COLUMN translation_next_retry_at TEXT",
)

# Daily News durability workstream (design/DECISIONS.md) — three new,
# wholly additive tables giving Daily News's own NewsStory/
# NewsSourceReference/NewsStateTransition model (src/models/
# daily_news_models.py) the same durable SQLite/Postgres persistence
# Radar already has, entirely separate from `candidates`/`filing_events`
# and every other table above (no shared columns, no cross-references).
#
# - `daily_news_stories`: primary key is the existing deterministic id
#   (`newsitem-{company-slug}-{hash(company|canonical_url)}` — see
#   daily_news_pipeline._story_id()) — the same idempotency key
#   daily_news_store.py's JSON store already uses. `version` is the
#   optimistic-concurrency column, same convention as `candidates.version`.
# - `daily_news_sources`: one row per NewsSourceReference (NewsStory.sources
#   is a tuple, not a single value — today's pipeline only ever produces
#   exactly one per story, but the model itself doesn't bound it), in
#   append order, mirroring `state_transitions`'s own shape. `url` carries
#   a UNIQUE index — the same canonical link can never be stored twice
#   across any story, a stronger, DB-enforced guarantee than the id hash
#   alone provides (that hash could theoretically differ across two rows
#   referencing the identical URL only if `company_name` text differed).
# - `daily_news_state_transitions`: one row per NewsStateTransition, in
#   append order — identical shape/purpose to `state_transitions` above,
#   just keyed to `daily_news_stories` instead of `candidates`.
_V11_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE daily_news_stories (
        id TEXT PRIMARY KEY,
        company_name TEXT NOT NULL,
        ticker TEXT,
        theme_slug TEXT NOT NULL DEFAULT '',
        headline TEXT NOT NULL,
        eeva_summary TEXT,
        is_fallback_summary INTEGER NOT NULL DEFAULT 0,
        translation_unavailable INTEGER NOT NULL DEFAULT 0,
        original_title TEXT,
        status TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_daily_news_stories_company ON daily_news_stories (company_name)",
    "CREATE INDEX idx_daily_news_stories_status ON daily_news_stories (status)",
    """
    CREATE TABLE daily_news_sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        story_id TEXT NOT NULL REFERENCES daily_news_stories (id),
        publisher TEXT NOT NULL,
        source_class TEXT NOT NULL,
        url TEXT NOT NULL,
        title TEXT NOT NULL,
        published_at TEXT NOT NULL,
        retrieved_at TEXT NOT NULL,
        original_language TEXT NOT NULL,
        excerpt_original TEXT,
        image_url TEXT,
        image_alt TEXT
    )
    """,
    "CREATE UNIQUE INDEX idx_daily_news_sources_url ON daily_news_sources (url)",
    "CREATE INDEX idx_daily_news_sources_story ON daily_news_sources (story_id, id)",
    """
    CREATE TABLE daily_news_state_transitions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        story_id TEXT NOT NULL REFERENCES daily_news_stories (id),
        status TEXT NOT NULL,
        at TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX idx_daily_news_state_transitions_story ON daily_news_state_transitions (story_id, id)",
)

# Daily News autonomous worker (design/DECISIONS.md) — two new, wholly
# additive tables giving scripts/daily_news_worker.py somewhere to
# persist its own internal health/status bookkeeping, entirely separate
# from daily_news_stories/daily_news_sources/daily_news_state_transitions
# (V11) — those hold the durable NewsStory data the pipeline itself
# produces; these hold the worker process's own operational state, read
# and written only by the worker (never by any pipeline call, page, or
# component), mirroring provider_scan_status's (V2) own
# worker-only-table convention exactly.
#
# - `daily_news_scan_status`: one row per registered feed, keyed by
#   `company_name` (the same string feed_registry.DailyNewsFeedSource.
#   company_name / DailyNewsScanReport.source_failures already key by).
#   `last_fetch_success_at` updates on every tick where that feed's own
#   fetch+parse succeeded, regardless of whether a new story resulted;
#   `last_story_published_at` only updates when a new story was actually
#   persisted that tick. This distinction is deliberate: the daily
#   reconciliation health check flags staleness using
#   `last_fetch_success_at` only, so a feed that is fetching successfully
#   but simply has nothing new to report is never mistaken for a broken
#   one.
# - `daily_news_worker_status`: a single row (keyed by a fixed
#   `worker_key` constant, since there is exactly one Daily News
#   pipeline, unlike Radar's per-provider granularity) tracking the
#   worker process's own tick/reconciliation bookkeeping.
_V12_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE daily_news_scan_status (
        company_name TEXT PRIMARY KEY,
        last_attempt_at TEXT,
        last_fetch_success_at TEXT,
        last_story_published_at TEXT,
        last_failure_code TEXT,
        items_discovered_last_run INTEGER NOT NULL DEFAULT 0,
        stories_published_last_run INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE daily_news_worker_status (
        worker_key TEXT PRIMARY KEY,
        last_tick_started_at TEXT,
        last_tick_completed_at TEXT,
        last_reconciliation_at TEXT,
        last_failure_code TEXT,
        updated_at TEXT NOT NULL
    )
    """,
)

# Company Discovery — Phase 2, passive Candidate Ledger only (design/
# DECISIONS.md). Seven wholly additive tables — no existing table is
# touched, no existing pipeline reads or writes any of them.
# `candidate_issuers.coverage_state` deliberately excludes 'Seed': a row
# in this table can never represent a Core company — Core stays
# exclusively in TrackedCompany/SEED_ISSUERS, untouched by this
# workstream. No promotion path exists in Phase 2; there is no write
# path from these tables into any live-monitoring configuration.
#
# - `candidate_issuers`: one row per Candidate entity. `issuer_id` is
#   `candidate:{sha256(normalized_legal_name|country_or_jurisdiction)[:16]}`
#   — deterministic, stable even once a real ticker/exchange is later
#   confirmed (see candidate_issuer_identifiers, which never changes
#   issuer_id). `composite_score` is a cache, recomputed from
#   candidate_evidence every worker tick — never hand-edited.
# - `candidate_issuer_identifiers`: confirmed source identifiers only,
#   normalized child table (never a JSON blob) — a public-company
#   identifier is never guessed; this table only ever holds one already
#   independently confirmed from source text.
# - `candidate_aliases`: mention-text -> known-Candidate lookup,
#   accumulated as more evidence names the same entity differently.
# - `candidate_evidence`: append-only, one row per extracted mention. A
#   `candidate_issuers` row is only ever created together with its first
#   evidence row, in the same repository call — see
#   candidate_issuer_repository.py's create_candidate_with_evidence(),
#   the only function that can insert a new candidate_issuers row.
#   `UNIQUE(dedup_key)` is the idempotency guarantee that makes the
#   worker's rolling ingestion-overlap window (candidate_pipeline.py)
#   safe to reprocess every tick.
# - `candidate_score_history`: one row per rescore, audit trail only.
# - `candidate_worker_status` / `candidate_state_transitions`: mirror
#   daily_news_worker_status / daily_news_state_transitions exactly.
_V13_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE candidate_issuers (
        issuer_id TEXT PRIMARY KEY,
        legal_name TEXT NOT NULL,
        native_name TEXT NOT NULL DEFAULT '',
        country_or_jurisdiction TEXT NOT NULL DEFAULT 'Unconfirmed',
        entity_kind TEXT NOT NULL DEFAULT 'unknown'
            CHECK (entity_kind IN ('corporate','subsidiary','fund','agency','government','unknown')),
        parent_issuer_id TEXT REFERENCES candidate_issuers (issuer_id),
        coverage_state TEXT NOT NULL
            CHECK (coverage_state IN ('Discovered','Rejected','Archived','Quarantined')),
        resolution_confidence TEXT NOT NULL DEFAULT 'Low'
            CHECK (resolution_confidence IN ('High','Medium','Low')),
        composite_score REAL NOT NULL DEFAULT 0.0,
        discovered_via TEXT NOT NULL,
        first_evidence_at TEXT NOT NULL,
        last_evidence_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_candidate_issuers_state ON candidate_issuers (coverage_state)",
    "CREATE INDEX idx_candidate_issuers_last_evidence ON candidate_issuers (last_evidence_at)",
    """
    CREATE TABLE candidate_issuer_identifiers (
        issuer_id TEXT NOT NULL REFERENCES candidate_issuers (issuer_id),
        source TEXT NOT NULL,
        native_id TEXT NOT NULL,
        confirmed_via TEXT NOT NULL,
        confirmed_at TEXT NOT NULL,
        PRIMARY KEY (issuer_id, source)
    )
    """,
    "CREATE UNIQUE INDEX idx_candidate_issuer_identifiers_native ON candidate_issuer_identifiers (source, native_id)",
    """
    CREATE TABLE candidate_aliases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        issuer_id TEXT NOT NULL REFERENCES candidate_issuers (issuer_id),
        alias_text TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX idx_candidate_aliases_issuer_alias ON candidate_aliases (issuer_id, alias_text)",
    "CREATE INDEX idx_candidate_aliases_text ON candidate_aliases (alias_text)",
    """
    CREATE TABLE candidate_evidence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        issuer_id TEXT NOT NULL REFERENCES candidate_issuers (issuer_id),
        source_type TEXT NOT NULL CHECK (source_type IN ('Filing','DailyNews')),
        source_name TEXT NOT NULL,
        source_record_id TEXT NOT NULL,
        source_url TEXT NOT NULL,
        source_snippet TEXT NOT NULL,
        relationship_type TEXT NOT NULL
            CHECK (relationship_type IN ('supplier','customer','partner','competitor','thematic_mention')),
        matched_pattern_category TEXT NOT NULL,
        related_core_issuer_id TEXT,
        theme_slug TEXT,
        supply_chain_layer TEXT,
        extraction_timestamp TEXT NOT NULL,
        source_published_at TEXT,
        dedup_key TEXT NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX idx_candidate_evidence_dedup ON candidate_evidence (dedup_key)",
    "CREATE INDEX idx_candidate_evidence_issuer ON candidate_evidence (issuer_id)",
    "CREATE INDEX idx_candidate_evidence_source_record ON candidate_evidence (source_record_id)",
    """
    CREATE TABLE candidate_score_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        issuer_id TEXT NOT NULL REFERENCES candidate_issuers (issuer_id),
        computed_at TEXT NOT NULL,
        composite_score REAL NOT NULL,
        evidence_count INTEGER NOT NULL,
        independent_source_count INTEGER NOT NULL,
        score_breakdown TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_candidate_score_history_issuer ON candidate_score_history (issuer_id, computed_at)",
    """
    CREATE TABLE candidate_worker_status (
        worker_key TEXT PRIMARY KEY,
        last_tick_started_at TEXT,
        last_tick_completed_at TEXT,
        last_failure_code TEXT,
        evidence_created_last_run INTEGER NOT NULL DEFAULT 0,
        candidates_created_last_run INTEGER NOT NULL DEFAULT 0,
        candidates_quarantined_last_run INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE candidate_state_transitions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        issuer_id TEXT NOT NULL REFERENCES candidate_issuers (issuer_id),
        from_state TEXT,
        to_state TEXT NOT NULL,
        at TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '',
        triggered_by TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_candidate_state_transitions_issuer ON candidate_state_transitions (issuer_id, at)",
)

# Forward-only migration steps, keyed by the version they move TO.
# Adding a new schema version later means appending a new
# (N, (...statements...)) entry here — existing entries are never edited
# or removed.
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
    (10, _V10_STATEMENTS),
    (11, _V11_STATEMENTS),
    (12, _V12_STATEMENTS),
    (13, _V13_STATEMENTS),
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
