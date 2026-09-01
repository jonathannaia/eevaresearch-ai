"""EevaResearch — Citrini-style Theme research workspace vertical slice
(design/DECISIONS.md). Tests for scripts/promote_match_to_evidence.py.
Every fixture is synthetic and locally constructed; no real network,
worker, scan, or LLM call anywhere in this file."""
from __future__ import annotations

import ast
from pathlib import Path

from scripts import promote_match_to_evidence as promo_mod
from src.config.settings import Settings
from src.data_access import backend_factory
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, StateTransition
from src.models.research_case import ResearchCase, ResearchCaseStatus
from src.models.theme_matching import MatchConfidence, MatchReviewStatus, ResearchCaseThemeMatch, ThemeMatchReviewDecision
from src.models.theme_research import EvidenceDirection, ResearchTheme, ThemeCategory, ThemeStatus, ThemeVisibility

REPO_ROOT = Path(__file__).parent.parent
_SCRIPT_PATH = REPO_ROOT / "scripts" / "promote_match_to_evidence.py"


def _theme(theme_id="theme-x", **overrides):
    defaults = dict(
        id=theme_id, category=ThemeCategory.BOTTLENECK, status=ThemeStatus.NEW, visibility=ThemeVisibility.INTERNAL,
        title="T", key_question="q", hypothesis="h", working_thesis="w", why_it_matters="y",
        what_could_change_the_view="c", what_to_watch_next="n",
        created_at="2026-09-01T00:00:00+00:00", updated_at="2026-09-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    return ResearchTheme(**defaults)


def _candidate(candidate_id="edgar-cand-1", **overrides):
    filing = FilingEvent(
        rcept_no="acc-1", corp_code="0000320193", corp_name="TSMC", stock_code="TSM",
        report_nm="8-K", rcept_dt="2026-08-15", flr_nm="TSMC", source_name="SEC EDGAR",
        source_url="https://example.com/filing", retrieved_at="2026-08-15T01:00:00+00:00",
        original_language="English", theme_slug="ai-buildout",
    )
    defaults = dict(
        id=candidate_id, filing=filing, matched_rules=["material_agreement:1.01"],
        confidence="High", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="TSMC capacity expansion.",
        state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="2026-08-15T00:00:00+00:00")],
    )
    defaults.update(overrides)
    return CandidateSignal(**defaults)


def _case(case_id="case-1", trigger_source_id="edgar-cand-1", **overrides):
    defaults = dict(
        id=case_id, trigger_source_type="radar", trigger_source_id=trigger_source_id, trigger_source_name="TSMC",
        trigger_summary="8-K", title="t", research_question="q", status=ResearchCaseStatus.OPEN,
        created_at="2026-08-15T00:00:00+00:00", version=1,
    )
    defaults.update(overrides)
    return ResearchCase(**defaults)


def _match(match_id="theme-match-abc", case_id="case-1", theme_id="theme-x", **overrides):
    defaults = dict(
        id=match_id, case_id=case_id, theme_id=theme_id, confidence=MatchConfidence.MEDIUM,
        direction=EvidenceDirection.CONTEXT, matched_sector_tag="ai-buildout",
        matched_rule_categories=("material_agreement",), matched_keywords=("capacity",),
        rationale="r", created_at="2026-08-15T00:00:00+00:00",
    )
    defaults.update(overrides)
    return ResearchCaseThemeMatch(**defaults)


def _decision(match_id="theme-match-abc", status=MatchReviewStatus.ACCEPTED, **overrides):
    defaults = dict(
        id="theme-match-review-1", match_id=match_id, decision=status,
        reviewer_note="Confirmed.", reviewed_at="2026-08-16T00:00:00+00:00",
    )
    defaults.update(overrides)
    return ThemeMatchReviewDecision(**defaults)


def _seed_full_scenario(settings, *, with_decision=True, decision_status=MatchReviewStatus.ACCEPTED):
    curator = backend_factory.get_theme_curator_repository(settings)
    curator.insert_theme(_theme())

    candidate_repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    candidate_repo.upsert_new_candidates([_candidate()])

    from src.data_access import research_store
    research_store.append_research_case(settings.cache_dir, _case())

    matching_repo = backend_factory.get_theme_matching_repository(settings)
    matching_repo.insert_match(_match())
    if with_decision:
        matching_repo.insert_review_decision(_decision(status=decision_status))


def _set_valid_content(monkeypatch, match_id="theme-match-abc", **overrides):
    defaults = dict(
        _MATCH_ID=match_id,
        _DIRECTION=EvidenceDirection.SUPPORTS,
        _FACT="TSMC disclosed a new capacity expansion agreement.",
        _RELEVANCE="Directly supports the binding-constraint hypothesis.",
    )
    defaults.update(overrides)
    for name, value in defaults.items():
        monkeypatch.setattr(promo_mod, name, value)


# ============================================================
# Disabled / dry-run / missing --confirm
# ============================================================


def test_authoring_disabled_by_default(tmp_path, monkeypatch, capsys):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    _seed_full_scenario(settings)
    _set_valid_content(monkeypatch)
    exit_code = promo_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "AUTHORING_ENABLED is False" in out
    curator = backend_factory.get_theme_curator_repository(settings)
    assert curator.get_theme("theme-x")


def test_dry_run_without_confirm_persists_nothing(tmp_path, monkeypatch):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    _seed_full_scenario(settings)
    _set_valid_content(monkeypatch)
    monkeypatch.setattr(promo_mod, "AUTHORING_ENABLED", True)

    exit_code = promo_mod.main(["--backend", "json", "--cache-dir", str(tmp_path)])
    assert exit_code == 0

    theme_repo = backend_factory.get_theme_repository(settings)
    assert theme_repo.evidence_for_theme("theme-x") == ()


def test_missing_confirm_leaves_backend_empty(tmp_path, monkeypatch):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    _seed_full_scenario(settings)
    _set_valid_content(monkeypatch)
    monkeypatch.setattr(promo_mod, "AUTHORING_ENABLED", True)
    promo_mod.main(["--backend", "json", "--cache-dir", str(tmp_path)])

    theme_repo = backend_factory.get_theme_repository(settings)
    assert theme_repo.evidence_for_theme("theme-x") == ()


# ============================================================
# Successful promotion
# ============================================================


def test_successful_promotion_creates_evidence(tmp_path, monkeypatch):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    _seed_full_scenario(settings)
    _set_valid_content(monkeypatch)
    monkeypatch.setattr(promo_mod, "AUTHORING_ENABLED", True)

    exit_code = promo_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    assert exit_code == 0

    theme_repo = backend_factory.get_theme_repository(settings)
    evidence = theme_repo.evidence_for_theme("theme-x")
    assert len(evidence) == 1
    assert evidence[0].direction is EvidenceDirection.SUPPORTS
    assert evidence[0].company == "TSMC"


# ============================================================
# Rejection paths
# ============================================================


def test_no_decision_at_all_rejected(tmp_path, monkeypatch):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    _seed_full_scenario(settings, with_decision=False)
    _set_valid_content(monkeypatch)
    monkeypatch.setattr(promo_mod, "AUTHORING_ENABLED", True)

    exit_code = promo_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    assert exit_code == 1
    theme_repo = backend_factory.get_theme_repository(settings)
    assert theme_repo.evidence_for_theme("theme-x") == ()


def test_pending_decision_rejected(tmp_path, monkeypatch):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    _seed_full_scenario(settings, decision_status=MatchReviewStatus.PENDING_REVIEW)
    _set_valid_content(monkeypatch)
    monkeypatch.setattr(promo_mod, "AUTHORING_ENABLED", True)

    exit_code = promo_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    assert exit_code == 1


def test_rejected_decision_rejected(tmp_path, monkeypatch):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    _seed_full_scenario(settings, decision_status=MatchReviewStatus.REJECTED)
    _set_valid_content(monkeypatch)
    monkeypatch.setattr(promo_mod, "AUTHORING_ENABLED", True)

    exit_code = promo_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    assert exit_code == 1


def test_missing_match_rejected(tmp_path, monkeypatch):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    _seed_full_scenario(settings)
    _set_valid_content(monkeypatch, match_id="theme-match-does-not-exist")
    monkeypatch.setattr(promo_mod, "AUTHORING_ENABLED", True)

    exit_code = promo_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    assert exit_code == 1


def test_placeholder_sentinel_rejected(tmp_path, monkeypatch):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    _seed_full_scenario(settings)
    _set_valid_content(monkeypatch, _FACT="REPLACE_ME")
    monkeypatch.setattr(promo_mod, "AUTHORING_ENABLED", True)

    exit_code = promo_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    assert exit_code == 1
    theme_repo = backend_factory.get_theme_repository(settings)
    assert theme_repo.evidence_for_theme("theme-x") == ()


def test_blank_fact_rejected(tmp_path, monkeypatch):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    _seed_full_scenario(settings)
    _set_valid_content(monkeypatch, _FACT="   ")
    monkeypatch.setattr(promo_mod, "AUTHORING_ENABLED", True)

    exit_code = promo_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    assert exit_code == 1


def test_duplicate_promotion_rejected(tmp_path, monkeypatch):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    _seed_full_scenario(settings)
    _set_valid_content(monkeypatch)
    monkeypatch.setattr(promo_mod, "AUTHORING_ENABLED", True)

    first = promo_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    second = promo_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])
    assert first == 0
    assert second == 1
    theme_repo = backend_factory.get_theme_repository(settings)
    assert len(theme_repo.evidence_for_theme("theme-x")) == 1


# ============================================================
# No side effects beyond the one evidence item
# ============================================================


def test_no_other_records_created(tmp_path, monkeypatch):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    _seed_full_scenario(settings)
    _set_valid_content(monkeypatch)
    monkeypatch.setattr(promo_mod, "AUTHORING_ENABLED", True)
    promo_mod.main(["--confirm", "--backend", "json", "--cache-dir", str(tmp_path)])

    theme_repo = backend_factory.get_theme_repository(settings)
    assert theme_repo.list_published_themes() == ()
    assert theme_repo.company_map_for_theme("theme-x") == ()
    curator = backend_factory.get_theme_curator_repository(settings)
    assert curator.research_notes_for_theme("theme-x") == ()
    assert curator.get_theme("theme-x").visibility is ThemeVisibility.INTERNAL


# ============================================================
# Forbidden imports / capabilities
# ============================================================


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_never_imports_worker_or_llm_modules():
    imported = _imported_module_names(_SCRIPT_PATH)
    forbidden_substrings = ("radar_worker", "openai", "anthropic", "requests", "httpx", "urllib")
    offenders = [m for m in imported if any(f in m.lower() for f in forbidden_substrings)]
    assert not offenders, offenders


def test_never_references_set_visibility_or_scope_types():
    tree = ast.parse(_SCRIPT_PATH.read_text(encoding="utf-8"), filename="promote_match_to_evidence.py")
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in ("ThemeMatchingScope", "ThemeCompanyMapEntry", "ThemeResearchNote"):
            offenders.append(node.id)
        if isinstance(node, ast.Attribute) and node.attr == "set_visibility":
            offenders.append("set_visibility")
    assert not offenders, offenders


def test_no_update_delete_or_batch_capability():
    exported = {name for name in dir(promo_mod) if not name.startswith("_")}
    forbidden_substrings = ("update", "delete", "replace", "upsert", "overwrite", "merge", "publish", "edit", "batch")
    offenders = [name for name in exported if any(f in name.lower() for f in forbidden_substrings)]
    assert not offenders, offenders
