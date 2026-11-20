"""editorial_matching.matched_companies_and_themes — pure, fixture-free
(reads the real tracked-company roster for aliases; no network)."""
from __future__ import annotations

from src.data_access.daily_news.editorial_matching import (
    THEME_KEYWORDS,
    company_mention_spans,
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


# ============================================================
# System-wide company-matched-news fix (design/DECISIONS.md) — curated
# brand-alias overlay: positive matches for the approved low-ambiguity
# list (Amazon Web Services/AWS, Google, IBM), and explicit
# false-positive tests proving the case-sensitivity mitigation. Bare
# "Amazon"/"Meta"/"Facebook" are deliberately NOT in the overlay this
# pass — see company_aliases.py's own docstring — so no positive test
# exists for those, only the pre-existing negative ones already above.
# ============================================================


def test_amazon_web_services_full_phrase_matches():
    assert "Amazon.com, Inc." in match_companies("Amazon Web Services outage disrupts major websites")


def test_aws_acronym_matches():
    assert "Amazon.com, Inc." in match_companies("AWS launches new region in Mexico")


def test_google_brand_alias_matches():
    assert "Alphabet Inc." in match_companies("Google unveils new Pixel phone lineup")


def test_ibm_brand_alias_matches():
    assert "International Business Machines Corporation" in match_companies("IBM announces new mainframe chip")


def test_bare_amazon_still_never_matches_this_pass():
    # Required alias-policy correction: bare "Amazon" is not in the
    # curated overlay — capitalization alone does not resolve its real
    # ambiguity (e.g. "Amazon River"), so it is deliberately excluded
    # until a separately reviewed, evidence-based decision.
    assert "Amazon.com, Inc." not in match_companies("Amazon reports record Prime Day sales")
    assert "Amazon.com, Inc." not in match_companies("Explorers followed the Amazon River deep into the rainforest")


def test_bare_meta_and_facebook_still_never_match_this_pass():
    assert "Meta Platforms, Inc." not in match_companies("Meta shares jump after earnings beat")
    assert "Meta Platforms, Inc." not in match_companies("Facebook parent company announces layoffs")


def test_lowercase_aws_does_not_match_case_sensitive_brand_alias():
    # "aws" (lowercase) must not match — curated brand aliases require
    # their own real-world capitalization, a deterministic mitigation
    # for acronym/common-word ambiguity.
    assert "Amazon.com, Inc." not in match_companies("this update uses aws-style lowercase text, unrelated")


def test_lowercase_google_does_not_match_case_sensitive_brand_alias():
    assert "Alphabet Inc." not in match_companies("you can google it if you want to know more")


def test_lowercase_ibm_does_not_match_case_sensitive_brand_alias():
    assert "International Business Machines Corporation" not in match_companies("ibm is not capitalized here")


def test_aws_does_not_match_as_a_substring_of_a_longer_word():
    assert "Amazon.com, Inc." not in match_companies("The AWSomeCorp company is unrelated")


def test_mechanical_alias_matching_stays_case_insensitive_unaffected_by_brand_overlay():
    # The pre-existing mechanical-alias behavior (case-insensitive) must
    # be completely unaffected by adding the case-sensitive brand
    # overlay for a DIFFERENT company.
    assert "Corning Inc." in match_companies("corning reports quarterly results")


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


# --- company_mention_spans (Signals admission precision fix, P0) ----
# Exposes WHERE a company is mentioned — the grammatical-agency
# proximity check in editorial_admission.py's own _company_is_
# grammatical_actor() is this function's one real caller.


def test_company_mention_spans_finds_mechanical_alias_occurrence():
    # "Oracle Corporation" has two mechanical aliases (company_aliases.py):
    # the full legal name, and "Oracle" (the suffix-stripped short form —
    # the exact reason this company is in editorial_admission's own
    # _AMBIGUOUS_ALIAS_COMPANIES list). Both spans are real, correct
    # matches for the same one mention.
    text = "Oracle Corporation reported strong AI cloud demand this quarter."
    spans = company_mention_spans(text, "Oracle Corporation")
    assert spans == ((0, 6), (0, 18))
    assert text[0:6] == "Oracle"
    assert text[0:18] == "Oracle Corporation"


def test_company_mention_spans_finds_every_occurrence_case_insensitively():
    text = "oracle corporation and Oracle Corporation both refer to the same company."
    spans = company_mention_spans(text, "Oracle Corporation")
    # 2 mechanical aliases ("Oracle" + "Oracle Corporation") x 2 real
    # occurrences in the text = 4 spans total.
    assert len(spans) == 4
    for start, end in spans:
        assert text[start:end].lower() in ("oracle", "oracle corporation")


def test_company_mention_spans_finds_brand_alias_case_sensitively():
    text = "Amazon Web Services (AWS) is a cloud platform; aws is also a lowercase unrelated word here."
    spans = company_mention_spans(text, "Amazon.com, Inc.")
    matched_text = {text[start:end] for start, end in spans}
    assert "Amazon Web Services" in matched_text
    assert "AWS" in matched_text
    assert "aws" not in matched_text  # case-sensitive brand alias — lowercase "aws" never matches


def test_company_mention_spans_returns_empty_for_unmentioned_company():
    assert company_mention_spans("A totally unrelated sentence.", "Oracle Corporation") == ()


def test_company_mention_spans_returns_empty_for_untracked_company_name():
    assert company_mention_spans("Some text here.", "Not A Real Tracked Company") == ()
