"""UI redesign item 1 — the two new reader-facing index pages
(src/ui/pages/companies.py, src/ui/pages/theme_index.py) and the theme
tag taxonomy they read from (src/logic/theme_taxonomy.py).

What these tests are actually for: both pages render real registry data
and nothing else. The failure mode worth guarding is not a broken
layout, it is a page that quietly shows something it cannot back up —
a romanized company name, a discovery stub presented as covered, or a
per-theme count that looks like an event count while omitting every
filing.
"""
from __future__ import annotations

from html import escape
from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.config.issuer_registry import DISCOVERY_STUBS, SEED_ISSUERS
from src.logic import theme_taxonomy
from src.ui.pages import companies as companies_page
from src.ui.pages import theme_index as theme_index_page

HARNESS_DIR = Path(__file__).parent / "apptest_pages"
REPO_ROOT = Path(__file__).parent.parent


def _run(harness: str) -> AppTest:
    at = AppTest.from_file(str(HARNESS_DIR / harness), default_timeout=20)
    at.run()
    assert not at.exception, at.exception
    return at


def _main_html(at: AppTest) -> str:
    return " ".join(m.value for m in at.main.get("markdown"))


# ============================================================
# theme_taxonomy — the tag taxonomy, not the Research Theses
# ============================================================


def test_every_slug_the_registry_carries_has_a_display_name():
    """An unmapped slug falls back to itself rather than vanishing, so
    the real guard is that none of them has to: every theme actually on
    a seed issuer is spelled out."""
    present = {slug for issuer in SEED_ISSUERS for slug in issuer.themes}
    unmapped = present - set(theme_taxonomy.THEME_DISPLAY_NAMES)
    assert unmapped == set(), f"these slugs would render as raw slugs: {sorted(unmapped)}"


def test_an_unmapped_slug_renders_as_itself_rather_than_blank():
    assert theme_taxonomy.theme_display_name("not-a-real-theme") == "not-a-real-theme"


def test_known_slugs_come_from_the_registry_not_the_display_map():
    """The registry is the source of truth for which themes exist. A
    display name for a theme no issuer carries must not invent a nav
    entry or an index card."""
    present = {slug for issuer in SEED_ISSUERS for slug in issuer.themes}
    assert set(theme_taxonomy.known_theme_slugs()) == present


def test_counts_match_the_registry_and_deliberately_do_not_sum_to_the_universe():
    counts = theme_taxonomy.company_counts_by_theme()
    for slug, n in counts.items():
        assert n == sum(1 for i in SEED_ISSUERS if slug in i.themes), slug
    # Multi-theme assignment is allowed (config/eevaresearch_theme_
    # registry.yaml: allow_multi_theme_assignment), so the per-theme
    # counts legitimately exceed the number of companies. A future edit
    # that "fixed" this into a partition would be wrong.
    assert sum(counts.values()) > len(SEED_ISSUERS)


def test_theme_taxonomy_imports_nothing_from_the_research_theses_lane():
    """The two "theme" concepts must not be wired together. This module
    is the tag taxonomy; theme_store/theme_research/themes_research own
    the curated narratives."""
    source = (REPO_ROOT / "src" / "logic" / "theme_taxonomy.py").read_text(encoding="utf-8")
    for forbidden in ("theme_store", "theme_research", "themes_research", "get_theme_repository"):
        assert f"import {forbidden}" not in source and f"from {forbidden}" not in source, forbidden


def test_theme_taxonomy_is_streamlit_free():
    source = (REPO_ROOT / "src" / "logic" / "theme_taxonomy.py").read_text(encoding="utf-8")
    assert "streamlit" not in source


# ============================================================
# Companies index
# ============================================================


def test_companies_page_lists_every_seed_issuer_by_its_english_name():
    at = _run("companies_page.py")
    html = _main_html(at)
    for issuer in SEED_ISSUERS:
        # Escaped, not raw: "Kulicke & Soffa Industries, Inc." reaches the
        # page as "&amp;". Comparing raw would quietly pass for every
        # company without punctuation and fail only for that one.
        assert escape(issuer.legal_name) in html, issuer.legal_name


def test_companies_page_lists_exactly_the_seed_universe_and_no_discovery_stub():
    """DISCOVERY_STUBS are structurally excluded from every scan path; a
    reader-facing "Companies" page listing one would present an
    unreviewed candidate as covered. Coverage shows them, labelled.

    Asserted on the exact row count rather than by searching for stub
    names: several stubs are shorter forms of a seed issuer's own name
    ("LG Innotek" vs "LG Innotek Co., Ltd."), so a substring search
    reports a stub that was never rendered."""
    at = _run("companies_page.py")
    html = _main_html(at)
    assert html.count('class="er-company-row"') == len(SEED_ISSUERS)

    # And a stub whose name shares nothing with any seed issuer is
    # genuinely absent, which the count alone would not prove if a stub
    # had somehow displaced a seed row.
    seed_text = " ".join(i.legal_name for i in SEED_ISSUERS)
    disjoint = [s.legal_name for s in DISCOVERY_STUBS if s.legal_name and s.legal_name not in seed_text]
    assert disjoint, "fixture check: expected at least one stub name unlike every seed name"
    for name in disjoint:
        assert escape(name) not in html, name


def test_native_names_are_shown_only_where_the_registry_holds_one():
    """The redesign asks for the English name with the Korean/Japanese
    name smaller underneath. `native_name` is populated for the EDINET
    issuers only — empty for every EDGAR and DART issuer, Korean ones
    included — so the page must show exactly those and invent none.
    Nothing here romanizes or translates."""
    at = _run("companies_page.py")
    html = _main_html(at)
    with_native = [i for i in SEED_ISSUERS if i.native_name]
    assert with_native, "fixture check: expected at least one issuer with a native name"
    for issuer in with_native:
        assert issuer.native_name in html, issuer.native_name
    # Exactly as many native-name blocks as there are populated values —
    # no placeholder, no repeat, nothing generated for the other issuers.
    assert html.count('class="er-company-native"') == len(with_native)


def test_native_names_are_language_tagged_so_they_get_the_right_font():
    """cjk_html() wraps each kana/kanji run in <span lang="ja">, which is
    what assets/styles.css's :lang(ja) rule keys off for the Japanese
    serif stack. A plain-escaped native name would silently render in the
    Korean font."""
    at = _run("companies_page.py")
    html = _main_html(at)
    assert 'lang="ja"' in html


def test_companies_page_links_nowhere_until_per_company_pages_exist():
    """Per-company detail pages are item 3. Until then a row is text: a
    row that looked clickable and did nothing would be worse than a
    plain list."""
    at = _run("companies_page.py")
    assert [pl.label for pl in at.main.get("page_link")] == []


def test_search_filters_by_company_name_and_by_ticker():
    at = _run("companies_page.py")
    at.text_input(key=companies_page._SEARCH_KEY).set_value("NVDA").run()
    html = _main_html(at)
    assert "NVIDIA" in html
    assert "Samsung Electronics" not in html

    at.text_input(key=companies_page._SEARCH_KEY).set_value("samsung").run()
    html = _main_html(at)
    assert "Samsung Electronics" in html
    assert "NVIDIA" not in html


def test_an_unmatchable_search_shows_an_empty_state_not_an_empty_page():
    at = _run("companies_page.py")
    at.text_input(key=companies_page._SEARCH_KEY).set_value("zzz-no-such-company").run()
    html = " ".join(m.value for m in at.get("markdown"))
    assert "No companies match" in html


def test_market_filter_narrows_to_one_jurisdiction():
    at = _run("companies_page.py")
    at.selectbox(key=companies_page._REGION_KEY).set_value("South Korea").run()
    html = _main_html(at)
    assert "Samsung Electronics" in html
    assert "NVIDIA" not in html


def test_theme_filter_narrows_to_companies_carrying_that_tag():
    at = _run("companies_page.py")
    at.selectbox(key=companies_page._THEME_KEY).set_value("space").run()
    html = _main_html(at)
    space_companies = [i for i in SEED_ISSUERS if "space" in i.themes]
    for issuer in space_companies:
        assert issuer.legal_name in html, issuer.legal_name
    # A company in no space cohort must be gone, proving the filter is
    # applied rather than merely re-sorting.
    assert "Samsung Electronics" not in html


# ============================================================
# Themes index
# ============================================================


def test_theme_index_shows_every_theme_with_its_real_company_count():
    at = _run("theme_index_page.py")
    html = _main_html(at)
    for slug, count in theme_taxonomy.company_counts_by_theme().items():
        assert theme_taxonomy.theme_display_name(slug) in html, slug
        assert f"{count} companies" in html, slug


def test_theme_index_labels_its_counts_as_companies_not_events():
    """The number a reader wants is events per theme, and it cannot be
    computed honestly yet: filing_signal_bridge.project_filing_signals()
    sets matched_themes=() on every filing-derived story, so a story
    count would report the news lane only and silently omit every
    filing. Until themes are projected onto filing stories, the label
    has to say what the number actually is."""
    at = _run("theme_index_page.py")
    html = " ".join(m.value for m in at.get("markdown"))
    assert "companies" in html
    for misleading in ("events", "filings", "signals"):
        assert f"{misleading} " not in html.lower().split("Counts are")[-1][:40], misleading


def test_the_filing_bridge_still_carries_no_themes_so_the_label_stays_honest():
    """The reason the count above is companies rather than events, pinned
    at its source. When this stops being true, the Themes index can show
    a real event count — and this test is the reminder."""
    source = (REPO_ROOT / "src" / "data_access" / "daily_news" / "filing_signal_bridge.py").read_text(encoding="utf-8")
    assert "matched_themes=()," in source


def test_theme_index_caps_the_company_list_and_says_how_many_it_held_back():
    """AI Buildout carries most of the universe; an uncapped list would
    bury the other four themes."""
    at = _run("theme_index_page.py")
    html = _main_html(at)
    biggest = max(theme_taxonomy.company_counts_by_theme().values())
    assert biggest > theme_index_page._NAMES_SHOWN, "fixture check: expected one oversized theme"
    assert f"+{biggest - theme_index_page._NAMES_SHOWN} more" in html


def test_theme_index_links_nowhere_until_theme_pages_exist():
    """Per-theme pages are item 4, which also fixes the theme "View"
    links that currently open external articles."""
    at = _run("theme_index_page.py")
    assert [pl.label for pl in at.main.get("page_link")] == []


def test_theme_index_shows_subtheme_slugs_as_filed_without_inventing_labels():
    """The registry has no display-name map for subthemes. Showing the
    slug as written is honest; a generated label would not be."""
    at = _run("theme_index_page.py")
    html = _main_html(at)
    memory_subthemes = theme_index_page._subtheme_labels("memory")
    assert "dram" in memory_subthemes
    for slug in memory_subthemes:
        assert slug in html, slug
