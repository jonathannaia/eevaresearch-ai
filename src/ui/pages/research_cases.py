"""EevaResearch Phase 4, Step 3C (design/DECISIONS.md) — hidden,
read-only Research Cases list/detail view for invited testers. Display
only: no authoring, write, or persistence-mutating call exists anywhere
in this module, and scripts/create_research_case.py (the only authoring
path) is never imported here.

Every persisted value is treated as caller-supplied free text and run
through `_esc()` (a thin html.escape() wrapper) before being placed
inside any `unsafe_allow_html=True` markdown block — the same discipline
radar_card.py already applies to `evidence_source_member`, applied here
to every field this page renders: title, question, trigger data, quotes,
translations, entities, supply-chain layer, reasoning, limitations, and
transmission paths. A `source_url` is only ever rendered as a clickable
link when it starts with `http://` or `https://` (see `_safe_source_url`)
— anything else (`javascript:`, `data:`, empty, malformed) renders as
plain escaped text instead.

Read shape (Step 3C's own explicit contract — see design/DECISIONS.md
and tests/test_research_cases_page.py for the proofs):
  - List view: exactly one bounded
    ResearchCaseRepositoryProtocol.list_recent_cases(20) call. No
    evidence/assertion read happens on this view at all.
  - Detail view: exactly one get_case(case_id) call, and only if a case
    is found, exactly one evidence_items_for_case_ids([case_id]) call
    and exactly one assertions_for_case_ids([case_id]) call — never a
    per-record call, and never for any case other than the one selected.

No source fetch, scan, translation, LLM/model call, or
research_case_validation call anywhere in this module. Repository
construction and every read are wrapped in a narrow `except Exception`
that renders only a restrained, generic unavailable message — never raw
exception text — matching radar_inbox.py's own fail-soft convention for
a comparison-repository read."""
from __future__ import annotations

import html
from typing import Sequence

import streamlit as st

from src.config.settings import get_settings
from src.data_access import backend_factory
from src.data_access.backend_factory import ResearchCaseRepositoryProtocol
from src.models.research_case import (
    AssertionStatus,
    DependencyAssertion,
    RelationshipAssertion,
    ResearchCase,
)
from src.ui.components.empty_state import empty_state
from src.ui.components.section import section_header
from src.ui.ui import get_page

_PAGE_TITLE = "Research Cases"
_SCOPE_STATEMENT = (
    "Structured research notes with source evidence. Not investment advice or verified causal conclusions."
)
_FOOTER_DISCLAIMER = (
    "Structured research notes only — not investment advice, a materiality judgment, "
    "or a verified causal conclusion."
)
_LIST_LIMIT = 20
_UNAVAILABLE_MESSAGE = "Research cases are temporarily unavailable."


def _esc(value: object) -> str:
    """Every caller-supplied/free-text value renders through this before
    being placed inside an unsafe_allow_html block — never a raw
    f-string interpolation of stored content."""
    if value is None:
        return ""
    return html.escape(str(value))


def _enum_label(value: object) -> str:
    """Safe display for an enum-shaped field: uses `.value` when present
    (the normal case), otherwise falls back to the escaped raw value —
    never raises, so a malformed/unexpected stored value degrades to a
    safe fallback instead of crashing the whole page."""
    raw = getattr(value, "value", value)
    escaped = _esc(raw)
    return escaped if escaped else "Unknown"


def _safe_source_url(url: object) -> str | None:
    """Only an `http://`/`https://` URL is ever rendered as a clickable
    link. Anything else — empty, malformed, or an unsafe scheme such as
    `javascript:`/`data:` — returns None, so the caller renders plain
    escaped text instead of a link."""
    if not isinstance(url, str):
        return None
    stripped = url.strip()
    lowered = stripped.lower()
    if lowered.startswith("https://") or lowered.startswith("http://"):
        return stripped
    return None


def _scope_statement() -> None:
    st.markdown(f'<div class="er-muted">{_esc(_SCOPE_STATEMENT)}</div>', unsafe_allow_html=True)


def _footer_disclaimer() -> None:
    st.divider()
    st.markdown(f'<div class="er-muted">{_esc(_FOOTER_DISCLAIMER)}</div>', unsafe_allow_html=True)


def render() -> None:
    settings = get_settings()
    case_id = st.query_params.get("case_id", "").strip()

    try:
        repository = backend_factory.get_research_case_repository(settings)
    except Exception:  # noqa: BLE001 — fail closed; never leak a raw connection/config error into the UI
        st.markdown(f'<div class="er-page-title">{_esc(_PAGE_TITLE)}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="er-muted">{_esc(_UNAVAILABLE_MESSAGE)}</div>', unsafe_allow_html=True)
        return

    if case_id:
        _render_detail(repository, case_id)
    else:
        _render_list(repository)


def _render_list(repository: ResearchCaseRepositoryProtocol) -> None:
    st.markdown(f'<div class="er-page-title">{_esc(_PAGE_TITLE)}</div>', unsafe_allow_html=True)
    _scope_statement()

    try:
        cases = repository.list_recent_cases(_LIST_LIMIT)
    except Exception:  # noqa: BLE001 — fail closed; never leak a raw connection/config error into the UI
        st.markdown(f'<div class="er-muted">{_esc(_UNAVAILABLE_MESSAGE)}</div>', unsafe_allow_html=True)
        return

    if not cases:
        empty_state(
            "No research cases yet.",
            "Curated research cases appear here once an operator records one.",
            key="research-cases-empty",
        )
        return

    detail_page = get_page("research_cases")
    for case in cases:
        _render_list_row(case, detail_page)


def _render_list_row(case: ResearchCase, detail_page) -> None:
    with st.container(border=True, key=f"research-case-row-{case.id}"):
        cols = st.columns([4, 2, 3, 2, 2, 2], vertical_alignment="center")
        with cols[0]:
            st.markdown(f'<div class="er-card-title">{_esc(case.title)}</div>', unsafe_allow_html=True)
        with cols[1]:
            st.markdown(f'<div class="er-muted">{_esc(case.trigger_source_type)}</div>', unsafe_allow_html=True)
        with cols[2]:
            st.markdown(f'<div class="er-muted">{_esc(case.trigger_source_name)}</div>', unsafe_allow_html=True)
        with cols[3]:
            st.markdown(f'<div class="er-muted">{_esc(case.created_at)}</div>', unsafe_allow_html=True)
        with cols[4]:
            st.markdown(f'<div class="er-muted">{_enum_label(case.status)}</div>', unsafe_allow_html=True)
        with cols[5]:
            if detail_page is not None:
                st.page_link(detail_page, label="Open", query_params={"case_id": case.id})


def _render_detail(repository: ResearchCaseRepositoryProtocol, case_id: str) -> None:
    list_page = get_page("research_cases")
    if list_page is not None:
        st.page_link(list_page, label="← All Research Cases")

    try:
        case = repository.get_case(case_id)
        evidence_items: tuple = ()
        assertions: tuple = ()
        if case is not None:
            evidence_items = repository.evidence_items_for_case_ids([case_id]).get(case_id, ())
            assertions = repository.assertions_for_case_ids([case_id]).get(case_id, ())
    except Exception:  # noqa: BLE001 — fail closed; never leak a raw connection/config error into the UI
        st.markdown(f'<div class="er-muted">{_esc(_UNAVAILABLE_MESSAGE)}</div>', unsafe_allow_html=True)
        return

    if case is None:
        empty_state("Case not found.", "This research case does not exist, or is no longer available.", key="research-case-not-found")
        return

    # Identity safety: only ever render a record whose own case_id
    # exactly matches the selected case — a mismatched record from a
    # defective backend/map is silently dropped, never displayed.
    evidence_items = tuple(item for item in evidence_items if item.case_id == case.id)
    assertions = tuple(a for a in assertions if a.case_id == case.id)

    st.markdown(f'<div class="er-page-title">{_esc(case.title)}</div>', unsafe_allow_html=True)
    _scope_statement()

    section_header("Research question")
    st.markdown(f'<div>{_esc(case.research_question)}</div>', unsafe_allow_html=True)

    section_header("Trigger")
    _detail_row("Source type", _esc(case.trigger_source_type))
    _detail_row("Source name", _esc(case.trigger_source_name))
    _detail_row("Summary", _esc(case.trigger_summary))
    _detail_row("Created", _esc(case.created_at))
    _detail_row("Status", _enum_label(case.status))

    section_header("Evidence")
    if not evidence_items:
        st.caption("No evidence recorded.")
    else:
        for item in evidence_items:
            _render_evidence_item(item)

    directly_supported = [a for a in assertions if a.assertion_status == AssertionStatus.DIRECTLY_SUPPORTED]
    hypotheses = [a for a in assertions if a.assertion_status == AssertionStatus.HYPOTHESIS]
    other = [a for a in assertions if a.assertion_status not in (AssertionStatus.DIRECTLY_SUPPORTED, AssertionStatus.HYPOTHESIS)]

    section_header("Directly supported")
    if not directly_supported:
        st.caption("None recorded.")
    else:
        for assertion in directly_supported:
            _render_assertion_summary(assertion)

    section_header("Hypotheses")
    if not hypotheses:
        st.caption("None recorded.")
    else:
        for assertion in hypotheses:
            _render_assertion_summary(assertion)
            _render_hypothesis_detail(assertion)

    section_header("Other recorded context")
    if not other:
        st.caption("None recorded.")
    else:
        for assertion in other:
            _detail_row("Status", _enum_label(assertion.assertion_status))
            _render_assertion_summary(assertion)

    _footer_disclaimer()


def _detail_row(label: str, escaped_value: str) -> None:
    st.markdown(f'<div class="er-muted"><strong>{_esc(label)}:</strong> {escaped_value}</div>', unsafe_allow_html=True)


def _render_evidence_item(item) -> None:
    with st.container(border=True, key=f"research-evidence-{item.id}"):
        _detail_row("Publisher / system", _esc(item.source_publisher_or_system))
        _detail_row("Source type", _esc(item.source_type))
        _detail_row("Source date", _esc(item.source_date))
        _detail_row("Original language", _esc(item.original_language))
        st.markdown(f'<div class="er-muted" style="margin-top:0.2rem;"><strong>Original excerpt</strong></div>', unsafe_allow_html=True)
        st.markdown(f'<div>{_esc(item.excerpt_original)}</div>', unsafe_allow_html=True)
        if item.excerpt_translated:
            st.markdown(
                '<div class="er-muted" style="margin-top:0.2rem;"><strong>Translated excerpt (machine translation)</strong></div>',
                unsafe_allow_html=True,
            )
            st.markdown(f'<div>{_esc(item.excerpt_translated)}</div>', unsafe_allow_html=True)
        safe_url = _safe_source_url(item.source_url)
        if safe_url:
            st.markdown(
                f'<div style="margin-top:0.2rem;"><a href="{html.escape(safe_url, quote=True)}" '
                f'target="_blank" rel="noopener noreferrer">{_esc(item.source_url)}</a></div>',
                unsafe_allow_html=True,
            )
        elif item.source_url:
            st.markdown(f'<div class="er-muted" style="margin-top:0.2rem;">{_esc(item.source_url)}</div>', unsafe_allow_html=True)


def _linked_evidence_count(assertion) -> int:
    evidence_ids = getattr(assertion, "evidence_ids", None)
    if isinstance(evidence_ids, (tuple, list)):
        return len(evidence_ids)
    return 0


def _render_assertion_summary(assertion) -> None:
    if isinstance(assertion, RelationshipAssertion):
        st.markdown(
            f'<div>{_esc(assertion.subject_entity)} — {_enum_label(assertion.role)} → {_esc(assertion.object_entity)}</div>',
            unsafe_allow_html=True,
        )
    elif isinstance(assertion, DependencyAssertion):
        layer = f" ({_esc(assertion.supply_chain_layer)})" if assertion.supply_chain_layer else ""
        st.markdown(
            f'<div>{_esc(assertion.affected_entity)} — {_enum_label(assertion.bottleneck_type)}{layer}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown('<div class="er-muted">Unknown assertion type.</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="er-muted" style="font-size:0.82rem;">Linked evidence: {_linked_evidence_count(assertion)}</div>',
        unsafe_allow_html=True,
    )


def _render_hypothesis_detail(assertion) -> None:
    if assertion.reasoning:
        _detail_row("Reasoning", _esc(assertion.reasoning))
    limitations: Sequence[str] = getattr(assertion, "limitations", ()) or ()
    if limitations:
        _detail_row("Limitations", "; ".join(_esc(limitation) for limitation in limitations))
    transmission_path: Sequence[str] | None = getattr(assertion, "transmission_path", None)
    if transmission_path:
        _detail_row("Transmission path", " → ".join(_esc(step) for step in transmission_path))
