"""EevaResearch — autonomous Theme candidate detection (design/
DECISIONS.md). Tests for the pure src.logic.theme_candidate_detection
engine. Every fixture is synthetic and locally constructed; this module
performs no I/O, so these tests never touch a real network, worker,
scan, database, or LLM."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.logic.theme_candidate_detection import (
    RELEVANCE_CATEGORY_REJECTED,
    RELEVANCE_KEYWORD_REJECTED,
    RELEVANCE_RELEVANT,
    ThemeCandidate,
    _classify_constraint_relevance,
    _is_constraint_relevant,
    detect_theme_candidates,
    detect_theme_candidates_with_diagnostics,
)
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


def test_keyword_match_via_report_name_when_an_excerpt_also_exists():
    """The report name is still part of the evaluated text for a native-
    English filing -- but only alongside a real excerpt. This is the
    half of the former `test_keyword_match_via_report_name_not_just_
    excerpt` that survives text selection; the other half (title with NO
    excerpt) is now covered by the title-alone test below, which asserts
    the opposite outcome."""
    import dataclasses

    pairs = []
    for company, cid, kid in (("TSMC", "c1", "case-1"), ("Samsung", "c2", "case-2")):
        case, candidate = _pair(company=company, candidate_id=cid, case_id=kid,
                                excerpt="The registrant entered into an agreement.")
        filing = dataclasses.replace(candidate.filing, report_nm="Capacity Expansion Agreement")
        pairs.append((case, dataclasses.replace(candidate, filing=filing)))
    result = _detect(pairs)
    assert len(result) == 1


def test_a_native_english_title_alone_is_never_evaluable_evidence():
    """DELIBERATE NARROWING, decided and retained rather than excepted.
    Two EDGAR-shaped candidates with NO excerpt and a keyword-rich report
    name used to cluster and fire a candidate. A title is not evidence --
    `report_nm` is `primaryDocDescription or form` and is frequently the
    bare form name -- so admitting on it alone admits a form class on its
    form name. Both are now `text_unavailable` and nothing fires. No
    native-English exception exists, by decision."""
    import dataclasses

    pairs = []
    for company, cid, kid in (("TSMC", "c1", "case-1"), ("Samsung", "c2", "case-2")):
        case, candidate = _pair(company=company, candidate_id=cid, case_id=kid, excerpt="")
        filing = dataclasses.replace(candidate.filing, report_nm="Capacity Expansion Agreement")
        pairs.append((case, dataclasses.replace(candidate, filing=filing)))

    result, diag = _detect_with_diag(pairs)
    assert result == ()
    assert (diag.text_unavailable, diag.keyword_rejected, diag.constraint_relevant) == (2, 0, 0)


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


# ============================================================
# Funnel diagnostics (observability only)
#
# The worker's detection summary previously reported only
# clusters_detected/themes_created, so a zero could mean "no pairs
# reached the detector", "nothing passed the category/keyword
# predicate", or "clusters formed but fell short of the distinct-company
# threshold" — three different repairs. These counters separate those
# cases. They are recorded at decisions the existing loops already make;
# nothing here feeds a detection decision.
# ============================================================


def _detect_with_diag(pairs, **overrides):
    kwargs = dict(
        as_of_date="2026-09-01", window_days=90, min_distinct_companies=2,
        constraint_keywords=_KEYWORDS, constraint_rule_categories=_CATEGORIES, already_covered=frozenset(),
    )
    kwargs.update(overrides)
    return detect_theme_candidates_with_diagnostics(pairs, **kwargs)


def test_no_pairs_gathered_reports_an_all_zero_funnel():
    result, diag = _detect_with_diag([])

    assert result == ()
    assert (diag.pairs_examined, diag.pairs_malformed, diag.constraint_relevant) == (0, 0, 0)
    assert (diag.clusters_formed, diag.clusters_scope_suppressed, diag.clusters_below_threshold) == (0, 0, 0)


def test_pairs_rejected_by_relevance_are_examined_but_not_relevant():
    """The category/keyword predicate is the suspected production
    blocker for DART and EDINET, so it must be distinguishable from an
    empty input."""
    wrong_category = _pair(candidate_id="c1", case_id="case-1", matched_rules=("governance:5.02",))
    no_keyword = _pair(candidate_id="c2", case_id="case-2", excerpt="Routine administrative notice.")

    result, diag = _detect_with_diag([wrong_category, no_keyword])

    assert result == ()
    assert diag.pairs_examined == 2
    assert diag.pairs_malformed == 0              # <- neither was malformed...
    assert diag.constraint_relevant == 0          # <- ...so relevance is the discriminating fact
    assert diag.clusters_formed == 0
    assert diag.clusters_below_threshold == 0


def test_relevant_pairs_below_the_company_threshold_are_counted_as_such():
    """One company, two filings: relevant and clustered, rejected only
    for too few distinct companies."""
    pairs = [
        _pair(company="TSMC", candidate_id="c1", case_id="case-1", rcept_dt="2026-08-01"),
        _pair(company="TSMC", candidate_id="c2", case_id="case-2", rcept_dt="2026-08-10"),
    ]

    result, diag = _detect_with_diag(pairs)

    assert result == ()
    assert diag.pairs_examined == 2
    assert diag.constraint_relevant == 2
    assert diag.clusters_formed == 1
    assert diag.clusters_below_threshold == 1     # <- rejected solely on the threshold
    assert diag.clusters_scope_suppressed == 0


def test_a_cluster_that_passes_the_threshold_is_counted_in_neither_reject_bucket():
    pairs = [
        _pair(company="TSMC", candidate_id="c1", case_id="case-1"),
        _pair(company="Samsung", candidate_id="c2", case_id="case-2"),
    ]

    result, diag = _detect_with_diag(pairs)

    assert len(result) == 1
    assert diag.constraint_relevant == 2
    assert diag.clusters_formed == 1
    assert diag.clusters_below_threshold == 0
    assert diag.clusters_scope_suppressed == 0


def test_scope_suppressed_clusters_are_never_also_counted_below_threshold():
    """`already_covered` is checked first, so the two reject buckets are
    mutually exclusive. A suppressed single-company cluster must appear
    only as suppressed."""
    pairs = [_pair(company="TSMC", candidate_id="c1", case_id="case-1")]

    result, diag = _detect_with_diag(pairs, already_covered=frozenset({("ai-buildout", "compute-accelerators")}))

    assert result == ()
    assert diag.clusters_formed == 1
    assert diag.clusters_scope_suppressed == 1
    assert diag.clusters_below_threshold == 0


def test_cluster_stage_reconciles_exactly():
    """clusters_formed == suppressed + below_threshold + returned."""
    pairs = [
        # fires: two companies
        _pair(company="TSMC", candidate_id="c1", case_id="case-1", theme_slug="ai-buildout", subtheme_slug="a"),
        _pair(company="Samsung", candidate_id="c2", case_id="case-2", theme_slug="ai-buildout", subtheme_slug="a"),
        # below threshold: one company
        _pair(company="Intel", candidate_id="c3", case_id="case-3", theme_slug="ai-buildout", subtheme_slug="b"),
        # suppressed by scope
        _pair(company="SK", candidate_id="c4", case_id="case-4", theme_slug="memory", subtheme_slug="c"),
        _pair(company="Micron", candidate_id="c5", case_id="case-5", theme_slug="memory", subtheme_slug="c"),
    ]

    result, diag = _detect_with_diag(pairs, already_covered=frozenset({("memory", "c")}))

    assert diag.clusters_formed == 3
    assert diag.clusters_scope_suppressed == 1
    assert diag.clusters_below_threshold == 1
    assert len(result) == 1
    assert diag.clusters_formed == diag.clusters_scope_suppressed + diag.clusters_below_threshold + len(result)


def test_config_short_circuit_is_distinguishable_from_a_relevance_wipeout():
    """An invalid config returns before the loop, so pairs_examined is 0
    even though pairs were supplied — the caller compares it against the
    number it passed in."""
    pairs = [_pair(candidate_id="c1", case_id="case-1"), _pair(candidate_id="c2", case_id="case-2")]

    result, diag = _detect_with_diag(pairs, window_days=0)

    assert result == ()
    assert diag.pairs_examined == 0              # not 2 — the loop never ran
    assert diag.constraint_relevant == 0


def test_malformed_pairs_are_examined_but_never_counted_relevant():
    result, diag = _detect_with_diag([("not-a-case", "not-a-candidate")])

    assert result == ()
    assert diag.pairs_examined == 1
    assert diag.pairs_malformed == 1
    assert diag.constraint_relevant == 0


def test_diagnostics_carry_only_integers():
    """Safety: nothing in this record can leak an issuer, slug, excerpt
    or identifier into a log line."""
    import dataclasses

    pairs = [_pair(company="TSMC", candidate_id="c1", case_id="case-1"),
             _pair(company="Samsung", candidate_id="c2", case_id="case-2")]
    _result, diag = _detect_with_diag(pairs)

    values = dataclasses.asdict(diag)
    assert values and all(type(v) is int for v in values.values()), values
    assert all(v >= 0 for v in values.values())


def test_entry_point_returns_exactly_what_the_diagnostics_variant_returns():
    """The unchanged public function must stay byte-identical in
    behavior — one evaluation, same candidates, counts discarded."""
    scenarios = [
        [],
        [_pair(company="TSMC", candidate_id="c1", case_id="case-1")],
        [_pair(company="TSMC", candidate_id="c1", case_id="case-1"),
         _pair(company="Samsung", candidate_id="c2", case_id="case-2")],
        [_pair(candidate_id="c1", case_id="case-1", matched_rules=("governance:5.02",))],
        [("not-a-case", "not-a-candidate")],
    ]
    for pairs in scenarios:
        assert _detect(pairs) == _detect_with_diag(pairs)[0]
    # ...including under scope suppression and an invalid config.
    sup = frozenset({("ai-buildout", "compute-accelerators")})
    two = [_pair(company="TSMC", candidate_id="c1", case_id="case-1"),
           _pair(company="Samsung", candidate_id="c2", case_id="case-2")]
    assert _detect(two, already_covered=sup) == _detect_with_diag(two, already_covered=sup)[0]
    assert _detect(two, window_days=0) == _detect_with_diag(two, window_days=0)[0]


def test_a_realistic_malformed_pair_is_counted_as_malformed_not_as_relevance_rejected():
    """The shape a wiring bug would actually produce: a real, fully
    relevant candidate paired with something that is not a ResearchCase.
    The existing type guard rejects it before the relevance predicate
    runs, so it must land in pairs_malformed and must NOT be attributed
    to the category/keyword gate."""
    _case, candidate = _pair(company="TSMC", candidate_id="c1", case_id="case-1")
    broken_pair = (None, candidate)          # case missing entirely
    swapped_pair = (candidate, candidate)    # candidate where a case belongs

    result, diag = _detect_with_diag([broken_pair, swapped_pair])

    assert result == ()
    assert diag.pairs_examined == 2
    assert diag.pairs_malformed == 2
    assert diag.constraint_relevant == 0
    assert diag.clusters_formed == 0


def test_pair_stage_identity_across_malformed_rejected_and_relevant_pairs():
    """pairs_examined - pairs_malformed - constraint_relevant
    == pairs that passed the type guard but failed relevance.

    Built so all three buckets are non-zero and the expected value of
    each is known independently of the counters."""
    _c, candidate = _pair(company="TSMC", candidate_id="cx", case_id="case-x")
    pairs = [
        (None, candidate),                                                      # malformed
        ("not-a-case", "not-a-candidate"),                                      # malformed
        _pair(candidate_id="c1", case_id="case-1", matched_rules=("governance:5.02",)),   # wrong category
        _pair(candidate_id="c2", case_id="case-2", excerpt="Routine notice."),            # no keyword
        _pair(company="TSMC", candidate_id="c3", case_id="case-3"),             # relevant
        _pair(company="Samsung", candidate_id="c4", case_id="case-4"),          # relevant
    ]

    result, diag = _detect_with_diag(pairs)

    assert diag.pairs_examined == 6
    assert diag.pairs_malformed == 2
    assert diag.constraint_relevant == 2
    relevance_rejected = diag.pairs_examined - diag.pairs_malformed - diag.constraint_relevant
    assert relevance_rejected == 2          # exactly the wrong-category and no-keyword pairs
    # ...and the cluster stage still reconciles on the same run.
    assert diag.clusters_formed == diag.clusters_scope_suppressed + diag.clusters_below_threshold + len(result)


# ============================================================
# Category-versus-keyword rejection diagnostics
#
# `_is_constraint_relevant` short-circuits: an empty category
# intersection returns before the keyword list is consulted. So a
# category rejection says nothing about whether a keyword would have
# matched, and collapsing the two into one count makes a vocabulary
# problem indistinguishable from a language problem. Production
# reported constraint_relevant=0 over 21 pairs without being able to
# say which gate fired. These separate them.
# ============================================================


def test_classifier_reports_each_gate_and_the_predicate_agrees():
    relevant = _pair(company="TSMC", candidate_id="c1", case_id="case-1")[1]
    wrong_category = _pair(candidate_id="c2", case_id="case-2", matched_rules=("governance:5.02",))[1]
    no_keyword = _pair(candidate_id="c3", case_id="case-3", excerpt="Routine administrative notice.")[1]

    assert _classify_constraint_relevance(relevant, _KEYWORDS, _CATEGORIES) == RELEVANCE_RELEVANT
    assert _classify_constraint_relevance(wrong_category, _KEYWORDS, _CATEGORIES) == RELEVANCE_CATEGORY_REJECTED
    assert _classify_constraint_relevance(no_keyword, _KEYWORDS, _CATEGORIES) == RELEVANCE_KEYWORD_REJECTED

    # The public predicate's truth value is unchanged for all three.
    assert _is_constraint_relevant(relevant, _KEYWORDS, _CATEGORIES) is True
    assert _is_constraint_relevant(wrong_category, _KEYWORDS, _CATEGORIES) is False
    assert _is_constraint_relevant(no_keyword, _KEYWORDS, _CATEGORIES) is False


def test_category_rejection_never_consults_the_keyword_list():
    """Short-circuit preserved: a keyword list that would raise if
    iterated proves the category gate returns first."""
    class _Exploding:
        def __iter__(self):
            raise AssertionError("keyword list must not be consulted after a category rejection")

    wrong_category = _pair(candidate_id="c1", case_id="case-1", matched_rules=("governance:5.02",))[1]

    assert _classify_constraint_relevance(wrong_category, _Exploding(), _CATEGORIES) == RELEVANCE_CATEGORY_REJECTED
    assert _is_constraint_relevant(wrong_category, _Exploding(), _CATEGORIES) is False


def test_category_rejected_pairs_are_counted_as_such():
    pairs = [
        _pair(candidate_id="c1", case_id="case-1", matched_rules=("governance:5.02",)),
        _pair(candidate_id="c2", case_id="case-2", matched_rules=()),
    ]

    result, diag = _detect_with_diag(pairs)

    assert result == ()
    assert (diag.category_rejected, diag.keyword_rejected, diag.constraint_relevant) == (2, 0, 0)


def test_keyword_rejected_pairs_are_counted_as_such():
    pairs = [
        _pair(candidate_id="c1", case_id="case-1", excerpt="Routine administrative notice."),
        _pair(candidate_id="c2", case_id="case-2", excerpt="Board appointed a new auditor."),
    ]

    result, diag = _detect_with_diag(pairs)

    assert result == ()
    assert (diag.category_rejected, diag.keyword_rejected, diag.constraint_relevant) == (0, 2, 0)


def test_a_passing_pair_is_counted_in_neither_gate():
    pairs = [
        _pair(company="TSMC", candidate_id="c1", case_id="case-1"),
        _pair(company="Samsung", candidate_id="c2", case_id="case-2"),
    ]

    result, diag = _detect_with_diag(pairs)

    assert len(result) == 1
    assert (diag.category_rejected, diag.keyword_rejected, diag.constraint_relevant) == (0, 0, 2)


def test_empty_input_and_invalid_config_report_zero_for_both_gates():
    _r1, d1 = _detect_with_diag([])
    assert (d1.category_rejected, d1.keyword_rejected) == (0, 0)

    supplied = [_pair(candidate_id="c1", case_id="case-1"), _pair(candidate_id="c2", case_id="case-2")]
    _r2, d2 = _detect_with_diag(supplied, window_days=0)
    assert d2.pairs_examined == 0            # loop never ran
    assert (d2.category_rejected, d2.keyword_rejected, d2.constraint_relevant) == (0, 0, 0)


def test_malformed_pairs_reach_neither_gate():
    _c, candidate = _pair(candidate_id="cx", case_id="case-x")

    _result, diag = _detect_with_diag([(None, candidate), ("x", "y")])

    assert diag.pairs_malformed == 2
    assert (diag.category_rejected, diag.keyword_rejected, diag.constraint_relevant) == (0, 0, 0)


def test_pair_stage_identity_holds_across_mixed_buckets():
    """pairs_examined − pairs_malformed
    == category_rejected + keyword_rejected + constraint_relevant,
    with every bucket non-zero and each expected value known
    independently of the counters."""
    _c, candidate = _pair(candidate_id="cx", case_id="case-x")
    pairs = [
        (None, candidate),                                                                   # malformed
        _pair(candidate_id="c1", case_id="case-1", matched_rules=("governance:5.02",)),      # category
        _pair(candidate_id="c2", case_id="case-2", matched_rules=("ownership_change:3.01",)),# category
        _pair(candidate_id="c3", case_id="case-3", excerpt="Routine notice."),               # keyword
        _pair(company="TSMC", candidate_id="c4", case_id="case-4"),                          # relevant
        _pair(company="Samsung", candidate_id="c5", case_id="case-5"),                       # relevant
    ]

    result, diag = _detect_with_diag(pairs)

    assert diag.pairs_examined == 6
    assert diag.pairs_malformed == 1
    assert diag.category_rejected == 2
    assert diag.keyword_rejected == 1
    assert diag.constraint_relevant == 2
    assert diag.pairs_examined - diag.pairs_malformed == (
        diag.category_rejected + diag.keyword_rejected + diag.constraint_relevant
    )
    # ...and the cluster identity still holds on the same run.
    assert diag.clusters_formed == diag.clusters_scope_suppressed + diag.clusters_below_threshold + len(result)


def test_pair_stage_identity_holds_for_every_existing_scenario_family():
    sup = frozenset({("ai-buildout", "compute-accelerators")})
    _c, cand = _pair(candidate_id="cx", case_id="case-x")
    families = [
        ([], {}),
        ([(None, cand)], {}),
        ([_pair(candidate_id="a", case_id="A", matched_rules=("governance:5.02",))], {}),
        ([_pair(candidate_id="b", case_id="B", excerpt="Routine notice.")], {}),
        ([_pair(company="TSMC", candidate_id="c", case_id="C")], {}),
        ([_pair(company="TSMC", candidate_id="d", case_id="D"),
          _pair(company="Samsung", candidate_id="e", case_id="E")], {}),
        ([_pair(company="TSMC", candidate_id="f", case_id="F"),
          _pair(company="Samsung", candidate_id="g", case_id="G")], dict(already_covered=sup)),
        ([_pair(company="TSMC", candidate_id="h", case_id="H")], dict(window_days=0)),
    ]
    for pairs, ov in families:
        _result, diag = _detect_with_diag(pairs, **ov)
        assert diag.pairs_examined - diag.pairs_malformed == (
            diag.category_rejected + diag.keyword_rejected + diag.constraint_relevant
        ), (pairs, ov)


def test_detection_output_is_unchanged_across_the_existing_scenario_families():
    """Non-interference: the classifier replaced a bool call inside the
    loop, so the returned candidates must be identical to what the
    public entry point produces, family by family."""
    sup = frozenset({("ai-buildout", "compute-accelerators")})
    _c, cand = _pair(candidate_id="cx", case_id="case-x")
    families = [
        ([], {}),
        ([(None, cand)], {}),
        ([("not-a-case", "not-a-candidate")], {}),
        ([_pair(candidate_id="a", case_id="A", matched_rules=("governance:5.02",))], {}),
        ([_pair(candidate_id="b", case_id="B", excerpt="Routine notice.")], {}),
        ([_pair(company="TSMC", candidate_id="c", case_id="C")], {}),
        ([_pair(company="TSMC", candidate_id="d", case_id="D"),
          _pair(company="Samsung", candidate_id="e", case_id="E")], {}),
        ([_pair(company="TSMC", candidate_id="f", case_id="F"),
          _pair(company="Samsung", candidate_id="g", case_id="G")], dict(already_covered=sup)),
        ([_pair(company="TSMC", candidate_id="h", case_id="H")], dict(window_days=0)),
        ([_pair(company="TSMC", candidate_id="i", case_id="I", rcept_dt="2026-01-01"),
          _pair(company="Samsung", candidate_id="j", case_id="J")], {}),
    ]
    for pairs, ov in families:
        assert _detect(pairs, **ov) == _detect_with_diag(pairs, **ov)[0], (pairs, ov)


def test_the_predicate_is_evaluated_once_per_pair(monkeypatch):
    """Classification must not cost a second evaluation."""
    from src.logic import theme_candidate_detection as mod

    calls = []
    real = mod._classify_constraint_relevance
    monkeypatch.setattr(mod, "_classify_constraint_relevance",
                        lambda c, k, r: (calls.append(1), real(c, k, r))[1])
    pairs = [
        _pair(company="TSMC", candidate_id="c1", case_id="case-1"),
        _pair(candidate_id="c2", case_id="case-2", matched_rules=("governance:5.02",)),
        _pair(candidate_id="c3", case_id="case-3", excerpt="Routine notice."),
    ]

    _detect_with_diag(pairs)

    assert len(calls) == 3  # exactly one per type-valid pair


def test_gate_outcome_constants_are_fixed_safe_literals():
    for token in (RELEVANCE_RELEVANT, RELEVANCE_CATEGORY_REJECTED, RELEVANCE_KEYWORD_REJECTED):
        assert isinstance(token, str) and token and token.replace("_", "").isalnum() and token.islower()
    assert len({RELEVANCE_RELEVANT, RELEVANCE_CATEGORY_REJECTED, RELEVANCE_KEYWORD_REJECTED}) == 3


def test_an_unrecognised_classifier_outcome_raises_rather_than_being_mislabelled(monkeypatch):
    """Unreachable with this module's own classifier, whose return set
    is the three RELEVANCE_* constants. Pinned because defaulting a
    fourth outcome into `keyword_rejected` would be exactly the
    mis-attribution these counters exist to prevent, and would break
    the pair-stage identity silently.

    The decision for such a pair is unchanged either way -- anything
    that is not RELEVANCE_RELEVANT is rejected -- so this changes only
    how the rejection is recorded."""
    from src.logic import theme_candidate_detection as mod

    monkeypatch.setattr(mod, "_classify_constraint_relevance", lambda c, k, r: "some_future_outcome")
    pairs = [_pair(company="TSMC", candidate_id="c1", case_id="case-1")]

    with pytest.raises(ValueError):
        _detect_with_diag(pairs)
    # The public entry point surfaces it identically.
    with pytest.raises(ValueError):
        _detect(pairs)


def test_the_two_known_rejection_outcomes_are_matched_explicitly(monkeypatch):
    """Each known outcome lands in its own bucket by explicit match,
    not by falling through to an else."""
    from src.logic import theme_candidate_detection as mod

    for outcome, expected in (
        (mod.RELEVANCE_CATEGORY_REJECTED, "category"),
        (mod.RELEVANCE_KEYWORD_REJECTED, "keyword"),
    ):
        monkeypatch.setattr(mod, "_classify_constraint_relevance", lambda c, k, r, o=outcome: o)
        _result, diag = _detect_with_diag([_pair(company="TSMC", candidate_id="c1", case_id="case-1")])
        if expected == "category":
            assert (diag.category_rejected, diag.keyword_rejected) == (1, 0)
        else:
            assert (diag.category_rejected, diag.keyword_rejected) == (0, 1)
        assert diag.pairs_examined - diag.pairs_malformed == (
            diag.category_rejected + diag.keyword_rejected + diag.constraint_relevant
        )


# ============================================================
# DART facility-investment category admission (category policy)
# ============================================================

# The allowlist as it stands AFTER this change, and as it stood before,
# written as literals here so a pure-engine test never has to import the
# worker. scripts/radar_worker.py's own constant is pinned separately, in
# tests/test_radar_worker_theme_candidate_detection_integration.py.
_ALLOWLIST_BEFORE = ("material_agreement", "financing_or_debt", "other_material_event")
_ALLOWLIST_AFTER = _ALLOWLIST_BEFORE + ("capex_or_facility_investment",)

# A real-shaped DART facility-investment rule string: the category slug,
# the rule name, then the Korean keyword that fired, exactly the
# `f"{category}:{rule_name}:{kw}"` shape dart_rules.evaluate_report_name
# emits. Only the leading slug is read by the gate.
_DART_CAPEX_RULE = "capex_or_facility_investment:facility_investment:신규시설투자"
# Deliberately contains no term from the keyword list, so a pair built
# with it can only be rejected at the keyword gate, never at the
# category gate. "agreement" alone is not a keyword -- "supply
# agreement" is -- and no substring of any other keyword occurs here.
_NO_KEYWORD_EXCERPT = "The registrant entered into an agreement."


def test_the_dart_facility_investment_category_now_passes_the_category_gate():
    """The whole change, stated as one before/after: the same candidate
    that the old allowlist rejected at the CATEGORY gate is admitted past
    it by the new one. Nothing about the keyword gate moves."""
    _case, candidate = _pair(matched_rules=(_DART_CAPEX_RULE,))

    assert _classify_constraint_relevance(candidate, _KEYWORDS, _ALLOWLIST_BEFORE) == RELEVANCE_CATEGORY_REJECTED
    assert _classify_constraint_relevance(candidate, _KEYWORDS, _ALLOWLIST_AFTER) == RELEVANCE_RELEVANT


def test_an_admitted_facility_investment_pair_without_a_keyword_moves_to_the_keyword_gate():
    """Admission is necessary, never sufficient. A pair carrying the newly
    admitted category but no keyword must be reported as
    `keyword_rejected` -- NOT `category_rejected`. This is the assertion
    that proves the change moved the rejection to a later gate rather
    than silently doing nothing, and it is exactly the migration the
    production counters would show."""
    _case, candidate = _pair(matched_rules=(_DART_CAPEX_RULE,), excerpt=_NO_KEYWORD_EXCERPT)

    assert _classify_constraint_relevance(candidate, _KEYWORDS, _ALLOWLIST_BEFORE) == RELEVANCE_CATEGORY_REJECTED
    assert _classify_constraint_relevance(candidate, _KEYWORDS, _ALLOWLIST_AFTER) == RELEVANCE_KEYWORD_REJECTED


@pytest.mark.parametrize("rule", [
    # Periodic reporting -- fires on every results filing.
    "earnings:earnings_or_results_report:실적",
    # The DART module routes this through its own materiality gate
    # precisely because a bare treasury-share transaction is routine.
    "treasury_stock_activity:treasury_stock_disposal_or_acquisition:자기주식취득",
    # EDGAR's coarse fallback when SEC's `items` metadata is missing or
    # malformed: it carries no event meaning at all, so admitting it
    # would admit every unclassified 8-K.
    "material_event_8k_pending_items:8-K",
    # DART's capital-raise category -- near-synonymous with the admitted
    # `financing_or_debt`, and still deliberately NOT admitted here.
    "financing:capital_raise_or_treasury_stock:유상증자",
])
def test_categories_outside_the_allowlist_are_still_category_rejected(rule):
    """A negative pin: broadening the allowlist beyond the one category
    this change adds must fail a test, not pass silently. Each candidate
    carries a keyword-rich excerpt, so only the category gate can be
    rejecting it."""
    _case, candidate = _pair(matched_rules=(rule,))

    assert _classify_constraint_relevance(candidate, _KEYWORDS, _ALLOWLIST_AFTER) == RELEVANCE_CATEGORY_REJECTED
    assert _is_constraint_relevant(candidate, _KEYWORDS, _ALLOWLIST_AFTER) is False


def test_pair_stage_identity_holds_for_a_mixed_batch_including_the_new_category():
    """All four outcomes in one batch, every bucket non-zero, with the
    identity still exact."""
    pairs = [
        (None, None),                                                                      # malformed
        _pair(company="A", candidate_id="a", case_id="A",
              matched_rules=("earnings:earnings_or_results_report:실적",)),                 # category gate
        _pair(company="B", candidate_id="b", case_id="B",
              matched_rules=(_DART_CAPEX_RULE,), excerpt=_NO_KEYWORD_EXCERPT),             # keyword gate
        _pair(company="C", candidate_id="c", case_id="C",
              matched_rules=(_DART_CAPEX_RULE,)),                                          # relevant
    ]

    _result, diag = _detect_with_diag(pairs, constraint_rule_categories=_ALLOWLIST_AFTER)

    assert (diag.pairs_examined, diag.pairs_malformed) == (4, 1)
    assert (diag.category_rejected, diag.keyword_rejected, diag.constraint_relevant) == (1, 1, 1)
    assert diag.pairs_examined - diag.pairs_malformed == (
        diag.category_rejected + diag.keyword_rejected + diag.constraint_relevant
    )


# ============================================================
# Text selection: which text the keyword gate may read
# ============================================================

import dataclasses as _dc  # noqa: E402 — local to this section's fixtures

from src.logic.theme_candidate_detection import (  # noqa: E402
    RELEVANCE_TEXT_UNAVAILABLE,
    _combined_text,
    _evidence_text,
)
from src.models.models import ExcerptQuality, Translation, TranslationState  # noqa: E402

# A real-shaped Translation: translate_cached_with_outcome() mints every
# one with target_lang="en" and source_lang lowercased, and always
# records a provider. Note what a Translation does NOT carry: a document
# id. It is tied to a filing only by living in that candidate's own
# persisted translation column, written by the candidate-specific
# translation and retry paths.
def _translation(text, *, target_lang="en", source_lang="ko", provider="deepl"):
    return Translation(
        translated_text=text, provider=provider, source_lang=source_lang,
        target_lang=target_lang, translated_at="2026-08-01T00:00:00+00:00", model=None,
    )


_KOREAN_EXCERPT_WITH_ACRONYM = "회사는 HBM 생산 관련 신규시설투자를 결정하였다."
_TRANSLATED_WITH_KEYWORD = "The company decided on a new facility investment for HBM capacity."
_TRANSLATED_WITHOUT_KEYWORD = "The company entered into an agreement."


def _non_english_pair(
    *, company="SK Hynix", candidate_id="k1", case_id="case-k1", language="Korean",
    excerpt=_KOREAN_EXCERPT_WITH_ACRONYM, report_nm="신규시설투자등",
    translation_state=TranslationState.NOT_REQUESTED, excerpt_translation=None,
    title_translation=None, excerpt_quality=ExcerptQuality.USABLE_TEXT,
):
    """A DART/EDINET-shaped pair. Defaults deliberately reproduce the
    pre-translation state: original-language excerpt, no translation."""
    case, candidate = _pair(company=company, candidate_id=candidate_id, case_id=case_id, excerpt=excerpt)
    filing = _dc.replace(candidate.filing, original_language=language, report_nm=report_nm)
    candidate = _dc.replace(
        candidate, filing=filing, translation_state=translation_state,
        excerpt_translation=excerpt_translation, title_translation=title_translation,
        excerpt_quality=excerpt_quality,
    )
    return case, candidate


def _translated_pair(**overrides):
    base = dict(
        translation_state=TranslationState.TRANSLATED,
        excerpt_translation=_translation(_TRANSLATED_WITH_KEYWORD),
        excerpt_quality=ExcerptQuality.USABLE_TEXT,
    )
    base.update(overrides)
    return _non_english_pair(**base)


# --- EDGAR regression: the native-English path does not move ---

def test_native_english_evidence_text_is_byte_identical_to_the_old_combined_text():
    """The EDGAR regression pin, and it is scoped: for a native-English
    filing WITH a non-blank excerpt, the text the gate reads is exactly
    what this module built before text selection existed. A
    native-English filing with no excerpt is a separate, deliberately
    changed case -- see the title-alone test below."""
    _case, candidate = _pair()
    assert candidate.filing.original_language == "English"
    assert _evidence_text(candidate) == _combined_text(candidate)


def test_native_english_candidate_classifies_exactly_as_before():
    _case, relevant = _pair()
    _case2, no_keyword = _pair(excerpt="The registrant entered into an agreement.")
    assert _classify_constraint_relevance(relevant, _KEYWORDS, _CATEGORIES) == RELEVANCE_RELEVANT
    assert _classify_constraint_relevance(no_keyword, _KEYWORDS, _CATEGORIES) == RELEVANCE_KEYWORD_REJECTED


# --- E2: a verified translation is evaluable ---

def test_a_verified_translated_excerpt_is_evaluated():
    _case, candidate = _translated_pair()
    assert _evidence_text(candidate) == _TRANSLATED_WITH_KEYWORD.lower()
    assert _classify_constraint_relevance(candidate, _KEYWORDS, _CATEGORIES) == RELEVANCE_RELEVANT


def test_a_verified_translation_without_a_keyword_is_keyword_rejected_not_text_unavailable():
    """Proves the pair was genuinely evaluated rather than skipped."""
    _case, candidate = _translated_pair(excerpt_translation=_translation(_TRANSLATED_WITHOUT_KEYWORD))
    assert _classify_constraint_relevance(candidate, _KEYWORDS, _CATEGORIES) == RELEVANCE_KEYWORD_REJECTED


# --- The acronym path is closed ---

@pytest.mark.parametrize("excerpt", [
    "회사는 HBM 생산 관련 신규시설투자를 결정하였다.",          # hbm
    "당사는 DRAM 라인 증설을 결정하였습니다.",                   # dram
    "新しいfab投資に関する臨時報告書です。",                     # fab
    "次世代node向けの設備投資を決定しました。",                   # node
])
def test_a_latin_acronym_in_untranslated_text_is_never_evidence(excerpt):
    """The incidental-acronym negative test. Four of the fifteen keywords
    are Latin-script and DO occur verbatim in Korean and Japanese
    filings. Without a translation the gate cannot read the sentence
    around them, so such a pair must be `text_unavailable` -- never
    `relevant`, and never `keyword_rejected` either, since no evidence
    text was ever selected."""
    _case, candidate = _non_english_pair(excerpt=excerpt)
    assert _evidence_text(candidate) is None
    assert _classify_constraint_relevance(candidate, _KEYWORDS, _CATEGORIES) == RELEVANCE_TEXT_UNAVAILABLE
    assert _is_constraint_relevant(candidate, _KEYWORDS, _CATEGORIES) is False


def test_the_same_acronym_still_matches_inside_a_verified_translation():
    """The acronym is not banned -- only reading it out of context is."""
    _case, candidate = _translated_pair(
        excerpt=_KOREAN_EXCERPT_WITH_ACRONYM,
        excerpt_translation=_translation("HBM capacity is constrained at the new fab."),
    )
    assert _classify_constraint_relevance(candidate, _KEYWORDS, _CATEGORIES) == RELEVANCE_RELEVANT


# --- Every way a translation can fail to qualify ---

@pytest.mark.parametrize("state", [
    TranslationState.PENDING, TranslationState.UNAVAILABLE, TranslationState.NOT_REQUESTED,
])
def test_a_translation_not_in_the_translated_state_is_not_evidence(state):
    _case, candidate = _translated_pair(translation_state=state)
    assert _classify_constraint_relevance(candidate, _KEYWORDS, _CATEGORIES) == RELEVANCE_TEXT_UNAVAILABLE


def test_translated_state_with_no_excerpt_translation_is_not_evidence():
    """The verified hazard: when a candidate has no extracted excerpt the
    pipeline drives translation_state from the TITLE attempt, so a
    successful title translation leaves state TRANSLATED with
    excerpt_translation still None. State alone must never qualify."""
    _case, candidate = _translated_pair(
        excerpt_translation=None, title_translation=_translation("Capacity expansion report"),
    )
    assert _classify_constraint_relevance(candidate, _KEYWORDS, _CATEGORIES) == RELEVANCE_TEXT_UNAVAILABLE


@pytest.mark.parametrize("text", [None, "", "   ", 123, b"capacity"])
def test_a_blank_or_malformed_translated_text_is_not_evidence(text):
    _case, candidate = _translated_pair(excerpt_translation=_translation(text))
    assert _classify_constraint_relevance(candidate, _KEYWORDS, _CATEGORIES) == RELEVANCE_TEXT_UNAVAILABLE


@pytest.mark.parametrize("target_lang", [None, "", "ko", "ja", "fr", 7])
def test_a_translation_whose_target_language_is_not_english_is_not_evidence(target_lang):
    """Never assumed: the target language is read and verified."""
    _case, candidate = _translated_pair(
        excerpt_translation=_translation(_TRANSLATED_WITH_KEYWORD, target_lang=target_lang),
    )
    assert _classify_constraint_relevance(candidate, _KEYWORDS, _CATEGORIES) == RELEVANCE_TEXT_UNAVAILABLE


def test_english_target_language_is_matched_case_and_whitespace_insensitively():
    for target_lang in ("EN", " en ", "En"):
        _case, candidate = _translated_pair(
            excerpt_translation=_translation(_TRANSLATED_WITH_KEYWORD, target_lang=target_lang),
        )
        assert _classify_constraint_relevance(candidate, _KEYWORDS, _CATEGORIES) == RELEVANCE_RELEVANT


@pytest.mark.parametrize("field,value", [
    ("provider", ""), ("provider", "   "), ("provider", None), ("provider", 1),
    ("source_lang", ""), ("source_lang", None),
])
def test_a_translation_without_recorded_metadata_is_not_evidence(field, value):
    _case, candidate = _translated_pair(
        excerpt_translation=_translation(_TRANSLATED_WITH_KEYWORD, **{field: value}),
    )
    assert _classify_constraint_relevance(candidate, _KEYWORDS, _CATEGORIES) == RELEVANCE_TEXT_UNAVAILABLE


@pytest.mark.parametrize("quality", [
    ExcerptQuality.VERY_SHORT_OR_EMPTY, ExcerptQuality.LIKELY_BOILERPLATE,
    ExcerptQuality.TABLE_HEAVY, ExcerptQuality.UNKNOWN,
])
def test_a_translation_of_a_poor_or_unknown_quality_original_is_not_evidence(quality):
    """excerpt_quality describes the ORIGINAL the translation was made
    from. A weak original cannot become strong evidence by being
    translated."""
    _case, candidate = _translated_pair(excerpt_quality=quality)
    assert _classify_constraint_relevance(candidate, _KEYWORDS, _CATEGORIES) == RELEVANCE_TEXT_UNAVAILABLE


def test_a_non_english_title_and_title_translation_are_never_evidence():
    _case, candidate = _non_english_pair(
        excerpt="", report_nm="신규시설투자등 capacity",
        translation_state=TranslationState.TRANSLATED,
        title_translation=_translation("New facility investment -- capacity expansion"),
    )
    assert _evidence_text(candidate) is None
    assert _classify_constraint_relevance(candidate, _KEYWORDS, _CATEGORIES) == RELEVANCE_TEXT_UNAVAILABLE


def test_the_report_name_is_not_appended_to_a_translated_excerpt():
    """Appending the untranslated foreign title would reopen the acronym
    path through the back door."""
    _case, candidate = _translated_pair(
        report_nm="HBM 관련 신규시설투자등",
        excerpt_translation=_translation(_TRANSLATED_WITHOUT_KEYWORD),
    )
    assert _evidence_text(candidate) == _TRANSLATED_WITHOUT_KEYWORD.lower()
    assert _classify_constraint_relevance(candidate, _KEYWORDS, _CATEGORIES) == RELEVANCE_KEYWORD_REJECTED


def test_an_unrecognised_original_language_is_treated_as_non_english():
    """Fail-closed: anything this app does not write as "English" must
    require a translation rather than fall back to raw text."""
    for language in ("", "  ", "Englsh", "en"):
        _case, candidate = _non_english_pair(language=language, excerpt="capacity expansion wafer")
        assert _classify_constraint_relevance(candidate, _KEYWORDS, _CATEGORIES) == RELEVANCE_TEXT_UNAVAILABLE


def test_english_original_language_is_matched_case_and_whitespace_insensitively():
    for language in ("English", "english", " English "):
        _case, candidate = _non_english_pair(language=language, excerpt="capacity expansion wafer")
        assert _classify_constraint_relevance(candidate, _KEYWORDS, _CATEGORIES) == RELEVANCE_RELEVANT


# --- Ordering, identity, single evaluation ---

def test_the_category_gate_still_decides_before_text_selection():
    """A pair failing the category check is `category_rejected` even with
    a perfect translation, so that bucket's meaning is unchanged."""
    _case, candidate = _translated_pair()
    candidate = _dc.replace(candidate, matched_rules=["earnings:earnings_or_results_report:실적"])
    assert _classify_constraint_relevance(candidate, _KEYWORDS, _CATEGORIES) == RELEVANCE_CATEGORY_REJECTED


def test_four_outcome_pair_stage_identity_with_every_bucket_non_zero():
    pairs = [
        (None, None),                                                               # malformed
        _pair(company="A", candidate_id="a", case_id="A",
              matched_rules=("earnings:earnings_or_results_report:실적",)),          # category
        _non_english_pair(company="B", candidate_id="b", case_id="B"),              # text_unavailable
        _translated_pair(company="C", candidate_id="c", case_id="C",
                         excerpt_translation=_translation(_TRANSLATED_WITHOUT_KEYWORD)),  # keyword
        _translated_pair(company="D", candidate_id="d", case_id="D"),               # relevant
    ]
    _result, diag = _detect_with_diag(pairs)
    assert (diag.pairs_examined, diag.pairs_malformed) == (5, 1)
    assert (diag.category_rejected, diag.text_unavailable, diag.keyword_rejected, diag.constraint_relevant) == (1, 1, 1, 1)
    assert diag.pairs_examined - diag.pairs_malformed == (
        diag.category_rejected + diag.text_unavailable + diag.keyword_rejected + diag.constraint_relevant
    )


_DECISION_TABLE = [
    # (label, pair factory, expected bucket attribute)
    ("edgar usable excerpt, no translation", lambda: _pair(), "constraint_relevant"),
    ("edgar excerpt without a keyword",
     lambda: _pair(excerpt="The registrant entered into an agreement."), "keyword_rejected"),
    ("edgar no excerpt, title only", lambda: _pair(excerpt=""), "text_unavailable"),
    ("non-english usable original, no translation", lambda: _non_english_pair(), "text_unavailable"),
    ("non-english verified translation", lambda: _translated_pair(), "constraint_relevant"),
    ("non-english verified translation, no keyword",
     lambda: _translated_pair(excerpt_translation=_translation(_TRANSLATED_WITHOUT_KEYWORD)), "keyword_rejected"),
    ("translation pending", lambda: _translated_pair(translation_state=TranslationState.PENDING), "text_unavailable"),
    ("translation unavailable",
     lambda: _translated_pair(translation_state=TranslationState.UNAVAILABLE), "text_unavailable"),
    ("translation blank", lambda: _translated_pair(excerpt_translation=_translation("")), "text_unavailable"),
    ("translation malformed", lambda: _translated_pair(excerpt_translation=_translation(123)), "text_unavailable"),
    ("translation of a poor-quality original",
     lambda: _translated_pair(excerpt_quality=ExcerptQuality.TABLE_HEAVY), "text_unavailable"),
    ("non-english title only",
     lambda: _non_english_pair(excerpt="", title_translation=_translation("capacity")), "text_unavailable"),
    ("latin acronym, untranslated", lambda: _non_english_pair(), "text_unavailable"),
    ("category gate fails",
     lambda: _pair(matched_rules=("earnings:earnings_or_results_report:실적",)), "category_rejected"),
]


@pytest.mark.parametrize("label,factory,expected", _DECISION_TABLE, ids=[r[0] for r in _DECISION_TABLE])
def test_every_decision_table_row_lands_in_exactly_one_bucket(label, factory, expected):
    """One row, one pair, one bucket -- and the four-term identity holds
    for that row on its own, which is what makes the sum hold for any
    mix of rows."""
    _result, diag = _detect_with_diag([factory()])
    buckets = {
        "category_rejected": diag.category_rejected,
        "text_unavailable": diag.text_unavailable,
        "keyword_rejected": diag.keyword_rejected,
        "constraint_relevant": diag.constraint_relevant,
    }
    assert buckets[expected] == 1, (label, buckets)
    assert sum(buckets.values()) == 1, (label, buckets)
    assert diag.pairs_examined - diag.pairs_malformed == sum(buckets.values())


def test_the_predicate_is_still_evaluated_once_per_pair_with_text_selection(monkeypatch):
    from src.logic import theme_candidate_detection as mod

    calls = []
    real = mod._classify_constraint_relevance
    monkeypatch.setattr(
        mod, "_classify_constraint_relevance",
        lambda c, k, r: (calls.append(1), real(c, k, r))[1],
    )
    pairs = [
        _pair(company="A", candidate_id="a", case_id="A"),
        _non_english_pair(company="B", candidate_id="b", case_id="B"),
        _translated_pair(company="C", candidate_id="c", case_id="C"),
        (None, None),
    ]
    _result, diag = _detect_with_diag(pairs)
    assert len(calls) == diag.pairs_examined - diag.pairs_malformed == 3


def test_matched_keywords_come_from_the_text_the_gate_actually_read():
    """A candidate admitted on its translated excerpt must not record
    keywords drawn from text it never matched on."""
    pairs = [
        _translated_pair(company="A", candidate_id="a", case_id="A",
                         excerpt_translation=_translation("HBM capacity is constrained.")),
        _translated_pair(company="B", candidate_id="b", case_id="B",
                         excerpt_translation=_translation("HBM capacity is constrained.")),
    ]
    result = _detect(pairs)
    assert len(result) == 1
    assert set(result[0].matched_keywords) <= {"hbm", "capacity"}
    assert "wafer" not in result[0].matched_keywords
