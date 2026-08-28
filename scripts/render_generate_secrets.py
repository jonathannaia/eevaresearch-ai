"""Render startup helper (Durable-State Phase 4M-4) — generates
`.streamlit/secrets.toml` at container start time from
`.streamlit/secrets.tmpl.toml`, substituting `${VAR_NAME}` placeholders
from this process's own environment variables. Render has no
TOML-secrets mechanism of its own (unlike Streamlit Community Cloud's
Secrets editor) — this script is Render's equivalent bridge, run once
per container start by `scripts/render_start.sh`, before
`streamlit run app.py` ever executes.

Why this exists at all: `app.py` accesses `st.user.is_logged_in` on
every single page load (`getattr(st.user, "is_logged_in", False)`), and
Streamlit raises `StreamlitAuthError` from that access itself whenever
no `[auth]` section is configured in `.streamlit/secrets.toml` —
`getattr`'s own default argument only ever suppresses `AttributeError`,
never this. Without a generated `secrets.toml` present before Streamlit
starts, the whole app fails on every request on any host that lacks
Streamlit Community Cloud's own Secrets mechanism (exactly the Render
Web Service failure this phase fixes) — this is true regardless of
whether `EDGE_PRIVATE_BETA_AUTH_ENABLED` is set, since the crashing
`st.user` access happens before that flag is ever checked.

STANDALONE ENTRY POINT ONLY. Not imported by `app.py`, any UI page, or
`scripts/radar_worker.py` — this script's only caller is
`scripts/render_start.sh`, and it never touches the worker, the
database, or Streamlit Community Cloud's own deployment (which never
runs this file at all).

Never logs, prints, or embeds a secret *value* anywhere — only
environment-variable *names* ever appear in this script's own output,
and only for a variable that's missing or blank. The generated file's
permissions are set to owner-read/write only (0600) as defense in
depth on a shared or misconfigured host.

Required environment variables (see design/DECISIONS.md's Phase 4M-4
entry and `.streamlit/secrets.tmpl.toml`'s own header comment for the
full description of each):
    EDGE_AUTH_REDIRECT_URI
    EDGE_AUTH_COOKIE_SECRET
    EDGE_GOOGLE_CLIENT_ID
    EDGE_GOOGLE_CLIENT_SECRET
    EDGE_GOOGLE_SERVER_METADATA_URL
"""
from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = PROJECT_ROOT / ".streamlit" / "secrets.tmpl.toml"
OUTPUT_PATH = PROJECT_ROOT / ".streamlit" / "secrets.toml"

REQUIRED_VARS: tuple[str, ...] = (
    "EDGE_AUTH_REDIRECT_URI",
    "EDGE_AUTH_COOKIE_SECRET",
    "EDGE_GOOGLE_CLIENT_ID",
    "EDGE_GOOGLE_CLIENT_SECRET",
    "EDGE_GOOGLE_SERVER_METADATA_URL",
)

_PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


class MissingEnvironmentVariableError(Exception):
    """Raised with only the *names* of missing/blank required
    variables — never a value, never a hint about any other variable's
    contents."""

    def __init__(self, missing_names: list[str]) -> None:
        self.missing_names = missing_names
        super().__init__(
            "Missing required environment variable(s) for Streamlit auth secrets: "
            + ", ".join(missing_names)
        )


def _toml_escape(value: str) -> str:
    """Escapes a value for safe embedding inside a TOML basic ("...")
    string. Backslash and double-quote are the only two characters
    TOML's basic-string syntax requires escaping; escaping them here
    means a secret containing a stray `"` or `\\` can never break out
    of the template's quoted string or corrupt the generated file's
    syntax. A literal `#` inside the value is already safe without any
    special handling, since it stays inside the quoted string and is
    never interpreted as a TOML comment marker there."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def render_secrets_toml(template_text: str, env: dict[str, str]) -> str:
    """Pure substitution — no file I/O — so it's directly testable with
    synthetic, non-secret environment values. Every `${NAME}` placeholder
    in `template_text` is replaced with `env[NAME]`, escaped for safe
    TOML embedding. Raises `MissingEnvironmentVariableError` naming
    every missing/blank required variable at once (never partially,
    never one at a time) if any of `REQUIRED_VARS` is absent or blank —
    checked before any substitution is attempted, so a caller never
    gets a partially-substituted result."""
    missing = [name for name in REQUIRED_VARS if not (env.get(name) or "").strip()]
    if missing:
        raise MissingEnvironmentVariableError(missing)

    def _substitute(match: "re.Match[str]") -> str:
        return _toml_escape(env[match.group(1)])

    return _PLACEHOLDER_PATTERN.sub(_substitute, template_text)


def main() -> int:
    if not TEMPLATE_PATH.exists():
        print(f"ERROR: template not found at {TEMPLATE_PATH}", file=sys.stderr)
        return 1

    template_text = TEMPLATE_PATH.read_text(encoding="utf-8")
    try:
        rendered = render_secrets_toml(template_text, dict(os.environ))
    except MissingEnvironmentVariableError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    os.chmod(OUTPUT_PATH, stat.S_IRUSR | stat.S_IWUSR)  # 0600 — owner read/write only
    print(f"Generated {OUTPUT_PATH} from {TEMPLATE_PATH.name} ({len(REQUIRED_VARS)} value(s) substituted).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
