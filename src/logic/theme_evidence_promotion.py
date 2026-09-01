"""EevaResearch — Citrini-style Theme research workspace vertical slice
(design/DECISIONS.md). A pure, deterministic function that turns an
already-ACCEPTED `ThemeMatchReviewDecision` (plus its own
`ResearchCaseThemeMatch` and the original Radar `CandidateSignal`) into
one `ThemeEvidenceItem` — the bridge between the internal, CONTEXT-only
deterministic matching pipeline (src.logic.research_case_theme_matching)
and a theme's real evidence ledger.

This is the one place a human reviewer's own judgment (not a rule-based
gate) gets to assert `direction` as SUPPORTS/CONTRADICTS/MIXED — or
keep it CONTEXT — and author the `fact`/`relevance` text a real
evidence item requires. Every one of those four values is a caller-
supplied argument; this function never invents, infers, or generates
them, and never calls an LLM.

This module performs no I/O of any kind: no file/JSON/SQLite/Postgres
access, no persistence call, no network/source fetch, no LLM/model
call, no UI call, no random value, no system-clock read. Deliberately
does not import src.data_access.theme_store (or any data_access
module) — the deterministic evidence-id formula is reimplemented here
directly, mirroring src.logic.research_case_theme_matching's own
precedent of never depending on a persistence module from a pure logic
module."""
from __future__ import annotations

import hashlib

from src.models.models import CandidateSignal
from src.models.theme_matching import MatchReviewStatus, ResearchCaseThemeMatch, ThemeMatchReviewDecision
from src.models.theme_research import EvidenceDirection, ThemeEvidenceItem

_ID_DIGEST_CHARS = 24


def _build_theme_evidence_id(theme_id: str, source_url: str, date: str) -> str:
    """Byte-for-byte identical to src.data_access.theme_store.build_theme_evidence_id
    — reimplemented locally rather than imported, per this module's own
    no-data_access-dependency discipline."""
    digest = hashlib.sha256(f"{theme_id}|{source_url}|{date}".encode("utf-8")).hexdigest()
    return f"theme-evidence-{digest[:_ID_DIGEST_CHARS]}"


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def build_evidence_from_accepted_match(
    candidate: CandidateSignal,
    match: ResearchCaseThemeMatch,
    decision: ThemeMatchReviewDecision,
    direction: EvidenceDirection,
    fact: str,
    relevance: str,
) -> ThemeEvidenceItem | None:
    """Returns `None` — never raises — for any invalid, inconsistent,
    or malformed input combination:
      - `decision.decision` must be ACCEPTED (a pending or rejected
        decision is never eligible for promotion);
      - `decision.match_id` must equal `match.id` (the decision must
        actually belong to this match, never trusted by position alone);
      - the candidate must carry a well-formed `filing` with non-blank
        `rcept_dt`/`corp_name`/`source_name`/`source_url`;
      - `fact`/`relevance` must be non-blank curator-authored text.

    `direction` is deliberately unrestricted to any `EvidenceDirection`
    member (including CONTEXT) — a human reviewer, unlike the automatic
    matcher, is allowed to assert SUPPORTS/CONTRADICTS/MIXED."""
    if not isinstance(decision, ThemeMatchReviewDecision) or decision.decision is not MatchReviewStatus.ACCEPTED:
        return None
    if not isinstance(match, ResearchCaseThemeMatch) or decision.match_id != match.id:
        return None
    if not isinstance(direction, EvidenceDirection):
        return None
    if not _nonblank(fact) or not _nonblank(relevance):
        return None

    filing = getattr(candidate, "filing", None)
    date = getattr(filing, "rcept_dt", None)
    company = getattr(filing, "corp_name", None)
    source_name = getattr(filing, "source_name", None)
    source_url = getattr(filing, "source_url", None)
    if not all(_nonblank(v) for v in (date, company, source_name, source_url)):
        return None

    evidence_id = _build_theme_evidence_id(match.theme_id, source_url, date)
    return ThemeEvidenceItem(
        id=evidence_id, theme_id=match.theme_id, date=date, company=company,
        source_name=source_name, source_url=source_url, fact=fact, relevance=relevance, direction=direction,
    )
