"""Theme Registry Foundation — typed, immutable representation of
config/eevaresearch_theme_registry.yaml (design/THEME_REGISTRY_FOUNDATION.md).

Pure data types only — no parsing, no file I/O, no validation logic (see
src/config/theme_registry_loader.py for both) and no matching logic (see
src/logic/theme_matching.py). Frozen dataclasses and tuple-based list
fields throughout, matching this repository's existing convention
(src/models/issuer.py, src/config/tracked_companies.py).

This is a separate, internal classification namespace from
src.config.ontology.PRIMARY_THEMES — the two are deliberately not
merged, cross-mapped, renamed, or reconciled in this phase. See
design/THEME_REGISTRY_FOUNDATION.md for why."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Geography:
    id: str
    names: tuple[str, ...]
    sources: tuple[str, ...]


@dataclass(frozen=True)
class CrossThemeRelationship:
    relation: str
    from_theme_id: str
    to_theme_ids: tuple[str, ...]


@dataclass(frozen=True)
class MatchingPolicy:
    minimum_theme_evidence: int
    require_material_event_for_candidate: bool
    allow_multi_theme_assignment: bool
    do_not_auto_publish: bool
    source_priority: tuple[str, ...]
    common_material_event_patterns: tuple[str, ...]


@dataclass(frozen=True)
class ClassificationOutputContract:
    required_fields: tuple[str, ...]
    initial_candidate_status: str
    autonomous_statuses_forbidden: tuple[str, ...]


@dataclass(frozen=True)
class Theme:
    """One taxonomy entry. `applies_to_all_themes=True` marks the single
    universal company-event layer (`company_specific_catalysts` in the
    registry) — see ThemeRegistry.universal_theme(). Every list field
    defaults to an empty tuple since that one entry omits
    technologies_products/entity_roles/discovery_queries entirely.

    `normalized_aliases` is populated once by the loader (parallel to
    `aliases`, same order) — never recomputed per match call. See
    src/logic/theme_matching.py's own normalize_text()."""

    id: str
    name: str
    priority: str
    description: str
    aliases: tuple[str, ...] = ()
    technologies_products: tuple[str, ...] = ()
    entity_roles: tuple[str, ...] = ()
    event_patterns: tuple[str, ...] = ()
    discovery_queries: tuple[str, ...] = ()
    applies_to_all_themes: bool = False
    candidate_requirements: tuple[str, ...] = ()
    normalized_aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ThemeRegistry:
    """The fully validated, immutable in-memory registry. Only ever
    constructed by src.config.theme_registry_loader.load_theme_registry()
    — never assembled by hand elsewhere — so a ThemeRegistry instance
    existing already implies it passed every validation rule."""

    version: int
    name: str
    primary_language: str
    matching_policy: MatchingPolicy
    geographies: tuple[Geography, ...]
    entity_roles: tuple[str, ...]
    materiality_keywords_high: tuple[str, ...]
    materiality_keywords_low: tuple[str, ...]
    cross_theme_relationships: tuple[CrossThemeRelationship, ...]
    themes: tuple[Theme, ...]
    classification_output_contract: ClassificationOutputContract

    def theme_by_id(self, theme_id: str) -> Theme | None:
        for theme in self.themes:
            if theme.id == theme_id:
                return theme
        return None

    def universal_theme(self) -> Theme | None:
        for theme in self.themes:
            if theme.applies_to_all_themes:
                return theme
        return None
