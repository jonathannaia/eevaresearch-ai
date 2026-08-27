"""Theme Registry Foundation — safe YAML loading and fail-closed
validation for config/eevaresearch_theme_registry.yaml
(design/THEME_REGISTRY_FOUNDATION.md).

The only file-I/O boundary for the theme registry. Uses yaml.safe_load
exclusively — never yaml.load — since the registry file, while tracked
and reviewed like any other source file, is still external, structured
input parsed at runtime.

Two entry points:
- load_theme_registry(path) -> ThemeRegistry: raises ThemeRegistryError
  on any problem (missing file, malformed YAML, or any failed
  validation rule). For tests and explicit, deliberate loading.
- load_theme_registry_or_none(path) -> tuple[ThemeRegistry | None, str | None]:
  never raises — the safe-by-default entry point every real caller
  (once this registry is ever wired into something, which this phase
  does not do) should use. Every failure mode degrades identically to
  (None, <sanitized reason code>); callers must treat None as "no theme
  classification available" and continue operating exactly as they do
  today — never crash, never invent a taxonomy on the fly. The reason
  code is a short, internal identifier (e.g. "duplicate_theme_id",
  "unknown_cross_theme_reference:<relation>") — safe to log, but still
  not meant for verbatim end-user display once a future phase wires
  this into any UI.

Unknown cross-theme references (an unrecognized `from`/`to` theme ID in
shared_cross_theme_relationships) are a hard load failure in this
module, not a drop-and-warn — an authoring error in the registry file
fails the whole registry closed for classification, while
load_theme_registry_or_none's own contract (above) keeps that failure
from ever reaching or crashing a caller."""
from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import yaml

from src.logic.theme_matching import normalize_text
from src.models.theme_registry import (
    ClassificationOutputContract,
    CrossThemeRelationship,
    Geography,
    MatchingPolicy,
    Theme,
    ThemeRegistry,
)

_SUPPORTED_REGISTRY_VERSION = 1
_VALID_PRIORITIES = frozenset({"critical", "high"})


class ThemeRegistryError(Exception):
    """Raised by load_theme_registry() on any structural/validation
    problem. Carries only a short, sanitized reason code — never a raw
    YAML-parser exception, a file path, or anything else unsafe to log.
    See this module's own docstring for the fail-closed contract every
    real caller should use instead (load_theme_registry_or_none)."""


def _fail(reason: str) -> NoReturn:
    raise ThemeRegistryError(reason)


def _require_mapping(value: object, reason: str) -> dict:
    if not isinstance(value, dict):
        _fail(reason)
    return value


def _require_list(value: object, reason: str) -> list:
    if not isinstance(value, list):
        _fail(reason)
    return value


def _tuple_of_str(value: object, reason: str) -> tuple[str, ...]:
    items = _require_list([] if value is None else value, reason)
    if not all(isinstance(item, str) for item in items):
        _fail(reason)
    return tuple(items)


def _parse_matching_policy(raw: dict) -> MatchingPolicy:
    return MatchingPolicy(
        minimum_theme_evidence=int(raw["minimum_theme_evidence"]),
        require_material_event_for_candidate=bool(raw["require_material_event_for_candidate"]),
        allow_multi_theme_assignment=bool(raw["allow_multi_theme_assignment"]),
        do_not_auto_publish=bool(raw["do_not_auto_publish"]),
        source_priority=_tuple_of_str(raw.get("source_priority"), "matching_policy.source_priority_invalid"),
        common_material_event_patterns=_tuple_of_str(
            raw.get("common_material_event_patterns"), "matching_policy.common_material_event_patterns_invalid",
        ),
    )


def _parse_geographies(raw: list) -> tuple[Geography, ...]:
    geographies = []
    for entry in raw:
        entry = _require_mapping(entry, "geographies.entry_invalid")
        geographies.append(Geography(
            id=entry.get("id", ""),
            names=_tuple_of_str(entry.get("names"), "geographies.names_invalid"),
            sources=_tuple_of_str(entry.get("sources"), "geographies.sources_invalid"),
        ))
    return tuple(geographies)


def _parse_theme(raw: object, known_entity_roles: frozenset[str]) -> Theme:
    raw = _require_mapping(raw, "themes.entry_invalid")
    theme_id = raw.get("id")
    if not isinstance(theme_id, str) or not theme_id:
        _fail("themes.missing_id")
    priority = raw.get("priority")
    if priority not in _VALID_PRIORITIES:
        _fail(f"themes.invalid_priority:{theme_id}")

    entity_roles = _tuple_of_str(raw.get("entity_roles"), f"themes.entity_roles_invalid:{theme_id}")
    for role in entity_roles:
        if role not in known_entity_roles:
            _fail(f"themes.unknown_entity_role:{theme_id}")

    aliases = _tuple_of_str(raw.get("aliases"), f"themes.aliases_invalid:{theme_id}")

    return Theme(
        id=theme_id,
        name=raw.get("name", ""),
        priority=priority,
        description=raw.get("description", ""),
        aliases=aliases,
        technologies_products=_tuple_of_str(
            raw.get("technologies_products"), f"themes.technologies_products_invalid:{theme_id}",
        ),
        entity_roles=entity_roles,
        event_patterns=_tuple_of_str(raw.get("event_patterns"), f"themes.event_patterns_invalid:{theme_id}"),
        discovery_queries=_tuple_of_str(
            raw.get("discovery_queries"), f"themes.discovery_queries_invalid:{theme_id}",
        ),
        applies_to_all_themes=bool(raw.get("applies_to_all_themes", False)),
        candidate_requirements=_tuple_of_str(
            raw.get("candidate_requirements"), f"themes.candidate_requirements_invalid:{theme_id}",
        ),
        normalized_aliases=tuple(normalize_text(alias) for alias in aliases),
    )


def _parse_cross_theme_relationships(raw: list, known_theme_ids: frozenset[str]) -> tuple[CrossThemeRelationship, ...]:
    relationships = []
    for entry in raw:
        entry = _require_mapping(entry, "shared_cross_theme_relationships.entry_invalid")
        relation = entry.get("relation", "")
        from_id = entry.get("from")
        to_ids = _tuple_of_str(entry.get("to"), f"shared_cross_theme_relationships.to_invalid:{relation}")

        if from_id not in known_theme_ids:
            _fail(f"shared_cross_theme_relationships.unknown_from:{relation}")
        for to_id in to_ids:
            if to_id not in known_theme_ids:
                _fail(f"shared_cross_theme_relationships.unknown_to:{relation}")

        relationships.append(CrossThemeRelationship(relation=relation, from_theme_id=from_id, to_theme_ids=to_ids))
    return tuple(relationships)


def _parse_classification_output_contract(raw: dict) -> ClassificationOutputContract:
    status_policy = _require_mapping(
        raw.get("status_policy", {}), "classification_output_contract.status_policy_invalid",
    )
    return ClassificationOutputContract(
        required_fields=_tuple_of_str(
            raw.get("required_fields"), "classification_output_contract.required_fields_invalid",
        ),
        initial_candidate_status=status_policy.get("initial_candidate_status", ""),
        autonomous_statuses_forbidden=_tuple_of_str(
            status_policy.get("autonomous_statuses_forbidden"),
            "classification_output_contract.autonomous_statuses_forbidden_invalid",
        ),
    )


def load_theme_registry(path: Path) -> ThemeRegistry:
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError:
        _fail("file_not_found")

    try:
        raw = yaml.safe_load(raw_text)
    except yaml.YAMLError:
        _fail("yaml_parse_error")

    raw = _require_mapping(raw, "root_not_a_mapping")

    version = raw.get("registry_version")
    if version != _SUPPORTED_REGISTRY_VERSION:
        _fail("unsupported_registry_version")

    known_entity_roles_list = _tuple_of_str(raw.get("entity_roles"), "entity_roles_invalid")
    known_entity_roles = frozenset(known_entity_roles_list)

    raw_themes = _require_list(raw.get("themes"), "themes_missing_or_invalid")
    themes = [_parse_theme(entry, known_entity_roles) for entry in raw_themes]

    theme_ids = [theme.id for theme in themes]
    if len(theme_ids) != len(set(theme_ids)):
        _fail("duplicate_theme_id")

    if sum(1 for theme in themes if theme.applies_to_all_themes) > 1:
        _fail("multiple_universal_themes")

    known_theme_ids = frozenset(theme_ids)
    cross_theme_relationships = _parse_cross_theme_relationships(
        _require_list(raw.get("shared_cross_theme_relationships", []), "shared_cross_theme_relationships_invalid"),
        known_theme_ids,
    )

    materiality_keywords = _require_mapping(raw.get("materiality_keywords", {}), "materiality_keywords_invalid")

    return ThemeRegistry(
        version=version,
        name=raw.get("registry_name", ""),
        primary_language=raw.get("primary_language", ""),
        matching_policy=_parse_matching_policy(
            _require_mapping(raw.get("matching_policy"), "matching_policy_missing"),
        ),
        geographies=_parse_geographies(_require_list(raw.get("geographies", []), "geographies_invalid")),
        entity_roles=known_entity_roles_list,
        materiality_keywords_high=_tuple_of_str(
            materiality_keywords.get("high_priority"), "materiality_keywords.high_priority_invalid",
        ),
        materiality_keywords_low=_tuple_of_str(
            materiality_keywords.get("lower_priority"), "materiality_keywords.lower_priority_invalid",
        ),
        cross_theme_relationships=cross_theme_relationships,
        themes=tuple(themes),
        classification_output_contract=_parse_classification_output_contract(
            _require_mapping(raw.get("classification_output_contract", {}), "classification_output_contract_missing"),
        ),
    )


def load_theme_registry_or_none(path: Path) -> tuple[ThemeRegistry | None, str | None]:
    try:
        return load_theme_registry(path), None
    except ThemeRegistryError as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 — any unexpected problem also fails closed, never raises to the caller
        return None, f"unexpected_error:{type(exc).__name__}"
