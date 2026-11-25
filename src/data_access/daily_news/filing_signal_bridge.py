"""Filings -> Signals bridge. Pure projection, no I/O.

A regulatory filing and a news story are different things, and this
project keeps Radar and Daily News deliberately separate. This module
does not merge them. It PROJECTS a filing that is already ingested,
already a candidate and already categorised into the one shape the
Signals feed renders, so a reader looking at the feed sees an issuer's
own 8-K next to a journalist's account of it.

WHY THIS EXISTS. NVIDIA's 2026-11-23 8-K reinstating its chief
executive was ingested, became a candidate, and could never appear in
Signals, because Signals reads editorial_stories and filings live in
candidates. The three adapters that map a filing into the Daily News
domain were built, tested, and run in shadow mode with their output
discarded. This connects the last step.

WHAT IT DOES NOT TOUCH. No scan, no pipeline, no candidate record, no
Radar surface. The adapters are called for their resolution work —
category, company identity, document URL — and the FilingDerivedNews
Candidate they return is read and dropped, never promoted or
persisted. That matters for EDINET in particular, whose candidates are
restricted to SHADOW status by validate_filing_candidate(); nothing
here changes that, because nothing here persists a candidate at all.

DORMANT BY DEFAULT. Gated on settings.filing_signals_enabled, off
unless explicitly set. The caller checks it; this module stays pure so
it can be exercised without a flag, a database or a worker."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from src.data_access.daily_news.dart_filing_candidate_adapter import map_dart_filing_to_candidate
from src.data_access.daily_news.edgar_filing_candidate_adapter import map_edgar_filing_to_candidate
from src.data_access.daily_news.edinet_filing_candidate_adapter import map_edinet_filing_to_candidate
from src.data_access.daily_news.filing_signal_tiers import (
    AMENDMENT_CARD_LABEL,
    CATEGORY_CARD_LABELS,
    is_amendment_category,
    tier_for_amendment,
    tier_for_filing_category,
)
from src.data_access.daily_news import filing_event_headline
from src.data_access.edgar import edgar_rules
from src.logic.filing_display import (
    EDINET_SOURCE_NAME,
    extractive_summary,
    is_readable_extracted_text,
    issuer_display_names,
    strip_dart_cover_page_header,
    strip_edinet_summary_artifacts,
    summary_is_substantive,
)
from src.models.daily_news_models import EditorialStory
from src.models.models import FilingEvent

#: Publisher shown on the card. The filing's own venue, never an
#: outlet — a filing has no journalist.
_PUBLISHER_BY_SOURCE: dict[str, str] = {
    "SEC EDGAR": "SEC EDGAR",
    "OpenDART / DART": "OpenDART",
    "EDINET": "EDINET",
}

#: Feed id, so a filing-derived story is distinguishable from every
#: RSS source at a glance and in a query.
_FEED_ID_BY_SOURCE: dict[str, str] = {
    "SEC EDGAR": "filing-sec-edgar",
    "OpenDART / DART": "filing-opendart",
    "EDINET": "filing-edinet",
}

#: Prefix for the deterministic story id. A filing projected twice
#: produces the same id and upserts, never a duplicate card.
#: Public: editorial_reclassification.py identifies filing-derived
#: stories by this prefix, so the two cannot drift apart silently.
STORY_ID_PREFIX = "filing"
_STORY_ID_PREFIX = STORY_ID_PREFIX

#: The DART amendment marker, stripped when deriving the base title to
#: compare against.
_DART_AMENDMENT_MARKER = "[기재정정]"


# --- Pending-items 8-K re-check ---------------------------------------
# SEC publishes an 8-K's item numbers in its own `items` column, but
# not always at the moment the filing appears. When they are absent,
# edgar_rules classifies the filing "material_event_8k_pending_items"
# — items unknown — and the adapter correctly declines it, because an
# 8-K of unknown item could be anything from a change of auditor to a
# Reg FD slide deck.
#
# Nothing re-checked them. In the dry run of 2026-11-17..23 that was
# EIGHT of thirty-two filings, a quarter of the window, including
# Meta, AMD, Oracle and Western Digital — not unwanted, merely
# unresolved, and then lost permanently.
#
# The items are recoverable without a second SEC request: the filing's
# own excerpt carries them, and edgar_rules already knows how to read
# them (extract_item_numbers) and how to combine them with whatever
# scan time knew (merge_8k_item_evaluation). This re-runs that
# combination at projection time, on each tick, until it resolves.
#
# BOUNDED. An 8-K whose items never materialise is retried only within
# PENDING_RECHECK_MAX_DAYS of its filing date and then left alone. The
# alternative is re-deriving every unresolved 8-K in history on every
# tick, forever, for filings whose metadata is simply never coming.
PENDING_RECHECK_MAX_DAYS = 14

_PENDING_ITEMS_CATEGORY = "material_event_8k_pending_items"


@dataclass(frozen=True)
class FilingDocumentContext:
    """What the extraction path already knows about one filing's
    document, handed to the projection so it does not have to go and
    find it. All three fields are optional: a filing whose document was
    never successfully extracted still projects, it simply gets the
    fallback headline and no summary.

    `item_anchor` is the recorded evidence-location section ("Item
    5.02"), never a value parsed out of the excerpt text here."""

    excerpt: str | None = None
    excerpt_translated: str | None = None
    item_anchor: str | None = None


_NO_DOCUMENT = FilingDocumentContext()


def has_pending_items(matched_rules: tuple[str, ...] | None) -> bool:
    return any((rule or "").startswith(_PENDING_ITEMS_CATEGORY) for rule in (matched_rules or ()))


def _as_utc(value: str) -> datetime | None:
    """Parses an ISO timestamp OR a bare calendar date.

    filing.rcept_dt is a bare date ("2026-11-23") while `now` is a full
    aware timestamp, so a naive-vs-aware guard alone rejected every
    real comparison and silently disabled the whole re-check. A bare
    date is anchored at UTC midnight, which is the only reading
    available and is accurate to within a day — far inside a window
    measured in days."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _days_between(earlier: str, later: str) -> float | None:
    """None when either value is missing or unparseable, which fails
    the window rather than opening it."""
    a, b = _as_utc(earlier), _as_utc(later)
    if a is None or b is None:
        return None
    return abs((b - a).total_seconds()) / 86400.0


def recheck_pending_8k_items(
    matched_rules: tuple[str, ...] | None,
    *,
    excerpt: str | None,
    filing_date: str,
    now: str,
    max_days: int = PENDING_RECHECK_MAX_DAYS,
) -> tuple[str, ...] | None:
    """Re-derived matched_rules when a pending 8-K's items can now be
    read from its excerpt, otherwise None (meaning: nothing changed,
    use what you had).

    Pure — reads the excerpt it is handed and performs no fetch. None
    for a filing that is not pending, has no excerpt, is outside the
    window, or whose excerpt still yields no items."""
    if not has_pending_items(matched_rules):
        return None
    if not excerpt:
        return None
    age_days = _days_between(filing_date, now)
    if age_days is None or age_days > max_days:
        return None

    excerpt_items = edgar_rules.extract_item_numbers(excerpt)
    if not excerpt_items:
        return None
    evaluation, newly_added = edgar_rules.merge_8k_item_evaluation((), excerpt_items)
    if not newly_added or not evaluation.matched_rules:
        return None
    return tuple(evaluation.matched_rules)


def story_id_for(filing: FilingEvent) -> str:
    """Deterministic and collision-free on the same key the filing
    store itself deduplicates by (source, corp_code, rcept_no) — never
    the title, which is neither unique nor stable."""
    return f"{_STORY_ID_PREFIX}:{filing.source_name}:{filing.corp_code}:{filing.rcept_no}"


def _resolve_candidate(filing: FilingEvent, matched_rules: tuple[str, ...] | None):
    """Routes to the one adapter that owns this source. Returns None
    for an unroutable source rather than guessing — a filing from a
    venue with no adapter is not projected."""
    if filing.source_name == "SEC EDGAR":
        return map_edgar_filing_to_candidate(filing, matched_rules or None)
    if filing.source_name == "EDINET":
        return map_edinet_filing_to_candidate(filing)
    if filing.source_name == "OpenDART / DART":
        # Amendments admitted here and nowhere else — see that
        # adapter's own docstring for why, and filing_signal_tiers for
        # how they are tiered once admitted.
        return map_dart_filing_to_candidate(filing, include_amendments=True)
    return None


def card_label_for(category: str) -> str | None:
    """A short label the card shows beside the tier, so an IPO
    registration does not read like an ordinary offering and an
    amendment does not read like an original filing."""
    if is_amendment_category(category):
        return AMENDMENT_CARD_LABEL
    return CATEGORY_CARD_LABELS.get(category)


#: The one source whose documents are English. Everything else gets the
#: curated document-type phrase instead of an extracted sentence — see
#: filing_event_headline.py for why.
_ENGLISH_NATIVE_SOURCE = "SEC EDGAR"


def _event_headline(
    filing: FilingEvent,
    candidate,
    category: str,
    excerpt: str | None,
    item_anchor: str | None,
) -> filing_event_headline.HeadlineResult:
    """The card's title line. Never the bare form name.

    EDGAR goes through the extractive path, which returns a verified
    span of the filing's own text or a fallback naming the company, the
    event type and the document. DART and EDINET go through the curated
    English document-type phrase joined to their own title."""
    company = candidate.company_name
    if filing.source_name == _ENGLISH_NATIVE_SOURCE:
        return filing_event_headline.edgar_event_headline(
            company=company,
            category=category,
            excerpt=excerpt,
            form_type=filing.pblntf_ty,
            item_anchor=item_anchor,
            excerpt_is_readable=bool(excerpt) and is_readable_extracted_text(excerpt),
        )
    return filing_event_headline.native_filing_headline(
        source_name=filing.source_name,
        category=category,
        title=candidate.title_translated or candidate.title_native,
    )


def _remaining_after(excerpt: str, sentence: str | None) -> str:
    """The excerpt with the sentence already used as the headline
    removed from the front.

    Without this the card reads the same thing twice: an extracted
    headline IS the excerpt's first event sentence, so showing the
    excerpt underneath it repeats it verbatim. What the reader wants
    below the headline is what came NEXT."""
    normalized = " ".join((excerpt or "").split())
    if not sentence:
        return normalized
    position = normalized.find(sentence)
    if position < 0:
        return normalized
    return normalized[position + len(sentence):].strip()


#: A translated cover page, once the issuer's own name and the form
#: version marker are discounted, is two or three words of document
#: type. Real content clears this easily — "Decision to Acquire Shares
#: and Equity Securities of Other Corporations" is nine.
_MIN_INFORMATIVE_SUMMARY_WORDS = 4

_FORM_VERSION_TOKEN = re.compile(r"^\d{1,3}\.\d{1,3}$")
_PLACEHOLDER_TOKEN = re.compile(r"^(?:0+|\d{8}|\d{6}-\d{7}|[A-Z])$")
_SUMMARY_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


def _is_translated_document_masthead(text: str, issuer_names: tuple[str, ...]) -> bool:
    """True for a translated DART/EDINET cover-page masthead — e.g.
    "Semiannual Report 6.7 LG Innotek Co., Ltd." — which is a document
    type, a form version and the issuer's own name, and tells the
    reader nothing the card's title has not already said.

    filing_display._is_dart_document_header() covers the same shape but
    recognises it either by a Korean corporate registry number, which a
    short masthead like the one above does not carry, or by matching
    the words before the version marker against the display title,
    which cannot work here: the summary is English and these filings'
    titles are not translated, so there is no English title to compare
    against. This reads the English text on its own terms instead."""
    issuer_words = {
        w.lower() for name in issuer_names for w in _SUMMARY_WORD.findall(name)
    }
    informative = 0
    for token in " ".join(text.split()).split():
        bare = token.strip(".,;:()[]{}\u00b7\u2014-/")
        if not bare or _FORM_VERSION_TOKEN.match(bare) or _PLACEHOLDER_TOKEN.match(bare):
            continue
        informative += sum(
            1 for w in _SUMMARY_WORD.findall(bare) if w.lower() not in issuer_words
        )
    return informative < _MIN_INFORMATIVE_SUMMARY_WORDS


def _card_excerpt(
    filing: FilingEvent,
    *,
    excerpt: str | None,
    excerpt_translated: str | None,
    headline: str,
    headline_sentence: str | None,
) -> str | None:
    """The card's summary paragraph, or None.

    Was unconditionally None, with the comment that a filing's excerpt
    is extracted by a separate path — true, but the worker already HAS
    that path's output by the time it calls this module, so the card was
    showing no summary at all for every filing while the text sat one
    function call away.

    English-native text is shown directly. Korean and Japanese text is
    shown only when a translation already exists; the native excerpt is
    never shown untranslated, because an English feed's reader cannot
    use it and this module is not permitted to translate.

    Then the same three gates the Radar card applies, in the same
    order, because the first dry run showed what skipping them costs:
    nine of eighteen summaries were translated DART cover-page
    mastheads — "Semiannual Report 6.7 LG Innotek Co., Ltd." — which
    is a heading, a form-version marker and the issuer's own name, and
    says nothing the card's title does not. is_readable_extracted_text
    passes that text happily; summary_is_substantive is the function
    written for exactly it, and its own docstring names this string.
    Returning None here is the honest answer and leaves the card
    showing its headline alone."""
    is_english = filing.source_name == _ENGLISH_NATIVE_SOURCE
    text = excerpt if is_english else excerpt_translated
    if not text or not is_readable_extracted_text(text):
        return None

    if filing.source_name == EDINET_SOURCE_NAME:
        text = strip_edinet_summary_artifacts(text)
    elif not is_english:
        text = strip_dart_cover_page_header(text)
    elif headline_sentence:
        text = _remaining_after(text, headline_sentence)

    summary = extractive_summary(text)
    if not summary:
        return None
    issuers = issuer_display_names(filing)
    if not summary_is_substantive(
        summary, headline, issuers,
        dart_header_rules=not is_english and filing.source_name != EDINET_SOURCE_NAME,
    ):
        return None
    if not is_english and _is_translated_document_masthead(summary, issuers):
        return None
    return summary


def build_filing_signal_story(
    filing: FilingEvent,
    *,
    matched_rules: tuple[str, ...] | None,
    retrieved_at: str,
    excerpt: str | None = None,
    excerpt_translated: str | None = None,
    item_anchor: str | None = None,
) -> EditorialStory | None:
    """One filing -> one EditorialStory, or None.

    None whenever anything is unresolved: an unroutable source, an
    adapter that declines the filing, a category with no tier, or a
    company the tracked registry cannot name. Never a default tier and
    never an invented company — an unplaceable filing stays a filing."""
    # A pending-items 8-K gets one more chance per tick, from its own
    # excerpt, before the adapter is asked — see the re-check block
    # above for why these were being lost outright.
    recovered_rules = recheck_pending_8k_items(
        matched_rules, excerpt=excerpt,
        filing_date=filing.filed_at or filing.rcept_dt, now=retrieved_at,
    )
    if recovered_rules is not None:
        matched_rules = recovered_rules

    candidate = _resolve_candidate(filing, matched_rules)
    if candidate is None:
        return None

    category = candidate.event_category
    if is_amendment_category(category) or _DART_AMENDMENT_MARKER in (filing.report_nm or ""):
        tier = tier_for_amendment(category, filing.report_nm or "")
    else:
        tier = tier_for_filing_category(category)
    if tier is None:
        # An unmapped category is not guessed at. See
        # filing_signal_tiers for why there is no default.
        return None

    reasons = [f"filing_event:{category}"]
    label = card_label_for(category)
    if label is not None:
        reasons.append(f"filing_label:{label}")
    if matched_rules:
        reasons.append(f"filing_rules:{','.join(matched_rules)}")

    headline_result = _event_headline(filing, candidate, category, excerpt, item_anchor)
    reasons.append(f"filing_headline:{headline_result.provenance}")

    company = candidate.company_name
    return EditorialStory(
        id=story_id_for(filing),
        headline=headline_result.headline,
        publisher=_PUBLISHER_BY_SOURCE.get(filing.source_name, filing.source_name),
        source_url=candidate.official_document_url or filing.source_url or "",
        published_at=candidate.filing_date,
        retrieved_at=retrieved_at,
        excerpt=_card_excerpt(
            filing, excerpt=excerpt, excerpt_translated=excerpt_translated,
            headline=headline_result.headline,
            headline_sentence=headline_result.source_sentence,
        ),
        matched_companies=(company,),
        matched_themes=(),
        source_feed_id=_FEED_ID_BY_SOURCE.get(filing.source_name, "filing-unknown"),
        materiality_tier=tier,
        materiality_reasons=tuple(reasons),
        identified_companies=(company,),
    )


def project_filing_signals(
    filings_with_rules: tuple[tuple[FilingEvent, tuple[str, ...]], ...],
    *,
    retrieved_at: str,
    documents_by_story_id: dict[str, FilingDocumentContext] | None = None,
) -> tuple[EditorialStory, ...]:
    """Every projectable filing, in input order, one story per filing.

    DEDUPLICATION IS BY STORY ID, NOT BY HEADLINE, and that correction
    matters more than it looks. The first version of this function
    suppressed any filing whose (publisher, headline) pair had already
    been seen. A filing's headline is its FORM NAME: every SEC 8-K is
    titled "8-K". So one EDGAR 8-K survived per run and every other one
    — NVIDIA's Item 5.02 governance filing among them — was discarded
    as a duplicate of an unrelated company's 8-K.

    Observed live: 14 projectable EDGAR filings collapsed to 4, one per
    distinct form name ("8-K", "FORM 8-K", "10-K", "10-Q").

    story_id_for() is already derived from (source, corp_code,
    rcept_no), the same key the filing store itself deduplicates by, so
    it is both necessary and sufficient here: a filing projected twice
    produces the same id and upserts. The headline check added nothing
    and removed almost everything.

    No cross-check against NEWS headlines either, deliberately: a
    filing and a news item about the same event are not duplicates of
    each other. The filing is the primary source and the news is a
    third party's account of it, and showing both is the point."""
    seen_ids: set[str] = set()
    stories: list[EditorialStory] = []
    for filing, matched_rules in filings_with_rules:
        story_id = story_id_for(filing)
        if story_id in seen_ids:
            continue
        document = (documents_by_story_id or {}).get(story_id, _NO_DOCUMENT)
        story = build_filing_signal_story(
            filing, matched_rules=matched_rules, retrieved_at=retrieved_at,
            excerpt=document.excerpt,
            excerpt_translated=document.excerpt_translated,
            item_anchor=document.item_anchor,
        )
        if story is None:
            continue
        seen_ids.add(story_id)
        stories.append(story)
    return tuple(stories)
