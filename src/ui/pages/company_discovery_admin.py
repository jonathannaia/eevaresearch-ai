"""Company Discovery Phase 2 — admin/status view. Hidden (not linked in
the sidebar, same `visibility="hidden"` pattern app.py already uses for
`daily_news_admin`/`disclaimer`), reachable by direct URL only, gated a
second way by `settings.company_discovery_admin_enabled` (default
disabled). Strictly read-only: no button, form, callback, or any other
state-changing control of any kind — this page never triggers a
discovery tick, never edits a candidate, and never promotes anything.
There is no promotion action to expose in Phase 2 regardless.

Never imported by any public page, and never imports from any public
page — see tests/test_company_discovery_scope_guard.py."""
from __future__ import annotations

import dataclasses

import streamlit as st

from src.config.settings import get_settings
from src.data_access.company_discovery.company_discovery_backend import get_candidate_issuer_repository
from src.data_access.state_db.candidate_issuer_repository import WORKER_STATUS_KEY
from src.ui.components.section import section_header


def render() -> None:
    st.markdown('<div class="er-page-title">Company Discovery — Admin / Status</div>', unsafe_allow_html=True)

    settings = get_settings()
    if not settings.company_discovery_admin_enabled:
        st.info("Company Discovery — Admin is not enabled on this deployment.")
        return

    st.markdown(
        '<div class="er-muted">Internal Candidate Ledger — read-only, not linked from the public sidebar. '
        "No promotion action exists in Phase 2.</div>",
        unsafe_allow_html=True,
    )

    backend = (settings.company_discovery_worker_db_backend or "").strip().lower()
    if backend not in ("sqlite", "postgres"):
        st.info("EDGE_COMPANY_DISCOVERY_WORKER_DB_BACKEND is not configured — nothing to show yet.")
        return

    # get_candidate_issuer_repository() reads settings.db_backend/
    # state_db_url/state_db_path (the same generic fields backend_
    # factory.py's own get_X_repository functions read) — never the
    # dashboard's own ambient EDGE_DB_BACKEND/EDGE_STATE_DB_URL pair.
    # Same "build an explicit settings view from the worker's own
    # dedicated fields" pattern scripts/company_discovery_worker.py's
    # own _build_worker_settings() already uses.
    ledger_settings = dataclasses.replace(
        settings, db_backend=backend,
        state_db_url=settings.company_discovery_worker_state_db_url,
        state_db_path=settings.company_discovery_worker_state_db_path,
    )
    try:
        repository = get_candidate_issuer_repository(ledger_settings)
    except Exception as exc:  # noqa: BLE001 — never leak a raw connection/config error into the UI
        st.info(f"Could not read the Candidate Ledger ({type(exc).__name__}).")
        return

    section_header("Worker status")
    worker_status = repository.get_worker_status()
    if worker_status is None:
        st.write("No worker tick recorded yet.")
    else:
        st.write(f"Worker key: `{WORKER_STATUS_KEY}`")
        st.write(f"Last tick started: {worker_status.last_tick_started_at or '—'}")
        st.write(f"Last tick completed: {worker_status.last_tick_completed_at or '—'}")
        st.write(f"Last failure: {worker_status.last_failure_code or 'none'}")
        st.write(
            f"Last run: evidence_created={worker_status.evidence_created_last_run} "
            f"candidates_created={worker_status.candidates_created_last_run} "
            f"candidates_quarantined={worker_status.candidates_quarantined_last_run}"
        )

    for state, heading in (
        ("Discovered", "Candidates"),
        ("Quarantined", "Quarantine queue"),
        ("Rejected", "Rejected"),
        ("Archived", "Archived"),
    ):
        section_header(heading)
        records = repository.list_candidates(coverage_state=state)
        if not records:
            st.write("None.")
            continue
        for record in records:
            with st.container(border=True, key=f"candidate-{record.issuer.issuer_id}"):
                st.markdown(f"**{record.issuer.legal_name}**")
                st.write(
                    f"issuer_id: `{record.issuer.issuer_id}` · entity_kind: {record.issuer.entity_kind} · "
                    f"resolution_confidence: {record.resolution_confidence.value} · "
                    f"composite_score: {record.composite_score:.3f}"
                )
                st.write(f"discovered_via: {record.issuer.discovered_via}")
                st.write(f"first_evidence_at: {record.first_evidence_at} · last_evidence_at: {record.last_evidence_at}")
                with st.expander("Evidence"):
                    for row in repository.get_evidence_for_issuer(record.issuer.issuer_id):
                        st.write(
                            f"- **{row['relationship_type']}** via `{row['matched_pattern_category']}` "
                            f"({row['source_type']}, {row['source_name']})"
                        )
                        st.caption(row["source_snippet"])
                        st.write(f"[source]({row['source_url']})")
