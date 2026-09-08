"""Autonomous Theme candidate detection, Phase 2 (design/DECISIONS.md)
— worker-level integration tests for
`scripts/radar_worker.py::_run_theme_auto_publish_step()` and its
gating in `run_one_tick()`. The pure 7-gate logic itself is already
fully covered by tests/test_theme_auto_publish.py; this file proves the
worker's own wiring: disabled by default (the flag itself, not just an
ineligible theme, blocks every visibility change), publish + audit note
on an eligible theme, ineligible themes stay internal, idempotency
across ticks (an already-published theme is never re-evaluated), and
per-theme isolation (one bad theme never blocks the rest of the batch).
Real in-memory SQLite backend, no live scan, no worker process — same
conventions as the sibling Phase 2 integration test files."""
from __future__ import annotations

from datetime import datetime, timezone

from scripts import radar_worker
from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.theme_store import build_theme_research_note_id
from src.models.theme_research import (
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

_THEME_ID = "theme-auto-publish-1"


def _worker_settings(tmp_path, *, auto_publish_enabled) -> Settings:
    ambient = Settings(
        radar_live_scan_enabled=True, radar_worker_db_backend="sqlite",
        radar_worker_state_db_path=tmp_path / "state.db", radar_worker_state_db_url=None,
        edgar_auto_publish_enabled=False, theme_auto_publish_enabled=auto_publish_enabled,
    )
    return radar_worker._build_worker_settings(ambient)


def _theme(theme_id=_THEME_ID, **overrides) -> ResearchTheme:
    defaults = dict(
        id=theme_id,
        category=ThemeCategory.BOTTLENECK,
        status=ThemeStatus.NEW,
        visibility=ThemeVisibility.INTERNAL,
        title="HBM capacity constraint",
        key_question="Will HBM supply remain the binding constraint through 2026?",
        hypothesis="HBM packaging capacity is the binding constraint.",
        working_thesis="Working thesis text.",
        why_it_matters="Matters because of AI accelerator supply chains.",
        what_could_change_the_view="A capacity expansion coming online early.",
        what_to_watch_next="Watch capex guidance.",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    return ResearchTheme(**defaults)


def _evidence(theme_id, company, direction, item_id):
    return ThemeEvidenceItem(
        id=item_id, theme_id=theme_id, date="2026-01-01", company=company, source_name="SEC EDGAR",
        source_url="https://example.com/filing", fact="Some official fact.", relevance="Directly relevant.",
        direction=direction,
    )


def _hypothesis_note(theme_id, note_id="note-1"):
    return ThemeResearchNote(
        id=note_id, theme_id=theme_id, note_type=ThemeNoteType.HYPOTHESIS, content="Hypothesis content.",
        confidence=HypothesisConfidence.HIGH,
        disconfirming_condition="If capacity utilization drops below 70%, thesis is disconfirmed.",
        created_at="2026-01-01T00:00:00+00:00",
    )


def _seed_eligible_theme(curator, theme_id=_THEME_ID):
    """Builds a theme that passes all 7 auto-publish gates."""
    theme = _theme(theme_id)
    curator.insert_theme(theme)
    for i, company in enumerate(["Company A", "Company B", "Company C"]):
        curator.insert_evidence_item(_evidence(theme_id, company, EvidenceDirection.SUPPORTS, f"ev-{theme_id}-{i}"))
    curator.insert_research_note(_hypothesis_note(theme_id, f"note-{theme_id}"))
    return theme


def _seed_ineligible_theme(curator, theme_id):
    """Only 1 supporting company / 1 supporting evidence item — fails
    gates (a) and (b), and has no hypothesis note at all — fails (d)/(f)."""
    theme = _theme(theme_id, title="Ineligible theme", key_question="Is this eligible?")
    curator.insert_theme(theme)
    curator.insert_evidence_item(_evidence(theme_id, "Company A", EvidenceDirection.SUPPORTS, f"ev-{theme_id}-0"))
    return theme


# ============================================================
# Disabled by default
# ============================================================


def test_disabled_by_default_never_publishes_even_when_all_gates_pass(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path, auto_publish_enabled=False)
    assert worker_settings.theme_auto_publish_enabled is False
    curator = backend_factory.get_theme_curator_repository(worker_settings)
    _seed_eligible_theme(curator)

    def _forbidden(*_a, **_k):
        raise AssertionError("must never be called when theme_auto_publish_enabled is False")

    monkeypatch.setattr(radar_worker, "_run_theme_auto_publish_step", _forbidden)
    _set_all_providers_no_op(monkeypatch)
    scan_status_repo = backend_factory.get_scan_status_repository(worker_settings)

    radar_worker.run_one_tick(worker_settings, scan_status_repo)

    theme = curator.get_theme(_THEME_ID)
    assert theme.visibility is ThemeVisibility.INTERNAL


def test_flag_off_leaves_zero_visibility_changes_across_multiple_eligible_themes(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path, auto_publish_enabled=False)
    curator = backend_factory.get_theme_curator_repository(worker_settings)
    _seed_eligible_theme(curator, "theme-1")
    _seed_eligible_theme(curator, "theme-2")
    _set_all_providers_no_op(monkeypatch)
    scan_status_repo = backend_factory.get_scan_status_repository(worker_settings)

    radar_worker.run_one_tick(worker_settings, scan_status_repo)

    assert all(t.visibility is ThemeVisibility.INTERNAL for t in curator.list_themes())


# ============================================================
# Enabled — publish + audit note on eligible, stay internal on ineligible
# ============================================================


def test_enabled_publishes_eligible_theme_with_audit_note(tmp_path, monkeypatch, capsys):
    worker_settings = _worker_settings(tmp_path, auto_publish_enabled=True)
    curator = backend_factory.get_theme_curator_repository(worker_settings)
    _seed_eligible_theme(curator)
    _set_all_providers_no_op(monkeypatch)
    scan_status_repo = backend_factory.get_scan_status_repository(worker_settings)

    radar_worker.run_one_tick(worker_settings, scan_status_repo)

    theme = curator.get_theme(_THEME_ID)
    assert theme.visibility is ThemeVisibility.PUBLISHED

    notes = curator.research_notes_for_theme(_THEME_ID)
    decision_notes = [n for n in notes if n.note_type is ThemeNoteType.DECISION]
    assert len(decision_notes) == 1
    assert "ELIGIBLE" in decision_notes[0].content
    assert "PASS" in decision_notes[0].content

    out = capsys.readouterr().out
    assert "theme auto-publish" in out
    assert "themes_published=1" in out


def test_enabled_leaves_ineligible_theme_internal_with_no_audit_note(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path, auto_publish_enabled=True)
    curator = backend_factory.get_theme_curator_repository(worker_settings)
    _seed_ineligible_theme(curator, "theme-ineligible")
    _set_all_providers_no_op(monkeypatch)
    scan_status_repo = backend_factory.get_scan_status_repository(worker_settings)

    radar_worker.run_one_tick(worker_settings, scan_status_repo)

    theme = curator.get_theme("theme-ineligible")
    assert theme.visibility is ThemeVisibility.INTERNAL
    notes = curator.research_notes_for_theme("theme-ineligible")
    assert notes == ()


# ============================================================
# Idempotency — a published theme is never re-evaluated
# ============================================================


def test_published_theme_never_reevaluated_on_a_later_tick(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path, auto_publish_enabled=True)
    curator = backend_factory.get_theme_curator_repository(worker_settings)
    _seed_eligible_theme(curator)
    _set_all_providers_no_op(monkeypatch)
    scan_status_repo = backend_factory.get_scan_status_repository(worker_settings)

    radar_worker.run_one_tick(worker_settings, scan_status_repo)
    first_notes = curator.research_notes_for_theme(_THEME_ID)
    assert len([n for n in first_notes if n.note_type is ThemeNoteType.DECISION]) == 1

    radar_worker.run_one_tick(worker_settings, scan_status_repo)
    second_notes = curator.research_notes_for_theme(_THEME_ID)
    decision_notes = [n for n in second_notes if n.note_type is ThemeNoteType.DECISION]
    assert len(decision_notes) == 1  # no second audit note inserted — the theme was never re-evaluated

    theme = curator.get_theme(_THEME_ID)
    assert theme.visibility is ThemeVisibility.PUBLISHED


# ============================================================
# Per-theme isolation
# ============================================================


class _RaisingEvidenceCuratorProxy:
    """A plain, non-frozen proxy — the real curator adapters
    backend_factory returns are frozen dataclasses, so monkeypatching an
    attribute directly onto one raises FrozenInstanceError (same
    precedent as _TrackingScanStatusRepoProxy in
    tests/test_radar_worker_research_case_integration.py). Forwards
    every method unchanged except evidence_for_theme, which raises only
    for one designated theme id."""

    def __init__(self, real_curator, raise_for_theme_id):
        self._real = real_curator
        self._raise_for_theme_id = raise_for_theme_id

    def list_themes(self):
        return self._real.list_themes()

    def get_theme(self, theme_id):
        return self._real.get_theme(theme_id)

    def evidence_for_theme(self, theme_id):
        if theme_id == self._raise_for_theme_id:
            raise RuntimeError("synthetic evaluation failure")
        return self._real.evidence_for_theme(theme_id)

    def research_notes_for_theme(self, theme_id):
        return self._real.research_notes_for_theme(theme_id)

    def set_visibility(self, theme_id, new_visibility, updated_at):
        return self._real.set_visibility(theme_id, new_visibility, updated_at)

    def insert_research_note(self, note):
        return self._real.insert_research_note(note)


def test_one_bad_theme_does_not_block_the_rest_of_the_batch(tmp_path, monkeypatch, capsys):
    worker_settings = _worker_settings(tmp_path, auto_publish_enabled=True)
    real_curator = backend_factory.get_theme_curator_repository(worker_settings)
    _seed_eligible_theme(real_curator, "theme-good")
    _seed_eligible_theme(real_curator, "theme-bad")

    proxy = _RaisingEvidenceCuratorProxy(real_curator, raise_for_theme_id="theme-bad")
    monkeypatch.setattr(backend_factory, "get_theme_curator_repository", lambda settings: proxy)
    _set_all_providers_no_op(monkeypatch)
    scan_status_repo = backend_factory.get_scan_status_repository(worker_settings)

    radar_worker.run_one_tick(worker_settings, scan_status_repo)

    good_theme = real_curator.get_theme("theme-good")
    assert good_theme.visibility is ThemeVisibility.PUBLISHED
    bad_theme = real_curator.get_theme("theme-bad")
    assert bad_theme.visibility is ThemeVisibility.INTERNAL

    out = capsys.readouterr().out
    assert "themes_published=1" in out
    assert "evaluation_errors=1" in out


# ============================================================
# Whole-step exception isolation from other steps
# ============================================================


def test_auto_publish_step_exception_does_not_affect_matching_or_detection_output(tmp_path, monkeypatch, capsys):
    worker_settings = _worker_settings(tmp_path, auto_publish_enabled=True)

    def _raise(*_a, **_k):
        raise ValueError("a raw internal detail that must never be printed")

    monkeypatch.setattr(radar_worker, "_run_theme_auto_publish_step", _raise)
    _set_all_providers_no_op(monkeypatch)
    scan_status_repo = backend_factory.get_scan_status_repository(worker_settings)

    radar_worker.run_one_tick(worker_settings, scan_status_repo)
    out = capsys.readouterr().out
    assert "EDGAR: theme-auto-publish step skipped (ValueError)." in out
    assert "raw internal detail" not in out
    assert "EDGAR: theme matching" in out  # the sibling step's own summary still printed


def _set_all_providers_no_op(monkeypatch):
    import types
    for provider_key in ("edgar", "dart", "edinet"):
        monkeypatch.setitem(
            radar_worker._SERVICE_MODULES, provider_key,
            types.SimpleNamespace(run_scan=lambda settings, candidate_repository=None: types.SimpleNamespace(
                candidates_detected=0, candidates_processed=0, warnings=(), end_date="2026-08-20",
            )),
        )
