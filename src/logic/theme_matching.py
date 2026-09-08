"""Theme Registry Foundation — pure, read-only theme classification
matching (design/THEME_REGISTRY_FOUNDATION.md).

No file I/O, no repository access, no network access, no candidate
construction, no materiality scoring, no status mutation, and no import
of review_actions, signal_promotion, backend_factory, container, or any
SignalRepository. Theme matching is a classification/retrieval hint
only — the registry's own matching_policy.do_not_auto_publish is true
in the committed file, and this module structurally cannot violate it,
since it never touches persistence of any kind. A structural test
(tests/test_theme_matching.py) asserts this module's own source
contains none of those imports, not just that it behaves correctly by
inspection.

Unicode handling: NFKC normalization (folds full-width/half-width and
compatibility characters to one canonical form) followed by casefold()
(the Unicode-correct case-insensitive comparison — not a bare .lower(),
which is documented-incorrect for e.g. German ß and several CJK
contexts). Applied identically to registry aliases (once, at load time,
cached on each Theme as `normalized_aliases` — see
src/config/theme_registry_loader.py) and to incoming text (at match
time, here).

Matching is deliberately simple and deterministic: a plain substring
check against normalized text, never fuzzy matching or an ML/embedding
classifier — consistent with this phase's own scope (Durable-State
Theme Registry Foundation), which explicitly excludes anything beyond
deterministic alias/event-pattern matching."""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from src.models.theme_registry import Theme, ThemeRegistry


def normalize_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


@dataclass(frozen=True)
class ThemeMatch:
    """Classification/retrieval metadata only — never a CandidateSignal,
    never a Signal, and never anything carrying a `status` field. See
    this module's own docstring for the full non-goal list."""

    theme_id: str
    matched_aliases: tuple[str, ...]
    matched_event_patterns: tuple[str, ...]


def _matched_aliases_for_theme(theme: Theme, normalized_text: str) -> tuple[str, ...]:
    matched = []
    for original_alias, normalized_alias in zip(theme.aliases, theme.normalized_aliases):
        if normalized_alias and normalized_alias in normalized_text:
            matched.append(original_alias)
    return tuple(matched)


def _matched_event_patterns_for_theme(theme: Theme, normalized_text: str) -> tuple[str, ...]:
    matched = []
    for pattern in theme.event_patterns:
        if pattern and normalize_text(pattern) in normalized_text:
            matched.append(pattern)
    return tuple(matched)


def match_themes(registry: ThemeRegistry, text: str) -> tuple[ThemeMatch, ...]:
    """Returns every theme whose alias appears in `text` — never a
    single "best" theme, matching the registry's own
    matching_policy.allow_multi_theme_assignment. Pure function: no
    argument or return value is ever written anywhere by this function
    itself."""
    normalized = normalize_text(text)
    matches = []
    for theme in registry.themes:
        matched_aliases = _matched_aliases_for_theme(theme, normalized)
        if not matched_aliases:
            continue
        matches.append(ThemeMatch(
            theme_id=theme.id,
            matched_aliases=matched_aliases,
            matched_event_patterns=_matched_event_patterns_for_theme(theme, normalized),
        ))
    return tuple(matches)
