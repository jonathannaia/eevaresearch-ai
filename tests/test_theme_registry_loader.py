"""Theme Registry Foundation — loader/validation tests
(design/THEME_REGISTRY_FOUNDATION.md).

Pure/offline: no network, no repository writes, no Docker, no database.
Uses the real, committed config/eevaresearch_theme_registry.yaml for
valid-load tests, and small synthetic fixtures (tmp_path) for every
failure-mode test, so a failure mode is never accidentally coupled to
the real file's current contents changing later."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.config.theme_registry_loader import ThemeRegistryError, load_theme_registry, load_theme_registry_or_none
from src.models.models import CandidateStatus

_REAL_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "config" / "eevaresearch_theme_registry.yaml"

_MINIMAL_VALID_REGISTRY: dict = {
    "registry_version": 1,
    "registry_name": "Test Registry",
    "primary_language": "en",
    "matching_policy": {
        "minimum_theme_evidence": 1,
        "require_material_event_for_candidate": True,
        "allow_multi_theme_assignment": True,
        "do_not_auto_publish": True,
        "source_priority": ["regulatory_filing"],
        "common_material_event_patterns": ["earnings"],
    },
    "geographies": [{"id": "united_states", "names": ["United States"], "sources": ["SEC_EDGAR"]}],
    "entity_roles": ["customer", "equipment_vendor"],
    "materiality_keywords": {"high_priority": ["guidance"], "lower_priority": ["annual meeting"]},
    "shared_cross_theme_relationships": [],
    "themes": [
        {
            "id": "theme_one",
            "name": "Theme One",
            "priority": "critical",
            "description": "First test theme.",
            "aliases": ["alpha"],
            "technologies_products": ["widget"],
            "entity_roles": ["customer"],
            "event_patterns": ["launch"],
            "discovery_queries": ["alpha launch"],
        },
        {
            "id": "theme_two",
            "name": "Theme Two",
            "priority": "high",
            "description": "Second test theme.",
            "aliases": ["beta"],
            "entity_roles": ["equipment_vendor"],
        },
    ],
    "classification_output_contract": {
        "required_fields": ["item_id"],
        "status_policy": {
            "initial_candidate_status": "NEW_FILING_EVENT",
            "autonomous_statuses_forbidden": ["PUBLISHED", "MONITORING", "DISMISSED"],
        },
    },
}


def _write_yaml(tmp_path: Path, content: dict | str, filename: str = "registry.yaml") -> Path:
    path = tmp_path / filename
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_text(yaml.safe_dump(content), encoding="utf-8")
    return path


# --- Valid load, against the real committed file ---

def test_valid_load_against_real_committed_registry():
    registry = load_theme_registry(_REAL_REGISTRY_PATH)
    assert len(registry.themes) == 13
    assert registry.theme_by_id("ai_infrastructure_and_semiconductors") is not None
    assert registry.universal_theme() is not None
    assert registry.universal_theme().id == "company_specific_catalysts"
    assert len(registry.cross_theme_relationships) == 4


def test_real_registry_forbidden_statuses_match_real_candidate_status_enum():
    """Data-integrity check: the registry's own
    classification_output_contract.status_policy.autonomous_statuses_forbidden
    values (given as CandidateStatus *member names*, e.g. "PUBLISHED")
    must be real CandidateStatus members — proving the registry and the
    actual review lifecycle agree on what "autonomous" may never set,
    not just that the registry says something plausible-looking."""
    registry = load_theme_registry(_REAL_REGISTRY_PATH)
    forbidden = registry.classification_output_contract.autonomous_statuses_forbidden
    assert set(forbidden) == {"PUBLISHED", "MONITORING", "DISMISSED"}
    assert set(forbidden) <= set(CandidateStatus.__members__)


def test_load_theme_registry_or_none_succeeds_for_real_file():
    registry, reason = load_theme_registry_or_none(_REAL_REGISTRY_PATH)
    assert registry is not None
    assert reason is None


# --- Missing file ---

def test_missing_file_fails_closed_without_raising():
    registry, reason = load_theme_registry_or_none(Path("/this/path/does/not/exist.yaml"))
    assert registry is None
    assert reason == "file_not_found"


def test_missing_file_raises_theme_registry_error_from_the_raising_entry_point():
    with pytest.raises(ThemeRegistryError):
        load_theme_registry(Path("/this/path/does/not/exist.yaml"))


# --- Malformed YAML ---

def test_malformed_yaml_fails_closed_with_sanitized_reason(tmp_path):
    path = _write_yaml(tmp_path, "themes: [this is not valid: yaml: at all: [[[", filename="bad.yaml")
    registry, reason = load_theme_registry_or_none(path)
    assert registry is None
    assert reason == "yaml_parse_error"
    # Sanitized: no file path, no raw parser text, in the returned reason.
    assert str(path) not in reason
    assert "line" not in reason.lower()


def test_yaml_that_parses_to_a_non_mapping_fails_closed(tmp_path):
    path = _write_yaml(tmp_path, "- just\n- a\n- list\n", filename="list.yaml")
    registry, reason = load_theme_registry_or_none(path)
    assert registry is None
    assert reason == "root_not_a_mapping"


# --- Duplicate theme IDs ---

def test_duplicate_theme_id_fails_closed(tmp_path):
    broken = dict(_MINIMAL_VALID_REGISTRY)
    broken["themes"] = [dict(_MINIMAL_VALID_REGISTRY["themes"][0]), dict(_MINIMAL_VALID_REGISTRY["themes"][0])]
    path = _write_yaml(tmp_path, broken)
    registry, reason = load_theme_registry_or_none(path)
    assert registry is None
    assert reason == "duplicate_theme_id"


# --- Unknown cross-theme references: hard fail (approved decision) ---

def test_unknown_from_theme_id_in_cross_theme_relationship_hard_fails(tmp_path):
    broken = dict(_MINIMAL_VALID_REGISTRY)
    broken["shared_cross_theme_relationships"] = [
        {"relation": "bogus_relation", "from": "does_not_exist", "to": ["theme_one"]},
    ]
    path = _write_yaml(tmp_path, broken)
    registry, reason = load_theme_registry_or_none(path)
    assert registry is None
    assert reason is not None
    assert reason.startswith("shared_cross_theme_relationships.unknown_from")


def test_unknown_to_theme_id_in_cross_theme_relationship_hard_fails(tmp_path):
    broken = dict(_MINIMAL_VALID_REGISTRY)
    broken["shared_cross_theme_relationships"] = [
        {"relation": "bogus_relation", "from": "theme_one", "to": ["does_not_exist"]},
    ]
    path = _write_yaml(tmp_path, broken)
    registry, reason = load_theme_registry_or_none(path)
    assert registry is None
    assert reason is not None
    assert reason.startswith("shared_cross_theme_relationships.unknown_to")


def test_valid_cross_theme_relationship_loads_successfully(tmp_path):
    valid = dict(_MINIMAL_VALID_REGISTRY)
    valid["shared_cross_theme_relationships"] = [
        {"relation": "drives", "from": "theme_one", "to": ["theme_two"]},
    ]
    path = _write_yaml(tmp_path, valid)
    registry, reason = load_theme_registry_or_none(path)
    assert reason is None
    assert registry is not None
    assert len(registry.cross_theme_relationships) == 1
    assert registry.cross_theme_relationships[0].from_theme_id == "theme_one"


# --- applies_to_all_themes uniqueness ---

def test_two_universal_themes_fails_closed(tmp_path):
    broken = dict(_MINIMAL_VALID_REGISTRY)
    theme_one = dict(_MINIMAL_VALID_REGISTRY["themes"][0], applies_to_all_themes=True)
    theme_two = dict(_MINIMAL_VALID_REGISTRY["themes"][1], applies_to_all_themes=True)
    broken["themes"] = [theme_one, theme_two]
    path = _write_yaml(tmp_path, broken)
    registry, reason = load_theme_registry_or_none(path)
    assert registry is None
    assert reason == "multiple_universal_themes"


def test_one_universal_theme_loads_successfully(tmp_path):
    valid = dict(_MINIMAL_VALID_REGISTRY)
    theme_one = dict(_MINIMAL_VALID_REGISTRY["themes"][0], applies_to_all_themes=True)
    valid["themes"] = [theme_one, dict(_MINIMAL_VALID_REGISTRY["themes"][1])]
    path = _write_yaml(tmp_path, valid)
    registry, reason = load_theme_registry_or_none(path)
    assert reason is None
    assert registry.universal_theme().id == "theme_one"


# --- Other validation rules ---

def test_unsupported_registry_version_fails_closed(tmp_path):
    broken = dict(_MINIMAL_VALID_REGISTRY)
    broken["registry_version"] = 999
    path = _write_yaml(tmp_path, broken)
    registry, reason = load_theme_registry_or_none(path)
    assert registry is None
    assert reason == "unsupported_registry_version"


def test_unknown_entity_role_on_a_theme_fails_closed(tmp_path):
    broken = dict(_MINIMAL_VALID_REGISTRY)
    bad_theme = dict(_MINIMAL_VALID_REGISTRY["themes"][0], entity_roles=["not_a_real_role"])
    broken["themes"] = [bad_theme, dict(_MINIMAL_VALID_REGISTRY["themes"][1])]
    path = _write_yaml(tmp_path, broken)
    registry, reason = load_theme_registry_or_none(path)
    assert registry is None
    assert reason is not None
    assert reason.startswith("themes.unknown_entity_role")


def test_invalid_priority_fails_closed(tmp_path):
    broken = dict(_MINIMAL_VALID_REGISTRY)
    bad_theme = dict(_MINIMAL_VALID_REGISTRY["themes"][0], priority="not_a_real_priority")
    broken["themes"] = [bad_theme, dict(_MINIMAL_VALID_REGISTRY["themes"][1])]
    path = _write_yaml(tmp_path, broken)
    registry, reason = load_theme_registry_or_none(path)
    assert registry is None
    assert reason is not None
    assert reason.startswith("themes.invalid_priority")


def test_minimal_valid_registry_loads_successfully(tmp_path):
    path = _write_yaml(tmp_path, _MINIMAL_VALID_REGISTRY)
    registry, reason = load_theme_registry_or_none(path)
    assert reason is None
    assert registry is not None
    assert len(registry.themes) == 2
    assert registry.theme_by_id("theme_one").normalized_aliases == ("alpha",)
