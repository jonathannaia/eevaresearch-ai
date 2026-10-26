"""Field-aware DART summary selection — deterministic, display-only.

What this produces is a DETERMINISTIC, FIELD-SELECTED EXCERPT of the
filing's own words — never a generated, paraphrased, or comprehensive
summary. Nothing is rewritten and nothing is inferred.

DART's short disclosure bodies are numbered field sets, not prose, so a
leading-substring excerpt returns whatever happens to come first: the
cover-page header, the inquiry's subject line, or — worst — the REPORT
being addressed, which reads as if the issuer had confirmed it. (An
issuer's answer may be a flat denial, but it may equally confirm that
something is under review while stating that nothing is decided; this
module selects whatever the issuer actually wrote and characterises it
in no way at all.) This module recognises the field structure and
selects the fields that carry the issuer's own position.

Everything here is structural and literal:

  * fields are recognised only from an ASCENDING numbered run starting at
    1, so a stray "10." in prose is never mistaken for a field marker;
  * a field's role is decided by matching its own label against an
    explicit Korean/English table, longest label first — never by
    guessing, never by receipt number, never by issuer;
  * the selected text is the document's own words, with its own label
    kept as the prefix, so the speaker stays identified;
  * when the structure is not positively recognised, or the field that
    carries the issuer's position cannot be included whole, this module
    returns None and the caller falls back. It never clips a field
    mid-way, because that is how a negation or a qualifier gets lost.

No model, no network, no stored-record change.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# --- roles ---------------------------------------------------------------

ROLE_RUMOUR_CONTENT = "rumour_content"      # the ALLEGATION — never the issuer's position
ROLE_MEDIA = "media"
ROLE_RUMOUR_DATE = "rumour_date"
ROLE_CLARIFICATION = "clarification"        # the issuer's answer to a rumour
ROLE_INQUIRY_SUBJECT = "inquiry_subject"
ROLE_ANSWER = "answer"                      # the issuer's answer to an exchange inquiry
ROLE_REQUEST_DETAILS = "request_details"    # the EXCHANGE's request — not an issuer statement
ROLE_DEADLINE = "deadline"
ROLE_REDISCLOSURE = "redisclosure"
ROLE_OTHER = "other"

# Label table. Longest first within each language so that the Korean
# clarification label ("풍문 또는 보도의 내용에 대한 해명내용") is never
# shadowed by the rumour-content label it begins with
# ("풍문 또는 보도의 내용") — the single most important distinction here,
# since confusing them would present the allegation as the issuer's own
# statement.
_LABELS: tuple[tuple[str, str], ...] = (
    # (label prefix, role) — Korean
    ("풍문 또는 보도의 내용에 대한 해명내용", ROLE_CLARIFICATION),
    ("풍문 또는 보도의 내용", ROLE_RUMOUR_CONTENT),
    ("풍문 또는 보도의 매체", ROLE_MEDIA),
    ("풍문 또는 보도의 발생일자", ROLE_RUMOUR_DATE),
    ("조회공시요구내용", ROLE_REQUEST_DETAILS),
    ("공시시한", ROLE_DEADLINE),
    ("재공시예정일", ROLE_REDISCLOSURE),
    ("기타 투자판단과 관련한 중요사항", ROLE_OTHER),
    ("제목", ROLE_INQUIRY_SUBJECT),
    ("내용", ROLE_ANSWER),  # delimiter-required, see _DELIMITED_LABELS
    # English (matched case-insensitively)
    ("clarification regarding the content of the rumor or report", ROLE_CLARIFICATION),
    ("clarification regarding the content of the rumor", ROLE_CLARIFICATION),
    ("content of the rumors or media reports", ROLE_RUMOUR_CONTENT),
    ("content of rumors or news reports", ROLE_RUMOUR_CONTENT),
    ("media outlet reporting the rumor", ROLE_MEDIA),
    ("date the rumor", ROLE_RUMOUR_DATE),
    ("details of the request for disclosure", ROLE_REQUEST_DETAILS),
    ("disclosure deadline", ROLE_DEADLINE),
    ("scheduled date of re-disclosure", ROLE_REDISCLOSURE),
    ("other important matters", ROLE_OTHER),
    ("response to the request for disclosure", ROLE_INQUIRY_SUBJECT),
    ("content", ROLE_ANSWER),  # delimiter-required, see _DELIMITED_LABELS
)

# Short labels that are also ordinary words. "Content" alone would claim
# "2. Contents of the Agreement Between Parties" as the issuer's answer,
# so these are recognised ONLY when the document delimits them the way the
# real filings do ("2. Content: We are reviewing ...").
_DELIMITED_LABELS = frozenset({"content", "내용", "제목"})
_LABEL_DELIMITERS = (":", "：")


# Administrative trailers that end a field's substantive content. Both are
# explicit, literal markers printed by DART itself — not a heuristic.
_TRAILER_MARKERS = ("(공시책임자)", "(Person Responsible for Disclosure)", "※")

# A field marker candidate: a 1-2 digit number followed by "." and space.
_MARKER_RE = re.compile(r"(?:(?<=\s)|^)(\d{1,2})\.(?=\s|$)")

# Outcome of a selection attempt. The distinction matters for safety:
# "not applicable" may fall through to the generic leading-substring
# summary, "unsafe" may NOT — see select_summary's own docstring.
OUTCOME_NOT_APPLICABLE = "not_applicable"   # no DART disclosure structure recognised
OUTCOME_UNSAFE = "unsafe"                   # recognised, but the issuer's position is not safely selectable
OUTCOME_SELECTED = "selected"               # a substantive field selection was made

# Roles whose presence means "this is one of the DART disclosure forms
# this module knows", even when the response field itself is unrecognised.
_IDENTIFYING_ROLES = frozenset({
    ROLE_RUMOUR_CONTENT, ROLE_CLARIFICATION, ROLE_INQUIRY_SUBJECT,
    ROLE_ANSWER, ROLE_REQUEST_DETAILS, ROLE_DEADLINE,
})

# Document kinds this module positively recognises.
KIND_RUMOUR_CLARIFICATION = "rumour_clarification"
KIND_INQUIRY_RESPONSE = "inquiry_response"
KIND_INQUIRY_REQUEST = "inquiry_request"

# Selection per kind: (required role, ordered roles to render).
_SELECTION: dict[str, tuple[str, tuple[str, ...]]] = {
    KIND_RUMOUR_CLARIFICATION: (ROLE_CLARIFICATION, (ROLE_CLARIFICATION, ROLE_REDISCLOSURE)),
    KIND_INQUIRY_RESPONSE: (ROLE_ANSWER, (ROLE_INQUIRY_SUBJECT, ROLE_ANSWER, ROLE_REDISCLOSURE)),
    KIND_INQUIRY_REQUEST: (ROLE_REQUEST_DETAILS, (ROLE_REQUEST_DETAILS, ROLE_DEADLINE)),
}

# Generous compared with the leading-substring cap, because whole fields
# are kept rather than cut. A field that does not fit is DROPPED whole; a
# required field that does not fit yields None.
# Sized so a whole clarification field (the longest observed is 698
# characters) plus a short re-disclosure date fits WITHOUT clipping.
# This is a cap on composed WHOLE fields, not a wider leading window:
# a field that does not fit is dropped entire, never cut.
_FIELD_SUMMARY_MAX_CHARS = 900


@dataclass(frozen=True)
class _Field:
    number: int
    label: str
    body: str
    role: str
    raw: str  # label + body exactly as the document prints them


def marker_end_positions(normalized: str) -> frozenset[int]:
    """End offsets of the numbered markers that form an ascending run
    1, 2, 3, ... — context, not a bare digit test.

    "The deadline is 10." in ordinary prose is not part of such a run, so
    its period stays a sentence ending. A genuine "10." IS a marker when
    markers 1-9 precede it."""
    ends: list[int] = []
    expected = 1
    for match in _MARKER_RE.finditer(normalized):
        if int(match.group(1)) != expected:
            continue
        ends.append(match.start() + len(match.group(1)) + 1)
        expected += 1
    # A lone "1." is not a list. Requiring at least "1." AND "2." keeps a
    # sentence that merely ends in a small number ("The ratio is 1.")
    # a sentence — the same two-field minimum parse_fields() applies.
    if len(ends) < 2:
        return frozenset()
    return frozenset(ends)


def _role_for(body: str) -> tuple[str, str]:
    """(label, role) for a field body, longest label first."""
    lowered = body.lower()
    for label, role in _LABELS:
        matched = lowered.startswith(label) if label[0].isascii() else body.startswith(label)
        if not matched:
            continue
        if label in _DELIMITED_LABELS:
            rest = body[len(label):].lstrip()
            if not rest.startswith(_LABEL_DELIMITERS):
                continue
        return body[: len(label)], role
    return "", ROLE_OTHER


_SENTENCE_FINAL = (".", "。", "!", "?", "！", "？")


def _strip_trailer(text: str) -> str:
    """Removes an administrative trailer, but ONLY where the substantive
    text before it has already ended in sentence-final punctuation (or the
    trailer starts the field).

    Without that guard an incidental marker truncated real content:
    "The Company denies ※ see note ※ and confirms nothing is decided."
    became "The Company denies". A trailer never cuts a sentence in half."""
    cut = len(text)
    for marker in _TRAILER_MARKERS:
        found = text.find(marker)
        while found != -1:
            before = text[:found].rstrip()
            if found == 0 or before.endswith(_SENTENCE_FINAL):
                cut = min(cut, found)
                break
            found = text.find(marker, found + 1)
    return text[:cut].strip(" :·-")


def parse_fields(text: str) -> tuple[_Field, ...]:
    """Fields of an ascending numbered run starting at 1. Returns () when
    no such run of at least two fields exists — the signal that this
    document's structure was NOT positively recognised."""
    normalized = " ".join(text.split())
    spans: list[tuple[int, int, int]] = []  # (number, body_start, marker_start)
    expected = 1
    for match in _MARKER_RE.finditer(normalized):
        if int(match.group(1)) != expected:
            continue
        spans.append((expected, match.end() + 1, match.start()))
        expected += 1
    if len(spans) < 2:
        return ()

    fields: list[_Field] = []
    for index, (number, body_start, _) in enumerate(spans):
        body_end = spans[index + 1][2] if index + 1 < len(spans) else len(normalized)
        raw = normalized[body_start:body_end].strip()
        label, role = _role_for(raw)
        body = raw[len(label):].strip(" :·-") if label else raw
        fields.append(_Field(
            number=number, label=label.strip(), body=_strip_trailer(body),
            role=role, raw=_strip_trailer(raw),
        ))
    return tuple(fields)


def classify(fields: tuple[_Field, ...]) -> str | None:
    roles = {f.role for f in fields if f.body}
    if ROLE_CLARIFICATION in roles:
        return KIND_RUMOUR_CLARIFICATION
    if ROLE_ANSWER in roles and ROLE_INQUIRY_SUBJECT in roles:
        return KIND_INQUIRY_RESPONSE
    if ROLE_REQUEST_DETAILS in roles:
        return KIND_INQUIRY_REQUEST
    return None


def _identifying_labels_present(text: str) -> bool:
    """A label-level scan, independent of whether the numbered run parses.

    A document whose markers are reordered, incomplete, or absent can
    still be plainly a rumour clarification — and such a document must
    never fall through to a leading-substring excerpt, because its FIRST
    field is the allegation being addressed."""
    lowered = text.lower()
    for label, role in _LABELS:
        if role not in _IDENTIFYING_ROLES:
            continue
        if label in _DELIMITED_LABELS:
            continue  # too generic to identify a form on its own
        if (label in lowered) if label[0].isascii() else (label in text):
            return True
    return False


def select_summary(text: str) -> tuple[str, str | None]:
    """(outcome, summary).

    OUTCOME_SELECTED  — use the returned text.
    OUTCOME_UNSAFE    — this IS a recognised disclosure form, but the
                        issuer's own position could not be selected
                        whole. The caller must show its honest fallback.
                        It must NOT fall back to a leading-substring
                        excerpt: for a rumour clarification that excerpt
                        begins with the ALLEGATION, which would read as
                        the issuer confirming the very report it is
                        addressing.
    OUTCOME_NOT_APPLICABLE — no recognised structure; ordinary prose
                        stays eligible for the generic path."""
    if not text or not text.strip():
        return OUTCOME_NOT_APPLICABLE, None
    selected = field_aware_summary(text)
    if selected:
        return OUTCOME_SELECTED, selected
    fields = parse_fields(text)
    if any(f.role in _IDENTIFYING_ROLES for f in fields) or _identifying_labels_present(text):
        return OUTCOME_UNSAFE, None
    return OUTCOME_NOT_APPLICABLE, None


def field_aware_summary(text: str) -> str | None:
    """A summary built from recognised fields, or None.

    None means "not positively recognised, or the issuer's own position
    could not be included whole" — the caller must then fall back rather
    than present something partial."""
    if not text or not text.strip():
        return None
    fields = parse_fields(text)
    if not fields:
        return None
    kind = classify(fields)
    if kind is None:
        return None

    required_role, ordered_roles = _SELECTION[kind]
    by_role = {f.role: f for f in fields if f.body}
    required = by_role.get(required_role)
    if required is None:
        return None

    def rendered(field: _Field) -> str:
        """The document's OWN wording, label included and unaltered — no
        punctuation is inserted and no word is rewritten, so the speaker
        and the qualification stay exactly as the filing states them."""
        return field.raw

    required_text = rendered(required)
    if len(required_text) > _FIELD_SUMMARY_MAX_CHARS:
        # Never clipped: a negation or qualifier lives at the end of these
        # fields, so a cut here would invert the meaning.
        return None

    chosen: list[_Field] = [required]
    budget = _FIELD_SUMMARY_MAX_CHARS - len(required_text)
    for role in ordered_roles:
        if role == required_role:
            continue
        field = by_role.get(role)
        if field is None:
            continue
        piece = rendered(field)
        if len(piece) + 1 <= budget:
            chosen.append(field)
            budget -= len(piece) + 1

    chosen.sort(key=lambda f: f.number)
    return " ".join(rendered(f) for f in chosen).strip()
