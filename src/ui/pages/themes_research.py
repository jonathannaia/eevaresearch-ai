"""EevaResearch — Evidence-First Themes MVP (design/DECISIONS.md). The
public, read-only Themes product surface: a small number of curated,
cross-company research narratives built from official-source evidence,
distinct from Radar (individual detected company signals) and from the
internal Research Case workflow objects, which never appear here.

Display only: no authoring, write, or persistence-mutating call exists
in this module. Every persisted value is treated as caller-supplied
free text and escaped via html.escape() before being placed inside an
unsafe_allow_html markdown block — the same discipline
src/ui/pages/research_cases.py already established. A source_url only
ever renders as a clickable link when it starts with http:// or
https://; anything else renders as plain escaped text.

Read shape: this page uses ONLY backend_factory.get_theme_repository()
— the published-only protocol. It never imports
get_theme_curator_repository, theme_store's insert/update functions, or
scripts/create_theme.py. A theme that is internal/ready_to_publish/
archived is indistinguishable from a nonexistent one, enforced entirely
by the repository layer (see backend_factory.ThemeRepositoryProtocol's
own docstring) — this page performs no additional visibility filtering
of its own because none is needed or trusted to be sufficient on its
own.

Deliberate, documented departure from research_cases.py's own "list
view never reads evidence" discipline: Themes are an intentionally
small, curated, low-volume set (the whole point of this MVP), so the
index view calls evidence_for_theme()/company_map_for_theme() once per
published theme to compute the card's evidence/company counts — a
tradeoff that would not be acceptable for Radar-scale candidate volume
but is the right one here, given the approved spec's own card
requirements. No case_id, candidate_id, NEEDS_REVIEW, or any other
internal Research Case/Radar term is ever read, stored, or rendered by
this module."""
from __future__ import annotations

import html

import streamlit as st

from src.config.settings import get_settings
from src.data_access import backend_factory
from src.data_access.backend_factory import ThemeRepositoryProtocol
from src.models.theme_research import CompanyRole, ResearchTheme
from src.ui.components.empty_state import empty_state
from src.ui.components.section import section_header
from src.ui.ui import get_page

_PAGE_TITLE = "Themes"
_SCOPE_STATEMENT = (
    "Evidence-backed investigations into potential bottlenecks, demand shifts, "
    "and second-order company impacts."
)
_FOOTER_DISCLAIMER = "Informational research only; not investment advice."
_UNAVAILABLE_MESSAGE = "Themes are temporarily unavailable."

_EMPTY_STATE_TITLE = "No active themes yet"
_EMPTY_STATE_DETAIL = (
    "EevaResearch is monitoring official company disclosures for evidence of emerging bottlenecks, "
    "demand shifts, and second-order company impacts. Themes are published when multiple official "
    "sources support a specific, testable research question."
)

_COMPANY_ROLE_SECTION_ORDER: tuple[CompanyRole, ...] = (
    CompanyRole.DEMAND_DRIVER,
    CompanyRole.CONSTRAINT_OWNER,
    CompanyRole.ENABLER,
    CompanyRole.EXPOSED,
    CompanyRole.DISCONFIRMING,
)


def _esc(value: object) -> str:
    """Every caller-supplied/free-text value renders through this
    before being placed inside an unsafe_allow_html block."""
    if value is None:
        return ""
    return html.escape(str(value))


def _enum_label(value: object) -> str:
    """Safe display for an enum-shaped field — never raises, so a
    malformed/unexpected stored value degrades to a safe fallback
    instead of crashing the page."""
    raw = getattr(value, "value", value)
    escaped = _esc(raw)
    return escaped if escaped else "Unknown"


def _safe_source_url(url: object) -> str | None:
    """Only an http://​/https:// URL is ever rendered as a clickable
    link. Anything else — empty, malformed, or an unsafe scheme such as
    javascript:/data: — returns None, so the caller renders plain
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
    theme_id = st.query_params.get("theme_id", "").strip()

    try:
        repository = backend_factory.get_theme_repository(settings)
    except Exception:  # noqa: BLE001 — fail closed; never leak a raw connection/config error into the UI
        st.markdown(f'<div class="er-page-title">{_esc(_PAGE_TITLE)}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="er-muted">{_esc(_UNAVAILABLE_MESSAGE)}</div>', unsafe_allow_html=True)
        return

    if theme_id:
        _render_detail(repository, theme_id)
    else:
        _render_index(repository)


def _render_index(repository: ThemeRepositoryProtocol) -> None:
    st.markdown(f'<div class="er-page-title">{_esc(_PAGE_TITLE)}</div>', unsafe_allow_html=True)
    _scope_statement()

    try:
        themes = repository.list_published_themes()
    except Exception:  # noqa: BLE001 — fail closed; never leak a raw connection/config error into the UI
        st.markdown(f'<div class="er-muted">{_esc(_UNAVAILABLE_MESSAGE)}</div>', unsafe_allow_html=True)
        return

    if not themes:
        empty_state(_EMPTY_STATE_TITLE, _EMPTY_STATE_DETAIL, key="themes-empty")
        return

    detail_page = get_page("themes")
    for theme in themes:
        try:
            evidence = repository.evidence_for_theme(theme.id)
            company_map = repository.company_map_for_theme(theme.id)
        except Exception:  # noqa: BLE001 — one theme's count lookup failing must not take down the whole index
            evidence, company_map = (), ()
        _render_card(theme, evidence, company_map, detail_page)


def _render_card(theme: ResearchTheme, evidence, company_map, detail_page) -> None:
    distinct_companies = {item.company for item in evidence} | {entry.company_name for entry in company_map}
    with st.container(border=True, key=f"theme-card-{theme.id}"):
        top_cols = st.columns([2, 2, 4, 2], vertical_alignment="center")
        with top_cols[0]:
            st.markdown(f'<div class="er-muted">{_enum_label(theme.category)}</div>', unsafe_allow_html=True)
        with top_cols[1]:
            st.markdown(f'<div class="er-muted">{_enum_label(theme.status)}</div>', unsafe_allow_html=True)
        with top_cols[2]:
            st.markdown(f'<div class="er-card-title">{_esc(theme.title)}</div>', unsafe_allow_html=True)
        with top_cols[3]:
            st.markdown(f'<div class="er-muted" style="text-align:right;">{_esc(theme.updated_at)}</div>', unsafe_allow_html=True)

        st.markdown(f'<div style="margin-top:0.3rem;">{_esc(theme.hypothesis)}</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="er-muted" style="margin-top:0.3rem;"><strong>Key question:</strong> {_esc(theme.key_question)}</div>',
            unsafe_allow_html=True,
        )

        bottom_cols = st.columns([2, 2, 4], vertical_alignment="center")
        with bottom_cols[0]:
            st.markdown(f'<div class="er-muted">Evidence: {len(evidence)}</div>', unsafe_allow_html=True)
        with bottom_cols[1]:
            st.markdown(f'<div class="er-muted">Companies: {len(distinct_companies)}</div>', unsafe_allow_html=True)
        with bottom_cols[2]:
            if detail_page is not None:
                st.page_link(detail_page, label="Open", query_params={"theme_id": theme.id})


def _render_detail(repository: ThemeRepositoryProtocol, theme_id: str) -> None:
    list_page = get_page("themes")
    if list_page is not None:
        st.page_link(list_page, label="← All Themes")

    try:
        theme = repository.get_published_theme(theme_id)
        evidence_items: tuple = ()
        company_map: tuple = ()
        if theme is not None:
            evidence_items = repository.evidence_for_theme(theme_id)
            company_map = repository.company_map_for_theme(theme_id)
    except Exception:  # noqa: BLE001 — fail closed; never leak a raw connection/config error into the UI
        st.markdown(f'<div class="er-muted">{_esc(_UNAVAILABLE_MESSAGE)}</div>', unsafe_allow_html=True)
        return

    if theme is None:
        empty_state("Theme not found.", "This research theme does not exist, or is no longer available.", key="theme-not-found")
        return

    # Identity safety: only ever render a record whose own theme_id
    # exactly matches the selected theme — a mismatched record from a
    # defective backend/map is silently dropped, never displayed.
    evidence_items = tuple(item for item in evidence_items if item.theme_id == theme.id)
    company_map = tuple(entry for entry in company_map if entry.theme_id == theme.id)

    # 1. Title, category, status, last updated
    st.markdown(f'<div class="er-page-title">{_esc(theme.title)}</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="er-muted">{_enum_label(theme.category)} · {_enum_label(theme.status)} · '
        f'Updated {_esc(theme.updated_at)}</div>',
        unsafe_allow_html=True,
    )
    _scope_statement()

    # 2. The question
    section_header("The question")
    st.markdown(f'<div>{_esc(theme.key_question)}</div>', unsafe_allow_html=True)

    # 3. Working thesis
    section_header("Working thesis")
    st.markdown(f'<div>{_esc(theme.working_thesis)}</div>', unsafe_allow_html=True)

    # 4. Why it may matter
    section_header("Why it may matter")
    st.markdown(f'<div>{_esc(theme.why_it_matters)}</div>', unsafe_allow_html=True)

    # 5. Evidence ledger
    section_header("Evidence ledger")
    if not evidence_items:
        st.caption("No evidence recorded.")
    else:
        for item in evidence_items:
            _render_evidence_row(item)

    # 6. Company map
    section_header("Company map")
    if not company_map:
        st.caption("No companies mapped yet.")
    else:
        by_role: dict[CompanyRole, list] = {}
        for entry in company_map:
            by_role.setdefault(entry.role, []).append(entry)
        for role in _COMPANY_ROLE_SECTION_ORDER:
            entries = by_role.get(role)
            if not entries:
                continue
            st.markdown(f'<div class="er-muted" style="margin-top:0.4rem;"><strong>{_enum_label(role)}</strong></div>', unsafe_allow_html=True)
            for entry in entries:
                note = f" — {_esc(entry.note)}" if entry.note else ""
                st.markdown(f'<div style="margin-left:0.8rem;">{_esc(entry.company_name)}{note}</div>', unsafe_allow_html=True)

    # 7. What could change the view
    section_header("What could change the view")
    st.markdown(f'<div>{_esc(theme.what_could_change_the_view)}</div>', unsafe_allow_html=True)

    # 8. What to watch next
    section_header("What to watch next")
    st.markdown(f'<div>{_esc(theme.what_to_watch_next)}</div>', unsafe_allow_html=True)

    # 9. Footer
    _footer_disclaimer()


def _render_evidence_row(item) -> None:
    with st.container(border=True, key=f"theme-evidence-{item.id}"):
        cols = st.columns([2, 2, 3, 2], vertical_alignment="center")
        with cols[0]:
            st.markdown(f'<div class="er-muted">{_esc(item.date)}</div>', unsafe_allow_html=True)
        with cols[1]:
            st.markdown(f'<div class="er-muted">{_esc(item.company)}</div>', unsafe_allow_html=True)
        with cols[2]:
            st.markdown(f'<div class="er-muted">{_esc(item.source_name)}</div>', unsafe_allow_html=True)
        with cols[3]:
            st.markdown(f'<div class="er-muted" style="text-align:right;">{_enum_label(item.direction)}</div>', unsafe_allow_html=True)

        st.markdown(f'<div style="margin-top:0.2rem;"><strong>Observed:</strong> {_esc(item.fact)}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="er-muted" style="margin-top:0.15rem;"><strong>Relevance:</strong> {_esc(item.relevance)}</div>', unsafe_allow_html=True)

        safe_url = _safe_source_url(item.source_url)
        if safe_url:
            st.markdown(
                f'<div style="margin-top:0.2rem;"><a href="{html.escape(safe_url, quote=True)}" '
                f'target="_blank" rel="noopener noreferrer">{_esc(item.source_url)}</a></div>',
                unsafe_allow_html=True,
            )
        elif item.source_url:
            st.markdown(f'<div class="er-muted" style="margin-top:0.2rem;">{_esc(item.source_url)}</div>', unsafe_allow_html=True)
