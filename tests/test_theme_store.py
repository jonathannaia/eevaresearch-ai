"""EevaResearch — Evidence-First Themes MVP (design/DECISIONS.md).
JSON-backend tests for src.data_access.theme_store. Every fixture is
synthetic and locally constructed; no real evidence, network call, or
authoring-script invocation anywhere in this file."""
from __future__ import annotations

import dataclasses

from src.data_access import theme_store
from src.models.theme_research import (
    CompanyRole,
    EvidenceDirection,
    ResearchTheme,
    ThemeCategory,
    ThemeCompanyMapEntry,
    ThemeEvidenceItem,
    ThemeStatus,
    ThemeVisibility,
)


def _theme(title="Test theme", created_at="2026-08-20T00:00:00+00:00", **overrides):
    defaults = dict(
        id=theme_store.build_theme_id(title, created_at),
        category=ThemeCategory.BOTTLENECK, status=ThemeStatus.NEW, visibility=ThemeVisibility.INTERNAL,
        title=title, key_question="q", hypothesis="h", working_thesis="w", why_it_matters="y",
        what_could_change_the_view="c", what_to_watch_next="n", created_at=created_at, updated_at=created_at,
    )
    defaults.update(overrides)
    return ResearchTheme(**defaults)


def _evidence(theme_id="theme-x", source_url="https://example.com/a", date="2026-08-15", **overrides):
    defaults = dict(
        id=theme_store.build_theme_evidence_id(theme_id, source_url, date),
        theme_id=theme_id, date=date, company="Acme Corp", source_name="SEC EDGAR",
        source_url=source_url, fact="f", relevance="r", direction=EvidenceDirection.SUPPORTS,
    )
    defaults.update(overrides)
    return ThemeEvidenceItem(**defaults)


def _company_map_entry(theme_id="theme-x", company_name="Acme Corp", role=CompanyRole.EXPOSED, **overrides):
    defaults = dict(
        id=theme_store.build_theme_company_map_id(theme_id, company_name, role),
        theme_id=theme_id, company_name=company_name, role=role, note=None,
    )
    defaults.update(overrides)
    return ThemeCompanyMapEntry(**defaults)


# ============================================================
# Themes: round trip, duplicates, visibility filtering
# ============================================================


def test_append_and_get_theme_round_trip(tmp_path):
    theme = _theme()
    assert theme_store.append_theme(tmp_path, theme) is True
    assert theme_store.get_theme(tmp_path, theme.id) == theme


def test_duplicate_theme_id_never_overwrites(tmp_path):
    theme = _theme()
    tampered = dataclasses.replace(theme, title="TAMPERED")
    assert theme_store.append_theme(tmp_path, theme) is True
    assert theme_store.append_theme(tmp_path, tampered) is False
    assert theme_store.get_theme(tmp_path, theme.id) == theme


def test_internal_theme_is_not_returned_by_published_lookup(tmp_path):
    theme = _theme(visibility=ThemeVisibility.INTERNAL)
    theme_store.append_theme(tmp_path, theme)
    assert theme_store.get_published_theme(tmp_path, theme.id) is None
    assert theme_store.get_theme(tmp_path, theme.id) == theme


def test_ready_to_publish_and_archived_themes_are_not_returned_by_published_lookup(tmp_path):
    ready = _theme(title="Ready", visibility=ThemeVisibility.READY_TO_PUBLISH)
    archived = _theme(title="Archived", visibility=ThemeVisibility.ARCHIVED)
    theme_store.append_theme(tmp_path, ready)
    theme_store.append_theme(tmp_path, archived)
    assert theme_store.get_published_theme(tmp_path, ready.id) is None
    assert theme_store.get_published_theme(tmp_path, archived.id) is None
    assert theme_store.list_published_themes(tmp_path) == ()


def test_list_published_themes_excludes_non_published_and_orders_deterministically(tmp_path):
    published_old = _theme(title="Old", created_at="2026-08-01T00:00:00+00:00", visibility=ThemeVisibility.PUBLISHED, updated_at="2026-08-01T00:00:00+00:00")
    published_new = _theme(title="New", created_at="2026-08-05T00:00:00+00:00", visibility=ThemeVisibility.PUBLISHED, updated_at="2026-08-05T00:00:00+00:00")
    internal = _theme(title="Internal", created_at="2026-08-10T00:00:00+00:00", visibility=ThemeVisibility.INTERNAL)
    for theme in (published_old, published_new, internal):
        theme_store.append_theme(tmp_path, theme)

    result = theme_store.list_published_themes(tmp_path)
    assert [t.id for t in result] == [published_new.id, published_old.id]


def test_set_theme_visibility_transitions_and_updates_timestamp(tmp_path):
    theme = _theme()
    theme_store.append_theme(tmp_path, theme)
    updated = theme_store.set_theme_visibility(tmp_path, theme.id, ThemeVisibility.PUBLISHED, "2026-08-25T00:00:00+00:00")
    assert updated.visibility == ThemeVisibility.PUBLISHED
    assert updated.updated_at == "2026-08-25T00:00:00+00:00"
    assert theme_store.get_published_theme(tmp_path, theme.id) == updated


def test_set_theme_visibility_on_missing_theme_is_a_safe_no_op(tmp_path):
    assert theme_store.set_theme_visibility(tmp_path, "theme-missing", ThemeVisibility.PUBLISHED, "2026-08-25T00:00:00+00:00") is None
    assert theme_store.load_themes(tmp_path) == {}


def test_missing_files_load_empty(tmp_path):
    assert theme_store.load_themes(tmp_path) == {}
    assert theme_store.load_theme_evidence_items(tmp_path) == {}
    assert theme_store.load_theme_company_map(tmp_path) == {}
    assert theme_store.get_theme(tmp_path, "theme-does-not-exist") is None


# ============================================================
# Evidence items
# ============================================================


def test_evidence_round_trip_and_duplicate_rejection(tmp_path):
    item = _evidence()
    assert theme_store.append_theme_evidence_item(tmp_path, item) is True
    tampered = dataclasses.replace(item, fact="TAMPERED")
    assert theme_store.append_theme_evidence_item(tmp_path, tampered) is False
    assert theme_store.load_theme_evidence_items(tmp_path)[item.id] == item


def test_evidence_for_theme_ids_bulk_empty_input_never_loads(tmp_path, monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("must not load when theme_ids is empty")

    monkeypatch.setattr(theme_store, "load_theme_evidence_items", _boom)
    assert theme_store.evidence_for_theme_ids(tmp_path, []) == {}


def test_evidence_for_theme_ids_returns_correct_items_ordered_with_partial_matches(tmp_path):
    a1 = _evidence(theme_id="theme-a", source_url="https://example.com/1", date="2026-08-01")
    a2 = _evidence(theme_id="theme-a", source_url="https://example.com/2", date="2026-08-05")
    b1 = _evidence(theme_id="theme-b", source_url="https://example.com/3", date="2026-08-03")
    for item in (a1, a2, b1):
        theme_store.append_theme_evidence_item(tmp_path, item)

    result = theme_store.evidence_for_theme_ids(tmp_path, ["theme-a", "theme-b", "theme-missing"])
    assert set(result.keys()) == {"theme-a", "theme-b"}
    assert [i.id for i in result["theme-a"]] == [a1.id, a2.id]
    assert result["theme-b"] == (b1,)


# ============================================================
# Company map
# ============================================================


def test_company_map_round_trip_and_duplicate_rejection(tmp_path):
    entry = _company_map_entry()
    assert theme_store.append_theme_company_map_entry(tmp_path, entry) is True
    tampered = dataclasses.replace(entry, note="TAMPERED")
    assert theme_store.append_theme_company_map_entry(tmp_path, tampered) is False
    assert theme_store.load_theme_company_map(tmp_path)[entry.id] == entry


def test_company_map_for_theme_ids_bulk_empty_input_never_loads(tmp_path, monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("must not load when theme_ids is empty")

    monkeypatch.setattr(theme_store, "load_theme_company_map", _boom)
    assert theme_store.company_map_for_theme_ids(tmp_path, []) == {}


def test_company_map_for_theme_ids_returns_correct_items_with_partial_matches(tmp_path):
    a = _company_map_entry(theme_id="theme-a", company_name="Acme", role=CompanyRole.EXPOSED)
    b = _company_map_entry(theme_id="theme-b", company_name="Beta", role=CompanyRole.ENABLER)
    theme_store.append_theme_company_map_entry(tmp_path, a)
    theme_store.append_theme_company_map_entry(tmp_path, b)

    result = theme_store.company_map_for_theme_ids(tmp_path, ["theme-a", "theme-b", "theme-missing"])
    assert set(result.keys()) == {"theme-a", "theme-b"}
    assert result["theme-a"] == (a,)
    assert result["theme-b"] == (b,)


# ============================================================
# No update/upsert/replace/delete path exists (except the one documented exception)
# ============================================================


def test_no_update_upsert_replace_or_delete_functions_except_set_theme_visibility():
    exported = {name for name in dir(theme_store) if not name.startswith("_")}
    forbidden_substrings = ("update", "delete", "replace", "upsert", "overwrite", "merge")
    offenders = [
        name for name in exported
        if any(f in name.lower() for f in forbidden_substrings) and name != "set_theme_visibility"
    ]
    assert not offenders, offenders
