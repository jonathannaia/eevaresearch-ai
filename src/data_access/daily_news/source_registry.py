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
    # Daily News Source-Expansion & Ingestion Design, Batch 1 (source-
    # registry/model foundation only — no source entry anywhere in this
    # registry uses any of these six yet, and none is enabled/created for
    # live use by this batch). ISSUER_IR/ISSUER_NEWSROOM are the
    # finer-grained successors the approved design proposes for
    # OFFICIAL_IR/OFFICIAL_NEWSROOM; REGULATOR_EXCHANGE similarly splits
    # into EXCHANGE/REGULATOR. All four original members above are kept
    # completely unchanged — every existing DailyNewsSourceEntry
    # (PILOT_SOURCE_REGISTRY, EXPANSION_BATCH_1/2) and every existing
    # caller keeps working with zero behavioral change; this is a purely
    # additive vocabulary extension, not a rename or migration.
    ISSUER_IR = "issuer_ir"
    ISSUER_NEWSROOM = "issuer_newsroom"
    EXCHANGE = "exchange"
    REGULATOR = "regulator"
    GOVERNMENT_POLICY = "government_policy"
    GOVERNMENT_PROCUREMENT = "government_procurement"


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


class SourceReliabilityTier(str, Enum):
    """Daily News Source-Expansion & Ingestion Design, Batch 1 — a
    separate, additive reliability/admission classification, deliberately
    NOT merged into or replacing SourceHealthState above. SourceHealthState
    is that enum's own existing fetch-health lifecycle (pending_review ->
    verified -> degraded/failing -> retired) and is left completely
    unchanged, per this batch's explicit "do not alter current
    source-health behavior" scope. No existing DailyNewsSourceEntry
    references this enum, and no field on that dataclass uses it yet —
    it exists only as a typed vocabulary for a later, separately-approved
    batch (Tier 2 independent-news admission, and a filing-derived
    candidate's own shadow/publish eligibility — see
    filing_event_models.py / policy_disclosure_models.py).

    VERIFIED / PROBATIONARY / RETIRED describe an admission decision
    ("is this source trustworthy enough to publish from"), distinct from
    SourceHealthState's own question ("is this source's feed currently
    fetching successfully"). SHADOW_ONLY and VALIDATION_REQUIRED are new
    states with no SourceHealthState equivalent at all: SHADOW_ONLY marks
    a source/category admitted for internal observation only, never
    autonomous publication; VALIDATION_REQUIRED marks a proposed source
    whose feed/API existence, terms, and stability have not yet been
    independently live-verified — the same posture this project's design
    document uses for every unverified candidate row it proposed."""

    VERIFIED = "verified"
    PROBATIONARY = "probationary"
    RETIRED = "retired"
    SHADOW_ONLY = "shadow_only"
    VALIDATION_REQUIRED = "validation_required"


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


def contains_excluded_source_name(text: str) -> bool:
    """Case-insensitive substring check against this module's fixed,
    permanently-excluded source-name list (SemiAnalysis, Citrini
    Research, Serenity) — same `_EXCLUDED_SOURCE_NAMES` set
    `validate_source_entry()` below already checks inline. Extracted as
    its own reusable, public function (Daily News Source-Expansion &
    Ingestion Design, Batch 1) so any other Daily News foundation module
    with its own free-text source/publisher-name-shaped field (e.g.
    policy_disclosure_models.OfficialDisclosureProvenance.issuing_body)
    can reuse the exact same check rather than re-declaring the excluded
    set. `validate_source_entry()`'s own existing inline loop is left
    untouched — this is a pure addition, not a refactor of that
    already-tested code path."""
    lowered = (text or "").strip().lower()
    return any(excluded in lowered for excluded in _EXCLUDED_SOURCE_NAMES)


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
    # Dashboard/Signals quality fix (design/
    # DASHBOARD_SIGNAL_QUALITY_FIX_DESIGN.md) — a curated, per-source
    # declared language, the same discipline already used for
    # attribution_label/jurisdiction/issuer_name (a human-verified fact
    # about the source, never derived from text). Additive,
    # default-preserving: every existing entry defaults to "English",
    # its own real, already-correct language, so no currently-registered
    # source's behavior changes. Mirrors DailyNewsFeedSource.language
    # exactly — see to_daily_news_feed_source below. This is the only
    # reliable signal this app has for a Latin-script non-English source
    # (e.g. French): the prior mechanism (summary_grounding.py's
    # non-Latin-script check) can only ever detect CJK/Hangul script,
    # never a Latin-script language.
    language: str = "English"


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
    # Extended (Batch 1) to include ISSUER_IR/ISSUER_NEWSROOM — the new
    # categories' own future issuer-linked entries must resolve via
    # tracked_company_for() exactly like OFFICIAL_IR/OFFICIAL_NEWSROOM/
    # OFFICIAL_FILING already do. No existing entry uses either new
    # category, so this extension changes zero existing validation
    # outcomes.
    _OFFICIAL_ISSUER_CATEGORIES = (
        SourceCategory.OFFICIAL_IR, SourceCategory.OFFICIAL_NEWSROOM, SourceCategory.OFFICIAL_FILING,
        SourceCategory.ISSUER_IR, SourceCategory.ISSUER_NEWSROOM,
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
        source_id=entry.source_id,
        language=entry.language,
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

# Daily News source-expansion batch 2 (2026-09-04) — exactly one entry:
# a worker-context validation candidate for the existing meta-ir-rss
# source, which is reportedly returning HTTPError:403. This entry is
# NOT a replacement for meta-ir-rss — that entry stays unchanged,
# enabled, and first in registry order; this one is appended after it
# to let the real worker's own fetch attempt (not this browser-context
# check) determine whether about.fb.com avoids the same block. Health
# state is deliberately NEEDS_REVIEW, never VERIFIED — a real browser
# navigation confirmed this feed is live, fresh, and on-domain, but
# that does not confirm it avoids the SAME bot/WAF block under the
# worker's own distinct fetch signature (User-Agent, IP, headers) that
# is causing meta-ir-rss's reported 403 — only a real worker-context
# fetch attempt can resolve that question. See this entry's own `notes`.
EXPANSION_BATCH_2_SOURCE_REGISTRY: tuple[DailyNewsSourceEntry, ...] = (
    DailyNewsSourceEntry(
        source_id="meta-newsroom-rss", category=SourceCategory.OFFICIAL_NEWSROOM, format=SourceFormat.RSS_ATOM,
        canonical_url="https://about.fb.com/feed/",
        domains=("about.fb.com",),
        # Requested as health_state="needs_review" — no such member exists
        # on SourceHealthState (PENDING_REVIEW, VERIFIED, DEGRADED,
        # FAILING, RETIRED only), and this task's own scope forbids
        # changing validation/categories, so no new member was added.
        # PENDING_REVIEW is used instead — its own docstring describes
        # exactly this situation: "never treated as a live, trustworthy
        # source until it has actually been fetched and confirmed
        # working." See this entry's own notes below.
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.PENDING_REVIEW,
        attribution_label="Meta Platforms, Inc.", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="Meta Platforms, Inc.", last_verified_at="2026-09-04",
        notes=(
            "Worker-context validation candidate for the existing, reportedly-blocked "
            "meta-ir-rss source (investor.atmeta.com, HTTPError:403). Not confirmed to bypass "
            "that 403 under the real worker's own fetch signature."
        ),
    ),
)

# Daily News source-expansion batch 3 (2026-09-11) — 4 official issuer
# IR RSS feeds, each independently live-verified this batch (real
# fetch, HTTP 200, parseable RSS 2.0, on-domain per-article item links
# — not a homepage/search redirect) AND independently proven, via a
# bounded local run_discovery() smoke test against a temp cache
# directory (no production state touched), to actually publish real
# stories under the existing, unmodified pipeline/gates. Selected from
# the tracked-company roster's coverage gap (companies with no existing
# Daily News source), prioritized for AI Buildout/Memory/Photonics
# theme relevance and material-news likelihood (earnings, product
# announcements, capacity/capex updates). Company-agnostic editorial or
# government sources (CNBC, Korea Herald, Yonhap, White House,
# Commerce/BIS, DOE) were explicitly out of scope for this batch — see
# design/DECISIONS.md.
#
# Attempted and excluded this same batch, with the specific reason —
# never silently dropped:
#   - Micron Technology (investors.micron.com): feed is live, parses,
#     and its item links looked correct on inspection, but the smoke
#     test proved every one of its 10 items is suppressed by the
#     existing, unmodified canonical_url.validate_canonical_url() gate
#     with reason "No valid canonical source URL" — Micron's own feed
#     emits plain-http:// (not https://) <link> values, and that gate
#     requires parsed.scheme == "https" unconditionally. Zero stories
#     would ever publish from this feed as configured; adding it would
#     have been a dead, misleading entry. This is a data-safety gate
#     already relied on everywhere else in Daily News, not a defect
#     introduced here, and is explicitly not weakened by this batch.
#     Revisit only if Micron's own feed later serves https:// links.
#   - Credo Technology Group Holding Ltd (investors.credosemi.com):
#     feed is live and parses, but its one item carries no <link>
#     element at all — canonical_url.validate_canonical_url() would
#     reject every item outright, so no story could ever publish from
#     it. Revisit only if Credo's own feed later adds real per-article
#     links.
#   - Microsoft Corporation (news.microsoft.com/feed/): feed is live
#     and parses, but its newest item is dated 2025-05-07 — over a year
#     stale relative to this verification — so it would surface as a
#     permanently-empty/stale source under the existing 7-day freshness
#     window, not a real material-news feed. This is Microsoft's own
#     general "Stories" feed, not a dedicated press-release feed; no
#     working press-release-specific feed was found this batch.
#   - Broadcom Inc., Texas Instruments Incorporated, Analog Devices
#     Inc., Vertiv Holdings Co, CoreWeave, Inc., Teradyne, Inc,
#     Constellation Energy Corporation, GlobalFoundries Inc., Coherent
#     Corp, Skyworks Solutions Inc., Monolithic Power Systems Inc.,
#     Entegris Inc., Amkor Technology Inc., Onto Innovation Inc.,
#     Axcelis Technologies Inc., MKS Inc: no working RSS/Atom feed
#     confirmed this batch — either the fetch attempt timed out/was
#     blocked, the guessed conventional feed path 404'd, or the
#     company's own IR site (confirmed via direct page fetch, not
#     guessed) offers only email alerts, no RSS. None of these was
#     added on a guess; each needs its own separate, successful live
#     verification before it can be proposed again.
EXPANSION_BATCH_3_SOURCE_REGISTRY: tuple[DailyNewsSourceEntry, ...] = (
    DailyNewsSourceEntry(
        source_id="qualcomm-ir-rss", category=SourceCategory.OFFICIAL_IR, format=SourceFormat.RSS_ATOM,
        canonical_url="https://investor.qualcomm.com/rss/pressrelease.aspx",
        domains=("investor.qualcomm.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Qualcomm Incorporated", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="Qualcomm Incorporated", last_verified_at="2026-09-11",
        notes=(
            "Daily News source-expansion batch 3 (2026-09-11) — live-verified official IR RSS "
            "feed, HTTP 200, dated items (newest 2026-09-08), each with a real per-article "
            "investor.qualcomm.com/news-events/press-releases/... link, on-domain. Bounded local "
            "run_discovery() smoke test (temp cache dir, no production state touched) confirmed "
            "8 real stories published from this feed."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="corning-ir-rss", category=SourceCategory.OFFICIAL_IR, format=SourceFormat.RSS_ATOM,
        canonical_url="https://investor.corning.com/rss/pressrelease.aspx",
        domains=("investor.corning.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Corning Incorporated", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="Corning Inc.", last_verified_at="2026-09-11",
        notes=(
            "Daily News source-expansion batch 3 (2026-09-11) — live-verified official IR RSS "
            "feed, HTTP 200, dated items (newest 2026-07-28), each with a real per-article "
            "investor.corning.com/news-and-events/news/... link, on-domain. Bounded local "
            "run_discovery() smoke test (temp cache dir, no production state touched) confirmed "
            "9 real stories published from this feed."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="synopsys-ir-rss", category=SourceCategory.OFFICIAL_IR, format=SourceFormat.RSS_ATOM,
        canonical_url="https://investor.synopsys.com/rss/pressrelease.aspx",
        domains=("investor.synopsys.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Synopsys, Inc.", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="Synopsys, Inc.", last_verified_at="2026-09-11",
        notes=(
            "Daily News source-expansion batch 3 (2026-09-11) — live-verified official IR RSS "
            "feed, HTTP 200, dated items (newest 2026-08-26), each with a real per-article "
            "investor.synopsys.com/news/news-details/... link, on-domain. Bounded local "
            "run_discovery() smoke test (temp cache dir, no production state touched) confirmed "
            "10 real stories published from this feed."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="cadence-ir-rss", category=SourceCategory.OFFICIAL_IR, format=SourceFormat.RSS_ATOM,
        canonical_url="https://investor.cadence.com/rss/pressrelease.aspx",
        domains=("investor.cadence.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Cadence Design Systems, Inc.", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="Cadence Design Systems, Inc.", last_verified_at="2026-09-11",
        notes=(
            "Daily News source-expansion batch 3 (2026-09-11) — live-verified official IR RSS "
            "feed, HTTP 200, dated items (newest 2026-09-02), each with a real per-article "
            "investor.cadence.com/news/news-details/... link, on-domain. Bounded local "
            "run_discovery() smoke test (temp cache dir, no production state touched) confirmed "
            "10 real stories published from this feed."
        ),
    ),
)

# Daily News source-expansion batch 4 (2026-09-13) — 3 official issuer
# newsroom RSS feeds, each independently live-verified this batch (real
# fetch, HTTP 200, parseable RSS 2.0, on-domain per-article item links),
# closing coverage gaps for three already-tracked issuers that had no
# existing Daily News source (Samsung Electronics and Murata Manufacturing
# were the only two of 28 tracked Japan/Korea issuers with a pre-existing
# gap this batch specifically targeted; only Samsung and Murata had a
# working feed found — see design/DECISIONS.md for the read-only
# discovery pass and the 21 other candidates checked with no working
# feed found, none of which are added here). Classified OFFICIAL_NEWSROOM,
# not OFFICIAL_IR, matching this registry's own established pattern
# (investor.*/ir.* dedicated subdomains get OFFICIAL_IR; a general
# corporate-domain newsroom/press feed gets OFFICIAL_NEWSROOM — see
# nvidia-newsroom-rss/sk-hynix-newsroom-rss above for the same
# distinction). No new category, format, matching exception, or
# eligibility rule — these are issuer-lane entries, resolved via the
# existing feed_registry.tracked_company_for() lookup exactly like
# every other OFFICIAL_NEWSROOM entry above.
EXPANSION_BATCH_4_SOURCE_REGISTRY: tuple[DailyNewsSourceEntry, ...] = (
    DailyNewsSourceEntry(
        source_id="samsung-newsroom-rss", category=SourceCategory.OFFICIAL_NEWSROOM, format=SourceFormat.RSS_ATOM,
        canonical_url="https://news.samsung.com/global/feed",
        domains=("news.samsung.com",),
        jurisdiction="South Korea", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Samsung Electronics", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="Samsung Electronics", last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 4 (2026-09-13) — live-verified official newsroom "
            "RSS feed, HTTP 200, dated items (newest 2026-09-09), each with a real per-article "
            "news.samsung.com/global/... link, on-domain (no www. prefix — confirmed live, not "
            "guessed)."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="murata-newsroom-rss", category=SourceCategory.OFFICIAL_NEWSROOM, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.murata.com/en-global/news/rssfeed",
        domains=("www.murata.com",),
        jurisdiction="Japan", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Murata Manufacturing Co., Ltd.", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="Murata Manufacturing Co., Ltd.", last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 4 (2026-09-13) — live-verified official newsroom "
            "RSS feed (\"Product News\"), HTTP 200, dated items (newest 2026-09-10), each with a "
            "real per-article www.murata.com/en-global/news/... link, on-domain."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="microchip-newsroom-rss", category=SourceCategory.OFFICIAL_NEWSROOM, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.microchip.com/RSS/recent-PRCorporate.xml",
        domains=("www.microchip.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Microchip Technology Incorporated", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="Microchip Technology Incorporated", last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 4 (2026-09-13) — live-verified official "
            "\"Corporate Press Releases\" RSS feed, HTTP 200, dated items (newest 2026-09-03), "
            "each with a real per-article www.microchip.com/en-us/about/news-releases/corporate/... "
            "link, on-domain. Low cadence confirmed live — roughly monthly, not stale/broken "
            "(newest item well within the existing 7-day freshness window at verification time)."
        ),
    ),
)

# Daily News source-expansion batch 5 (2026-09-13) — one official IR RSS
# feed, live-verified this batch (real fetch, HTTP 200, parseable RSS
# 2.0, on-domain per-article item links), closing a coverage gap for a
# newly-tracked issuer that had no existing Daily News source. Hewlett
# Packard Enterprise Company is a Daily News-only discovery — not in
# tracked_companies.py (Radar's own scan universe) — so it is resolved
# via a new src.config.issuer_registry.DISCOVERY_STUBS entry (stub:HPE),
# the same established mechanism already used for Quanta Services, nVent
# Electric, Arista Networks, and Cisco Systems above; tracked_companies.py
# itself is untouched. Classified OFFICIAL_IR, matching this registry's
# own established investor.*/ir.*-subdomain convention.
EXPANSION_BATCH_5_SOURCE_REGISTRY: tuple[DailyNewsSourceEntry, ...] = (
    DailyNewsSourceEntry(
        source_id="hpe-ir-rss", category=SourceCategory.OFFICIAL_IR, format=SourceFormat.RSS_ATOM,
        canonical_url="https://investors.hpe.com/rss/news",
        domains=("investors.hpe.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Hewlett Packard Enterprise Company", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="Hewlett Packard Enterprise Company", last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 5 (2026-09-13) — live-verified official IR RSS "
            "feed, HTTP 200, 121 dated items, newest 2026-09-02, all items on-domain "
            "(investors.hpe.com/news-and-events/...). IR/earnings-cadence feed, not a general "
            "newsroom — publication frequency will reflect that. Resolved via a new "
            "src.config.issuer_registry.DISCOVERY_STUBS entry (stub:HPE); not part of "
            "tracked_companies.py or any EDGAR/DART/EDINET scan universe."
        ),
    ),
)

# Daily News Cohort 1 batch (2026-09-15) — 4 official issuer IR/
# newsroom RSS feeds, each independently live-verified this batch (real
# fetch, HTTP 200, parseable RSS 2.0, on-domain per-article item links),
# closing coverage gaps for 4 of the 10 Tier 1 Cohort 1 supply-chain
# issuers added to tracked_companies.py in PR #59 (Equinix, L3Harris
# Technologies, Firefly Aerospace, Yaskawa Electric) and, for L3Harris/
# Firefly specifically, giving the `space` theme its first-ever
# issuer-linked Daily News source (previously zero) — see design/
# DAILY_NEWS_COHORT1_IMPLEMENTATION_DESIGN_2026_09_15.md for the full
# evidence record, including the deliberate Yaskawa `language="English"`
# curation override (that feed's own metadata declares Japanese; every
# observed item is genuinely English-language content).
EXPANSION_BATCH_6_SOURCE_REGISTRY: tuple[DailyNewsSourceEntry, ...] = (
    DailyNewsSourceEntry(
        source_id="equinix-ir-rss", category=SourceCategory.OFFICIAL_IR, format=SourceFormat.RSS_ATOM,
        canonical_url="https://investor.equinix.com/news-events/press-releases/rss",
        domains=("investor.equinix.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Equinix, Inc.", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="Equinix, Inc.", last_verified_at="2026-09-15",
        notes=(
            "Daily News Cohort 1 batch (2026-09-15) — live-verified official IR RSS feed, HTTP "
            "200, RSS 2.0, channel title 'Equinix, Inc. (EQIX) Press Releases', dated items "
            "(newest 2026-09-10), each with a real per-article "
            "investor.equinix.com/news-events/press-releases/detail/... link, on-domain."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="l3harris-newsroom-rss", category=SourceCategory.OFFICIAL_NEWSROOM, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.l3harris.com/feeds/newsroom/rss.xml",
        domains=("www.l3harris.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="L3Harris Technologies, Inc.", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="L3Harris Technologies, Inc.", last_verified_at="2026-09-15",
        notes=(
            "Daily News Cohort 1 batch (2026-09-15) — live-verified official newsroom RSS feed, "
            "HTTP 200, RSS 2.0, channel title 'L3Harris Technologies', dated items (newest "
            "2026-09-15), each with a real per-article www.l3harris.com/newsroom/... link, "
            "on-domain. First-ever issuer-linked `space`-theme Daily News source. Multi-segment "
            "company — this feed mixes space-segment and much larger non-space defense-"
            "electronics content; no per-item segment filter exists, matching every other issuer "
            "feed in this registry — a known, accepted characteristic, not a defect."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="firefly-aerospace-news-rss", category=SourceCategory.OFFICIAL_NEWSROOM, format=SourceFormat.RSS_ATOM,
        canonical_url="https://fireflyspace.com/feed/",
        domains=("fireflyspace.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Firefly Aerospace Inc.", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="Firefly Aerospace Inc.", last_verified_at="2026-09-15",
        notes=(
            "Daily News Cohort 1 batch (2026-09-15) — live-verified official news RSS feed, HTTP "
            "200, RSS 2.0, channel title 'Firefly Aerospace', dated items (newest 2026-09-09), "
            "each with a real per-article fireflyspace.com/news/... link, on-domain. Second "
            "issuer-linked `space`-theme Daily News source, pairing with L3Harris."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="yaskawa-newsroom-rss", category=SourceCategory.OFFICIAL_NEWSROOM, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.yaskawa-global.com/feed/",
        domains=("www.yaskawa-global.com",),
        jurisdiction="Japan", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="YASKAWA Electric Corporation", licensing_classification=_PILOT_LICENSING_CLASSIFICATION,
        priority=1, issuer_name="YASKAWA Electric Corporation", last_verified_at="2026-09-15",
        language="English",
        notes=(
            "Daily News Cohort 1 batch (2026-09-15) — live-verified official newsroom RSS feed, "
            "HTTP 200, RSS 2.0, dated items (newest 2026-09-13), each with a real per-article "
            "www.yaskawa-global.com/newsrelease/.../ir/news/... link, on-domain. Second "
            "issuer-linked `humanoids`-theme Daily News source (after Rockwell Automation). "
            "`language=\"English\"` is a DELIBERATE curation override, not a default or an "
            "oversight — the feed's own <channel><language> tag declares 'ja', but every "
            "observed item's real title/content is plain English (Yaskawa's own English-"
            "language global site); per DailyNewsSourceEntry.language's own field discipline "
            "('a human-verified fact about the source, never derived from text'), the curated "
            "value reflects observed content, never blindly-copied feed metadata."
        ),
    ),
)

# The real, live runtime feed list — original 12 pilot sources first
# (byte-identical, same order), then expansion batch 1's 7 sources,
# then expansion batch 2's 1 source, then expansion batch 3's 4
# sources, then expansion batch 4's 3 sources, then expansion batch 5's
# 1 source, then the Daily News Cohort 1 batch's 4 sources, in the
# exact order given (19 + 1 + 4 + 3 + 1 + 4 = 32).
# feed_registry.PILOT_FEEDS is generated from this tuple via
# to_daily_news_feed_source(); see that module's own updated docstring.
RUNTIME_SOURCE_REGISTRY: tuple[DailyNewsSourceEntry, ...] = (
    PILOT_SOURCE_REGISTRY + EXPANSION_BATCH_1_SOURCE_REGISTRY + EXPANSION_BATCH_2_SOURCE_REGISTRY
    + EXPANSION_BATCH_3_SOURCE_REGISTRY + EXPANSION_BATCH_4_SOURCE_REGISTRY + EXPANSION_BATCH_5_SOURCE_REGISTRY
    + EXPANSION_BATCH_6_SOURCE_REGISTRY
)

_EDITORIAL_LICENSING_CLASSIFICATION = (
    "Independent journalism (publisher-owned RSS feed) — public headline/metadata content; "
    "headline, publisher-provided excerpt, and direct link only, never full article-body "
    "reproduction, per this project's existing no-full-article-reproduction policy (see "
    "src/data_access/daily_news/summary_grounding.py)."
)

# Government / Public Sector Daily News lane (design/DECISIONS.md) —
# distinct from _EDITORIAL_LICENSING_CLASSIFICATION above: these are
# U.S. federal government works (public domain, 17 U.S.C. section 105), not
# publisher-owned independent journalism. Same "headline, excerpt, and
# direct link only" reproduction discipline as every other Daily News
# source, stated in its own accurate terms rather than reusing the
# "independent journalism" wording, which does not describe a .gov/.mil
# source.
_GOVERNMENT_LICENSING_CLASSIFICATION = (
    "U.S. federal government work (public domain) — public headline/metadata content; "
    "headline, publisher-provided excerpt, and direct link only, never full article-body "
    "reproduction, per this project's existing no-full-article-reproduction policy (see "
    "src/data_access/daily_news/summary_grounding.py)."
)

# Daily News source-expansion batch 2 editorial lane (2026-09-13) — a
# non-U.S. counterpart to _GOVERNMENT_LICENSING_CLASSIFICATION above:
# an official exchange/regulator/central-bank source whose material is
# NOT U.S. federal government work and therefore not assumed public
# domain under any foreign equivalent — stated in its own accurate
# terms rather than reusing either the U.S.-public-domain or
# independent-journalism wording, neither of which describes this
# source type. Used for JPX Market News, Japan's Financial Services
# Agency below.
_EXCHANGE_REGULATOR_LICENSING_CLASSIFICATION = (
    "Official exchange/regulator source (publisher-owned RSS feed) — public market/regulatory "
    "information; headline, publisher-provided excerpt, and direct link only, never full "
    "article-body reproduction, per this project's existing no-full-article-reproduction policy "
    "(see src/data_access/daily_news/summary_grounding.py)."
)

# Daily News source-expansion batch 2 editorial lane (2026-09-13) — for
# a press-release distribution wire service (PR Newswire): syndicated
# issuer press releases the wire service itself did not report or
# write, not independently-reported journalism. Deliberately distinct
# wording from _EDITORIAL_LICENSING_CLASSIFICATION's "Independent
# journalism" — never describe a wire-service source that way. Category
# remains SourceCategory.INDEPENDENT_NEWS (the only issuer-agnostic,
# non-official category this registry's admission rules define today;
# adding a dedicated wire-service category is out of this batch's
# scope), but this licensing text and every entry's own notes state the
# wire-service distinction explicitly.
_WIRE_SERVICE_LICENSING_CLASSIFICATION = (
    "Press-release distribution wire service (publisher-owned RSS feed) — syndicated issuer "
    "press-release content the wire service itself did not report or write; not independent "
    "journalism. Headline, publisher-provided excerpt, and direct link only, never full "
    "article-body reproduction, per this project's existing no-full-article-reproduction policy "
    "(see src/data_access/daily_news/summary_grounding.py)."
)

# Editorial Daily News v1 (design/DECISIONS.md) — a SEPARATE registry
# from RUNTIME_SOURCE_REGISTRY, deliberately never merged into it:
# these 10 sources are issuer_agnostic=True (category=INDEPENDENT_NEWS),
# a shape feed_registry.to_daily_news_feed_source() explicitly rejects
# (see that function's own docstring) — they are read by
# editorial_pipeline.py, never by daily_news_pipeline.run_discovery()
# or the issuer-IR feed_registry.PILOT_FEEDS list. Every entry
# independently live-verified this batch: real fetch, HTTP 200,
# parseable RSS 2.0, real dated per-article item links on the domain
# listed. CNBC's real, currently-working RSS mechanism is
# search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=
# <id> — the legacy cnbc.com/id/.../device/rss/rss.html URLs return
# HTTP 403 and are never used. Every CNBC item's own <link> resolves to
# www.cnbc.com (confirmed live, not search.cnbc.com, the feed host
# itself) — domains is set accordingly, never the feed host. Korea
# Herald Business is the only section with a real feed (no separate
# Technology feed exists on koreaherald.com/rss, confirmed live).
# Yonhap was attempted and excluded: unreachable from this session's own
# tooling both times it was tried — not silently added.
EDITORIAL_SOURCE_REGISTRY_V1: tuple[DailyNewsSourceEntry, ...] = (
    DailyNewsSourceEntry(
        source_id="cnbc-top-news-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
        domains=("www.cnbc.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="CNBC", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-11",
        notes=(
            "Editorial Daily News v1 (2026-09-11) — live-verified, HTTP 200, RSS 2.0, channel "
            "title 'US Top News and Analysis'. Item links confirmed on www.cnbc.com (e.g. "
            "cnbc.com/2026/09/11/cpi-inflation-breakdown-august-2026.html)."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="cnbc-business-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147",
        domains=("www.cnbc.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="CNBC", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-11",
        notes="Editorial Daily News v1 (2026-09-11) — live-verified, HTTP 200, RSS 2.0, channel title 'Business News'.",
    ),
    DailyNewsSourceEntry(
        source_id="cnbc-finance-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",
        domains=("www.cnbc.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="CNBC", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-11",
        notes="Editorial Daily News v1 (2026-09-11) — live-verified, HTTP 200, RSS 2.0, channel title 'Finance'.",
    ),
    DailyNewsSourceEntry(
        source_id="cnbc-economy-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
        domains=("www.cnbc.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="CNBC", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-11",
        notes="Editorial Daily News v1 (2026-09-11) — live-verified, HTTP 200, RSS 2.0, channel title 'Economy'.",
    ),
    DailyNewsSourceEntry(
        source_id="cnbc-technology-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910",
        domains=("www.cnbc.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="CNBC", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-11",
        notes="Editorial Daily News v1 (2026-09-11) — live-verified, HTTP 200, RSS 2.0, channel title 'Tech'.",
    ),
    DailyNewsSourceEntry(
        source_id="cnbc-earnings-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839135",
        domains=("www.cnbc.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="CNBC", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-11",
        notes="Editorial Daily News v1 (2026-09-11) — live-verified, HTTP 200, RSS 2.0, channel title 'Earnings'.",
    ),
    DailyNewsSourceEntry(
        source_id="cnbc-energy-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19836768",
        domains=("www.cnbc.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="CNBC", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-11",
        notes="Editorial Daily News v1 (2026-09-11) — live-verified, HTTP 200, RSS 2.0, channel title 'Energy'.",
    ),
    DailyNewsSourceEntry(
        source_id="cnbc-politics-policy-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000113",
        domains=("www.cnbc.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="CNBC", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-11",
        notes="Editorial Daily News v1 (2026-09-11) — live-verified, HTTP 200, RSS 2.0, channel title 'Politics & Policy'.",
    ),
    DailyNewsSourceEntry(
        source_id="cnbc-asia-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19832390",
        domains=("www.cnbc.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="CNBC", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-11",
        notes="Editorial Daily News v1 (2026-09-11) — live-verified, HTTP 200, RSS 2.0, channel title 'Asia News'.",
    ),
    DailyNewsSourceEntry(
        source_id="korea-herald-business-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.koreaherald.com/rss/kh_Business",
        domains=("www.koreaherald.com",),
        jurisdiction="South Korea", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="The Korea Herald", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-11",
        notes=(
            "Editorial Daily News v1 (2026-09-11) — live-verified, HTTP 200, RSS, channel title "
            "'The korea Herald News Rss'. Item links confirmed on www.koreaherald.com/article/... "
            "(e.g. koreaherald.com/article/10870958). No separate Technology feed exists on "
            "koreaherald.com/rss (confirmed live) — Business is the only available section."
        ),
    ),
    # Government / Public Sector Daily News lane (design/DECISIONS.md) —
    # the first two entries in EDITORIAL_SOURCE_REGISTRY that are real
    # U.S. federal government sources rather than independent
    # journalism. Both use the already-defined, previously-unused
    # SourceCategory.GOVERNMENT_POLICY value (see this module's own
    # SourceCategory docstring) — no new enum member added. Eligibility
    # for these two specific source_ids is handled by two explicit,
    # hardcoded rules in editorial_pipeline.py's own eligibility check
    # (not a category-based or generically-extensible bypass) — see that
    # module's own docstring for exactly why.
    DailyNewsSourceEntry(
        source_id="spaceforce-news-rss", category=SourceCategory.GOVERNMENT_POLICY, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.spaceforce.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1060&max=10",
        domains=("www.spaceforce.mil",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="U.S. Space Force", licensing_classification=_GOVERNMENT_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, last_verified_at="2026-09-12",
        notes=(
            "Government / Public Sector Daily News lane (2026-09-12) — live-verified, HTTP 200, "
            "RSS 2.0, channel title 'United States Space Force News'. Item links confirmed on "
            "www.spaceforce.mil/News/Article-Display/Article/..., real <category> tags present. "
            "No `allowlisted` flag required — that gate applies only to SourceCategory."
            "INDEPENDENT_NEWS, not GOVERNMENT_POLICY."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="nist-news-rss", category=SourceCategory.GOVERNMENT_POLICY, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.nist.gov/news-events/news/rss.xml",
        domains=("www.nist.gov",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="National Institute of Standards and Technology (NIST)",
        licensing_classification=_GOVERNMENT_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, last_verified_at="2026-09-12",
        notes=(
            "Government / Public Sector Daily News lane (2026-09-12) — live-verified, HTTP 200, "
            "RSS 2.0, channel title 'NIST News'. Item links confirmed on "
            "www.nist.gov/news-events/news/..., real <dc:creator> present. Restricted to a strict, "
            "source-scoped CHIPS/semiconductor allow-list in editorial_pipeline.py — general NIST "
            "science news (the majority of this feed) is deliberately excluded, not published."
        ),
    ),
)

# Daily News source-expansion batch 2, editorial lane (2026-09-13) — 23
# more issuer_agnostic=True editorial sources, each independently
# live-verified this batch (real fetch, HTTP 200, parseable RSS/Atom,
# real dated per-article item links confirmed on-domain across every
# item in the feed, not just a sample). Appended AFTER
# EDITORIAL_SOURCE_REGISTRY_V1, never interleaved — see
# EDITORIAL_SOURCE_REGISTRY below, which preserves this exact ordering.
#
# The Register's two feeds (headlines + On Prem) share one
# attribution_label ("The Register") and PR Newswire's two feeds
# (general + financial-services) share one attribution_label ("PR
# Newswire") — same convention as the 9 existing CNBC feeds above,
# needed because dedup.is_duplicate_title() keys cross-feed duplicate
# detection on (normalized_title, publisher): two feeds from the same
# real-world publisher must report that publisher identically or a
# duplicate story from both feeds would not be recognized as one. PR
# Newswire is a press-release distribution wire service, not
# independent journalism — see _WIRE_SERVICE_LICENSING_CLASSIFICATION's
# own docstring; its entries' notes restate this explicitly so it is
# never read as 2 independent publishers among "25 independent
# publishers" — this batch is 24 feeds across meaningfully fewer real
# publishers once The Register's and PR Newswire's pairs are counted
# once each.
#
# Bank of Japan (boj.or.jp/en/rss/whatsnew.xml) was live-verified
# (HTTP 200, 43 parseable dated items) but is REJECTED, not added: a
# full item-by-item scan (not just a sample) found every single one of
# its 43 item <link> values uses plain http://, never https:// — the
# existing, unmodified canonical_url.validate_canonical_url() gate
# requires parsed.scheme == "https" unconditionally, so zero stories
# would ever publish from this feed as configured. Same "Micron"
# failure pattern already documented in expansion batch 3 above; not
# silently dropped, and no unverified substitute was added in its
# place. Revisit only if the Bank of Japan's own feed later serves
# https:// links.
EDITORIAL_SOURCE_REGISTRY_BATCH_2: tuple[DailyNewsSourceEntry, ...] = (
    # --- US independent news / trade press ---
    DailyNewsSourceEntry(
        source_id="techcrunch-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://techcrunch.com/feed/",
        domains=("techcrunch.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="TechCrunch", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 2, editorial lane (2026-09-13) — live-verified, "
            "HTTP 200, RSS 2.0, 20 dated items, newest 2026-09-13, all 20 items on-domain "
            "(techcrunch.com)."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="ars-technica-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://feeds.arstechnica.com/arstechnica/index",
        domains=("arstechnica.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Ars Technica", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 2, editorial lane (2026-09-13) — live-verified, "
            "HTTP 200, RSS 2.0, 20 dated items, newest 2026-09-13, all 20 items on-domain "
            "(arstechnica.com — the feed host, feeds.arstechnica.com, is not the item-link domain)."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="the-verge-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.theverge.com/rss/index.xml",
        domains=("www.theverge.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="The Verge", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 2, editorial lane (2026-09-13) — live-verified, "
            "HTTP 200, Atom, 10 dated items, newest 2026-09-13, all 10 items on-domain."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="the-register-headlines-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.theregister.com/headlines.atom",
        domains=("www.theregister.com",),
        jurisdiction="United Kingdom", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="The Register", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 2, editorial lane (2026-09-13) — live-verified, "
            "HTTP 200, real Atom/RSS content despite the .atom extension, 50 dated items, newest "
            "2026-09-13, all 50 items on-domain. Shares attribution_label 'The Register' with "
            "the-register-on-prem-rss below (same real-world publisher, two sections) — see this "
            "batch's own module-level comment for why."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="the-register-on-prem-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.theregister.com/on_prem/headlines.atom",
        domains=("www.theregister.com",),
        jurisdiction="United Kingdom", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="The Register", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 2, editorial lane (2026-09-13) — live-verified, "
            "HTTP 200, 50 dated items, newest 2026-09-10, all 50 items on-domain. Shares "
            "attribution_label 'The Register' with the-register-headlines-rss above (same "
            "real-world publisher, two sections, not two independent publishers) — see this "
            "batch's own module-level comment for why."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="pr-newswire-general-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.prnewswire.com/rss/news-releases-list.rss",
        domains=("www.prnewswire.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="PR Newswire", licensing_classification=_WIRE_SERVICE_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 2, editorial lane (2026-09-13) — live-verified, "
            "HTTP 200, RSS 2.0, 20 dated items, newest 2026-09-13, all 20 items on-domain. "
            "Press-release distribution wire service, not independent journalism — see "
            "_WIRE_SERVICE_LICENSING_CLASSIFICATION. Shares attribution_label 'PR Newswire' with "
            "pr-newswire-financial-services-rss below (same real-world publisher, two feeds, not "
            "two independent publishers) — see this batch's own module-level comment for why. "
            "The feed itself carries a mix of languages (global syndication); the pipeline's own "
            "company/theme fail-closed matching gate, not this admission entry, is what "
            "determines which items actually publish."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="pr-newswire-financial-services-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.prnewswire.com/rss/financial-services-latest-news/financial-services-latest-news-list.rss",
        domains=("www.prnewswire.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="PR Newswire", licensing_classification=_WIRE_SERVICE_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 2, editorial lane (2026-09-13) — live-verified, "
            "HTTP 200, RSS 2.0, 20 dated items, newest 2026-09-13, all 20 items on-domain. "
            "Press-release distribution wire service, not independent journalism — see "
            "_WIRE_SERVICE_LICENSING_CLASSIFICATION. Shares attribution_label 'PR Newswire' with "
            "pr-newswire-general-rss above — see this batch's own module-level comment for why."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="semiconductor-engineering-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://semiengineering.com/feed/",
        domains=("semiengineering.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Semiconductor Engineering", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 2, editorial lane (2026-09-13) — live-verified, "
            "HTTP 200, RSS 2.0, 10 dated items, newest 2026-09-11, all 10 items on-domain."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="ieee-spectrum-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://spectrum.ieee.org/feeds/feed.rss",
        domains=("spectrum.ieee.org", "event.on24.com"),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="IEEE Spectrum", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 2, editorial lane (2026-09-13) — live-verified, "
            "HTTP 200, RSS 2.0, 30 dated items, newest 2026-09-11, 29 of 30 items on "
            "spectrum.ieee.org; one item this batch linked to a webinar registration page on "
            "event.on24.com, included in `domains` since it is a real IEEE-sponsored-event link "
            "seen live in the feed's own content, not a guess."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="data-center-frontier-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.datacenterfrontier.com/__rss/website-scheduled-content.xml?input=%7B%22sectionAlias%22%3A%22home%22%7D",
        domains=("www.datacenterfrontier.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Data Center Frontier", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 2, editorial lane (2026-09-13) — live-verified, "
            "HTTP 200, RSS 2.0, 25 dated items, newest 2026-09-11, all 25 items on-domain. Note "
            "the site's own conventional /rss.xml path 404s — this is the real, working feed URL, "
            "confirmed live, not guessed."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="toms-hardware-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.tomshardware.com/feeds/all",
        domains=("www.tomshardware.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Tom's Hardware", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 2, editorial lane (2026-09-13) — live-verified, "
            "HTTP 200, RSS 2.0, 50 dated items, newest 2026-09-13, all 50 items on-domain."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="supply-chain-dive-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.supplychaindive.com/feeds/news/",
        domains=("www.supplychaindive.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Supply Chain Dive", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 2, editorial lane (2026-09-13) — live-verified, "
            "HTTP 200, RSS 2.0, 10 dated items, newest 2026-09-11, all 10 items on-domain."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="utility-dive-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.utilitydive.com/feeds/news/",
        domains=("www.utilitydive.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Utility Dive", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 2, editorial lane (2026-09-13) — live-verified, "
            "HTTP 200, RSS 2.0, 10 dated items, newest 2026-09-11, all 10 items on-domain."
        ),
    ),
    # --- US federal regulators (market-relevant policy/regulatory action) ---
    DailyNewsSourceEntry(
        source_id="sec-press-releases-rss", category=SourceCategory.REGULATOR, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.sec.gov/news/pressreleases.rss",
        domains=("www.sec.gov",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="U.S. Securities and Exchange Commission (SEC)",
        licensing_classification=_GOVERNMENT_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 2, editorial lane (2026-09-13) — live-verified, "
            "HTTP 200, RSS 2.0, 25 dated items, newest 2026-09-11, all 25 items on-domain. No "
            "`allowlisted` flag required — that gate applies only to SourceCategory."
            "INDEPENDENT_NEWS, not REGULATOR."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="federal-reserve-press-rss", category=SourceCategory.REGULATOR, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.federalreserve.gov/feeds/press_all.xml",
        domains=("www.federalreserve.gov",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Board of Governors of the Federal Reserve System",
        licensing_classification=_GOVERNMENT_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 2, editorial lane (2026-09-13) — live-verified, "
            "HTTP 200, RSS 2.0, 20 dated items, newest 2026-09-11, all 20 items on-domain."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="ftc-press-releases-rss", category=SourceCategory.REGULATOR, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.ftc.gov/feeds/press-release.xml",
        domains=("www.ftc.gov",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Federal Trade Commission (FTC)", licensing_classification=_GOVERNMENT_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 2, editorial lane (2026-09-13) — live-verified, "
            "HTTP 200, RSS 2.0, 10 dated items, newest 2026-09-10, all 10 items on-domain."
        ),
    ),
    # --- Japan ---
    DailyNewsSourceEntry(
        source_id="japan-times-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.japantimes.co.jp/feed/",
        domains=("www.japantimes.co.jp",),
        jurisdiction="Japan", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="The Japan Times", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 2, editorial lane (2026-09-13) — live-verified, "
            "HTTP 200, RSS 2.0, 30 dated items, newest 2026-09-14 (JST), all 30 items on-domain."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="jpx-market-news-rss", category=SourceCategory.EXCHANGE, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.jpx.co.jp/english/rss/markets_news.xml",
        domains=("www.jpx.co.jp",),
        jurisdiction="Japan", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Japan Exchange Group (JPX)", licensing_classification=_EXCHANGE_REGULATOR_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 2, editorial lane (2026-09-13) — live-verified, "
            "HTTP 200, RSS 2.0, 22 dated items, newest 2026-09-11, all 22 items on-domain. No "
            "`allowlisted` flag required — that gate applies only to SourceCategory."
            "INDEPENDENT_NEWS, not EXCHANGE."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="fsa-japan-news-rss", category=SourceCategory.REGULATOR, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.fsa.go.jp/fsaEnNewsList_rss2.xml",
        domains=("www.fsa.go.jp",),
        jurisdiction="Japan", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Financial Services Agency of Japan (FSA)",
        licensing_classification=_EXCHANGE_REGULATOR_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 2, editorial lane (2026-09-13) — live-verified, "
            "HTTP 200, RSS 2.0, 10 dated items, newest 2026-09-10 (JST), all 10 items on-domain. "
            "Several linked items are PDFs — the existing canonical_url gate does not reject by "
            "file type, only by scheme/domain/path shape, and every one of this feed's links uses "
            "https:// (unlike Bank of Japan's feed — see this batch's own rejection note above)."
        ),
    ),
    # --- South Korea ---
    DailyNewsSourceEntry(
        source_id="yonhap-news-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://en.yna.co.kr/RSS/news.xml",
        domains=("en.yna.co.kr",),
        jurisdiction="South Korea", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Yonhap News Agency", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 2, editorial lane (2026-09-13) — live-verified, "
            "HTTP 200, RSS 2.0, 100 dated items, newest 2026-09-14 (KST), all 100 items on-domain. "
            "Previously attempted and excluded in the Editorial Daily News v1 batch above as "
            "'unreachable from this session's own tooling' — reattempted and confirmed live this "
            "batch at the same URL."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="korea-times-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://feed.koreatimes.co.kr/k/allnews.xml",
        domains=("www.koreatimes.co.kr",),
        jurisdiction="South Korea", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="The Korea Times", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 2, editorial lane (2026-09-13) — live-verified, "
            "HTTP 200, RSS 2.0, 7 dated items, newest 2026-09-13, all 7 items on-domain "
            "(www.koreatimes.co.kr — the feed host, feed.koreatimes.co.kr, is not the item-link "
            "domain). Legacy www.koreatimes.co.kr/... feed paths 301/403; this is the real, "
            "working feed URL, confirmed live, not guessed."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="korea-it-times-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.koreaittimes.com/rss/S1N1.xml",
        domains=("www.koreaittimes.com",),
        jurisdiction="South Korea", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Korea IT Times", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 2, editorial lane (2026-09-13) — live-verified, "
            "HTTP 200, RSS 2.0, 20 dated items, newest 2026-09-13, all 20 items on-domain. This "
            "is the English/HOME section feed; allArticle.xml mixes in Korean-language content "
            "and was deliberately not used."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="thelec-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.thelec.net/rss/allArticle.xml",
        domains=("www.thelec.net",),
        jurisdiction="South Korea", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="TheElec", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-13",
        notes=(
            "Daily News source-expansion batch 2, editorial lane (2026-09-13) — live-verified, "
            "HTTP 200, RSS 2.0, 50 dated items, newest 2026-09-14 (KST), all 50 items on-domain. "
            "This is the English edition (thelec.net), not the Korean edition (thelec.kr) — the "
            "feed's own <language> tag incorrectly reports 'ko' despite genuinely English content; "
            "confirmed live by reading real item titles/summaries, not by trusting that tag, and "
            "ingestion here never filters on it."
        ),
    ),
)

# Daily News source-expansion batch 3, Phase 1 activation (from the
# read-only US/Japan/Korea source audit) — exactly one entry: Data
# Center Dynamics. Re-verified live this batch (cache-bypassed fetch,
# not trusted from the audit's own earlier check): HTTP 200,
# application/rss+xml, 20 dated items, newest published minutes before
# this verification, every item on-domain (www.datacenterdynamics.com).
#
# METI's Japan Atom feed (the audit's other candidate,
# meti.go.jp/ml_index_en_atom.xml) was re-verified this same batch and
# is REJECTED, not added: still a real, live, well-formed Atom feed
# (confirmed again), but a cache-bypassed re-fetch shows its newest
# entry dated 2026-06-19 — roughly 3 months stale relative to this
# batch. editorial_pipeline.py's own unmodified ingestion-time freshness
# gate (`_is_fresh`, 72-hour window — see run_editorial_discovery's own
# `items_stale` counter) would reject 100% of this feed's current items
# before matching/admission ever runs; it would sit permanently silent
# under real conditions. Same "Microsoft"/"Bank of Japan" failure
# pattern already documented and rejected elsewhere in this registry —
# not a guess, and not silently dropped. Revisit only if METI's own
# English feed resumes near-real-time publication.
EDITORIAL_SOURCE_REGISTRY_BATCH_3: tuple[DailyNewsSourceEntry, ...] = (
    DailyNewsSourceEntry(
        source_id="data-center-dynamics-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.datacenterdynamics.com/en/rss/",
        domains=("www.datacenterdynamics.com",),
        jurisdiction="United Kingdom", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Data Center Dynamics", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-15",
        notes=(
            "Daily News source-expansion batch 3, Phase 1 activation (2026-09-15) — live-verified, "
            "cache-bypassed fetch, HTTP 200, application/rss+xml, 20 dated items, newest published "
            "minutes before verification, all 20 items on-domain "
            "(www.datacenterdynamics.com/en/news/...). Publisher is Data Centre Dynamics Ltd, "
            "London, UK (confirmed via the site's own footer); domain and branding use the US "
            "spelling 'DataCenterDynamics'/'DCD'."
        ),
    ),
)

# Daily News Cohort 1 batch (2026-09-15) — 2 independent trade-press
# sources, each independently live-verified this batch (real fetch,
# HTTP 200, parseable RSS 2.0, on-domain dated per-article item links),
# closing the `space` and `humanoids` themes' dedicated-trade-press gap
# (previously zero for both) — see design/
# DAILY_NEWS_COHORT1_IMPLEMENTATION_DESIGN_2026_09_15.md for the full
# evidence record. Both issuer-agnostic (category=INDEPENDENT_NEWS,
# allowlisted=True, required by validate_source_entry()) — matched to
# tracked companies/themes at item-match time via editorial_matching.py,
# never by a stored theme field on the source itself.
EDITORIAL_SOURCE_REGISTRY_BATCH_4: tuple[DailyNewsSourceEntry, ...] = (
    DailyNewsSourceEntry(
        source_id="spacenews-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://spacenews.com/feed/",
        domains=("spacenews.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="SpaceNews", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-15",
        notes=(
            "Daily News Cohort 1 batch (2026-09-15) — live-verified independent space-industry "
            "trade-press RSS feed, HTTP 200, RSS 2.0, channel title 'SpaceNews', dated items "
            "(newest 2026-09-15), all on-domain (spacenews.com/...). First dedicated space-"
            "industry trade-press source in this registry — closes a real, previously-zero gap "
            "(the existing general-tech sources — TechCrunch, Ars Technica, The Verge — carry "
            "only incidental space coverage)."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="robot-report-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.therobotreport.com/feed/",
        domains=("www.therobotreport.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="The Robot Report", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-15",
        notes=(
            "Daily News Cohort 1 batch (2026-09-15) — live-verified independent robotics trade-"
            "press RSS feed, HTTP 200, RSS 2.0, channel title 'The Robot Report', dated items "
            "(newest 2026-09-15), all on-domain (www.therobotreport.com/...). First dedicated "
            "robotics trade-press source in this registry — closes a real, previously-zero gap."
        ),
    ),
)

# The real, live editorial feed list — v1's 12 sources first (unchanged,
# same order), then batch 2's 23 sources, then batch 3's 1 source, then
# the Daily News Cohort 1 batch's 2 sources, in the exact order given
# (12 + 23 + 1 + 2 = 38). Never merged into RUNTIME_SOURCE_REGISTRY —
# read only by editorial_pipeline.py; see that module's own docstring.
EDITORIAL_SOURCE_REGISTRY: tuple[DailyNewsSourceEntry, ...] = (
    EDITORIAL_SOURCE_REGISTRY_V1 + EDITORIAL_SOURCE_REGISTRY_BATCH_2 + EDITORIAL_SOURCE_REGISTRY_BATCH_3
    + EDITORIAL_SOURCE_REGISTRY_BATCH_4
)

# Gated market-news source expansion (design/DECISIONS.md) — deliberately
# a SEPARATE registry from EDITORIAL_SOURCE_REGISTRY above, never merged
# into it: every source above is already unconditionally polled by the
# real worker (scripts/daily_news_worker.py's own _run_editorial_tick
# calls run_editorial_discovery() with no source_entries override, so it
# always gets EDITORIAL_SOURCE_REGISTRY's own default — no per-source
# flag exists for any of those 36 sources today). This registry holds
# sources that are NOT included by default; a source here is only ever
# added to a real discovery run when its own source_id is explicitly
# present in Settings.daily_news_enabled_market_news_sources (parsed
# from EDGE_DAILY_NEWS_ENABLED_SOURCES) — see
# src.data_access.daily_news.market_news_sources.enabled_gated_sources().
# Default empty allow-list means this whole registry contributes zero
# entries, zero network calls, and zero writes; existing behavior for
# every one of the 36 always-on sources above is completely unchanged.
#
# Light Reading (lightreading.com) — live-verified this batch (real
# fetch, HTTP 200, RSS 2.0, 50 dated items, newest published minutes
# before verification, all 50 on-domain). Networking/telecom/optical-
# infrastructure trade press — closes a real gap already identified in
# the read-only US/Japan/Korea source audit: photonics is a tracked
# theme with no dedicated trade-press source anywhere in
# EDITORIAL_SOURCE_REGISTRY above (only general tech press). robots.txt
# checked directly (not assumed): `User-agent: *` disallows only a
# handful of specific paths (/search, /api/health, ...), not a blanket
# prohibition — RSS/syndication access is permitted for a generic
# fetcher; the file separately blocks a named list of AI-training
# crawlers (ClaudeBot, GPTBot, CCBot, ...), a different use case
# (bulk-training-corpus collection) than this project's own headline+
# excerpt+direct-link-only policy. MarketWatch (Dow Jones) was checked
# and explicitly rejected instead: its robots.txt carries an explicit
# legal notice — "Collection of content ... through automated means is
# prohibited unless you have express written permission from Dow Jones
# & Company, Inc." — a real ToS bar this project's own standards
# (no scraping/ToS-violating integrations) require honoring, not a
# guess.
GATED_MARKET_NEWS_SOURCE_REGISTRY: tuple[DailyNewsSourceEntry, ...] = (
    DailyNewsSourceEntry(
        source_id="light-reading-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.lightreading.com/rss.xml",
        domains=("www.lightreading.com",),
        jurisdiction="United States", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Light Reading", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-15",
        notes=(
            "Gated market-news source expansion (2026-09-15) — live-verified, HTTP 200, RSS 2.0, "
            "50 dated items, newest published minutes before verification, all 50 on-domain "
            "(www.lightreading.com/...). robots.txt confirmed permissive for a generic fetcher "
            "(see this registry's own module-level comment above for the full check). Dormant "
            "unless 'light-reading-rss' is explicitly present in "
            "EDGE_DAILY_NEWS_ENABLED_SOURCES — never included in EDITORIAL_SOURCE_REGISTRY."
        ),
    ),
)

# Gated Japan/Korea source expansion (design/DECISIONS.md) — a SEPARATE
# gated registry and allow-list flag from GATED_MARKET_NEWS_SOURCE_
# REGISTRY above (EDGE_DAILY_NEWS_ENABLED_SOURCES_JP_KR, not
# EDGE_DAILY_NEWS_ENABLED_SOURCES — see
# src.data_access.daily_news.jp_kr_sources.enabled_gated_sources()),
# kept independent on purpose so either expansion can be reverted or
# extended without touching the other. Same discipline as every gated
# entry above: dormant unless its own source_id is explicitly present
# in the allow-list; never merged into EDITORIAL_SOURCE_REGISTRY.
#
# Read-only audit (design/DECISIONS.md) — the 8 JP/KR sources already
# in EDITORIAL_SOURCE_REGISTRY (japan-times-rss, jpx-market-news-rss,
# fsa-japan-news-rss, korea-herald-business-rss, yonhap-news-rss,
# korea-times-rss, korea-it-times-rss, thelec-rss) are already live —
# a real read-only dry run this batch (310 items fetched across all 8)
# found japan-times-rss, jpx-market-news-rss, fsa-japan-news-rss, and
# yonhap-news-rss each admitted ZERO stories in that sample (each is
# real and fetching correctly — zero source_failures — but their own
# content is too broad/general to name a tracked company or theme);
# korea-herald-business-rss/korea-times-rss/korea-it-times-rss/
# thelec-rss admitted 11 combined. Japan in particular has no real
# observed admitted coverage from its 3 existing sources in this
# sample — the gap this batch's Japan candidate directly targets.
#
# Japan — Japan Times Business section (japantimes.co.jp/business/feed/).
# A browser-context fetch confirmed it live (HTTP 200, real
# application/rss+xml, 20 dated items, newest published same day, all
# on-domain) and genuinely on-theme — its first 6 items alone included
# "SoftBank gets upsized $11.9 billion loan in OpenAI funding push"
# (SoftBank is tracked, ai-buildout theme), meaningfully higher on-theme
# density than the already-registered general japan-times-rss
# (japantimes.co.jp/feed/, same publisher/robots.txt). robots.txt
# confirmed permissive for this exact path: Disallow: /rss and
# /rssFeed/* (a legacy path this site no longer uses) but NOT /feed/ or
# /business/feed/. HOWEVER — a real worker-context read-only dry run
# this same batch (scripts/daily_news_jp_kr_dry_run.py, the real
# rss_atom_client User-Agent) got HTTPError:403 specifically on this
# path, while the already-live general /feed/ path returns 200 under
# the identical header (reproduced directly, not a one-off). Added to
# this registry as PENDING_REVIEW, not VERIFIED, with allow-list
# activation NOT recommended until this is resolved — see this entry's
# own notes for the full finding. Same posture this registry's own
# meta-newsroom-rss entry (EXPANSION_BATCH_2_SOURCE_REGISTRY above)
# already established for an identical "browser-confirmed, worker-
# blocked" situation.
#
# Other Japan candidates attempted this batch and NOT added, with the
# specific reason — never silently dropped:
#   - METI (meti.go.jp/ml_index_en_atom.xml): re-verified — still real
#     and live, but newest item is still dated 2026-06-19, unchanged
#     from the prior audit 3 months earlier. Confirmed persistently
#     stale, not a transient snapshot. Same exclusion as before.
#   - NHK World: no RSS/Atom link declared on the English news page;
#     the only discoverable feed (www3.nhk.or.jp/rss/news/cat0.xml) is
#     Japanese-language, itself over a month stale, and every item link
#     is plain http:// (would fail the https-only canonical-URL gate
#     regardless) — three independent disqualifiers.
#   - JETRO, Kyodo News (english.kyodonews.net), Yomiuri's Japan News
#     (japannews.yomiuri.co.jp), SEMI (semi.org): no RSS/Atom feed
#     discoverable via a declared <link> tag or conventional path on
#     any of these sites this batch.
#   - Japan Today (japantoday.com/feed): real, live, permissive
#     robots.txt, but its content is a general world-news aggregate
#     (politics, sport, celebrity) with essentially no Japan-corporate/
#     market content in a live sample — legally fine, thematically
#     unsuitable.
#
# Korea — Business Korea, two English-language section feeds
# (businesskorea.co.kr publishes both Korean- and English-language
# sections under numbered RSS paths; the "allArticle" feed mixes both
# languages and was deliberately not used, same discipline as korea-
# it-times-rss's own existing entry). robots.txt confirmed permissive:
# only /admin/ disallowed, no legal notice, no blanket prohibition.
#   - Industries (gns_S1N17.xml): live-verified, HTTP 200, 20 dated
#     items, newest published same day, all on-domain. Real examples
#     from this batch: "Chinese Kingnet Joins Wemade Acquisition
#     Consortium", "South Korea ICT Exports Surpass 60% of Total".
#   - Science & Technology (gns_S1N27.xml): live-verified, HTTP 200, 20
#     dated items. Lower cadence than Industries (newest item ~6 days
#     old at verification, not every day) but exceptionally high
#     on-theme density: "Samsung Unveils Processing DRAM as HBM
#     Alternative" (Samsung is tracked, memory theme), "Samsung
#     Develops New Tech to Overcome Interconnect Limits in AI Chips"
#     (ai-buildout theme). Same "low cadence, not stale/broken"
#     precedent already established for microchip-newsroom-rss above.
GATED_JP_KR_SOURCE_REGISTRY: tuple[DailyNewsSourceEntry, ...] = (
    DailyNewsSourceEntry(
        source_id="japan-times-business-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.japantimes.co.jp/business/feed/",
        domains=("www.japantimes.co.jp",),
        # NOT VERIFIED — see notes. A browser-context fetch confirmed
        # this feed live and on-theme, but the real worker's own fetch
        # signature (rss_atom_client's "EevaResearch-DailyNews/1.0"
        # User-Agent) gets HTTPError:403 on this exact path — reproduced
        # directly via requests.get() with that same header, not a
        # one-off. Same posture as the existing meta-newsroom-rss entry
        # (EXPANSION_BATCH_2_SOURCE_REGISTRY above): a browser navigation
        # confirming "live" does not confirm the worker's own distinct
        # fetch signature avoids the same block — only a real
        # worker-context fetch attempt can resolve that. PENDING_REVIEW
        # until re-verified working under the real fetch signature;
        # never treated as a live, trustworthy source until then.
        jurisdiction="Japan", enabled=True, health_state=SourceHealthState.PENDING_REVIEW,
        attribution_label="The Japan Times", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-15",
        notes=(
            "Gated Japan/Korea source expansion (2026-09-15) — browser-context fetch confirmed "
            "live, HTTP 200, application/rss+xml, 20 dated items, all on-domain "
            "(www.japantimes.co.jp/business/...), genuinely on-theme content. BUT: a real "
            "worker-context read-only dry run this same batch (scripts/daily_news_jp_kr_dry_run.py, "
            "using the same requests-based client and 'EevaResearch-DailyNews/1.0' User-Agent the "
            "real worker uses) recorded source_failures={'japan-times-business-rss': "
            "'HTTPError:403'} — reproduced directly and deterministically via requests.get() with "
            "that same header. The already-registered general japan-times-rss (/feed/, same "
            "publisher/robots.txt) returns 200 under the identical header — this 403 is specific "
            "to the /business/ section, not a general Japan Times block. Do not enable via the "
            "allow-list until this is resolved (e.g., a different worker-safe User-Agent, or "
            "direct confirmation from Japan Times) — zero stories would publish from this feed "
            "as configured today. Shares attribution_label 'The Japan Times' with the "
            "already-registered general japan-times-rss (same real-world publisher, two "
            "sections) — same convention as The Register's pair in EDITORIAL_SOURCE_REGISTRY."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="businesskorea-industries-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.businesskorea.co.kr/rss/gns_S1N17.xml",
        domains=("www.businesskorea.co.kr",),
        jurisdiction="South Korea", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Business Korea", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-15",
        notes=(
            "Gated Japan/Korea source expansion (2026-09-15) — live-verified, HTTP 200, "
            "application/xml, 20 dated items, newest published same day, all on-domain "
            "(www.businesskorea.co.kr/news/articleView.html?...). This is the English-language "
            "'Industries' section (gns_S1N17.xml), confirmed by reading real item titles — "
            "other numbered sections on this same site are Korean-language and were not used, "
            "same discipline as korea-it-times-rss's own existing entry. Shares attribution_label "
            "'Business Korea' with businesskorea-science-tech-rss below (same publisher, two "
            "sections)."
        ),
    ),
    DailyNewsSourceEntry(
        source_id="businesskorea-science-tech-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.businesskorea.co.kr/rss/gns_S1N27.xml",
        domains=("www.businesskorea.co.kr",),
        jurisdiction="South Korea", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="Business Korea", licensing_classification=_EDITORIAL_LICENSING_CLASSIFICATION,
        priority=1, issuer_agnostic=True, allowlisted=True, last_verified_at="2026-09-15",
        notes=(
            "Gated Japan/Korea source expansion (2026-09-15) — live-verified, HTTP 200, "
            "application/xml, 20 dated items, all on-domain. This is the English-language "
            "'Science & Technology' section (gns_S1N27.xml). Lower cadence than the Industries "
            "feed above (newest item ~6 days old at verification) but exceptionally high "
            "on-theme density in a live sample — see this registry's own module-level comment "
            "above for real examples. Shares attribution_label 'Business Korea' with "
            "businesskorea-industries-rss above."
        ),
    ),
)
