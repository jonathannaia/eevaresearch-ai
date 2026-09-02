"""EevaResearch — Citrini-style Theme research workspace vertical slice
(design/DECISIONS.md). Hidden, internal-only UI so non-technical users
can run the full constraint-research workflow without shell scripts:
create an internal Theme (with its matching scope / "constraint
layers"), map company roles, review radar-match candidates and promote
them to SUPPORTS/CONTRADICTS/MIXED/CONTEXT evidence, record hypotheses/
decisions/watch items, and publish only via an explicit, gated
transition. Not a product feature: never linked from any sidebar nav
group, the command palette, or app.py's `HIDDEN_FROM_NAV` list — see
app.py's own registration of this page (a standalone `st.Page(...,
visibility="hidden")` entry, the same pattern as research_cases/
company/disclaimer). Gated a third way, on top of being hidden and
behind the existing app-wide private-beta login gate: this page
immediately returns if `settings.theme_workspace_enabled` is False.

Uses only the private ThemeCuratorRepositoryProtocol /
ThemeMatchingRepositoryProtocol seams — never
ThemeRepositoryProtocol (the public, published-only protocol
themes_research.py uses). A Theme is always created at
visibility=internal (hardcoded, no selector); the only way a theme's
visibility ever changes is the explicit "Publish" tab's own gated
transition, reusing the exact same internal -> ready_to_publish ->
published -> archived state machine scripts/create_theme.py already
enforces (duplicated here, not imported — this app must never import a
script), plus the same "at least one evidence item" requirement before
a theme may leave `internal`.

Every persisted or free-text value is escaped via `_esc()` before being
placed inside an `unsafe_allow_html=True` markdown block, and a
`source_url` is only ever rendered as a link when it starts with
`http://`/`https://` — the same discipline research_cases.py/
themes_research.py already apply. Radar candidate context (the EDGAR
CandidateSignal behind a pending match) is EDGAR-only for now, matching
Phase A2's own worker scope — `_CANDIDATE_SOURCE_NAME` is hardcoded to
"SEC EDGAR"."""
from __future__ import annotations

import html
from datetime import datetime, timezone

import streamlit as st

from src.config.settings import Settings, get_settings
from src.data_access import backend_factory
from src.data_access.backend_factory import (
    ThemeCuratorRepositoryProtocol,
    ThemeMatchingRepositoryProtocol,
)
from src.data_access.theme_matching_store import build_review_decision_id
from src.data_access.theme_store import build_theme_company_map_id, build_theme_id, build_theme_research_note_id
from src.logic.theme_auto_publish import evaluate_auto_publish_gates
from src.logic.theme_evidence_promotion import build_evidence_from_accepted_match
from src.models.theme_matching import (
    MatchReviewStatus,
    ResearchCaseThemeMatch,
    ThemeMatchingScope,
    ThemeMatchReviewDecision,
)
from src.models.theme_research import (
    CompanyRole,
    EvidenceDirection,
    HypothesisConfidence,
    ResearchTheme,
    ThemeCategory,
    ThemeCompanyMapEntry,
    ThemeNoteType,
    ThemeResearchNote,
    ThemeStatus,
    ThemeVisibility,
)
from src.ui.components.empty_state import empty_state
from src.ui.components.section import section_header
from src.ui.ui import get_page

_PAGE_TITLE = "Constraint Research Workspace"
_SCOPE_STATEMENT = (
    "Internal-only. Bottleneck/constraint research workspaces — never visible at /themes until explicitly published."
)
_UNAVAILABLE_MESSAGE = "The research workspace is temporarily unavailable."
_NOT_ENABLED_MESSAGE = "The constraint research workspace is not enabled on this deployment."

# EDGAR-only for now, matching Phase A2's own worker scope.
_CANDIDATE_SOURCE_NAME = "SEC EDGAR"

# Explicit evidence threshold before a theme may leave `internal` —
# necessary but not sufficient: the human "Apply transition" click is
# still always required regardless of whether these are met.
_MIN_EVIDENCE_ITEMS_TO_PUBLISH = 2
_MIN_DISTINCT_EVIDENCE_COMPANIES_TO_PUBLISH = 2

# Same state machine as scripts/create_theme.py's own
# _ALLOWED_VISIBILITY_TRANSITIONS — duplicated, not imported (this app
# must never import a script).
_ALLOWED_VISIBILITY_TRANSITIONS: dict[ThemeVisibility, frozenset[ThemeVisibility]] = {
    ThemeVisibility.INTERNAL: frozenset({ThemeVisibility.READY_TO_PUBLISH}),
    ThemeVisibility.READY_TO_PUBLISH: frozenset({ThemeVisibility.PUBLISHED, ThemeVisibility.INTERNAL}),
    ThemeVisibility.PUBLISHED: frozenset({ThemeVisibility.ARCHIVED}),
    ThemeVisibility.ARCHIVED: frozenset(),
}


def _esc(value: object) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


def _enum_label(value: object) -> str:
    raw = getattr(value, "value", value)
    escaped = _esc(raw)
    return escaped if escaped else "Unknown"


def _safe_source_url(url: object) -> str | None:
    if not isinstance(url, str):
        return None
    stripped = url.strip()
    lowered = stripped.lower()
    if lowered.startswith("https://") or lowered.startswith("http://"):
        return stripped
    return None


def _split_csv(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _detail_row(label: str, escaped_value: str) -> None:
    st.markdown(f'<div class="er-muted"><strong>{_esc(label)}:</strong> {escaped_value}</div>', unsafe_allow_html=True)


# ============================================================
# Business logic — plain functions, directly unit-testable
# without driving any Streamlit widget.
# ============================================================


def create_theme_with_scope(
    curator: ThemeCuratorRepositoryProtocol,
    matching_repo: ThemeMatchingRepositoryProtocol,
    *,
    title: str, category: ThemeCategory, key_question: str, hypothesis: str, working_thesis: str,
    why_it_matters: str, what_could_change_the_view: str, what_to_watch_next: str,
    sector_tags: tuple[str, ...], sector_subtags: tuple[str, ...], allowed_rule_categories: tuple[str, ...],
    required_keywords: tuple[str, ...], excluded_keywords: tuple[str, ...], created_at: str,
) -> tuple[ResearchTheme | None, tuple[str, ...]]:
    """Creates the Theme (always visibility=internal) and its matching
    scope ("constraint layers" = sector_subtags) together. Never raises.
    A scope-insert failure after a successful theme-insert is not an
    error state — a theme with no scope is simply inert for matching,
    exactly like every theme created before this scope existed; there
    is no orphan-record risk here (see design/DECISIONS.md)."""
    errors: list[str] = []
    required_fields = {
        "title": title, "key_question": key_question, "hypothesis": hypothesis, "working_thesis": working_thesis,
        "why_it_matters": why_it_matters, "what_could_change_the_view": what_could_change_the_view,
        "what_to_watch_next": what_to_watch_next,
    }
    for name, value in required_fields.items():
        if not value or not value.strip():
            errors.append(f"{name.replace('_', ' ')} must not be blank.")
    if not sector_tags:
        errors.append("At least one sector tag is required.")
    if not allowed_rule_categories:
        errors.append("At least one allowed rule category is required.")
    if not required_keywords:
        errors.append("At least one required keyword is required.")
    if errors:
        return None, tuple(errors)

    theme_id = build_theme_id(title, created_at)
    theme = ResearchTheme(
        id=theme_id, category=category, status=ThemeStatus.NEW, visibility=ThemeVisibility.INTERNAL,
        title=title, key_question=key_question, hypothesis=hypothesis, working_thesis=working_thesis,
        why_it_matters=why_it_matters, what_could_change_the_view=what_could_change_the_view,
        what_to_watch_next=what_to_watch_next, created_at=created_at, updated_at=created_at,
    )
    if not curator.insert_theme(theme):
        return None, ("A theme with this exact title and creation moment already exists.",)

    scope = ThemeMatchingScope(
        theme_id=theme_id, sector_tags=sector_tags, sector_subtags=sector_subtags,
        allowed_matched_rule_categories=allowed_rule_categories, required_keywords=required_keywords,
        excluded_keywords=excluded_keywords,
    )
    matching_repo.insert_scope(scope)
    return theme, ()


def add_company_map_entry(
    curator: ThemeCuratorRepositoryProtocol, theme_id: str, company_name: str, role: CompanyRole, note: str | None,
) -> tuple[bool, tuple[str, ...]]:
    if not company_name or not company_name.strip():
        return False, ("Company name must not be blank.",)
    entry_id = build_theme_company_map_id(theme_id, company_name, role)
    entry = ThemeCompanyMapEntry(id=entry_id, theme_id=theme_id, company_name=company_name, role=role, note=note or None)
    if not curator.insert_company_map_entry(entry):
        return False, ("This company/role combination is already mapped for this theme.",)
    return True, ()


def gather_match_context(settings: Settings, research_case_repo, match: ResearchCaseThemeMatch):
    """Returns (case, candidate) — either may be None. Never raises."""
    try:
        case = research_case_repo.get_case(match.case_id)
    except Exception:  # noqa: BLE001 — fail closed for display purposes
        return None, None
    if case is None:
        return None, None
    try:
        candidate_repo = backend_factory.get_candidate_repository(settings, _CANDIDATE_SOURCE_NAME)
        candidate = candidate_repo.get_candidate(case.trigger_source_id)
    except Exception:  # noqa: BLE001 — fail closed for display purposes
        return case, None
    return case, candidate


def promote_candidate(
    matching_repo: ThemeMatchingRepositoryProtocol, curator: ThemeCuratorRepositoryProtocol,
    candidate, match: ResearchCaseThemeMatch, direction: EvidenceDirection, fact: str, relevance: str,
    reviewed_at: str, reviewer_note: str | None = None,
) -> tuple[bool, tuple[str, ...]]:
    """Records an ACCEPTED review decision, then immediately promotes it
    to a real ThemeEvidenceItem — the one-click "review + promote"
    action, reusing the exact pure build_evidence_from_accepted_match
    from Phase A2/A3 verbatim."""
    if not fact or not fact.strip() or not relevance or not relevance.strip():
        return False, ("Fact and relevance must not be blank.",)

    decision = ThemeMatchReviewDecision(
        id=build_review_decision_id(match.id, reviewed_at), match_id=match.id,
        decision=MatchReviewStatus.ACCEPTED, reviewer_note=reviewer_note, reviewed_at=reviewed_at,
    )
    if not matching_repo.insert_review_decision(decision):
        return False, ("A review decision for this match already exists.",)

    evidence = build_evidence_from_accepted_match(candidate, match, decision, direction, fact, relevance)
    if evidence is None:
        return False, ("Could not build a valid evidence item from this match/candidate/decision.",)

    if not curator.insert_evidence_item(evidence):
        return False, ("Evidence for this match already exists.",)
    return True, ()


def reject_candidate(
    matching_repo: ThemeMatchingRepositoryProtocol, match: ResearchCaseThemeMatch,
    reviewed_at: str, reviewer_note: str | None = None,
) -> tuple[bool, tuple[str, ...]]:
    decision = ThemeMatchReviewDecision(
        id=build_review_decision_id(match.id, reviewed_at), match_id=match.id,
        decision=MatchReviewStatus.REJECTED, reviewer_note=reviewer_note, reviewed_at=reviewed_at,
    )
    if not matching_repo.insert_review_decision(decision):
        return False, ("A review decision for this match already exists.",)
    return True, ()


def add_research_note(
    curator: ThemeCuratorRepositoryProtocol, theme_id: str, note_type: ThemeNoteType, content: str,
    confidence: HypothesisConfidence | None, disconfirming_condition: str | None, created_at: str,
) -> tuple[bool, tuple[str, ...]]:
    errors: list[str] = []
    if not content or not content.strip():
        errors.append("Content must not be blank.")
    if note_type is ThemeNoteType.HYPOTHESIS:
        if confidence is None:
            errors.append("Confidence is required for a hypothesis.")
        if not disconfirming_condition or not disconfirming_condition.strip():
            errors.append("Disconfirming condition is required for a hypothesis.")
    else:
        confidence = None
        disconfirming_condition = None
    if errors:
        return False, tuple(errors)

    note = ThemeResearchNote(
        id=build_theme_research_note_id(theme_id, note_type, content, created_at), theme_id=theme_id,
        note_type=note_type, content=content, confidence=confidence,
        disconfirming_condition=disconfirming_condition, created_at=created_at,
    )
    if not curator.insert_research_note(note):
        return False, ("This exact note already exists.",)
    return True, ()


def publish_transition(
    curator: ThemeCuratorRepositoryProtocol, theme: ResearchTheme, new_visibility: ThemeVisibility, updated_at: str,
) -> tuple[ResearchTheme | None, tuple[str, ...]]:
    allowed = _ALLOWED_VISIBILITY_TRANSITIONS.get(theme.visibility, frozenset())
    if new_visibility not in allowed:
        return None, (f"Cannot transition from {theme.visibility.value!r} to {new_visibility.value!r}.",)
    if theme.visibility is ThemeVisibility.INTERNAL and new_visibility is ThemeVisibility.READY_TO_PUBLISH:
        evidence = curator.evidence_for_theme(theme.id)
        if len(evidence) < _MIN_EVIDENCE_ITEMS_TO_PUBLISH:
            return None, (
                f"At least {_MIN_EVIDENCE_ITEMS_TO_PUBLISH} evidence items are required before marking this "
                f"theme ready to publish (currently {len(evidence)}).",
            )
        distinct_companies = {item.company for item in evidence if item.company}
        if len(distinct_companies) < _MIN_DISTINCT_EVIDENCE_COMPANIES_TO_PUBLISH:
            return None, (
                f"Evidence from at least {_MIN_DISTINCT_EVIDENCE_COMPANIES_TO_PUBLISH} distinct companies is "
                f"required before marking this theme ready to publish (currently {len(distinct_companies)}).",
            )
    updated = curator.set_visibility(theme.id, new_visibility, updated_at)
    if updated is None:
        return None, ("Transition failed — theme not found.",)
    return updated, ()


def unpublish_theme(
    curator: ThemeCuratorRepositoryProtocol, theme: ResearchTheme, reason: str, updated_at: str,
) -> tuple[ResearchTheme | None, tuple[str, ...]]:
    """Publication is reversible only via this explicit, audited path
    (design/DECISIONS.md, Phase 2 auto-publish policy): reuses the
    already-allowed PUBLISHED -> ARCHIVED transition, but requires a
    non-blank reason and records it as one immutable DECISION
    ThemeResearchNote."""
    if not reason or not reason.strip():
        return None, ("A reason is required to unpublish (archive) a published theme.",)
    updated, errors = publish_transition(curator, theme, ThemeVisibility.ARCHIVED, updated_at)
    if errors or updated is None:
        return updated, errors
    note_content = f"Unpublished (archived): {reason.strip()}"
    note = ThemeResearchNote(
        id=build_theme_research_note_id(theme.id, ThemeNoteType.DECISION, note_content, updated_at),
        theme_id=theme.id,
        note_type=ThemeNoteType.DECISION,
        content=note_content,
        confidence=None,
        disconfirming_condition=None,
        created_at=updated_at,
    )
    curator.insert_research_note(note)
    return updated, ()


# ============================================================
# Rendering
# ============================================================


def render() -> None:
    settings = get_settings()
    if not settings.theme_workspace_enabled:
        st.markdown(f'<div class="er-page-title">{_esc(_PAGE_TITLE)}</div>', unsafe_allow_html=True)
        st.info(_NOT_ENABLED_MESSAGE)
        return

    theme_id = st.query_params.get("theme_id", "").strip()

    try:
        curator = backend_factory.get_theme_curator_repository(settings)
        matching_repo = backend_factory.get_theme_matching_repository(settings)
        research_case_repo = backend_factory.get_research_case_repository(settings)
    except Exception:  # noqa: BLE001 — fail closed; never leak a raw connection/config error into the UI
        st.markdown(f'<div class="er-page-title">{_esc(_PAGE_TITLE)}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="er-muted">{_esc(_UNAVAILABLE_MESSAGE)}</div>', unsafe_allow_html=True)
        return

    if theme_id:
        _render_detail(settings, curator, matching_repo, research_case_repo, theme_id)
    else:
        _render_list_and_create(curator, matching_repo)


def _render_list_and_create(curator: ThemeCuratorRepositoryProtocol, matching_repo: ThemeMatchingRepositoryProtocol) -> None:
    st.markdown(f'<div class="er-page-title">{_esc(_PAGE_TITLE)}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="er-muted">{_esc(_SCOPE_STATEMENT)}</div>', unsafe_allow_html=True)

    try:
        themes = curator.list_themes()
    except Exception:  # noqa: BLE001 — fail closed
        st.markdown(f'<div class="er-muted">{_esc(_UNAVAILABLE_MESSAGE)}</div>', unsafe_allow_html=True)
        return

    section_header("Themes")
    if not themes:
        empty_state("No themes yet.", "Create the first internal Theme below.", key="theme-workspace-empty")
    else:
        detail_page = get_page("theme_workspace")
        for theme in themes:
            _render_theme_row(theme, detail_page)

    st.divider()
    section_header("Create a new internal Theme")
    _render_create_form(curator, matching_repo)


def _render_theme_row(theme: ResearchTheme, detail_page) -> None:
    with st.container(border=True, key=f"theme-workspace-row-{theme.id}"):
        cols = st.columns([4, 2, 2, 2], vertical_alignment="center")
        with cols[0]:
            st.markdown(f'<div class="er-card-title">{_esc(theme.title)}</div>', unsafe_allow_html=True)
        with cols[1]:
            st.markdown(f'<div class="er-muted">{_enum_label(theme.status)}</div>', unsafe_allow_html=True)
        with cols[2]:
            st.markdown(f'<div class="er-muted">{_enum_label(theme.visibility)}</div>', unsafe_allow_html=True)
        with cols[3]:
            if detail_page is not None:
                st.page_link(detail_page, label="Open", query_params={"theme_id": theme.id})


def _render_create_form(curator: ThemeCuratorRepositoryProtocol, matching_repo: ThemeMatchingRepositoryProtocol) -> None:
    with st.form("theme-workspace-create-form", clear_on_submit=True):
        title = st.text_input("Title")
        category = st.selectbox("Category", list(ThemeCategory), format_func=lambda c: c.value)
        key_question = st.text_area("Key question")
        hypothesis = st.text_area("Hypothesis (one sentence)")
        working_thesis = st.text_area("Working thesis")
        why_it_matters = st.text_area("Why it may matter")
        what_could_change_the_view = st.text_area("What could change the view")
        what_to_watch_next = st.text_area("What to watch next")

        st.markdown("**Constraint layers & matching scope**")
        sector_tags_raw = st.text_input("Sector tags (comma-separated)")
        sector_subtags_raw = st.text_input("Constraint layers / sector subtags (comma-separated)")
        allowed_rule_categories_raw = st.text_input("Allowed EDGAR rule categories (comma-separated)")
        required_keywords_raw = st.text_input("Required keywords (comma-separated)")
        excluded_keywords_raw = st.text_input("Excluded phrases (comma-separated, optional)")

        submitted = st.form_submit_button("Create Theme")

    if not submitted:
        return

    created_at = datetime.now(timezone.utc).isoformat()
    theme, errors = create_theme_with_scope(
        curator, matching_repo,
        title=title.strip(), category=category, key_question=key_question.strip(), hypothesis=hypothesis.strip(),
        working_thesis=working_thesis.strip(), why_it_matters=why_it_matters.strip(),
        what_could_change_the_view=what_could_change_the_view.strip(), what_to_watch_next=what_to_watch_next.strip(),
        sector_tags=_split_csv(sector_tags_raw), sector_subtags=_split_csv(sector_subtags_raw),
        allowed_rule_categories=_split_csv(allowed_rule_categories_raw), required_keywords=_split_csv(required_keywords_raw),
        excluded_keywords=_split_csv(excluded_keywords_raw), created_at=created_at,
    )
    if errors:
        for error in errors:
            st.error(error)
    elif theme is not None:
        st.success(f"Theme created: {theme.title}")
        st.rerun()


def _render_detail(
    settings: Settings, curator: ThemeCuratorRepositoryProtocol, matching_repo: ThemeMatchingRepositoryProtocol,
    research_case_repo, theme_id: str,
) -> None:
    list_page = get_page("theme_workspace")
    if list_page is not None:
        st.page_link(list_page, label="← All Themes")

    try:
        theme = curator.get_theme(theme_id)
    except Exception:  # noqa: BLE001 — fail closed
        st.markdown(f'<div class="er-muted">{_esc(_UNAVAILABLE_MESSAGE)}</div>', unsafe_allow_html=True)
        return

    if theme is None:
        empty_state("Theme not found.", "This theme does not exist, or is no longer available.", key="theme-workspace-not-found")
        return

    st.markdown(f'<div class="er-page-title">{_esc(theme.title)}</div>', unsafe_allow_html=True)

    tabs = st.tabs(["Overview", "Company Map", "Radar Candidates", "Evidence", "Research Log", "Publish"])
    with tabs[0]:
        _render_overview_tab(matching_repo, theme)
    with tabs[1]:
        _render_company_map_tab(curator, theme)
    with tabs[2]:
        _render_candidates_tab(settings, curator, matching_repo, research_case_repo, theme)
    with tabs[3]:
        _render_evidence_tab(curator, theme)
    with tabs[4]:
        _render_research_log_tab(curator, theme)
    with tabs[5]:
        _render_publish_tab(curator, theme)


def _render_overview_tab(matching_repo: ThemeMatchingRepositoryProtocol, theme: ResearchTheme) -> None:
    _detail_row("Status", _enum_label(theme.status))
    _detail_row("Visibility", _enum_label(theme.visibility))
    _detail_row("Created", _esc(theme.created_at))

    for label, value in [
        ("Key question", theme.key_question), ("Hypothesis", theme.hypothesis), ("Working thesis", theme.working_thesis),
        ("Why it may matter", theme.why_it_matters), ("What could change the view", theme.what_could_change_the_view),
        ("What to watch next", theme.what_to_watch_next),
    ]:
        section_header(label)
        st.markdown(f'<div>{_esc(value)}</div>', unsafe_allow_html=True)

    section_header("Constraint layers (matching scope)")
    try:
        scope = matching_repo.get_scope(theme.id)
    except Exception:  # noqa: BLE001 — fail closed
        scope = None
    if scope is None:
        st.caption("No matching scope configured — this theme will never surface radar candidates.")
    else:
        _detail_row("Sector tags", ", ".join(_esc(t) for t in scope.sector_tags) or "None")
        _detail_row("Constraint layers", ", ".join(_esc(t) for t in scope.sector_subtags) or "None")
        _detail_row("Allowed rule categories", ", ".join(_esc(t) for t in scope.allowed_matched_rule_categories) or "None")
        _detail_row("Required keywords", ", ".join(_esc(t) for t in scope.required_keywords) or "None")
        _detail_row("Excluded phrases", ", ".join(_esc(t) for t in scope.excluded_keywords) or "None")


def _render_company_map_tab(curator: ThemeCuratorRepositoryProtocol, theme: ResearchTheme) -> None:
    try:
        entries = curator.company_map_for_theme(theme.id)
    except Exception:  # noqa: BLE001 — fail closed
        st.markdown(f'<div class="er-muted">{_esc(_UNAVAILABLE_MESSAGE)}</div>', unsafe_allow_html=True)
        return

    if not entries:
        st.caption("No company roles mapped yet.")
    else:
        for entry in entries:
            with st.container(border=True, key=f"theme-workspace-company-{entry.id}"):
                st.markdown(
                    f'<div><strong>{_esc(entry.company_name)}</strong> — {_enum_label(entry.role)}</div>',
                    unsafe_allow_html=True,
                )
                if entry.note:
                    st.markdown(f'<div class="er-muted">{_esc(entry.note)}</div>', unsafe_allow_html=True)

    st.divider()
    with st.form(f"theme-workspace-company-form-{theme.id}", clear_on_submit=True):
        company_name = st.text_input("Company name")
        role = st.selectbox("Role", list(CompanyRole), format_func=lambda r: r.value)
        note = st.text_area("Note (optional)")
        submitted = st.form_submit_button("Add company role")

    if submitted:
        created, errors = add_company_map_entry(curator, theme.id, company_name.strip(), role, note.strip() or None)
        if errors:
            for error in errors:
                st.error(error)
        elif created:
            st.success("Company role added.")
            st.rerun()


def _render_candidates_tab(
    settings: Settings, curator: ThemeCuratorRepositoryProtocol, matching_repo: ThemeMatchingRepositoryProtocol,
    research_case_repo, theme: ResearchTheme,
) -> None:
    try:
        pending = [m for m in matching_repo.list_pending_matches() if m.theme_id == theme.id]
    except Exception:  # noqa: BLE001 — fail closed
        st.markdown(f'<div class="er-muted">{_esc(_UNAVAILABLE_MESSAGE)}</div>', unsafe_allow_html=True)
        return

    if not pending:
        st.caption("No pending radar candidates for this theme.")
        return

    for match in pending:
        _render_candidate_review(settings, curator, matching_repo, research_case_repo, match)


def _render_candidate_review(
    settings: Settings, curator: ThemeCuratorRepositoryProtocol, matching_repo: ThemeMatchingRepositoryProtocol,
    research_case_repo, match: ResearchCaseThemeMatch,
) -> None:
    case, candidate = gather_match_context(settings, research_case_repo, match)
    with st.container(border=True, key=f"theme-workspace-candidate-{match.id}"):
        if case is None or candidate is None:
            st.markdown('<div class="er-muted">This candidate\'s underlying record is no longer available.</div>', unsafe_allow_html=True)
            return

        filing = candidate.filing
        st.markdown(
            f'<div><strong>{_esc(filing.corp_name)}</strong> — {_esc(filing.report_nm)} ({_esc(filing.rcept_dt)})</div>',
            unsafe_allow_html=True,
        )
        _detail_row("Matched sector tag", _esc(match.matched_sector_tag) or "—")
        _detail_row("Matched rule categories", ", ".join(_esc(c) for c in match.matched_rule_categories))
        _detail_row("Matched keywords", ", ".join(_esc(k) for k in match.matched_keywords))
        _detail_row("Rationale", _esc(match.rationale))
        st.markdown('<div class="er-muted" style="margin-top:0.3rem;"><strong>Excerpt</strong></div>', unsafe_allow_html=True)
        st.markdown(f'<div>{_esc(candidate.excerpt_original)}</div>', unsafe_allow_html=True)
        safe_url = _safe_source_url(filing.source_url)
        if safe_url:
            st.markdown(
                f'<div style="margin-top:0.3rem;"><a href="{html.escape(safe_url, quote=True)}" '
                f'target="_blank" rel="noopener noreferrer">{_esc(filing.source_url)}</a></div>',
                unsafe_allow_html=True,
            )

        with st.form(f"theme-workspace-review-form-{match.id}"):
            direction = st.selectbox(
                "Direction", list(EvidenceDirection), format_func=lambda d: d.value, key=f"direction-{match.id}",
            )
            fact = st.text_area("Fact (what did the filing say)", key=f"fact-{match.id}")
            relevance = st.text_area("Relevance (why it matters to the theme)", key=f"relevance-{match.id}")
            reviewer_note = st.text_input("Reviewer note (optional)", key=f"note-{match.id}")
            col1, col2 = st.columns(2)
            with col1:
                accept_clicked = st.form_submit_button("Accept as evidence")
            with col2:
                reject_clicked = st.form_submit_button("Reject")

        if accept_clicked or reject_clicked:
            reviewed_at = datetime.now(timezone.utc).isoformat()
            if accept_clicked:
                ok, errors = promote_candidate(
                    matching_repo, curator, candidate, match, direction, fact.strip(), relevance.strip(),
                    reviewed_at, reviewer_note.strip() or None,
                )
                success_message = "Match accepted and promoted to evidence."
            else:
                ok, errors = reject_candidate(matching_repo, match, reviewed_at, reviewer_note.strip() or None)
                success_message = "Match rejected."

            if errors:
                for error in errors:
                    st.error(error)
            elif ok:
                st.success(success_message)
                st.rerun()


def _render_evidence_tab(curator: ThemeCuratorRepositoryProtocol, theme: ResearchTheme) -> None:
    try:
        evidence = curator.evidence_for_theme(theme.id)
    except Exception:  # noqa: BLE001 — fail closed
        st.markdown(f'<div class="er-muted">{_esc(_UNAVAILABLE_MESSAGE)}</div>', unsafe_allow_html=True)
        return

    if not evidence:
        st.caption("No evidence recorded yet.")
        return

    by_direction: dict[EvidenceDirection, list] = {}
    for item in evidence:
        by_direction.setdefault(item.direction, []).append(item)

    for direction in (EvidenceDirection.SUPPORTS, EvidenceDirection.CONTRADICTS, EvidenceDirection.MIXED, EvidenceDirection.CONTEXT):
        items = by_direction.get(direction, [])
        section_header(f"{direction.value} ({len(items)})")
        if not items:
            st.caption("None.")
            continue
        for item in items:
            _render_evidence_item(item)


def _render_evidence_item(item) -> None:
    with st.container(border=True, key=f"theme-workspace-evidence-{item.id}"):
        _detail_row("Company", _esc(item.company))
        _detail_row("Date", _esc(item.date))
        _detail_row("Source", _esc(item.source_name))
        st.markdown('<div style="margin-top:0.2rem;"><strong>Fact</strong></div>', unsafe_allow_html=True)
        st.markdown(f'<div>{_esc(item.fact)}</div>', unsafe_allow_html=True)
        st.markdown('<div style="margin-top:0.2rem;"><strong>Relevance</strong></div>', unsafe_allow_html=True)
        st.markdown(f'<div>{_esc(item.relevance)}</div>', unsafe_allow_html=True)
        safe_url = _safe_source_url(item.source_url)
        if safe_url:
            st.markdown(
                f'<div style="margin-top:0.2rem;"><a href="{html.escape(safe_url, quote=True)}" '
                f'target="_blank" rel="noopener noreferrer">{_esc(item.source_url)}</a></div>',
                unsafe_allow_html=True,
            )
        elif item.source_url:
            st.markdown(f'<div class="er-muted" style="margin-top:0.2rem;">{_esc(item.source_url)}</div>', unsafe_allow_html=True)


def _render_research_log_tab(curator: ThemeCuratorRepositoryProtocol, theme: ResearchTheme) -> None:
    try:
        notes = curator.research_notes_for_theme(theme.id)
    except Exception:  # noqa: BLE001 — fail closed
        st.markdown(f'<div class="er-muted">{_esc(_UNAVAILABLE_MESSAGE)}</div>', unsafe_allow_html=True)
        return

    for label, note_type in [("Hypotheses", ThemeNoteType.HYPOTHESIS), ("Decisions", ThemeNoteType.DECISION), ("Watch items", ThemeNoteType.WATCH_ITEM)]:
        section_header(label)
        matching_notes = [n for n in notes if n.note_type is note_type]
        if not matching_notes:
            st.caption("None recorded.")
        for note in matching_notes:
            _render_note(note)

    st.divider()
    section_header("Add a note")
    with st.form(f"theme-workspace-note-form-{theme.id}", clear_on_submit=True):
        note_type = st.selectbox("Type", list(ThemeNoteType), format_func=lambda t: t.value)
        content = st.text_area("Content")
        confidence_raw = st.selectbox("Confidence (hypotheses only)", ["—"] + [c.value for c in HypothesisConfidence])
        disconfirming_condition = st.text_area("Disconfirming condition (hypotheses only)")
        submitted = st.form_submit_button("Add note")

    if submitted:
        confidence = HypothesisConfidence(confidence_raw) if confidence_raw != "—" else None
        created_at = datetime.now(timezone.utc).isoformat()
        ok, errors = add_research_note(curator, theme.id, note_type, content.strip(), confidence, disconfirming_condition.strip() or None, created_at)
        if errors:
            for error in errors:
                st.error(error)
        elif ok:
            st.success("Note added.")
            st.rerun()


def _render_note(note: ThemeResearchNote) -> None:
    with st.container(border=True, key=f"theme-workspace-note-{note.id}"):
        st.markdown(f'<div>{_esc(note.content)}</div>', unsafe_allow_html=True)
        if note.confidence is not None:
            _detail_row("Confidence", _enum_label(note.confidence))
        if note.disconfirming_condition:
            _detail_row("Disconfirming condition", _esc(note.disconfirming_condition))
        _detail_row("Recorded", _esc(note.created_at))


def _render_auto_publish_eligibility(curator: ThemeCuratorRepositoryProtocol, theme: ResearchTheme) -> None:
    """Live, read-only eligibility computed on every render — never
    persists anything (the audit DECISION note is only ever written by
    the worker's own successful auto-publish transition, or by the
    explicit unpublish action below, to avoid research-log spam)."""
    section_header("Auto-publish eligibility")
    try:
        evidence = curator.evidence_for_theme(theme.id)
        notes = curator.research_notes_for_theme(theme.id)
        evaluation = evaluate_auto_publish_gates(theme, evidence, notes)
    except Exception:
        st.caption("Auto-publish eligibility is temporarily unavailable.")
        return
    for gate in evaluation.gates:
        icon = "✅" if gate.passed else "❌"
        st.markdown(
            f'<div class="er-muted">{icon} <strong>{_esc(gate.name)}:</strong> {_esc(gate.detail)}</div>',
            unsafe_allow_html=True,
        )
    if evaluation.eligible:
        st.caption(
            "All auto-publish gates currently pass. If EDGE_THEME_AUTO_PUBLISH_ENABLED is on, the worker "
            "will publish this theme on a future tick."
        )
    else:
        st.caption("Not yet eligible for auto-publish — see failing gates above.")
    st.divider()


def _render_publish_tab(curator: ThemeCuratorRepositoryProtocol, theme: ResearchTheme) -> None:
    _detail_row("Current visibility", _enum_label(theme.visibility))

    if theme.visibility is ThemeVisibility.INTERNAL:
        _render_auto_publish_eligibility(curator, theme)

    allowed = _ALLOWED_VISIBILITY_TRANSITIONS.get(theme.visibility, frozenset())
    if not allowed:
        st.caption("No further transitions are possible from this state.")
        return

    options = sorted(v.value for v in allowed)
    choice = st.selectbox("Transition to", options, key=f"theme-workspace-publish-select-{theme.id}")

    is_unpublish = theme.visibility is ThemeVisibility.PUBLISHED and choice == ThemeVisibility.ARCHIVED.value
    reason = ""
    if is_unpublish:
        reason = st.text_area(
            "Reason for unpublishing (required)", key=f"theme-workspace-unpublish-reason-{theme.id}"
        )

    if st.button("Apply transition", key=f"theme-workspace-publish-btn-{theme.id}"):
        new_visibility = ThemeVisibility(choice)
        updated_at = datetime.now(timezone.utc).isoformat()
        if is_unpublish:
            updated, errors = unpublish_theme(curator, theme, reason, updated_at)
        else:
            updated, errors = publish_transition(curator, theme, new_visibility, updated_at)
        if errors:
            for error in errors:
                st.error(error)
        elif updated is not None:
            st.success(f"Theme is now {updated.visibility.value}.")
            st.rerun()
