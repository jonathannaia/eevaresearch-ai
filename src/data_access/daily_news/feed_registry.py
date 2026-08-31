"""Daily News Slice 1's own feed-source registry. Owns only the
approved feed URL and canonical-domain allowlist for each pilot
company — company identity, ticker, and theme come from
src/config/tracked_companies.py, the authoritative registry already
used by Radar's own three sources, reused here read-only. This module
never imports anything from src.data_access.dart/edgar/edinet and never
touches any Radar store.

Every entry below was independently verified live before being added —
see design/DECISIONS.md for the exact verification method and the two
companies (Micron, Rocket Lab) that were checked and excluded rather
than guessed.
"""
from __future__ import annotations

from dataclasses import dataclass

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
)


def tracked_company_for(company_name: str) -> TrackedCompany | None:
    """Looks up identity/ticker/theme from tracked_companies.py by exact
    name match — the one read-only touch point this module has into
    that shared registry. Returns None rather than raising if a feed
    source's company_name doesn't match any tracked company, so a
    registry typo fails safe (the pipeline skips that source and records
    it as a configuration warning) rather than crashing discovery."""
    for company in get_tracked_companies(active_only=False):
        if company.name == company_name:
            return company
    return None
