"""Admin Users v1 (design/DECISIONS.md) — the minimal per-account
bookkeeping record app.py's own mandatory sign-in gate writes for each
authenticated visitor, and src/ui/pages/admin_users.py reads. Shared
between the SQLite and Postgres repository modules (same convention as
src.models.theme_research), so neither backend defines its own copy.

Deliberately minimal: normalized email, an optional display name
sourced only from st.user's own "name" claim, first/last-seen
timestamps, and a sign-in count. No IP address, device/browser
fingerprint, page-view or event history, role/status, OAuth token, or
any other identity claim — this is account bookkeeping for private-beta
administration, not a profiling or analytics record."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UserAccount:
    email: str
    display_name: str | None
    first_seen_at: str
    last_seen_at: str
    sign_in_count: int
