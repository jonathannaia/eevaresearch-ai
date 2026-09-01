"""EevaResearch — Phase A3 (design/DECISIONS.md). A private, operator-only
authoring tool for exactly one insert-only ThemeMatchingScope attached
to an existing, non-archived parent ResearchTheme. Enables internal,
deterministic, CONTEXT-only ResearchCaseThemeMatch collection for that
Theme — never a public effect of any kind. Not a product feature: no
application runtime module (app.py, any src/ui page) imports this
script, and it is never invoked automatically by anything in this
repository.

Invoke as (from the repo root), after the parent Theme already exists
(see scripts/create_internal_theme_draft.py or scripts/create_theme.py):

    python -m scripts.create_theme_matching_scope --confirm [--backend json|sqlite|postgres]

This script never publishes a Theme and never transitions visibility —
it does not import set_visibility or any visibility-changing capability
at all. It never creates a Theme, ThemeEvidenceItem,
ThemeCompanyMapEntry, or ThemeMatchReviewDecision. It creates ONLY a
single ThemeMatchingScope through the existing
ThemeMatchingRepositoryProtocol.insert_scope seam.

Safety gates (mirrors scripts/create_theme.py's own exact contract):

  1. AUTHORING_ENABLED below defaults to False. An operator must
     deliberately edit this file and set it to True before this script
     will ever consider persisting anything.
  2. Even with AUTHORING_ENABLED = True, the `--confirm` CLI flag is
     also required — without it, this is a dry run: build, validate,
     print what would happen, never write.
  3. A content-level placeholder-sentinel scan refuses to persist a
     scope that still contains the literal REPLACE_ME string anywhere.

No external behavior: no network call, no source fetch/validation, no
LLM/model call, no scanning/discovery, no worker invocation, no
deployment. Scopes are insert-only, keyed by theme_id — this script has
no update, versioning, or deletion capability, matching the persistence
layer's own established design (no such repository method exists to
call)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.config.settings import Settings, get_settings
from src.data_access import backend_factory
from src.models.theme_matching import ThemeMatchingScope
from src.models.theme_research import ThemeVisibility

# =============================================================================
# SAFETY GATE 1 of 2 — see module docstring. Must be hand-edited to True.
# =============================================================================
AUTHORING_ENABLED = False

# A literal sentinel an operator must replace with real, checked content.
_PLACEHOLDER_SENTINEL = "REPLACE_ME"

# A parent Theme's own visibility must be one of these for a scope to be
# creatable — the exact same "active" definition
# theme_matching_store.list_active_scopes() already establishes.
_ELIGIBLE_PARENT_VISIBILITIES = frozenset({ThemeVisibility.INTERNAL, ThemeVisibility.READY_TO_PUBLISH, ThemeVisibility.PUBLISHED})

# Defensive caps — new code, no existing precedent to match, but cheap
# insurance against a paste mistake producing a pathological record.
_MAX_ENTRY_LENGTH = 100
_MAX_LIST_SIZE = 50

# =============================================================================
# OPERATOR-AUTHORED SCOPE CONTENT — edit every value below with real,
# checked content before setting AUTHORING_ENABLED = True.
# =============================================================================

_SCOPE_THEME_ID = _PLACEHOLDER_SENTINEL  # the parent Theme's id, e.g. "theme-abc123..."

_SCOPE_SECTOR_TAGS: tuple[str, ...] = (
    "ai-buildout",
    "memory",
)
_SCOPE_SECTOR_SUBTAGS: tuple[str, ...] = (
    "compute-accelerators",
    "dram",
    "hbm",
    "semiconductor-test",
    "power-cooling",
    "interconnect",
    "interconnect-switching",
    "optical-components",
)
_SCOPE_ALLOWED_RULE_CATEGORIES: tuple[str, ...] = (
    "capex_or_facility_investment",
    "material_agreement",
    "financing_or_debt",
    "supply_or_sales_contract",
    "other_material_event",
)
_SCOPE_REQUIRED_KEYWORDS: tuple[str, ...] = (
    "capacity",
    "wafer",
    "fab",
    "foundry",
    "packaging",
    "hbm",
    "dram",
    "allocation",
    "lead time",
    "yield",
    "node",
    "supply agreement",
    "capacity expansion",
)
_SCOPE_EXCLUDED_KEYWORDS: tuple[str, ...] = (
    "share repurchase",
    "stock buyback",
    "dividend declaration",
    "annual meeting of stockholders",
    "proxy statement",
    "executive compensation",
)


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _dedupe_preserving_order(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return tuple(ordered)


def _normalize_lowercase_list(values: tuple[str, ...]) -> tuple[str, ...]:
    """For sector_tags/sector_subtags/allowed_matched_rule_categories —
    compared by exact equality against lowercase-hyphenated taxonomy/
    category slugs, so strip+lowercase before dedup."""
    return _dedupe_preserving_order([v.strip().lower() for v in values if isinstance(v, str) and v.strip()])


def _normalize_keyword_list(values: tuple[str, ...]) -> tuple[str, ...]:
    """For required/excluded keywords — evaluate_theme_match() already
    lowercases both sides of the comparison itself, so only strip+dedup
    here (case is preserved for readability in the stored record)."""
    return _dedupe_preserving_order([v.strip() for v in values if isinstance(v, str) and v.strip()])


def build_authored_scope(enable_authoring: bool = AUTHORING_ENABLED) -> ThemeMatchingScope | None:
    """Pure, no I/O. Returns None when `enable_authoring` is False (the
    default). `enable_authoring` is a parameter so tests can exercise
    both branches without editing this file."""
    if not enable_authoring:
        return None

    return ThemeMatchingScope(
        theme_id=_SCOPE_THEME_ID,
        sector_tags=_normalize_lowercase_list(_SCOPE_SECTOR_TAGS),
        sector_subtags=_normalize_lowercase_list(_SCOPE_SECTOR_SUBTAGS),
        allowed_matched_rule_categories=_normalize_lowercase_list(_SCOPE_ALLOWED_RULE_CATEGORIES),
        required_keywords=_normalize_keyword_list(_SCOPE_REQUIRED_KEYWORDS),
        excluded_keywords=_normalize_keyword_list(_SCOPE_EXCLUDED_KEYWORDS),
    )


def contains_placeholder_sentinel(scope: ThemeMatchingScope) -> bool:
    values: list[str] = [scope.theme_id]
    values.extend(scope.sector_tags)
    values.extend(scope.sector_subtags)
    values.extend(scope.allowed_matched_rule_categories)
    values.extend(scope.required_keywords)
    values.extend(scope.excluded_keywords)
    return any(_PLACEHOLDER_SENTINEL in value for value in values if isinstance(value, str))


def _validate_list_sizes_and_lengths(field_name: str, values: tuple[str, ...]) -> list[str]:
    errors: list[str] = []
    if len(values) > _MAX_LIST_SIZE:
        errors.append(f"scope.{field_name} has {len(values)} entries, exceeding the maximum of {_MAX_LIST_SIZE}.")
    for value in values:
        if len(value) > _MAX_ENTRY_LENGTH:
            errors.append(f"scope.{field_name} entry {value!r} exceeds the maximum length of {_MAX_ENTRY_LENGTH} characters.")
    return errors


def validate_scope_content(scope: ThemeMatchingScope) -> tuple[str, ...]:
    """Plain, deterministic content checks — never raises. Does not
    check the parent Theme at all (that requires a repository read; see
    validate_parent_theme below, run separately at the call site)."""
    errors: list[str] = []
    if not _nonblank(scope.theme_id):
        errors.append("scope.theme_id must not be blank.")

    if not scope.sector_tags:
        errors.append("scope.sector_tags must not be empty — at least one is required.")
    if not scope.allowed_matched_rule_categories:
        errors.append("scope.allowed_matched_rule_categories must not be empty — at least one is required.")
    if not scope.required_keywords:
        errors.append("scope.required_keywords must not be empty — at least one is required.")

    for field_name, values in [
        ("sector_tags", scope.sector_tags), ("sector_subtags", scope.sector_subtags),
        ("allowed_matched_rule_categories", scope.allowed_matched_rule_categories),
        ("required_keywords", scope.required_keywords), ("excluded_keywords", scope.excluded_keywords),
    ]:
        errors.extend(_validate_list_sizes_and_lengths(field_name, values))

    return tuple(errors)


def validate_parent_theme(theme) -> tuple[str, ...]:
    """Checks against an already-loaded parent ResearchTheme (or None
    if not found) — a repository read, so this is intentionally
    separate from validate_scope_content's pure, I/O-free checks."""
    if theme is None:
        return ("Parent Theme does not exist — refusing to create a scope for a nonexistent theme_id.",)
    if theme.visibility not in _ELIGIBLE_PARENT_VISIBILITIES:
        return (
            f"Parent Theme visibility is {theme.visibility.value!r} — a scope may only be created for a "
            f"theme with visibility in {sorted(v.value for v in _ELIGIBLE_PARENT_VISIBILITIES)}.",
        )
    return ()


def persist_scope(scope: ThemeMatchingScope, backend: str, cache_dir=None, sqlite_path=None, postgres_url=None) -> bool:
    """The single write call — assumes the parent-Theme and existing-
    scope checks have already been performed by the caller (see
    main()). Never creates a Theme, evidence item, company-map entry,
    match, or review decision."""
    settings = Settings(
        db_backend=backend,
        cache_dir=Path(cache_dir) if cache_dir else Settings().cache_dir,
        state_db_path=Path(sqlite_path) if sqlite_path else None,
        state_db_url=postgres_url,
    )
    matching_repository = backend_factory.get_theme_matching_repository(settings)
    return matching_repository.insert_scope(scope)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("json", "sqlite", "postgres"), default=None)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--sqlite-path", default=None)
    parser.add_argument("--postgres-url", default=None)
    parser.add_argument("--confirm", action="store_true", help="Required to actually persist. Without it: dry run only.")
    return parser.parse_args(argv)


def _resolve_backend_settings(args: argparse.Namespace):
    settings = get_settings()
    backend = args.backend or settings.db_backend or "json"
    cache_dir = args.cache_dir or settings.cache_dir
    sqlite_path = args.sqlite_path or (str(settings.state_db_path) if settings.state_db_path else None)
    postgres_url = args.postgres_url or settings.state_db_url
    return backend, cache_dir, sqlite_path, postgres_url


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    built = build_authored_scope(AUTHORING_ENABLED)
    if built is None:
        print("AUTHORING_ENABLED is False — this script is disabled by default. Edit "
              "scripts/create_theme_matching_scope.py, fill in real content in place of every "
              "REPLACE_ME placeholder, and set AUTHORING_ENABLED = True before re-running.")
        return 0

    scope = built
    if contains_placeholder_sentinel(scope):
        print(f"Refusing to proceed: the authored content still contains the {_PLACEHOLDER_SENTINEL!r} "
              "placeholder in one or more fields. Replace every placeholder with real, checked content "
              "before running again.")
        return 1

    errors = validate_scope_content(scope)
    if errors:
        print("Scope content is invalid — nothing was persisted. Issues:")
        for error in errors:
            print(f"  - {error}")
        return 1

    backend, cache_dir, sqlite_path, postgres_url = _resolve_backend_settings(args)
    settings = Settings(
        db_backend=backend, cache_dir=Path(cache_dir) if cache_dir else Settings().cache_dir,
        state_db_path=Path(sqlite_path) if sqlite_path else None, state_db_url=postgres_url,
    )
    curator = backend_factory.get_theme_curator_repository(settings)
    matching_repository = backend_factory.get_theme_matching_repository(settings)

    theme = curator.get_theme(scope.theme_id)
    parent_errors = validate_parent_theme(theme)
    if parent_errors:
        print("Cannot create scope — parent Theme check failed:")
        for error in parent_errors:
            print(f"  - {error}")
        return 1

    if matching_repository.get_scope(scope.theme_id) is not None:
        print("Cannot create scope — a scope already exists for this theme_id (scopes are insert-only).")
        return 1

    if not args.confirm:
        print("Dry run (pass --confirm to persist): content is well-formed, contains no placeholder text, "
              "and the parent Theme is eligible.")
        print(f"  Parent theme id: {scope.theme_id} (visibility: {theme.visibility.value})")
        print(f"  Sector tags: {list(scope.sector_tags)}")
        print(f"  Sector subtags: {list(scope.sector_subtags)}")
        print(f"  Allowed rule categories: {list(scope.allowed_matched_rule_categories)}")
        print(f"  Required keywords: {list(scope.required_keywords)}")
        print(f"  Excluded keywords: {list(scope.excluded_keywords)}")
        return 0

    created = persist_scope(scope, backend, cache_dir=cache_dir, sqlite_path=sqlite_path, postgres_url=postgres_url)
    print(f"scope for theme {scope.theme_id}: {'created' if created else 'already existed (unchanged)'}")
    return 0 if created else 1


if __name__ == "__main__":
    sys.exit(main())
