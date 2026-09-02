"""Daily News admin/status view — hidden (not linked in the sidebar,
same `visibility="hidden"` pattern app.py already uses for `company`/
`disclaimer`), reachable by direct URL only, for controlled pilot
verification. Runs one manual discovery pass on demand and shows the
full DailyNewsScanReport, including per-item suppression reasons and
sanitized per-source failures — the detail the public page in
daily_news.py deliberately never shows.
"""
from __future__ import annotations

import streamlit as st

from src.config.settings import get_settings
from src.data_access.daily_news import daily_news_pipeline
from src.ui.components.section import section_header


def render() -> None:
    st.markdown('<div class="er-page-title">Daily News — Admin / Status</div>', unsafe_allow_html=True)

    settings = get_settings()
    if not settings.daily_news_admin_enabled:
        st.info("Daily News — Admin is not enabled on this deployment.")
        return

    st.markdown(
        '<div class="er-muted">Internal ingestion status — not linked from the public sidebar.</div>',
        unsafe_allow_html=True,
    )

    if st.button("Run discovery now"):
        with st.spinner("Polling pilot feeds..."):
            report = daily_news_pipeline.run_discovery(settings.cache_dir)
        st.session_state["_daily_news_last_report"] = report

    report = st.session_state.get("_daily_news_last_report")
    if report is None:
        st.write("No discovery run yet this session. Click “Run discovery now”.")
        return

    section_header("Last run")
    st.write(f"Scan ID: `{report.scan_id}`")
    st.write(f"Started: {report.started_at}  ·  Completed: {report.completed_at}")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Sources polled", report.sources_polled)
    col2.metric("Items discovered", report.items_discovered)
    col3.metric("Stories published", report.stories_published)
    col4.metric("Deduplicated", report.items_deduplicated)

    if report.source_failures:
        section_header("Source failures (sanitized)")
        for company_name, failure_code in report.source_failures.items():
            st.write(f"- **{company_name}**: `{failure_code}`")

    if report.warnings:
        section_header("Warnings")
        for warning in report.warnings:
            st.write(f"- {warning}")

    if report.suppressed_items:
        section_header("Suppressed items")
        for company_name, title, reason in report.suppressed_items:
            st.write(f"- **[{company_name}]** {title} — _{reason}_")
