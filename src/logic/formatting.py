"""Pure formatting helpers — no Streamlit, no I/O, unit-testable in
isolation. Shared across every page so a percentage or date never gets
formatted two different ways in two different places."""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


def fmt_pct(value: float, decimals: int = 1, signed: bool = True) -> str:
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value:.{decimals}f}%"


def fmt_currency(value: float, decimals: int = 0) -> str:
    return f"${value:,.{decimals}f}"


def fmt_date(iso_str: str) -> str:
    """'2026-09-05' or a full ISO datetime -> 'Sep 5, 2026'. Returns the
    original string unparsed rather than raising on bad input."""
    for parser in (lambda s: datetime.fromisoformat(s), lambda s: datetime.strptime(s, "%Y-%m-%d")):
        try:
            dt = parser(iso_str)
            return f"{dt:%b} {dt.day}, {dt.year}"
        except (ValueError, TypeError):
            continue
    return iso_str


def fmt_datetime(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str)
        return f"{dt:%b} {dt.day}, {dt.year}, {dt:%H:%M} UTC"
    except (ValueError, TypeError):
        return iso_str


_EASTERN = ZoneInfo("America/New_York")


def fmt_datetime_local(iso_str: str, tz: ZoneInfo = _EASTERN) -> str:
    """Display-only Eastern-time conversion (Phase T1, design/
    DECISIONS.md) — a distinctly-named function so a call site is never
    ambiguous about which timezone it renders in, unlike silently
    changing fmt_datetime's own long-standing UTC behavior in place.
    Storage/comparison stays UTC everywhere else in the app; this only
    changes what an already-computed UTC timestamp looks like once it
    becomes a display string. `%Z` reads the correct live abbreviation
    ("EDT"/"EST") from the IANA tzdata database itself, resolved against
    the actual date being formatted — never a hardcoded offset or
    suffix, so it's automatically correct on both sides of a DST
    transition. A naive (no-tzinfo) input is assumed UTC, matching this
    module's own days_ago() convention."""
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone(tz)
        return f"{local_dt:%b} {local_dt.day}, {local_dt.year}, {local_dt:%H:%M} {local_dt:%Z}"
    except (ValueError, TypeError):
        return iso_str


def today_local(now: datetime | None = None) -> date:
    """Today's calendar date in the app's one established display
    timezone (_EASTERN, same convention fmt_datetime_local already uses)
    — never the host process's own OS-local date.today(), and never a
    bare UTC-date truncation, which can be off by a calendar day near a
    US midnight boundary (e.g. 11pm Eastern is already the next day in
    UTC). `now` is injectable so a caller (a UI page picking a date-
    picker's max date, or a test) can pass an explicit UTC instant rather
    than depending on wall-clock time — defaults to datetime.now(utc)
    when omitted, same pattern as radar_freshness.compute_radar_freshness's
    own optional `now` parameter."""
    now = now or datetime.now(timezone.utc)
    return now.astimezone(_EASTERN).date()


def days_ago(iso_str: str) -> int | None:
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    return int((datetime.now(timezone.utc) - dt).total_seconds() // 86400)
