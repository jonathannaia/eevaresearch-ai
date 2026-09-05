"""Daily News Source-Expansion & Ingestion Design, Batch 1.5 — a typed,
UNUSED record of a completed, bounded, read-only live source-validation
pass (Federal Register, NIST/CHIPS, JPX, BIS, USTR — 2026-09-04). This
module is deliberately and completely separate from every real,
currently-live Daily News structure:

- NOT `source_registry.RUNTIME_SOURCE_REGISTRY` / `PILOT_SOURCE_REGISTRY`
  / `EXPANSION_BATCH_*_SOURCE_REGISTRY` — those are the real, live,
  worker-polled sources.
- NOT `feed_registry.PILOT_FEEDS` — the real pipeline/worker's own
  runtime feed list, derived from the registry above.
- NOT imported by `daily_news_pipeline.py`, `scripts/daily_news_worker.py`,
  `daily_news_backend.py`, any UI page, or any translation provider —
  see tests/test_daily_news_source_candidates.py's own isolation tests
  for the mechanical proof, not merely this docstring's claim.

Every `SourceCandidateRecord` here records the OUTCOME of a manual,
bounded, permitted-source-only browser validation pass performed outside
any worker/pipeline/test context — `validated_at`/`validation_source_record`
exist specifically so this local record is never confused with a live
`SourceHealthState` result the real worker would produce by actually
polling the endpoint. No network request, feed fetch, or database write
occurs anywhere in this module — every field below is a literal,
already-known value from that completed validation pass.

Category/format/reliability vocabulary is reused, not reinvented, from
`source_registry.py` (SourceCategory, SourceFormat, SourceHealthState,
SourceReliabilityTier) and `contains_excluded_source_name()` — the same
enums and exclusion guard every other Daily News source record uses.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse

from src.data_access.daily_news.source_registry import (
    SourceCategory,
    SourceFormat,
    SourceHealthState,
    SourceReliabilityTier,
    contains_excluded_source_name,
)


class SourceCandidateStatus(str, Enum):
    VALIDATED_SHADOW_CANDIDATE = "validated_shadow_candidate"
    DEFERRED = "deferred"
    VALIDATION_REQUIRED = "validation_required"


class SourceCandidateValidationError(ValueError):
    """Raised only by assert_valid_source_candidate() — never a raw/
    unsanitized value, always the same violation strings
    validate_source_candidate_record() returns."""


class FilterPolicyValidationError(ValueError):
    """Raised only by assert_valid_filter_policy() — never a raw/
    unsanitized value, always the same violation strings
    validate_filter_policy() returns."""


def _is_https_url(url: str | None) -> bool:
    if not url or not url.strip():
        return False
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.netloc)


@dataclass(frozen=True)
class SourceCandidateRecord:
    """One completed, local, read-only source-validation result.
    Deliberately carries NO `enabled` field at all (per this batch's own
    explicit preference) — a candidate record is inert data; it cannot
    be "turned on" by construction, only by a later, separate,
    explicitly-approved batch that creates a real `DailyNewsSourceEntry`
    from it in `source_registry.py` itself.

    `health_state` is optional and, for every record in this batch, left
    `None` — none of these five sources has ever been polled by the real
    worker, so no real `SourceHealthState` fetch-health value exists for
    any of them yet; setting one here would misleadingly imply a live
    result this record does not represent.
    """

    source_id: str
    display_name: str
    category: SourceCategory
    reliability_tier: SourceReliabilityTier
    status: SourceCandidateStatus
    official_landing_page: str
    # ISO 8601 date — when this LOCAL validation pass observed the facts
    # recorded below, never a live worker fetch timestamp.
    validated_at: str
    # A short, explicit note distinguishing this record from a live
    # SourceHealthState/worker result — required non-empty, checked by
    # validate_source_candidate_record() below.
    validation_source_record: str
    health_state: SourceHealthState | None = None
    machine_endpoint: str | None = None
    machine_format: SourceFormat | None = None
    allowed_item_domains: tuple[str, ...] = ()
    language: str = ""
    likely_theme_tags: tuple[str, ...] = ()
    filtering_requirement: str = ""
    # References a FilterPolicy.policy_id below — required for
    # VALIDATED_SHADOW_CANDIDATE, since every such candidate's own known
    # filtering_requirement must have a corresponding proposed policy.
    filter_policy_id: str | None = None
    validation_notes: str = ""
    constraints: str = ""


@dataclass(frozen=True)
class SourceFilterPolicy:
    """Typed, UNUSED proposed filter-policy DATA for a future source's
    theme-relevance gate. Pure data — no fetch, no live evaluation, no
    worker wiring, no import of rss_atom_client/canonical_url/any
    network code. `evaluation_order` documents, as data, the order a
    future (separately-built, separately-approved) evaluator would check
    these fields in — it is not itself executable and performs no
    checking on its own.

    `fail_closed_requires_positive_match` must always be True (enforced
    by validate_filter_policy() below) — the fail-closed contract this
    project applies everywhere else (canonical_url.py's own gate,
    source_registry.py's own admission checks) applies here too: an item
    matching none of this policy's positive-match fields must never be
    admitted by default."""

    policy_id: str
    source_id: str  # the SourceCandidateRecord.source_id this policy is proposed for
    agency_allowlist: tuple[str, ...] = ()
    title_keywords: tuple[str, ...] = ()
    url_keywords: tuple[str, ...] = ()
    topic_theme_tags: tuple[str, ...] = ()
    excluded_terms: tuple[str, ...] = ()
    # Free-text future item-type/status values (e.g. a real Federal
    # Register `type` field value like "Rule", or a later
    # DisclosureItemType/FilingCandidateStatus value) — stored as text
    # since no live evaluator or shared enum reference exists yet for
    # this cross-source concept.
    item_type_status_allowlist: tuple[str, ...] = ()
    fail_closed_requires_positive_match: bool = True
    # Ordered field names documenting the PROPOSED check order for a
    # future evaluator — validated against _EVALUATION_ORDER_FIELDS
    # below, never executed.
    evaluation_order: tuple[str, ...] = ()
    notes: str = ""


_EVALUATION_ORDER_FIELDS: frozenset[str] = frozenset({
    "agency_allowlist", "title_keywords", "url_keywords", "topic_theme_tags",
    "excluded_terms", "item_type_status_allowlist",
})

_POSITIVE_MATCH_FIELDS: tuple[str, ...] = (
    "agency_allowlist", "title_keywords", "url_keywords", "topic_theme_tags",
)


def validate_source_candidate_record(record: SourceCandidateRecord) -> tuple[str, ...]:
    """Pure, single-record validation — returns every violation found
    (empty tuple = valid), never raises. Mirrors
    source_registry.validate_source_entry()'s own "collect every
    violation, don't stop at the first" convention."""
    violations: list[str] = []

    try:
        SourceCategory(record.category)
    except ValueError:
        violations.append(f"unsupported category: {record.category!r}")
    try:
        SourceReliabilityTier(record.reliability_tier)
    except ValueError:
        violations.append(f"unsupported reliability_tier: {record.reliability_tier!r}")
    try:
        SourceCandidateStatus(record.status)
    except ValueError:
        violations.append(f"unsupported status: {record.status!r}")
    if record.health_state is not None:
        try:
            SourceHealthState(record.health_state)
        except ValueError:
            violations.append(f"unsupported health_state: {record.health_state!r}")
    if record.machine_format is not None:
        try:
            SourceFormat(record.machine_format)
        except ValueError:
            violations.append(f"unsupported machine_format: {record.machine_format!r}")

    if not record.source_id or not record.source_id.strip():
        violations.append("source_id must be non-empty")
    if not record.display_name or not record.display_name.strip():
        violations.append("display_name must be non-empty")
    if not _is_https_url(record.official_landing_page):
        violations.append("official_landing_page must be a non-empty https:// URL")
    if not record.validated_at or not record.validated_at.strip():
        violations.append("validated_at must be non-empty")
    if not record.validation_source_record or not record.validation_source_record.strip():
        violations.append("validation_source_record must be non-empty")

    for field_name, value in (
        ("source_id", record.source_id), ("display_name", record.display_name),
        ("validation_notes", record.validation_notes), ("constraints", record.constraints),
    ):
        if contains_excluded_source_name(value):
            violations.append(f"excluded source name matched in {field_name}: {value!r}")

    if record.status == SourceCandidateStatus.VALIDATED_SHADOW_CANDIDATE:
        if not _is_https_url(record.machine_endpoint):
            violations.append("validated_shadow_candidate requires a non-empty https:// machine_endpoint")
        if record.machine_format is None:
            violations.append("validated_shadow_candidate requires a non-null machine_format")
        if not record.allowed_item_domains:
            violations.append("validated_shadow_candidate requires a non-empty allowed_item_domains")
        if not record.language or not record.language.strip():
            violations.append("validated_shadow_candidate requires a non-empty language")
        if not record.filter_policy_id:
            violations.append("validated_shadow_candidate requires a filter_policy_id reference")

    if record.status == SourceCandidateStatus.DEFERRED:
        if record.machine_endpoint is not None:
            violations.append("deferred candidate must have machine_endpoint=None")
        if "no scraper" not in record.constraints.lower():
            violations.append(
                "deferred candidate's constraints must explicitly state that no scraper is "
                "implemented or implied"
            )

    return tuple(violations)


def assert_valid_source_candidate(record: SourceCandidateRecord) -> None:
    violations = validate_source_candidate_record(record)
    if violations:
        raise SourceCandidateValidationError("; ".join(violations))


def validate_filter_policy(policy: SourceFilterPolicy) -> tuple[str, ...]:
    """Pure, single-policy validation — returns every violation found
    (empty tuple = valid), never raises."""
    violations: list[str] = []

    if not policy.policy_id or not policy.policy_id.strip():
        violations.append("policy_id must be non-empty")
    if not policy.source_id or not policy.source_id.strip():
        violations.append("source_id must be non-empty")

    if policy.fail_closed_requires_positive_match is not True:
        violations.append("fail_closed_requires_positive_match must always be True")

    if not any(getattr(policy, field_name) for field_name in _POSITIVE_MATCH_FIELDS):
        violations.append(
            "a fail-closed policy must define at least one non-empty positive-match field "
            f"({', '.join(_POSITIVE_MATCH_FIELDS)})"
        )

    unknown_order_fields = set(policy.evaluation_order) - _EVALUATION_ORDER_FIELDS
    if unknown_order_fields:
        violations.append(f"evaluation_order contains unrecognized field name(s): {sorted(unknown_order_fields)}")

    if contains_excluded_source_name(policy.notes):
        violations.append(f"excluded source name matched in notes: {policy.notes!r}")

    return tuple(violations)


def assert_valid_filter_policy(policy: SourceFilterPolicy) -> None:
    violations = validate_filter_policy(policy)
    if violations:
        raise FilterPolicyValidationError("; ".join(violations))


# ============================================================
# Proposed filter policies — data only, per source-validated candidate
# below that carries a known filtering_requirement. No fetch, no live
# evaluation code anywhere in this module.
# ============================================================

FILTER_POLICIES: tuple[SourceFilterPolicy, ...] = (
    SourceFilterPolicy(
        policy_id="federal-register-agency-keyword-filter",
        source_id="federal-register-api",
        # Real agency names as they appear in the Federal Register API's
        # own `agencies[].name` field (live-sampled 2026-09-04) — Office
        # of the U.S. Trade Representative is included deliberately: USTR's
        # own site (ustr.gov) has no feed, but USTR-issued determinations
        # and notices ARE published in the Federal Register, so this is a
        # real path to USTR-originated material even though ustr-press-
        # releases itself is deferred below.
        agency_allowlist=(
            "Bureau of Industry and Security", "Commerce Department",
            "National Institute of Standards and Technology",
            "Office of the United States Trade Representative",
        ),
        title_keywords=(
            "semiconductor", "export control", "advanced computing", "CHIPS Act", "entity list",
        ),
        url_keywords=("export-control", "entity-list", "semiconductor"),
        topic_theme_tags=("ai-buildout", "memory"),
        excluded_terms=(),
        # Real `type` field values observed live in a 3-item sample from
        # documents.json (2026-09-04): "Rule", "Notice", "Proposed Rule".
        item_type_status_allowlist=("Rule", "Proposed Rule", "Notice"),
        evaluation_order=("agency_allowlist", "title_keywords", "url_keywords", "topic_theme_tags", "excluded_terms"),
        notes=(
            "Proposed data only — no evaluator exists yet. Federal Register's API covers every "
            "federal agency; without this filter (or an equivalent), federal-register-api would "
            "surface overwhelmingly off-theme content."
        ),
    ),
    SourceFilterPolicy(
        policy_id="nist-chips-topic-filter",
        source_id="nist-chips-news",
        agency_allowlist=(),
        title_keywords=("CHIPS", "semiconductor", "fab", "advanced packaging", "funding award", "wafer"),
        url_keywords=("chips",),
        topic_theme_tags=("ai-buildout", "memory"),
        # Real off-theme topics observed live in the sampled feed
        # (2026-09-04): quantum-technology, construction-safety-committee,
        # nanotech-measurement-methodology items alongside the CHIPS ones.
        excluded_terms=("quantum", "forensic science", "cybersecurity", "construction safety"),
        item_type_status_allowlist=(),
        evaluation_order=("title_keywords", "url_keywords", "topic_theme_tags", "excluded_terms"),
        notes=(
            "Proposed data only — no evaluator exists yet. nist-chips-news is NIST's entire news "
            "RSS output, not a CHIPS-specific feed; without this filter almost every item would be "
            "off-theme (NIST covers metrology, cybersecurity, forensics, etc., far beyond CHIPS)."
        ),
    ),
    SourceFilterPolicy(
        policy_id="jpx-institutional-scope-filter",
        source_id="jpx-news",
        agency_allowlist=(),
        # Real, source-provided bracketed classification prefixes
        # observed live in every sampled item's own title (2026-09-04),
        # e.g. "[JPX]JPX Monthly Headlines - August 2026",
        # "[JPX,TSE,OSE,TOCOM]Trading Overview in August 2026" — JPX's
        # own institutional/regulatory categories, not an invented list.
        title_keywords=("[JPX]", "[TSE]", "[OSE]", "[TOCOM]", "[JPX-R]"),
        url_keywords=(),
        topic_theme_tags=(),  # deliberately empty — see jpx-news record's own notes
        excluded_terms=(),
        item_type_status_allowlist=(),
        evaluation_order=("title_keywords",),
        notes=(
            "Proposed data only — no evaluator exists yet. This policy exists primarily to keep "
            "jpx-news scoped to JPX's own institutional/regulatory news (the only content this "
            "feed actually carries) and to make explicit, at the data level, that this is NOT a "
            "per-issuer timely-disclosure (TDnet) source — TDnet remains separately deferred "
            "(design/DECISIONS.md) and is not represented anywhere in this module."
        ),
    ),
)


# ============================================================
# Validated source candidates — exactly the five records from the
# completed 2026-09-04 bounded, read-only, permitted-source-only live
# validation pass. No more, no fewer.
# ============================================================

VALIDATED_SOURCE_CANDIDATES: tuple[SourceCandidateRecord, ...] = (
    SourceCandidateRecord(
        source_id="federal-register-api",
        display_name="U.S. Federal Register",
        category=SourceCategory.GOVERNMENT_POLICY,
        reliability_tier=SourceReliabilityTier.SHADOW_ONLY,
        status=SourceCandidateStatus.VALIDATED_SHADOW_CANDIDATE,
        official_landing_page="https://www.federalregister.gov",
        validated_at="2026-09-04",
        validation_source_record=(
            "Local, bounded, read-only browser validation pass, 2026-09-04 — not a live "
            "SourceHealthState/worker fetch result; health_state is intentionally None."
        ),
        machine_endpoint="https://www.federalregister.gov/api/v1/documents.json",
        machine_format=SourceFormat.OFFICIAL_API,
        # federalregister.gov only — govinfo.gov (used separately for
        # each item's own pdf_url) is a distinct official domain
        # deliberately NOT included here; that is a separate, later
        # decision, per this batch's own explicit instruction.
        allowed_item_domains=("federalregister.gov",),
        language="English",
        likely_theme_tags=("ai-buildout", "memory"),
        filtering_requirement=(
            "Agency and keyword allowlist required before any ingestion — this API covers every "
            "federal agency, not just export-control/semiconductor-relevant ones."
        ),
        filter_policy_id="federal-register-agency-keyword-filter",
        validation_notes=(
            "Live-verified 2026-09-04: real JSON response from documents.json (per_page=3, "
            "order=newest) returned real items with title/type/document_number/html_url/"
            "pdf_url/publication_date/agencies fields. No API key required; CORS headers "
            "documented (a live fetch() call from this session's own sandboxed browser was "
            "blocked by an unrelated environment-level network restriction — direct page "
            "navigation to the same URL succeeded and returned the real JSON body). "
            "Pagination: first 2000 results via page/per_page, then date-range filters for "
            "deeper history, per the API's own documented behavior. html_url values sampled "
            "were direct, individual document pages (e.g. .../documents/2026/09/04/2026-18194/"
            "geographic-targeting-order-...), never a search/listing page."
        ),
        constraints=(
            "Only stated usage restriction found: republishers may not use official NARA/OFR "
            "logos or seals. govinfo.gov (pdf_url) intentionally not added to allowed_item_domains."
        ),
    ),
    SourceCandidateRecord(
        source_id="nist-chips-news",
        display_name="NIST News (CHIPS Program Office coverage)",
        category=SourceCategory.GOVERNMENT_POLICY,
        reliability_tier=SourceReliabilityTier.SHADOW_ONLY,
        status=SourceCandidateStatus.VALIDATED_SHADOW_CANDIDATE,
        official_landing_page="https://www.nist.gov/chips",
        validated_at="2026-09-04",
        validation_source_record=(
            "Local, bounded, read-only browser validation pass, 2026-09-04 — not a live "
            "SourceHealthState/worker fetch result; health_state is intentionally None."
        ),
        machine_endpoint="https://www.nist.gov/news-events/news/rss.xml",
        machine_format=SourceFormat.RSS_ATOM,
        # www.nist.gov only, matching the exact hostname actually used by
        # every sampled item link — no bare "nist.gov" form was observed
        # on any real item URL, so it is not added (domain validation
        # architecture does not require both forms here).
        allowed_item_domains=("www.nist.gov",),
        language="English",
        likely_theme_tags=("ai-buildout", "memory"),
        filtering_requirement=(
            "CHIPS/semiconductor title/URL/topic keyword filter required before ingestion — this "
            "is NIST's entire news RSS output (all topics: metrology, cybersecurity, forensics, "
            "quantum, etc.), not a CHIPS-specific feed."
        ),
        filter_policy_id="nist-chips-topic-filter",
        validation_notes=(
            "Live-verified 2026-09-04: real RSS 2.0 fetch, channel title \"NIST News\", items "
            "carry title/link/description/pubDate (RFC 822)/dc:creator/guid. No CHIPS-specific "
            "feed was found under NIST's own RSS-feeds hub (By Type: NIST News/NIST Events; By "
            "Topic: no CHIPS or Semiconductor entry). CHIPS-relevant items were separately "
            "confirmed live on the CHIPS landing page itself (e.g. TSMC $100B, Bosch $225M, "
            "I-Pulse $250M funding-award announcements), all sharing the same "
            "www.nist.gov/news-events/news/... URL pattern as the general feed's own items."
        ),
        constraints="No explicit RSS terms/reuse text was found on the RSS-feeds hub page sampled.",
    ),
    SourceCandidateRecord(
        source_id="jpx-news",
        display_name="JPX News Release (Japan Exchange Group)",
        category=SourceCategory.EXCHANGE,
        reliability_tier=SourceReliabilityTier.SHADOW_ONLY,
        status=SourceCandidateStatus.VALIDATED_SHADOW_CANDIDATE,
        official_landing_page="https://www.jpx.co.jp/english/",
        validated_at="2026-09-04",
        validation_source_record=(
            "Local, bounded, read-only browser validation pass, 2026-09-04 — not a live "
            "SourceHealthState/worker fetch result; health_state is intentionally None."
        ),
        machine_endpoint="https://www.jpx.co.jp/english/rss/jpx-news.xml",
        machine_format=SourceFormat.RSS_ATOM,
        # www.jpx.co.jp only, matching the exact hostname actually used
        # by every sampled item link — no bare "jpx.co.jp" form was
        # observed on any real item URL from this feed.
        allowed_item_domains=("www.jpx.co.jp",),
        language="English",
        likely_theme_tags=(),  # deliberately empty — see validation_notes
        filtering_requirement=(
            "Exchange/institutional-news scope filter required. This is JPX's own corporate/"
            "regulatory news (market operations, monthly headlines, regulatory annual reports) — "
            "it is NOT TDnet and must never be described as per-issuer timely disclosure."
        ),
        filter_policy_id="jpx-institutional-scope-filter",
        validation_notes=(
            "Live-verified 2026-09-04: real RSS 2.0 fetch, channel title \"JPX News Release\", "
            "items carry title/link/guid/pubDate (RFC 822 with +0900 JST offset). Sampled items: "
            "JPX Monthly Headlines, Trading Overview (JPX/TSE/OSE/TOCOM), Nadeshiko Brands "
            "applications, JPX-R Annual Report, JJ-Link Phase 2 — all institutional/market-"
            "operations content, none an individual-issuer material-event disclosure. Left "
            "likely_theme_tags empty deliberately: this content is not mapped to any of the five "
            "Eeva themes (ai-buildout/humanoids/space/memory/photonics) — it is issuer-agnostic "
            "exchange news, matching this project's own \"leave unset rather than misapplied\" "
            "convention (see e.g. Applied Materials' own tracked_companies.py entry)."
        ),
        constraints=(
            "JPX's own stated terms (RSS index page): \"JPX shall not be liable for any damages "
            "or losses incurred by any user or third party arising from the use of RSS feeds\"; "
            "\"questions about a use of RSS and RSS tools are not acceptable\" (no support "
            "provided); \"delivery may be subject to stopped and URL, contents or format may be "
            "subject to change without notice.\" Five sibling feeds exist at the same path "
            "(markets_news.xml, equities-suspended.xml, derivatives-suspended.xml, alerts.xml, "
            "site-updates.xml) — only jpx-news.xml was sampled; the others are out of scope for "
            "this batch."
        ),
    ),
    SourceCandidateRecord(
        source_id="bis-news-updates",
        display_name="BIS News & Updates (Bureau of Industry and Security)",
        category=SourceCategory.REGULATOR,
        reliability_tier=SourceReliabilityTier.VALIDATION_REQUIRED,
        status=SourceCandidateStatus.DEFERRED,
        official_landing_page="https://www.bis.gov/news-updates",
        validated_at="2026-09-04",
        validation_source_record=(
            "Local, bounded, read-only browser validation pass, 2026-09-04 — not a live "
            "SourceHealthState/worker fetch result; health_state is intentionally None."
        ),
        machine_endpoint=None,
        machine_format=None,
        allowed_item_domains=(),  # intentionally empty — no machine endpoint exists to validate item links against yet
        language="English",
        likely_theme_tags=("ai-buildout",),
        filtering_requirement="Not applicable yet — no machine endpoint exists to filter.",
        filter_policy_id=None,
        validation_notes=(
            "No RSS/Atom/API discoverable anywhere on bis.gov (bis.doc.gov redirects here) after "
            "checking the homepage and the /news-updates listing page (a faceted HTML search by "
            "topic/country). Individual press-release pages ARE real, direct, dated-URL pages "
            "(e.g. bis.gov/press-release/robert-bosch-gmbh-bosch-pay-36-million-penalty-bis-"
            "violations-pertaining-shipments-huawei — export-control enforcement, "
            "semiconductor-adjacent), confirming genuine per-item content exists, but only via "
            "an HTML listing, never a feed."
        ),
        constraints=(
            "Requires a separately approved HTML-listing adapter plus item-date-extraction work "
            "before this source could be validated further. No scraper is implemented, "
            "suggested, or implied by this record."
        ),
    ),
    SourceCandidateRecord(
        source_id="ustr-press-releases",
        display_name="USTR Press Releases",
        category=SourceCategory.GOVERNMENT_POLICY,
        reliability_tier=SourceReliabilityTier.VALIDATION_REQUIRED,
        status=SourceCandidateStatus.DEFERRED,
        official_landing_page="https://ustr.gov/about-us/policy-offices/press-office/press-releases",
        validated_at="2026-09-04",
        validation_source_record=(
            "Local, bounded, read-only browser validation pass, 2026-09-04 — not a live "
            "SourceHealthState/worker fetch result; health_state is intentionally None."
        ),
        machine_endpoint=None,
        machine_format=None,
        allowed_item_domains=(),  # intentionally empty — no machine endpoint exists to validate item links against yet
        language="English",
        likely_theme_tags=(),  # deliberately empty — see validation_notes
        filtering_requirement=(
            "Not applicable yet — no machine endpoint exists to filter. Even if one is found "
            "later, content is broadly diplomatic/trade-political and would require strict theme "
            "filtering before any ingestion."
        ),
        filter_policy_id=None,
        validation_notes=(
            "No RSS/Atom/API discoverable anywhere on ustr.gov after checking the homepage and "
            "the press-releases listing page. Individual press-release pages are real, direct, "
            "dated-URL pages (e.g. ustr.gov/about/policy-offices/press-office/press-releases/"
            "2026/august/...), but sampled content (G20 ministerial credentialing, an Iowa trip, "
            "a labor-mechanism resolution) was predominantly general trade-diplomacy content, "
            "not clearly Eeva-theme-relevant on its face — left likely_theme_tags empty rather "
            "than overstating relevance. Note: USTR-issued official determinations/notices DO "
            "separately appear in the Federal Register (see federalregister-agency-keyword-"
            "filter's own agency_allowlist above), which is a real, working API — that is the "
            "recommended path to USTR-originated material, not this deferred candidate."
        ),
        constraints=(
            "Requires a separately approved HTML-listing adapter plus item-date-extraction work "
            "before this source could be validated further. No scraper is implemented, "
            "suggested, or implied by this record."
        ),
    ),
)
