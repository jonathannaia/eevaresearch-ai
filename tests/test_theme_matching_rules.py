"""EevaResearch — Phase A0 (design/DECISIONS.md). Focused tests for the
pure, deterministic src.logic.research_case_theme_matching.evaluate_theme_match().
Every fixture is synthetic and directly constructed; this file never
touches persistence, a real store/repository, a real scan, the
network, the UI, an LLM, a source client, the authoring script, the
worker, or a real current date."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.logic.research_case_theme_matching import evaluate_theme_match
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent
from src.models.theme_matching import MatchConfidence, ResearchCaseThemeMatch, ThemeMatchingScope
from src.models.theme_research import EvidenceDirection

REPO_ROOT = Path(__file__).parent.parent
_CREATED_AT = "2026-08-20T00:00:00+00:00"

# ============================================================
# Semiconductor pilot scope fixture — "AI Infrastructure: Where Is the
# Binding Constraint?" (human-facing title/context only; the pure
# matcher never creates or stores a Theme).
# ============================================================

_PILOT_SCOPE = ThemeMatchingScope(
    theme_id="theme-ai-infra-binding-constraint",
    sector_tags=("ai-buildout", "memory"),
    sector_subtags=(
        "compute-accelerators", "dram", "hbm", "semiconductor-test",
        "power-cooling", "interconnect", "interconnect-switching", "optical-components",
    ),
    allowed_matched_rule_categories=(
        "capex_or_facility_investment", "material_agreement", "financing_or_debt",
        "supply_or_sales_contract", "other_material_event",
    ),
    required_keywords=(
        "capacity", "wafer", "fab", "foundry", "packaging", "hbm", "dram",
        "allocation", "lead time", "yield", "node", "supply agreement", "capacity expansion",
    ),
    # Narrowly scoped routine-event terms only — never broad enough to
    # reject ordinary semiconductor capex/supply/capacity/packaging/
    # memory/equipment/infrastructure disclosures.
    excluded_keywords=("stock buyback", "dividend increase", "executive compensation"),
)


def _filing(**overrides) -> FilingEvent:
    defaults = dict(
        rcept_no="acc-1", corp_code="0000320193", corp_name="Example Corp", stock_code="",
        report_nm="8-K", rcept_dt="2026-08-15", flr_nm="Example Corp", source_name="SEC EDGAR",
        theme_slug="ai-buildout", subtheme_slug="compute-accelerators",
    )
    defaults.update(overrides)
    return FilingEvent(**defaults)


def _candidate(**overrides) -> CandidateSignal:
    defaults = dict(
        id="edgar-cand-1", filing=_filing(), matched_rules=["capex_or_facility_investment:2.03"],
        confidence="High", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="The company announced a new wafer fab capacity expansion.",
    )
    defaults.update(overrides)
    return CandidateSignal(**defaults)


def _match(candidate=None, case_id="case-1", scope=None, created_at=_CREATED_AT):
    return evaluate_theme_match(candidate or _candidate(), case_id, scope or _PILOT_SCOPE, created_at)


# ============================================================
# Proof: match when sector + category + one keyword all pass
# ============================================================


def test_match_when_sector_category_and_keyword_all_pass():
    result = _match()
    assert isinstance(result, ResearchCaseThemeMatch)
    assert result.case_id == "case-1"
    assert result.theme_id == _PILOT_SCOPE.theme_id
    assert result.direction == EvidenceDirection.CONTEXT


# ============================================================
# Negative gates
# ============================================================


def test_no_match_when_sector_does_not_match():
    candidate = _candidate(filing=_filing(theme_slug="humanoids", subtheme_slug="industrial-automation"))
    assert _match(candidate) is None


def test_no_match_when_rule_category_does_not_intersect():
    candidate = _candidate(matched_rules=["governance_or_management_change:5.02"])
    assert _match(candidate) is None


def test_no_match_when_required_keyword_absent():
    candidate = _candidate(excerpt_original="The company reported quarterly results.", filing=_filing(report_nm="8-K"))
    assert _match(candidate) is None


def test_excluded_keyword_overrides_otherwise_valid_positive_gates():
    candidate = _candidate(excerpt_original="Wafer fab capacity expansion alongside an executive compensation adjustment.")
    assert _match(candidate) is None


# ============================================================
# Case-insensitivity
# ============================================================


def test_matching_is_case_insensitive_for_keywords_and_exclusions():
    candidate = _candidate(excerpt_original="WAFER FAB CAPACITY EXPANSION under way.")
    result = _match(candidate)
    assert result is not None

    excluded_candidate = _candidate(excerpt_original="Wafer fab capacity expansion and an EXECUTIVE COMPENSATION plan.")
    assert _match(excluded_candidate) is None


# ============================================================
# Safe handling of missing/None text fields
# ============================================================


def test_missing_excerpt_is_safe_and_report_name_can_still_satisfy_keyword():
    candidate = _candidate(excerpt_original=None, filing=_filing(report_nm="8-K announcing wafer fab expansion"))
    result = _match(candidate)
    assert result is not None
    assert "wafer" in result.matched_keywords or "fab" in result.matched_keywords


def test_missing_report_name_is_safe_and_excerpt_can_still_satisfy_keyword():
    candidate = _candidate(filing=_filing(report_nm=None), excerpt_original="New wafer fab capacity announced.")
    result = _match(candidate)
    assert result is not None


def test_both_excerpt_and_report_name_none_never_raises_and_yields_no_match():
    candidate = _candidate(excerpt_original=None, filing=_filing(report_nm=None))
    assert _match(candidate) is None


# ============================================================
# Sector priority: subtheme first, then theme
# ============================================================


def test_subtheme_match_takes_deterministic_priority_over_theme_match_when_both_apply():
    candidate = _candidate(filing=_filing(theme_slug="ai-buildout", subtheme_slug="hbm"))
    result = _match(candidate)
    assert result is not None
    assert result.matched_sector_tag == "hbm"


def test_theme_slug_used_when_only_theme_matches():
    candidate = _candidate(filing=_filing(theme_slug="memory", subtheme_slug="not-a-tracked-subtheme"))
    result = _match(candidate)
    assert result is not None
    assert result.matched_sector_tag == "memory"


def test_subtheme_slug_used_when_only_subtheme_matches():
    candidate = _candidate(filing=_filing(theme_slug="not-a-tracked-theme", subtheme_slug="dram"))
    result = _match(candidate)
    assert result is not None
    assert result.matched_sector_tag == "dram"


# ============================================================
# matched_rule_categories: only intersecting, deduplicated, ordered
# ============================================================


def test_only_intersecting_rule_categories_are_emitted():
    candidate = _candidate(matched_rules=["capex_or_facility_investment:2.03", "governance_or_management_change:5.02"])
    result = _match(candidate)
    assert result is not None
    assert result.matched_rule_categories == ("capex_or_facility_investment",)


def test_duplicate_rule_categories_are_deduplicated_deterministically():
    candidate = _candidate(matched_rules=[
        "capex_or_facility_investment:2.03", "capex_or_facility_investment:8-K", "material_agreement:1.01",
    ])
    result = _match(candidate)
    assert result is not None
    assert result.matched_rule_categories == ("capex_or_facility_investment", "material_agreement")


# ============================================================
# Confidence
# ============================================================


def test_one_distinct_keyword_creates_medium_confidence():
    candidate = _candidate(excerpt_original="A new wafer line was announced.", filing=_filing(report_nm="8-K"))
    result = _match(candidate)
    assert result is not None
    assert result.matched_keywords == ("wafer",)
    assert result.confidence == MatchConfidence.MEDIUM


def test_two_or_more_distinct_keywords_create_high_confidence():
    candidate = _candidate(excerpt_original="Wafer fab capacity expansion under way.")
    result = _match(candidate)
    assert result is not None
    assert len(result.matched_keywords) >= 2
    assert result.confidence == MatchConfidence.HIGH


def test_repeated_occurrences_of_the_same_keyword_do_not_increase_confidence():
    candidate = _candidate(excerpt_original="Wafer wafer wafer wafer capacity was not otherwise discussed.")
    result = _match(candidate)
    assert result is not None
    assert result.matched_keywords == ("capacity", "wafer")
    assert result.confidence == MatchConfidence.HIGH  # exactly two distinct keywords, not four


def test_matched_keywords_follow_scope_order_not_text_order():
    candidate = _candidate(excerpt_original="fab and wafer and capacity all mentioned")
    result = _match(candidate)
    assert result is not None
    # scope.required_keywords order is ("capacity", "wafer", "fab", ...)
    assert result.matched_keywords == ("capacity", "wafer", "fab")


# ============================================================
# Direction is always CONTEXT
# ============================================================


def test_direction_is_always_context():
    result = _match()
    assert result.direction == EvidenceDirection.CONTEXT
    assert result.direction != EvidenceDirection.SUPPORTS
    assert result.direction != EvidenceDirection.CONTRADICTS
    assert result.direction != EvidenceDirection.MIXED


# ============================================================
# Deterministic ID
# ============================================================


def test_id_is_deterministic_for_the_same_case_id_and_theme_id():
    a = _match(case_id="case-1")
    b = _match(case_id="case-1")
    assert a.id == b.id


def test_id_changes_when_case_id_changes():
    a = _match(case_id="case-1")
    b = _match(case_id="case-2")
    assert a.id != b.id


def test_id_changes_when_theme_id_changes():
    other_scope = ThemeMatchingScope(
        theme_id="theme-different", sector_tags=_PILOT_SCOPE.sector_tags, sector_subtags=_PILOT_SCOPE.sector_subtags,
        allowed_matched_rule_categories=_PILOT_SCOPE.allowed_matched_rule_categories,
        required_keywords=_PILOT_SCOPE.required_keywords, excluded_keywords=_PILOT_SCOPE.excluded_keywords,
    )
    a = _match(scope=_PILOT_SCOPE)
    b = _match(scope=other_scope)
    assert a.id != b.id


def test_id_is_independent_of_created_at_and_candidate_content():
    a = _match(created_at="2026-08-20T00:00:00+00:00")
    b = _match(created_at="2099-01-01T00:00:00+00:00")
    assert a.id == b.id


# ============================================================
# Rationale content
# ============================================================


def test_rationale_is_deterministic_and_repeatable():
    a = _match()
    b = _match()
    assert a.rationale == b.rationale


def test_rationale_contains_only_sector_categories_and_keywords_never_raw_excerpt_or_investment_language():
    secret_excerpt = "UNIQUE_EXCERPT_MARKER wafer fab capacity expansion UNIQUE_EXCERPT_MARKER"
    candidate = _candidate(excerpt_original=secret_excerpt)
    result = _match(candidate)
    assert result is not None
    assert "UNIQUE_EXCERPT_MARKER" not in result.rationale
    forbidden_terms = ("buy", "sell", "bullish", "bearish", "target price", "rating", "recommend", "bottleneck confirmed")
    rationale_lower = result.rationale.lower()
    for term in forbidden_terms:
        assert term not in rationale_lower
    assert result.matched_sector_tag in result.rationale
    for category in result.matched_rule_categories:
        assert category in result.rationale
    for keyword in result.matched_keywords:
        assert keyword in result.rationale


# ============================================================
# No mutation of inputs
# ============================================================


def test_candidate_and_scope_are_unchanged_after_evaluation():
    import dataclasses

    candidate = _candidate()
    filing_before = dataclasses.replace(candidate.filing)
    matched_rules_before = list(candidate.matched_rules)
    scope_before = dataclasses.replace(_PILOT_SCOPE)

    evaluate_theme_match(candidate, "case-1", _PILOT_SCOPE, _CREATED_AT)

    assert candidate.filing == filing_before
    assert candidate.matched_rules == matched_rules_before
    assert _PILOT_SCOPE == scope_before


# ============================================================
# Malformed input safety (no explicit gate spec, but must never raise)
# ============================================================


@pytest.mark.parametrize("bad_matched_rules", [None, "not-a-list", 123, [None, 123]])
def test_malformed_matched_rules_never_raises_and_yields_no_match(bad_matched_rules):
    import dataclasses

    candidate = dataclasses.replace(_candidate(), matched_rules=bad_matched_rules)
    assert _match(candidate) is None


def test_none_filing_never_raises_and_yields_no_match():
    import dataclasses

    candidate = dataclasses.replace(_candidate(), filing=None)
    assert _match(candidate) is None


# ============================================================
# Scope / dependency guards
# ============================================================


def test_module_has_no_persistence_worker_ui_network_file_llm_or_clock_dependency():
    source = (REPO_ROOT / "src" / "logic" / "research_case_theme_matching.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="research_case_theme_matching.py")

    allowed_stdlib = {"__future__", "hashlib"}
    allowed_project_modules = {"src.models.models", "src.models.theme_matching", "src.models.theme_research"}
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in allowed_stdlib and alias.name not in allowed_project_modules:
                    offenders.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
            if top not in allowed_stdlib and node.module not in allowed_project_modules:
                offenders.append(node.module)
    assert not offenders, offenders

    forbidden_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and (
            (n.func.attr in ("now", "today", "utcnow") and isinstance(n.func.value, ast.Name) and n.func.value.id in ("datetime", "date"))
            or (n.func.attr == "time" and isinstance(n.func.value, ast.Name) and n.func.value.id == "time")
            or (isinstance(n.func.value, ast.Name) and n.func.value.id in ("os", "random", "requests"))
        )
    ]
    assert not forbidden_calls, forbidden_calls


def test_models_module_has_no_persistence_ui_or_worker_dependency():
    source = (REPO_ROOT / "src" / "models" / "theme_matching.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="theme_matching.py")
    allowed_stdlib = {"__future__", "dataclasses", "enum"}
    allowed_project_modules = {"src.models.theme_research"}
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in allowed_stdlib and alias.name not in allowed_project_modules:
                    offenders.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
            if top not in allowed_stdlib and node.module not in allowed_project_modules:
                offenders.append(node.module)
    assert not offenders, offenders


def test_theme_matching_types_are_never_imported_by_public_ui_or_protocols():
    """Public UI pages must never import anything theme-matching-named
    at all. backend_factory.py is checked separately, at class/function
    granularity — since Phase A1 (design/DECISIONS.md) legitimately
    adds a private ThemeMatchingRepositoryProtocol seam to that same
    file, a blanket whole-file import check no longer distinguishes
    that private seam from the public ThemeRepositoryProtocol seam it
    must stay isolated from."""
    candidate_files = [
        "src/ui/pages/themes_research.py", "src/ui/pages/radar_inbox.py", "src/ui/pages/daily_news.py",
        "src/ui/pages/watchlists.py",
    ]
    offenders = []
    for rel_path in candidate_files:
        full_path = REPO_ROOT / rel_path
        if not full_path.exists():
            continue
        tree = ast.parse(full_path.read_text(encoding="utf-8"), filename=rel_path)
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                if "theme_matching" in module:
                    offenders.append(f"{rel_path}: imports {module!r}")
    assert not offenders, offenders


def test_backend_factory_public_theme_seam_never_references_theme_matching():
    """backend_factory.py's own module-level import of the
    theme-matching layer (for its private ThemeMatchingRepositoryProtocol
    seam) is expected and allowed. What must never happen is the
    *public* Theme seam — ThemeRepositoryProtocol and its three
    adapters — referencing anything theme-matching-named inside its own
    class bodies."""
    rel_path = "src/data_access/backend_factory.py"
    full_path = REPO_ROOT / rel_path
    tree = ast.parse(full_path.read_text(encoding="utf-8"), filename=rel_path)
    public_theme_classes = {
        "ThemeRepositoryProtocol", "JsonThemeRepository", "SqliteThemeRepository", "PostgresThemeRepository",
    }
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name in public_theme_classes:
            for inner in ast.walk(node):
                names_to_check = []
                if isinstance(inner, ast.Name):
                    names_to_check.append(inner.id)
                elif isinstance(inner, ast.Attribute):
                    names_to_check.append(inner.attr)
                for name in names_to_check:
                    if "theme_matching" in name.lower():
                        offenders.append(f"{node.name}: references {name!r}")
    assert not offenders, offenders


def test_no_ui_worker_persistence_or_migration_files_touched():
    """Phase A0 touched only src/models/theme_research.py (one additive
    enum member) among tracked files. Phase A1 (design/DECISIONS.md)
    deliberately extends this to the private theme-matching persistence
    layer: the V8 migration in both schema.py files, the
    ThemeMatchingRepositoryProtocol seam in backend_factory.py, and the
    intentional test updates those changes require. The brand-new
    theme-matching store/repository/test files themselves are untracked
    and never appear in `git diff HEAD` at all, by definition — only
    already-tracked files being modified show up here."""
    import subprocess

    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    allowed = {
        "src/models/theme_research.py",
        "src/data_access/state_db/schema.py",
        "src/data_access/postgres_state_db/schema.py",
        "src/data_access/backend_factory.py",
        "tests/test_theme_matching_rules.py",
        "tests/test_research_case_persistence.py",
        "tests/test_theme_repository_sqlite_postgres.py",
        # Phase A2 (design/DECISIONS.md) — the EDGAR-only, post-Research-
        # Case theme-matching worker hook.
        "scripts/radar_worker.py",
        # Citrini-style Theme research workspace vertical slice (design/
        # DECISIONS.md) — the hidden, internal-only theme_workspace UI
        # page and its supporting reads/settings flag.
        "app.py",
        "src/config/settings.py",
        "src/data_access/theme_store.py",
        "src/data_access/state_db/theme_repository.py",
        "src/data_access/postgres_state_db/theme_repository.py",
        "tests/test_navigation.py",
        # Autonomous Theme candidate detection (design/DECISIONS.md).
        "src/ui/pages/theme_workspace.py",
        "tests/test_theme_workspace_page.py",
        "tests/test_radar_worker_no_secrets_guard.py",
        # Phase 2 (design/DECISIONS.md) — cross-market (EDGAR/DART/
        # EDINET) research-case creation, cross-market theme clustering,
        # and the gated auto-publish policy.
        "tests/test_radar_worker_research_case_integration.py",
        "tests/test_radar_worker_safety_invariants.py",
        "tests/test_radar_worker_theme_candidate_detection_integration.py",
        "tests/test_radar_worker_theme_matching_integration.py",
    }
    assert changed <= allowed, changed - allowed


def _docstring_constant_node_ids(tree: ast.AST) -> set[int]:
    """id()s of every module/class/function docstring's own Constant
    node, so a source-name scan can exclude legitimate design-rationale
    prose while still checking real code (identifiers and non-
    docstring string literals)."""
    ids: set[int] = set()
    candidates: list[ast.AST] = [tree] + [
        n for n in ast.walk(tree) if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    for node in candidates:
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            ids.add(id(body[0].value))
    return ids


def test_provider_neutral_naming_no_edgar_dart_edinet_reference():
    """AST-based, not substring-based: checks that no function/class/
    variable *name* or non-docstring string *literal* names a specific
    source — module/function docstrings legitimately explain the design
    rationale in prose (e.g. why EDGAR's own stock_code is unreliable),
    which a naive whole-file substring scan would false-positive on."""
    for rel_path in ["src/models/theme_matching.py", "src/logic/research_case_theme_matching.py"]:
        source = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=rel_path)
        docstring_ids = _docstring_constant_node_ids(tree)
        offenders = []
        for node in ast.walk(tree):
            names_to_check = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names_to_check.append(node.name)
            elif isinstance(node, ast.Name):
                names_to_check.append(node.id)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstring_ids:
                names_to_check.append(node.value)
            for name in names_to_check:
                lowered = name.lower()
                for forbidden in ("edgar", "dart", "edinet"):
                    if forbidden in lowered:
                        offenders.append((rel_path, name))
        assert not offenders, offenders
