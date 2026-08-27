"""Theme Registry Foundation — matching/normalization tests
(design/THEME_REGISTRY_FOUNDATION.md).

Pure/offline: no network, no repository writes, no Docker, no database.
The structural guard tests at the bottom are the ones proving theme
matching cannot alter or bypass the human-review publication
lifecycle — by AST-inspecting this phase's own source, not merely by
behavioral inspection."""
from __future__ import annotations

import ast
from pathlib import Path

from src.config.theme_registry_loader import load_theme_registry
from src.logic.theme_matching import ThemeMatch, match_themes, normalize_text
from src.models.theme_registry import MatchingPolicy, Theme, ThemeRegistry

_REAL_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "config" / "eevaresearch_theme_registry.yaml"


def _minimal_registry(themes: tuple[Theme, ...]) -> ThemeRegistry:
    from src.models.theme_registry import ClassificationOutputContract

    return ThemeRegistry(
        version=1, name="test", primary_language="en",
        matching_policy=MatchingPolicy(
            minimum_theme_evidence=1, require_material_event_for_candidate=True,
            allow_multi_theme_assignment=True, do_not_auto_publish=True,
            source_priority=(), common_material_event_patterns=(),
        ),
        geographies=(), entity_roles=(), materiality_keywords_high=(), materiality_keywords_low=(),
        cross_theme_relationships=(), themes=themes,
        classification_output_contract=ClassificationOutputContract(
            required_fields=(), initial_candidate_status="NEW_FILING_EVENT",
            autonomous_statuses_forbidden=("PUBLISHED", "MONITORING", "DISMISSED"),
        ),
    )


def _theme(theme_id: str, aliases: tuple[str, ...], event_patterns: tuple[str, ...] = ()) -> Theme:
    return Theme(
        id=theme_id, name=theme_id, priority="critical", description="",
        aliases=aliases, event_patterns=event_patterns,
        normalized_aliases=tuple(normalize_text(a) for a in aliases),
    )


# --- normalize_text: Unicode NFKC + casefold ---

def test_normalize_text_casefolds_ascii():
    assert normalize_text("GPU") == normalize_text("gpu") == "gpu"


def test_normalize_text_folds_fullwidth_latin_to_halfwidth():
    # U+FF27 U+FF30 U+FF35 = fullwidth "GPU"
    fullwidth_gpu = "ＧＰＵ"
    assert normalize_text(fullwidth_gpu) == normalize_text("GPU")


def test_normalize_text_preserves_and_matches_korean():
    assert normalize_text("데이터센터 AI") == normalize_text("데이터센터 AI")
    assert normalize_text("데이터센터 AI") != normalize_text("전혀 다른 문자열")


# --- match_themes: pure, in-process, no loader/file dependency ---

def test_match_themes_finds_single_theme_by_alias():
    registry = _minimal_registry((_theme("alpha_theme", ("widget",)),))
    matches = match_themes(registry, "The company shipped a new widget this quarter.")
    assert [m.theme_id for m in matches] == ["alpha_theme"]
    assert matches[0].matched_aliases == ("widget",)


def test_match_themes_is_case_insensitive_and_unicode_normalized():
    registry = _minimal_registry((_theme("alpha_theme", ("GPU",)),))
    fullwidth_gpu_in_text = "New ＧＰＵ announced today."
    matches = match_themes(registry, fullwidth_gpu_in_text)
    assert [m.theme_id for m in matches] == ["alpha_theme"]


def test_match_themes_returns_multiple_themes_not_just_one():
    registry = _minimal_registry((
        _theme("alpha_theme", ("widget",)),
        _theme("beta_theme", ("gadget",)),
    ))
    matches = match_themes(registry, "Announced both a new widget and a new gadget.")
    assert {m.theme_id for m in matches} == {"alpha_theme", "beta_theme"}


def test_match_themes_returns_nothing_for_no_alias_match():
    registry = _minimal_registry((_theme("alpha_theme", ("widget",)),))
    assert match_themes(registry, "Completely unrelated text.") == ()


def test_match_themes_includes_matched_event_patterns():
    registry = _minimal_registry((_theme("alpha_theme", ("widget",), event_patterns=("product launch",)),))
    matches = match_themes(registry, "New widget product launch announced.")
    assert matches[0].matched_event_patterns == ("product launch",)


# --- End-to-end against the real committed registry ---

def test_end_to_end_multilingual_and_multitheme_against_real_registry():
    registry = load_theme_registry(_REAL_REGISTRY_PATH)
    text = "The company announced a 데이터센터 AI accelerator and a silicon photonics partnership."
    matches = match_themes(registry, text)
    theme_ids = {m.theme_id for m in matches}
    assert "ai_infrastructure_and_semiconductors" in theme_ids
    assert "optical_networking_and_data_center_connectivity" in theme_ids
    # Universal company-event layer also matches "partnership".
    assert "company_specific_catalysts" in theme_ids


# --- Structural guards: theme matching cannot alter publication status ---

_MODULES_UNDER_GUARD = (
    Path(__file__).resolve().parent.parent / "src" / "logic" / "theme_matching.py",
    Path(__file__).resolve().parent.parent / "src" / "config" / "theme_registry_loader.py",
    Path(__file__).resolve().parent.parent / "src" / "models" / "theme_registry.py",
)

_FORBIDDEN_IMPORT_SUBSTRINGS = (
    "review_actions", "signal_promotion", "SignalRepository", "backend_factory",
    "data_access.container", "record_review_decision",
)


def test_theme_registry_modules_import_nothing_review_or_publish_related():
    for module_path in _MODULES_UNDER_GUARD:
        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    imported.add(f"{module}.{alias.name}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)

        for forbidden in _FORBIDDEN_IMPORT_SUBSTRINGS:
            assert not any(forbidden in name for name in imported), (
                f"{module_path.name} must not import anything matching {forbidden!r}"
            )


def test_theme_registry_modules_have_no_repository_or_network_imports():
    """No requests/psycopg/sqlite3/httpx anywhere in this phase's own
    three modules — theme matching has zero I/O of its own."""
    for module_path in _MODULES_UNDER_GUARD:
        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported_roots.add(alias.name.split(".")[0])

        for forbidden_root in ("requests", "psycopg", "sqlite3", "httpx", "streamlit"):
            assert forbidden_root not in imported_roots


def test_theme_match_objects_carry_no_status_field():
    """ThemeMatch is classification metadata only — it structurally
    cannot represent (let alone set) a CandidateStatus."""
    match = ThemeMatch(theme_id="x", matched_aliases=(), matched_event_patterns=())
    assert not hasattr(match, "status")


def test_matching_never_constructs_a_candidate_signal():
    """No reference to CandidateSignal construction anywhere in the
    matching module's source — matching only ever reads text in and
    returns ThemeMatch records out."""
    source = (Path(__file__).resolve().parent.parent / "src" / "logic" / "theme_matching.py").read_text(
        encoding="utf-8",
    )
    assert "CandidateSignal(" not in source
