"""Daily News Source-Expansion & Ingestion Design, Batch 1 — a typed,
UNUSED foundation model for a future government_policy /
government_procurement Daily News item (design doc §3). Nothing in this
module is imported or constructed by daily_news_pipeline.py, scripts/
daily_news_worker.py, daily_news_backend.py, any UI page, or any
translation provider — see tests/test_daily_news_expansion_models.py's
own isolation tests for the mechanical proof.

Political/policy safeguard, encoded as a type, not left to editorial
judgment at publish time (design doc §3):

- DisclosureItemType is a CLOSED, fixed vocabulary — the exact 8 item
  types the approved design names. An unrecognized item type is rejected
  by validate_policy_disclosure_candidate() below; there is no free-form
  "other" escape hatch, unlike some of this project's filing-category
  taxonomies elsewhere, since an unbounded political-content category is
  exactly the risk this safeguard exists to prevent.
- DisclosureClassification has exactly ONE member,
  OFFICIAL_PUBLIC_DISCLOSURE — every instance of this model IS that
  classification; the field exists (rather than being an implicit,
  undeclared assumption) so a caller/renderer can display the label
  without inventing its own string, and so the validator has something
  concrete to assert against.
- No field on PolicyDisclosureCandidate expresses inferred political
  intent, an investment recommendation, or sentiment — verified by this
  module's own field-name denylist check
  (test_daily_news_expansion_models.py's
  test_no_field_name_suggests_intent_recommendation_or_sentiment), not
  merely by omission in this docstring.
- DISCLOSED_TRADE_HOLDING_LOBBYING items are structurally barred from
  AUTONOMOUS_ELIGIBLE publication_eligibility — see
  validate_policy_disclosure_candidate()'s own explicit check. This is
  the task's own explicit correction: a disclosed politician trade/
  holding/lobbying record, if represented at all, is shadow_only or
  manual_review_required metadata, never autonomous-publish eligible.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse

from src.data_access.daily_news.source_registry import contains_excluded_source_name


class DisclosureItemType(str, Enum):
    """Closed vocabulary — see module docstring. Exactly the 8 eligible
    item types the approved design document names (§3); no other value
    is ever valid."""

    ENACTED_RULE = "enacted_rule"
    PROPOSED_RULE = "proposed_rule"
    AGENCY_ACTION = "agency_action"
    OFFICIAL_PRESS_RELEASE = "official_press_release"
    BILL_STATUS = "bill_status"
    BUDGET_APPROPRIATION = "budget_appropriation"
    PROCUREMENT_AWARD = "procurement_award"
    DISCLOSED_TRADE_HOLDING_LOBBYING = "disclosed_trade_holding_lobbying"


class DisclosureLifecycleStatus(str, Enum):
    """A coarse, closed lifecycle marker distinguishing a confirmed
    action from a mere proposal (design doc §3) — the exact stage detail
    (e.g. a bill's real legislative-stage text, or a procurement award's
    own status string) belongs in `status_detail` below, which is
    free-text precisely because that finer vocabulary genuinely varies
    by item type and jurisdiction; this field only ever answers "is this
    proposed, final and not yet in effect, or already in effect."""

    PROPOSED = "proposed"
    FINAL = "final"
    EFFECTIVE = "effective"


class DisclosureClassification(str, Enum):
    """Exactly one member — see module docstring. Every
    PolicyDisclosureCandidate's own `classification` field must equal
    this value; validate_policy_disclosure_candidate() rejects anything
    else, including a plausible-looking but different string."""

    OFFICIAL_PUBLIC_DISCLOSURE = "Official public disclosure"


class PublicationEligibility(str, Enum):
    AUTONOMOUS_ELIGIBLE = "autonomous_eligible"
    SHADOW_ONLY = "shadow_only"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


class PolicyDisclosureValidationError(ValueError):
    """Raised only by assert_valid_policy_disclosure_candidate() — never
    a raw/unsanitized value, always the same violation strings
    validate_policy_disclosure_candidate() returns."""


def _is_https_url(url: str | None) -> bool:
    if not url or not url.strip():
        return False
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.netloc)


@dataclass(frozen=True)
class OfficialDisclosureProvenance:
    """Immutable, verbatim record of exactly which official body issued
    this disclosure and where it was retrieved from — never a news
    outlet's characterization of it. `issuing_body` is checked against
    this project's fixed excluded-source-name list
    (source_registry.contains_excluded_source_name) by
    validate_policy_disclosure_candidate() below, the same guard every
    other Daily News source-name-shaped field already carries."""

    issuing_body: str  # e.g. "U.S. Federal Register", "USAspending.gov", "Congress.gov"
    retrieved_at: str  # ISO 8601
    document_identifier: str  # e.g. Federal Register document number, docket number, award ID, bill number


@dataclass(frozen=True)
class PolicyDisclosureCandidate:
    """Typed, UNUSED foundation model — see module docstring. No field
    here expresses inferred intent, a recommendation, or sentiment;
    `subject_entity_name` and `notes` are both explicitly factual-only
    (an agency, official, or bidder's own official name; a
    non-interpretive clarifying note), never a characterization."""

    item_type: DisclosureItemType
    classification: DisclosureClassification
    lifecycle_status: DisclosureLifecycleStatus
    status_detail: str  # required, non-empty free text — e.g. the exact bill stage or procurement status string
    title: str  # verbatim, official title/caption of the disclosure
    published_at: str  # ISO 8601, the official document's own date
    provenance: OfficialDisclosureProvenance
    publication_eligibility: PublicationEligibility
    official_document_url: str | None = None
    subject_entity_name: str | None = None  # factual only — e.g. the issuing agency, official, or bidder's own name
    notes: str = ""


def validate_policy_disclosure_candidate(candidate: PolicyDisclosureCandidate) -> tuple[str, ...]:
    """Pure, single-candidate validation — returns every violation found
    (empty tuple = valid), never raises. Mirrors
    source_registry.validate_source_entry()'s own "collect every
    violation, don't stop at the first" convention."""
    violations: list[str] = []

    try:
        DisclosureItemType(candidate.item_type)
    except ValueError:
        violations.append(f"unsupported item_type: {candidate.item_type!r}")

    try:
        DisclosureLifecycleStatus(candidate.lifecycle_status)
    except ValueError:
        violations.append(f"unsupported lifecycle_status: {candidate.lifecycle_status!r}")

    try:
        PublicationEligibility(candidate.publication_eligibility)
    except ValueError:
        violations.append(f"unsupported publication_eligibility: {candidate.publication_eligibility!r}")

    if candidate.classification != DisclosureClassification.OFFICIAL_PUBLIC_DISCLOSURE:
        violations.append(
            "classification must be DisclosureClassification.OFFICIAL_PUBLIC_DISCLOSURE — "
            f"got {candidate.classification!r}"
        )

    if not candidate.status_detail or not candidate.status_detail.strip():
        violations.append("status_detail must be non-empty")
    if not candidate.title or not candidate.title.strip():
        violations.append("title must be non-empty")
    if not candidate.published_at or not candidate.published_at.strip():
        violations.append("published_at must be non-empty")

    if not candidate.provenance.issuing_body or not candidate.provenance.issuing_body.strip():
        violations.append("provenance.issuing_body must be non-empty")
    if not candidate.provenance.retrieved_at or not candidate.provenance.retrieved_at.strip():
        violations.append("provenance.retrieved_at must be non-empty")
    if not candidate.provenance.document_identifier or not candidate.provenance.document_identifier.strip():
        violations.append("provenance.document_identifier must be non-empty")

    if contains_excluded_source_name(candidate.provenance.issuing_body):
        violations.append(f"excluded source name matched in provenance.issuing_body: {candidate.provenance.issuing_body!r}")

    if candidate.official_document_url is not None and not _is_https_url(candidate.official_document_url):
        violations.append("official_document_url, when set, must be a non-empty https:// URL")

    # Structural safeguard (design doc §3's own explicit correction): a
    # disclosed politician trade/holding/lobbying record is never
    # autonomous-publish eligible, regardless of any other field.
    if (
        candidate.item_type == DisclosureItemType.DISCLOSED_TRADE_HOLDING_LOBBYING
        and candidate.publication_eligibility == PublicationEligibility.AUTONOMOUS_ELIGIBLE
    ):
        violations.append(
            "disclosed_trade_holding_lobbying items must never be publication_eligibility="
            "AUTONOMOUS_ELIGIBLE — use SHADOW_ONLY or MANUAL_REVIEW_REQUIRED"
        )

    return tuple(violations)


def assert_valid_policy_disclosure_candidate(candidate: PolicyDisclosureCandidate) -> None:
    violations = validate_policy_disclosure_candidate(candidate)
    if violations:
        raise PolicyDisclosureValidationError("; ".join(violations))
