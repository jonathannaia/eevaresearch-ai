"""Manual, one-shot backfill of materiality_tier for legacy Daily News
issuer stories (Dashboard Recently Updated performance remediation).

NOT a worker, NOT a migration, NOT an ingestion change, and NOT invoked
automatically by anything — an operator runs this deliberately, mirroring
scripts/import_daily_news_json_to_db.py's and
scripts/backfill_company_discovery.py's own "manual, one-shot, bounded,
idempotent" pattern exactly.

Why it exists. A NewsStory persisted before materiality_tier existed
carries None, and src.data_access.daily_news.daily_news_pipeline.
effective_issuer_tier() therefore re-derives its tier at READ time, on
every Dashboard render. Production measurement on the deployed commit
put that at 277 of 342 stories (81.0%) and 1,991.0 ms median for the
recently_updated.news_sources step — essentially the whole Recently
Updated stage. New ingestion has set the tier at construction time since
the column was added (run_discovery), so this cohort is finite and
closed: it cannot grow, and nothing here changes ingestion.

Storing the tier is behaviour-preserving BY CONSTRUCTION. Ingestion
classifies (title, _classification_text(summary), OFFICIAL_COMPANY,
issuer_name_forms(company_name, krx_code)); the read fallback classifies
(headline, _classification_text(excerpt_original), source_class,
issuer_name_forms(company_name, ticker)). excerpt_original IS the
ingested summary and ticker IS krx_code, so the inputs are identical —
this script stores exactly the value every read path already computes
today. _proposed_classification() below reproduces that construction and
tests/test_backfill_daily_news_materiality_tiers.py asserts it agrees
with effective_issuer_tier() on every representative shape, including a
non-vacuous BACKGROUND case.

It deliberately does NOT call effective_issuer_tier() for the value it
writes: that helper returns only the tier and discards the reasons, and
both fields are persisted here.

Invoke as:
    .venv/bin/python -m scripts.backfill_daily_news_materiality_tiers
    .venv/bin/python -m scripts.backfill_daily_news_materiality_tiers --limit 25 --write

Dry run is the default and writes nothing. --write is required to
persist, and --limit is always bounded (an omitted --limit means
DEFAULT_LIMIT, never "the whole store").

Safe to run more than once. Eligibility is "the stored materiality_tier
is None", re-evaluated per record per run, so an already-tiered story is
skipped forever and a rerun after a complete pass is a no-op. A stored
tier is never overwritten, and no read path is modified.

Only prints fixed-name aggregate counters — never story content, a
headline, excerpt, URL, company, ticker, record id, database identity,
credential, exception message, or traceback.
"""
from __future__ import annotations

import argparse
import subprocess
import time
from dataclasses import dataclass, field, replace

from src.config.settings import Settings, get_settings
from src.data_access.daily_news import daily_news_backend
from src.data_access.daily_news.daily_news_pipeline import _classification_text
from src.data_access.daily_news.materiality_classification import (
    classify_issuer_story,
    issuer_name_forms,
)
from src.models.daily_news_models import NewsMaterialityTier, NewsStory

# Conservative by design: an omitted --limit must never mean "rewrite the
# whole production store". The measured legacy cohort is ~277 stories, so
# this default covers a meaningful slice while still requiring a
# deliberate, larger --limit to complete the job.
DEFAULT_LIMIT = 25

# Stop before grinding through an unexpectedly failing population: if the
# classifier or the repository is failing systematically, the run should
# end while the aggregate counters still describe something useful.
DEFAULT_FAILURE_BUDGET = 5

SUPPORTED_BACKENDS = ("sqlite", "postgres")


class MalformedClassificationError(ValueError):
    """The classifier returned something that cannot be stored.

    Its own class name is all that ever reaches the output, so this type
    exists to make a malformed result distinguishable in the aggregate
    from an ordinary classifier exception — without carrying any of the
    offending value."""


@dataclass
class BackfillSummary:
    """Aggregate-only outcome of one run. Every field is an integer
    count, a bool, or a duration — by construction there is nowhere to
    put story content, an identifier, or an exception message.

    The counters are split into two populations that must never be
    conflated:

      DISCOVERY (the whole scanned store)
        candidates_seen = skipped_already_tiered + skipped_no_sources
                          + eligible_total
      PROCESSING (only what --limit and the failure budget allowed)
        eligible_processed <= eligible_total

    eligible_remaining is exact only when the scan ran to completion. A
    failure-budget stop ends the scan early, so the remaining count is
    genuinely unknown and is reported as "unknown" rather than guessed —
    an early stop must never hide inside an apparently whole-store
    aggregate.

    Failures are tracked in two buckets because they settle different
    invariants: a classification failure never reaches a write, so it
    consumes a processed candidate without producing a proposal; a write
    failure consumes one after its proposal was already counted."""

    mode: str
    limit: int
    candidates_seen: int = 0
    skipped_already_tiered: int = 0
    skipped_no_sources: int = 0
    eligible_total: int = 0
    eligible_processed: int = 0
    proposed_high_signal: int = 0
    proposed_watchlist: int = 0
    proposed_background: int = 0
    written: int = 0
    skipped_raced: int = 0
    skipped_missing: int = 0
    classification_failures: int = 0
    write_failures: int = 0
    failures_by_exception_class: dict = field(default_factory=dict)
    elapsed_ms: float = 0.0
    scan_complete: bool = True
    limit_reached: bool = False
    stopped_on_failure_budget: bool = False

    @property
    def failures(self) -> int:
        return self.classification_failures + self.write_failures

    @property
    def proposed_total(self) -> int:
        return self.proposed_high_signal + self.proposed_watchlist + self.proposed_background

    @property
    def eligible_remaining(self) -> int | None:
        """None means "unknown" — the scan stopped before discovering the
        whole store, so no exact remaining count can honestly be given."""
        if not self.scan_complete:
            return None
        return self.eligible_total - self.eligible_processed

    def record_failure(self, exc: BaseException, *, during: str) -> None:
        """Counts an exception by CLASS NAME only — never its message,
        arguments, or traceback, the same discipline backend_factory.py
        already applies to connection errors. `during` is one of
        "classification" | "write" and only selects a counter."""
        if during == "classification":
            self.classification_failures += 1
        else:
            self.write_failures += 1
        name = type(exc).__name__
        self.failures_by_exception_class[name] = self.failures_by_exception_class.get(name, 0) + 1

    def _rendered_failures(self) -> str:
        return ",".join(f"{name}={count}" for name, count in sorted(self.failures_by_exception_class.items()))

    def render(self, batch: int, git_commit: str) -> str:
        remaining = self.eligible_remaining
        rendered_remaining = "unknown" if remaining is None else str(remaining)
        return (
            f'event="materiality_backfill" mode="{self.mode}" '
            f'git_commit="{git_commit}" batch={batch} limit={self.limit} '
            f"candidates_seen={self.candidates_seen} "
            f"skipped_already_tiered={self.skipped_already_tiered} "
            f"skipped_no_sources={self.skipped_no_sources} "
            f"eligible_total={self.eligible_total} "
            f"eligible_processed={self.eligible_processed} "
            f'eligible_remaining="{rendered_remaining}" '
            f"proposed_high_signal={self.proposed_high_signal} "
            f"proposed_watchlist={self.proposed_watchlist} "
            f"proposed_background={self.proposed_background} "
            f"written={self.written} "
            f"skipped_raced={self.skipped_raced} "
            f"skipped_missing={self.skipped_missing} "
            f"classification_failures={self.classification_failures} "
            f"write_failures={self.write_failures} "
            f'failures_by_exception_class="{self._rendered_failures()}" '
            f'scan_complete={str(self.scan_complete).lower()} '
            f'limit_reached={str(self.limit_reached).lower()} '
            f'stopped_on_failure_budget={str(self.stopped_on_failure_budget).lower()} '
            f"elapsed_ms={self.elapsed_ms:.1f}"
        )


def _proposed_classification(story: NewsStory) -> tuple[NewsMaterialityTier, tuple[str, ...]]:
    """The classification the READ FALLBACK would perform for this story,
    returning its reasons as well as its tier.

    Deliberately reproduces daily_news_pipeline.effective_issuer_tier()'s
    own input construction rather than calling it, because that helper
    discards the reasons (`tier, _ = classify_issuer_story(...)`) and
    both fields are persisted here. The read path itself is untouched;
    tests assert this function's tier equals effective_issuer_tier() for
    every representative story shape, so the two cannot drift.

    Callers must handle the no-sources case before calling: the fallback
    answers WATCHLIST there without classifying at all, and such a story
    is skipped rather than written (see run_backfill)."""
    source_ref = story.sources[0]
    return classify_issuer_story(
        story.headline,
        _classification_text(source_ref.excerpt_original),
        source_ref.source_class,
        issuer_names=issuer_name_forms(story.company_name, story.ticker),
    )


def _validated_classification(story: NewsStory) -> tuple[NewsMaterialityTier, tuple[str, ...]]:
    """_proposed_classification plus a storage-compatibility check.

    The repositories persist the tier as `tier.value` and the reasons as
    `json.dumps(list(reasons))`, so anything that is not a real
    NewsMaterialityTier or not a tuple of str would either raise deep
    inside the write or silently store something unreadable. Checking
    here turns that into an ordinary, safely-counted per-story failure
    that writes nothing and leaves the record eligible.

    The classifier itself is not modified and its behaviour is not
    altered — this only inspects what it returned."""
    tier, reasons = _proposed_classification(story)
    if not isinstance(tier, NewsMaterialityTier):
        raise MalformedClassificationError("classifier returned a non-tier value")
    if not isinstance(reasons, tuple) or not all(isinstance(reason, str) for reason in reasons):
        raise MalformedClassificationError("classifier returned non-string reasons")
    return tier, reasons


def _count_proposal(summary: BackfillSummary, tier: NewsMaterialityTier) -> None:
    if tier == NewsMaterialityTier.HIGH_SIGNAL:
        summary.proposed_high_signal += 1
    elif tier == NewsMaterialityTier.BACKGROUND:
        summary.proposed_background += 1
    else:
        summary.proposed_watchlist += 1


def _git_commit() -> str:
    """The local commit this run classified from, so a later audit can
    tell WHICH classifier revision produced a stored tier — no
    provenance column exists today (see the design plan). Never fails
    the run: an unavailable commit is reported as such."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=False,
        )
    except Exception:  # noqa: BLE001 — provenance is best-effort, never fatal
        return "unavailable"
    resolved = completed.stdout.strip()
    return resolved if completed.returncode == 0 and resolved else "unavailable"


def run_backfill(
    repository, *, write: bool = False, limit: int = DEFAULT_LIMIT,
    failure_budget: int = DEFAULT_FAILURE_BUDGET,
) -> BackfillSummary:
    """One bounded pass over the store.

    Eligibility is "the stored materiality_tier is None", evaluated per
    record per run, which is what makes a rerun a no-op rather than a
    second write. A non-None tier is never overwritten — including a
    stored BACKGROUND whose content would classify differently today.

    `limit` bounds how many ELIGIBLE stories one run PROCESSES, never how
    many it scans: the loop keeps counting after the limit is reached so
    eligible_total still describes the whole store, and limit_reached
    says so explicitly. A failure-budget stop is different — it ends the
    scan, so scan_complete goes false and eligible_remaining is reported
    as "unknown" rather than guessed.

    Every write re-reads the stored record first: a story another
    process has tiered in the meantime is counted as skipped_raced and
    left alone, and update_story's own optimistic `WHERE version = ...`
    closes the remaining window (conflict and not_found are safe skips,
    never blind retries). Per-story transactions come from update_story
    itself, so a mid-run failure leaves earlier stories committed and
    later ones eligible for a later, manually approved rerun."""
    summary = BackfillSummary(mode="write" if write else "dry_run", limit=limit)
    started_at = time.monotonic()
    try:
        for story in repository.load_stories().values():
            summary.candidates_seen += 1
            if story is None or story.materiality_tier is not None:
                # A None value can only come from a store that lost the
                # row between the id read and the row read; treat it as
                # already-accounted rather than eligible.
                summary.skipped_already_tiered += 1
                continue
            if not story.sources:
                summary.skipped_no_sources += 1
                continue

            summary.eligible_total += 1
            if summary.eligible_processed >= limit:
                # Keep SCANNING so eligible_total still describes the
                # whole store; only processing is bounded.
                summary.limit_reached = True
                continue
            summary.eligible_processed += 1

            try:
                tier, reasons = _validated_classification(story)
            except Exception as exc:  # noqa: BLE001 — aggregate-counted, record left eligible
                summary.record_failure(exc, during="classification")
                if summary.failures >= failure_budget:
                    summary.stopped_on_failure_budget = True
                    summary.scan_complete = False
                    break
                continue

            _count_proposal(summary, tier)
            if not write:
                continue

            try:
                _write_tier(repository, story, tier, reasons, summary)
            except Exception as exc:  # noqa: BLE001 — aggregate-counted, record left eligible
                summary.record_failure(exc, during="write")
                if summary.failures >= failure_budget:
                    summary.stopped_on_failure_budget = True
                    summary.scan_complete = False
                    break
    finally:
        summary.elapsed_ms = (time.monotonic() - started_at) * 1000
    return summary


def _write_tier(repository, story, tier, reasons, summary: BackfillSummary) -> None:
    """Re-reads, then writes under the existing optimistic lock.

    Outcome mapping, against the real repositories (see
    state_db/daily_news_repository.update_story and its Postgres twin —
    both RETURN an UpdateOutcome, never raise, for these cases):

      "updated"   -> written
      "conflict"  -> skipped_raced   (version moved; never retried)
      "not_found" -> skipped_missing (row gone since the scan)

    plus two pre-write skips this function decides itself: a row that
    has vanished (get_story -> None) is skipped_missing, and one another
    process has tiered in the meantime is skipped_raced. Every path
    accounts for the candidate exactly once."""
    current = repository.get_story(story.id)
    if current is None:
        summary.skipped_missing += 1
        return
    if current.materiality_tier is not None:
        # Another process tiered it between the load and this write.
        summary.skipped_raced += 1
        return

    # `replace` on the freshly re-read row, so every other column
    # update_story rewrites (company_name … status) is written back
    # byte-identical and only materiality_tier/materiality_reasons
    # actually change. Verified: replace() preserves all 14 fields.
    expected_version = repository.get_story_version(story.id)
    outcome = repository.update_story(
        replace(current, materiality_tier=tier, materiality_reasons=reasons),
        expected_version,
    )
    if outcome.status == "updated":
        summary.written += 1
    elif outcome.status == "not_found":
        summary.skipped_missing += 1
    else:
        # "conflict" — another writer won the optimistic lock. Never retried.
        summary.skipped_raced += 1


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="backfill_daily_news_materiality_tiers",
        description=(
            "Manual, one-shot backfill of materiality_tier for legacy Daily News issuer "
            "stories. Dry run by default; --write is required to persist anything."
        ),
    )
    parser.add_argument(
        "--write", action="store_true",
        help="Persist tiers. Omitted, the run is a dry run and writes nothing.",
    )
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT,
        help=(
            f"Maximum ELIGIBLE stories to process in this run (default: {DEFAULT_LIMIT}). "
            "An omitted --limit never means the whole store."
        ),
    )
    parser.add_argument(
        "--failure-budget", type=int, default=DEFAULT_FAILURE_BUDGET,
        help=f"Stop the run after this many failures (default: {DEFAULT_FAILURE_BUDGET}).",
    )
    return parser.parse_args(argv)


def _normalized_backend(settings: Settings) -> str:
    return (settings.db_backend or "json").strip().lower()


def main(argv=None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    backend = _normalized_backend(settings)
    if backend not in SUPPORTED_BACKENDS:
        # Same no-op exit as scripts/import_daily_news_json_to_db.py: with
        # no durable backend configured there is nothing to back-fill.
        print(
            'EDGE_DB_BACKEND is not "sqlite" or "postgres" (or is unset) — there is no '
            "durable store to back-fill. Exiting without changing anything."
        )
        return 0

    summary = run_backfill(
        daily_news_backend.get_daily_news_repository(settings),
        write=args.write, limit=args.limit, failure_budget=args.failure_budget,
    )
    print(summary.render(batch=1, git_commit=_git_commit()))
    if summary.stopped_on_failure_budget:
        print('event="materiality_backfill" note="stopped_on_failure_budget"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
