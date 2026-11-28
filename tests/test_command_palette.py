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
    # "Companies" is back (UI redesign item 1), and "Themes" is the real
    # tag taxonomy rather than demo rows — see below.
    assert groups == {"Companies", "Themes", "Radar Signals", "Actions", "Switch theme"}


def test_the_themes_group_is_the_real_taxonomy_and_never_demo_rows():
    """This group used to read ctx.theme_repository, which is
    DemoThemeRepository (src/data_access/container.py) — demo data,
    routed at the Research Theses page, which has nothing to do with it.
    It now reads the registry-backed taxonomy and routes at the Themes
    index, so a demo row can no longer reach the palette at all."""
    from src.logic.theme_taxonomy import company_counts_by_theme, theme_display_name

    # A demo theme named something unmistakable must NOT appear, even
    # when the context supplies one.
    items = command_palette._index(_ctx(themes=[_theme(name="A Demo Theme Row")]))
    assert "A Demo Theme Row" not in [i["label"] for i in items]

    theme_items = [i for i in items if i["group"] == "Themes"]
    counts = company_counts_by_theme()
    assert [i["label"] for i in theme_items] == [theme_display_name(s) for s in counts]
    assert {i["go"] for i in theme_items} == {"theme_index"}
    assert theme_items[0]["sub"] == f"{list(counts.values())[0]} companies"
    # Nothing in the palette routes at the Research Theses page any more;
    # indexing published theses is a separate capability.
    assert "themes" not in {i["go"] for i in items}


def test_every_tracked_company_is_searchable_by_name_and_by_ticker():
    """UI redesign item 1: "⌘K/search by ticker or company". The index
    reads the real issuer registry, and _matches() has to find a company
    from either field."""
    from src.config.issuer_registry import SEED_ISSUERS

    items = command_palette._index(_ctx())
    company_items = [i for i in items if i["group"] == "Companies"]
    assert len(company_items) == len(SEED_ISSUERS)
    assert [i["go"] for i in company_items] == ["companies"] * len(SEED_ISSUERS)

    nvidia = next(i for i in company_items if i["label"] == "NVIDIA")
    assert command_palette._matches(nvidia, "nvidia")
    assert command_palette._matches(nvidia, "NVDA"), "ticker search must work"

    # The 13 EDINET issuers carry a native name, which joins `sub` so a
    # search in Japanese finds them too. Nothing is romanized: an issuer
    # without a native name in the registry simply has none here.
    with_native = [i for i in SEED_ISSUERS if i.native_name]
    assert with_native
    softbank = next(i for i in company_items if i["label"] == with_native[0].legal_name)
    assert command_palette._matches(softbank, with_native[0].native_name)


def test_the_filings_action_exists_while_filings_is_off_the_rail():
    """radar_inbox left the sidebar in item 1 and is not merged into the
    Feed until item 2, so this action is the one discoverable path to it
    in the meantime. Remove it when the Feed actually carries filings —
    until then it must stay."""
    items = command_palette._index(_ctx())
    filings = [i for i in items if i["go"] == "radar_inbox"]
    assert len(filings) == 1
    assert filings[0] == {"group": "Actions", "label": "Open Filings", "sub": "Radar filing list", "go": "radar_inbox"}


def test_results_are_capped_per_group_so_no_group_is_pushed_out_of_sight():
    """With 129 companies in the index, the former flat shown[:25] slice
    filled the whole dialog with companies and hid Research Theses,
    Radar Signals and every action whenever the query was empty."""
    rows = [{"group": "Companies", "label": f"C{n}", "sub": "", "go": "companies"} for n in range(40)]
    rows += [{"group": "Actions", "label": "Open Filings", "sub": "", "go": "radar_inbox"}]
    rows += [{"group": "Switch theme", "label": "Switch theme: Dark", "sub": "", "go": "theme:dark"}]

    capped = command_palette._capped_by_group(rows)
    companies = [i for i in capped if i["group"] == "Companies" and i["go"]]
    assert len(companies) == command_palette._PER_GROUP_LIMIT
    # The held-back count is stated rather than silently dropped, and the
    # marker carries no destination so it renders as text, not a button.
    marker = next(i for i in capped if not i["go"])
    assert marker["sub"] == f"+{40 - command_palette._PER_GROUP_LIMIT} more — narrow the search"
    # Every other group survives the cap.
    assert {"Actions", "Switch theme"} <= {i["group"] for i in capped}


def test_theme_switch_commands_are_never_capped():
    """They are commands, not search results: however many companies
    match, the theme switcher stays reachable."""
    rows = [{"group": "Switch theme", "label": f"Switch theme: {n}", "sub": "", "go": f"theme:{n}"} for n in range(20)]
    capped = command_palette._capped_by_group(rows)
    assert len(capped) == 20


def test_index_always_includes_the_open_methodology_action():
    items = command_palette._index(_ctx())
    assert {"group": "Actions", "label": "Open Methodology", "sub": "", "go": "methodology"} in items


def test_empty_repositories_produce_only_companies_actions_and_theme_commands():
    """With no themes and no signals persisted, what remains is the
    static registry-backed Companies group plus the fixed commands —
    never an invented row."""
    items = command_palette._index(_ctx())
    from src.logic.theme_taxonomy import company_counts_by_theme, theme_display_name

    non_company = [i["label"] for i in items if i["group"] != "Companies"]
    non_company = [l for l in non_company if l not in
                   {theme_display_name(s) for s in company_counts_by_theme()}]
    assert non_company == [
        "Open Filings", "Open Methodology",
        "Switch theme: System", "Switch theme: Dark", "Switch theme: Light",
    ]
    assert [i["go"] for i in items if i["go"].startswith("theme:")] == [
        "theme:system", "theme:dark", "theme:light",
    ]


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
