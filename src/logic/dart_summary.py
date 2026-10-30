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
    # 답변내용 ("content of the reply") is an EXPLICIT response label and
    # keeps specific handling. Listed before 내용 so longest-first
    # matching reaches it.
    ("답변내용", ROLE_ANSWER),
    # 제목 is deliberately NOT a subject role: it is the generic word
    # "title", exactly like English "Title:", and giving it a role would
    # let the Korean pair classify as an issuer response with none of the
    # corroboration the English pair requires. It is recognised only via
    # _GENERIC_SUBJECT_LABELS, on the corroborated path.
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

# Generic field labels that identify NOTHING on their own. The live
# "Response to a Request for Disclosure" filings print their subject as
# plain "1. Title:" rather than the longer wording in the table above, so
# the form is unrecognisable from labels alone. These are accepted as an
# inquiry-response subject ONLY when the caller corroborates the document
# class from the filing's own stored classification (see
# select_summary(inquiry_response_expected=...)) — never from the labels
# by themselves, because "Title:"/"Content:" appear in many unrelated
# disclosures.
_GENERIC_SUBJECT_LABELS = ("title", "제목")

# Generic in EVERY language this module handles. "제목"/"내용" are the
# Korean words for "title"/"content" and carry no more evidential weight
# than their English counterparts — a Korean document must clear the same
# corroboration and request/response discrimination as an English one.
#
# The category "Rumor Inquiry or Response" covers BOTH an exchange's
# request and an issuer's reply, so it is supporting context only — it
# cannot by itself establish that a generic "Content:" field holds an
# issuer response. The subject field must additionally describe the
# document as a RESPONSE in its own words. These are the document's own
# self-descriptions, exactly like the label table above — not an
# interpretation of what the issuer meant.
_RESPONSE_SELF_DESCRIPTIONS = ("response to", "답변")
# Roles whose presence proves the document is the REQUEST side, or is a
# rumour-content document, in which case the generic path never applies.
_NON_RESPONSE_ROLES = frozenset({ROLE_REQUEST_DETAILS, ROLE_DEADLINE, ROLE_RUMOUR_CONTENT})


def _is_generic_subject(field: "_Field") -> bool:
    """A field whose own text opens with a bare generic subject label.

    These never reach the role table (deliberately — "Title:" identifies
    nothing), so the check is on the field's own raw text."""
    raw = (field.raw or "").lstrip().lower()
    return any(raw.startswith(lab + d) for lab in _GENERIC_SUBJECT_LABELS for d in (":", "：", " "))


def _subject_describes_a_response(field: "_Field") -> bool:
    """The subject field says, in the document's own words, that this is
    a reply — "Response to Request for Disclosure ...", "... 답변".

    An exchange REQUEST carries the same broad category and the same
    generic labels but never describes itself this way, which is what
    separates the two."""
    text = (field.raw or "").lower()
    return any(marker in text for marker in _RESPONSE_SELF_DESCRIPTIONS)


def _generic_response_subject(fields: tuple["_Field", ...]) -> "_Field | None":
    """The generically-labelled subject of an issuer RESPONSE, or None.

    Returns None whenever any request-side or rumour-content role is
    present, so a request document — or an allegation restated under a
    "Content:" label — can never be read as the issuer's answer."""
    if any(f.role in _NON_RESPONSE_ROLES for f in fields if f.body):
        return None
    return next((f for f in fields
                 if f.body and _is_generic_subject(f) and _subject_describes_a_response(f)), None)

# Short labels that are also ordinary words. "Content" alone would claim
# "2. Contents of the Agreement Between Parties" as the issuer's answer,
# so these are recognised ONLY when the document delimits them the way the
# real filings do ("2. Content: We are reviewing ...").
_DELIMITED_LABELS = frozenset({"content", "내용", "답변내용", "제목"})
_LABEL_DELIMITERS = (":", "：")
# DART prints its Korean field labels without a colon ("2. 내용 당사는 ..."),
# so whitespace is an accepted delimiter there. English keeps colon-only:
# "Content " opens ordinary prose ("Content Summary of Terms"), whereas
# "내용" followed by a non-space character is a different word entirely
# ("내용물"), which is why the looser rule is safe in Korean and not in
# English.
_WHITESPACE_DELIMITED_LABELS = frozenset({"내용", "답변내용", "제목"})


# Administrative trailers that end a field's substantive content. Both are
# explicit, literal markers printed by DART itself — not a heuristic.
_TRAILER_MARKERS = (
    "(공시책임자)",
    # Translation variants of the same Korean signature block, observed
    # live. Each is still only honoured after a completed sentence.
    "(Person Responsible for Disclosure)",
    "(Disclosure Officer)",
    "※",
)

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

# Roles that carry the ISSUER'S OWN WORDS. A selection from one of these
# must end on sentence-final punctuation: a body cut mid-clause can drop
# the negation or qualifier that reverses its meaning ("... however,
# nothing has been" / "... has neither pursued nor"). A request's own
# subject line legitimately ends without punctuation, so it is excluded.
_ISSUER_STATEMENT_ROLES = frozenset({ROLE_CLARIFICATION, ROLE_ANSWER})
# A complete statement ends in sentence-final punctuation, optionally
# followed by closing quotes or brackets ("... and fab."  /  ... decided.”
# /  ... (the figure).").  Those closers are stripped BEFORE the check, so
# a genuine quoted sentence is accepted while text that merely ends on a
# bracket — a visible cut — is not.
_STATEMENT_FINAL = (".", "。", "!", "?", "！", "？")
_CLOSING_MARKS = "\"'”’)）]】』」>»"

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
            tail = body[len(label):]
            delimited = tail.lstrip().startswith(_LABEL_DELIMITERS)
            if not delimited and label in _WHITESPACE_DELIMITED_LABELS:
                delimited = bool(tail) and tail[0].isspace()
            if not delimited:
                continue
        return body[: len(label)], role
    return "", ROLE_OTHER


_SENTENCE_FINAL = (".", "。", "!", "?", "！", "？")


def _ends_completely(body: str) -> bool:
    """Conservative only. Sentence-final punctuation (after closing quotes
    or brackets) is treated as "not visibly cut".

    It establishes nothing about completeness. A field truncated
    immediately after an abbreviation ("... SK Hynix Inc.") ends in a
    period and is accepted — that is a MISSED TRUNCATION (a false
    negative for truncation detection), not evidence that the field is
    complete. The check only rejects text that is visibly mid-clause — an
    ellipsis, a dangling conjunction — because that is where a negation
    or qualifier gets lost. Missed truncations are preferred to
    discarding complete issuer statements."""
    stripped = body.rstrip().rstrip(_CLOSING_MARKS).rstrip()
    return stripped.endswith(_STATEMENT_FINAL)


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


def classify(fields: tuple[_Field, ...], inquiry_response_expected: bool = False) -> str | None:
    roles = {f.role for f in fields if f.body}
    if ROLE_CLARIFICATION in roles:
        return KIND_RUMOUR_CLARIFICATION
    if ROLE_ANSWER in roles and ROLE_INQUIRY_SUBJECT in roles:
        return KIND_INQUIRY_RESPONSE
    if ROLE_REQUEST_DETAILS in roles:
        return KIND_INQUIRY_REQUEST
    # Corroborated generic form: an answer field plus a generically
    # labelled subject, accepted only because the CALLER established the
    # document class from the filing's own stored classification.
    if inquiry_response_expected and ROLE_ANSWER in roles:
        subject = _generic_response_subject(fields)
        if subject is not None:
            return KIND_INQUIRY_RESPONSE
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


def select_summary(text: str, inquiry_response_expected: bool = False) -> tuple[str, str | None]:
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
    selected = field_aware_summary(text, inquiry_response_expected)
    if selected:
        return OUTCOME_SELECTED, selected
    fields = parse_fields(text)
    if (any(f.role in _IDENTIFYING_ROLES for f in fields)
            or _identifying_labels_present(text)
            or (inquiry_response_expected and fields)):
        return OUTCOME_UNSAFE, None
    return OUTCOME_NOT_APPLICABLE, None


def field_aware_summary(text: str, inquiry_response_expected: bool = False) -> str | None:
    """A summary built from recognised fields, or None.

    None means "not positively recognised, or the issuer's own position
    could not be included whole" — the caller must then fall back rather
    than present something partial."""
    if not text or not text.strip():
        return None
    fields = parse_fields(text)
    if not fields:
        return None
    kind = classify(fields, inquiry_response_expected)
    if kind is None:
        return None

    required_role, ordered_roles = _SELECTION[kind]
    by_role = {f.role: f for f in fields if f.body}
    if kind == KIND_INQUIRY_RESPONSE and ROLE_INQUIRY_SUBJECT not in by_role:
        generic = _generic_response_subject(fields)
        if generic is not None:
            by_role[ROLE_INQUIRY_SUBJECT] = generic
    required = by_role.get(required_role)
    if required is None:
        return None

    def rendered(field: _Field) -> str:
        """The document's OWN wording, label included and unaltered — no
        punctuation is inserted and no word is rewritten, so the speaker
        and the qualification stay exactly as the filing states them."""
        return field.raw

    # An issuer statement cut mid-clause is never rendered: the stored
    # field may itself be incomplete (the extraction cap), and a cut here
    # can drop the very negation or qualifier that carries the meaning.
    if required_role in _ISSUER_STATEMENT_ROLES and not _ends_completely(required.body):
        return None

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
