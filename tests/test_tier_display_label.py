"""The tier is called "Early" in the interface and "Watchlist" in the
database (tier_labels).

The redesign gives the reader a personal watchlist of starred
companies, with its own nav block and its own "Watchlist only" filter.
Two unrelated things called "watchlist" on one screen is a defect, and
the tier is the one that can move — the other belongs to the reader.

The stored value does NOT move. "Watchlist" is in the Postgres column
and in every editorial_story_revisions row already written as
"tier:High Signal->Watchlist"; that log exists to be immutable."""
from __future__ import annotations

import pytest

from src.models.daily_news_models import NewsMaterialityTier
from src.ui.components.tier_labels import tier_display_label


def test_the_watchlist_tier_reads_as_early():
    assert tier_display_label(NewsMaterialityTier.WATCHLIST) == "Early"


@pytest.mark.parametrize("tier", [
    NewsMaterialityTier.HIGH_SIGNAL, NewsMaterialityTier.BACKGROUND,
])
def test_every_other_tier_keeps_its_name(tier):
    assert tier_display_label(tier) == tier.value


def test_the_stored_value_is_untouched():
    """If this ever fails, a migration and a rewrite of the revision
    log became necessary and nobody meant them to."""
    assert NewsMaterialityTier.WATCHLIST.value == "Watchlist"


def test_every_tier_has_a_display_name():
    for tier in NewsMaterialityTier:
        assert tier_display_label(tier)


def test_no_tier_badge_renders_the_stored_value_directly():
    """Two modules each built their own badge from `tier.value`.
    Renaming in one and not the other is the failure the shared label
    exists to prevent, so neither may go back to doing it itself."""
    import inspect

    from src.ui.components import editorial_coverage
    from src.ui.pages import daily_news

    for module in (daily_news, editorial_coverage):
        source = inspect.getsource(module._tier_badge_html)
        assert "tier_display_label(tier)" in source, module.__name__
        assert "{tier.value}" not in source, module.__name__


def test_a_missing_tier_renders_as_nothing_rather_than_none():
    assert tier_display_label(None) == ""
