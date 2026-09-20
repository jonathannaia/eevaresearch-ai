"""Exact-quote verification: does the stored excerpt actually support the
claim the model wrote?

Until now the matrix checked that every evidence record *resolved* — that
it existed, came from an approved tier and was current — but never that
the sentence being published could be found in the retrieved text. A
fluent, well-formed claim citing a real evidence id could pass every row
while asserting something the document never said. This module closes
that gap, and the policy's last row refuses any would-publish decision
whose claims it cannot support.

The rule is deliberately literal about the things that cannot be
paraphrased, and tolerant about the words that can:

  * every number, amount, percentage and date in the claim must appear in
    the excerpt, normalized only for thousands separators and currency
    symbols. A figure the document does not contain is fatal;
  * every remaining content word must appear in the excerpt, after
    removing stopwords, the issuer's own name (which comes from the
    filing metadata, not the text) and the attribution a claim uses to
    say who reported the fact ("X states that …"). Words match on a
    light stem, so the grammatical shift a restatement forces — "we
    operate" becoming "CoreWeave operates" — is not treated as new
    content.

That accepts a faithfully attributed restatement and rejects an invented
counterparty, an unstated figure, or a fact drawn from somewhere else.
It is a containment test, not a semantic one: it cannot catch a claim
that reuses the document's own words to mean something different, which
is why a would-publish decision still needs human review before the
limited-publishing stage.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

# Words that carry no factual weight, so their absence proves nothing.
_STOPWORDS = frozenset("""
a an and are as at be been being by for from had has have in into is it its of on or over that the their there
this to was were will with within without during under above about after before between than then these those
""".split())

# How a claim attributes a fact to its source. Attribution is the agent's
# own framing, never something the document has to contain.
_ATTRIBUTION = frozenset("""
states state stated states' reports report reported says said say discloses disclose disclosed announces announce
announced according reporting disclosing announcing confirms confirm confirmed
""".split())

_PRONOUNS = frozenset("our ours we us their theirs they them it his her hers company companys issuer".split())

# Nouns that name the document itself rather than anything it says, so a
# claim may cite the source it came from ("according to the filing").
_SOURCE_NOUNS = frozenset("filing filings document documents disclosure disclosures release prospectus form".split())

_NUMBER = re.compile(r"(?<![\w.])(?:[$€£¥]\s?)?\d[\d,]*(?:\.\d+)?%?")
_WORD = re.compile(r"[a-z0-9]+(?:[.'-][a-z0-9]+)*")


@dataclass(frozen=True)
class QuoteSupport:
    """Whether one claim is supported, and precisely what was missing."""
    claim_id: str
    verified: bool
    missing_numbers: tuple[str, ...] = ()
    missing_words: tuple[str, ...] = ()
    detail: str = ""


def normalize(text: str) -> str:
    """Case, Unicode form, smart punctuation and whitespace only — never
    word order or content."""
    text = unicodedata.normalize("NFKC", text or "")
    for smart, plain in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'),
                         ("—", " "), ("–", " "), ("−", "-"), (" ", " ")):
        text = text.replace(smart, plain)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _numbers(text: str) -> tuple[str, ...]:
    values = []
    for raw in _NUMBER.findall(text):
        cleaned = raw.replace(",", "").replace(" ", "").lstrip("$€£¥")
        if cleaned:
            values.append(cleaned)
    return tuple(values)


def _words(text: str) -> tuple[str, ...]:
    return tuple(_WORD.findall(text))


def _stem(word: str) -> str:
    """Enough morphology to survive a restatement, and no more. Applied to
    both sides, so it only ever collapses a claim word onto an excerpt word
    that already shares its root — never onto an unrelated one."""
    word = word.removesuffix("'s")
    if len(word) > 4 and word.endswith("ies"):
        word = word[:-3] + "y"
    elif len(word) > 4 and word.endswith("es"):
        word = word[:-2]
    elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    if len(word) > 4 and word.endswith("ing"):
        word = word[:-3]
    elif len(word) > 4 and word.endswith("ed"):
        word = word[:-2]
    return word.removesuffix("e") if len(word) > 4 else word


def _significant(words: Iterable[str], *, allowed: frozenset[str]) -> tuple[str, ...]:
    out = []
    for word in words:
        if word in _STOPWORDS or word in _ATTRIBUTION or word in _PRONOUNS or word in allowed:
            continue
        if word in _SOURCE_NOUNS:
            continue
        if word.isdigit() or _NUMBER.fullmatch(word):
            continue  # numbers are checked separately and more strictly
        out.append(word)
    return tuple(out)


def _allowed_from_issuer(issuer_names: Sequence[str]) -> frozenset[str]:
    allowed: set[str] = set()
    for name in issuer_names:
        allowed.update(_words(normalize(name)))
        allowed.update({"inc", "corp", "corporation", "co", "ltd", "plc", "llc", "holdings", "group"})
    return frozenset(allowed)


def verify_claim(
    statement: str, excerpts: Sequence[str], *, claim_id: str = "", issuer_names: Sequence[str] = (),
) -> QuoteSupport:
    """One claim against the excerpts its evidence actually carried."""
    haystack = normalize(" \n ".join(e for e in excerpts if e))
    if not haystack:
        return QuoteSupport(claim_id, False, detail="no stored excerpt to verify against")
    claim = normalize(statement)
    if not claim:
        return QuoteSupport(claim_id, False, detail="claim statement is empty")

    excerpt_numbers = set(_numbers(haystack))
    missing_numbers = tuple(sorted({n for n in _numbers(claim) if n not in excerpt_numbers}))

    excerpt_words = set(_words(haystack))
    excerpt_stems = {_stem(word) for word in excerpt_words}
    allowed = _allowed_from_issuer(issuer_names)
    missing_words = tuple(sorted({
        word for word in _significant(_words(claim), allowed=allowed)
        if word not in excerpt_words and _stem(word) not in excerpt_stems
    }))

    verified = not missing_numbers and not missing_words
    detail = ""
    if missing_numbers:
        detail = "figures not in the excerpt: " + ", ".join(missing_numbers)
    elif missing_words:
        detail = "wording not in the excerpt: " + ", ".join(missing_words)
    return QuoteSupport(claim_id, verified, missing_numbers, missing_words, detail)


def verify_proposal(
    proposal, evidence_by_id: Mapping[str, object], *, issuer_names: Sequence[str] = (),
    evidence_text: Mapping[str, str] | None = None,
) -> dict[str, QuoteSupport]:
    """Every claim against only the evidence it cites. A claim citing an
    evidence id that carries no excerpt is unsupported, never assumed.

    `evidence_text` is the retrieved text the session actually read. The
    evidence record's own excerpt_or_locator may be a locator ("section:
    Item 2") rather than the text, so the text is preferred when present."""
    evidence_text = evidence_text or {}
    results: dict[str, QuoteSupport] = {}
    for claim in proposal.claims:
        excerpts = []
        for evidence_id in claim.evidence_ids:
            text = (evidence_text.get(evidence_id) or "").strip()
            if text:
                excerpts.append(text)
                continue
            record = evidence_by_id.get(evidence_id)
            if record is not None:
                excerpts.append(getattr(record, "excerpt_or_locator", "") or "")
        results[claim.claim_id] = verify_claim(
            claim.statement, excerpts, claim_id=claim.claim_id, issuer_names=issuer_names,
        )
    return results


def unsupported_claim_ids(support: Mapping[str, QuoteSupport]) -> tuple[str, ...]:
    return tuple(sorted(cid for cid, result in support.items() if not result.verified))
