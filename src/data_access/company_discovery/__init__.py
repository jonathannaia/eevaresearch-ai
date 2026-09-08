"""Company Discovery — Phase 2 (passive Candidate Ledger only). Pure,
read-existing-data-only package: no import of `requests`, `feedparser`,
any `*_client.py`, `rss_atom_client`, or any translation-provider
module anywhere under this package — enforced by
`tests/test_company_discovery_scope_guard.py`, not just documented.
Never imports from `src.ui` and is never imported by any existing
public page. See each module's own docstring for its specific role."""
