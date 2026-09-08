"""EevaResearch — Citrini-style Theme research workspace vertical slice
(design/DECISIONS.md). A private, operator-only tool that promotes one
already-ACCEPTED `ThemeMatchReviewDecision` into a real
`ThemeEvidenceItem` on its match's parent Theme — the "ingest radar
evidence" step of the research workspace. Not a product feature: no
application runtime module (app.py, any src/ui page) imports this
script, and it is never invoked automatically by anything in this
repository, including scripts/radar_worker.py.

Invoke as (from the repo root), after a match has already been created
by the deterministic matcher and reviewed/accepted by a human:

    python -m scripts.promote_match_to_evidence --confirm [--backend json|sqlite|postgres]

Safety gates (mirrors every other authoring script in this codebase):

  1. AUTHORING_ENABLED below defaults to False. An operator must
     deliberately edit this file and set it to True before this script
     will ever consider persisting anything.
  2. Even with AUTHORING_ENABLED = True, the `--confirm` CLI flag is
     also required — without it, this is a dry run: build, validate,
     print what would happen, never write.
  3. A content-level placeholder-sentinel scan refuses to persist
     content that still contains the literal REPLACE_ME string anywhere.

This script refuses to proceed unless an ACCEPTED review decision
already exists for the named match — a pending or rejected decision (or
no decision at all) is never eligible for promotion; this is a purely
mechanical, already-decided step, never a place where review happens.
The actual evidence-building logic is the pure, no-I/O
src.logic.theme_evidence_promotion.build_evidence_from_accepted_match —
this script only fetches the already-persisted match/decision/candidate,
supplies the operator-authored direction/fact/relevance, and performs
the one insert through the existing ThemeCuratorRepositoryProtocol.
insert_evidence_item seam.

No external behavior: no network call, no source fetch, no LLM/model
call, no scanning/discovery, no worker invocation, no deployment, no
automatic publication, no visibility change (this script does not
import set_visibility). EDGAR-only for now, matching this codebase's
existing EDGAR-only matching worker (Phase A2) — the candidate lookup
below is hardcoded to the "SEC EDGAR" source; DART/EDINET matches are
not yet supported by this promotion path."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.config.settings import Settings, get_settings
from src.data_access import backend_factory
from src.logic.theme_evidence_promotion import build_evidence_from_accepted_match
from src.models.theme_matching import MatchReviewStatus
from src.models.theme_research import EvidenceDirection

# =============================================================================
# SAFETY GATE 1 of 2 — see module docstring. Must be hand-edited to True.
# =============================================================================
AUTHORING_ENABLED = False

_PLACEHOLDER_SENTINEL = "REPLACE_ME"

# EDGAR-only for now — see module docstring.
_CANDIDATE_SOURCE_NAME = "SEC EDGAR"

# =============================================================================
# OPERATOR-AUTHORED PROMOTION CONTENT — edit every value below with real,
# checked content before setting AUTHORING_ENABLED = True.
# =============================================================================

_MATCH_ID = _PLACEHOLDER_SENTINEL  # the ResearchCaseThemeMatch id being promoted
_DIRECTION = EvidenceDirection.CONTEXT  # edit to SUPPORTS / CONTRADICTS / MIXED / CONTEXT
_FACT = _PLACEHOLDER_SENTINEL  # the observed fact, not an interpretation
_RELEVANCE = _PLACEHOLDER_SENTINEL  # why this fact matters to the theme


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def contains_placeholder_sentinel() -> bool:
    return any(_PLACEHOLDER_SENTINEL in v for v in (_MATCH_ID, _FACT, _RELEVANCE) if isinstance(v, str))


def _resolve_backend_settings(args: argparse.Namespace):
    settings = get_settings()
    backend = args.backend or settings.db_backend or "json"
    cache_dir = args.cache_dir or settings.cache_dir
    sqlite_path = args.sqlite_path or (str(settings.state_db_path) if settings.state_db_path else None)
    postgres_url = args.postgres_url or settings.state_db_url
    return backend, cache_dir, sqlite_path, postgres_url


def _build_settings(backend: str, cache_dir=None, sqlite_path=None, postgres_url=None) -> Settings:
    return Settings(
        db_backend=backend,
        cache_dir=Path(cache_dir) if cache_dir else Settings().cache_dir,
        state_db_path=Path(sqlite_path) if sqlite_path else None,
        state_db_url=postgres_url,
    )


def gather_promotion_inputs(settings: Settings, match_id: str):
    """Reads (never writes) the match, its ACCEPTED decision, the
    originating Research Case, and the originating CandidateSignal.
    Returns (match, decision, candidate, errors) — errors is a tuple of
    human-readable strings; when non-empty, the other three fields are
    not to be trusted/used."""
    matching_repository = backend_factory.get_theme_matching_repository(settings)
    match = matching_repository.get_match(match_id)
    if match is None:
        return None, None, None, ("No match found with this id.",)

    decisions = matching_repository.list_review_decisions_for_match(match_id)
    accepted = [d for d in decisions if d.decision is MatchReviewStatus.ACCEPTED]
    if not accepted:
        return match, None, None, ("No ACCEPTED review decision exists for this match — nothing to promote.",)
    decision = accepted[-1]  # most recent ACCEPTED decision, per list's own (reviewed_at, id) ordering

    research_case_repository = backend_factory.get_research_case_repository(settings)
    case = research_case_repository.get_case(match.case_id)
    if case is None:
        return match, decision, None, ("The match's own Research Case no longer exists.",)

    candidate_repository = backend_factory.get_candidate_repository(settings, _CANDIDATE_SOURCE_NAME)
    candidate = candidate_repository.get_candidate(case.trigger_source_id)
    if candidate is None:
        return match, decision, None, ("The originating CandidateSignal no longer exists in the EDGAR candidate store.",)

    return match, decision, candidate, ()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("json", "sqlite", "postgres"), default=None)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--sqlite-path", default=None)
    parser.add_argument("--postgres-url", default=None)
    parser.add_argument("--confirm", action="store_true", help="Required to actually persist. Without it: dry run only.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if not AUTHORING_ENABLED:
        print("AUTHORING_ENABLED is False — this script is disabled by default. Edit "
              "scripts/promote_match_to_evidence.py, fill in real content in place of every "
              "REPLACE_ME placeholder, and set AUTHORING_ENABLED = True before re-running.")
        return 0

    if contains_placeholder_sentinel():
        print(f"Refusing to proceed: the authored content still contains the {_PLACEHOLDER_SENTINEL!r} "
              "placeholder in one or more fields. Replace every placeholder with real, checked content "
              "before running again.")
        return 1

    if not _nonblank(_MATCH_ID) or not _nonblank(_FACT) or not _nonblank(_RELEVANCE):
        print("Promotion content is invalid — nothing was persisted. _MATCH_ID/_FACT/_RELEVANCE must not be blank.")
        return 1

    backend, cache_dir, sqlite_path, postgres_url = _resolve_backend_settings(args)
    settings = _build_settings(backend, cache_dir=cache_dir, sqlite_path=sqlite_path, postgres_url=postgres_url)

    match, decision, candidate, errors = gather_promotion_inputs(settings, _MATCH_ID)
    if errors:
        print("Cannot promote this match — checks failed:")
        for error in errors:
            print(f"  - {error}")
        return 1

    evidence = build_evidence_from_accepted_match(candidate, match, decision, _DIRECTION, _FACT, _RELEVANCE)
    if evidence is None:
        print("Cannot promote this match — the built evidence item failed validation (see "
              "build_evidence_from_accepted_match's own contract). Nothing was persisted.")
        return 1

    if not args.confirm:
        print("Dry run (pass --confirm to persist): match/decision/candidate checks passed, "
              "and the built evidence item is well-formed.")
        print(f"  Theme id: {evidence.theme_id}")
        print(f"  Evidence id: {evidence.id}")
        print(f"  Direction: {evidence.direction.value}")
        print(f"  Company: {evidence.company}")
        print(f"  Source: {evidence.source_name} ({evidence.source_url})")
        return 0

    curator = backend_factory.get_theme_curator_repository(settings)
    created = curator.insert_evidence_item(evidence)
    print(f"evidence {evidence.id}: {'created' if created else 'already existed (unchanged)'}")
    return 0 if created else 1


if __name__ == "__main__":
    sys.exit(main())
