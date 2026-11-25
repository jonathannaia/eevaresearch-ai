"""Re-classification of already-persisted editorial stories inside the
live display window.

WHY THIS EXISTS. editorial_story_repository.upsert_new_stories() skips
any id that already exists, and editorial_pipeline.py's own comment is
explicit that it "never reclassifies an already-persisted story". That
is the right default — a published signal should not silently change
under a reader — but it has a cost that was measured three times in
one review:

  - Two Nintendo/TSMC rows sat in the window as High Signals with no
    attributed company, after the tracked-company requirement had
    already shipped to fix exactly that.
  - An export-controls row kept an NVIDIA chip after the regulatory-
    subject veto had shipped to remove it.
  - Eight copies of one Nvidia governance story sat in the window
    because every one of them was ingested in the hours BEFORE the
    same-story dedupe deployed.

In each case the fix was live and the stored rows were untouchable, so
the feed stayed wrong for a week while correct code ran beside it.

WHAT THIS DOES NOT DO. It never raises a tier on its own authority
beyond what the current classifier says, never invents a company, and
never deletes a row. A suppressed duplicate is marked, not removed, so
the archive can still show it was published. Every change is paired
with a revision record (old tier, old companies, reason, timestamp) —
see account_access_events for the same append-only discipline. Nothing
here writes; planning is pure and the caller decides.

WINDOW. Only stories inside _RECLASSIFY_WINDOW_DAYS are considered.
Outside it a story is no longer displayed, so rewriting it would churn
history for no visible benefit and would rewrite rows whose original
classification is the only honest record of what was published.

DEDUPE IS PART OF THE PASS, NOT A SEPARATE STEP. Re-classifying a
window that predates the dedupe would otherwise promote every member
of a duplicate cluster at once: measured on the live window, Gate G
alone would have raised eight copies of one Nvidia story to High
Signal simultaneously. The pass therefore re-runs same-story and
same-event detection across the window and suppresses later members,
first-seen-wins, exactly as the ingest path does.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.data_access.daily_news import same_story_dedup
from src.data_access.daily_news.editorial_admission import assess_admission
from src.data_access.daily_news.editorial_matching import matched_companies_and_themes
from src.data_access.daily_news.materiality_classification import (
    cap_without_tracked_company,
    classify_editorial_story,
    flag_vague_headline,
)
from src.data_access.daily_news.source_registry import SourceCategory
from src.models.daily_news_models import NewsMaterialityTier

#: Matches the display window the Daily News feed itself uses.
RECLASSIFY_WINDOW_DAYS = 7

#: Written into the revision record so the archive can tell a
#: correction apart from an original classification.
REVISION_SOURCE = "window_reclassification"

#: editorial_pipeline._MAX_EXCERPT_CHARS — the length a stored excerpt
#: was truncated to, from a feed summary that is not retained.
#:
#: WHY LENGTH ALONE CANNOT SETTLE THIS. _extractive_excerpt() trims
#: back to a sentence or word boundary under the cap, so a truncated
#: excerpt lands anywhere below it, not at it. Measured on the live
#: window, the 40 proposed High Signal demotions had excerpt lengths
#: spread across 300-399 (22 of them), 250-299 (8) and below (10) —
#: a distribution that cannot be separated into "truncated" and
#: "complete" by length. An earlier version of this guard tested for
#: >= 400 and therefore excluded nothing at all.
TRUNCATED_EXCERPT_CHARS = 400

#: Comfortably below any truncation boundary: an excerpt this short is
#: the whole cleaned summary, so re-classifying it compares like with
#: like.
COMPLETE_EXCERPT_CHARS = 250

SUPPRESSED_TIER = NewsMaterialityTier.BACKGROUND

#: Ranked so the pass can tell a demotion from a promotion. None is a
#: row persisted before the materiality column existed; anything the
#: classifier now says about it is new information, never a weakening.
_TIER_ORDER: dict = {
    None: -1,
    NewsMaterialityTier.BACKGROUND: 0,
    NewsMaterialityTier.WATCHLIST: 1,
    NewsMaterialityTier.HIGH_SIGNAL: 2,
}


@dataclass(frozen=True)
class ReclassificationChange:
    """One story's correction. `new_*` is what the current code says;
    `old_*` is what is stored. Only emitted when something actually
    differs — an unchanged story produces no record at all, so the
    revision table stays a log of corrections rather than a second
    copy of the feed."""
    story_id: str
    headline: str
    published_at: str
    old_tier: NewsMaterialityTier | None
    new_tier: NewsMaterialityTier
    old_companies: tuple[str, ...]
    new_companies: tuple[str, ...]
    new_reasons: tuple[str, ...]
    reason: str
    #: Set only on a suppressed duplicate: the survivor it is linked
    #: to, so the card can show it as related coverage. None on every
    #: other kind of change, which leaves the column untouched.
    duplicate_of_story_id: str | None = None

    @property
    def tier_changed(self) -> bool:
        return self.old_tier is not self.new_tier

    @property
    def companies_changed(self) -> bool:
        return self.old_companies != self.new_companies


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def stories_in_window(stories, now: datetime) -> list:
    """The display window, newest first. Newest-first ordering is what
    makes the dedupe pass below first-seen-wins by PUBLICATION rather
    than by whatever order the store happened to return."""
    cutoff = now - timedelta(days=RECLASSIFY_WINDOW_DAYS)
    inside = []
    for story in stories:
        published = _parse(story.published_at)
        if published is None or published < cutoff:
            continue
        inside.append(story)
    inside.sort(key=lambda s: _parse(s.published_at) or datetime.min.replace(tzinfo=timezone.utc))
    return inside


def _reclassify_one(story) -> tuple[NewsMaterialityTier, tuple[str, ...], tuple[str, ...]]:
    """(tier, reasons, attributed_companies) for one stored story, via
    exactly the calls editorial_pipeline.py makes in the same order.

    The input is the stored headline and excerpt, not the original feed
    summary, which is not retained. The excerpt is a truncation of that
    summary, so this can only ever see LESS text than ingest did — it
    can correct a story the current rules reject, and cannot invent
    evidence that was never stored. That asymmetry is deliberate and is
    why the pass is safe to run repeatedly."""
    companies, themes = matched_companies_and_themes(story.headline, story.excerpt)
    tier, reasons = classify_editorial_story(
        story.headline, story.excerpt, SourceCategory.INDEPENDENT_NEWS,
    )
    admission = assess_admission(story.headline, story.excerpt, companies, themes, reasons)
    attributed = admission.identified_companies if admission.admitted else ()
    tier, reasons = cap_without_tracked_company(tier, reasons, attributed)
    reasons = flag_vague_headline(story.headline, reasons)
    return tier, reasons, attributed


def plan_reclassification(stories, now: datetime) -> tuple[ReclassificationChange, ...]:
    """Pure. Returns one change per story whose tier or attribution the
    current code disagrees with, plus one per duplicate the current
    dedupe would have suppressed at ingest. Writes nothing."""
    window = stories_in_window(stories, now)
    changes: list[ReclassificationChange] = []

    seen_headlines: list[tuple[str, str, str, str]] = []
    seen_events: list[tuple[str, str, str, str, tuple[str, ...], str | None]] = []

    for story in window:
        tier, reasons, attributed = _reclassify_one(story)

        duplicate_of = same_story_dedup.find_same_story_duplicate(
            seen_headlines, headline=story.headline, published_at=story.published_at,
        )
        if duplicate_of is None:
            duplicate_of = same_story_dedup.find_same_event_duplicate(
                seen_events,
                headline=story.headline,
                published_at=story.published_at,
                companies=attributed,
                category=same_story_dedup.event_category(reasons),
            )
            if duplicate_of is not None:
                duplicate_reason = f"same_event_duplicate_of:{duplicate_of[1]}"
            else:
                duplicate_reason = ""
        else:
            duplicate_reason = f"same_story_duplicate_of:{duplicate_of[1]}"

        if duplicate_of is not None:
            # Suppressed, not deleted: the row stays so the archive can
            # still show it was published, and its tier drops out of
            # the visible feed.
            if (
                story.materiality_tier is not SUPPRESSED_TIER
                or story.duplicate_of_story_id != duplicate_of[0]
            ):
                changes.append(ReclassificationChange(
                    story_id=story.id, headline=story.headline, published_at=story.published_at,
                    old_tier=story.materiality_tier, new_tier=SUPPRESSED_TIER,
                    old_companies=story.matched_companies, new_companies=story.matched_companies,
                    new_reasons=(duplicate_reason,) + reasons, reason=duplicate_reason,
                    duplicate_of_story_id=duplicate_of[0],
                ))
            continue

        seen_headlines.append((story.id, story.publisher, story.headline, story.published_at))
        seen_events.append((
            story.id, story.publisher, story.headline, story.published_at,
            attributed, same_story_dedup.event_category(reasons),
        ))

        if tier is story.materiality_tier and attributed == story.matched_companies:
            continue

        # DIRECTION MATTERS when the source text may be truncated.
        #
        # A PROMOTION is always safe. The pass sees at most the text
        # ingest saw, so a gate that fires on a truncated excerpt would
        # have fired on the full summary too — less evidence reaching
        # the same conclusion is stronger evidence, not weaker.
        #
        # A DEMOTION is not. It rests on evidence being ABSENT, and
        # absence in a truncated excerpt says nothing about the summary
        # it was cut from. Applying one would silently delete a signal
        # on the strength of text we no longer hold.
        #
        # So a demotion, or the removal of an attributed company, is
        # applied only when the excerpt is short enough to be certainly
        # complete. Everything else is left exactly as it is: the pass
        # declines rather than guesses, and the story ages out of the
        # window on its original classification — the same outcome as
        # before this pass existed, which is the honest floor.
        #
        # Duplicate suppression is decided above and is exempt
        # throughout: it compares headlines, companies and publication
        # times, never the excerpt.
        weakening = _TIER_ORDER[tier] < _TIER_ORDER[story.materiality_tier] or (
            set(attributed) < set(story.matched_companies)
        )
        if weakening and len(story.excerpt or "") > COMPLETE_EXCERPT_CHARS:
            continue
        if tier is not story.materiality_tier:
            old = story.materiality_tier.value if story.materiality_tier else "none"
            reason = f"tier:{old}->{tier.value}"
        else:
            reason = "companies"
        changes.append(ReclassificationChange(
            story_id=story.id, headline=story.headline, published_at=story.published_at,
            old_tier=story.materiality_tier, new_tier=tier,
            old_companies=story.matched_companies, new_companies=attributed,
            new_reasons=reasons, reason=reason,
        ))
    return tuple(changes)
