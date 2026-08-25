"""Durable-State Phase 4H-1 — private hosted-Signals preview launcher.

LOCAL/PRIVATE PREVIEW ONLY. This is NOT ordinary app startup — app.py is
completely untouched by this file and remains JSON-default exactly as
before; nothing here is imported by, or imports, app.py. This is NOT
production or public deployment infrastructure. This file's mere
existence is NOT an authorization to connect to Neon or any hosted
database — running it with a real preview DSN, and any later step
(starting it, binding a port, opening it in a browser, retaining real
scan results, publishing signals, deploying, or exposing anything
publicly) each requires its own separate, explicit approval.

Run (later, once separately approved) as:
    streamlit run scripts/hosted_signals_preview.py
Launch binding (host/port) is deliberately not decided here — see this
module's own _run_preview() docstring.

Reads exactly one environment variable, EEVA_HOSTED_SIGNALS_PREVIEW_DSN,
and nothing else — never EDGE_DB_BACKEND, never EDGE_STATE_DB_URL, never
get_settings(), never a Streamlit secrets file. Importing
src.config.settings does trigger that module's own pre-existing
load_dotenv(PROJECT_ROOT / ".env") call as an unavoidable side effect of
the import itself (identical to every other file in this codebase that
imports Settings, including the whole test suite) — this script does not
read .env itself, and python-dotenv's load_dotenv() never overrides a
variable already present in the process environment, so a preview DSN
the operator has already exported for this one invocation is never
altered by it.

On a missing DSN or a hosted-repository construction failure, this
script renders only the same static, non-leaky "Hosted signals are
temporarily unavailable" state signals.py itself renders for an injected
collaborator failure (see src/ui/pages/signals.py) — never an exception
message, traceback, hostname, DSN, username, or password, and never a
silent fallback to JSON/default app data. On success, it renders nothing
but the existing, unmodified signals.render() with an explicitly
injected hosted SignalRepository — no other page, no normal multi-page
routing, no container.get_repositories(), no source scanner, no review
action, and no publishing logic is imported or called anywhere in this
file.
"""
from __future__ import annotations

import os

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.interfaces import SignalRepository
from src.ui.components.empty_state import empty_state
from src.ui.pages import signals
from src.ui.ui import with_chrome

_PREVIEW_DSN_ENV_VAR = "EEVA_HOSTED_SIGNALS_PREVIEW_DSN"

_UNAVAILABLE_TITLE = "Hosted signals are temporarily unavailable."
_UNAVAILABLE_DETAIL = "Try again shortly, or view the standard signal feed."
_UNAVAILABLE_KEY = "signals-hosted-unavailable"


def _read_preview_dsn() -> str | None:
    """The only environment read in this entire script. Deliberately
    `os.environ.get` (never a default value baked in) so an unset
    variable is unambiguous. Never EDGE_DB_BACKEND, never
    EDGE_STATE_DB_URL, never get_settings()."""
    return os.environ.get(_PREVIEW_DSN_ENV_VAR) or None


def _build_hosted_signal_repository(dsn: str) -> SignalRepository:
    """Explicitly constructs Settings(db_backend="postgres", state_db_url=dsn)
    from the supplied DSN only, then calls the existing, unmodified
    backend_factory.get_signal_repository() — no new construction logic.
    Every other Settings field is pinned to an explicit, neutral value
    rather than left to its own ambient-environment default_factory, so
    this call reads no environment variable beyond the one already
    captured in `dsn` — matching this repo's existing hosted-validation
    script discipline (see design/DECISIONS.md, Durable-State Phase
    4D-3's hosted validation scripts)."""
    explicit_settings = Settings(
        db_backend="postgres",
        state_db_url=dsn,
        data_mode="demo",
        dart_api_key=None,
        translation_api_key=None,
        edgar_user_agent=None,
        edinet_subscription_key=None,
        edgar_discovery_enabled=False,
        state_db_path=None,
        private_beta_auth_enabled=False,
        private_beta_allowed_emails=frozenset(),
        remote_cache_enabled=False,
        r2_account_id=None,
        r2_access_key_id=None,
        r2_secret_access_key=None,
        r2_bucket=None,
        r2_endpoint=None,
    )
    return backend_factory.get_signal_repository(explicit_settings)


def _render_hosted_unavailable() -> None:
    """Static, non-leaky failure state — no exception text, traceback,
    hostname, DSN, username, or password is ever passed into this
    function, so none can reach the page. Mirrors signals.py's own
    injected-collaborator failure text/key exactly (Durable-State Phase
    4G-1) for a consistent message, without importing signals.py's
    render() itself for this branch."""
    empty_state(_UNAVAILABLE_TITLE, _UNAVAILABLE_DETAIL, key=_UNAVAILABLE_KEY)


def _run_preview() -> None:
    """The one function this script calls when actually launched via
    `streamlit run scripts/hosted_signals_preview.py`. Guarded by the
    __name__ == "__main__" check below so importing this module (as this
    phase's own tests do) never triggers a Streamlit render call. Does
    not import or call app.py, container.get_repositories(), normal
    multi-page routing (st.navigation), source scanners, review actions,
    or publishing logic — only signals.render() itself, with an
    explicitly injected repository or not called at all on failure.
    Does not create a browser URL, start Streamlit's own server, or set
    any host/port/bind configuration — launch binding is a separate,
    later approval."""
    dsn = _read_preview_dsn()
    if dsn is None:
        with_chrome(_render_hosted_unavailable, "signals", show_sidebar=False)()
        return

    try:
        repo = _build_hosted_signal_repository(dsn)
    except Exception:
        with_chrome(_render_hosted_unavailable, "signals", show_sidebar=False)()
        return

    with_chrome(lambda: signals.render(signal_repository=repo), "signals", show_sidebar=False)()


if __name__ == "__main__":
    _run_preview()
