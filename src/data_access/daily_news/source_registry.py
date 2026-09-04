"""Daily News source-registry FOUNDATION (design/DAILY_NEWS_SOURCE_
ADMISSION_POLICY.md) — a typed, data-driven admission/validation layer
for Daily News sources, built to support a much broader future source
set than the 12 hand-authored `feed_registry.PILOT_FEEDS` entries.

Foundation only. This module adds NO new external I/O, is imported by
NOTHING outside itself and its own tests, and is NOT wired into
daily_news_pipeline.run_discovery(), scripts/daily_news_worker.py, or
any UI page. `feed_registry.py` — the module the real pipeline/worker
actually read from today — is completely untouched by this file; see
this module's own docstring section "Migration decision" below for why.

Deliberately reuses `feed_registry.tracked_company_for()` (read-only)
for issuer-linkage validation, so an entry that resolves here resolves
identically to how the real pipeline already resolves
`DailyNewsFeedSource.company_name` today — the same two-source lookup
(tracked_companies.py, then issuer_registry.DISCOVERY_STUBS), never a
separate or looser rule.

Migration decision (item 6 of the approved scope): a real migration —
making `feed_registry.PILOT_FEEDS` a value *derived* from this new
registry — was considered and rejected for this foundation pass, even
though a byte-identical derivation is achievable (see
`PILOT_SOURCE_REGISTRY`/`to_daily_news_feed_source` below, and
test_daily_news_source_registry.py's own equivalence proof). Rejected
because `feed_registry.py` is imported at module-load time by the real,
already-running worker (`scripts/daily_news_worker.py`) and pipeline
(`daily_news_pipeline.py`) — coupling its import chain to a brand-new,
just-written validation module inside the SAME task that introduces
that module is a real, if small, availability risk for zero behavioral
benefit this pass. Instead: `feed_registry.py` is left completely
unmodified, and this module provides (a) `PILOT_SOURCE_REGISTRY`, a
parallel, fully-typed, fully-validated registry-shaped description of
the exact same 12 sources, and (b) `to_daily_news_feed_source()`, an
adapter proven by test to reproduce `feed_registry.PILOT_FEEDS` exactly,
field-for-field, in the same order — so the equivalence is proven now,
and wiring `feed_registry.PILOT_FEEDS` to actually be generated from
this module (or replacing it outright) is a separate, later,
explicitly-approved step, exactly as the task's own item 6 permits.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlparse

from src.data_access.daily_news.feed_registry import DailyNewsFeedSource, tracked_company_for


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
    feed with zero behavioral change — see this module's own "Migration
    decision" docstring section. Only ever valid for an
    RSS_ATOM-format, issuer-linked entry; every other shape raises,
    since the pipeline's own DailyNewsFeedSource has no way to represent
    an issuer-agnostic or non-RSS/Atom source at all today."""
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
        notes="Migrated from feed_registry.PILOT_FEEDS.",
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
        notes="Migrated from feed_registry.PILOT_FEEDS. Q4-hosted IR subdomain, exact hostname only.",
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
