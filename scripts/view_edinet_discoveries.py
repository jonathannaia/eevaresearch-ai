"""Standalone, read-only viewer for EDINET Phase 1 discovery records.
Invoke as:

    streamlit run scripts/view_edinet_discoveries.py

Deliberately NOT wired into app.py/ui.py's navigation — a fully separate
process from the main product. This script only reads
data/cache/edinet_discovered_candidates.json via
discovery_service.load_discoveries(); it never calls EDINET, never runs a
scan, never resolves a company name, and never writes to any cache — an
empty/missing file just renders an empty table.

"Discovered" here means: a filing from a company NOT in
tracked_companies.py, matched by EDINET's existing, unchanged rule
engine. No theme, no promotion to the tracked watchlist, no extraction —
see discovery_service.py's own docstring for the full Phase 1 scope."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from src.config.settings import get_settings
from src.data_access.edinet import discovery_service

st.set_page_config(page_title="EDINET Discoveries (Experimental)", layout="wide")

st.title("EDINET Discovered Candidates — Phase 1 (Experimental)")
st.caption(
    "Read-only. Filings from companies NOT in the tracked EDINET watchlist, matched by the "
    "existing deterministic rule engine. Not shown in Radar Inbox or Signals. No company here "
    "has been promoted to the tracked watchlist, assigned a theme, or had its document extracted."
)

cache_dir = get_settings().cache_dir
discoveries = discovery_service.load_discoveries(cache_dir)

if not discoveries:
    st.info(
        "No discovery records yet. This view never triggers a scan itself — a discovery scan "
        "must be run separately (a future, explicitly approved action)."
    )
else:
    confidence_options = sorted({d.confidence for d in discoveries})
    default_selection = [c for c in confidence_options if c in ("High", "Moderate")] or confidence_options
    selected = st.multiselect("Confidence", options=confidence_options, default=default_selection)

    rows = [
        {
            "Company": d.company_name,
            "EDINET Code": d.edinet_code,
            "Confidence": d.confidence,
            "Matched Category": d.matched_rule.split(":", 1)[0].replace("_", " ").title(),
            "Filing Title": d.doc_description,
            "Filing Date": d.filing_date,
            "Discovered At": d.discovered_at,
            "Source Link": d.source_url,
        }
        for d in discoveries
        if d.confidence in selected
    ]
    st.dataframe(
        rows,
        column_config={"Source Link": st.column_config.LinkColumn("Source Link", display_text="Open filing ↗")},
        use_container_width=True,
        hide_index=True,
    )
    st.caption(f"{len(rows)} of {len(discoveries)} discovery record(s) shown.")
