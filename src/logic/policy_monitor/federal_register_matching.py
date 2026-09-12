"""Federal Register policy-monitor pilot — deterministic, fail-closed
matching (design/DECISIONS.md, Federal Register Policy Monitor Pilot).
No LLM, no ranking score, no persistence: a pure function over one
fetch's worth of FederalRegisterDocument rows, returning at most three
QualifyingPolicyDevelopment items, newest-publication-date first.

Reuses, read-only, the existing approved
src.data_access.daily_news.source_candidates.FILTER_POLICIES entry
"federal-register-agency-keyword-filter" for the agency allowlist and
title/url keywords — that file is not modified by this module. Also
reuses two pieces of the existing, otherwise-unused
src.data_access.daily_news.policy_disclosure_models foundation that map
onto a Federal Register document without ambiguity: DisclosureItemType
(the closed item-type vocabulary) and OfficialDisclosureProvenance (the
provenance shape). This module deliberately does NOT construct a
PolicyDisclosureCandidate — that dataclass's own required
lifecycle_status field (PROPOSED / FINAL / EFFECTIVE) has no single
agreed mapping for a Federal Register "Notice" (approved this batch
only for item_type: Notice -> AGENCY_ACTION), and inventing one was not
part of what was approved — see the accompanying implementation report.

Qualification requires, in order, ALL of: a non-empty, not-already-seen
document_number; a non-empty https html_url; a valid parseable
publication_date; a type exactly "Rule", "Proposed Rule", or "Notice"
(mapped to the approved DisclosureItemType); an agency matching the
approved allowlist; AND at least one approved title/url keyword match
(agency-only and keyword-only matches both fail closed — approved
combinator). Any failure suppresses the item entirely; nothing is
partially rendered.

Theme scope: AI Buildout only (Federal Register Policy Monitor Pilot,
scope correction). Every approved title/url keyword
("semiconductor", "export control", "advanced computing", "CHIPS Act",
"entity list") resolves only to "ai-buildout" — this pilot does not
implement Memory theme coverage. The underlying SourceFilterPolicy's
own `topic_theme_tags` names both "ai-buildout" and "memory", but that
field describes the Federal Register source's broader, not-yet-built
future scope, not this pilot's own current scope: none of the approved
keywords is memory-specific, and this batch does not add, infer, or
guess one to bridge that gap. Memory policy coverage is explicitly a
separate, future, not-yet-approved research/validation/implementation
task."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.data_access.daily_news.policy_disclosure_models import DisclosureItemType, OfficialDisclosureProvenance
from src.data_access.daily_news.source_candidates import FILTER_POLICIES, SourceFilterPolicy
from src.data_access.policy_monitor.federal_register_client import FederalRegisterDocument

_POLICY_ID = "federal-register-agency-keyword-filter"
_MAX_QUALIFYING = 3

# Federal Register's own literal `type` display strings (live-sampled,
# see source_candidates.py's own federal-register-api validation notes)
# mapped to the approved, closed DisclosureItemType vocabulary. Approved
# mapping (Federal Register Policy Monitor Pilot) — do not extend
# without separate approval.
_TYPE_MAPPING: dict[str, DisclosureItemType] = {
    "Rule": DisclosureItemType.ENACTED_RULE,
    "Proposed Rule": DisclosureItemType.PROPOSED_RULE,
    "Notice": DisclosureItemType.AGENCY_ACTION,
}

_THEME_DISPLAY_NAMES: dict[str, str] = {
    "ai-buildout": "AI Buildout",
}

# Every approved title/url keyword (source_candidates.py's own
# federal-register-agency-keyword-filter policy), lowercased, mapped to
# (theme_slug, display topic label). This pilot is AI Buildout only
# (see module docstring) — every entry below resolves to "ai-buildout".
# The url_keywords' two hyphenated forms map to the same display label
# as their title-keyword equivalent.
_KEYWORD_RESOLUTION: dict[str, tuple[str, str]] = {
    "semiconductor": ("ai-buildout", "Semiconductor"),
    "export control": ("ai-buildout", "Export control"),
    "advanced computing": ("ai-buildout", "Advanced computing"),
    "chips act": ("ai-buildout", "CHIPS Act"),
    "entity list": ("ai-buildout", "Entity list"),
    "export-control": ("ai-buildout", "Export control"),
    "entity-list": ("ai-buildout", "Entity list"),
}


@dataclass(frozen=True)
class QualifyingPolicyDevelopment:
    """One Federal Register document that has passed every fail-closed
    gate in _qualify_document(). Read-time/in-memory only — never
    persisted, never a NewsStory, never touches Daily News in any way."""

    title: str
    item_type: DisclosureItemType
    type_label: str  # verbatim "Rule" | "Proposed Rule" | "Notice"
    document_number: str
    html_url: str
    publication_date: str  # "YYYY-MM-DD" — date-only, never rendered with a fabricated time-of-day
    agency_name: str
    provenance: OfficialDisclosureProvenance
    theme_slug: str
    theme_display_name: str
    matched_topic: str
    reason: str  # exact "Why shown: <theme display name> · <topic>" string


def _federal_register_filter_policy() -> SourceFilterPolicy:
    for policy in FILTER_POLICIES:
        if policy.policy_id == _POLICY_ID:
            return policy
    raise LookupError(f"{_POLICY_ID!r} not found in source_candidates.FILTER_POLICIES")


def _is_valid_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


def _matched_agency(agency_names: tuple[str, ...], agency_allowlist: tuple[str, ...]) -> str | None:
    for allowed in agency_allowlist:
        if allowed in agency_names:
            return allowed
    return None


def _matched_keyword(
    title: str, html_url: str, title_keywords: tuple[str, ...], url_keywords: tuple[str, ...],
) -> str | None:
    lowered_title = title.lower()
    for keyword in title_keywords:
        if keyword.lower() in lowered_title:
            return keyword.lower()
    lowered_url = html_url.lower()
    for keyword in url_keywords:
        if keyword.lower() in lowered_url:
            return keyword.lower()
    return None


def _qualify_document(
    doc: FederalRegisterDocument, policy: SourceFilterPolicy, retrieved_at: str,
) -> QualifyingPolicyDevelopment | None:
    if not doc.document_number:
        return None
    if not doc.html_url:
        return None
    if not doc.publication_date or not _is_valid_date(doc.publication_date):
        return None
    if not doc.type or doc.type not in _TYPE_MAPPING:
        return None
    if not doc.title or not doc.title.strip():
        return None

    item_type = _TYPE_MAPPING[doc.type]

    matched_agency = _matched_agency(doc.agency_names, policy.agency_allowlist)
    if matched_agency is None:
        return None

    matched_keyword = _matched_keyword(doc.title, doc.html_url, policy.title_keywords, policy.url_keywords)
    if matched_keyword is None:
        return None

    resolution = _KEYWORD_RESOLUTION.get(matched_keyword)
    if resolution is None:
        return None  # fail closed: an unresolved keyword never renders an ambiguous theme
    theme_slug, topic_label = resolution
    theme_display_name = _THEME_DISPLAY_NAMES.get(theme_slug)
    if theme_display_name is None:
        return None

    reason = f"Why shown: {theme_display_name} · {topic_label}"

    provenance = OfficialDisclosureProvenance(
        issuing_body="U.S. Federal Register", retrieved_at=retrieved_at, document_identifier=doc.document_number,
    )
    return QualifyingPolicyDevelopment(
        title=doc.title, item_type=item_type, type_label=doc.type, document_number=doc.document_number,
        html_url=doc.html_url, publication_date=doc.publication_date, agency_name=matched_agency,
        provenance=provenance, theme_slug=theme_slug, theme_display_name=theme_display_name,
        matched_topic=topic_label, reason=reason,
    )


def qualifying_policy_developments(
    documents: tuple[FederalRegisterDocument, ...],
) -> tuple[QualifyingPolicyDevelopment, ...]:
    """The pilot's one public entry point: every fail-closed gate,
    per-fetch document-number dedup, deterministic ordering (publication
    date descending, document number ascending tie-break — two stable
    sorts, applied in that order), and the top-three bound, all in one
    place. Returns an empty tuple, never raises, if nothing qualifies."""
    policy = _federal_register_filter_policy()
    retrieved_at = datetime.now(timezone.utc).isoformat()

    seen_document_numbers: set[str] = set()
    qualifying: list[QualifyingPolicyDevelopment] = []
    for doc in documents:
        if doc.document_number and doc.document_number in seen_document_numbers:
            continue
        item = _qualify_document(doc, policy, retrieved_at)
        if item is None:
            continue
        seen_document_numbers.add(item.document_number)
        qualifying.append(item)

    ordered = sorted(qualifying, key=lambda item: item.document_number)
    ordered = sorted(ordered, key=lambda item: item.publication_date, reverse=True)
    return tuple(ordered[:_MAX_QUALIFYING])
