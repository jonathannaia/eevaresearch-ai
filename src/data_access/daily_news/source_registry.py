"""Daily News source registry (design/DAILY_NEWS_SOURCE_ADMISSION_
POLICY.md) — a typed, data-driven admission/validation layer for Daily
News sources.

Migration status (UPDATED — Daily News source-expansion batch 1,
2026-09-04): `feed_registry.PILOT_FEEDS`, the value the real pipeline
(`daily_news_pipeline.py`) and worker (`scripts/daily_news_worker.py`)
actually read, is now DERIVED from `RUNTIME_SOURCE_REGISTRY` below via
`to_daily_news_feed_source()` — see `feed_registry.py`'s own updated
docstring for the wiring itself. This was originally deferred at this
module's first introduction (design/DECISIONS.md's "Daily News source-
registry foundation" entry) specifically to keep this brand-new
validation module decoupled from the real, already-running worker's
import chain until it had been independently proven correct — that
proof is `test_adapted_original_twelve_pilot_feeds_are_unchanged_and_
first_in_order` (tests/test_daily_news_source_registry.py), which this
same batch's own wiring change is required to keep passing.

`RUNTIME_SOURCE_REGISTRY = PILOT_SOURCE_REGISTRY + EXPANSION_BATCH_1_
SOURCE_REGISTRY` — the original 12 pilot sources, unchanged, first;
then the 7 sources added in expansion batch 1, in the exact order
given. `feed_registry.PILOT_FEEDS` reproduces this order exactly.

Deliberately reuses `feed_registry.tracked_company_for()` (read-only)
for issuer-linkage validation, so an entry that resolves here resolves
identically to how the real pipeline already resolves
`DailyNewsFeedSource.company_name` today — the same two-source lookup
(tracked_companies.py, then issuer_registry.DISCOVERY_STUBS), never a
separate or looser rule. Imported LOCALLY (inside the functions that
need it, not at module top level) together with `DailyNewsFeedSource`
itself — `feed_registry.py` now imports FROM this module at its own top
level (to build `PILOT_FEEDS`), so a top-level import in the opposite
direction here would be circular; a local/deferred import is the
standard, safe way to let two modules reference each other without
requiring either to fully load before the other. Neither loses any
functionality: both names are resolved successfully the moment they're
actually used, well after both modules have finished loading.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:  # avoids the circular import at runtime; see module docstring
    from src.data_access.daily_news.feed_registry import DailyNewsFeedSource


class SourceCategory(str, Enum):
    OFFICIAL_IR = "official_ir"
    OFFICIAL_NEWSROOM = "official_newsroom"
    OFFICIAL_FILING = "official_filing"
    REGULATOR_EXCHANGE = "regulator_exchange"
    INDEPENDENT_NEWS = "independent_news"


class SourceFormat(str, Enum):
    RSS_ATOM = "rss_atom"
    OFFICIAL_API = "official_api"
    OFFICIAL_HTML_LISTING = "official_html_listing"
    LICENSED_FEED = "licensed_feed"


class SourceHealthState(str, Enum):
    """Source-health/review lifecycle — see design/
    DAILY_NEWS_SOURCE_ADMISSION_POLICY.md for the full state-machine
    narrative. A brand-new entry starts PENDING_REVIEW; only a source
    that has actually been fetched and confirmed working (never
    guessed) may be marked VERIFIED. DEGRADED/FAILING are for a
    previously-verified source whose health has since regressed;
    RETIRED is terminal — a retired source is never polled again but
    stays in the registry for audit/provenance history."""

    PENDING_REVIEW = "pending_review"
    VERIFIED = "verified"
    DEGRADED = "degraded"
    FAILING = "failing"
    RETIRED = "retired"


class SourceRegistryValidationError(ValueError):
    """Raised only by the `assert_*` convenience wrappers below — never
    a raw/unsanitized value, always the same human-readable violation
    strings `validate_source_entry`/`find_registry_violations` return."""


# Case-insensitive, matched against attribution_label/source_id/
# issuer_name — deliberately name-based, never a guessed domain: this
# project's own established discipline is to never fabricate an
# identifier/URL it hasn't independently verified, and no verified
# domain exists locally for any of these three names.
_EXCLUDED_SOURCE_NAMES: frozenset[str] = frozenset({
    "semianalysis", "citrini research", "citrini", "serenity",
})

# Deliberately conservative and explicit — never a heuristic/suffix
# match. A domain not listed here is never assumed safe by omission;
# this is one input among several the validator checks, not the sole
# gate.
_SOCIAL_MEDIA_DOMAINS: frozenset[str] = frozenset({
    "twitter.com", "x.com", "reddit.com", "facebook.com", "instagram.com",
    "linkedin.com", "threads.net", "tiktok.com", "youtube.com", "youtu.be",
    "mastodon.social", "bsky.app", "substack.com", "medium.com",
})


@dataclass(frozen=True)
class DailyNewsSourceEntry:
    """One admitted (or candidate) Daily News source. Every field here
    is required metadata per the approved scope (design/
    DAILY_NEWS_SOURCE_ADMISSION_POLICY.md) — construction never fills
    in a plausible-looking default for anything that must be explicitly
    decided (issuer linkage, licensing classification, allowlisting)."""

    source_id: str  # stable, unique slug — never reused across a retired entry
    category: SourceCategory
    format: SourceFormat
    canonical_url: str  # the HTTPS endpoint actually fetched (feed/API/listing URL)
    domains: tuple[str, ...]  # every domain an item link from this source may resolve to
    jurisdiction: str  # free-text, matching tracked_companies.py/issuer_registry.py's own convention
    enabled: bool
    health_state: SourceHealthState
    attribution_label: str  # the publisher label shown for provenance/citation
    licensing_classification: str  # non-empty usage/rights classification
    priority: int  # 1 = highest; not yet consumed by any runtime ranking (Daily News is chronological-only today)
    # Issuer linkage — exactly one of (issuer_name set) / (issuer_agnostic
    # True) must hold; see validate_source_entry. issuer_name, when set,
    # must exactly match a real tracked_company_for() lookup.
    issuer_name: str | None = None
    issuer_agnostic: bool = False
    last_verified_at: str | None = None  # ISO 8601; None = not yet independently verified
    # Independent-news-only gate — see validate_source_entry. Ignored
    # (never itself sufficient) for every other category.
    allowlisted: bool = False
    allowed_event_filters: tuple[str, ...] = ()  # optional; empty = no filter (matches today's real behavior)
    image_host: str | None = None  # mirrors DailyNewsFeedSource.image_host exactly — see to_daily_news_feed_source
    notes: str = ""


def normalize_source_url(url: str) -> str:
    """Lowercases scheme/host, strips a trailing `/` from the path
    (never from the query string), and drops the fragment — used only
    for duplicate-detection, never for the actual fetch (the real,
    as-authored `canonical_url` is always what gets used/displayed)."""
    parsed = urlparse((url or "").strip())
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower().rstrip(".")
    path = (parsed.path or "").rstrip("/")
    normalized = f"{scheme}://{netloc}{path}"
    if parsed.query:
        normalized += f"?{parsed.query}"
    return normalized


def _has_https_url(url: str) -> bool:
    if not url or not url.strip():
        return False
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.netloc)


def validate_source_entry(entry: DailyNewsSourceEntry) -> tuple[str, ...]:
    """Pure, single-entry validation — returns every violation found
    (empty tuple = valid), never raises, never stops at the first
    problem, so a caller building an admission report sees the whole
    picture at once. Duplicate-across-registry detection is a separate,
    registry-level concern — see find_registry_violations()."""
    from src.data_access.daily_news.feed_registry import tracked_company_for  # local import — see module docstring

    violations: list[str] = []

    try:
        SourceCategory(entry.category)
    except ValueError:
        violations.append(f"unsupported category: {entry.category!r}")
    try:
        SourceFormat(entry.format)
    except ValueError:
        violations.append(f"unsupported format: {entry.format!r}")
    try:
        SourceHealthState(entry.health_state)
    except ValueError:
        violations.append(f"unsupported health_state: {entry.health_state!r}")

    if not entry.source_id or not entry.source_id.strip():
        violations.append("source_id must be non-empty")

    if not _has_https_url(entry.canonical_url):
        violations.append("canonical_url must be a non-empty https:// URL")

    if not entry.domains:
        violations.append("domains must be non-empty (at least one canonical domain)")
    else:
        for domain in entry.domains:
            normalized_domain = (domain or "").strip().lower()
            if not normalized_domain:
                violations.append("domains must not contain an empty entry")
                continue
            if normalized_domain in _SOCIAL_MEDIA_DOMAINS:
                violations.append(f"domain is a prohibited social-media domain: {domain!r}")

    if not entry.jurisdiction or not entry.jurisdiction.strip():
        violations.append("jurisdiction must be non-empty")

    if not entry.attribution_label or not entry.attribution_label.strip():
        violations.append("attribution_label must be non-empty")

    if entry.priority < 1:
        violations.append("priority must be >= 1")

    # Explicit exclusions — name-based, case-insensitive, checked
    # against every human-readable identity field on the entry.
    name_fields = (entry.attribution_label, entry.source_id, entry.issuer_name or "")
    for name_field in name_fields:
        lowered = name_field.strip().lower()
        for excluded in _EXCLUDED_SOURCE_NAMES:
            if excluded in lowered:
                violations.append(f"excluded source name matched: {excluded!r} (in {name_field!r})")

    # Issuer linkage — exactly one of issuer_name / issuer_agnostic.
    if entry.issuer_name and entry.issuer_agnostic:
        violations.append("issuer_name and issuer_agnostic are mutually exclusive — set exactly one")
    elif not entry.issuer_name and not entry.issuer_agnostic:
        violations.append("must set issuer_name or explicitly set issuer_agnostic=True")

    # Official-category sources must map to a real canonical tracked
    # issuer — the exact same lookup daily_news_pipeline.run_discovery()
    # itself already performs (tracked_company_for), so an entry that
    # validates here is guaranteed resolvable by the real pipeline too.
    _OFFICIAL_ISSUER_CATEGORIES = (
        SourceCategory.OFFICIAL_IR, SourceCategory.OFFICIAL_NEWSROOM, SourceCategory.OFFICIAL_FILING,
    )
    if entry.category in _OFFICIAL_ISSUER_CATEGORIES and not entry.issuer_agnostic:
        if entry.issuer_name and tracked_company_for(entry.issuer_name) is None:
            violations.append(
                f"issuer_name {entry.issuer_name!r} does not resolve via tracked_company_for() "
                "— official issuer sources must map to an existing canonical tracked issuer"
            )

    # licensing_classification is required metadata for every source,
    # independent_news included — checked once, regardless of category.
    if not entry.licensing_classification or not entry.licensing_classification.strip():
        violations.append("licensing_classification must be non-empty")

    # Independent news carries one additional requirement beyond every
    # other category: explicit allowlisting. A non-empty
    # licensing_classification (checked above, unconditionally) is
    # still required but no longer sufficient on its own for this
    # category — allowlisted=True must also be set.
    if entry.category == SourceCategory.INDEPENDENT_NEWS and not entry.allowlisted:
        violations.append("independent_news sources must be explicitly allowlisted (allowlisted=True)")

    return tuple(violations)


def assert_valid_source_entry(entry: DailyNewsSourceEntry) -> None:
    violations = validate_source_entry(entry)
    if violations:
        raise SourceRegistryValidationError("; ".join(violations))


def find_registry_violations(entries: tuple[DailyNewsSourceEntry, ...]) -> tuple[str, ...]:
    """Every per-entry violation (prefixed with that entry's source_id)
    plus registry-wide duplicate detection: two entries whose
    (normalized canonical_url, issuer key, category) triple collide are
    both reported — normalization never treats two different real URLs
    as the same, and never lets a trailing-slash/case difference hide a
    genuine duplicate."""
    violations: list[str] = []
    seen: dict[tuple[str, str, str], str] = {}

    for entry in entries:
        for violation in validate_source_entry(entry):
            violations.append(f"{entry.source_id}: {violation}")

        issuer_key = entry.issuer_name if entry.issuer_name else "__issuer_agnostic__"
        key = (normalize_source_url(entry.canonical_url), issuer_key, str(entry.category))
        if key in seen:
            violations.append(
                f"{entry.source_id}: duplicate of {seen[key]!r} — same normalized canonical_url + "
                "issuer + category"
            )
        else:
            seen[key] = entry.source_id

    return tuple(violations)


def to_daily_news_feed_source(entry: DailyNewsSourceEntry) -> DailyNewsFeedSource:
    """Adapter proving this registry can represent an existing pilot
    feed with zero behavioral change — see this module's own docstring.
    Only ever valid for an RSS_ATOM-format, issuer-linked entry; every
    other shape raises, since the pipeline's own DailyNewsFeedSource has
    no way to represent an issuer-agnostic or non-RSS/Atom source at all
    today. This is the exact function `feed_registry.py` now calls, at
    its own module-load time, to build the real, live `PILOT_FEEDS`."""
    from src.data_access.daily_news.feed_registry import DailyNewsFeedSource  # local import — see module docstring

    if entry.format != SourceFormat.RSS_ATOM:
        raise SourceRegistryValidationError(
            f"{entry.source_id}: to_daily_news_feed_source only supports SourceFormat.RSS_ATOM, got {entry.format!r}"
        )
    if entry.issuer_agnostic or not entry.issuer_name:
        raise SourceRegistryValidationError(
            f"{entry.source_id}: to_daily_news_feed_source requires an issuer-linked entry (issuer_agnostic=False, issuer_name set)"
        )
    return DailyNewsFeedSource(
        company_name=entry.issuer_name,
        feed_url=entry.canonical_url,
        # Informational-only field on the old model (rss_atom_client.py's
        # own docstring: feedparser handles both formats transparently
        # regardless of this string) — every pilot entry migrated below
        # is a real RSS feed, so "rss" reproduces the old model exactly;
        # this adapter is not yet asked to distinguish a hypothetical
        # Atom-only source, since none exists in PILOT_FEEDS today.
        feed_format="rss",
        canonical_domains=entry.domains,
        image_host=entry.image_host,
    )


_PILOT_LICENSING_CLASSIFICATION = (
    "Official company source (investor-relations or newsroom RSS feed) — public press-release "
    "content; headline, extractive excerpt, and direct link only, per this project's existing "
    "no-full-article-reproduction policy (see src/data_access/daily_news/summary_grounding.py)."
)

# Parallel, fully-typed description of the exact same 12 sources in
# feed_registry.PILOT_FEEDS, in the same order — same URLs, same
# domains, same image hosts, proven byte-identical via
# to_daily_news_feed_source() (see test_daily_news_source_registry.py's
# own equivalence test). health_state is VERIFIED for all 12 (they are
# the real, already-live pilot feeds); last_verified_at is deliberately
# None for all 12 — no per-entry verification timestamp was tracked
# before this registry existed, and inventing one now would be exactly
# the kind of fabricated fact this project consistently refuses to
# record (see design/DECISIONS.md's own Daily News Slice 1 entry for
# the real, narrative verification history instead). priority is a
# uniform placeholder (not yet consumed by any runtime ranking — Daily
# News remains chronological-only, unchanged by this foundation).
PILOT_SOURCE_REGISTRY: tuple[DailyNewsSourceEntry, ...] = (
    DailyNewsSourceEntry(
        source_id="nvidia-newsroom-rss", category=SourceCategory.OFFICIAL_NEWSROOM, format=SourceFormat.RSS_ATOM,
        canonical_url="https://nvidianews.nvidia.com/releases.xml",
        domains=("nvidianews.nvidia.com", "blogs.nvidia.com"),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="NVIDIA", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="NVIDIA", image_host="iprsoftwaremedia.com",
        notes="Migrated from feed_registry.PILOT_FEEDS — see that module's own entry for verification provenance.",
    ),
    DailyNewsSourceEntry(
        source_id="intel-ir-rss", category=SourceCategory.OFFICIAL_IR, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.intc.com/news-events/press-releases/rss",
        domains=("www.intc.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Intel Corp.", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="Intel Corp.",
        notes=(
            "Migrated from feed_registry.PILOT_FEEDS. Repaired (Daily News feed audit, design/"
            "DECISIONS.md) — newsroom.intel.com/feed no longer served RSS at all (404s into an "
            "Access-Denied redirector; confirmed live). Replaced with Intel's own official "
            "investor-relations RSS feed (www.intc.com), confirmed live and fetchable with this "
            "app's own worker User-Agent."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="amd-ir-rss", category=SourceCategory.OFFICIAL_IR, format=SourceFormat.RSS_ATOM,
        canonical_url="https://ir.amd.com/news-events/press-releases/rss",
        domains=("ir.amd.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Advanced Micro Devices", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="Advanced Micro Devices",
        notes="Migrated from feed_registry.PILOT_FEEDS.",
    ),
    DailyNewsSourceEntry(
        source_id="bloom-energy-ir-rss", category=SourceCategory.OFFICIAL_IR, format=SourceFormat.RSS_ATOM,
        canonical_url="https://investor.bloomenergy.com/rss/pressrelease.aspx",
        domains=("investor.bloomenergy.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Bloom Energy Corp", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="Bloom Energy Corp",
        notes="Migrated from feed_registry.PILOT_FEEDS.",
    ),
    DailyNewsSourceEntry(
        source_id="marvell-ir-rss", category=SourceCategory.OFFICIAL_IR, format=SourceFormat.RSS_ATOM,
        canonical_url="https://investor.marvell.com/rss-news-feed",
        domains=("investor.marvell.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Marvell Technology, Inc.", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="Marvell Technology, Inc.",
        notes="Migrated from feed_registry.PILOT_FEEDS.",
    ),
    DailyNewsSourceEntry(
        source_id="maxlinear-ir-rss", category=SourceCategory.OFFICIAL_IR, format=SourceFormat.RSS_ATOM,
        canonical_url="https://investors.maxlinear.com/news/rss",
        domains=("investors.maxlinear.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="MaxLinear, Inc.", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="MaxLinear, Inc.",
        notes="Migrated from feed_registry.PILOT_FEEDS.",
    ),
    DailyNewsSourceEntry(
        source_id="rockwell-automation-ir-rss", category=SourceCategory.OFFICIAL_IR, format=SourceFormat.RSS_ATOM,
        canonical_url="https://rockwell2023tf.q4web.com/rss/pressrelease.aspx",
        domains=("rockwell2023tf.q4web.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Rockwell Automation", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="Rockwell Automation",
        notes=(
            "Migrated from feed_registry.PILOT_FEEDS. One-company exception: Rockwell's "
            "dedicated Q4-hosted IR subdomain, not its own root domain. Exact hostname only — "
            "canonical_url.py's existing set-membership match already rejects any other "
            "q4web.com subdomain; never widen this to a wildcard/suffix match across q4web.com "
            "generally."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="sk-hynix-newsroom-rss", category=SourceCategory.OFFICIAL_NEWSROOM, format=SourceFormat.RSS_ATOM,
        canonical_url="https://news.skhynix.com/en/feed",
        domains=("news.skhynix.com",),
        jurisdiction="South Korea", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="SK Hynix", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="SK Hynix", image_host="d18r0a86za96sg.cloudfront.net",
        notes="Migrated from feed_registry.PILOT_FEEDS.",
    ),
    DailyNewsSourceEntry(
        source_id="quanta-services-ir-rss", category=SourceCategory.OFFICIAL_IR, format=SourceFormat.RSS_ATOM,
        canonical_url="https://investors.quantaservices.com/news-events/press-releases/rss",
        domains=("investors.quantaservices.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Quanta Services, Inc.", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="Quanta Services, Inc.",
        notes="Migrated from feed_registry.PILOT_FEEDS.",
    ),
    DailyNewsSourceEntry(
        source_id="nvent-electric-ir-rss", category=SourceCategory.OFFICIAL_IR, format=SourceFormat.RSS_ATOM,
        canonical_url="https://investors.nvent.com/rss/pressrelease.aspx",
        domains=("investors.nvent.com",),
        jurisdiction="Ireland", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="nVent Electric plc", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="nVent Electric plc",
        notes="Migrated from feed_registry.PILOT_FEEDS.",
    ),
    DailyNewsSourceEntry(
        source_id="arista-networks-ir-rss", category=SourceCategory.OFFICIAL_IR, format=SourceFormat.RSS_ATOM,
        canonical_url="https://investors.arista.com/rss/pressrelease.aspx",
        domains=("investors.arista.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Arista Networks, Inc.", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="Arista Networks, Inc.",
        notes="Migrated from feed_registry.PILOT_FEEDS.",
    ),
    DailyNewsSourceEntry(
        source_id="cisco-newsroom-rss", category=SourceCategory.OFFICIAL_NEWSROOM, format=SourceFormat.RSS_ATOM,
        canonical_url="https://newsroom.cisco.com/c/services/i/servlets/newsroom/rssfeed.json?feed=press-releases",
        domains=("newsroom.cisco.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Cisco Systems, Inc.", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="Cisco Systems, Inc.", image_host="newsroom.cisco.com",
        notes=(
            "Migrated from feed_registry.PILOT_FEEDS. URL path ends in .json but the response "
            "content is real RSS 2.0 XML (confirmed live, see feed_registry.py's own comment)."
        ),
    ),
)


# Daily News source-expansion batch 1 (2026-09-04) — 7 official issuer
# IR/newsroom RSS feeds, each independently live-verified (real fetch,
# HTTP 200, parseable RSS 2.0, on-domain dated items) in a separate,
# bounded, read-only verification pass before this batch was approved.
# Appended AFTER PILOT_SOURCE_REGISTRY, never interleaved — see
# RUNTIME_SOURCE_REGISTRY below, which preserves this exact ordering.
EXPANSION_BATCH_1_SOURCE_REGISTRY: tuple[DailyNewsSourceEntry, ...] = (
    DailyNewsSourceEntry(
        source_id="amazon-ir-rss", category=SourceCategory.OFFICIAL_IR, format=SourceFormat.RSS_ATOM,
        canonical_url="https://ir.aboutamazon.com/rss/pressrelease.aspx",
        domains=("ir.aboutamazon.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Amazon.com, Inc.", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="Amazon.com, Inc.", last_verified_at="2026-09-04",
        notes=(
            "Daily News source-expansion batch 1 (2026-09-04) — live-verified official IR RSS "
            "feed, HTTP 200, 10 dated items, newest 2026-07-30. One item (a proxy-statement link) "
            "resolves off-domain (ezodproxy.com) — the existing per-item canonical_url gate "
            "already suppresses that single item; it does not affect feed-level admission."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="meta-ir-rss", category=SourceCategory.OFFICIAL_IR, format=SourceFormat.RSS_ATOM,
        canonical_url="https://investor.atmeta.com/rss/pressrelease.aspx",
        domains=("investor.atmeta.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Meta Platforms, Inc.", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="Meta Platforms, Inc.", last_verified_at="2026-09-04",
        notes=(
            "Daily News source-expansion batch 1 (2026-09-04) — live-verified official IR RSS "
            "feed, HTTP 200, 10 dated items, newest 2026-08-26, all items on-domain."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="oracle-ir-rss", category=SourceCategory.OFFICIAL_IR, format=SourceFormat.RSS_ATOM,
        canonical_url="https://investor.oracle.com/rss/pressrelease.aspx",
        domains=("investor.oracle.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Oracle Corporation", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="Oracle Corporation", last_verified_at="2026-09-04",
        notes=(
            "Daily News source-expansion batch 1 (2026-09-04) — live-verified official IR RSS "
            "feed, HTTP 200, 10 dated items, newest 2026-09-02, all items on-domain."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="applied-materials-ir-rss", category=SourceCategory.OFFICIAL_IR, format=SourceFormat.RSS_ATOM,
        canonical_url="https://ir.appliedmaterials.com/rss/news-releases.xml",
        domains=("ir.appliedmaterials.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Applied Materials, Inc.", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="Applied Materials, Inc.", last_verified_at="2026-09-04",
        notes=(
            "Daily News source-expansion batch 1 (2026-09-04) — live-verified official IR RSS "
            "feed (discovered via the site's own declared <link rel=alternate> feed tag), HTTP "
            "200, 10 dated items, newest 2026-08-27, all items on-domain."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="lam-research-newsroom-rss", category=SourceCategory.OFFICIAL_NEWSROOM, format=SourceFormat.RSS_ATOM,
        canonical_url="https://newsroom.lamresearch.com/press-releases?pagetemplate=rss",
        domains=("newsroom.lamresearch.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Lam Research Corp", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="Lam Research Corp", last_verified_at="2026-09-04",
        notes=(
            "Daily News source-expansion batch 1 (2026-09-04) — live-verified official newsroom "
            "RSS feed (linked directly from the real investor.lamresearch.com IR site's own "
            "'Press Releases Site' links; discovered via the page's own declared <link "
            "rel=alternate> feed tag), HTTP 200, 5 dated items, newest 2026-08-27, on-domain."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="kla-ir-rss", category=SourceCategory.OFFICIAL_IR, format=SourceFormat.RSS_ATOM,
        canonical_url="https://ir.kla.com/news-events/press-releases/rss",
        domains=("ir.kla.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="KLA Corp", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="KLA Corp", last_verified_at="2026-09-04",
        notes=(
            "Daily News source-expansion batch 1 (2026-09-04) — live-verified official IR RSS "
            "feed (discovered via an explicit on-page 'RSS News Feed' link), HTTP 200, 10 dated "
            "items, newest 2026-08-20, all items on-domain."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="arm-newsroom-rss", category=SourceCategory.OFFICIAL_NEWSROOM, format=SourceFormat.RSS_ATOM,
        canonical_url="https://newsroom.arm.com/news/feed/",
        domains=("newsroom.arm.com",),
        jurisdiction="United Kingdom", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Arm Holdings plc", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="Arm Holdings plc", last_verified_at="2026-09-04",
        notes=(
            "Daily News source-expansion batch 1 (2026-09-04) — live-verified official newsroom "
            "RSS feed (linked from investors.arm.com; discovered via the page's own declared "
            "<link rel=alternate> feed tag), HTTP 200, 6 dated items, newest 2026-07-29, on-domain."
        ),
    ),
)

# The real, live runtime feed list — original 12 pilot sources first
# (byte-identical, same order), then expansion batch 1's 7 sources, in
# the exact order given. feed_registry.PILOT_FEEDS is generated from
# this tuple via to_daily_news_feed_source(); see that module's own
# updated docstring.
RUNTIME_SOURCE_REGISTRY: tuple[DailyNewsSourceEntry, ...] = PILOT_SOURCE_REGISTRY + EXPANSION_BATCH_1_SOURCE_REGISTRY
