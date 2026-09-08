"""Ontology foundation — static vocabulary + known-conflict records."""
from src.config.ontology import (
    KNOWN_CATEGORY_CONFLICTS,
    PRIMARY_THEMES,
    SUPPLY_CHAIN_LAYERS,
    is_valid_layer,
    is_valid_theme,
)


def test_primary_themes_match_the_five_dashboard_themes():
    assert PRIMARY_THEMES == ("ai-buildout", "humanoids", "space", "memory", "photonics")


def test_no_duplicate_themes_or_layers():
    assert len(PRIMARY_THEMES) == len(set(PRIMARY_THEMES))
    assert len(SUPPLY_CHAIN_LAYERS) == len(set(SUPPLY_CHAIN_LAYERS))


def test_themes_and_layers_are_disjoint_except_memory():
    # 'memory' is deliberately both a theme and a layer (see ontology.py's
    # module docstring on why the two vocabularies aren't merged) — the
    # one intentional overlap, not an error.
    overlap = set(PRIMARY_THEMES) & set(SUPPLY_CHAIN_LAYERS)
    assert overlap == {"memory"}


def test_is_valid_theme_and_layer():
    assert is_valid_theme("ai-buildout")
    assert not is_valid_theme("networking-interconnect")  # a themes.json subtheme, not a primary theme
    assert is_valid_layer("interconnect")
    assert not is_valid_layer("ai-buildout")  # a theme, not a layer


def test_known_category_conflicts_has_four_entries():
    assert len(KNOWN_CATEGORY_CONFLICTS) == 4


def test_known_category_conflicts_all_have_nonempty_description():
    for conflict in KNOWN_CATEGORY_CONFLICTS:
        assert conflict.subject.strip() != ""
        assert conflict.description.strip() != ""
