"""EevaResearch — Phase A1 (design/DECISIONS.md). Append-only(-mostly)
JSON persistence for the internal theme-matching model family
(src.models.theme_matching). Three dedicated sibling files, never mixed
into any existing candidate/comparison/Research Case/Theme store:

    theme_matching_scopes.json
    research_case_theme_matches.json
    theme_match_review_decisions.json

Wholly internal: nothing here is imported by src/ui/pages/themes_research.py,
any other public UI page, or ThemeRepositoryProtocol (the public/UI-
facing, published-only Theme read seam in
src/data_access/backend_factory.py). `list_active_scopes()` is the one
function that reads the separate, existing theme_store.load_themes()
— a read-only cross-store lookup needed to derive "active" from the
referenced ResearchTheme's own visibility (see that function's own
docstring); it never mutates Theme data.

Insert-only for every record type in this phase — no update/replace/
upsert/delete function exists anywhere in this module.
`theme_matching_scopes` is keyed by theme_id itself (one scope per
theme; a curator wanting to revise an existing scope is out of scope
until a later, separately approved model change adds real
revisioning — see src.models.theme_matching's own docstring).
`research_case_theme_matches` and `theme_match_review_decisions` are
both insert-only and immutable — "pending review" is derived purely
from a match having no decision row, never a mutable status column.

No wall-clock reads anywhere in this module — every timestamp is
caller-supplied. No import of CandidateSignal, FilingEvent, any source
client, or any UI type."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from src.data_access import theme_store
from src.models.theme_matching import (
    MatchConfidence,
    MatchReviewStatus,
    ResearchCaseThemeMatch,
    ThemeMatchingScope,
    ThemeMatchReviewDecision,
)
from src.models.theme_research import EvidenceDirection, ThemeVisibility

_SCOPES_FILENAME = "theme_matching_scopes.json"
_MATCHES_FILENAME = "research_case_theme_matches.json"
_REVIEW_DECISIONS_FILENAME = "theme_match_review_decisions.json"

_ID_DIGEST_CHARS = 24


def build_review_decision_id(match_id: str, reviewed_at: str) -> str:
    """Deterministic, content-addressed id for one review decision — the
    same sha256-truncated-hex convention already established throughout
    this codebase. A caller must still supply real decision content
    (`decision`, `reviewer_note`); this helper never calls a clock and
    never invents `reviewed_at` itself."""
    digest = hashlib.sha256(f"{match_id}|{reviewed_at}".encode("utf-8")).hexdigest()
    return f"theme-match-review-{digest[:_ID_DIGEST_CHARS]}"


# --- Matching scopes ---------------------------------------------------------


def _scope_from_dict(data: dict) -> ThemeMatchingScope:
    return ThemeMatchingScope(
        theme_id=data["theme_id"],
        sector_tags=tuple(data["sector_tags"]),
        sector_subtags=tuple(data["sector_subtags"]),
        allowed_matched_rule_categories=tuple(data["allowed_matched_rule_categories"]),
        required_keywords=tuple(data["required_keywords"]),
        excluded_keywords=tuple(data["excluded_keywords"]),
    )


def load_scopes(cache_dir: Path, filename: str = _SCOPES_FILENAME) -> dict[str, ThemeMatchingScope]:
    """Never raises — a missing or corrupt file is treated as an empty
    collection, matching every other store in this codebase's
    tolerant-load convention. Keyed by theme_id."""
    path = cache_dir / filename
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, ThemeMatchingScope] = {}
    for theme_id, data in raw.items():
        try:
            result[theme_id] = _scope_from_dict(data)
        except (KeyError, TypeError, ValueError):
            continue
    return result


def get_scope(cache_dir: Path, theme_id: str, filename: str = _SCOPES_FILENAME) -> ThemeMatchingScope | None:
    return load_scopes(cache_dir, filename).get(theme_id)


def _save_scopes(cache_dir: Path, scopes: dict[str, ThemeMatchingScope], filename: str = _SCOPES_FILENAME) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {theme_id: asdict(scope) for theme_id, scope in scopes.items()}
    (cache_dir / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def insert_scope(cache_dir: Path, scope: ThemeMatchingScope, filename: str = _SCOPES_FILENAME) -> bool:
    """INSERT-only: True when newly inserted, False when a scope for
    this exact theme_id already existed — the already-persisted scope
    is left completely untouched, never overwritten."""
    scopes = load_scopes(cache_dir, filename)
    if scope.theme_id in scopes:
        return False
    scopes[scope.theme_id] = scope
    _save_scopes(cache_dir, scopes, filename)
    return True


def list_active_scopes(
    cache_dir: Path, scopes_filename: str = _SCOPES_FILENAME,
) -> tuple[ThemeMatchingScope, ...]:
    """A scope is active exactly when its referenced ResearchTheme's
    own `visibility` is anything other than ARCHIVED — internal,
    ready_to_publish, and published Themes are all eligible; there is
    no separate scope-level "is_active" field in this phase (see
    src.models.theme_matching's own docstring on why). A scope whose
    theme_id has no corresponding ResearchTheme at all (a data
    inconsistency, never expected in practice) is conservatively
    treated as inactive rather than included. Deterministically ordered
    by theme_id. Reads research_themes read-only via theme_store —
    never mutates Theme data."""
    scopes = load_scopes(cache_dir, scopes_filename)
    if not scopes:
        return ()
    themes = theme_store.load_themes(cache_dir)
    active = [
        scope for scope in scopes.values()
        if (theme := themes.get(scope.theme_id)) is not None and theme.visibility != ThemeVisibility.ARCHIVED
    ]
    return tuple(sorted(active, key=lambda scope: scope.theme_id))


# --- Matches -------------------------------------------------------------


def _match_from_dict(data: dict) -> ResearchCaseThemeMatch:
    return ResearchCaseThemeMatch(
        id=data["id"],
        case_id=data["case_id"],
        theme_id=data["theme_id"],
        confidence=MatchConfidence(data["confidence"]),
        direction=EvidenceDirection(data["direction"]),
        matched_sector_tag=data.get("matched_sector_tag"),
        matched_rule_categories=tuple(data["matched_rule_categories"]),
        matched_keywords=tuple(data["matched_keywords"]),
        rationale=data["rationale"],
        created_at=data["created_at"],
    )


def load_matches(cache_dir: Path, filename: str = _MATCHES_FILENAME) -> dict[str, ResearchCaseThemeMatch]:
    path = cache_dir / filename
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, ResearchCaseThemeMatch] = {}
    for match_id, data in raw.items():
        try:
            result[match_id] = _match_from_dict(data)
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _save_matches(cache_dir: Path, matches: dict[str, ResearchCaseThemeMatch], filename: str = _MATCHES_FILENAME) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {match_id: asdict(match) for match_id, match in matches.items()}
    (cache_dir / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def insert_match(cache_dir: Path, match: ResearchCaseThemeMatch, filename: str = _MATCHES_FILENAME) -> bool:
    """INSERT-only: True when newly inserted, False when a match with
    this exact id already existed. Since `match.id` is itself
    deterministically derived from (case_id, theme_id) (see
    src.logic.research_case_theme_matching), this single id-existence
    check is also a full (case_id, theme_id) duplicate check — no
    separate pair lookup is needed."""
    matches = load_matches(cache_dir, filename)
    if match.id in matches:
        return False
    matches[match.id] = match
    _save_matches(cache_dir, matches, filename)
    return True


def existing_match_ids_for_case_ids(
    cache_dir: Path, case_ids: Sequence[str], filename: str = _MATCHES_FILENAME,
) -> frozenset[str]:
    """Bulk, read-only: the ids of every persisted match whose case_id
    is one of the supplied `case_ids` — one load for a non-empty
    request; empty input returns `frozenset()` immediately without
    loading anything."""
    if not case_ids:
        return frozenset()
    matches = load_matches(cache_dir, filename)
    case_id_set = set(case_ids)
    return frozenset(match.id for match in matches.values() if match.case_id in case_id_set)


def list_pending_matches(
    cache_dir: Path, matches_filename: str = _MATCHES_FILENAME, decisions_filename: str = _REVIEW_DECISIONS_FILENAME,
) -> tuple[ResearchCaseThemeMatch, ...]:
    """Every match with zero rows in theme_match_review_decisions —
    the pending queue is derived entirely from this absence, never a
    stored status field. Deterministically ordered by (created_at, id)."""
    matches = load_matches(cache_dir, matches_filename)
    if not matches:
        return ()
    decisions = load_review_decisions(cache_dir, decisions_filename)
    decided_match_ids = {decision.match_id for decision in decisions.values()}
    pending = [match for match in matches.values() if match.id not in decided_match_ids]
    return tuple(sorted(pending, key=lambda match: (match.created_at, match.id)))


# --- Review decisions ---------------------------------------------------------


def _review_decision_from_dict(data: dict) -> ThemeMatchReviewDecision:
    return ThemeMatchReviewDecision(
        id=data["id"],
        match_id=data["match_id"],
        decision=MatchReviewStatus(data["decision"]),
        reviewer_note=data.get("reviewer_note"),
        reviewed_at=data["reviewed_at"],
    )


def load_review_decisions(cache_dir: Path, filename: str = _REVIEW_DECISIONS_FILENAME) -> dict[str, ThemeMatchReviewDecision]:
    path = cache_dir / filename
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, ThemeMatchReviewDecision] = {}
    for decision_id, data in raw.items():
        try:
            result[decision_id] = _review_decision_from_dict(data)
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _save_review_decisions(
    cache_dir: Path, decisions: dict[str, ThemeMatchReviewDecision], filename: str = _REVIEW_DECISIONS_FILENAME,
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {decision_id: asdict(decision) for decision_id, decision in decisions.items()}
    (cache_dir / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def insert_review_decision(
    cache_dir: Path, decision: ThemeMatchReviewDecision, filename: str = _REVIEW_DECISIONS_FILENAME,
) -> bool:
    """INSERT-only: True when newly inserted, False when a decision
    with this exact id already existed. Decisions are never updated in
    place — a corrected decision is a new, separately-authored record,
    never an edit of an existing one."""
    decisions = load_review_decisions(cache_dir, filename)
    if decision.id in decisions:
        return False
    decisions[decision.id] = decision
    _save_review_decisions(cache_dir, decisions, filename)
    return True


def list_review_decisions_for_match(
    cache_dir: Path, match_id: str, filename: str = _REVIEW_DECISIONS_FILENAME,
) -> tuple[ThemeMatchReviewDecision, ...]:
    """Every decision recorded against one match, in one load, ordered
    deterministically by (reviewed_at, id) — the complete, immutable
    audit trail for that match."""
    decisions = load_review_decisions(cache_dir, filename)
    matching = [decision for decision in decisions.values() if decision.match_id == match_id]
    return tuple(sorted(matching, key=lambda decision: (decision.reviewed_at, decision.id)))
