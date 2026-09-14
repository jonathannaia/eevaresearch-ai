"""Command palette (src/ui/components/command_palette.py) — focused unit
tests for _index()/_matches()/_navigate(), added as part of the Signals
materiality classification pre-push audit (design/DECISIONS.md) to close
a real, pre-existing gap: this module had zero dedicated test coverage
before this file, despite the product-naming separation pass renaming
its "Signals" group to "Radar Signals". Pure-function level — no
AppTest/Streamlit runtime needed for _index()/_matches(); _navigate() is
exercised via monkeypatching, mirroring this codebase's established
pattern for testing st.switch_page call sites without a real app run."""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.models.models import Direction, Horizon, Signal, Strength, Theme
from src.ui.components import command_palette


def _signal(title: str = "Test signal", theme_slug: str = "ai-buildout") -> Signal:
    return Signal(
        id="signal-1", title=title, theme_slug=theme_slug, subtheme_slug=None,
        direction=Direction.EMERGING, strength=Strength.MODERATE, horizon=Horizon.MULTI_WEEK,
        evidence_count=1, interpretation="x", contrary_evidence="x",
        validation_criteria="x", invalidation_criteria="x", related_tickers=[],
        last_updated="2026-01-01T00:00:00+00:00", is_demo=False,
    )


def _theme(name: str = "Test theme") -> Theme:
    return Theme(slug="test-theme", name=name, description="d", subthemes=[])


def _ctx(signals=(), themes=()) -> SimpleNamespace:
    return SimpleNamespace(
        signal_repository=SimpleNamespace(get_all_signals=lambda: list(signals)),
        theme_repository=SimpleNamespace(get_all_themes=lambda: list(themes)),
    )


# --- _index(): group labels ---


def test_radar_derived_signals_are_indexed_under_the_radar_signals_group_not_bare_signals():
    """Product-naming separation (design/DECISIONS.md): the Radar-derived
    signal board's palette group was renamed from "Signals" to "Radar
    Signals" once Daily News claimed the bare "Signals" label for its
    own, unrelated feed. This is the direct regression test for that
    rename at the actual indexing code level — command_palette.py:78."""
    items = command_palette._index(_ctx(signals=[_signal(title="A named signal")]))
    signal_items = [i for i in items if i["label"] == "A named signal"]
    assert len(signal_items) == 1
    assert signal_items[0]["group"] == "Radar Signals"
    assert signal_items[0]["group"] != "Signals"


def test_no_bare_signals_group_exists_anywhere_in_the_index():
    """Direct proof of "no collision": nothing in this module ever
    produces a palette group literally named "Signals" — the Daily News
    rework's own "Signals" page is not indexed by this module at all
    (it has no per-item content to index, unlike Themes/Radar Signals),
    so there is no ambiguity between the two product concepts here."""
    items = command_palette._index(_ctx(
        signals=[_signal(title="Sig A"), _signal(title="Sig B")],
        themes=[_theme(name="Theme A")],
    ))
    groups = {i["group"] for i in items}
    assert "Signals" not in groups
    assert groups == {"Themes", "Radar Signals", "Actions"}


def test_themes_are_indexed_under_the_unchanged_themes_group():
    items = command_palette._index(_ctx(themes=[_theme(name="My Theme")]))
    theme_items = [i for i in items if i["label"] == "My Theme"]
    assert len(theme_items) == 1
    assert theme_items[0]["group"] == "Themes"
    assert theme_items[0]["go"] == "themes"


def test_index_always_includes_the_open_methodology_action():
    items = command_palette._index(_ctx())
    assert {"group": "Actions", "label": "Open Methodology", "sub": "", "go": "methodology"} in items


def test_empty_repositories_produce_only_the_static_action_item():
    items = command_palette._index(_ctx())
    assert len(items) == 1
    assert items[0]["label"] == "Open Methodology"


# --- _matches(): search should still find Radar Signals items by their new group name ---


def test_matches_finds_a_radar_signal_item_by_its_new_group_name():
    item = {"group": "Radar Signals", "label": "Some filing signal", "sub": "ai-buildout", "go": "signals"}
    assert command_palette._matches(item, "radar")
    assert command_palette._matches(item, "Radar Signals")


def test_matches_is_case_insensitive_and_checks_label_sub_and_group():
    item = {"group": "Radar Signals", "label": "Capacity Expansion", "sub": "memory", "go": "signals"}
    assert command_palette._matches(item, "CAPACITY")
    assert command_palette._matches(item, "memory")
    assert command_palette._matches(item, "radar signals")
    assert not command_palette._matches(item, "nonexistent-term")


# --- _navigate(): the route key ("signals") itself is unchanged by the label rename ---


def test_navigate_routes_a_radar_signals_item_to_the_unchanged_signals_page_key(monkeypatch):
    """The label rename (Signals -> Radar Signals) never touched the
    underlying route key or url_path (an explicit, approved decision —
    see src/ui/ui.py's HIDDEN_FROM_NAV comment) — this proves navigation
    for a Radar Signals palette result still resolves through the same
    "signals" key command_palette._index() already emits at
    command_palette.py:78."""
    fake_page = object()
    monkeypatch.setattr(command_palette, "get_page", lambda key: fake_page if key == "signals" else None)
    switch_page_mock = MagicMock()
    monkeypatch.setattr(command_palette.st, "switch_page", switch_page_mock)

    command_palette._navigate({"group": "Radar Signals", "label": "x", "sub": "y", "go": "signals"})

    switch_page_mock.assert_called_once_with(fake_page)


def test_navigate_is_a_safe_no_op_when_the_target_page_is_not_registered(monkeypatch):
    monkeypatch.setattr(command_palette, "get_page", lambda key: None)
    switch_page_mock = MagicMock()
    monkeypatch.setattr(command_palette.st, "switch_page", switch_page_mock)

    command_palette._navigate({"group": "Radar Signals", "label": "x", "sub": "y", "go": "signals"})

    switch_page_mock.assert_not_called()
