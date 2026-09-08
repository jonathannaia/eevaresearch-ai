"""Durable-State Phase 1 — a local SQLite transactional-state foundation
(design/DECISIONS.md). Code-only in this phase: nothing in the app reads
or writes through this package yet. The existing JSON-backed cache
modules (`src/data_access/dart/candidate_store.py`,
`src/data_access/{dart,edgar,edinet}/scan_service.py`,
`src/data_access/edgar/cik_resolver.py`,
`src/data_access/dart/corp_code_resolver.py`) remain completely
untouched and remain the default behavior — see `Settings.db_backend`,
which defaults to `"json"`.

**SQLite in this phase is a local-development/test store only.** A
SQLite file inside a Streamlit Community Cloud container must be treated
as ephemeral unless a persistent volume is separately verified and
explicitly approved — this package does not claim, configure, or test
otherwise (see design/DECISIONS.md).

Deliberate divergence from the existing JSON stores' read behavior: a
JSON loader treats a missing/corrupt file as an empty result and never
raises (`candidate_store.load_candidates`'s own documented convention).
This package's readers do the opposite on purpose — a database failure
(a corrupt file, a locked file, a missing schema) propagates as a real
`sqlite3.Error` rather than silently becoming "zero records," per this
phase's explicit requirement not to convert a database failure into an
apparently-successful empty read."""
