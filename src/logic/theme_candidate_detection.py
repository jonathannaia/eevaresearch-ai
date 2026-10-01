"""EevaResearch — autonomous Theme candidate detection (design/
DECISIONS.md). A pure, deterministic engine that clusters already-
extracted, already-case-linked Radar CandidateSignals by
(FilingEvent.theme_slug, subtheme_slug) — the exact same sector/subtag
taxonomy every existing Theme already uses — and, when independent
official-source evidence for one cluster crosses a caller-configured
threshold within a caller-configured time window, synthesizes the
content for one INTERNAL candidate ResearchTheme: a research question,
a preliminary working thesis, why it may matter, what could change the
view, what to watch next, a hypothesis statement with its own
disconfirming condition, and a plain-English rationale explaining
exactly why the candidate was created.

Deliberately general, not AI-specific: the constraint-relevant keyword
vocabulary, the constraint-relevant matched-rule categories, the
minimum-distinct-companies threshold, and the lookback window are ALL
caller-supplied parameters. Nothing in this module hardcodes a sector,
an "AI" thesis, or any specific taxonomy value — the starting
semiconductor/AI-infrastructure vocabulary lives only in
scripts/radar_worker.py's own module-level constants, exactly like
every other tunable in that file (see e.g.
_THEME_MATCHING_BACKLOG_MAX_CASES).

This module performs no I/O of any kind: no file/JSON/SQLite/Postgres
access, no persistence call, no network/source fetch, no LLM/model
call, no UI call, no random value, no system-clock read — `as_of_date`
is always a caller-supplied value. It never creates, updates, or
publishes a Theme, a scope, a match, a company-map entry, or a research
note — it produces at most a tuple of plain ThemeCandidate data
records; the caller (scripts/radar_worker.py) decides whether and how
to persist them. It never infers a company's supply-chain role beyond
"contributed a triggering disclosure" — the same non-goal
src.logic.research_case_theme_matching.evaluate_theme_match already
established — and it never asserts SUPPORTS/CONTRADICTS/MIXED for any
piece of evidence; every candidate it proposes is, by construction,
still entirely unreviewed."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Sequence

from src.models.models import CandidateSignal
from src.models.research_case import ResearchCase

_ID_DIGEST_CHARS = 24


@dataclass(frozen=True)
class ThemeCandidate:
    """Pure data only — never persisted by this module. `member_case_ids`
    are the exact Research Case ids whose candidates contributed to this
    cluster; the caller uses these to build the bootstrap
    ResearchCaseThemeMatch rows against the newly-created scope."""

    theme_slug: str
    subtheme_slug: str | None
    company_names: tuple[str, ...]
    member_case_ids: tuple[str, ...]
    matched_rule_categories: tuple[str, ...]
    matched_keywords: tuple[str, ...]
    research_question: str
    hypothesis_statement: str
    working_thesis: str
    why_it_matters: str
    what_could_change_the_view: str
    what_to_watch_next: str
    disconfirming_condition: str
    rationale_summary: str


def _rule_category(matched_rule: object) -> str:
    if not isinstance(matched_rule, str):
        return ""
    return matched_rule.split(":", 1)[0].strip()


def _candidate_categories(candidate: object) -> set[str]:
    matched_rules = getattr(candidate, "matched_rules", None)
    if not isinstance(matched_rules, (list, tuple)):
        return set()
    return {c for c in (_rule_category(r) for r in matched_rules) if c}


def _combined_text(candidate: object) -> str:
    filing = getattr(candidate, "filing", None)
    excerpt = getattr(candidate, "excerpt_original", None)
    report_nm = getattr(filing, "report_nm", None)
    excerpt = excerpt if isinstance(excerpt, str) else ""
    report_nm = report_nm if isinstance(report_nm, str) else ""
    return f"{excerpt} {report_nm}".lower()


# The three outcomes of the relevance predicate, named so a caller can
# record WHICH gate rejected a pair without evaluating the predicate a
# second time. Fixed literals: no candidate value is ever carried here.
RELEVANCE_RELEVANT = "relevant"
RELEVANCE_CATEGORY_REJECTED = "category_rejected"
RELEVANCE_KEYWORD_REJECTED = "keyword_rejected"

# Fixed message: carries no candidate value, so it is safe even
# though the worker logs only the exception TYPE, never the text.
_UNRECOGNISED_RELEVANCE_OUTCOME = "unrecognised relevance outcome"


def _classify_constraint_relevance(
    candidate: object, keywords: Sequence[str], rule_categories: Sequence[str],
) -> str:
    """The relevance predicate, reporting which gate decided.

    Byte-for-byte the same two checks, in the same order, with the same
    short-circuit: an empty category intersection returns before
    `_combined_text` is built or any keyword is examined. That ordering
    is why the two rejections are not symmetric — a category rejection
    says nothing about whether a keyword would have matched — and why
    counting them separately is the only way to tell the two apart."""
    if not (_candidate_categories(candidate) & set(rule_categories)):
        return RELEVANCE_CATEGORY_REJECTED
    combined = _combined_text(candidate)
    if any(isinstance(k, str) and k.lower() in combined for k in keywords):
        return RELEVANCE_RELEVANT
    return RELEVANCE_KEYWORD_REJECTED


def _is_constraint_relevant(
    candidate: object, keywords: Sequence[str], rule_categories: Sequence[str],
) -> bool:
    """Unchanged predicate: same signature, same truth value, same
    short-circuit, same exceptions. It now delegates so that the
    detector can classify and decide from ONE evaluation rather than
    running the predicate twice."""
    return _classify_constraint_relevance(candidate, keywords, rule_categories) == RELEVANCE_RELEVANT


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip()[:10]).date()
    except ValueError:
        return None


def _matched_categories_for_members(
    members: Sequence[tuple[ResearchCase, CandidateSignal]], allowed: Sequence[str],
) -> tuple[str, ...]:
    allowed_set = set(allowed)
    found: set[str] = set()
    for _case, candidate in members:
        found |= _candidate_categories(candidate) & allowed_set
    return tuple(sorted(found))


def _matched_keywords_for_members(
    members: Sequence[tuple[ResearchCase, CandidateSignal]], keywords: Sequence[str],
) -> tuple[str, ...]:
    found: set[str] = set()
    for _case, candidate in members:
        combined = _combined_text(candidate)
        for keyword in keywords:
            if isinstance(keyword, str) and keyword.lower() in combined:
                found.add(keyword)
    return tuple(sorted(found))


def _build_candidate(
    theme_slug: str, subtheme_slug: str | None,
    members: Sequence[tuple[ResearchCase, CandidateSignal]],
    company_names: tuple[str, ...], rule_categories: Sequence[str], keywords: Sequence[str],
) -> ThemeCandidate:
    layer_label = subtheme_slug or theme_slug
    member_case_ids = tuple(sorted({case.id for case, _candidate in members}))
    matched_categories = _matched_categories_for_members(members, rule_categories)
    matched_keywords = _matched_keywords_for_members(members, keywords)
    companies_joined = ", ".join(company_names)

    research_question = f"Is {layer_label} becoming a binding constraint on {theme_slug}-related supply chains?"
    hypothesis_statement = (
        f"Multiple independent companies ({companies_joined}) have disclosed {layer_label}-related events "
        "consistent with an emerging supply-chain constraint."
    )
    working_thesis = (
        "Internal, auto-generated candidate. This Theme is collecting official-source context for human "
        "review and does not represent a published research conclusion."
    )
    why_it_matters = (
        f"If confirmed, a binding constraint in {layer_label} could affect pricing, availability, or delivery "
        f"timing across {theme_slug}-related companies."
    )
    disconfirming_condition = (
        f"If fewer than {len(company_names)} independent companies continue to disclose {layer_label}-related "
        "constraint signals over the next review window, or capacity/supply commentary reverses, reject this "
        "hypothesis."
    )
    what_to_watch_next = f"Further official disclosures from {companies_joined} and peers relevant to {layer_label}."
    rationale_summary = (
        f"Auto-created: {len(company_names)} independent companies ({companies_joined}) filed "
        f"{', '.join(matched_categories) or 'material'} disclosures matching constraint keywords "
        f"({', '.join(matched_keywords) or 'n/a'}) for constraint layer {layer_label!r} within the detection window."
    )

    return ThemeCandidate(
        theme_slug=theme_slug, subtheme_slug=subtheme_slug, company_names=company_names,
        member_case_ids=member_case_ids, matched_rule_categories=matched_categories, matched_keywords=matched_keywords,
        research_question=research_question, hypothesis_statement=hypothesis_statement, working_thesis=working_thesis,
        why_it_matters=why_it_matters, what_could_change_the_view=disconfirming_condition,
        what_to_watch_next=what_to_watch_next, disconfirming_condition=disconfirming_condition,
        rationale_summary=rationale_summary,
    )


@dataclass(frozen=True)
class DetectionDiagnostics:
    """Counts of decisions this module ALREADY makes, recorded as they
    are made — never a second evaluation pass, and never a judgment of
    its own. Integers only: no issuer, slug, excerpt, identifier or
    source text can travel through this record, so every field is safe
    to put in a log line.

    Observability only. Nothing here is read by detection, and removing
    it would not change a single returned candidate.

    `pairs_examined` is pairs the clustering loop actually iterated,
    which is 0 when the config guard short-circuits before the loop.
    A caller that knows how many pairs it supplied can therefore tell a
    config short-circuit (supplied > 0, examined == 0) apart from a
    relevance wipeout (examined > 0, constraint_relevant == 0).

    The pair stage separates its two rejections rather than conflating
    them, because they imply different repairs:

        pairs_examined - pairs_malformed - constraint_relevant
            == pairs that passed the type guard but failed relevance

    `pairs_malformed` counts pairs rejected by the existing
    ResearchCase/CandidateSignal type check, which runs BEFORE the
    relevance predicate — so a malformed pair never reaches it and is
    never counted as relevance-rejected.

    The relevance predicate's own two gates are counted separately,
    because they imply different repairs and the predicate
    short-circuits between them:

        pairs_examined - pairs_malformed
            == category_rejected + keyword_rejected + constraint_relevant

    `category_rejected` is pairs whose candidate categories do not
    intersect the allowlist; the keyword list is never consulted for
    them, so such a pair says nothing about whether a keyword would
    have matched. `keyword_rejected` is pairs that passed the category
    gate and matched no keyword. Collapsing the two would make a
    vocabulary problem indistinguishable from a language problem.

    The cluster stage reconciles exactly:

        clusters_formed
            == clusters_scope_suppressed
             + clusters_below_threshold
             + len(returned candidates)

    because the three outcomes are the three mutually exclusive branches
    of the second loop. `clusters_below_threshold` counts clusters
    rejected SOLELY for too few distinct companies — the
    `already_covered` check runs first, so a scope-suppressed cluster
    is never also counted as below threshold."""

    pairs_examined: int
    pairs_malformed: int
    category_rejected: int
    keyword_rejected: int
    constraint_relevant: int
    clusters_formed: int
    clusters_scope_suppressed: int
    clusters_below_threshold: int


def detect_theme_candidates(
    case_candidate_pairs: Sequence[tuple[ResearchCase, CandidateSignal]],
    *,
    as_of_date: str,
    window_days: int,
    min_distinct_companies: int,
    constraint_keywords: Sequence[str],
    constraint_rule_categories: Sequence[str],
    already_covered: frozenset[tuple[str, str | None]],
) -> tuple[ThemeCandidate, ...]:
    """Pure, deterministic, no I/O, no clock read. `already_covered` is
    the set of (theme_slug, subtheme_slug) pairs (subtheme_slug is None
    for a tag-only entry) an existing active scope already covers —
    those clusters are never re-proposed, so a firing cluster only ever
    produces one candidate for its lifetime. Never raises for malformed
    input; a candidate/filing missing required fields is simply
    excluded from clustering. Returns candidates deterministically
    ordered by (theme_slug, subtheme_slug or '').

    Unchanged entry point, kept so every existing caller and test sees
    exactly the same signature and return value. It delegates to
    `detect_theme_candidates_with_diagnostics` and discards the
    counts — there is one evaluation, not two."""
    candidates, _diagnostics = detect_theme_candidates_with_diagnostics(
        case_candidate_pairs,
        as_of_date=as_of_date,
        window_days=window_days,
        min_distinct_companies=min_distinct_companies,
        constraint_keywords=constraint_keywords,
        constraint_rule_categories=constraint_rule_categories,
        already_covered=already_covered,
    )
    return candidates


def detect_theme_candidates_with_diagnostics(
    case_candidate_pairs: Sequence[tuple[ResearchCase, CandidateSignal]],
    *,
    as_of_date: str,
    window_days: int,
    min_distinct_companies: int,
    constraint_keywords: Sequence[str],
    constraint_rule_categories: Sequence[str],
    already_covered: frozenset[tuple[str, str | None]],
) -> tuple[tuple[ThemeCandidate, ...], DetectionDiagnostics]:
    """`detect_theme_candidates` plus the funnel counts, from one pass.

    The returned candidates are byte-for-byte what the entry point above
    returns; every gate, threshold, suppression and ordering below is
    the original logic untouched. The only additions are integer
    increments recorded at decisions the loops already make, which is
    why this cannot re-evaluate inputs or disagree with itself."""
    as_of = _parse_date(as_of_date)
    if as_of is None or window_days <= 0 or min_distinct_companies <= 0:
        # Config guard: the loop never runs, so every count is 0. A
        # caller comparing this against the number of pairs it supplied
        # sees the short-circuit rather than mistaking it for relevance
        # rejecting everything.
        return (), DetectionDiagnostics(0, 0, 0, 0, 0, 0, 0, 0)
    cutoff = as_of - timedelta(days=window_days)

    pairs_examined = 0
    pairs_malformed = 0
    category_rejected = 0
    keyword_rejected = 0
    constraint_relevant = 0

    clusters: dict[tuple[str, str | None], list[tuple[ResearchCase, CandidateSignal]]] = {}
    for case, candidate in case_candidate_pairs:
        pairs_examined += 1
        if not isinstance(case, ResearchCase) or not isinstance(candidate, CandidateSignal):
            pairs_malformed += 1
            continue
        relevance = _classify_constraint_relevance(
            candidate, constraint_keywords, constraint_rule_categories,
        )
        if relevance != RELEVANCE_RELEVANT:
            if relevance == RELEVANCE_CATEGORY_REJECTED:
                category_rejected += 1
            elif relevance == RELEVANCE_KEYWORD_REJECTED:
                keyword_rejected += 1
            else:
                # Unreachable with this module's own classifier, whose
                # return set is the three constants above. Raising
                # rather than defaulting to one bucket is deliberate: a
                # fourth outcome counted as `keyword_rejected` would be
                # exactly the mis-attribution these counters exist to
                # prevent, and would break the pair-stage identity
                # silently. This is a programming error, not malformed
                # input -- input handling is unchanged.
                raise ValueError(_UNRECOGNISED_RELEVANCE_OUTCOME)
            continue
        constraint_relevant += 1
        filing = getattr(candidate, "filing", None)
        theme_slug = getattr(filing, "theme_slug", None)
        if not isinstance(theme_slug, str) or not theme_slug:
            continue
        subtheme_slug = getattr(filing, "subtheme_slug", None)
        subtheme_slug = subtheme_slug if isinstance(subtheme_slug, str) and subtheme_slug else None
        filed_on = _parse_date(getattr(filing, "rcept_dt", None))
        if filed_on is None or filed_on < cutoff or filed_on > as_of:
            continue
        key = (theme_slug, subtheme_slug)
        clusters.setdefault(key, []).append((case, candidate))

    clusters_scope_suppressed = 0
    clusters_below_threshold = 0

    results: list[ThemeCandidate] = []
    for key in sorted(clusters.keys(), key=lambda k: (k[0], k[1] or "")):
        if key in already_covered:
            clusters_scope_suppressed += 1
            continue
        members = clusters[key]
        company_names = tuple(sorted({
            candidate.filing.corp_name for _case, candidate in members
            if isinstance(getattr(candidate.filing, "corp_name", None), str) and candidate.filing.corp_name
        }))
        if len(company_names) < min_distinct_companies:
            clusters_below_threshold += 1
            continue
        theme_slug, subtheme_slug = key
        results.append(_build_candidate(theme_slug, subtheme_slug, members, company_names, constraint_rule_categories, constraint_keywords))
    return tuple(results), DetectionDiagnostics(
        pairs_examined=pairs_examined,
        pairs_malformed=pairs_malformed,
        category_rejected=category_rejected,
        keyword_rejected=keyword_rejected,
        constraint_relevant=constraint_relevant,
        clusters_formed=len(clusters),
        clusters_scope_suppressed=clusters_scope_suppressed,
        clusters_below_threshold=clusters_below_threshold,
    )
