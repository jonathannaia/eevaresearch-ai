"""EevaResearch — Evidence-First Themes MVP (design/DECISIONS.md). The
public, read-only "Research Theses" product surface (user-facing name
only — every internal model/table/repository/route identifier stays
"Theme"/"theme_id"/etc. unchanged, no rename, no data migration): a
small number of curated, cross-company research narratives built from
official-source evidence, distinct from Radar (individual detected
company signals) and from the internal Research Case workflow objects,
which never appear here.

Open to every signed-in user (Research Theses admin-gate removal,
design/DECISIONS.md) — app.py's own mandatory Google sign-in gate,
which runs before any page including this one, is the only
authentication check this route needs or has. There is deliberately no
further is_admin()/role check inside this module: admin-only Theme
*authoring, curation, editing, and visibility transitions* live
entirely in the separate internal src/ui/pages/theme_workspace.py tool
(its own admin/feature-flag gate, completely untouched by this file),
never here — this page only ever reads, never writes.

Display only: no authoring, write, or persistence-mutating call exists
in this module. Every persisted value is treated as caller-supplied
free text and escaped via html.escape() before being placed inside an
unsafe_allow_html markdown block — the same discipline
src/ui/pages/research_cases.py already established. A source_url only
ever renders as a clickable link when it starts with http:// or
https://; anything else renders as plain escaped text.

Read shape: this page uses ONLY backend_factory.get_theme_repository()
— the published-only protocol (list_published_themes()/
get_published_theme(), plus evidence_for_theme()/company_map_for_theme()
scoped to an already-published theme_id). It never imports
get_theme_curator_repository, theme_store's insert/update functions, or
scripts/create_theme.py. A theme that is internal/ready_to_publish/
archived is indistinguishable from a nonexistent one, enforced entirely
by the repository layer (see backend_factory.ThemeRepositoryProtocol's
own docstring) — this page performs no additional visibility filtering
of its own because none is needed or trusted to be sufficient on its
own. This is exactly as true for a non-admin visitor as it was
previously true only for an admin one — removing the is_admin() gate
above did not change, weaken, or bypass this read boundary in any way.

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
from collections import Counter

import streamlit as st

from src.config.settings import get_settings
from src.config.tracked_companies import get_tracked_companies
from src.data_access import backend_factory
from src.data_access.backend_factory import ThemeRepositoryProtocol
from src.data_access.daily_news import daily_news_backend
from src.logic import theme_evidence
from src.logic.formatting import fmt_datetime_local
from src.logic.market_map import jurisdiction_for_source
from src.logic.source_link import public_source_url
from src.models.theme_research import CompanyRole, EvidenceDirection, ResearchTheme, ThemeCategory, ThemeStatus
from src.ui import render_timing
from src.ui.components.empty_state import empty_state
from src.ui.components.primitives import EVIDENCE_DIRECTIONS, chip_html, evidence_bar_html, page_header
from src.ui.components.section import section_header
from src.ui.ui import get_page

# User-facing product name only — see module docstring's opening note.
_PAGE_TITLE = "Research Theses"
_SCOPE_STATEMENT = (
    "Evidence-backed investigations into potential bottlenecks, demand shifts, "
    "and second-order company impacts."
)
_FOOTER_DISCLAIMER = "Informational research only; not investment advice."
_UNAVAILABLE_MESSAGE = "Research Theses are temporarily unavailable."

_EMPTY_STATE_TITLE = "No active research theses yet"
_EMPTY_STATE_DETAIL = (
    "EevaResearch is monitoring official company disclosures for evidence of emerging bottlenecks, "
    "demand shifts, and second-order company impacts. Research Theses are published when multiple "
    "official sources support a specific, testable research question."
)

# Research Theses — live evidence wiring (design/DECISIONS.md).
_THESIS_EXPLAINER = (
    "A Research Thesis connects multiple filings and news items into an evidence-backed "
    "bottleneck, demand shift, or second-order effect Eeva is tracking across companies."
)
_LIVE_EVIDENCE_EXPLAINER = (
    "Recent official filings and news items tagged to this thesis's companies — shown for context, "
    "not analyzed or scored. Distinct from the curated Evidence ledger above, which an analyst has "
    "reviewed and tagged Supports/Contradicts/Mixed."
)
_LIVE_EVIDENCE_EMPTY = "No recent evidence yet."
_LIVE_EVIDENCE_LIMIT = 5

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
    escaped text instead of a link. EDINET-safety fix (design/
    DECISIONS.md): also passed through public_source_url(), so a raw,
    key-required EDINET API URL is rewritten to the public disclosure
    portal root rather than ever rendered as a clickable link that would
    return an HTTP 401 to a reader — every other source's URL is
    returned unchanged."""
    if not isinstance(url, str):
        return None
    stripped = url.strip()
    lowered = stripped.lower()
    if lowered.startswith("https://") or lowered.startswith("http://"):
        return public_source_url(stripped)
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
        with render_timing.data_load():
            repository = backend_factory.get_theme_repository(settings)
    except Exception:  # noqa: BLE001 — fail closed; never leak a raw connection/config error into the UI
        st.markdown(f'<div class="er-page-title">{_esc(_PAGE_TITLE)}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="er-muted">{_esc(_UNAVAILABLE_MESSAGE)}</div>', unsafe_allow_html=True)
        return

    if theme_id:
        _render_detail(repository, theme_id)
    else:
        _render_index(repository)


_CATEGORY_FILTER_KEY = "theses-category-filter"
_ALL_CATEGORIES_LABEL = "All"
_READING_A_THESIS: tuple[tuple[str, str, str], ...] = (
    ("supports", "Supports", "Primary-source evidence consistent with the thesis."),
    ("mixed", "Mixed", "Cuts both ways; needs a reviewer's call."),
    ("contradicts", "Contradicts", "Evidence against the thesis. Always shown, never collapsed."),
    ("context", "Context", "Frames the question without deciding it."),
)


def _category_counts(themes) -> dict[str, int]:
    counts: dict[str, int] = {c.value: 0 for c in ThemeCategory}
    for theme in themes:
        counts[_enum_label(theme.category)] = counts.get(_enum_label(theme.category), 0) + 1
    return counts


def _render_category_filter(themes) -> str | None:
    """A real-count category filter over the published list (redesign v2)
    — display filtering of already-published theses only; the repository
    read and every thesis's own data are untouched. Returns the selected
    category label, or None for All."""
    counts = _category_counts(themes)
    options = [f"{_ALL_CATEGORIES_LABEL} {len(themes)}"] + [
        f"{label} {n}" if n else label for label, n in counts.items()
    ]
    choice = st.segmented_control(
        "Thesis type", options=options, default=options[0], key=_CATEGORY_FILTER_KEY, label_visibility="collapsed",
    )
    if not choice or choice == options[0]:
        return None
    return choice.rsplit(" ", 1)[0] if choice[-1].isdigit() else choice


def _render_index(repository: ThemeRepositoryProtocol) -> None:
    page_header(_PAGE_TITLE, _THESIS_EXPLAINER)
    _scope_statement()

    try:
        themes = repository.list_published_themes()
    except Exception:  # noqa: BLE001 — fail closed; never leak a raw connection/config error into the UI
        st.markdown(f'<div class="er-muted">{_esc(_UNAVAILABLE_MESSAGE)}</div>', unsafe_allow_html=True)
        return

    if not themes:
        empty_state(_EMPTY_STATE_TITLE, _EMPTY_STATE_DETAIL, key="themes-empty")
        return

    selected = _render_category_filter(themes)
    shown = [t for t in themes if selected is None or _enum_label(t.category) == selected]
    # Recently updated first — a real field (ResearchTheme.updated_at), the
    # only sort the page offers.
    shown = sorted(shown, key=lambda t: t.updated_at, reverse=True)

    detail_page = get_page("themes")
    tickers = _ticker_by_company_name()
    main_col, side_col = st.columns([2.2, 1], gap="medium")
    with main_col:
        if not shown:
            st.markdown(f'<div class="er-muted">No {_esc(selected)} theses are published yet.</div>', unsafe_allow_html=True)
        for theme in shown:
            try:
                evidence = repository.evidence_for_theme(theme.id)
                company_map = repository.company_map_for_theme(theme.id)
            except Exception:  # noqa: BLE001 — one theme's count lookup failing must not take down the whole index
                evidence, company_map = (), ()
            _render_card(theme, evidence, company_map, detail_page, tickers)
    with side_col:
        _render_reading_legend()


def _render_reading_legend() -> None:
    rows = "".join(
        f'<div class="er-legend-row"><span class="er-evdot er-ev-{slug}"></span>'
        f'<div><div class="er-legend-name">{_esc(name)}</div><div class="er-muted">{_esc(text)}</div></div></div>'
        for slug, name, text in _READING_A_THESIS
    )
    with st.container(key="card-thesis-legend"):
        st.markdown(f'<div class="er-section-label er-tight">Reading a thesis</div><div class="er-legend">{rows}</div>', unsafe_allow_html=True)


_DIRECTION_TAG_CLASS = {"Supports": "er-tag-ev-supports", "Contradicts": "er-tag-ev-contradicts", "Mixed": "er-tag-ev-mixed"}


def _evidence_direction_chips_html(evidence) -> str:
    counts = Counter(_esc(getattr(item.direction, "value", item.direction)) for item in evidence)
    if not counts:
        return '<span class="er-muted">No evidence recorded yet.</span>'
    return "".join(
        f'<span class="er-status-tag {_DIRECTION_TAG_CLASS.get(direction, "er-tag-neutral")}" '
        f'style="margin-right:0.35rem;">{direction} · {count}</span>'
        for direction, count in counts.items()
    )


def _ticker_by_company_name() -> dict[str, str]:
    """Exact TrackedCompany.name -> public securities code/ticker (EDINET's
    5-char code shown as its 4-digit public form). A mapped company that
    is not a tracked company gets no code — never a guessed one."""
    from src.logic import filing_display

    codes: dict[str, str] = {}
    for company in get_tracked_companies():
        code = company.krx_code or ""
        if company.source == filing_display.EDINET_SOURCE_NAME:
            code = filing_display.edinet_display_securities_code(code) or code
        if code:
            codes[company.name] = code
    return codes


def _theme_tags_html(company_map) -> str:
    """Primary theme tags row (design/DECISIONS.md, "Research Theses as
    a real research surface") — derived purely from TrackedCompany.
    themes for whichever of this thesis's mapped companies are real
    tracked companies (see theme_evidence.theme_tags_for_companies's own
    docstring: an exact company_name match, never a fuzzy lookup). Empty
    string (render nothing) when no mapped company is a tracked company
    or the company map itself is empty — never a placeholder tag."""
    company_names = frozenset(entry.company_name for entry in company_map)
    tags = theme_evidence.theme_tags_for_companies(company_names, get_tracked_companies())
    if not tags:
        return ""
    return "".join(
        f'<span class="er-status-tag er-tag-neutral" style="margin-right:0.3rem;">{_esc(tag)}</span>' for tag in tags
    )


def _company_chips_html(company_map, tickers: dict[str, str]) -> str:
    names = sorted({entry.company_name for entry in company_map})
    chips = []
    for name in names:
        code = tickers.get(name)
        code_html = f' <span class="er-mono er-mono-muted">{_esc(code)}</span>' if code else ""
        chips.append(f'<span class="er-status-tag er-tag-theme">{_esc(name)}{code_html}</span>')
    return "".join(chips)


def _render_card(theme: ResearchTheme, evidence, company_map, detail_page, tickers: dict[str, str] | None = None) -> None:
    """Redesign v2 thesis card: category + real status chips (ThemeStatus
    is an authored field — "New" appears only when the status really is
    NEW), the updated timestamp, a serif title, the working thesis, the
    key question in a serif inset, an evidence bar with all four real
    direction counts (Contradicts always rendered, never collapsed), the
    mapped companies with their tracked-company codes, and the existing
    Open link. Same container key prefix (card-theme-) as before."""
    tickers = tickers or {}
    distinct_companies = {item.company for item in evidence} | {entry.company_name for entry in company_map}
    counts = Counter(getattr(item.direction, "value", item.direction) for item in evidence)
    bar_counts = {label: counts.get(label, 0) for label, _ in EVIDENCE_DIRECTIONS}
    with st.container(key=f"card-theme-{theme.id}"):
        status_variant = "info" if theme.status == ThemeStatus.NEW else "neutral"
        st.markdown(
            '<div class="er-split-head">'
            f'<div>{chip_html(_enum_label(theme.category), "info")} {chip_html(_enum_label(theme.status), status_variant)}</div>'
            f'<div class="er-split-meta">Updated {_esc(fmt_datetime_local(theme.updated_at))}</div></div>',
            unsafe_allow_html=True,
        )
        st.markdown(f'<div class="er-card-title er-serif-title">{_esc(theme.title)}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="er-thesis-summary">{_esc(theme.working_thesis)}</div>', unsafe_allow_html=True)
        theme_tags = _theme_tags_html(company_map)
        if theme_tags:
            st.markdown(f'<div style="margin-top:0.4rem;">{theme_tags}</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="er-inset" style="margin-top:0.8rem;"><div class="er-inset-label">Key question</div>'
            f'<div class="er-serif-question">{_esc(theme.key_question)}</div></div>',
            unsafe_allow_html=True,
        )
        ev_col, co_col = st.columns([1.1, 1], gap="medium")
        with ev_col:
            st.markdown(
                f'<div class="er-split-head" style="margin:0.9rem 0 0.4rem 0;"><div class="er-inset-label" style="margin:0;">Evidence</div>'
                f'<div class="er-mono">{len(evidence)} items</div></div>{evidence_bar_html(bar_counts)}',
                unsafe_allow_html=True,
            )
        with co_col:
            st.markdown(
                f'<div class="er-split-head" style="margin:0.9rem 0 0.4rem 0;"><div class="er-inset-label" style="margin:0;">Companies</div>'
                f'<div class="er-mono">{len(distinct_companies)}</div></div>'
                f'<div class="er-chip-row">{_company_chips_html(company_map, tickers)}</div>',
                unsafe_allow_html=True,
            )
        foot_cols = st.columns([3, 1.2], vertical_alignment="center")
        with foot_cols[0]:
            review = bar_counts["Mixed"] + bar_counts["Contradicts"]
            if review:
                st.markdown(
                    f'<div class="er-muted"><span class="er-evdot er-ev-mixed"></span> {review} item{"s" if review != 1 else ""} '
                    "need review (mixed or contradicting)</div>",
                    unsafe_allow_html=True,
                )
        with foot_cols[1]:
            if detail_page is not None:
                with st.container(key=f"cta-primary-open-theme-{theme.id}"):
                    st.page_link(detail_page, label="Open thesis →", query_params={"theme_id": theme.id})


def _render_detail(repository: ThemeRepositoryProtocol, theme_id: str) -> None:
    list_page = get_page("themes")
    if list_page is not None:
        st.page_link(list_page, label="← All Research Theses")

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
        empty_state(
            "Research Thesis not found.", "This research thesis does not exist, or is no longer available.",
            key="theme-not-found",
        )
        return

    # Identity safety: only ever render a record whose own theme_id
    # exactly matches the selected theme — a mismatched record from a
    # defective backend/map is silently dropped, never displayed.
    evidence_items = tuple(item for item in evidence_items if item.theme_id == theme.id)
    company_map = tuple(entry for entry in company_map if entry.theme_id == theme.id)

    # 1. Title, category, status, last updated
    st.markdown(f'<div class="er-page-title er-serif-title" style="font-size:var(--fs-page-title);">{_esc(theme.title)}</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="er-muted">{_enum_label(theme.category)} · {_enum_label(theme.status)} · '
        f'Updated {_esc(fmt_datetime_local(theme.updated_at))}</div>',
        unsafe_allow_html=True,
    )
    _scope_statement()

    # 2. The question
    section_header("The question")
    st.markdown(f'<div>{_esc(theme.key_question)}</div>', unsafe_allow_html=True)

    # 2a. What Eeva tested — optional (design/DECISIONS.md): the
    # original hypothesis and what primary-source review found, distinct
    # from the (possibly revised) working thesis below. Omitted entirely
    # when absent, exactly like an empty company-map role group, so a
    # Theme authored before this field existed renders identically to
    # before.
    if theme.what_eeva_tested:
        section_header("What Eeva tested")
        st.markdown(f'<div>{_esc(theme.what_eeva_tested)}</div>', unsafe_allow_html=True)

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

    # 6a. Live evidence — recent Radar filings + Daily News items
    section_header("Live evidence")
    st.markdown(f'<div class="er-muted">{_esc(_LIVE_EVIDENCE_EXPLAINER)}</div>', unsafe_allow_html=True)
    _render_live_evidence(company_map)

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
            jurisdiction = jurisdiction_for_source(item.source_name)
            source_label = f"{item.source_name} · {jurisdiction}" if jurisdiction else item.source_name
            st.markdown(f'<div class="er-muted">{_esc(source_label)}</div>', unsafe_allow_html=True)
        with cols[3]:
            st.markdown(f'<div class="er-muted" style="text-align:right;">{_enum_label(item.direction)}</div>', unsafe_allow_html=True)

        st.markdown(f'<div style="margin-top:0.2rem;"><strong>Observed:</strong> {_esc(item.fact)}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="er-muted" style="margin-top:0.15rem;"><strong>Relevance:</strong> {_esc(item.relevance)}</div>', unsafe_allow_html=True)

        safe_url = _safe_source_url(item.source_url)
        if safe_url:
            # EDINET-safety fix (design/DECISIONS.md): the displayed link
            # text must match `safe_url`, not the raw stored `item.
            # source_url` — otherwise an EDINET link would show the
            # original api.edinet-fsa.go.jp text while actually pointing
            # at the rewritten public portal URL.
            st.markdown(
                f'<div style="margin-top:0.2rem;"><a href="{html.escape(safe_url, quote=True)}" '
                f'target="_blank" rel="noopener noreferrer">{_esc(safe_url)}</a></div>',
                unsafe_allow_html=True,
            )
        elif item.source_url:
            st.markdown(f'<div class="er-muted" style="margin-top:0.2rem;">{_esc(item.source_url)}</div>', unsafe_allow_html=True)


def _load_recent_filing_events() -> tuple:
    """Reads via the same backend_factory seam this page's own
    get_theme_repository() call already uses — one call per Radar
    source, never a new query/matching system. A per-source read
    failure (misconfigured/unreachable backend) degrades that source to
    empty rather than taking down the whole Live evidence section,
    mirroring this app's existing per-source failure-isolation
    discipline (see radar_inbox.py's own _load_source_items)."""
    settings = get_settings()
    filings: list = []
    for source in ("OpenDART / DART", "SEC EDGAR", "EDINET"):
        try:
            filings.extend(backend_factory.get_filing_event_repository(settings, source).load_filing_events())
        except Exception:  # noqa: BLE001 — fail closed per source
            continue
    return tuple(filings)


def _load_recent_news_items() -> tuple:
    """Same seam as _load_recent_filing_events above, for Daily News's
    two existing repositories (issuer-matched NewsStory, issuer-agnostic
    EditorialStory) — read-only, no new query system."""
    settings = get_settings()
    news_stories: tuple = ()
    editorial_stories: tuple = ()
    try:
        news_stories = tuple(daily_news_backend.get_daily_news_repository(settings).load_stories().values())
    except Exception:  # noqa: BLE001 — fail closed
        pass
    try:
        editorial_stories = tuple(daily_news_backend.get_editorial_story_repository(settings).load_stories().values())
    except Exception:  # noqa: BLE001 — fail closed
        pass
    return news_stories, editorial_stories


def _render_live_evidence(company_map) -> None:
    company_names = frozenset(entry.company_name for entry in company_map)
    if not company_names:
        st.caption(_LIVE_EVIDENCE_EMPTY)
        return

    filings = _load_recent_filing_events()
    news_stories, editorial_stories = _load_recent_news_items()
    filing_links = theme_evidence.recent_filing_evidence(filings, company_names, _LIVE_EVIDENCE_LIMIT)
    news_links = theme_evidence.recent_daily_news_evidence(news_stories, editorial_stories, company_names, _LIVE_EVIDENCE_LIMIT)

    if not filing_links and not news_links:
        st.caption(_LIVE_EVIDENCE_EMPTY)
        return

    for link in filing_links:
        _render_evidence_link(link)
    for link in news_links:
        _render_evidence_link(link)


def _render_evidence_link(link) -> None:
    safe_url = _safe_source_url(link.url)
    st.markdown(
        '<div style="margin-top:0.3rem; display:flex; align-items:baseline; gap:0.4rem;">'
        f'<span class="er-status-tag er-tag-neutral">{_esc(link.kind)}</span>'
        f'<span class="er-muted">{_esc(link.company)} · {_esc(link.date)}</span>'
        "</div>",
        unsafe_allow_html=True,
    )
    if safe_url:
        st.markdown(
            f'<div><a href="{html.escape(safe_url, quote=True)}" target="_blank" '
            f'rel="noopener noreferrer">{_esc(link.title)}</a></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(f'<div>{_esc(link.title)}</div>', unsafe_allow_html=True)
