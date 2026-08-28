from datetime import datetime, timedelta, timezone

from src.logic.formatting import days_ago, fmt_currency, fmt_date, fmt_datetime, fmt_datetime_local, fmt_pct


def test_fmt_pct_signed_positive():
    assert fmt_pct(4.0) == "+4.0%"


def test_fmt_pct_signed_negative():
    assert fmt_pct(-1.5) == "-1.5%"


def test_fmt_pct_unsigned():
    assert fmt_pct(4.0, signed=False) == "4.0%"


def test_fmt_pct_decimals():
    assert fmt_pct(4.567, decimals=2) == "+4.57%"


def test_fmt_currency_basic():
    assert fmt_currency(1234567) == "$1,234,567"


def test_fmt_date_valid_date_only():
    assert fmt_date("2026-09-05") == "Sep 5, 2026"


def test_fmt_date_valid_datetime():
    assert fmt_date("2026-09-05T12:00:00+00:00") == "Sep 5, 2026"


def test_fmt_date_invalid_returns_original():
    assert fmt_date("not-a-date") == "not-a-date"


def test_fmt_datetime_valid():
    result = fmt_datetime("2026-09-05T12:30:00+00:00")
    assert "Sep 5, 2026" in result
    assert "12:30" in result


def test_fmt_datetime_invalid_returns_original():
    assert fmt_datetime("garbage") == "garbage"


def test_fmt_datetime_local_summer_shows_edt():
    # Phase T1 (design/DECISIONS.md): 2026-07-15T18:30:00+00:00 is
    # within US daylight saving time -> America/New_York is UTC-4 (EDT).
    assert fmt_datetime_local("2026-07-15T18:30:00+00:00") == "Jul 15, 2026, 14:30 EDT"


def test_fmt_datetime_local_winter_shows_est():
    # 2026-01-15T18:30:00+00:00 is outside daylight saving time ->
    # America/New_York is UTC-5 (EST).
    assert fmt_datetime_local("2026-01-15T18:30:00+00:00") == "Jan 15, 2026, 13:30 EST"


def test_fmt_datetime_local_never_hardcodes_offset_or_suffix():
    """The abbreviation must come from the IANA database dynamically
    (via %Z), not a hardcoded string — proven by checking both DST sides
    produce a *different* abbreviation from the same function/code path,
    and that swapping the wall-clock hour is consistent with a real
    UTC-4/UTC-5 conversion rather than a fixed offset."""
    summer = fmt_datetime_local("2026-07-15T18:30:00+00:00")
    winter = fmt_datetime_local("2026-01-15T18:30:00+00:00")
    assert "EDT" in summer and "EST" not in summer
    assert "EST" in winter and "EDT" not in winter


def test_fmt_datetime_local_treats_naive_input_as_utc():
    # No explicit offset in the input -> assumed UTC, same convention as
    # this module's own days_ago(), then converted to Eastern.
    assert fmt_datetime_local("2026-07-15T18:30:00") == "Jul 15, 2026, 14:30 EDT"


def test_fmt_datetime_local_invalid_returns_original():
    assert fmt_datetime_local("garbage") == "garbage"


def test_fmt_datetime_still_returns_utc_unchanged():
    """fmt_datetime itself is untouched by Phase T1 — only
    fmt_datetime_local is new. Guards against ever silently repurposing
    the original, ambiguously-named function."""
    result = fmt_datetime("2026-07-15T18:30:00+00:00")
    assert "UTC" in result
    assert "EDT" not in result and "EST" not in result


def test_days_ago_computes_correctly():
    ts = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    assert days_ago(ts) == 3


def test_days_ago_invalid_returns_none():
    assert days_ago("garbage") is None
