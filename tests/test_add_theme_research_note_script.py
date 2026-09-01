"""EevaResearch — Citrini-style Theme research workspace vertical slice
(design/DECISIONS.md). Tests for scripts/add_theme_research_note.py.
Every fixture is synthetic and locally constructed; no real network,
worker, scan, or LLM call anywhere in this file."""
from __future__ import annotations

import ast
from pathlib import Path

from scripts import add_theme_research_note as note_mod
from src.config.settings import Settings
from src.data_access import backend_factory
from src.models.theme_research import (
    HypothesisConfidence,
    ResearchTheme,
    ThemeCategory,
    ThemeNoteType,
    ThemeStatus,
    ThemeVisibility,
)

REPO_ROOT = Path(__file__).parent.parent
_SCRIPT_PATH = REPO_ROOT / "scripts" / "add_theme_research_note.py"


def _theme(theme_id="theme-x", **overrides):
    defaults = dict(
        id=theme_id, category=ThemeCategory.BOTTLENECK, status=ThemeStatus.NEW, visibility=ThemeVisibility.INTERNAL,
        title="T", key_question="q", hypothesis="h", working_thesis="w", why_it_matters="y",
        what_could_change_the_view="c", what_to_watch_next="n",
        created_at="2026-09-01T00:00:00+00:00", updated_at="2026-09-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    return ResearchTheme(**defaults)


def _seed_theme(settings, **overrides):
    curator = backend_factory.get_theme_curator_repository(settings)
    curator.insert_theme(_theme(**overrides))


def _set_valid_hypothesis(monkeypatch, theme_id="theme-x", **overrides):
    defaults = dict(
        _NOTE_THEME_ID=theme_id,
        _NOTE_TYPE=ThemeNoteType.HYPOTHESIS,
        _NOTE_CONTENT="Packaging capacity is the binding constraint.",
        _NOTE_CONFIDENCE=HypothesisConfidence.MEDIUM,
        _NOTE_DISCONFIRMING_CONDITION="If packaging capacity outpaces demand for two quarters, reject.",
        _NOTE_CREATED_AT="2026-09-01T00:00:00Z",
    )
    defaults.update(overrides)
    for name, value in defaults.items():
        monkeypatch.setattr(note_mod, name, value)


# ============================================================
# Disabled / dry-run / missing --confirm
# ============================================================


def test_authoring_disabled_by_default_returns_none():
    assert note_mod.build_authored_note(False) is None


def test_dry_run_without_confirm_persists_nothing(tmp_path, monkeypatch):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    _seed_theme(settings)
    _set_valid_hypothesis(monkeypatch)
    monkeypatch.setattr(note_mod, "AUTHORING_ENABLED", True)

    def _forbidden(*_a, **_k):
        raise AssertionError("must not persist during a dry run")

    monkeypatch.setattr(note_mod, "persist_note", _forbidden)
    exit_code = note_mod.main(["--backend", "json", "--cache-dir", str(tmp_path)])
    assert exit_code == 0

    curator = backend_factory.get_theme_curator_repository(settings)
    assert curator.research_notes_for_theme("theme-x") == ()


def test_missing_confirm_leaves_backend_empty(tmp_path, monkeypatch):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    _seed_theme(settings)
    _set_valid_hypothesis(monkeypatch)
    monkeypatch.setattr(note_mod, "AUTHORING_ENABLED", True)
    note_mod.main(["--backend", "json", "--cache-dir", str(tmp_path)])

    curator = backend_factory.get_theme_curator_repository(settings)
    assert curator.research_notes_for_theme("theme-x") == ()


# ============================================================
# Successful creation — each note type
# ============================================================


def test_successful_hypothesis_creation(tmp_path, monkeypatch):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    _seed_theme(settings)
    _set_valid_hypothesis(monkeypatch)
    monkeypatch.setattr(note_mod, "AUTHORING_ENABLED", True)

    exit_code = note_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    assert exit_code == 0
    curator = backend_factory.get_theme_curator_repository(settings)
    notes = curator.research_notes_for_theme("theme-x")
    assert len(notes) == 1
    assert notes[0].note_type is ThemeNoteType.HYPOTHESIS
    assert notes[0].confidence is HypothesisConfidence.MEDIUM


def test_successful_decision_creation(tmp_path, monkeypatch):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    _seed_theme(settings)
    _set_valid_hypothesis(
        monkeypatch, _NOTE_TYPE=ThemeNoteType.DECISION, _NOTE_CONTENT="Escalate to weekly review.",
        _NOTE_CONFIDENCE=None, _NOTE_DISCONFIRMING_CONDITION=None,
    )
    monkeypatch.setattr(note_mod, "AUTHORING_ENABLED", True)

    exit_code = note_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    assert exit_code == 0
    curator = backend_factory.get_theme_curator_repository(settings)
    notes = curator.research_notes_for_theme("theme-x")
    assert notes[0].note_type is ThemeNoteType.DECISION
    assert notes[0].confidence is None


def test_successful_watch_item_creation(tmp_path, monkeypatch):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    _seed_theme(settings)
    _set_valid_hypothesis(
        monkeypatch, _NOTE_TYPE=ThemeNoteType.WATCH_ITEM, _NOTE_CONTENT="Watch Q3 CoWoS capacity commentary.",
        _NOTE_CONFIDENCE=None, _NOTE_DISCONFIRMING_CONDITION=None,
    )
    monkeypatch.setattr(note_mod, "AUTHORING_ENABLED", True)

    exit_code = note_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    assert exit_code == 0


# ============================================================
# Validation
# ============================================================


def test_hypothesis_without_confidence_rejected(monkeypatch):
    _set_valid_hypothesis(monkeypatch, _NOTE_CONFIDENCE=None)
    note = note_mod.build_authored_note(True)
    errors = note_mod.validate_note_content(note)
    assert any("confidence" in e for e in errors)


def test_hypothesis_without_disconfirming_condition_rejected(monkeypatch):
    _set_valid_hypothesis(monkeypatch, _NOTE_DISCONFIRMING_CONDITION=None)
    note = note_mod.build_authored_note(True)
    errors = note_mod.validate_note_content(note)
    assert any("disconfirming_condition" in e for e in errors)


def test_decision_with_confidence_rejected(monkeypatch):
    _set_valid_hypothesis(monkeypatch, _NOTE_TYPE=ThemeNoteType.DECISION, _NOTE_DISCONFIRMING_CONDITION=None)
    note = note_mod.build_authored_note(True)
    errors = note_mod.validate_note_content(note)
    assert any("confidence" in e for e in errors)


def test_placeholder_sentinel_rejected(tmp_path, monkeypatch):
    _set_valid_hypothesis(monkeypatch, _NOTE_CONTENT="REPLACE_ME")
    monkeypatch.setattr(note_mod, "AUTHORING_ENABLED", True)
    exit_code = note_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    assert exit_code == 1


def test_blank_content_rejected(monkeypatch):
    _set_valid_hypothesis(monkeypatch, _NOTE_CONTENT="   ")
    note = note_mod.build_authored_note(True)
    errors = note_mod.validate_note_content(note)
    assert any("content" in e for e in errors)


def test_oversized_content_rejected(monkeypatch):
    _set_valid_hypothesis(monkeypatch, _NOTE_CONTENT="x" * (note_mod._MAX_CONTENT_LENGTH + 1))
    note = note_mod.build_authored_note(True)
    errors = note_mod.validate_note_content(note)
    assert any("exceeds" in e for e in errors)


def test_duplicate_note_rejected(tmp_path, monkeypatch):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    _seed_theme(settings)
    _set_valid_hypothesis(monkeypatch)
    monkeypatch.setattr(note_mod, "AUTHORING_ENABLED", True)
    first = note_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    second = note_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    assert first == 0
    assert second == 1


# ============================================================
# Backend parity
# ============================================================


def test_sqlite_backend_creates_note(tmp_path, monkeypatch):
    settings = Settings(db_backend="sqlite", state_db_path=tmp_path / "state.db")
    _seed_theme(settings)
    _set_valid_hypothesis(monkeypatch)
    monkeypatch.setattr(note_mod, "AUTHORING_ENABLED", True)

    exit_code = note_mod.main(["--confirm", "--backend", "sqlite", "--sqlite-path", str(tmp_path / "state.db")])
    assert exit_code == 0
    curator = backend_factory.get_theme_curator_repository(settings)
    assert len(curator.research_notes_for_theme("theme-x")) == 1


# ============================================================
# No public visibility / forbidden imports
# ============================================================


def test_no_public_visibility_effect(tmp_path, monkeypatch):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    _seed_theme(settings)
    _set_valid_hypothesis(monkeypatch)
    monkeypatch.setattr(note_mod, "AUTHORING_ENABLED", True)
    note_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])

    theme_repo = backend_factory.get_theme_repository(settings)
    assert theme_repo.list_published_themes() == ()


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_never_imports_worker_scan_network_or_llm_modules():
    imported = _imported_module_names(_SCRIPT_PATH)
    forbidden_substrings = ("radar_worker", "edgar", "dart", "edinet", "openai", "anthropic", "requests", "httpx", "urllib")
    offenders = [m for m in imported if any(f in m.lower() for f in forbidden_substrings)]
    assert not offenders, offenders


def test_never_references_set_visibility_or_evidence_or_scope_types():
    tree = ast.parse(_SCRIPT_PATH.read_text(encoding="utf-8"), filename="add_theme_research_note.py")
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in ("ThemeEvidenceItem", "ThemeCompanyMapEntry", "ThemeMatchingScope", "ResearchCaseThemeMatch"):
            offenders.append(node.id)
        if isinstance(node, ast.Attribute) and node.attr == "set_visibility":
            offenders.append("set_visibility")
    assert not offenders, offenders


def test_no_update_delete_or_batch_capability():
    exported = {name for name in dir(note_mod) if not name.startswith("_")}
    forbidden_substrings = ("update", "delete", "replace", "upsert", "overwrite", "merge", "publish", "edit", "batch")
    offenders = [name for name in exported if any(f in name.lower() for f in forbidden_substrings)]
    assert not offenders, offenders
