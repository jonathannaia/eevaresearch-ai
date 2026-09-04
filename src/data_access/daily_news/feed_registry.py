"""Daily News Slice 1's own feed-source registry. Owns only the
approved feed URL and canonical-domain allowlist for each pilot
company — company identity, ticker, and theme come from
src/config/tracked_companies.py (the authoritative registry already
used by Radar's own three sources) or, for a company not tracked by any
Radar source, src/config/issuer_registry.py's DISCOVERY_STUBS (e.g.
Quanta Services) — both reused here read-only. This module never
imports anything from src.data_access.dart/edgar/edinet and never
touches any Radar store.

Every entry was independently verified live before being added — see
design/DECISIONS.md for the exact verification method and the two
companies (Micron, Rocket Lab) that were checked and excluded rather
than guessed.

Migration (Daily News source-expansion batch 1, 2026-09-04): `PILOT_FEEDS`
below is now DERIVED from `source_registry.RUNTIME_SOURCE_REGISTRY` via
`source_registry.to_daily_news_feed_source()`, rather than being a
hand-typed literal tuple — this replaces the module's own former literal
12-entry tuple with a computed value proven, by
`tests/test_daily_news_source_registry.py::
test_adapted_original_twelve_pilot_feeds_are_unchanged_and_first_in_order`,
to reproduce those exact same 12 entries, field-for-field, in the same
order, as the first 12 of the resulting 19. The 7 entries appended after
them are Daily News source-expansion batch 1's own additions — see
`source_registry.EXPANSION_BATCH_1_SOURCE_REGISTRY` for their full,
individually-verified metadata (each independently live-verified: real
fetch, HTTP 200, parseable RSS 2.0, on-domain dated items).

`source_registry.py` does a LOCAL (function-body, not top-level) import
of `DailyNewsFeedSource` from this module — the two modules reference
each other, so a top-level import in both directions would be circular;
see source_registry.py's own docstring for why a local/deferred import
is the safe, standard way to resolve this. This module's own top-level
import of `source_registry` is safe in either direction, since
source_registry.py has zero top-level dependency back on this module.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.config.issuer_registry import DISCOVERY_STUBS
from src.config.tracked_companies import TrackedCompany, get_tracked_companies
from src.data_access.daily_news.source_registry import (
    RUNTIME_SOURCE_REGISTRY,
    SourceFormat,
    to_daily_news_feed_source,
)


@dataclass(frozen=True)
class DailyNewsFeedSource:
    company_name: str  # must exactly match a real TrackedCompany.name
    feed_url: str
    feed_format: str  # "rss" | "atom" — informational only; rss_atom_client.py handles both regardless
    canonical_domains: tuple[str, ...]  # every domain an approved item link may resolve to
    # Separate, stricter allowlist for a source-provided item IMAGE —
    # deliberately never derived from canonical_domains above (an image
    # CDN is commonly a different host than the IR page itself — verified
    # live for NVIDIA/SK Hynix, see design/DECISIONS.md). None means no
    # image host has been explicitly approved for this source; every
    # candidate image then fails closed via canonical_url.validate_image_url().
    image_host: str | None = None


# Derived from source_registry.RUNTIME_SOURCE_REGISTRY — see this
# module's own docstring. Every enabled, RSS/Atom, issuer-linked entry
# in that registry becomes one DailyNewsFeedSource here, in the exact
# order the registry lists them: the original 12 pilot sources first
# (their full individual verification history now lives on their own
# entries in source_registry.PILOT_SOURCE_REGISTRY), then Daily News
# source-expansion batch 1's 7 sources.
PILOT_FEEDS: tuple[DailyNewsFeedSource, ...] = tuple(
    to_daily_news_feed_source(entry)
    for entry in RUNTIME_SOURCE_REGISTRY
    if entry.enabled and entry.format == SourceFormat.RSS_ATOM
)


def tracked_company_for(company_name: str) -> TrackedCompany | None:
    """Looks up identity/ticker/theme by exact name match, checking two
    read-only sources in order:

    1. tracked_companies.py — every company also in Radar's own scan
       universe.
    2. issuer_registry.DISCOVERY_STUBS — a company known to Daily News
       only (e.g. Quanta Services), never onboarded to any Radar source.
       A match here is synthesized into a TrackedCompany purely for this
       module's own return-shape contract: `source=""` (never equal to
       any real "OpenDART / DART"/"SEC EDGAR"/"EDINET" value) and
       `active=False`. This synthesized record is never written back to
       tracked_companies.py, never passed to with_resolved_ciks/
       with_resolved_corp_codes, and cannot enter any EDGAR/DART/EDINET
       pipeline — those only ever call get_tracked_companies_for_source()
       against the real TRACKED_COMPANIES tuple directly, which
       DISCOVERY_STUBS is never merged into (see
       tracked_companies_from_issuer_registry()'s own docstring for why
       that exclusion is structural, not a filter that could be
       forgotten).

    Returns None if neither source matches, so a registry typo fails
    safe (the pipeline skips that source and records it as a
    configuration warning) rather than crashing discovery."""
    for company in get_tracked_companies(active_only=False):
        if company.name == company_name:
            return company

    for issuer in DISCOVERY_STUBS:
        if issuer.legal_name == company_name:
            return TrackedCompany(
                name=issuer.legal_name, exchange=issuer.primary_exchange or "",
                krx_code=issuer.primary_ticker or "", source="",
                themes=issuer.themes, subthemes=issuer.subthemes, active=False,
                notes=issuer.notes,
            )

    return None
