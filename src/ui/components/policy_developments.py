"""Policy developments — Federal Register policy-monitor pilot (design/
DECISIONS.md). A self-contained, source-specific Dashboard section,
deliberately separate from Daily News: zero import of anything under
src.data_access.daily_news or src.models.daily_news_models, and never
called from src/ui/pages/daily_news.py. Read-time/in-memory only — one
live Federal Register fetch per render, nothing persisted, nothing
scheduled.

Renders nothing at all — no heading, no subtitle, no shell, no
placeholder, no empty-state card — unless at least one real Federal
Register document currently qualifies under
src.logic.policy_monitor.federal_register_matching's fail-closed rules,
matching the same convention src/ui/pages/dashboard.py's own
_render_priority_signals()/_render_theme_health() already use."""
from __future__ import annotations

import html

import streamlit as st

from src.data_access.policy_monitor.federal_register_client import fetch_candidate_documents
from src.logic.formatting import fmt_date
from src.logic.policy_monitor.federal_register_matching import qualifying_policy_developments
from src.ui.components.section import section_header

_CANDIDATE_FETCH_COUNT = 20


def _esc(value: object) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


def render_policy_developments() -> None:
    fetch_result = fetch_candidate_documents(per_page=_CANDIDATE_FETCH_COUNT)
    items = qualifying_policy_developments(fetch_result.documents)
    if not items:
        return

    section_header("Policy developments", "Official government actions matched to tracked research themes")

    for item in items:
        with st.container(border=True, key=f"card-policy-development-{item.document_number}"):
            st.markdown(
                f'<div class="er-muted">Federal Register · Government / policy · '
                f'{_esc(fmt_date(item.publication_date))}</div>',
                unsafe_allow_html=True,
            )
            st.markdown(f'<div class="er-card-title">{_esc(item.title)}</div>', unsafe_allow_html=True)
            st.markdown(
                f'<div class="er-muted">{_esc(item.agency_name)} · {_esc(item.type_label)}</div>',
                unsafe_allow_html=True,
            )
            st.markdown(f'<div class="er-muted">{_esc(item.reason)}</div>', unsafe_allow_html=True)
            with st.container(key=f"cta-tertiary-policy-development-{item.document_number}"):
                st.link_button("Open official document →", item.html_url, use_container_width=True)
