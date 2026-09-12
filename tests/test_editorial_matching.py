"""editorial_matching.matched_companies_and_themes — pure, fixture-free
(reads the real tracked-company roster for aliases; no network)."""
from __future__ import annotations

from src.data_access.daily_news.editorial_matching import (
    THEME_KEYWORDS,
    match_companies,
    match_themes,
    matched_companies_and_themes,
)

# --- No ticker matching ---------------------------------------------


def test_bare_ticker_alone_never_matches():
    for ticker_only_text in ("QCOM shares rose today", "GLW hit a new high", "SNPS trades flat"):
        assert match_companies(ticker_only_text) == ()


def test_no_ticker_field_is_ever_read():
    import inspect

    from src.data_access.daily_news import editorial_matching

    source = inspect.getsource(editorial_matching)
    assert ".krx_code" not in source


# --- Company matching, word boundaries -------------------------------


def test_full_company_name_matches():
    assert "Oracle Corporation" in match_companies("Oracle Corporation reports earnings")


def test_stripped_alias_matches():
    assert "Oracle Corporation" in match_companies("Oracle posts 30% revenue growth")


def test_alias_does_not_match_inside_an_unrelated_word():
    # "Corning" must not match inside "Corningware" or similar — proves
    # the trailing word boundary, not just the leading one.
    assert "Corning Inc." not in match_companies("They bought a set of Corningware dishes")


def test_alias_does_not_match_as_a_substring_of_a_longer_word():
    # "MKS" (MKS Inc's stripped alias) must not match inside "MKSanything"
    assert "MKS Inc" not in match_companies("The MKSanything corporation is unrelated")


def test_case_insensitive_company_matching():
    assert "Corning Inc." in match_companies("corning reports quarterly results")


def test_multi_company_match():
    companies = match_companies("Dell, Skyworks Solutions, and Oracle Corporation all moved today")
    assert "Skyworks Solutions, Inc." in companies
    assert "Oracle Corporation" in companies


def test_bare_short_name_not_in_alias_list_does_not_match():
    # "Dell" alone (not "Dell Technologies") must not match — the
    # stripped alias is "Dell Technologies", never bare "Dell".
    assert "Dell Technologies Inc." not in match_companies("Dell posted strong laptop sales")


# --- Theme matching — exact approved phrases only ---------------------


def test_every_approved_theme_phrase_matches_its_own_theme():
    for theme_slug, phrases in THEME_KEYWORDS.items():
        for phrase in phrases:
            assert theme_slug in match_themes(f"Breaking news: {phrase} announced today"), (theme_slug, phrase)


def test_bare_overloaded_words_never_match_any_theme():
    for bare_word in ("AI", "space", "memory", "robot", "robots"):
        assert match_themes(f"This story is about {bare_word} in general") == ()


def test_theme_phrase_count_matches_the_approved_table_exactly():
    assert THEME_KEYWORDS["ai-buildout"] == (
        "AI data center", "AI infrastructure", "AI chip", "AI accelerator",
        "AI cloud", "GPU cluster", "hyperscaler capex", "data center capex",
    )
    assert THEME_KEYWORDS["memory"] == (
        "HBM", "DRAM", "NAND flash", "NAND chip", "memory chip",
        "memory market", "DDR5", "high-bandwidth memory",
    )
    assert THEME_KEYWORDS["space"] == (
        "satellite launch", "rocket launch", "orbital launch",
        "launch vehicle", "spacecraft", "space station",
        "satellite constellation",
    )
    assert THEME_KEYWORDS["photonics"] == (
        "photonics", "silicon photonics", "optical networking",
        "co-packaged optics", "optical transceiver", "optical interconnect",
    )
    assert THEME_KEYWORDS["humanoids"] == (
        "humanoid robot", "humanoid robotics", "industrial robot",
        "warehouse robotics", "robotics automation",
    )


def test_multi_theme_match():
    themes = match_themes("New GPU cluster deployment enables satellite launch telemetry via DRAM upgrades")
    assert "ai-buildout" in themes
    assert "space" in themes
    assert "memory" in themes


# --- Fail-closed eligibility -------------------------------------------


def test_zero_company_and_zero_theme_match_is_empty():
    companies, themes = matched_companies_and_themes("Record U.S. cyclosporiasis outbreak is over, CDC says", None)
    assert companies == ()
    assert themes == ()


def test_theme_only_story_is_valid_with_no_company_match():
    companies, themes = matched_companies_and_themes("New HBM production capacity announced by the industry", None)
    assert companies == ()
    assert themes == ("memory",)


def test_company_only_story_is_valid_with_no_theme_match():
    companies, themes = matched_companies_and_themes("Oracle Corporation announces new dividend policy", None)
    assert companies == ("Oracle Corporation",)
    assert themes == ()


def test_company_and_theme_together():
    companies, themes = matched_companies_and_themes(
        "Oracle posts 30% revenue growth fueled by AI cloud demand", None,
    )
    assert companies == ("Oracle Corporation",)
    assert themes == ("ai-buildout",)


def test_description_is_also_checked_when_title_alone_has_no_match():
    companies, themes = matched_companies_and_themes(
        "Breaking industry update", "Oracle Corporation reported strong AI cloud demand this quarter.",
    )
    assert companies == ("Oracle Corporation",)
    assert themes == ("ai-buildout",)


def test_none_description_does_not_raise():
    companies, themes = matched_companies_and_themes("Oracle Corporation news", None)
    assert companies == ("Oracle Corporation",)
