"""EevaResearch — Evidence-First Themes MVP (design/DECISIONS.md).
Append-only(-mostly) JSON persistence for the public ResearchTheme model
family (src.models.theme_research). Three dedicated sibling files, never
mixed into any existing candidate/comparison/Daily News/Research Case
store:

    themes.json
    theme_evidence_items.json
    theme_company_map.json

Insert-only for evidence and company-map entries — no update/replace/
upsert/delete function exists for either. `ResearchTheme` itself is the
one deliberate exception: `set_theme_visibility()` is a narrow, private,
curator-only update path (publishing is inherently a status
transition — internal -> ready_to_publish -> published -> archived —
not an append-only fact), never exposed to the public/UI-facing read
protocol in backend_factory.py.

No wall-clock reads anywhere in this module — every timestamp is
caller-supplied. No import of CandidateSignal, FilingEvent, ResearchCase,
any source client, or any UI type — only src.models.theme_research and
the standard library."""
from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from src.models.theme_research import (
    CompanyRole,
    EvidenceDirection,
    HypothesisConfidence,
    ResearchTheme,
    ThemeCategory,
    ThemeCompanyMapEntry,
    ThemeEvidenceItem,
    ThemeNoteType,
    ThemeResearchNote,
    ThemeStatus,
    ThemeVisibility,
)

_THEMES_FILENAME = "themes.json"
_EVIDENCE_FILENAME = "theme_evidence_items.json"
_COMPANY_MAP_FILENAME = "theme_company_map.json"
_RESEARCH_NOTES_FILENAME = "theme_research_notes.json"

_ID_DIGEST_CHARS = 24


# --- Deterministic, content-addressed ID factories -------------------------
# Same sha256-truncated-hex technique already established throughout this
# codebase (comparison_store.build_comparison_record_id, research_store.
# build_case_id) — never randomness, never wall-clock state.


def build_theme_id(title: str, created_at: str) -> str:
    digest = hashlib.sha256(f"{title}|{created_at}".encode("utf-8")).hexdigest()
    return f"theme-{digest[:_ID_DIGEST_CHARS]}"


def build_theme_evidence_id(theme_id: str, source_url: str, date: str) -> str:
    digest = hashlib.sha256(f"{theme_id}|{source_url}|{date}".encode("utf-8")).hexdigest()
    return f"theme-evidence-{digest[:_ID_DIGEST_CHARS]}"


def build_theme_company_map_id(theme_id: str, company_name: str, role: CompanyRole) -> str:
    digest = hashlib.sha256(f"{theme_id}|{company_name}|{role.value}".encode("utf-8")).hexdigest()
    return f"theme-company-{digest[:_ID_DIGEST_CHARS]}"


def build_theme_research_note_id(theme_id: str, note_type: ThemeNoteType, content: str, created_at: str) -> str:
    digest = hashlib.sha256(f"{theme_id}|{note_type.value}|{content}|{created_at}".encode("utf-8")).hexdigest()
    return f"theme-note-{digest[:_ID_DIGEST_CHARS]}"


# --- Themes ------------------------------------------------------------------


def _theme_from_dict(data: dict) -> ResearchTheme:
    return ResearchTheme(
        id=data["id"],
        category=ThemeCategory(data["category"]),
        status=ThemeStatus(data["status"]),
        visibility=ThemeVisibility(data["visibility"]),
        title=data["title"],
        key_question=data["key_question"],
        hypothesis=data["hypothesis"],
        working_thesis=data["working_thesis"],
        why_it_matters=data["why_it_matters"],
        what_could_change_the_view=data["what_could_change_the_view"],
        what_to_watch_next=data["what_to_watch_next"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


def load_themes(cache_dir: Path, filename: str = _THEMES_FILENAME) -> dict[str, ResearchTheme]:
    """Never raises — a missing or corrupt file is treated as an empty
    collection, matching every other store in this codebase's
    tolerant-load convention."""
    path = cache_dir / filename
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, ResearchTheme] = {}
    for theme_id, data in raw.items():
        try:
            result[theme_id] = _theme_from_dict(data)
        except (KeyError, TypeError, ValueError):
            continue
    return result


def get_theme(cache_dir: Path, theme_id: str, filename: str = _THEMES_FILENAME) -> ResearchTheme | None:
    """Read-only single-theme lookup, any visibility — for curator/
    private use only. The public/UI-facing lookup is
    get_published_theme() below, which additionally enforces
    visibility == PUBLISHED."""
    return load_themes(cache_dir, filename).get(theme_id)


def get_published_theme(cache_dir: Path, theme_id: str, filename: str = _THEMES_FILENAME) -> ResearchTheme | None:
    """A theme that exists but is not PUBLISHED behaves identically to
    one that does not exist at all — the one enforcement point every
    public read path must go through."""
    theme = get_theme(cache_dir, theme_id, filename)
    if theme is None or theme.visibility != ThemeVisibility.PUBLISHED:
        return None
    return theme


def list_published_themes(cache_dir: Path, filename: str = _THEMES_FILENAME) -> tuple[ResearchTheme, ...]:
    """Every PUBLISHED theme, deterministically ordered by
    (updated_at DESC, id DESC) — never dict/file iteration order. Never
    loads/returns a non-published theme; the public UI never filters
    this result further."""
    themes = load_themes(cache_dir, filename)
    published = [theme for theme in themes.values() if theme.visibility == ThemeVisibility.PUBLISHED]
    return tuple(sorted(published, key=lambda theme: (theme.updated_at, theme.id), reverse=True))


def _save_themes(cache_dir: Path, themes: dict[str, ResearchTheme], filename: str = _THEMES_FILENAME) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {theme_id: asdict(theme) for theme_id, theme in themes.items()}
    (cache_dir / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_theme(cache_dir: Path, theme: ResearchTheme, filename: str = _THEMES_FILENAME) -> bool:
    """INSERT-only: returns True when this was a new id, False when a
    theme with this exact id already existed — the already-persisted
    theme is left completely untouched."""
    themes = load_themes(cache_dir, filename)
    if theme.id in themes:
        return False
    themes[theme.id] = theme
    _save_themes(cache_dir, themes, filename)
    return True


def set_theme_visibility(
    cache_dir: Path, theme_id: str, new_visibility: ThemeVisibility, updated_at: str, filename: str = _THEMES_FILENAME,
) -> ResearchTheme | None:
    """The one deliberate update path in this module — see module
    docstring for why publishing needs this while evidence/company-map
    entries stay strictly append-only. Curator/private use only; never
    exposed to the public read protocol. Returns the updated theme, or
    None if `theme_id` doesn't exist (no-op, nothing written)."""
    themes = load_themes(cache_dir, filename)
    existing = themes.get(theme_id)
    if existing is None:
        return None
    updated = dataclasses.replace(existing, visibility=new_visibility, updated_at=updated_at)
    themes[theme_id] = updated
    _save_themes(cache_dir, themes, filename)
    return updated


# --- Evidence items ----------------------------------------------------------


def _evidence_item_from_dict(data: dict) -> ThemeEvidenceItem:
    return ThemeEvidenceItem(
        id=data["id"],
        theme_id=data["theme_id"],
        date=data["date"],
        company=data["company"],
        source_name=data["source_name"],
        source_url=data["source_url"],
        fact=data["fact"],
        relevance=data["relevance"],
        direction=EvidenceDirection(data["direction"]),
    )


def load_theme_evidence_items(cache_dir: Path, filename: str = _EVIDENCE_FILENAME) -> dict[str, ThemeEvidenceItem]:
    path = cache_dir / filename
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, ThemeEvidenceItem] = {}
    for item_id, data in raw.items():
        try:
            result[item_id] = _evidence_item_from_dict(data)
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _save_theme_evidence_items(cache_dir: Path, items: dict[str, ThemeEvidenceItem], filename: str = _EVIDENCE_FILENAME) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {item_id: asdict(item) for item_id, item in items.items()}
    (cache_dir / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_theme_evidence_item(cache_dir: Path, item: ThemeEvidenceItem, filename: str = _EVIDENCE_FILENAME) -> bool:
    items = load_theme_evidence_items(cache_dir, filename)
    if item.id in items:
        return False
    items[item.id] = item
    _save_theme_evidence_items(cache_dir, items, filename)
    return True


def evidence_for_theme_ids(
    cache_dir: Path, theme_ids: Sequence[str], filename: str = _EVIDENCE_FILENAME,
) -> dict[str, tuple[ThemeEvidenceItem, ...]]:
    """Bulk, read-only: every evidence item for each of the supplied
    theme ids, in exactly one load_theme_evidence_items() call for a
    non-empty request (never one load per theme id). Empty input
    returns `{}` immediately without loading anything. Deterministically
    ordered by (theme_id, date, id)."""
    if not theme_ids:
        return {}
    items = load_theme_evidence_items(cache_dir, filename)
    by_theme: dict[str, list[ThemeEvidenceItem]] = {}
    theme_id_set = set(theme_ids)
    for item in items.values():
        if item.theme_id in theme_id_set:
            by_theme.setdefault(item.theme_id, []).append(item)
    return {
        theme_id: tuple(sorted(entries, key=lambda item: (item.date, item.id)))
        for theme_id, entries in by_theme.items()
    }


# --- Company map ---------------------------------------------------------


def _company_map_entry_from_dict(data: dict) -> ThemeCompanyMapEntry:
    return ThemeCompanyMapEntry(
        id=data["id"],
        theme_id=data["theme_id"],
        company_name=data["company_name"],
        role=CompanyRole(data["role"]),
        note=data.get("note"),
    )


def load_theme_company_map(cache_dir: Path, filename: str = _COMPANY_MAP_FILENAME) -> dict[str, ThemeCompanyMapEntry]:
    path = cache_dir / filename
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, ThemeCompanyMapEntry] = {}
    for entry_id, data in raw.items():
        try:
            result[entry_id] = _company_map_entry_from_dict(data)
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _save_theme_company_map(cache_dir: Path, entries: dict[str, ThemeCompanyMapEntry], filename: str = _COMPANY_MAP_FILENAME) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {entry_id: asdict(entry) for entry_id, entry in entries.items()}
    (cache_dir / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_theme_company_map_entry(cache_dir: Path, entry: ThemeCompanyMapEntry, filename: str = _COMPANY_MAP_FILENAME) -> bool:
    entries = load_theme_company_map(cache_dir, filename)
    if entry.id in entries:
        return False
    entries[entry.id] = entry
    _save_theme_company_map(cache_dir, entries, filename)
    return True


def company_map_for_theme_ids(
    cache_dir: Path, theme_ids: Sequence[str], filename: str = _COMPANY_MAP_FILENAME,
) -> dict[str, tuple[ThemeCompanyMapEntry, ...]]:
    """Bulk, read-only counterpart to evidence_for_theme_ids() — same
    one-load, empty-input-loads-nothing, and deterministic
    (theme_id, role, company_name, id) ordering."""
    if not theme_ids:
        return {}
    entries = load_theme_company_map(cache_dir, filename)
    by_theme: dict[str, list[ThemeCompanyMapEntry]] = {}
    theme_id_set = set(theme_ids)
    for entry in entries.values():
        if entry.theme_id in theme_id_set:
            by_theme.setdefault(entry.theme_id, []).append(entry)
    return {
        theme_id: tuple(sorted(items, key=lambda entry: (entry.role.value, entry.company_name, entry.id)))
        for theme_id, items in by_theme.items()
    }


# --- Research notes (hypotheses, decisions, watch items) ---------------------


def _research_note_from_dict(data: dict) -> ThemeResearchNote:
    return ThemeResearchNote(
        id=data["id"],
        theme_id=data["theme_id"],
        note_type=ThemeNoteType(data["note_type"]),
        content=data["content"],
        confidence=HypothesisConfidence(data["confidence"]) if data.get("confidence") is not None else None,
        disconfirming_condition=data.get("disconfirming_condition"),
        created_at=data["created_at"],
    )


def load_theme_research_notes(cache_dir: Path, filename: str = _RESEARCH_NOTES_FILENAME) -> dict[str, ThemeResearchNote]:
    path = cache_dir / filename
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, ThemeResearchNote] = {}
    for note_id, data in raw.items():
        try:
            result[note_id] = _research_note_from_dict(data)
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _save_theme_research_notes(cache_dir: Path, notes: dict[str, ThemeResearchNote], filename: str = _RESEARCH_NOTES_FILENAME) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {note_id: asdict(note) for note_id, note in notes.items()}
    (cache_dir / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_theme_research_note(cache_dir: Path, note: ThemeResearchNote, filename: str = _RESEARCH_NOTES_FILENAME) -> bool:
    """INSERT-only, exactly like evidence items and company-map
    entries — no update/replace path exists. A reassessed hypothesis is
    a new note, never a mutation of an old one."""
    notes = load_theme_research_notes(cache_dir, filename)
    if note.id in notes:
        return False
    notes[note.id] = note
    _save_theme_research_notes(cache_dir, notes, filename)
    return True


def research_notes_for_theme_ids(
    cache_dir: Path, theme_ids: Sequence[str], filename: str = _RESEARCH_NOTES_FILENAME,
) -> dict[str, tuple[ThemeResearchNote, ...]]:
    """Bulk, read-only counterpart to evidence_for_theme_ids() — same
    one-load, empty-input-loads-nothing, and deterministic
    (theme_id, created_at, id) ordering (chronological research log)."""
    if not theme_ids:
        return {}
    notes = load_theme_research_notes(cache_dir, filename)
    by_theme: dict[str, list[ThemeResearchNote]] = {}
    theme_id_set = set(theme_ids)
    for note in notes.values():
        if note.theme_id in theme_id_set:
            by_theme.setdefault(note.theme_id, []).append(note)
    return {
        theme_id: tuple(sorted(entries, key=lambda note: (note.created_at, note.id)))
        for theme_id, entries in by_theme.items()
    }
