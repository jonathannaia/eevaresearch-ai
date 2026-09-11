"""Pure aggregation logic for the Dashboard's Recent Theme Activity rollup
(design/DECISIONS.md) — supersedes the Market Map tile grid (commit
ee685cb, "Redesign Market Map as a grouped tile mosaic") as the
Dashboard's theme-level module, cleanly rather than by reverting that
commit: the tile-mosaic code stays available in git history, this module
and its caller simply replace what the Dashboard renders.

Groups already-loaded, already-real filing/Daily News items by their
existing, reliably-populated `theme_slug` field — set identically at
scan/discovery time in every provider's own scan_service.py /
daily_news_pipeline.py, always `company.themes[0]` (see
src/config/tracked_companies.py's own primary-theme convention) — within
a fixed recency window. This module decides ONLY: which items qualify
(within the window), how they group (by theme_slug), and in what order
the resulting theme rows are shown (recency, then count, then name). No
price, market cap, sentiment, materiality, or importance score is
computed or implied anywhere here; count and recency are the only
signals this module ever produces, and both are presented as observed
activity, never as ranked importance — see build_recent_theme_activity's
own docstring for the exact ordering rule.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable


@dataclass(frozen=True)
class ThemeActivityItem:
    """One already-resolved, already-real filing or Daily News item,
    carrying only what the rollup needs. `timestamp` must already be a
    timezone-aware datetime (the same "naive input assumed UTC"
    convention src.logic.formatting and src.ui.components.
    recently_updated already use) — resolving a raw source timestamp is
    the caller's job, not this pure function's."""

    theme_slug: str
    company_name: str
    item_type: str  # "Filing" | "Daily News" — verbatim, never inferred beyond what the caller already knows
    timestamp: datetime
    display_date: str
    source_url: str | None


@dataclass(frozen=True)
class ThemeActivityRow:
    theme_slug: str
    theme_name: str
    count: int
    most_recent: ThemeActivityItem


def build_recent_theme_activity(
    items: Iterable[ThemeActivityItem],
    theme_names_by_slug: dict[str, str],
    *,
    now: datetime | None = None,
    window_days: int = 14,
    max_rows: int = 5,
) -> list[ThemeActivityRow]:
    """Filters `items` to the last `window_days` (relative to `now`,
    defaulting to the real current UTC time — injectable for
    deterministic tests, the same `now: datetime | None = None`
    convention already used by src/logic/radar_freshness.py and
    src/logic/formatting.py), groups the survivors by `theme_slug`, and
    returns at most `max_rows` ThemeActivityRow objects.

    Ordering (exact, no other tiebreak):
      1. The row's most-recent qualifying item's timestamp, descending.
      2. The row's qualifying item count, descending.
      3. The row's theme display name, alphabetically.

    Fails closed per theme: a `theme_slug` absent from
    `theme_names_by_slug` is dropped entirely — no row is ever shown
    with an invented, guessed, or missing theme name, and no membership
    relationship beyond the literal theme_slug match is assumed."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)

    qualifying = [item for item in items if item.timestamp >= cutoff]

    by_slug: dict[str, list[ThemeActivityItem]] = {}
    for item in qualifying:
        by_slug.setdefault(item.theme_slug, []).append(item)

    rows: list[ThemeActivityRow] = []
    for theme_slug, group in by_slug.items():
        theme_name = theme_names_by_slug.get(theme_slug)
        if not theme_name:
            continue  # fail closed — never an invented or guessed theme label
        most_recent = max(group, key=lambda i: i.timestamp)
        rows.append(ThemeActivityRow(theme_slug=theme_slug, theme_name=theme_name, count=len(group), most_recent=most_recent))

    # Stable sort applied lowest-priority-first: each later sort call
    # re-orders only ties left by the previous one, so the net effect is
    # exactly the three-level rule documented above, primary key last.
    rows.sort(key=lambda r: r.theme_name)
    rows.sort(key=lambda r: r.count, reverse=True)
    rows.sort(key=lambda r: r.most_recent.timestamp, reverse=True)
    return rows[:max_rows]
