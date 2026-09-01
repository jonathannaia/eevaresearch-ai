"""EevaResearch — Citrini-style Theme research workspace vertical slice
(design/DECISIONS.md). A private, operator-only tool for exactly one
insert-only ThemeResearchNote (a hypothesis, a curator decision, or a
watch item) attached to an existing parent ResearchTheme. Not a product
feature: no application runtime module (app.py, any src/ui page)
imports this script, and it is never invoked automatically by anything
in this repository.

Invoke as (from the repo root), after the parent Theme already exists:

    python -m scripts.add_theme_research_note --confirm [--backend json|sqlite|postgres]

Notes are insert-only and append-only, exactly like evidence items and
company-map entries — this script has no update, edit, or delete
capability. Reassessing a hypothesis (e.g., raising or lowering
confidence as evidence accumulates) means authoring a NEW note, never
editing an old one — the research log is a chronological record of how
the team's thinking evolved.

Safety gates (mirrors every other authoring script in this codebase):

  1. AUTHORING_ENABLED below defaults to False. An operator must
     deliberately edit this file and set it to True before this script
     will ever consider persisting anything.
  2. Even with AUTHORING_ENABLED = True, the `--confirm` CLI flag is
     also required — without it, this is a dry run: build, validate,
     print what would happen, never write.
  3. A content-level placeholder-sentinel scan refuses to persist a
     note that still contains the literal REPLACE_ME string anywhere.

No external behavior: no network call, no LLM/model call, no
scanning/discovery, no worker invocation, no deployment, no automatic
publication, no visibility change (this script does not import
set_visibility). Never creates a Theme, evidence item, company-map
entry, match, or review decision — only a single ThemeResearchNote
through the existing ThemeCuratorRepositoryProtocol.insert_research_note
seam. This note type is never shown to any public user, regardless of
the parent Theme's own visibility — see backend_factory.py's own
ThemeRepositoryProtocol comment for why."""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from src.config.settings import Settings, get_settings
from src.data_access import backend_factory
from src.models.theme_research import HypothesisConfidence, ThemeNoteType, ThemeResearchNote

# =============================================================================
# SAFETY GATE 1 of 2 — see module docstring. Must be hand-edited to True.
# =============================================================================
AUTHORING_ENABLED = False

_PLACEHOLDER_SENTINEL = "REPLACE_ME"
_MAX_CONTENT_LENGTH = 2000

# =============================================================================
# OPERATOR-AUTHORED NOTE CONTENT — edit every value below with real,
# checked content before setting AUTHORING_ENABLED = True.
# confidence/disconfirming_condition are only meaningful for
# note_type == ThemeNoteType.HYPOTHESIS; leave both None otherwise.
# =============================================================================

_NOTE_THEME_ID = _PLACEHOLDER_SENTINEL  # the parent Theme's id
_NOTE_TYPE = ThemeNoteType.HYPOTHESIS  # edit to HYPOTHESIS / DECISION / WATCH_ITEM
_NOTE_CONTENT = _PLACEHOLDER_SENTINEL
_NOTE_CONFIDENCE: HypothesisConfidence | None = None  # edit to LOW/MEDIUM/HIGH only for HYPOTHESIS notes
_NOTE_DISCONFIRMING_CONDITION: str | None = None  # only for HYPOTHESIS notes
_NOTE_CREATED_AT = _PLACEHOLDER_SENTINEL  # e.g. "2026-09-01T00:00:00Z" or "...+00:00", an authored moment


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def build_authored_note(enable_authoring: bool = AUTHORING_ENABLED) -> ThemeResearchNote | None:
    """Pure, no I/O. Returns None when `enable_authoring` is False (the
    default). `enable_authoring` is a parameter so tests can exercise
    both branches without editing this file. The note's id is built
    inline here (sha256 of theme_id|note_type|content|created_at) —
    matching src.data_access.theme_store.build_theme_research_note_id's
    exact formula, computed locally rather than imported, so this
    function stays pure and importable without touching data_access."""
    if not enable_authoring:
        return None

    digest = hashlib.sha256(
        f"{_NOTE_THEME_ID}|{_NOTE_TYPE.value}|{_NOTE_CONTENT}|{_NOTE_CREATED_AT}".encode("utf-8")
    ).hexdigest()
    note_id = f"theme-note-{digest[:24]}"

    return ThemeResearchNote(
        id=note_id,
        theme_id=_NOTE_THEME_ID,
        note_type=_NOTE_TYPE,
        content=_NOTE_CONTENT,
        confidence=_NOTE_CONFIDENCE,
        disconfirming_condition=_NOTE_DISCONFIRMING_CONDITION,
        created_at=_NOTE_CREATED_AT,
    )


def contains_placeholder_sentinel(note: ThemeResearchNote) -> bool:
    values = [note.theme_id, note.content, note.created_at]
    if note.disconfirming_condition is not None:
        values.append(note.disconfirming_condition)
    return any(isinstance(v, str) and _PLACEHOLDER_SENTINEL in v for v in values)


def validate_note_content(note: ThemeResearchNote) -> tuple[str, ...]:
    """Plain, deterministic content checks — never raises."""
    errors: list[str] = []
    if not _nonblank(note.theme_id):
        errors.append("note.theme_id must not be blank.")
    if not _nonblank(note.content):
        errors.append("note.content must not be blank.")
    elif len(note.content) > _MAX_CONTENT_LENGTH:
        errors.append(f"note.content exceeds the maximum length of {_MAX_CONTENT_LENGTH} characters.")
    if not _nonblank(note.created_at):
        errors.append("note.created_at must not be blank.")

    if note.note_type is ThemeNoteType.HYPOTHESIS:
        if note.confidence is None:
            errors.append("note.confidence is required for a HYPOTHESIS note.")
        if not _nonblank(note.disconfirming_condition):
            errors.append("note.disconfirming_condition is required for a HYPOTHESIS note.")
    else:
        if note.confidence is not None:
            errors.append("note.confidence must be None for a non-HYPOTHESIS note.")
        if note.disconfirming_condition is not None:
            errors.append("note.disconfirming_condition must be None for a non-HYPOTHESIS note.")

    return tuple(errors)


def persist_note(note: ThemeResearchNote, backend: str, cache_dir=None, sqlite_path=None, postgres_url=None) -> bool:
    settings = Settings(
        db_backend=backend,
        cache_dir=Path(cache_dir) if cache_dir else Settings().cache_dir,
        state_db_path=Path(sqlite_path) if sqlite_path else None,
        state_db_url=postgres_url,
    )
    curator = backend_factory.get_theme_curator_repository(settings)
    return curator.insert_research_note(note)


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

    built = build_authored_note(AUTHORING_ENABLED)
    if built is None:
        print("AUTHORING_ENABLED is False — this script is disabled by default. Edit "
              "scripts/add_theme_research_note.py, fill in real content in place of every "
              "REPLACE_ME placeholder, and set AUTHORING_ENABLED = True before re-running.")
        return 0

    note = built
    if contains_placeholder_sentinel(note):
        print(f"Refusing to proceed: the authored content still contains the {_PLACEHOLDER_SENTINEL!r} "
              "placeholder in one or more fields. Replace every placeholder with real, checked content "
              "before running again.")
        return 1

    errors = validate_note_content(note)
    if errors:
        print("Note content is invalid — nothing was persisted. Issues:")
        for error in errors:
            print(f"  - {error}")
        return 1

    if not args.confirm:
        print("Dry run (pass --confirm to persist): content is well-formed and contains no placeholder text.")
        print(f"  Theme id: {note.theme_id}")
        print(f"  Note type: {note.note_type.value}")
        print(f"  Note id: {note.id}")
        return 0

    backend, cache_dir, sqlite_path, postgres_url = _resolve_backend_settings(args)
    created = persist_note(note, backend, cache_dir=cache_dir, sqlite_path=sqlite_path, postgres_url=postgres_url)
    print(f"note {note.id}: {'created' if created else 'already existed (unchanged)'}")
    return 0 if created else 1


if __name__ == "__main__":
    sys.exit(main())
