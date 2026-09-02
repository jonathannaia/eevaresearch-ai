"""Signal drawer (brief §11) — opens over the current view without
navigating away. Implemented with st.dialog (Streamlit's native modal
overlay) rather than a hand-rolled HTML/JS slide-over: it's a real
Streamlit primitive that satisfies "opens over the feed, doesn't navigate
away, closes via Esc/X, focus behaves" without extra JS plumbing — see
design/DECISIONS.md. Renders all eight spec fields; marks the signal read
on open (not on hover, per §10).
"""
from __future__ import annotations

import streamlit as st

from src.logic.formatting import fmt_date
from src.models.models import ClaimType, Signal
from src.ui.components.badges import direction_dot_html
from src.ui.components.evidence_chips import evidence_chip
from src.ui.components.excerpts import render_excerpt
from src.ui.ui import READ_IDS_KEY


def open_signal_drawer(signal: Signal, evidence_repository=None) -> None:
    read_ids = st.session_state.setdefault(READ_IDS_KEY, set())
    read_ids.add(signal.id)

    @st.dialog(signal.title, width="large")
    def _drawer() -> None:
        tag = signal.theme_slug + (f" / {signal.subtheme_slug}" if signal.subtheme_slug else "")
        if signal.is_demo:
            source_label = "EevaResearch Demo Data"
        else:
            identity = f"{signal.issuer} · {signal.exchange_symbol}" if signal.exchange_symbol else signal.issuer
            source_label = f"{identity} · {signal.source_name}"
        st.markdown(
            f'<div class="er-mono er-muted">{source_label} · {fmt_date(signal.last_updated)} · {tag}</div>',
            unsafe_allow_html=True,
        )

        meta_cols = st.columns(3)
        with meta_cols[0]:
            st.markdown('<div class="er-metric-label">Direction</div>', unsafe_allow_html=True)
            st.markdown(direction_dot_html(signal.direction), unsafe_allow_html=True)
        with meta_cols[1]:
            st.markdown('<div class="er-metric-label">Strength</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="er-mono">{signal.strength.value}</div>', unsafe_allow_html=True)
        with meta_cols[2]:
            st.markdown('<div class="er-metric-label">Horizon</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="er-mono">{signal.horizon.value}</div>', unsafe_allow_html=True)

        st.markdown('<div class="er-section-label">What changed</div>', unsafe_allow_html=True)
        st.write(signal.interpretation)

        st.markdown('<div class="er-section-label">Evidence</div>', unsafe_allow_html=True)
        if signal.is_demo:
            evidence_items = []
            if evidence_repository is not None and signal.related_tickers:
                evidence_items = evidence_repository.get_evidence_for_ticker(signal.related_tickers[0])
            if evidence_items:
                evidence_chip(evidence_items[0].claim_type, has_source=bool(evidence_items[0].source_name))
                render_excerpt(evidence_items[0])
            else:
                st.markdown(
                    f'<div class="er-muted">{signal.evidence_count} sample evidence item(s) attributed to this signal '
                    '— no individual excerpt attached in this phase.</div>',
                    unsafe_allow_html=True,
                )
        elif signal.excerpt:
            # Real Radar promotion — both excerpt fields are copied
            # verbatim from the extracted filing (CandidateSignal.
            # excerpt_original / excerpt_translation), never generated or
            # translated here.
            evidence_chip(ClaimType.FACT, has_source=bool(signal.source_url))
            if signal.excerpt_translated:
                st.markdown(f'<div class="er-excerpt">{signal.excerpt_translated}</div>', unsafe_allow_html=True)
                st.markdown(
                    '<div class="er-muted" style="font-size:0.78rem; margin-top:-0.1rem;">'
                    'English — machine translation. Original remains the evidence.</div>',
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f'<div class="er-muted" style="font-size:0.78rem; margin-top:0.3rem;">Original ({signal.original_language}):</div>',
                    unsafe_allow_html=True,
                )
                st.markdown(f'<div class="er-excerpt">{signal.excerpt}</div>', unsafe_allow_html=True)
            else:
                st.markdown(f'<div class="er-excerpt">{signal.excerpt}</div>', unsafe_allow_html=True)
                if signal.translation_state and signal.translation_state != "Not requested":
                    st.markdown(
                        f'<div class="er-muted" style="font-size:0.78rem;">{signal.translation_state}.</div>',
                        unsafe_allow_html=True,
                    )
        else:
            st.markdown('<div class="er-muted">No excerpt available for this filing yet.</div>', unsafe_allow_html=True)

        st.markdown(
            f'<div class="er-section-label">Contrary evidence</div>'
            f'<div style="background:rgba(255,255,255,.025); padding:0.6rem 0.75rem; border-radius:var(--r-sm);">'
            f'{signal.contrary_evidence or "None recorded."}</div>',
            unsafe_allow_html=True,
        )

        st.markdown('<div class="er-section-label">Validates if</div>', unsafe_allow_html=True)
        st.write(signal.validation_criteria)

        st.markdown('<div class="er-section-label">Invalidates if</div>', unsafe_allow_html=True)
        st.write(signal.invalidation_criteria)

        st.divider()
        if signal.source_url:
            st.link_button("Open filing", signal.source_url, width="stretch")
        else:
            st.button(
                "Open filing", key=f"drawer-filing-{signal.id}", width="stretch", disabled=True,
                help="No source document link available for this signal.",
            )

    _drawer()
