"""Research — conversational research interface. Renamed from
research_chat.py (brief §4). Phase 1 uses only canned demo answers
(src/data_access/demo/research_answer_provider.py); there is no LLM call
here, per the foundation-phase scope. Conversation state lives in
st.session_state only — nothing persists across a reload.

Answers with a `claims` breakdown render via the evidence spine (brief §7);
older-shape answers fall back to the plain five-section layout.

Phase C (editorial-simplicity pass, design/DECISIONS.md): the question
input is now a plain st.text_input + primary "Ask" button pinned near the
top of the page, not st.chat_input — Streamlit docks chat_input to the
bottom of its container unconditionally, which made it impossible to
satisfy this phase's product rule ("the question input is the obvious
first interaction, placed directly under the prompt"). The same top
input/button is used for every question, first or follow-up, so it's the
one way to ask something — the old "Start a research thread" empty state
and its separate "New thread" button are gone (they were two more paths to
the same action). Thread history/rendering and the demo-answer lookup are
otherwise unchanged.
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from src.data_access.container import get_repositories
from src.logic.formatting import fmt_date
from src.models.models import ChatAnswer
from src.ui.components.badges import demo_badge
from src.ui.components.evidence_spine import evidence_spine_row

SESSION_KEY = "chat_messages"

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


def _render_answer_card(answer: ChatAnswer) -> None:
    with st.container(border=True, key=f"card-answer-{abs(hash(answer.question))}"):
        top = st.columns([4, 1])
        with top[0]:
            st.markdown(f"**{answer.question}**")
        with top[1]:
            demo_badge("Sample")

        # Phase C: evidence type + confidence + freshness collapse into one
        # quiet inline metadata line rather than three equal-weight pill
        # badges. The evidence-type *label* (Fact/Interpretation/Inference/
        # Uncertainty) is still shown, plainly, as text — only its previous
        # colored-chip rendering is gone at this answer-summary level.
        st.markdown(
            f'<div class="er-muted" style="font-size:0.78rem; margin:0.1rem 0 0.6rem 0;">'
            f'{answer.claim_type.value} · Confidence: {answer.confidence.value} · Freshness: {answer.freshness}</div>',
            unsafe_allow_html=True,
        )

        if answer.claims:
            # Evidence spine (brief §7) — each claim gets its own left-gutter
            # bar segment; segments must abut exactly to read as one
            # continuous line, so is_first/is_last are set precisely on the
            # first/last row rather than every row getting its own margin.
            # answer_claim_type (Phase C) suppresses the per-claim chip when
            # it just repeats the answer.claim_type already named above —
            # only a claim whose type differs (e.g. an Uncertainty aside in
            # an Interpretation answer) still shows its own chip.
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
                    answer_claim_type=answer.claim_type,
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

    # The one clear prompt + input + primary action (Phase C) — the
    # obvious first interaction on the page, and the one way to start or
    # continue a thread (typing a follow-up here works the same way).
    st.markdown(
        '<div class="er-section-label" style="color:var(--text); font-weight:600; '
        'font-size:0.92rem; margin-top:var(--space-4);">What would you like to investigate?</div>',
        unsafe_allow_html=True,
    )
    with st.form("research-ask-form", clear_on_submit=True, border=False):
        input_cols = st.columns([5, 1], vertical_alignment="bottom")
        with input_cols[0]:
            question = st.text_input(
                "Research question", key="research-question-input",
                placeholder="Ask about a company, theme, filing, catalyst, or market move.",
                label_visibility="collapsed",
            )
        with input_cols[1]:
            with st.container(key="cta-primary-research-ask"):
                asked = st.form_submit_button("Ask", width="stretch")
    if asked and question and question.strip():
        st.session_state.setdefault(SESSION_KEY, []).append(question.strip())
        st.rerun()

    # Suggested questions: subtle text links beneath the input (Phase C),
    # not full-width bordered buttons competing with it for attention —
    # same cta-tertiary-* ghost-link treatment used for every other
    # low-emphasis action in the app.
    suggested = ctx.research_answer_provider.get_suggested_questions()
    if suggested:
        by_category: dict[str, list[str]] = {}
        for q in suggested:
            by_category.setdefault(_QUESTION_CATEGORY.get(q, "Theme"), []).append(q)
        st.markdown(
            '<div class="er-muted" style="font-size:0.76rem; margin-top:var(--space-3);">Or try one of these:</div>',
            unsafe_allow_html=True,
        )
        for category in _CATEGORY_ORDER:
            items = by_category.get(category)
            if not items:
                continue
            st.markdown(f'<div class="er-muted" style="font-size:0.72rem; margin:0.3rem 0 0.1rem 0;">{category}</div>', unsafe_allow_html=True)
            cols = st.columns(len(items))
            for col, q in zip(cols, items):
                with col:
                    with st.container(key=f"cta-tertiary-suggested-{q}"):
                        if st.button(q, key=f"suggested-{q}"):
                            st.session_state.setdefault(SESSION_KEY, []).append(q)
                            st.rerun()

    messages = st.session_state.setdefault(SESSION_KEY, [])
    if messages:
        st.divider()
        for q in messages:
            # Streamlit's default "assistant" avatar renders as a colored
            # icon, which the zero-accent-colour rule (brief §5) rules out
            # — use the real brand mark instead. "user" defaults to a
            # neutral outline, left as-is.
            with st.chat_message("user", avatar=_USER_AVATAR):
                st.write(q)
            with st.chat_message("assistant", avatar=str(_LOGO_PATH) if _LOGO_PATH.exists() else None):
                _render_answer_card(ctx.research_answer_provider.get_answer(q))

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
