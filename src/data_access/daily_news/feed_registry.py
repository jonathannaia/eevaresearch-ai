"""Daily News Slice 1's own feed-source registry. Owns only the
approved feed URL and canonical-domain allowlist for each pilot
company — company identity, ticker, and theme come from
src/config/tracked_companies.py (the authoritative registry already
used by Radar's own three sources) or, for a company not tracked by any
Radar source, src/config/issuer_registry.py's DISCOVERY_STUBS (e.g.
Quanta Services) — both reused here read-only. This module never
imports anything from src.data_access.dart/edgar/edinet and never
touches any Radar store.

Every entry below was independently verified live before being added —
see design/DECISIONS.md for the exact verification method and the two
companies (Micron, Rocket Lab) that were checked and excluded rather
than guessed.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.config.issuer_registry import DISCOVERY_STUBS
from src.config.tracked_companies import TrackedCompany, get_tracked_companies


@dataclass(frozen=True)
class DailyNewsFeedSource:
    company_name: str  # must exactly match a real TrackedCompany.name
    feed_url: str
    feed_format: str  # "rss" | "atom" — informational only; rss_atom_client.py handles both regardless
    canonical_domains: tuple[str, ...]  # every domain an approved item link may resolve to


PILOT_FEEDS: tuple[DailyNewsFeedSource, ...] = (
    DailyNewsFeedSource(
        company_name="NVIDIA",
        feed_url="https://nvidianews.nvidia.com/releases.xml",
        feed_format="rss",
        # blogs.nvidia.com confirmed live in the verified feed's own item
        # links alongside nvidianews.nvidia.com itself (design/DECISIONS.md)
        # — both are NVIDIA-owned, not a third-party domain.
        canonical_domains=("nvidianews.nvidia.com", "blogs.nvidia.com"),
    ),
    DailyNewsFeedSource(
        company_name="Intel Corp.",
        feed_url="https://newsroom.intel.com/feed",
        feed_format="rss",
        canonical_domains=("newsroom.intel.com",),
    ),
    DailyNewsFeedSource(
        company_name="Advanced Micro Devices",
        feed_url="https://ir.amd.com/news-events/press-releases/rss",
        feed_format="rss",
        canonical_domains=("ir.amd.com",),
    ),
    DailyNewsFeedSource(
        company_name="Bloom Energy Corp",
        feed_url="https://investor.bloomenergy.com/rss/pressrelease.aspx",
        feed_format="rss",
        canonical_domains=("investor.bloomenergy.com",),
    ),
    DailyNewsFeedSource(
        company_name="Marvell Technology, Inc.",
        feed_url="https://investor.marvell.com/rss-news-feed",
        feed_format="rss",
        canonical_domains=("investor.marvell.com",),
    ),
    DailyNewsFeedSource(
        company_name="MaxLinear, Inc.",
        feed_url="https://investors.maxlinear.com/news/rss",
        feed_format="rss",
        canonical_domains=("investors.maxlinear.com",),
    ),
    DailyNewsFeedSource(
        company_name="Rockwell Automation",
        feed_url="https://rockwell2023tf.q4web.com/rss/pressrelease.aspx",
        feed_format="rss",
        # One-company exception: Rockwell's dedicated Q4-hosted IR
        # subdomain, not its own root domain. Exact hostname only —
        # canonical_url.py's existing set-membership match already
        # rejects any other q4web.com subdomain; never widen this to a
        # wildcard/suffix match across q4web.com generally.
        canonical_domains=("rockwell2023tf.q4web.com",),
    ),
    DailyNewsFeedSource(
        company_name="SK Hynix",
        feed_url="https://news.skhynix.com/en/feed",
        feed_format="rss",
        canonical_domains=("news.skhynix.com",),
    ),
    DailyNewsFeedSource(
        company_name="Quanta Services, Inc.",
        feed_url="https://investors.quantaservices.com/news-events/press-releases/rss",
        feed_format="rss",
        canonical_domains=("investors.quantaservices.com",),
    ),
    DailyNewsFeedSource(
        company_name="nVent Electric plc",
        feed_url="https://investors.nvent.com/rss/pressrelease.aspx",
        feed_format="rss",
        canonical_domains=("investors.nvent.com",),
    ),
    DailyNewsFeedSource(
        company_name="Arista Networks, Inc.",
        feed_url="https://investors.arista.com/rss/pressrelease.aspx",
        feed_format="rss",
        canonical_domains=("investors.arista.com",),
    ),
    DailyNewsFeedSource(
        company_name="Cisco Systems, Inc.",
        # Path ends in .json, but the response content is confirmed live
        # RSS 2.0 XML — rss_atom_client.py's feedparser-based parsing
        # doesn't care about the URL's own extension, only the actual
        # response body, so this requires no special-casing.
        feed_url="https://newsroom.cisco.com/c/services/i/servlets/newsroom/rssfeed.json?feed=press-releases",
        feed_format="rss",
        canonical_domains=("newsroom.cisco.com",),
    ),
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
