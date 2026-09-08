#!/usr/bin/env bash
# Render Web Service startup entrypoint (Durable-State Phase 4M-4).
#
# Render has no TOML-secrets mechanism of its own — this script bridges
# Render's plain environment variables into .streamlit/secrets.toml (via
# scripts/render_generate_secrets.py) before Streamlit itself starts,
# since app.py accesses st.user on every page load and Streamlit raises
# StreamlitAuthError from that access alone whenever [auth] isn't
# configured. Never touches Streamlit Community Cloud's own deployment
# (which uses its own Secrets editor and never runs this file at all),
# the worker (scripts/radar_worker.py — a completely separate
# entrypoint/process, unaffected by anything here), or the database.
#
# Render's own "Start Command" setting for this Web Service should be:
#   bash scripts/render_start.sh
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python3 "${script_dir}/render_generate_secrets.py"

exec streamlit run app.py --server.address 0.0.0.0 --server.port "${PORT:-8501}"
