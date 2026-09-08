"""Durable-State Phase 4B — an isolated local Postgres transactional-
state package. Code-only and local-test-only in this phase: nothing in
the application reads or writes through this package yet, and it is
never reachable from any real service entry point (see
design/DECISIONS.md's Phase 4B-0/4B-1 records). The existing JSON- and
SQLite-backed stores (src/data_access/dart/candidate_store.py,
src/data_access/state_db/) remain completely untouched and remain the
default behavior — see Settings.db_backend, which defaults to "json".

Independent from src/data_access/state_db/ by design: no shared code,
no dialect abstraction, no import relationship between the two packages
— this phase's explicit constraint (see design/DECISIONS.md).

**Postgres in this phase is a local-disposable-test store only.** No
hosted provider, credential, or connection is used, configured, or
implied by this package's existence.

Deliberate divergence from the JSON stores' read behavior, matching
src/data_access/state_db/__init__.py's own documented policy for
SQLite: a database failure (a connection failure, a missing schema, a
constraint violation) propagates as a real psycopg.Error rather than
silently becoming "zero records," per this phase's explicit requirement
not to convert a database failure into an apparently-successful empty
read."""
