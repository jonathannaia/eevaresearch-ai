"""Structured facts read out of a Korean disclosure's own field table.

WHY THIS EXISTS. A DART card currently says "Supply or Sales Contract
— 단일판매·공급계약체결": the document type, and nothing about the
event. The filing itself is far more specific than that, and it is not
prose — DART major-decision disclosures are FIELD TABLES with fixed
statutory labels, printed into the extracted text in a fixed order:

    1. 판매·공급계약 구분  상품공급
     - 세부내용  크로아티아 천무 다연장로켓 수출사업
    2. 계약내역  계약금액(원) 641,095,389,786  최근매출액(원)
       26,702,901,248,815  매출액대비(%) 2.40  대규모법인여부 해당
    3. 계약상대  크로아티아 국방부 (The Ministry of Defence of the
       Republic of Croatia)
    5. 계약기간  시작일 2026-09-10  종료일 2030-09-09

Reading a labelled number out of that table is not translation and not
interpretation. The labels are fixed strings this module matches
literally; the values are copied out byte for byte. Nothing here
renders Korean prose into English, and nothing here romanises or
translates a name — see counterparty_for_display() for the one place
that question arises and how it is answered without doing either.

"-" IS NOT ZERO AND NOT ABSENT. Korean defence contractors routinely
withhold the contract value as a trade secret and file "-" in
계약금액(원) while still filing 최근매출액(원), the company's OWN
revenue, in the very next field. A parser that treats "-" as missing
and reaches for the next number on the line turns a withheld contract
value into "₩26.7tn". Both observed live, in the same issuer, eleven
days apart. Withheld fields are therefore recorded as withheld, never
dropped, and the caller is expected to say so rather than guess.

AMENDMENTS ARE NOT READ AT ALL. A 정정신고(보고) body is a
정정전/정정후 correction table, not the original field table: the same
labels appear, each with two competing values. Reading one would be a
coin flip between the old figure and the new. is_amendment_body()
detects them and parse_structured_fields() returns None."""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from src.config.tracked_companies import get_tracked_companies

#: A field filed as withheld. DART prints a bare hyphen (ASCII or the
#: full-width form) where a value is not disclosed.
_WITHHELD_VALUES: frozenset[str] = frozenset({"-", "\uff0d", "\u2013", "\u2014", ""})

#: The amendment marker in a corrected disclosure's own body.
_AMENDMENT_MARKERS: tuple[str, ...] = (
    "\uc815\uc815\uc2e0\uace0",       # 정정신고
    "\uc815\uc815\uc804",             # 정정전
    "\uc815\uc815\ud6c4",             # 정정후
    "[\uae30\uc7ac\uc815\uc815]",     # [기재정정]
)

#: 특별배당 — the marker a filing uses to call a dividend special.
_SPECIAL_DIVIDEND = "\ud2b9\ubcc4"

SUPPLY_CONTRACT = "supply_contract"
FACILITY_INVESTMENT = "facility_investment"
CASH_DIVIDEND = "cash_dividend"
TREASURY_ACQUISITION = "treasury_acquisition"
TREASURY_DISPOSAL = "treasury_disposal"
PROVISIONAL_RESULTS = "provisional_results"

#: field key -> the statutory label(s) DART prints, longest first so a
#: label that contains another ("자기자본대비" vs "자기자본") cannot be
#: matched by its own prefix.
_TEMPLATES: dict[str, dict[str, tuple[str, ...]]] = {
    SUPPLY_CONTRACT: {
        "kind": ("\ud310\ub9e4\u00b7\uacf5\uae09\uacc4\uc57d \uad6c\ubd84", "\ud310\ub9e4\u00b7\uacf5\uae09\uacc4\uc57d\uad6c\ubd84"),
        "detail": ("\uccb4\uacb0\uacc4\uc57d\uba85", "\uc138\ubd80\ub0b4\uc6a9"),
        "amount": ("\uacc4\uc57d\uae08\uc561",),
        "recent_revenue": ("\ucd5c\uadfc\ub9e4\ucd9c\uc561",),
        "ratio": ("\ub9e4\ucd9c\uc561\ub300\ube44",),
        "large_corporation": ("\ub300\uaddc\ubaa8\ubc95\uc778\uc5ec\ubd80",),
        "counterparty": ("\uacc4\uc57d\uc0c1\ub300\ubc29", "\uacc4\uc57d\uc0c1\ub300"),
        "relationship": ("\ud68c\uc0ac\uc640\uc758 \uad00\uacc4", "\ud68c\uc0ac\uc640\uc758\uad00\uacc4"),
        "region": ("\ud310\ub9e4\u00b7\uacf5\uae09\uc9c0\uc5ed",),
        "start_date": ("\uc2dc\uc791\uc77c",),
        "end_date": ("\uc885\ub8cc\uc77c",),
    },
    FACILITY_INVESTMENT: {
        "kind": ("\ud22c\uc790\uad6c\ubd84",),
        "detail": ("\ud22c\uc790\ub300\uc0c1",),
        "amount": ("\ud22c\uc790\uae08\uc561",),
        "equity": ("\uc790\uae30\uc790\ubcf8",),
        "ratio": ("\uc790\uae30\uc790\ubcf8\ub300\ube44",),
        "large_corporation": ("\ub300\uaddc\ubaa8\ubc95\uc778\uc5ec\ubd80",),
        "purpose": ("\ud22c\uc790\ubaa9\uc801",),
    },
}

_TEMPLATES[CASH_DIVIDEND] = {
    "kind": ("\ubc30\ub2f9\uad6c\ubd84",),                              # 배당구분
    "per_share": ("1\uc8fc\ub2f9 \ubc30\ub2f9\uae08", "1\uc8fc\ub2f9\ubc30\ub2f9\uae08"),     # 1주당 배당금
    "ratio": ("\uc2dc\uac00\ubc30\ub2f9\ub960", "\uc2dc\uac00\ubc30\ub2f9\ub960(%)"),        # 시가배당률
    "amount": ("\ubc30\ub2f9\uae08\ucd1d\uc561",),                     # 배당금총액
    "record_date": ("\ubc30\ub2f9\uae30\uc900\uc77c",),                # 배당기준일
}
_TEMPLATES[TREASURY_ACQUISITION] = {
    "shares": ("\ucde8\ub4dd\uc608\uc815\uc8fc\uc2dd",),              # 취득예정주식
    "amount": ("\ucde8\ub4dd\uc608\uc815\uae08\uc561",),              # 취득예정금액
    "start_date": ("\uc2dc\uc791\uc77c",),
    "end_date": ("\uc885\ub8cc\uc77c",),
}
_TEMPLATES[TREASURY_DISPOSAL] = {
    "shares": ("\ucc98\ubd84\uc608\uc815\uc8fc\uc2dd",),              # 처분예정주식
    "amount": ("\ucc98\ubd84\uc608\uc815\uae08\uc561",),              # 처분예정금액
    "start_date": ("\uc2dc\uc791\uc77c",),
    "end_date": ("\uc885\ub8cc\uc77c",),
}

#: Which label identifies each template: the AMOUNT field, AS THE
#: TABLE PRINTS IT, followed by its "(\uc6d0)" unit. Samsung's
#: \uc7a5\ub798\uc0ac\uc5c5\u00b7\uacbd\uc601\uacc4\ud68d disclosure mentions \ud22c\uc790\uae08\uc561 in running
#: prose and was detected as a facility-investment table on that alone.
#: It parsed to nothing and produced no headline, so nothing wrong
#: reached a card — but a template this module cannot actually read
#: should not be claimed in the first place.
_TEMPLATE_SIGNATURES: tuple[tuple[str, str], ...] = (
    (SUPPLY_CONTRACT, "\uacc4\uc57d\uae08\uc561(\uc6d0)"),
    (FACILITY_INVESTMENT, "\ud22c\uc790\uae08\uc561(\uc6d0)"),
    (CASH_DIVIDEND, "\ubc30\ub2f9\uae08\ucd1d\uc561(\uc6d0)"),
    (TREASURY_ACQUISITION, "\ucde8\ub4dd\uc608\uc815\uae08\uc561(\uc6d0)"),
    (TREASURY_DISPOSAL, "\ucc98\ubd84\uc608\uc815\uae08\uc561(\uc6d0)"),
)

#: Curated English for each template's two headline numbers. A hand
#: translation of a fixed statutory label, written once — the same
#: discipline as filing_display's category tables, and the only English
#: this module introduces.
AMOUNT_LABELS: dict[str, str] = {
    SUPPLY_CONTRACT: "contract value",
    FACILITY_INVESTMENT: "investment",
    CASH_DIVIDEND: "dividend",
    TREASURY_ACQUISITION: "buyback",
    TREASURY_DISPOSAL: "disposal",
    PROVISIONAL_RESULTS: "revenue",
}
RATIO_LABELS: dict[str, str] = {
    SUPPLY_CONTRACT: "of recent revenue",
    FACILITY_INVESTMENT: "of equity",
    CASH_DIVIDEND: "dividend yield",
    PROVISIONAL_RESULTS: "year on year",
}

#: "(원)" won, "(%)" percent, "(주)" shares — the unit DART prints
#: as part of the label itself.
_UNIT_SUFFIX_RE = re.compile(r"^\s*\((?:\uc6d0|%|\uc8fc)\)\s*")
#: DART numbers its sections "1. " "2. " "3. ". A value ends where the
#: next numbered section begins, even when that section's own label is
#: not one this module tracks — otherwise 판매·공급지역 reads as
#: "크로아티아 5. 계약기간". The trailing space is required, so a
#: decimal ratio ("2.40") and an ISO date are never mistaken for one.
_SECTION_MARKER_RE = re.compile(r"\s\d{1,2}\.\s")
_NUMBER_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?$")
_LATIN_RE = re.compile(r"[A-Za-z]")
_HANGUL_RE = re.compile(r"[\uac00-\ud7a3]")
#: A parenthesised run with no Hangul in it — the filing's own English
#: rendering of a counterparty, which many DART filings print.
_ENGLISH_PARENTHETICAL_RE = re.compile(r"\(([^()\uac00-\ud7a3]{3,120})\)")


@dataclass(frozen=True)
class StructuredFilingFacts:
    """What the field table said. Every string is copied from the
    filing; nothing is rephrased.

    `withheld` names the fields the filing printed as "-", so a caller
    can distinguish "the contract is worth nothing we can show" from
    "we did not look"."""

    template: str
    kind: str | None = None
    amount_won: int | None = None
    ratio_percent: Decimal | None = None
    counterparty: str | None = None
    per_share_won: int | None = None
    operating_profit_won: int | None = None
    operating_profit_yoy: Decimal | None = None
    record_date: str | None = None
    shares: int | None = None
    detail: str | None = None
    region: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    withheld: tuple[str, ...] = ()

    @property
    def amount_label(self) -> str:
        return AMOUNT_LABELS[self.template]

    @property
    def ratio_label(self) -> str:
        return RATIO_LABELS[self.template]

    @property
    def is_special_dividend(self) -> bool:
        """True when the filing itself calls this a special dividend.

        Currently never true in this corpus: every dividend filing in
        the store declares 배당구분 분기배당 (quarterly), including
        Samsung's ₩4,601-per-share one, which a reader would plainly
        call special against its own ₩374 quarterly. The filing does
        not say so, and this module does not infer it — comparing an
        issuer's dividend against its own previous filing is a
        cross-filing question, not a field-table one."""
        return self.template == CASH_DIVIDEND and _SPECIAL_DIVIDEND in (self.kind or "")

    @property
    def has_headline_numbers(self) -> bool:
        return self.amount_won is not None or self.ratio_percent is not None


def is_amendment_body(text: str) -> bool:
    """True for a 정정신고(보고) correction table.

    Its labels are the originals', each carrying a 정정전 (before) and a
    정정후 (after) value, so picking "the" value is a coin flip between
    a superseded figure and a current one."""
    return any(marker in (text or "") for marker in _AMENDMENT_MARKERS)


def _normalise(text: str) -> str:
    return " ".join((text or "").split())


def _label_positions(text: str, template: dict[str, tuple[str, ...]]) -> list[tuple[int, int, str]]:
    """(start, end, field_key) for every label found, in document
    order. Longer labels win over shorter ones that start at the same
    place, so 자기자본대비 is never read as 자기자본."""
    found: list[tuple[int, int, str]] = []
    for key, labels in template.items():
        for label in labels:
            index = text.find(label)
            if index >= 0:
                found.append((index, index + len(label), key))
                break
    found.sort()
    deduped: list[tuple[int, int, str]] = []
    for start, end, key in found:
        if deduped and start < deduped[-1][1]:
            # Overlapping labels: keep whichever reaches further right,
            # which is the longer, more specific one.
            if end > deduped[-1][1]:
                deduped[-1] = (deduped[-1][0], end, key)
            continue
        deduped.append((start, end, key))
    return deduped


def _value_after(text: str, end: int, next_start: int | None) -> str:
    raw = text[end : next_start if next_start is not None else len(text)]
    raw = _UNIT_SUFFIX_RE.sub("", raw)
    section = _SECTION_MARKER_RE.search(raw)
    if section is not None:
        raw = raw[: section.start()]
    # A trailing bullet belongs to the NEXT field's label, not to this
    # value: "계약상대 <name> - 회사와의 관계" puts the hyphen that
    # introduces the following line at the end of the name.
    raw = re.sub(r"\s+[-\u2013\u2014\uff0d]\s*$", "", raw)
    return raw.strip(" \t:\u00b7")


#: A share class printed between a numeric label and its value —
#: "취득예정금액(원) 보통주식 14,999,999,992,000 기타주식 -". The first
#: class is the one the headline reports; the rest sit behind it.
_SHARE_CLASS_SUBLABELS: tuple[str, ...] = (
    "\ubcf4\ud1b5\uc8fc\uc2dd",   # 보통주식 common
    "\uc885\ub958\uc8fc\uc2dd",   # 종류주식 class
    "\uc6b0\uc120\uc8fc\uc2dd",   # 우선주식 preferred
    "\uae30\ud0c0\uc8fc\uc2dd",   # 기타주식 other
    "\ub2f9\ud574\uc2e4\uc801",   # 당해실적 this period
)


def _first_numeric_token(span: str) -> str | None:
    """The value of a numeric field, reading past any share-class
    sub-label. None when the field was withheld.

    Returns the FIRST token rather than scanning for any number in the
    span, deliberately: scanning is what turns a withheld contract
    value into the company's own revenue two fields later."""
    for token in span.split():
        if token in _SHARE_CLASS_SUBLABELS:
            continue
        return token
    return None


def _as_int(value: str) -> int | None:
    if not _NUMBER_RE.match(value):
        return None
    try:
        return int(Decimal(value.replace(",", "")))
    except (InvalidOperation, ValueError):
        return None


def _as_decimal(value: str) -> Decimal | None:
    if not _NUMBER_RE.match(value):
        return None
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation:
        return None


def detect_template(text: str) -> str | None:
    for template, signature in _TEMPLATE_SIGNATURES:
        if signature in text:
            return template
    return None


def parse_structured_fields(excerpt: str | None) -> StructuredFilingFacts | None:
    """The filing's own field values, or None.

    None for an empty excerpt, an amendment body, or a document whose
    field table this module does not know. Never a partial guess from a
    document it could not identify."""
    text = _normalise(excerpt or "")
    if not text or is_amendment_body(text):
        return None
    template = detect_template(text)
    if template is None:
        # The one table-shaped disclosure, tried last so a label:value
        # template is always preferred where one exists.
        return parse_provisional_results(text)

    positions = _label_positions(text, _TEMPLATES[template])
    values: dict[str, str] = {}
    for index, (_, end, key) in enumerate(positions):
        next_start = positions[index + 1][0] if index + 1 < len(positions) else None
        values[key] = _value_after(text, end, next_start)

    withheld = tuple(
        key for key in ("amount", "ratio", "counterparty", "detail")
        if key in values
        and (_first_numeric_token(values[key]) or values[key].strip()) in _WITHHELD_VALUES
    )

    def present(key: str) -> str | None:
        value = values.get(key, "").strip()
        return None if value in _WITHHELD_VALUES else value

    def numeric(key: str) -> str | None:
        span = values.get(key)
        if span is None:
            return None
        token = _first_numeric_token(span)
        return None if token is None or token in _WITHHELD_VALUES else token

    amount_text = numeric("amount")
    ratio_text = numeric("ratio")
    per_share_text = numeric("per_share")
    shares_text = numeric("shares")
    return StructuredFilingFacts(
        template=template,
        kind=present("kind"),
        amount_won=_as_int(amount_text) if amount_text else None,
        ratio_percent=_as_decimal(ratio_text) if ratio_text else None,
        per_share_won=_as_int(per_share_text) if per_share_text else None,
        shares=_as_int(shares_text) if shares_text else None,
        counterparty=present("counterparty"),
        detail=present("detail"),
        region=present("region"),
        start_date=present("start_date"),
        end_date=present("end_date"),
        record_date=present("record_date"),
        withheld=withheld,
    )


def _tracked_english_name(native: str) -> str | None:
    """The registry's English name for a company filed under its Korean
    name — resolved on TrackedCompany.native_name, never by
    transliterating anything.

    Currently inert: native_name is empty for all twelve DART-tracked
    companies, so this returns None for every input until that column
    is populated from an authoritative source. Written anyway because
    the alternative — matching on a romanisation this module invents —
    is the thing that must never happen."""
    stripped = native.strip()
    if not stripped:
        return None
    for company in get_tracked_companies():
        if company.native_name and company.native_name.strip() == stripped:
            return company.name
    return None


@dataclass(frozen=True)
class CounterpartyName:
    """`as_filed` is True when `text` is the filing's own Korean, which
    the caller must label as such rather than present as English."""

    text: str
    as_filed: bool


def counterparty_for_display(value: str | None) -> CounterpartyName | None:
    """The counterparty as it may be shown, and whether it is Korean.

    Four cases, in order, and none of them involves this module
    producing a name of its own:

      1. the registry already knows the company in English
      2. the filing itself prints an English rendering in brackets —
         "크로아티아 국방부 (The Ministry of Defence of the Republic of
         Croatia)" — which is the FILING's English, not ours
      3. the filed name is already Latin script, as many foreign
         counterparties are
      4. otherwise the Korean exactly as filed, flagged so the card can
         say so

    There is deliberately no romanisation step and no fifth case."""
    raw = (value or "").strip()
    if not raw:
        return None

    tracked = _tracked_english_name(raw)
    if tracked:
        return CounterpartyName(tracked, as_filed=False)

    english = _ENGLISH_PARENTHETICAL_RE.search(raw)
    if english and _LATIN_RE.search(english.group(1)):
        return CounterpartyName(english.group(1).strip(), as_filed=False)

    if not _HANGUL_RE.search(raw):
        return CounterpartyName(raw, as_filed=False)

    return CounterpartyName(raw, as_filed=True)


# ============================================================
# Rendering
# ============================================================

#: Won is denominated small — a mid-size contract runs to twelve
#: digits — so an exact figure cannot sit in a headline:
#: "₩641,095,389,786 supply contract" is unreadable. The headline
#: carries a rounded figure and the card carries the exact one, which
#: is why exact_won() exists alongside this.
_SCALES: tuple[tuple[int, str], ...] = (
    (1_000_000_000_000, "tn"),
    (1_000_000_000, "bn"),
    (1_000_000, "m"),
)
_WON = "₩"


def exact_won(amount: int) -> str:
    """The figure exactly as filed, grouped. For the card body, never
    the headline."""
    return f"{_WON}{amount:,}"


def rounded_won(amount: int) -> str:
    """A rounded figure for the headline — DERIVED, not verbatim, which
    is the one place this module's output is not a copy of the filing.
    Always paired with exact_won() on the card so the filed figure is
    never only available by rounding backwards."""
    for scale, suffix in _SCALES:
        if abs(amount) >= scale:
            value = Decimal(amount) / Decimal(scale)
            return f"{_WON}{value.quantize(Decimal('0.1'))}{suffix}"
    return exact_won(amount)


#: What the card calls each template's event. Curated English for a
#: fixed statutory document type, same as filing_display's tables.
EVENT_LABELS: dict[str, str] = {
    SUPPLY_CONTRACT: "supply contract",
    FACILITY_INVESTMENT: "new facility investment",
    CASH_DIVIDEND: "dividend",
    TREASURY_ACQUISITION: "treasury share buyback",
    TREASURY_DISPOSAL: "treasury share disposal",
    PROVISIONAL_RESULTS: "provisional revenue",
}

AS_FILED_SUFFIX = " (as filed)"

#: What the card says when the issuer filed "-" for the figure. A
#: filing that withholds its value but NAMES ITS COUNTERPARTY is still
#: worth a headline: who the contract is with is the news, and
#: "withheld" is the honest label for the part the issuer chose not to
#: disclose. Observed live on Hanwha Aerospace's 2026-08-31 contract
#: with INDRA SISTEMAS S.A., where both figures were "-" and the
#: counterparty was named in full.
_WITHHELD_NOTES: dict[str, str] = {
    SUPPLY_CONTRACT: "contract value withheld",
    FACILITY_INVESTMENT: "investment value withheld",
    CASH_DIVIDEND: "dividend total withheld",
    TREASURY_ACQUISITION: "buyback value withheld",
    TREASURY_DISPOSAL: "disposal value withheld",
}


def structured_headline(company: str, facts: StructuredFilingFacts) -> str | None:
    """A headline built from the field table, or None when there is
    nothing in it worth putting on a card.

    A filing that withholds its figures but NAMES ITS COUNTERPARTY
    still gets one — "Hanwha Aerospace Co., Ltd. — supply contract
    with INDRA SISTEMAS S.A. (contract value withheld)". Who the
    contract is with is the news, and it is strictly more than the
    document-type card this would otherwise fall back to.

    Only four things ever reach it: the company name the registry
    already resolved, a curated English label for a statutory document
    type, a number copied from the filing, and a counterparty name that
    is either the registry's, the filing's own English, or the filing's
    Korean marked "(as filed)". No Korean prose is translated and no
    name is romanised — 투자대상 ("청주 M17 건설") is deliberately left
    out of the headline for that reason, and belongs on the card as
    filed."""
    event = EVENT_LABELS[facts.template]
    counterparty = counterparty_for_display(facts.counterparty)
    if facts.amount_won is None and facts.ratio_percent is None and counterparty is None:
        # No figures and nobody named: the field table said nothing the
        # document type has not already said.
        return None

    lead = f"{rounded_won(facts.amount_won)} {event}" if facts.amount_won is not None else event

    if counterparty is not None:
        name = counterparty.text + (AS_FILED_SUFFIX if counterparty.as_filed else "")
        lead = f"{lead} with {name}"

    # Per-share and share-count sit next to the total rather than
    # replacing it: a dividend reader wants both the cheque and the
    # rate, and a buyback reader wants both the spend and the float.
    if facts.per_share_won is not None:
        lead = f"{lead}, {exact_won(facts.per_share_won)} per common share"
    elif facts.shares is not None:
        lead = f"{lead} ({facts.shares:,} shares)"

    if facts.ratio_percent is not None:
        # A growth rate reads as a change and carries its sign; a share
        # of revenue or of equity is a proportion and does not.
        if facts.template == PROVISIONAL_RESULTS:
            sign = "+" if facts.ratio_percent >= 0 else ""
            lead = f"{lead} ({sign}{facts.ratio_percent}% {facts.ratio_label})"
        else:
            lead = f"{lead} ({facts.ratio_percent}% {facts.ratio_label})"
    elif facts.amount_won is None and "amount" in facts.withheld:
        lead = f"{lead} ({_WITHHELD_NOTES[facts.template]})"

    if facts.operating_profit_won is not None:
        if facts.operating_profit_won < 0:
            # A percentage change on a loss is ambiguous without the
            # filing's own sign convention — 두산로보틱스 filed "12.2"
            # against a loss that narrowed — so the figure is named a
            # loss and the percentage is left off.
            lead = f"{lead}, {rounded_won(abs(facts.operating_profit_won))} operating loss"
        elif facts.operating_profit_yoy is not None:
            sign = "+" if facts.operating_profit_yoy >= 0 else ""
            lead = (
                f"{lead}, {rounded_won(facts.operating_profit_won)} operating profit "
                f"({sign}{facts.operating_profit_yoy}%)"
            )
        else:
            lead = f"{lead}, {rounded_won(facts.operating_profit_won)} operating profit"

    return f"{company} — {lead}"


EXACT_NOTE_LABELS: dict[str, str] = {
    SUPPLY_CONTRACT: "Contract value",
    FACILITY_INVESTMENT: "Investment",
    CASH_DIVIDEND: "Dividend total",
    TREASURY_ACQUISITION: "Buyback value",
    TREASURY_DISPOSAL: "Disposal value",
    PROVISIONAL_RESULTS: "Revenue",
}


def exact_figure_note(facts: StructuredFilingFacts) -> str | None:
    """The filed figure in full, for the card body.

    The headline carries a rounded figure because twelve digits of won
    cannot sit in one — this is the other half of that bargain, so the
    exact number the issuer filed is always on the card and never only
    recoverable by rounding backwards. None when there was no figure
    to begin with."""
    if facts.amount_won is None:
        return None
    label = EXACT_NOTE_LABELS.get(facts.template, "Value")
    note = f"{label} as filed: {exact_won(facts.amount_won)}."
    if facts.start_date and facts.end_date:
        note = f"{note} Term: {facts.start_date} to {facts.end_date}."
    if facts.record_date:
        note = f"{note} Record date: {facts.record_date}."
    return note


# ============================================================
# Provisional results (연결재무제표기준영업(잠정)실적)
# ============================================================
#
# THE ONE TEMPLATE THAT IS NOT LABEL:VALUE. This disclosure prints a
# TABLE — a header row of period columns, then one row per line item:
#
#   1. 연결실적내용 단위 : 백만원, %
#   구분 당기실적 전기실적 전기대비 증감율(%) 전년동기실적 전년동기대비 증감율(%)
#   매출액 당해실적 6,367,124 5,527,241 15.2 - 5,369,431 18.6 - 누계실적 17,429,200
#
# Positional reading of that row is exactly the kind of thing that
# silently returns the wrong column when a filing carries one extra
# "-" or omits a period. So this reads ONE cell: the first number in
# the 매출액 row, which is 당기실적 — revenue for the period just
# reported. The year-on-year percentage sits four columns further
# along and is deliberately NOT read; it is worth more than the
# revenue figure to a reader and is the single most likely cell to
# come back wrong, so it waits for a verified column contract rather
# than an inferred one.
#
# THE COLUMN CONTRACT IS CHECKED, NOT ASSUMED. The year-on-year
# percentage sits six tokens along the 매출액 row, and reading it by
# position alone is what returns the wrong column when a filing
# carries one extra "-". Two real filings establish the shape —
#
#   LG Innotek  6,367,124  5,527,241  15.2  -  5,369,431  18.6  -
#   SK Hynix   79,318,746 52,576,287  50.9  -  22,231,952 256.8  -
#
#   당기실적  전기실적  전기대비%  흑자적자  전년동기실적  전년동기대비%  흑자적자
#
# — and _year_on_year_percent() re-checks that exact shape on every
# filing before reading the sixth cell: three numbers, a non-number,
# two numbers, a non-number. Anything else and the percentage is
# dropped while the revenue figure, which is the first cell and needs
# no contract, is kept.
#
# THE UNIT IS READ, NOT ASSUMED. The filing declares "단위 : 백만원"
# and the cell is in millions of won. A filing that declares any other
# unit, or none, is skipped rather than scaled by a guess — a wrong
# multiplier here is a thousand-fold error in a headline.

_RESULTS_TABLE_HEADING = "연결실적내용"      # 연결실적내용
_RESULTS_UNIT_MILLIONS = "백만원"                        # 백만원
_RESULTS_REVENUE_ROW = "매출액"                          # 매출액
_RESULTS_THIS_PERIOD = "당해실적"                    # 당해실적
_RESULTS_OPERATING_ROW = "\uc601\uc5c5\uc774\uc775"   # 영업이익
_MILLION = 1_000_000


def parse_provisional_results(excerpt: str | None) -> StructuredFilingFacts | None:
    """Revenue for the period just reported, or None.

    Every guard below has to hold: the table heading, the declared
    unit, the revenue row, its 당해실적 marker, and a number straight
    after it. Any one missing and this returns None rather than
    reading a neighbouring cell."""
    text = _normalise(excerpt or "")
    if not text or is_amendment_body(text):
        return None
    if _RESULTS_TABLE_HEADING not in text or _RESULTS_UNIT_MILLIONS not in text:
        return None

    row = text.find(_RESULTS_REVENUE_ROW)
    if row < 0:
        return None
    marker = text.find(_RESULTS_THIS_PERIOD, row)
    if marker < 0:
        return None
    token = _first_numeric_token(text[marker + len(_RESULTS_THIS_PERIOD):])
    if token is None or token in _WITHHELD_VALUES:
        return None
    millions = _as_int(token)
    if millions is None:
        return None

    operating, operating_yoy = _operating_profit(text)
    return StructuredFilingFacts(
        template=PROVISIONAL_RESULTS,
        amount_won=millions * _MILLION,
        ratio_percent=_year_on_year_percent(text[marker + len(_RESULTS_THIS_PERIOD):]),
        operating_profit_won=operating,
        operating_profit_yoy=operating_yoy,
    )


def _operating_profit(text: str) -> tuple[int | None, Decimal | None]:
    """The 영업이익 row, read with the same contract as the revenue
    row. (None, None) when the row is absent or truncated — the
    excerpt is capped and this row sits below the revenue one, so it
    is the first thing a cap removes. Revenue is never affected by
    this returning nothing.

    The figure is signed: 두산로보틱스 filed -13,404 for the quarter,
    which is an operating LOSS, and dropping the sign would turn it
    into a profit."""
    row = text.find(_RESULTS_OPERATING_ROW)
    if row < 0:
        return None, None
    marker = text.find(_RESULTS_THIS_PERIOD, row)
    if marker < 0:
        return None, None
    rest = text[marker + len(_RESULTS_THIS_PERIOD):]
    token = _first_numeric_token(rest)
    if token is None or token in _WITHHELD_VALUES:
        return None, None
    millions = _as_int(token)
    if millions is None:
        return None, None
    return millions * _MILLION, _year_on_year_percent(rest)


#: this | prior quarter | QoQ% | swing flag | year-ago | YoY% | swing flag
_RESULTS_ROW_SHAPE: tuple[bool, ...] = (True, True, True, False, True, True, False)
_YEAR_ON_YEAR_COLUMN = 5


def _year_on_year_percent(row: str) -> Decimal | None:
    """The 전년동기대비 증감율 cell, or None when the row does not have
    the exact shape the contract above describes.

    Dropping the percentage costs a reader some context. Reading the
    wrong cell tells them a company grew 50.9% when it grew 256.8%,
    which is worse than saying nothing."""
    tokens = row.split()[: len(_RESULTS_ROW_SHAPE)]
    if len(tokens) < len(_RESULTS_ROW_SHAPE):
        return None
    for token, should_be_numeric in zip(tokens, _RESULTS_ROW_SHAPE):
        if bool(_NUMBER_RE.match(token)) is not should_be_numeric:
            return None
    return _as_decimal(tokens[_YEAR_ON_YEAR_COLUMN])
