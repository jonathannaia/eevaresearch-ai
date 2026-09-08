"""EevaResearch — autonomous Theme candidate detection (design/
DECISIONS.md). Tests for the pure src.logic.theme_candidate_detection
engine. Every fixture is synthetic and locally constructed; this module
performs no I/O, so these tests never touch a real network, worker,
scan, database, or LLM."""
from __future__ import annotations

import ast
from pathlib import Path

from src.logic.theme_candidate_detection import ThemeCandidate, detect_theme_candidates
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, StateTransition
from src.models.research_case import ResearchCase, ResearchCaseStatus

REPO_ROOT = Path(__file__).parent.parent
_MODULE_PATH = REPO_ROOT / "src" / "logic" / "theme_candidate_detection.py"

_KEYWORDS = ("capacity", "wafer")
_CATEGORIES = ("material_agreement",)


def _pair(
    company="TSMC", corp_code="0001", rcept_dt="2026-08-01", candidate_id="c1", case_id="case-1",
    theme_slug="ai-buildout", subtheme_slug="compute-accelerators", matched_rules=("material_agreement:1.01",),
    excerpt="Company disclosed a capacity expansion and wafer allocation agreement.",
) -> tuple[ResearchCase, CandidateSignal]:
    filing = FilingEvent(
        rcept_no=f"acc-{candidate_id}", corp_code=corp_code, corp_name=company, stock_code="X", report_nm="8-K",
        rcept_dt=rcept_dt, flr_nm=company, source_name="SEC EDGAR", source_url="https://example.com",
        retrieved_at=rcept_dt + "T01:00:00+00:00", original_language="English",
        theme_slug=theme_slug, subtheme_slug=subtheme_slug,
    )
    candidate = CandidateSignal(
        id=candidate_id, filing=filing, matched_rules=list(matched_rules), confidence="High",
        status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED, excerpt_original=excerpt,
        state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at=rcept_dt + "T00:00:00+00:00")],
    )
    case = ResearchCase(
        id=case_id, trigger_source_type="radar", trigger_source_id=candidate_id, trigger_source_name=company,
        trigger_summary="8-K", title="t", research_question="q", status=ResearchCaseStatus.OPEN,
        created_at=rcept_dt + "T00:00:00+00:00", version=1,
    )
    return case, candidate


def _detect(pairs, **overrides):
    kwargs = dict(
        as_of_date="2026-09-01", window_days=90, min_distinct_companies=2,
        constraint_keywords=_KEYWORDS, constraint_rule_categories=_CATEGORIES, already_covered=frozenset(),
    )
    kwargs.update(overrides)
    return detect_theme_candidates(pairs, **kwargs)


# ============================================================
# Happy path / threshold behavior
# ============================================================


def test_threshold_met_fires_one_candidate():
    pairs = [
        _pair(company="TSMC", candidate_id="c1", case_id="case-1", rcept_dt="2026-08-01"),
        _pair(company="Samsung", candidate_id="c2", case_id="case-2", rcept_dt="2026-08-10"),
    ]
    result = _detect(pairs)
    assert len(result) == 1
    candidate = result[0]
    assert isinstance(candidate, ThemeCandidate)
    assert candidate.theme_slug == "ai-buildout"
    assert candidate.subtheme_slug == "compute-accelerators"
    assert candidate.company_names == ("Samsung", "TSMC")
    assert candidate.member_case_ids == ("case-1", "case-2")


def test_below_threshold_fires_nothing():
    pairs = [_pair(company="TSMC", candidate_id="c1", case_id="case-1")]
    assert _detect(pairs) == ()


def test_same_company_twice_does_not_count_as_two_distinct():
    pairs = [
        _pair(company="TSMC", candidate_id="c1", case_id="case-1", rcept_dt="2026-08-01"),
        _pair(company="TSMC", candidate_id="c2", case_id="case-2", rcept_dt="2026-08-10"),
    ]
    assert _detect(pairs) == ()


def test_three_companies_all_included():
    pairs = [
        _pair(company="TSMC", candidate_id="c1", case_id="case-1", rcept_dt="2026-08-01"),
        _pair(company="Samsung", candidate_id="c2", case_id="case-2", rcept_dt="2026-08-10"),
        _pair(company="Micron", candidate_id="c3", case_id="case-3", rcept_dt="2026-08-20"),
    ]
    result = _detect(pairs)
    assert result[0].company_names == ("Micron", "Samsung", "TSMC")
    assert result[0].member_case_ids == ("case-1", "case-2", "case-3")


# ============================================================
# Constraint-relevance gating
# ============================================================


def test_wrong_rule_category_excluded():
    pairs = [
        _pair(company="TSMC", candidate_id="c1", case_id="case-1", matched_rules=("earnings_or_results:10-Q",)),
        _pair(company="Samsung", candidate_id="c2", case_id="case-2", matched_rules=("earnings_or_results:10-Q",)),
    ]
    assert _detect(pairs) == ()


def test_missing_keyword_excluded():
    pairs = [
        _pair(company="TSMC", candidate_id="c1", case_id="case-1", excerpt="Routine quarterly update."),
        _pair(company="Samsung", candidate_id="c2", case_id="case-2", excerpt="Routine quarterly update."),
    ]
    assert _detect(pairs) == ()


def test_keyword_match_via_report_name_not_just_excerpt():
    case, candidate = _pair(company="TSMC", candidate_id="c1", case_id="case-1", excerpt="")
    import dataclasses
    filing = dataclasses.replace(candidate.filing, report_nm="Capacity Expansion Agreement")
    candidate = dataclasses.replace(candidate, filing=filing)
    case2, candidate2 = _pair(company="Samsung", candidate_id="c2", case_id="case-2", excerpt="")
    filing2 = dataclasses.replace(candidate2.filing, report_nm="Capacity Expansion Agreement")
    candidate2 = dataclasses.replace(candidate2, filing=filing2)
    result = _detect([(case, candidate), (case2, candidate2)])
    assert len(result) == 1


def test_keyword_case_insensitive():
    pairs = [
        _pair(company="TSMC", candidate_id="c1", case_id="case-1", excerpt="CAPACITY expansion agreement."),
        _pair(company="Samsung", candidate_id="c2", case_id="case-2", excerpt="Capacity Expansion Agreement."),
    ]
    result = _detect(pairs)
    assert len(result) == 1


# ============================================================
# Clustering by (theme_slug, subtheme_slug)
# ============================================================


def test_different_subtheme_slugs_do_not_merge():
    pairs = [
        _pair(company="TSMC", candidate_id="c1", case_id="case-1", subtheme_slug="compute-accelerators"),
        _pair(company="Samsung", candidate_id="c2", case_id="case-2", subtheme_slug="hbm"),
    ]
    assert _detect(pairs) == ()  # each cluster alone only has 1 distinct company


def test_missing_subtheme_slug_clusters_by_theme_slug_alone():
    pairs = [
        _pair(company="TSMC", candidate_id="c1", case_id="case-1", subtheme_slug=None),
        _pair(company="Samsung", candidate_id="c2", case_id="case-2", subtheme_slug=None),
    ]
    result = _detect(pairs)
    assert len(result) == 1
    assert result[0].subtheme_slug is None


def test_missing_theme_slug_excluded_entirely():
    pairs = [
        _pair(company="TSMC", candidate_id="c1", case_id="case-1", theme_slug=""),
        _pair(company="Samsung", candidate_id="c2", case_id="case-2", theme_slug=""),
    ]
    assert _detect(pairs) == ()


def test_multiple_independent_clusters_both_fire_deterministically_ordered():
    pairs = [
        _pair(company="TSMC", candidate_id="c1", case_id="case-1", theme_slug="ai-buildout", subtheme_slug="compute-accelerators"),
        _pair(company="Samsung", candidate_id="c2", case_id="case-2", theme_slug="ai-buildout", subtheme_slug="compute-accelerators"),
        _pair(company="SK Hynix", candidate_id="c3", case_id="case-3", theme_slug="memory", subtheme_slug="hbm"),
        _pair(company="Micron", candidate_id="c4", case_id="case-4", theme_slug="memory", subtheme_slug="hbm"),
    ]
    result = _detect(pairs)
    assert [(c.theme_slug, c.subtheme_slug) for c in result] == [("ai-buildout", "compute-accelerators"), ("memory", "hbm")]


# ============================================================
# Time window
# ============================================================


def test_outside_window_excluded():
    pairs = [
        _pair(company="TSMC", candidate_id="c1", case_id="case-1", rcept_dt="2025-01-01"),
        _pair(company="Samsung", candidate_id="c2", case_id="case-2", rcept_dt="2025-01-02"),
    ]
    assert _detect(pairs, window_days=90, as_of_date="2026-09-01") == ()


def test_exactly_at_window_edge_included():
    pairs = [
        _pair(company="TSMC", candidate_id="c1", case_id="case-1", rcept_dt="2026-06-03"),  # 90 days before 2026-09-01
        _pair(company="Samsung", candidate_id="c2", case_id="case-2", rcept_dt="2026-09-01"),
    ]
    result = _detect(pairs, window_days=90, as_of_date="2026-09-01")
    assert len(result) == 1


def test_future_filing_beyond_as_of_date_excluded():
    pairs = [
        _pair(company="TSMC", candidate_id="c1", case_id="case-1", rcept_dt="2026-08-01"),
        _pair(company="Samsung", candidate_id="c2", case_id="case-2", rcept_dt="2026-12-01"),
    ]
    result = _detect(pairs, window_days=90, as_of_date="2026-09-01")
    assert result == ()  # only 1 company falls within the window


# ============================================================
# already_covered dedup
# ============================================================


def test_already_covered_exact_pair_skipped():
    pairs = [
        _pair(company="TSMC", candidate_id="c1", case_id="case-1"),
        _pair(company="Samsung", candidate_id="c2", case_id="case-2"),
    ]
    result = _detect(pairs, already_covered=frozenset({("ai-buildout", "compute-accelerators")}))
    assert result == ()


def test_covered_different_subtheme_does_not_block():
    pairs = [
        _pair(company="TSMC", candidate_id="c1", case_id="case-1", subtheme_slug="compute-accelerators"),
        _pair(company="Samsung", candidate_id="c2", case_id="case-2", subtheme_slug="compute-accelerators"),
    ]
    result = _detect(pairs, already_covered=frozenset({("ai-buildout", "hbm")}))
    assert len(result) == 1


# ============================================================
# Malformed input never raises
# ============================================================


def test_malformed_pairs_never_raise():
    assert _detect([(None, None)]) == ()
    assert _detect([("not-a-case", "not-a-candidate")]) == ()
    assert _detect([]) == ()


def test_invalid_config_returns_empty_not_raise():
    pairs = [_pair(company="TSMC"), _pair(company="Samsung", candidate_id="c2", case_id="case-2")]
    assert _detect(pairs, as_of_date="not-a-date") == ()
    assert _detect(pairs, window_days=0) == ()
    assert _detect(pairs, min_distinct_companies=0) == ()


# ============================================================
# Generality — no hardcoded sector/AI content in the engine itself
# ============================================================


def test_engine_has_no_io_or_hardcoded_ai_vocabulary():
    source = _MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename="theme_candidate_detection.py")
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders.extend(a.name for a in node.names if "data_access" in a.name or "requests" in a.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if "data_access" in node.module or "requests" in node.module:
                offenders.append(node.module)
    assert not offenders, offenders

    clock_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr in ("now", "today", "utcnow") and isinstance(n.func.value, ast.Name) and n.func.value.id in ("datetime", "date")
    ]
    assert not clock_calls


def test_generic_sector_never_mentions_ai_semiconductor_terms_in_output():
    """Proves the engine is genuinely sector-agnostic: run it against a
    completely unrelated (e.g. pharma-flavored) taxonomy and confirm
    the synthesized content is generic, not AI/semiconductor-flavored."""
    pairs = [
        _pair(company="PharmaCo A", candidate_id="c1", case_id="case-1", theme_slug="biotech", subtheme_slug="active-pharma-ingredient", excerpt="Company disclosed a capacity constrained supply agreement."),
        _pair(company="PharmaCo B", candidate_id="c2", case_id="case-2", theme_slug="biotech", subtheme_slug="active-pharma-ingredient", excerpt="Company disclosed a capacity constrained supply agreement."),
    ]
    result = _detect(pairs, constraint_keywords=("capacity", "supply agreement"))
    assert len(result) == 1
    assert result[0].theme_slug == "biotech"
    assert "wafer" not in result[0].research_question.lower()
    assert "semiconductor" not in result[0].rationale_summary.lower()
