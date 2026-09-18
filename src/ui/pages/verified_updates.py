"""Verified Updates — the public surface for the Autonomous Research
Agent's AUTO_PUBLISHED records (design §9.2, §5.3). A narrow card, not a
Signal: headline, issuer, the direct reported fact, source label/date,
official document id + URL, exact excerpt or locator, validated prior
context, "What this does not establish", a visible VERIFIED label, and
evidence/audit attribution. No direction, strength, horizon, or
interpretation — none exist on the model, so none can be rendered.

Read-only by construction: this page reads verified_update_store only.
It never imports the agent package (enforced by
tests/test_mcp_agent_tool_scope_guard.py) and renders nothing with
unsafe_allow_html — every field is bounded plain text from a validated
record. Gated by settings.verified_updates_page_enabled (default off).
"""
from __future__ import annotations

import streamlit as st

from src.config.settings import get_settings
from src.data_access.verified_update_store import load_verified_updates
from src.models.verified_update import VerifiedUpdate

PAGE_TITLE = "Verified Updates"
NOT_ENABLED_TEXT = "Verified Updates is not enabled in this environment."
EMPTY_TEXT = "No verified updates have been published yet."
DOES_NOT_ESTABLISH_LABEL = "What this does not establish"


def _render_card(update: VerifiedUpdate) -> None:
    with st.container(border=True):
        st.caption(update.label)
        st.markdown(f"**{update.headline}**")
        st.write(update.fact_statement)
        st.caption(f"{update.issuer} · {update.source_label} · {update.source_date}")
        st.markdown(f"Official document: [{update.source_document_id}]({update.source_url})")
        st.caption(f"Excerpt / locator: {update.excerpt_or_locator}")
        if update.factual_context:
            st.caption("Validated prior context: " + " | ".join(update.factual_context))
        st.markdown(f"*{DOES_NOT_ESTABLISH_LABEL}:* {update.what_this_does_not_establish}")
        st.caption(
            f"Evidence {', '.join(update.evidence_ids)} · Published {update.published_at} by {update.published_by} · "
            f"Audit session {update.audit_session_id}"
        )


def render() -> None:
    settings = get_settings()
    st.subheader(PAGE_TITLE)
    st.caption("Direct reported facts from official filings and company announcements, each resolved to its evidence.")
    if not settings.verified_updates_page_enabled:
        st.info(NOT_ENABLED_TEXT)
        return
    updates = load_verified_updates(settings.cache_dir)
    if not updates:
        st.caption(EMPTY_TEXT)
        return
    for update in updates:
        _render_card(update)
