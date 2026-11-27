"""An event headline for a filing card, grounded in the filing's own text.

THE PROBLEM. A filing's headline is its FORM NAME. Every SEC 8-K in the
feed is titled "8-K", so five cards in a row read "8-K", "8-K", "8-K" —
the reader cannot tell NVIDIA reinstating a chief executive from Dell
furnishing a quarterly press release without opening both.

WHAT THIS DOES NOT DO. It does not write a headline. There is no model
here and no paraphrase: an EDGAR headline produced by this module is a
CONTIGUOUS SPAN of the filing's own excerpt with three kinds of
material deleted — the date preamble, parenthetical asides, and
trailing clauses that push it past the length cap — and nothing added
except the company name the tracked registry already resolved. Deletion
preserves order, so every headline is a token subsequence of the
excerpt it came from, and is_grounded_in() proves exactly that, on
every call, before the headline is returned. A headline that fails that
check is discarded in favour of the fallback, so the invariant cannot
be violated by a future edit to the extraction rules either.

    "On November 22, 2026, the Board of Directors of NVIDIA Corporation
     reinstated Jen-Hsun Huang as NVIDIA's President, Chief Executive
     Officer and member of the Board of Directors, effective
     immediately."
      -> "The Board of Directors of NVIDIA Corporation reinstated
          Jen-Hsun Huang as NVIDIA's President, Chief Executive Officer
          and member of the Board of Directors"

Not "NVIDIA reinstates Jen-Hsun Huang as CEO". That reads better and
this module cannot produce it, because turning "reinstated" into
"reinstates" and "Chief Executive Officer" into "CEO" is writing, and
writing is the thing that can be wrong about a filing. Length is the
price of being checkable.

WHY NOT ENGLISH FOR DART AND EDINET. A Korean or Japanese filing's
excerpt is Korean or Japanese, and no translation of excerpt text is
approved (see summary_grounding.py's own note on the same boundary). An
extracted headline would therefore be a Korean sentence, which is no
more readable to this feed's reader than the form name is. Those cards
instead get the curated English document-type phrase that
filing_display.py already maintains, joined to the filing's own
title — the identical treatment the Radar card gives them, which is
established, tested and translated by hand rather than by inference.

WHEN NOTHING CAN BE EXTRACTED. The fallback is not a failure message.
"Analog Devices, Inc. — Results of operations (Form 10-K)" names the
company, the event type and the document, all of them already
established facts, and is a strictly better card than "10-K". Every
EDGAR filing therefore gets a headline; only the GROUNDING of that
headline varies, and `provenance` records which it was so the caller —
and a dry run — can tell them apart."""
from __future__ import annotations

import re
from dataclasses import dataclass

from src.logic.filing_display import native_category_title

#: A headline longer than this is trimmed by dropping trailing
#: comma-delimited clauses. Set from the real corpus rather than taste:
#: the shortest real 8-K event sentence in the 2026-11-18..25 window is
#: 115 characters and the longest that still reads as one statement is
#: 176, so a cap below ~160 would reject ordinary filings and a cap
#: above it would admit the 400-character compensation paragraphs.
EVENT_HEADLINE_MAX_CHARS = 160

#: Below this, a trimmed headline has lost too much to stand on its own
#: and the fallback is used instead.
EVENT_HEADLINE_MIN_CHARS = 50

#: How far past the date preamble to look for the end of the event
#: sentence. Bounded so a document with no sentence punctuation cannot
#: produce an unbounded headline.
_EVENT_SENTENCE_SEARCH_CHARS = 420

_PROVENANCE_EXCERPT = "excerpt"
_PROVENANCE_FALLBACK = "fallback"
_PROVENANCE_NATIVE = "native_title"
#: Built from a Korean disclosure's own field table — see
#: dart_structured_fields.py.
PROVENANCE_STRUCTURED = "structured_fields"

_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
)

#: "On November 22, 2026" / "Effective as of March 3, 2026". The event
#: sentence of an 8-K item begins here in every filing in the corpus,
#: which is what makes it a reliable anchor for skipping the statutory
#: item heading ("Item 5.02. Departure of Directors or Certain
#: Officers; ...") that precedes it and is identical on every filing of
#: that item.
_DATE_PREAMBLE_RE = re.compile(
    rf"\b(?:On|Effective(?:\s+as\s+of)?)\s+(?:{_MONTHS})\s+\d{{1,2}},\s+\d{{4}}\b"
)

#: A parenthetical immediately after the date — (the "Grant Date") — is
#: part of the preamble, not of the statement.
_TRAILING_PAREN_RE = re.compile(r"^\s*\([^()]{0,80}\)")

_PARENTHETICAL_RE = re.compile(r"\s*\([^()]{0,160}\)")
_PARENTHETICAL_MAX_PASSES = 3

#: A sentence ends at a period followed by whitespace and a capital, or
#: at the end of the text. Abbreviations ending in a period and followed
#: by a capital ("Inc. (the") are the reason for the negative lookbehind
#: list rather than a bare period test.
_ABBREVIATIONS = ("Inc", "Corp", "Co", "Ltd", "L.P", "plc", "No", "Mr", "Ms", "Mrs", "Dr", "St")
_SENTENCE_END_RE = re.compile(r"\.(?=\s+[A-Z(])|\.$")

#: A headline must assert that something HAPPENED. The statutory item
#: heading is a list of nouns ("Departure of Directors or Certain
#: Officers; Election of Directors; Appointment of Certain Officers"),
#: so requiring a finite verb is what keeps the heading from being
#: mistaken for the event.
_EVENT_VERBS: frozenset[str] = frozenset({
    "adopted", "agreed", "amended", "announced", "appointed", "approved",
    "authorized", "acquired", "closed", "commenced", "completed", "declared",
    "delivered", "determined", "elected", "entered", "established", "executed",
    "filed", "granted", "informed", "issued", "launched", "named", "notified",
    "priced", "promoted", "received", "reinstated", "reported", "resigned",
    "retired", "signed", "sold", "submitted", "terminated",
})

#: Event-type labels for the fallback headline. One entry per category
#: that filing_signal_tiers.py gives a tier to; a category with no entry
#: here falls through to a readable form of the category slug rather
#: than to a blank, so adding a category can never produce an empty
#: card — only a plainer one.
CATEGORY_EVENT_LABELS: dict[str, str] = {
    "governance_or_management_change": "Change of directors or officers",
    "material_agreement": "Material definitive agreement",
    "earnings_or_results": "Results of operations",
    "acquisition_or_disposition": "Acquisition or disposition of assets",
    "financing_or_debt": "Direct financial obligation",
    "unregistered_equity_sale": "Unregistered sale of equity securities",
}

_TOKEN_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z.'’&/-]*")


@dataclass(frozen=True)
class HeadlineResult:
    """`provenance` is "excerpt" when the headline was extracted from
    the filing's own text and verified against it, "native_title" when
    it is the curated document-type phrase plus the filing's own title,
    and "fallback" when nothing could be extracted. `source_sentence` is
    the untouched sentence an extracted headline came from, so a review
    can put the two side by side without re-deriving anything."""

    headline: str
    provenance: str
    source_sentence: str | None = None
    rejected_because: str | None = None

    @property
    def is_extracted(self) -> bool:
        return self.provenance == _PROVENANCE_EXCERPT


#: Trailing punctuation is stripped before comparison so that "2026"
#: and "2026." are the same token. Without this the grounding check
#: fails on every headline whose last word happens to end a sentence in
#: the excerpt — which is most of them.
_TOKEN_EDGE_PUNCTUATION = ".'\u2019&/-"


def tokens(text: str) -> list[str]:
    found = (
        t.strip(_TOKEN_EDGE_PUNCTUATION).lower()
        for t in _TOKEN_RE.findall(text or "")
    )
    return [t for t in found if t]


def is_grounded_in(headline_body: str, excerpt: str) -> bool:
    """True when every token of `headline_body` appears in `excerpt`, in
    the same order — i.e. the headline is obtainable from the excerpt by
    deleting words and nothing else.

    This is the whole safety property of the extraction path, so it is
    checked at the point of return and not merely in a test. Greedy
    matching is exact for a subsequence test: advancing on the first
    available match can never make a later match impossible."""
    wanted = tokens(headline_body)
    if not wanted:
        return False
    index = 0
    for token in tokens(excerpt):
        if token == wanted[index]:
            index += 1
            if index == len(wanted):
                return True
    return False


def _sentence_bounds(excerpt: str, start: int) -> int | None:
    """End offset of the sentence beginning at `start`, or None when no
    sentence boundary exists inside the bounded search window."""
    window = excerpt[start : start + _EVENT_SENTENCE_SEARCH_CHARS]
    for match in _SENTENCE_END_RE.finditer(window):
        preceding = window[: match.start()]
        last_word = preceding.rsplit(" ", 1)[-1] if preceding else ""
        if last_word.rstrip(".") in _ABBREVIATIONS:
            continue
        return start + match.end()
    return None


def _strip_parentheticals(text: str) -> str:
    for _ in range(_PARENTHETICAL_MAX_PASSES):
        stripped = _PARENTHETICAL_RE.sub("", text)
        if stripped == text:
            break
        text = stripped
    return text


def _tidy(text: str) -> str:
    text = " ".join(text.split())
    text = re.sub(r"\s+([,;.])", r"\1", text)
    text = re.sub(r",\s*,", ",", text)
    return text.strip().strip(",;").strip()


#: Capitalised words that are roles, bodies or document furniture
#: rather than the name of a person or a company. Without this list a
#: dropped ", effective immediately as Chief Executive Officer" would
#: read as dropping a named person and send an otherwise good headline
#: to the fallback.
_NON_NAME_CAPITALISED: frozenset[str] = frozenset({
    "amended", "award", "awards", "board", "chairman", "chief", "committee",
    "company", "compensation", "director", "directors", "exchange", "executive",
    "financial", "general", "incentive", "officer", "officers", "operating",
    "plan", "president", "registrant", "restated", "secretary", "senior",
    "treasurer", "vice",
})

_CAPITALISED_TOKEN_RE = re.compile(r"\b[A-Z][\w.'\u2019-]*")


def _introduces_a_name(dropped: str, kept: str) -> bool:
    """True when the text trimmed off the end names a person or an
    entity that the surviving headline does not.

    Qualcomm's 8-K is the case this exists for: one sentence grants
    awards to Cristiano Amon AND Akash Palkhiwala, only the first fits
    the cap, and a headline naming one of two officers misstates who
    the filing is about — an ellipsis is not enough of a warning for
    that. Deliberately over-eager: a capitalised word it cannot place
    counts as a name, because the cost of a false positive is the
    fallback headline and the cost of a false negative is a wrong one."""
    kept_tokens = set(tokens(kept))
    for match in _CAPITALISED_TOKEN_RE.finditer(dropped):
        word = match.group().strip(".,;:'\u2019-")
        if not word:
            continue
        lowered = word.lower()
        if lowered in _NON_NAME_CAPITALISED or lowered in kept_tokens:
            continue
        return True
    return False


def _trim_to_cap(body: str) -> tuple[str, bool, str]:
    """Drops trailing comma-delimited clauses until the body fits the
    cap. Returns the body, whether anything was dropped, and the text
    that was dropped — a dropped clause is always marked with an
    ellipsis, and a clause that names someone new is not dropped at
    all (see _introduces_a_name)."""
    original = body
    truncated = False
    while len(body) > EVENT_HEADLINE_MAX_CHARS and "," in body:
        body = body[: body.rindex(",")].rstrip()
        truncated = True
    return body, truncated, original[len(body):] if truncated else ""


def _has_event_verb(body: str) -> bool:
    return any(token in _EVENT_VERBS for token in tokens(body))


def _humanized_category(category: str) -> str:
    text = (category or "").replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else "Filing"


def normalized_item_anchor(item_anchor: str | None) -> str | None:
    """"Item 5.02" from "Item 5.02", "5.02" or "Item Item 5.02".

    filing_display.item_anchor_label() prefixes "Item " onto a stored
    evidence_location.section, and in production those sections are
    themselves stored as "Item 2.02" — so its output is "Item Item
    2.02". That is a defect in the Radar card's own label and is being
    tracked separately; this function is defensive so a filing card
    never repeats it."""
    text = (item_anchor or "").strip()
    if not text:
        return None
    while text.lower().startswith("item "):
        text = text[5:].strip()
    return f"Item {text}" if text else None


def fallback_headline(
    company: str, category: str, form_type: str | None, item_anchor: str | None,
) -> str:
    """"[Company] — [event label] (Form 8-K Item 5.02)". Every part is
    a fact already established elsewhere: the company by the tracked
    registry, the category by the rule engine, the form by SEC's own
    metadata, the item by the recorded extraction anchor. Nothing is
    read out of the document text, so this is safe for a filing whose
    excerpt is raw XBRL."""
    label = CATEGORY_EVENT_LABELS.get(category) or _humanized_category(category)
    qualifiers = []
    form = (form_type or "").strip()
    if form:
        qualifiers.append(f"Form {form}")
    item = normalized_item_anchor(item_anchor)
    if item:
        qualifiers.append(item)
    suffix = f" ({' '.join(qualifiers)})" if qualifiers else ""
    return f"{company} — {label}{suffix}"


#: Words that appear in so many company names that matching on them
#: says nothing about identity. Without this list "Cisco Systems, Inc."
#: counts as named by a sentence about "Dell Technologies Inc.", purely
#: because both end in "Inc.", and the card then omits the prefix that
#: would have told the reader whose filing it is.
_GENERIC_COMPANY_TOKENS: frozenset[str] = frozenset({
    "ag", "and", "co", "company", "corp", "corporation", "electronics", "gmbh",
    "group", "holdings", "inc", "incorporated", "industries", "international",
    "kk", "limited", "llc", "lp", "ltd", "nv", "of", "plc", "sa", "se",
    "semiconductor", "semiconductors", "systems", "technologies", "technology", "the",
})


def _distinctive_tokens(company: str) -> set[str]:
    all_tokens = set(tokens(company))
    distinctive = all_tokens - _GENERIC_COMPANY_TOKENS
    # A name made entirely of generic words is better matched on all of
    # them than on nothing at all.
    return distinctive or all_tokens


def _prefixed_with_company(body: str, company: str) -> str:
    """The company name is prefixed only when the extracted sentence
    does not already name it. It comes from the tracked registry rather
    than the document, which is why is_grounded_in() is run on the body
    alone — prefixing is the one and only addition this module makes,
    and it is made from an already-resolved fact, never from the text."""
    distinctive = _distinctive_tokens(company)
    if distinctive and distinctive & set(tokens(body)):
        return body
    return f"{company} — {body}"


def edgar_event_headline(
    *,
    company: str,
    category: str,
    excerpt: str | None,
    form_type: str | None = None,
    item_anchor: str | None = None,
    excerpt_is_readable: bool = True,
) -> HeadlineResult:
    """An extracted, verified event headline, or the fallback.

    `excerpt_is_readable` is the caller's already-computed
    filing_display.is_readable_extracted_text() verdict. A 10-K excerpt
    in this corpus is raw XBRL — "adi-20261031 0000006281 False 2026 FY
    http://fasb.org/us-gaap/2026#AccruedIncomeTaxesCurrent ..." — and
    passing it through the extraction rules would at best waste work and
    at worst surface a namespace URL as a headline."""
    fallback = fallback_headline(company, category, form_type, item_anchor)

    def give_up(reason: str) -> HeadlineResult:
        return HeadlineResult(fallback, _PROVENANCE_FALLBACK, rejected_because=reason)

    text = " ".join((excerpt or "").split())
    if not text:
        return give_up("no excerpt")
    if not excerpt_is_readable:
        return give_up("excerpt is not readable prose")

    preamble = _DATE_PREAMBLE_RE.search(text)
    if preamble is None:
        return give_up("no dated event sentence")

    end = _sentence_bounds(text, preamble.start())
    if end is None:
        return give_up("event sentence has no boundary in range")
    source_sentence = text[preamble.start() : end].strip()

    body = source_sentence[preamble.end() - preamble.start() :]
    body = _TRAILING_PAREN_RE.sub("", body)
    body = _strip_parentheticals(body)
    body = _tidy(body).rstrip(".")
    if not body:
        return give_up("nothing left after removing the preamble")
    if not _has_event_verb(body):
        return give_up("no event verb")

    body, truncated, dropped = _trim_to_cap(body)
    if truncated and _introduces_a_name(dropped, body):
        return give_up("trimming would drop a named person or entity")
    if len(body) < EVENT_HEADLINE_MIN_CHARS:
        return give_up("too short after trimming to the cap")
    if len(body) > EVENT_HEADLINE_MAX_CHARS:
        return give_up("too long and no clause boundary to trim at")
    if not _has_event_verb(body):
        return give_up("event verb lost while trimming")

    if not is_grounded_in(body, text):
        # Unreachable by construction — every step above is a deletion.
        # Kept as the enforcement point rather than an assertion so a
        # future rule that stops being deletion-only degrades to the
        # fallback instead of publishing an ungrounded headline.
        return give_up("failed the grounding check")

    headline = body[:1].upper() + body[1:]
    if truncated:
        headline = f"{headline}…"
    headline = _prefixed_with_company(headline, company)
    return HeadlineResult(headline, _PROVENANCE_EXCERPT, source_sentence=source_sentence)


def native_filing_headline(
    *, source_name: str, category: str, title: str | None,
) -> HeadlineResult:
    """The curated English document-type phrase joined to the filing's
    own title — "Risk or Incident Disclosure — 중대재해 발생".

    The phrase comes from filing_display.py's hand-written tables, the
    same ones the Radar card uses, so the two surfaces cannot drift into
    describing one filing two ways."""
    native = (title or "").strip()
    label = native_category_title(source_name, category)
    if label and native and label != native:
        return HeadlineResult(f"{label} — {native}", _PROVENANCE_NATIVE)
    return HeadlineResult(label or native, _PROVENANCE_NATIVE)
