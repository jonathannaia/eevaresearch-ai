"""Same-story detection for the Daily News editorial lane. Pure, deterministic, no I/O — the same discipline dedup.py and
localization_dedup.py already follow.

THE GAP THIS FILLS. editorial_pipeline.py keys duplicates on
(normalized_title, publisher) or normalized URL — an EXACT title
match. A near-duplicate slips through whether or not the publisher is
the same, which is why the live feed on 2026-11-23 showed both of
these as separate High Signals:

    "Reports: Russia may launch nuclear weapon into orbit as soon as
     next month"
    "Russia may launch a nuclear-armed satellite far sooner than we
     thought"

PUBLISHER IS NOT A GATE, AND THAT WAS A CORRECTION. This module first
required the two items to come from DIFFERENT publishers, on the
reasoning that same-publisher duplicates were already caught exactly
upstream and that a publisher's own second piece is a follow-up.
Checking the real data killed that: BOTH Russia items are from Ars
Technica, minutes apart. The different-publisher gate would have
excluded the only true duplicate in the window.

Scored across every pair of the 35 live High Signal items in that
window — 595 pairs — exactly one lands above threshold, and it is
that pair. So the gate costs a real detection and buys nothing
measurable.

WHY HEADLINE SIMILARITY ALONE DOES NOT WORK. Those two headlines share
three words. Ordinary token-set similarity over all words puts them at
roughly 0.2, below any threshold that would not also merge unrelated
stories. The signal is not how much the headlines overlap — it is
WHICH words they share.

So similarity is computed over RARE tokens only: words of four or more
characters that are neither stopwords nor part of this product's own
taxonomy vocabulary. Dropping taxonomy words is the important half.
Two genuinely different Samsung stories will both say "memory",
"chips" and "HBM", because that is what this entire feed is about —
counting those as evidence of sameness would merge the feed into
itself. "russia", "nuclear" and "softbank" carry real information
precisely because they are not what the feed is usually about.

Measured against real pairs from that day:

    unrelated Samsung / NVIDIA / TSMC pairs    0.09 - 0.12
    the Russia pair                            0.40
    a reconstructed SoftBank/SK hynix pair     0.67

The threshold sits at 0.35, in the middle of a wide gap rather than on
a knife edge.

NARROW BY CONSTRUCTION, like localization_dedup.py. Every structural
gate must independently hold before the one similarity check runs, and
a partial match never suppresses: two items are always kept as two
fully visible stories unless every gate agrees. The first-seen story
wins, matching this package's existing convention — nothing is merged,
and no corroborating source is attached to the survivor."""
from __future__ import annotations

import re
from typing import Iterable

from src.data_access.daily_news.company_aliases import build_company_alias_entries
from src.data_access.daily_news.materiality_classification import TAXONOMY_KEYWORDS

#: Two items must be published within this long of each other. A story
#: genuinely re-reported a week later is a follow-up, not a duplicate.
MAX_HOURS_APART = 48

#: Similarity required over rare tokens. See the module docstring for
#: the measured separation this sits inside.
MIN_RARE_TOKEN_SIMILARITY = 0.35

#: Below this, a headline has too little distinctive content for a
#: ratio to mean anything — two three-word headlines sharing two words
#: score 0.5 while saying almost nothing.
MIN_RARE_TOKENS = 3

#: At least this many shared rare tokens, independent of the ratio.
MIN_SHARED_RARE_TOKENS = 2

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _normalize(headline: str) -> str:
    """Whitespace- and punctuation-insensitive form, for the
    exact-headline route only."""
    return " ".join(_TOKEN_RE.findall((headline or "").lower()))
_MIN_TOKEN_LENGTH = 4

# Ordinary English plus the reporting and time vocabulary every
# headline carries. Not a general stopword list — only words whose
# presence in two headlines says nothing about whether they describe
# the same event.
_STOPWORDS: frozenset[str] = frozenset("""
this that these those with from into over under after before again more most other some such only
same very than when while then there here what which whose will would could should might must
have has had been being does did doing about above below between through during without within
news report reports reported said says saying told sources source exclusive update updated
year years week weeks month months today tomorrow yesterday soon sooner later latest early earlier
next last first second third amid ahead according reportedly just also still even much many
thought thinks believe believed expect expected expects fear feared warn warns warned claim claims
claimed suggest suggests suggested seem seems seemed appear appears appeared likely unlikely
""".split())


def _company_vocabulary() -> frozenset[str]:
    """Every word appearing in a tracked company's name or alias, plus
    the legal suffixes.

    THE SUBJECT IS NOT THE STORY. Two different Oracle headlines both
    say "Oracle" and "Corporation"; on a short headline those two words
    can be half the distinctive content, which put two genuinely
    unrelated Oracle stories at 0.40 — above the threshold. The company
    is what both stories are ABOUT, not evidence that they are the SAME
    story. What distinguishes them is everything else.

    Found by a test fixture, not by reasoning: six synthetic
    same-company headlines collapsed into one."""
    words: set[str] = {
        "corp", "corporation", "incorporated", "limited", "holdings", "group",
        "company", "technologies", "technology", "electronics", "industries",
        "international", "systems", "solutions", "partners", "ventures",
    }
    for entry in build_company_alias_entries():
        for name in (entry.company_name, *entry.aliases, *entry.brand_aliases):
            words.update(_TOKEN_RE.findall(name.lower()))
    return frozenset(word for word in words if len(word) >= _MIN_TOKEN_LENGTH)


def _taxonomy_vocabulary() -> frozenset[str]:
    """Every word appearing in any taxonomy phrase. Excluded from the
    similarity because they are what this whole feed is about — see the
    module docstring."""
    return frozenset(
        word
        for phrases in TAXONOMY_KEYWORDS.values()
        for phrase in phrases
        for word in _TOKEN_RE.findall(phrase.lower())
    )


_TAXONOMY_WORDS = _taxonomy_vocabulary()
_COMPANY_WORDS = _company_vocabulary()
_IGNORED_WORDS = _STOPWORDS | _TAXONOMY_WORDS | _COMPANY_WORDS


def rare_tokens(headline: str) -> frozenset[str]:
    """Pure. The distinctive words of a headline: four or more
    characters, and none of a stopword, this feed's taxonomy
    vocabulary, or a tracked company's name. See each vocabulary's own
    function for why it is excluded."""
    return frozenset(
        token
        for token in _TOKEN_RE.findall((headline or "").lower())
        if len(token) >= _MIN_TOKEN_LENGTH and token not in _IGNORED_WORDS
    )


def rare_token_similarity(left: str, right: str) -> float:
    """Jaccard similarity over rare tokens. 0.0 when either side has
    none, so an empty or wholly generic headline never matches."""
    a, b = rare_tokens(left), rare_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _hours_apart(left: str, right: str) -> float | None:
    """None when either timestamp is missing or unparseable — which
    fails the gate rather than passing it, since an unknown interval is
    not evidence of sameness."""
    from datetime import datetime

    try:
        a = datetime.fromisoformat(left)
        b = datetime.fromisoformat(right)
    except (TypeError, ValueError):
        return None
    if (a.tzinfo is None) != (b.tzinfo is None):
        return None
    return abs((a - b).total_seconds()) / 3600.0


def find_same_story_duplicate(
    existing: Iterable[tuple[str, str, str]],
    headline: str,
    published_at: str,
) -> tuple[str, str, float] | None:
    """Returns (matched_publisher, matched_headline, similarity) for the
    first already-stored story this one duplicates, or None.

    `existing` is an iterable of (publisher, headline, published_at) —
    the caller's own already-persisted stories, passed in this shape so
    this module never imports daily_news_models.py, exactly as dedup.py
    does.

    Every gate must hold: both headlines carrying enough distinctive
    content to compare, publication within MAX_HOURS_APART, and
    similarity at or above the threshold with at least
    MIN_SHARED_RARE_TOKENS words in common. Any gate failing means
    both stories are kept. Publisher is deliberately NOT a gate — see
    the module docstring."""
    candidate_rare = rare_tokens(headline)
    normalized_candidate = _normalize(headline)

    for existing_publisher, existing_headline, existing_published_at in existing:
        # Route 0 — the same headline, word for word. The strongest
        # possible same-story evidence, and it needs no similarity
        # machinery at all. Upstream catches this only when the
        # publisher also matches, so a syndicated wire story carried by
        # two outlets reaches here untouched. No token-count floor:
        # "Oracle Corporation posts AI cloud growth" has two
        # distinctive words once the company name is removed, and two
        # identical copies of it are still one story.
        if normalized_candidate and _normalize(existing_headline) == normalized_candidate:
            return existing_publisher, existing_headline, 1.0

        if not candidate_rare:
            continue
        existing_rare = rare_tokens(existing_headline)
        if not existing_rare:
            continue
        shared = candidate_rare & existing_rare
        if len(shared) < MIN_SHARED_RARE_TOKENS:
            continue
        hours = _hours_apart(existing_published_at, published_at)
        if hours is None or hours > MAX_HOURS_APART:
            continue

        similarity = len(shared) / len(candidate_rare | existing_rare)

        # Route 1 — ratio. Needs enough distinctive content on BOTH
        # sides for a ratio to mean anything.
        if (
            len(candidate_rare) >= MIN_RARE_TOKENS
            and len(existing_rare) >= MIN_RARE_TOKENS
            and similarity >= MIN_RARE_TOKEN_SIMILARITY
        ):
            return existing_publisher, existing_headline, similarity

        # Route 2 — containment. The shorter headline's distinctive
        # words appear WHOLLY inside the longer one. A ratio punishes
        # this shape for the longer headline merely saying more:
        # "SoftBank Son weighed a $1 trillion bid for SK hynix" against
        # "SoftBank's Son Weighed $1 Trillion Acquisition of SK hynix"
        # leaves the shorter side with two distinctive words, both
        # present in the longer, which the MIN_RARE_TOKENS floor would
        # otherwise discard entirely.
        #
        # Verified against all 595 pairs of the live High Signal window:
        # this route adds that detection and not one false positive.
        smaller = candidate_rare if len(candidate_rare) <= len(existing_rare) else existing_rare
        if shared == smaller:
            return existing_publisher, existing_headline, similarity
    return None
