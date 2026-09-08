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


def _is_constraint_relevant(
    candidate: object, keywords: Sequence[str], rule_categories: Sequence[str],
) -> bool:
    if not (_candidate_categories(candidate) & set(rule_categories)):
        return False
    combined = _combined_text(candidate)
    return any(isinstance(k, str) and k.lower() in combined for k in keywords)


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
    ordered by (theme_slug, subtheme_slug or '')."""
    as_of = _parse_date(as_of_date)
    if as_of is None or window_days <= 0 or min_distinct_companies <= 0:
        return ()
    cutoff = as_of - timedelta(days=window_days)

    clusters: dict[tuple[str, str | None], list[tuple[ResearchCase, CandidateSignal]]] = {}
    for case, candidate in case_candidate_pairs:
        if not isinstance(case, ResearchCase) or not isinstance(candidate, CandidateSignal):
            continue
        if not _is_constraint_relevant(candidate, constraint_keywords, constraint_rule_categories):
            continue
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

    results: list[ThemeCandidate] = []
    for key in sorted(clusters.keys(), key=lambda k: (k[0], k[1] or "")):
        if key in already_covered:
            continue
        members = clusters[key]
        company_names = tuple(sorted({
            candidate.filing.corp_name for _case, candidate in members
            if isinstance(getattr(candidate.filing, "corp_name", None), str) and candidate.filing.corp_name
        }))
        if len(company_names) < min_distinct_companies:
            continue
        theme_slug, subtheme_slug = key
        results.append(_build_candidate(theme_slug, subtheme_slug, members, company_names, constraint_rule_categories, constraint_keywords))
    return tuple(results)
