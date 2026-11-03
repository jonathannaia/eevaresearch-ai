"""Reader-facing Daily News ingestion status — pure functions turning the
Daily News worker's own already-persisted status rows into one honest
line a reader can act on. Mirrors src/logic/radar_freshness.py's shape
and discipline for the Radar lane (classify what was persisted, never
recompute it), but is deliberately a separate module reading separate
tables: the Daily News lane shares no store, no cursor, and no worker
with Radar, and `src/ui/pages/daily_news.py` must never import a
Radar-owned file (see that page's own docstring).

Reads nothing except what is passed in. No story records, no published_at
timestamps, no page-render time, no file modification time, no source
code: **worker health is never inferred from story content**. A feed that
is fetching successfully and simply has nothing new to report is exactly
as healthy as a busy one — that is the whole distinction this module
exists to make, and reading story dates would destroy it (see
daily_news_scan_status_repository.py's own note on why
`last_fetch_success_at` and `last_story_published_at` are separate
fields).

The five states, and the persisted facts each one is entitled to claim:

  * `current`         — the worker's newest recorded completed tick is
                        within the staleness threshold and recorded no
                        failure. It means a run was RECORDED recently —
                        never that new content arrived, which this module
                        cannot see at all.
  * `partial`         — that tick completed, but at least one registered
                        company feed's own latest attempt recorded a
                        failure code. The feed-level rows distinguish
                        this; the worker row alone cannot.
  * `attempt_failed`  — the worker's own latest tick recorded a failure
                        code. That is a REPORTED failure and nothing
                        more: nothing stored establishes whether the tick
                        went on to finish, so no message here claims it
                        did not. Any earlier recorded run is reported
                        alongside the failure, never instead of it, and
                        `last_success_stale` records whether that earlier
                        run is itself outside the recency window, so a
                        failure on top of a long gap reads as both facts
                        rather than just the newer one.
  * `stale`           — the newest recorded completed tick is older
                        than the threshold below.
  * `unknown`         — the status was read successfully and holds no
                        completed run: no row at all, or a row whose
                        `last_tick_completed_at` is genuinely EMPTY.
                        This state may say that no completed run is
                        recorded, because that was actually observed.
  * `unavailable`     — the completion evidence could not be read.
                        Two distinct causes, both indeterminate rather
                        than absent: the status read itself could not be
                        performed, or the row holds a completion
                        timestamp that is PRESENT but unparseable. The
                        second case matters as much as the first: a
                        malformed value is evidence that a run WAS
                        recorded, so reporting it as "no completed run
                        is recorded" would be exactly the absence claim
                        this state exists to avoid. Each cause has its
                        own message; neither claims an absence.

What this module deliberately does NOT claim:

  * **Never "all sources were checked".** A completed tick means the
    worker ran, not that every registered source was reached. Per-source
    outcomes are persisted for issuer (company) feeds only; the
    editorial / market-news lane persists no per-source status at all
    today — it is log-only by an explicit earlier decision, because
    `daily_news_source_status`'s own `company_name NOT NULL` column and
    issuer-shaped counters cannot represent an issuer-agnostic source
    without a false value or dropped data (see scripts/
    daily_news_worker.py's docstring). Representing that lane per source
    needs a new table or column, which is out of scope here, so
    EDITORIAL_COVERAGE_RECORDED states the gap instead of papering over
    it.
  * **Never "nothing is recorded" when nothing was read.** See
    `unavailable` below.
  * **Never "this is a quiet period".** The Signals feed a reader sees
    is both lanes combined, so while the editorial lane's per-source
    results are unrecorded, no ingestion state can establish that an
    empty feed means nothing was published rather than that something
    was missed. `confirms_quiet_feed` is therefore False for every state
    today, and `empty_feed_explanation()` says why — see those two.
  * **Never a healthy state from an old success.** `attempt_failed` and
    `stale` both outrank `current`; a recorded failure is never
    downgraded to a timestamp.

Staleness threshold — the approved initial policy, stated exactly:

    stale once the newest recorded completed run is older than
    3 × the ingestion interval configured for THIS process
    (`EDGE_DAILY_NEWS_SCAN_INTERVAL_MINUTES`, default 30 minutes,
    so a 90-minute threshold by default)

Three limits on what that means, each load-bearing:

  * It is a **status policy, not a coverage guarantee and not an SLA**.
    Crossing the threshold means no completed run has been recorded
    recently; staying under it means one has. Neither says anything
    about how much was ingested — see the coverage note above.
  * The interval is **the one configured for the process asking**, which
    is not established to be the worker's. The worker is a separate
    service with its own environment; it reads the same variable name
    from its own environment (scripts/daily_news_worker.py's own tick
    loop), and nothing in this codebase lets a reader process discover
    the interval the worker is actually running. So the threshold is
    derived from local configuration, and this module never claims to
    know the worker's cadence.
  * The multiplier is deliberately a code constant, not a setting, so
    the policy cannot drift per deployment. design/DECISIONS.md's
    Phase F1 entry independently records the same ×3 shape for the Radar
    lane (`3 × effective_interval_minutes`, 60-minute safe default); the
    Daily News threshold here is its own decision with its own default,
    not a value inherited from that one.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.data_access.state_db.daily_news_scan_status_repository import (
    DailyNewsFeedScanStatus,
    DailyNewsWorkerStatus,
)
from src.logic.formatting import fmt_datetime_local

# Same default the Settings field itself uses
# (`daily_news_scan_interval_minutes`), applied only when the configured
# value is missing or non-positive — never a silent guess over a real one.
DEFAULT_INTERVAL_MINUTES = 30

# The approved initial policy for this lane: stale after 3 intervals.
# See this module's docstring for exactly what that does and does not
# claim, including that the interval is the local process's own.
STALE_THRESHOLD_MULTIPLIER = 3

# Whether the editorial / market-news lane persists per-source results.
# False by construction today — scripts/daily_news_worker.py records that
# lane's outcomes to the log only. A constant rather than a literal at
# each call site so the one place this becomes true is the one place that
# has to change, and so no caller can quietly assume otherwise.
EDITORIAL_COVERAGE_RECORDED = False

UNKNOWN_MESSAGE = "No completed ingestion run is recorded."
UNAVAILABLE_MESSAGE = "Ingestion status could not be determined."
INDETERMINATE_COMPLETION_MESSAGE = (
    "A completed ingestion run is recorded, but its timestamp could not be read."
)
COMPLETION_TIME_UNREADABLE_MESSAGE = "The last recorded run's timestamp could not be read."
ATTEMPT_FAILED_MESSAGE = "The latest ingestion attempt reported a failure."
NO_EARLIER_RUN_MESSAGE = "No earlier completed run is on record."
LAST_SUCCESS_ALSO_STALE_MESSAGE = "That run is itself not recent."
PARTIAL_SUFFIX = "Some company feeds could not be reached."
MARKET_NEWS_COVERAGE_UNKNOWN_NOTE = (
    "Per-source results are recorded for company feeds only; market-news source results are not recorded."
)
QUIET_FEED_UNCONFIRMABLE_MESSAGE = (
    "Market-news source results are not recorded, so an empty feed cannot be confirmed as a quiet "
    "period rather than a gap in ingestion."
)
QUIET_FEED_NOT_CURRENT_MESSAGE = (
    "Because the latest ingestion is not confirmed complete, an empty feed may be a gap rather than a "
    "quiet period."
)
QUIET_FEED_CONFIRMED_MESSAGE = (
    "Every recorded source was reached on the latest run, so an empty feed reflects a quiet period "
    "rather than a gap in ingestion."
)


@dataclass(frozen=True)
class DailyNewsIngestionStatus:
    state: str  # "current" | "partial" | "attempt_failed" | "stale" | "unknown" | "unavailable"
    message: str
    # True when a completed run exists but is itself beyond the staleness
    # threshold. Set independently of `state`, so an `attempt_failed`
    # status carries the age of the last success as well as the failure
    # — two separate facts, neither hiding the other.
    last_success_stale: bool = False
    # Carried on the status itself so a renderer cannot forget it: a
    # positive-sounding state means nothing about the lane whose results
    # are not persisted.
    editorial_coverage_recorded: bool = EDITORIAL_COVERAGE_RECORDED

    @property
    def confirms_quiet_feed(self) -> bool:
        """Whether a reader may read an empty feed as "nothing was
        published" rather than "something was missed". Requires BOTH a
        current ingestion state AND per-source results for every lane the
        feed draws on — the Signals feed combines the issuer and
        editorial lanes, and the editorial lane records nothing per
        source today, so this is False for every state until that
        changes. A recent tick alone never establishes coverage."""
        return self.state == "current" and self.editorial_coverage_recorded


def effective_interval_minutes(interval_minutes: int | None) -> int:
    return interval_minutes if interval_minutes and interval_minutes > 0 else DEFAULT_INTERVAL_MINUTES


def stale_threshold_minutes(interval_minutes: int | None) -> int:
    return effective_interval_minutes(interval_minutes) * STALE_THRESHOLD_MULTIPLIER


def completion_evidence(raw: str | None) -> tuple[str, datetime | None]:
    """What the stored completion timestamp actually supports:

        ("absent",    None)     the field is empty — nothing was recorded
        ("malformed", None)     a value is there but cannot be parsed
        ("parsed",    datetime) a usable timestamp

    Absent and malformed are deliberately NOT collapsed. An empty field
    is observed absence and may be reported as such; an unparseable
    value is a recorded run whose time is unknown, and reporting it as
    absence would be a claim the evidence does not support."""
    if raw is None or not str(raw).strip():
        return ("absent", None)
    parsed = _parse_utc(raw)
    return ("parsed", parsed) if parsed is not None else ("malformed", None)


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def failed_feed_count(feed_statuses: dict[str, DailyNewsFeedScanStatus] | None) -> int:
    """How many registered company feeds recorded a failure code on their
    own most recent attempt. Counts only rows that exist — a feed with no
    row at all has no recorded outcome to fail, and inventing one would
    be exactly the false claim this module avoids."""
    if not feed_statuses:
        return 0
    return sum(1 for status in feed_statuses.values() if status.last_failure_code)


def classify_ingestion(
    worker_status: DailyNewsWorkerStatus | None,
    interval_minutes: int | None,
    failed_feeds: int = 0,
    now: datetime | None = None,
    editorial_coverage_recorded: bool = EDITORIAL_COVERAGE_RECORDED,
    read_failed: bool = False,
) -> DailyNewsIngestionStatus:
    """`worker_status`/`failed_feeds` come from the Daily News scan-status
    repository, already read by the caller — never re-read or recomputed
    here.

    `read_failed=True` means the read itself could not be performed, and
    is checked before anything else: nothing was observed, so no claim
    about what is or is not recorded may be made. `None` for
    `worker_status` after a SUCCESSFUL read is the different fact that no
    completed run is recorded.

    Order matters: a recorded failure is examined before any timestamp,
    so an old success can never present as a current one.

    `now` is always the caller's current time, never a cached one — see
    src/ui/components/daily_news_status.py, which caches the persisted
    rows but classifies them afresh on every render, so a cache cannot
    hold a state past its own boundary."""
    now = now or datetime.now(timezone.utc)
    threshold = stale_threshold_minutes(interval_minutes)

    def _status(state: str, message: str, last_success_stale: bool = False) -> DailyNewsIngestionStatus:
        return DailyNewsIngestionStatus(
            state=state,
            message=message,
            last_success_stale=last_success_stale,
            editorial_coverage_recorded=editorial_coverage_recorded,
        )

    if read_failed:
        return _status("unavailable", UNAVAILABLE_MESSAGE)

    if worker_status is None:
        return _status("unknown", UNKNOWN_MESSAGE)

    evidence, last_completed = completion_evidence(worker_status.last_tick_completed_at)
    # Staleness is only ever derived from a timestamp that parsed. A
    # malformed value supports no age claim in either direction.
    last_completed_is_stale = (
        evidence == "parsed" and (now - last_completed).total_seconds() / 60 > threshold
    )

    if worker_status.last_failure_code:
        # The failure code itself is deliberately not shown: this is a
        # reader-facing line, and the page's own long-standing scope
        # keeps technical failure detail in daily_news_admin.py. What a
        # reader needs is that the latest attempt did not finish, plus
        # when ingestion last genuinely completed and whether that is
        # itself behind.
        # The failure code is independently valid evidence and is
        # reported in every branch below. What differs is only what may
        # be said about the last completed run.
        if evidence == "absent":
            return _status("attempt_failed", f"{ATTEMPT_FAILED_MESSAGE} {NO_EARLIER_RUN_MESSAGE}")
        if evidence == "malformed":
            # A run is on record; its time is not readable. Neither an
            # absence nor an age may be claimed, so `last_success_stale`
            # stays False — not because the run is recent, but because
            # staleness is undetermined.
            return _status("attempt_failed", f"{ATTEMPT_FAILED_MESSAGE} {COMPLETION_TIME_UNREADABLE_MESSAGE}")
        parts = [ATTEMPT_FAILED_MESSAGE, f"Last recorded run completed {fmt_datetime_local(last_completed.isoformat())}."]
        if last_completed_is_stale:
            parts.append(LAST_SUCCESS_ALSO_STALE_MESSAGE)
        return _status("attempt_failed", " ".join(parts), last_success_stale=last_completed_is_stale)

    if evidence == "malformed":
        # A completion IS recorded; only its timestamp is unreadable.
        # Indeterminate, never absent.
        return _status("unavailable", INDETERMINATE_COMPLETION_MESSAGE)

    if evidence == "absent":
        # A tick may have started — `last_tick_started_at` can be set —
        # but nothing has ever been recorded as completed, so there is no
        # successful ingestion to report. Started is not finished.
        return _status("unknown", UNKNOWN_MESSAGE)

    when = fmt_datetime_local(last_completed.isoformat())

    if last_completed_is_stale:
        return _status(
            "stale", f"No recent run recorded. The last recorded run completed {when}.",
            last_success_stale=True,
        )

    if failed_feeds:
        return _status("partial", f"Last recorded run completed {when}. {PARTIAL_SUFFIX}")

    # Deliberately about the RUN, not about content: a recorded run is
    # evidence the worker ran, never evidence that anything new arrived
    # and never evidence of complete source coverage.
    return _status("current", f"Last recorded run completed {when}.")


def empty_feed_explanation(status: DailyNewsIngestionStatus) -> str:
    """What an empty feed actually means, given the ingestion status — the
    distinction a reader cannot otherwise make.

    This is a statement about INGESTION only. It is rendered as its own
    separate line and never merged into the page's own empty-state
    message, which is a statement about something different: what matched
    the selected company, tier, and time window. Conflating the two would
    reintroduce exactly the ambiguity this pass exists to remove."""
    if not status.editorial_coverage_recorded:
        # Today's only reachable branch, and the reason confirms_quiet_feed
        # is False for every state: half the combined feed's per-source
        # results are not persisted at all.
        return QUIET_FEED_UNCONFIRMABLE_MESSAGE
    if status.confirms_quiet_feed:
        return QUIET_FEED_CONFIRMED_MESSAGE
    return QUIET_FEED_NOT_CURRENT_MESSAGE
