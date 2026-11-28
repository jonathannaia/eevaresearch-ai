"""What a materiality tier is CALLED in the interface.

The stored tier and the displayed tier are deliberately two different
things. NewsMaterialityTier.WATCHLIST keeps its value — "Watchlist" is
written into the Postgres column, and into every
editorial_story_revisions row already logged as
"tier:High Signal->Watchlist". That log exists to be immutable, so the
rename happens here, at the last possible moment before a reader sees
the word.

WHY RENAME AT ALL. The redesign gives the reader a personal watchlist:
a list of starred companies, with its own nav block and its own "Watchlist
only" filter. Two unrelated things called "watchlist" on the same screen
is a defect, and the tier is the one that can move, because the other
one is the reader's own.

ONE FUNCTION, TWO CALLERS. daily_news.py and editorial_coverage.py each
rendered `{tier.value}` into their own badge. Renaming in one and not
the other is the failure this module exists to prevent."""
from __future__ import annotations

from src.models.daily_news_models import NewsMaterialityTier

#: Display name per tier. Only WATCHLIST differs from its stored value.
TIER_DISPLAY_LABELS: dict[NewsMaterialityTier, str] = {
    NewsMaterialityTier.HIGH_SIGNAL: "High Signal",
    NewsMaterialityTier.WATCHLIST: "Early",
    NewsMaterialityTier.BACKGROUND: "Background",
}


def tier_display_label(tier: NewsMaterialityTier | None) -> str:
    """The reader-facing name. Falls back to the stored value for a
    tier this map does not know, which is visible and wrong-looking
    rather than blank and silent."""
    if tier is None:
        return ""
    return TIER_DISPLAY_LABELS.get(tier, tier.value)
