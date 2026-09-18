"""Default research-surface visibility for filings (redesign v2 materiality
policy): a filing whose Radar candidate has already resolved to
CandidateStatus.NOT_MATERIAL is excluded from every default research-
facing list — Filings, the Dashboard's Regional Brief and Theme Activity,
Recently Updated (which already applied this) — and may only reappear
behind an explicit "Include administrative filings" control.

This module decides nothing about materiality itself: it only reads the
status the existing DART gates (radar_pipeline.py via
equity_transaction_materiality/ownership_materiality/low_value_filing_
rules) already persisted on the candidate. Pure, Streamlit-free, and
fail-closed in the "show" direction only in the sense that a store read
failure yields an EMPTY exclusion set — a filing is never hidden because
of an unrelated backend error, and never shown as material because of
one either (its own row simply falls back to the bare FilingEvent the
surface already rendered before this gate existed).
"""
from __future__ import annotations

from src.config.settings import Settings
from src.data_access import backend_factory
from src.models.models import CandidateSignal, CandidateStatus


def is_not_material(candidate: CandidateSignal | None) -> bool:
    return candidate is not None and candidate.status == CandidateStatus.NOT_MATERIAL


def not_material_rcept_nos(settings: Settings, source: str) -> frozenset[str]:
    """Receipt/accession/document ids of every candidate for `source`
    whose persisted status is NOT_MATERIAL — the exclusion set a default
    surface subtracts from its bare FilingEvent list. Read-only; an
    unreachable candidate store yields an empty set (see module note)."""
    try:
        candidates = backend_factory.get_candidate_repository(settings, source).load_candidates()
    except Exception:  # noqa: BLE001 — read failure never hides or promotes anything
        return frozenset()
    return frozenset(c.filing.rcept_no for c in candidates.values() if is_not_material(c))
