"""Research — conversational research interface. Renamed from
research_chat.py (brief §4). Phase 1 uses only canned demo answers
(src/data_access/demo/research_answer_provider.py); there is no LLM call
here, per the foundation-phase scope. Conversation state lives in
st.session_state only — nothing persists across a reload.

Answers with a `claims` breakdown render via the evidence spine (brief §7);
older-shape answers fall back to the plain five-section layout.
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from src.data_access.container import get_repositories
from src.logic.formatting import fmt_date
from src.models.models import ChatAnswer
from src.ui.components.badges import claim_type_badge, demo_badge
from src.ui.components.empty_state import empty_state
from src.ui.components.evidence_spine import evidence_spine_row
from src.ui.components.section import section_header

SESSION_KEY = "chat_messages"
FOCUS_COMPOSER_KEY = "_focus_composer_pending"

# Category mapped from each suggested question's actual content — brief
# wants a Company/Theme/Compare/Catalyst/Risk-check taxonomy, but only
# categories with a real matching demo question render (no invented
# sample content just to fill an empty bucket).
_QUESTION_CATEGORY = {
    "What is happening in AI networking and photonics?": "Theme",
    "Compare three optical interconnect suppliers.": "Compare",
    "What are the main catalysts for the memory cycle?": "Catalyst",
    "Which parts of the humanoid supply chain would benefit from volume scaling?": "Theme",
    "Where is capital rotating across the five themes?": "Theme",
}
_CATEGORY_ORDER = ["Company", "Theme", "Compare", "Catalyst", "Risk check"]
_LOGO_PATH = Path(__file__).resolve().parents[3] / "assets" / "eeva-logo.png"
# Streamlit's default "user" chat avatar renders as a colored icon, which
# the zero-accent-colour rule (brief §5) rules out — a plain monochrome
# glyph in a data URI stands in for it instead.
_USER_AVATAR = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E"
    "%3Ccircle cx='12' cy='12' r='12' fill='%232A2A2A'/%3E"
    "%3Ccircle cx='12' cy='9.5' r='3.6' fill='none' stroke='%23B4B4B4' stroke-width='1.4'/%3E"
    "%3Cpath d='M5 19c1.2-3.2 4-4.8 7-4.8s5.8 1.6 7 4.8' fill='none' stroke='%23B4B4B4' stroke-width='1.4'/%3E"
    "%3C/svg%3E"
)


def _focus_composer() -> None:
    st.session_state[FOCUS_COMPOSER_KEY] = True


def _render_answer_card(answer: ChatAnswer) -> None:
    with st.container(border=True, key=f"card-answer-{abs(hash(answer.question))}"):
        top = st.columns([4, 1])
        with top[0]:
            st.markdown(f"**{answer.question}**")
        with top[1]:
            demo_badge("Sample")

        badge_row = st.columns([1, 1, 4])
        with badge_row[0]:
            claim_type_badge(answer.claim_type, has_source=bool(answer.sources))
        with badge_row[1]:
            st.badge(f"Confidence: {answer.confidence.value}", color="gray")
        with badge_row[2]:
            st.markdown(f'<div class="er-muted">Freshness: {answer.freshness}</div>', unsafe_allow_html=True)

        if answer.claims:
            # Evidence spine (brief §7) — each claim gets its own left-gutter
            # bar segment; segments must abut exactly to read as one
            # continuous line, so is_first/is_last are set precisely on the
            # first/last row rather than every row getting its own margin.
            for i, claim in enumerate(answer.claims):
                evidence = claim.evidence[0] if claim.evidence else None
                evidence_spine_row(
                    claim.text,
                    claim.claim_type,
                    has_source=bool(evidence and evidence.source_name),
                    excerpt=evidence.excerpt if evidence else None,
                    excerpt_original=evidence.excerpt_original if evidence else None,
                    source_label=(f"{evidence.source_name} · {evidence.source_type}" if evidence else None),
                    is_first=(i == 0),
                    is_last=(i == len(answer.claims) - 1),
                )
        else:
            st.markdown(f"**What happened**\n\n{answer.what_happened}")
            st.markdown(f"**Why it matters**\n\n{answer.why_it_matters}")
            st.markdown(f"**What may be underappreciated**\n\n{answer.underappreciated}")
            st.markdown(f"**Risks / thesis breakers**\n\n{answer.risks}")
            st.markdown(f"**What to watch next**\n\n{answer.what_to_watch}")

        if answer.sources:
            with st.expander(f"Sources ({len(answer.sources)})"):
                for s in answer.sources:
                    st.markdown(f"- {s.source_name} (no external source — demo data), retrieved {fmt_date(s.retrieved_at)}")
        else:
            st.caption("No sources attached — sample answer.")


def render() -> None:
    ctx = get_repositories()
    st.markdown('<div class="er-page-title">Research</div>', unsafe_allow_html=True)
    st.write("Ask a research question and get a structured, evidence-labeled answer.")

    suggested = ctx.research_answer_provider.get_suggested_questions()
    if suggested:
        section_header("Suggested research questions")
        by_category: dict[str, list[str]] = {}
        for q in suggested:
            by_category.setdefault(_QUESTION_CATEGORY.get(q, "Theme"), []).append(q)
        for category in _CATEGORY_ORDER:
            items = by_category.get(category)
            if not items:
                continue
            st.markdown(f'<div class="er-muted" style="font-size:0.76rem; margin:0.3rem 0 0.2rem 0;">{category}</div>', unsafe_allow_html=True)
            cols = st.columns(len(items))
            for col, q in zip(cols, items):
                with col:
                    if st.button(q, key=f"suggested-{q}", width="stretch"):
                        st.session_state.setdefault(SESSION_KEY, []).append(q)

    st.divider()

    messages = st.session_state.setdefault(SESSION_KEY, [])
    if not messages:
        empty_state(
            "Start a research thread",
            "Ask about a company, theme, filing, catalyst, or market move.",
            action_label="New thread",
            on_click=_focus_composer,
            key="research-no-threads",
        )
    else:
        for q in messages:
            # Streamlit's default "assistant" avatar renders as a colored
            # icon, which the zero-accent-colour rule (brief §5) rules out
            # — use the real brand mark instead. "user" defaults to a
            # neutral outline, left as-is.
            with st.chat_message("user", avatar=_USER_AVATAR):
                st.write(q)
            with st.chat_message("assistant", avatar=str(_LOGO_PATH) if _LOGO_PATH.exists() else None):
                _render_answer_card(ctx.research_answer_provider.get_answer(q))

    if st.session_state.pop(FOCUS_COMPOSER_KEY, False):
        st.iframe(
            "<script>var el = window.parent.document.querySelector('textarea[data-testid=\"stChatInputTextArea\"]'); "
            "if (el) { el.focus(); }</script>",
            height=1,
        )

    typed = st.chat_input("Ask a research question...")
    if typed:
        # chat_input is rendered after the messages read/render block above,
        # so the value appended here wouldn't show until a second rerun
        # without this explicit rerun — unlike the suggested-question
        # buttons above, which append before that same read happens.
        st.session_state.setdefault(SESSION_KEY, []).append(typed)
        st.rerun()

    # One concise, permanent, non-intrusive note — not a dismissible banner
    # (those train people to dismiss without reading), and not repeated
    # elsewhere on this page (UX-refinement pass: previously had both this
    # line AND a separate first-session dismissible note saying nearly the
    # same thing).
    st.markdown(
        '<div style="font-size:12px; color:var(--text-4); margin-top:0.5rem;">'
        "Sample-mode responses are illustrative. Verify material claims against primary sources.</div>",
        unsafe_allow_html=True,
    )
