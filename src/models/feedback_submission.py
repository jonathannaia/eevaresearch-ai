"""Open-beta feedback — the minimal per-account record src/ui/pages/
feedback.py writes and src/ui/pages/admin_users.py's own "Open-beta
feedback" section reads. Same minimalism convention as
src/models/user_account.py: normalized email as the natural identity,
one closed-choice answer per question, one conditional free-text detail,
and one optional free-text field. No IP address, device/browser
fingerprint, page-view or event history, OAuth token, session/cookie
data, analytics event, or payment data — this is a lightweight product-
feedback record, not a profiling or support-ticket system.

Upsert-on-email semantics (see the repository modules): a signed-in
user has at most one row, always their latest answer — resubmitting
replaces the prior response rather than creating a second row."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FeedbackRole(str, Enum):
    INDIVIDUAL_INVESTOR = "Individual investor"
    PROFESSIONAL_INVESTOR_OR_ANALYST = "Professional investor or analyst"
    JOURNALIST_OR_MEDIA = "Journalist or media"
    RESEARCHER_OR_ACADEMIC = "Researcher or academic"
    OTHER = "Other"


class FeedbackTrackingWorkflow(str, Enum):
    INVESTOR_RELATIONS_PAGES = "Company investor-relations pages"
    NEWS_ALERTS = "News alerts"
    PAID_TERMINAL = "Paid terminal"
    ANOTHER_RESEARCH_TOOL = "Another research tool"
    DOES_NOT_CURRENTLY_TRACK = "I do not currently track them"
    OTHER = "Other"


class FeedbackPrimaryInterest(str, Enum):
    US_FILINGS_EDGAR = "U.S. filings (EDGAR)"
    KOREA_FILINGS_DART = "Korea filings (DART)"
    JAPAN_FILINGS_EDINET = "Japan filings (EDINET)"
    DAILY_NEWS = "Daily News"
    THEME_AND_SUPPLY_CHAIN_RESEARCH = "Theme and supply-chain research"


# Server-side length bounds (see src/ui/pages/feedback.py's own
# validation) — the only place free text from this feature is ever
# accepted. Deliberately small: this is a two-minute pulse-check form,
# not a long-form ticket.
MAX_TRACKING_WORKFLOW_OTHER_LENGTH = 200
MAX_WEEKLY_VALUE_FEEDBACK_LENGTH = 1000


@dataclass(frozen=True)
class FeedbackSubmission:
    email: str
    display_name: str | None
    submitted_at: str
    role: FeedbackRole
    tracking_workflow: FeedbackTrackingWorkflow
    tracking_workflow_other: str | None
    primary_interest: FeedbackPrimaryInterest
    weekly_value_feedback: str | None
