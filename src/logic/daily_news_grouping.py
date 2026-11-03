"""Cross-lane presentation grouping for the Signals feed — pure,
Streamlit-free, and deliberately conservative.

The problem it solves: a company announcement (Lane A, the issuer's own
release) and an editorial write-up of the same announcement (Lane B,
independent coverage) are two separate records in two separate stores,
and the feed showed them as two separate cards. On the overlap pairs the
Cohort 1 design flagged — Yaskawa's newsroom vs The Robot Report,
Firefly's newsroom vs SpaceNews — a reader saw one event twice.

What this module does, and what it refuses to do:

  * **Presentation only.** It takes two already-selected display lists
    and returns display slots. It never reads or writes a store, never
    re-runs either lane's dedup, never touches admission, materiality or
    publication, and never mutates an input. Both records survive
    untouched; both publishers and both source URLs stay on the card.
  * **Issuer/editorial pairs only.** Grouping two editorial items from
    different publishers is out of scope: that is a separate decision
    about whether independent reports of one event are one item or two,
    and nothing here implies an answer. An issuer story therefore takes
    at most ONE editorial member, so a group can never transitively
    group two publishers with each other.
  * **No claim about corroboration.** A group means "these records name
    the same announcement", not that the editorial item verifies the
    issuer's claim. The renderer must not label it corroboration.
  * **No claim of semantic matching.** Every condition below is a
    lexical or temporal test over fields already stored. Nothing here
    understands the two texts; the exclusions are explicit lexicons and
    bounded budgets, deliberately tuned to refuse rather than to catch
    everything. The residual risks are listed at the end of this
    docstring, and they are real: this module will miss rewordings it
    should group, and it can still group two texts whose disagreement is
    expressed in words no lexicon here contains.

--- Matching contract ---

All four conditions must hold. Any one failing leaves both items as
separate slots; uncertainty always resolves to "separate".

1. **Company attribution compatible.** The issuer story's `company_name`
   must appear in the editorial story's `matched_companies` — the
   admission-vetted subset the model itself designates as the only
   source for visible tags and filtering. `identified_companies` is
   deliberately NOT used: the model's own docstring forbids it from
   driving display. An editorial item with no matched companies (theme-
   only coverage) can never group, which is what makes "missing
   attribution stays separate" structural rather than a check.

2. **Event evidence compatible — asymmetric token containment.** Every
   significant token of the issuer headline must appear in the editorial
   headline, after the existing `dedup.normalize_title_unicode()`
   normalizer and a small explicit stop-word list. A matching normalized
   headline alone is not required and not sufficient on its own:
   containment lets the editorial item add words (a publisher prefix, a
   venue, context) while refusing anything that DROPS or REPLACES a word
   the issuer used. That asymmetry is what keeps "Yaskawa Launches
   MOTOMAN-HC12" apart from "Yaskawa Delays MOTOMAN-HC12" — `launches`
   is absent from the second — and apart from an analysis piece that
   merely names the same company.

3. **No conflicting figures.** Numeric figures (with their magnitude or
   unit word, so "$515 million" and "515 million" agree) are extracted
   from both headlines and both excerpts. If both sides carry figures
   and neither set is contained in the other, the items are treated as
   describing materially different coverage and stay separate. One side
   adding a figure the other omits is not a conflict; a different amount
   is. No figure is ever inferred, and no article body is fetched —
   only the headline and the stored extractive excerpt are read.

   This check is a VETO ONLY. The absence of a numeric conflict is never
   evidence of agreement: two items with no figures at all still have to
   satisfy every other condition, and nothing here treats "no numbers
   disagreed" as a reason to group.

3a. **No status marker on one side only.** An explicit, reviewed lexicon
   of event-status and correction words (correction, retraction, denies,
   cancels, delays, halts, recalls, …). If either side carries a marker
   the other does not, the two are not describing the same event state
   and stay separate. Token containment alone cannot catch this: a
   marker that REPLACES a word is caught by containment, but one that is
   merely APPENDED ("… — Correction") is not, which is precisely the
   dangerous direction.

3b. **No opposed polarity term.** A small lexicon of opposed pairs
   (beats/misses, raises/cuts, approved/rejected, completes/delays,
   wins/loses, expands/closes). If one side carries a term and the other
   carries its opposite, the coverage disagrees qualitatively even when
   every number matches, and the items stay separate.

3c. **No combined event.** If the editorial headline adds a coordinating
   conjunction the issuer headline does not have AND adds at least
   COMBINED_EVENT_MIN_EXTRA_TOKENS significant tokens, it is treated as
   describing the issuer's event plus something else, and is not grouped
   behind the release. A bounded extra-token budget
   (MAX_EXTRA_EDITORIAL_TOKENS) applies independently: beyond it, the
   headlines are too far apart for containment to be meaningful
   evidence, so the match is treated as insufficient rather than good.

4. **Bounded publication-time relationship.** The two `published_at`
   values must be within GROUP_WINDOW_HOURS of each other, in either
   direction. Pickup normally follows a release, but an embargoed
   preview can precede it, so the window is symmetric.

Tier is not a condition here because the caller applies this function
within one tier bucket (see group_feed_slots' own docstring), which is
what guarantees a grouped card can never surface a member the active
tier filter excluded.

--- Residual risk, stated rather than claimed away ---

What this module can still get wrong, by construction:

  * A disagreement phrased outside the polarity lexicon ("ahead of
    guidance" vs "short of guidance" if neither term is listed) is
    invisible to it, and such a pair can group when every number
    matches. No numeric agreement is ever read as semantic agreement,
    but nor can a lexicon close this.
  * A second event joined without a coordinating conjunction ("Yaskawa
    Launches MOTOMAN-HC12, Service Center Opens") is not caught by the
    combined-event guard, which keys on an added conjunction.
  * "after", "as" and "amid" are deliberately NOT treated as combining
    conjunctions, because ordinary pickup uses them ("… after Q3"), so a
    genuinely combined headline using one of them is missed.
  * Rewording is the common miss, not the rare one: any pickup that
    drops or replaces an issuer token stays separate, which is most real
    editorial headlines.

--- Ranking contract ---

Grouping moves no item between tiers and creates no new rank position.

  * A group is only ever formed from members **already in the same tier
    bucket**, so nothing is promoted or demoted. Two sources naming one
    event confer no materiality: a Background issuer release grouped
    with a Background editorial item is still Background, and a
    High Signal issuer release keeps the tier its own materiality
    reasons earned.
  * A group occupies the slot its **newest member already held**, under
    the feed's existing newest-first ordering. The other member's slot
    is removed, which is the only ordering change: no item moves up past
    an item that was above it. Picking the newest member (rather than
    the issuer's own timestamp) keeps the feed's reverse-chronological
    invariant exact; the card still presents the issuer release as the
    primary reference, and each member's own timestamp stays visible.
  * Item counts are unchanged. Callers that count material — the
    sidebar badge, the Dashboard tile, the section headers — keep
    counting items, because grouping changes how many cards are drawn,
    not how much material exists. Slot counts are available separately
    for anything that needs to describe the rendered list.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.data_access.daily_news.dedup import normalize_title_unicode
from src.models.daily_news_models import EditorialStory, NewsStory

ISSUER_KIND = "issuer"
EDITORIAL_KIND = "editorial"

# Symmetric window between the two publication timestamps. Chosen to sit
# inside both lanes' own freshness windows (editorial 72h, issuer 7
# days) while covering same-day and next-day pickup, which is where
# real editorial write-ups of a release land. A policy choice, not a
# derived constant.
GROUP_WINDOW_HOURS = 48

# Event-status and correction markers. Explicit and reviewed: each entry
# makes grouping STRICTLY LESS likely, never more, and a marker present
# on one side only is treated as a different event state. Normalized
# single tokens, matched against the same token set containment uses.
_STATUS_MARKERS = frozenset({
    "correction", "corrections", "corrected", "corrects",
    "retraction", "retracts", "retracted",
    "denial", "denies", "denied", "deny",
    "cancellation", "cancels", "cancelled", "canceled", "cancel",
    "delay", "delays", "delayed",
    "postpones", "postponed", "postponement",
    "halts", "halted", "halt", "suspends", "suspended", "suspension",
    "scraps", "scrapped", "pauses", "paused",
    "withdraws", "withdrawn", "withdrawal",
    "terminates", "terminated", "termination",
    "recalls", "recalled", "recall",
    "disputes", "disputed", "rebuts", "refutes", "walkback",
    "update", "updated", "revised", "revises", "revision",
})

# Opposed pairs. If one side carries a term from a half and the other
# carries a term from the OPPOSITE half, the coverage disagrees
# qualitatively even when every figure matches. Kept as two halves so a
# term is never opposed to a synonym in its own half.
_POLARITY_PAIRS: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (frozenset({"beats", "beat", "exceeds", "exceeded", "tops", "topped"}),
     frozenset({"misses", "missed", "lags", "lagged", "undershoots"})),
    (frozenset({"raises", "raised", "lifts", "lifted", "increases", "increased"}),
     frozenset({"cuts", "cut", "lowers", "lowered", "reduces", "reduced", "trims", "trimmed"})),
    (frozenset({"approved", "approves", "cleared", "clears"}),
     frozenset({"rejected", "rejects", "blocked", "blocks"})),
    (frozenset({"completes", "completed", "finishes", "finished"}),
     frozenset({"delays", "delayed", "postpones", "postponed"})),
    (frozenset({"wins", "won", "awarded", "secures", "secured"}),
     frozenset({"loses", "lost", "forfeits", "forfeited"})),
    (frozenset({"expands", "expanded", "opens", "opened"}),
     frozenset({"closes", "closed", "shutters", "shuttered", "exits", "exited"})),
)

# Coordinating conjunctions that join two clauses. "after", "as" and
# "amid" are deliberately absent — ordinary pickup uses them, and
# treating them as combiners would reject legitimate matches.
_COMBINING_CONJUNCTIONS = frozenset({"and", "plus", "also", "while", "alongside"})

# A pickup headline may add a publisher prefix, a venue, or a little
# context. Beyond this budget the two headlines are too far apart for
# containment to be meaningful evidence, so the match is treated as
# insufficient. A policy choice, deliberately small.
MAX_EXTRA_EDITORIAL_TOKENS = 6
# With an added coordinating conjunction, even a small addition reads as
# a second clause.
COMBINED_EVENT_MIN_EXTRA_TOKENS = 2

# Function words only — never a word that could carry the event itself.
# Deliberately short and explicit: every addition widens what counts as
# "the same announcement", so this list is a reviewed decision rather
# than a general-purpose stop-word corpus.
_STOP_WORDS = frozenset({
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "is", "its",
    "of", "on", "or", "the", "to", "with",
})

# A figure plus the magnitude/unit word immediately after it, so
# "$515 million" and "515 million" normalize to the same token while
# "515" and "550" stay different.
_FIGURE_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*(million|billion|trillion|bn|m|k|%|percent|kg|tons?|yen|usd|eur)?",
    re.IGNORECASE,
)
_MAGNITUDE_ALIASES = {"bn": "billion", "m": "million", "percent": "%"}


@dataclass(frozen=True)
class FeedSlot:
    """One slot in the rendered feed. `members` is (kind, item) pairs in
    presentation order, primary first. A single-item slot is exactly
    what the feed showed before this module existed."""

    members: tuple[tuple[str, NewsStory | EditorialStory], ...]

    @property
    def primary(self) -> tuple[str, NewsStory | EditorialStory]:
        return self.members[0]

    @property
    def is_group(self) -> bool:
        return len(self.members) > 1

    @property
    def secondary(self) -> tuple[tuple[str, NewsStory | EditorialStory], ...]:
        return self.members[1:]


def published_at_of(kind: str, item: NewsStory | EditorialStory) -> str:
    """The same per-type timestamp field the page's own `_feed_sort_key`
    and `_item_published_at` read — reused rather than re-derived so a
    slot can never sort by a different clock than an item."""
    if kind == ISSUER_KIND:
        return item.sources[0].published_at if item.sources else ""
    return item.published_at or ""


def _parse(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def significant_tokens(headline: str) -> frozenset[str]:
    return frozenset(
        token for token in normalize_title_unicode(headline or "").split() if token not in _STOP_WORDS
    )


def figures(*texts: str | None) -> frozenset[str]:
    """Normalized numeric figures found in the given texts. Pure
    extraction — nothing is inferred, and no text beyond what the caller
    already holds is read."""
    found: set[str] = set()
    for text in texts:
        if not text:
            continue
        for raw_value, raw_magnitude in _FIGURE_RE.findall(text):
            value = raw_value.replace(",", "")
            magnitude = (raw_magnitude or "").lower()
            magnitude = _MAGNITUDE_ALIASES.get(magnitude, magnitude)
            found.add(f"{value} {magnitude}".strip())
    return frozenset(found)


def _all_tokens(*texts: str | None) -> frozenset[str]:
    """Every normalized token across the given texts, stop words kept —
    the status and polarity lexicons must see words like "not" or "and"
    that `significant_tokens` deliberately drops."""
    tokens: set[str] = set()
    for text in texts:
        if text:
            tokens.update(normalize_title_unicode(text).split())
    return frozenset(tokens)


def status_markers(*texts: str | None) -> frozenset[str]:
    return _all_tokens(*texts) & _STATUS_MARKERS


def _status_markers_disagree(issuer_texts: tuple[str | None, ...], editorial_texts: tuple[str | None, ...]) -> bool:
    """A marker on one side only means the two describe different states
    of the event — including the appended-correction case that token
    containment cannot see."""
    return status_markers(*issuer_texts) != status_markers(*editorial_texts)


def _polarity_conflict(issuer_texts: tuple[str | None, ...], editorial_texts: tuple[str | None, ...]) -> bool:
    issuer_tokens = _all_tokens(*issuer_texts)
    editorial_tokens = _all_tokens(*editorial_texts)
    for positive, negative in _POLARITY_PAIRS:
        if (issuer_tokens & positive and editorial_tokens & negative) or (
            issuer_tokens & negative and editorial_tokens & positive
        ):
            return True
    return False


def _combines_another_event(issuer_headline: str, editorial_headline: str) -> bool:
    """The editorial headline adds a coordinating conjunction the issuer
    headline did not have, plus enough extra words to be a second
    clause."""
    issuer_raw = _all_tokens(issuer_headline)
    editorial_raw = _all_tokens(editorial_headline)
    added_conjunction = bool((editorial_raw - issuer_raw) & _COMBINING_CONJUNCTIONS)
    extra_significant = len(significant_tokens(editorial_headline) - significant_tokens(issuer_headline))
    return added_conjunction and extra_significant >= COMBINED_EVENT_MIN_EXTRA_TOKENS


def _too_far_apart(issuer_headline: str, editorial_headline: str) -> bool:
    extra = len(significant_tokens(editorial_headline) - significant_tokens(issuer_headline))
    return extra > MAX_EXTRA_EDITORIAL_TOKENS


def _figures_conflict(issuer_figures: frozenset[str], editorial_figures: frozenset[str]) -> bool:
    if not issuer_figures or not editorial_figures:
        return False  # one side simply omits figures — not a disagreement
    return not (issuer_figures <= editorial_figures or editorial_figures <= issuer_figures)


def describes_same_announcement(issuer: NewsStory, editorial: EditorialStory) -> bool:
    """The four-condition matching contract in this module's docstring,
    in the order that rejects soonest. Pure: reads only fields already
    on the two records."""
    # 1. company attribution
    if issuer.company_name not in editorial.matched_companies:
        return False

    # 4. publication-time relationship (cheap, and rejects a lot)
    issuer_at = _parse(published_at_of(ISSUER_KIND, issuer))
    editorial_at = _parse(published_at_of(EDITORIAL_KIND, editorial))
    if issuer_at is None or editorial_at is None:
        return False  # an unparseable timestamp is an uncertain match
    if abs(editorial_at - issuer_at) > timedelta(hours=GROUP_WINDOW_HOURS):
        return False

    # 2. event evidence — every issuer token must survive in the editorial headline
    issuer_tokens = significant_tokens(issuer.headline)
    if not issuer_tokens:
        return False
    if not issuer_tokens <= significant_tokens(editorial.headline):
        return False

    # 3c. combined event, and the extra-token budget — an editorial
    # headline that adds a second clause, or simply too much, is not
    # sufficient evidence of the same announcement.
    if _combines_another_event(issuer.headline, editorial.headline):
        return False
    if _too_far_apart(issuer.headline, editorial.headline):
        return False

    # The issuer story's own display text is `eeva_summary` (an
    # extractive excerpt of the release); the editorial story's is
    # `excerpt`. Both are already held — no article body is fetched.
    issuer_texts = (issuer.headline, issuer.eeva_summary)
    editorial_texts = (editorial.headline, editorial.excerpt)

    # 3a. a status/correction marker on one side only.
    if _status_markers_disagree(issuer_texts, editorial_texts):
        return False

    # 3b. opposed polarity terms.
    if _polarity_conflict(issuer_texts, editorial_texts):
        return False

    # 3. conflicting figures. A VETO: passing it is not evidence of
    # agreement, which is why it is the last check rather than the
    # decisive one — every condition above had to hold first.
    return not _figures_conflict(
        figures(*issuer_texts), figures(*editorial_texts),
    )


def _slot_sort_key(slot: FeedSlot) -> tuple[str, str]:
    """Newest member's timestamp, then a stable identifier — so the
    output order is fully determined by the records themselves and never
    by the order they were passed in."""
    newest = max(published_at_of(kind, item) for kind, item in slot.members)
    return (newest, slot.primary[1].id)


def group_feed_slots(
    items: tuple[tuple[str, NewsStory | EditorialStory], ...] | list[tuple[str, NewsStory | EditorialStory]],
) -> tuple[FeedSlot, ...]:
    """Groups issuer/editorial pairs describing one announcement into
    single slots, and returns every slot newest-first.

    `items` must be the already-filtered, already-tier-partitioned
    (kind, item) pairs for ONE tier bucket. Passing a mixed-tier list
    would let a grouped card surface a member the active tier filter
    excluded, which is exactly what the caller must not do.

    Every input item appears in exactly one output slot: nothing is
    dropped, nothing is duplicated, and no input object is modified.
    Pairing is one-to-one and deterministic — each issuer story takes at
    most one editorial member, each editorial item joins at most one
    group, and when several editorial items match one release the
    closest publication time wins, with the story id breaking a tie."""
    issuers = [(kind, item) for kind, item in items if kind == ISSUER_KIND]
    editorials = [(kind, item) for kind, item in items if kind == EDITORIAL_KIND]

    # Deterministic iteration regardless of input order.
    issuers.sort(key=lambda pair: (published_at_of(*pair), pair[1].id), reverse=True)

    claimed: set[int] = set()
    slots: list[FeedSlot] = []

    for issuer_pair in issuers:
        issuer = issuer_pair[1]
        candidates = [
            (index, pair)
            for index, pair in enumerate(editorials)
            if index not in claimed and describes_same_announcement(issuer, pair[1])
        ]
        if not candidates:
            slots.append(FeedSlot(members=(issuer_pair,)))
            continue

        issuer_at = _parse(published_at_of(*issuer_pair))

        def _closeness(entry, issuer_at=issuer_at):
            index, pair = entry
            editorial_at = _parse(published_at_of(*pair))
            delta = abs((editorial_at - issuer_at).total_seconds()) if issuer_at and editorial_at else float("inf")
            return (delta, pair[1].id)

        index, editorial_pair = min(candidates, key=_closeness)
        claimed.add(index)
        slots.append(FeedSlot(members=(issuer_pair, editorial_pair)))

    slots.extend(
        FeedSlot(members=(pair,)) for index, pair in enumerate(editorials) if index not in claimed
    )

    slots.sort(key=_slot_sort_key, reverse=True)
    return tuple(slots)


def slot_count(slots: tuple[FeedSlot, ...]) -> int:
    """How many cards will be drawn. Deliberately separate from the item
    counts the sidebar badge, Dashboard tile and section headers use —
    see the ranking contract in this module's docstring."""
    return len(slots)


def item_count(slots: tuple[FeedSlot, ...]) -> int:
    """How much material the slots represent. Equals the number of items
    passed in, grouped or not."""
    return sum(len(slot.members) for slot in slots)
