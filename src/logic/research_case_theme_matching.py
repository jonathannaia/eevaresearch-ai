"""EevaResearch — Phase A0 (design/DECISIONS.md). A pure, deterministic
function deciding whether an already-persisted Radar `CandidateSignal`
(paired with the internal Research Case id it produced) is eligible to
become a `ResearchCaseThemeMatch` candidate for one internal Theme's
matching scope.

Deliberately named and placed distinctly from the pre-existing, wholly
unrelated `src/logic/theme_matching.py` (Theme Registry Foundation —
free-text alias/event-pattern classification against a curated
`ThemeRegistry`, see design/THEME_REGISTRY_FOUNDATION.md). That module
answers "which registry themes does this text mention"; this one
answers a narrower, different question — "does this specific Research
Case's originating candidate satisfy one Theme's explicit matching
scope" — and shares no types, no registry, and no code with it. Naming
this module `research_case_theme_matching.py` rather than reusing
`theme_matching.py` is a deliberate collision-avoidance choice, not an
oversight.

This module performs no I/O of any kind: no file/JSON/SQLite/Postgres
access, no persistence call, no network/source fetch, no LLM/model
call, no UI call, no random value, no subprocess, no environment-
variable read, and no system-clock read — `created_at` is always a
caller-supplied value, never generated here. It never mutates its
`candidate`/`scope` inputs, never infers a company's supply-chain role,
never assigns SUPPORTS/CONTRADICTS/MIXED, and never creates, updates,
or publishes a Theme — it produces at most one plain data record, or
`None`.

Deliberately provider-neutral: this module and `evaluate_theme_match()`
never name EDGAR/DART/EDINET specifically, and never require ticker
data (EDGAR's own `FilingEvent.stock_code` is not reliably populated —
see the Theme-matching architecture audit, design/DECISIONS.md). Every
field this function reads already exists on `CandidateSignal`/
`FilingEvent` for all three sources today; nothing here assumes an
EDGAR-only shape."""
from __future__ import annotations

import hashlib

from src.models.models import CandidateSignal
from src.models.theme_matching import MatchConfidence, ResearchCaseThemeMatch, ThemeMatchingScope
from src.models.theme_research import EvidenceDirection

_ID_DIGEST_CHARS = 24


def _build_match_id(case_id: str, theme_id: str) -> str:
    """Byte-for-byte matches this codebase's established sha256-
    truncated-hex ID convention (see e.g. research_store.build_case_id,
    theme_store.build_theme_id) — deterministic solely from
    (case_id, theme_id), never candidate content, so re-evaluating the
    same case against the same theme always derives the same id."""
    digest = hashlib.sha256(f"{case_id}|{theme_id}".encode("utf-8")).hexdigest()
    return f"theme-match-{digest[:_ID_DIGEST_CHARS]}"


def _as_text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _rule_categories(matched_rules: object) -> tuple[str, ...]:
    """Every derived category from a matched_rules-shaped value, in
    input order, including duplicates — deduplication happens at the
    call site once the allowed-scope intersection is known. A
    malformed (non-list/tuple, or containing a non-string) value
    safely yields no categories rather than raising."""
    if not isinstance(matched_rules, (list, tuple)):
        return ()
    categories: list[str] = []
    for rule in matched_rules:
        if not isinstance(rule, str):
            continue
        category = rule.split(":", 1)[0].strip()
        if category:
            categories.append(category)
    return tuple(categories)


def _dedupe_preserving_order(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return tuple(ordered)


def evaluate_theme_match(
    candidate: CandidateSignal,
    case_id: str,
    scope: ThemeMatchingScope,
    created_at: str,
) -> ResearchCaseThemeMatch | None:
    """Pure, deterministic, no I/O. Returns `None` whenever any gate
    fails to pass or the exclusion gate fires — never raises for a
    malformed `candidate`/`scope`. See module docstring for the full
    non-goal list.

    Gate order (all of A-C must pass; D overrides everything):
      D. Exclusion  — any excluded keyword anywhere in the combined
         text unconditionally disqualifies, checked first.
      A. Sector     — filing.theme_slug in scope.sector_tags, or
         filing.subtheme_slug in scope.sector_subtags.
      B. Category   — at least one `matched_rule.split(":", 1)[0]`
         category intersects scope.allowed_matched_rule_categories.
      C. Keyword    — at least one scope.required_keywords entry
         appears case-insensitively in excerpt_original + report_nm.
    """
    filing = getattr(candidate, "filing", None)
    excerpt_original = _as_text(getattr(candidate, "excerpt_original", None))
    report_nm = _as_text(getattr(filing, "report_nm", None))
    combined_text_lower = f"{excerpt_original} {report_nm}".lower()

    # D. Exclusion gate — overrides every positive gate, checked first.
    for excluded_keyword in scope.excluded_keywords:
        if isinstance(excluded_keyword, str) and excluded_keyword.lower() in combined_text_lower:
            return None

    # A. Sector gate. matched_sector_tag priority: subtheme_slug first,
    # then theme_slug — the more specific value wins when both match.
    theme_slug = getattr(filing, "theme_slug", None)
    subtheme_slug = getattr(filing, "subtheme_slug", None)
    theme_slug_matches = isinstance(theme_slug, str) and theme_slug in scope.sector_tags
    subtheme_slug_matches = isinstance(subtheme_slug, str) and subtheme_slug in scope.sector_subtags
    if not (theme_slug_matches or subtheme_slug_matches):
        return None
    matched_sector_tag = subtheme_slug if subtheme_slug_matches else theme_slug

    # B. Rule-category gate.
    allowed_categories = set(scope.allowed_matched_rule_categories)
    candidate_categories = _rule_categories(getattr(candidate, "matched_rules", None))
    matched_rule_categories = _dedupe_preserving_order(
        [category for category in candidate_categories if category in allowed_categories]
    )
    if not matched_rule_categories:
        return None

    # C. Keyword gate. Order/dedup follows scope.required_keywords'
    # own order, never the text's — deterministic regardless of where
    # in the combined text a keyword happens to appear.
    matched_keywords = _dedupe_preserving_order([
        keyword for keyword in scope.required_keywords
        if isinstance(keyword, str) and keyword.lower() in combined_text_lower
    ])
    if not matched_keywords:
        return None

    confidence = MatchConfidence.HIGH if len(matched_keywords) >= 2 else MatchConfidence.MEDIUM

    rationale = (
        f"Matched sector tag '{matched_sector_tag}'; "
        f"rule categories: {', '.join(matched_rule_categories)}; "
        f"keywords: {', '.join(matched_keywords)}."
    )

    return ResearchCaseThemeMatch(
        id=_build_match_id(case_id, scope.theme_id),
        case_id=case_id,
        theme_id=scope.theme_id,
        confidence=confidence,
        direction=EvidenceDirection.CONTEXT,
        matched_sector_tag=matched_sector_tag,
        matched_rule_categories=matched_rule_categories,
        matched_keywords=matched_keywords,
        rationale=rationale,
        created_at=created_at,
    )
