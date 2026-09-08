"""Durable-State Phase 4L-1 — standalone, strictly read-only hosted-Postgres
candidate-audit command.

STANDALONE ENTRY POINT ONLY. This module is NOT imported by app.py,
scripts/run_scan.py, scripts/hosted_postgres_candidate_ingest.py,
scripts/hosted_signals_preview.py, any normal UI page, any normal scan
entry point, any GitHub workflow, or any Streamlit deployment
configuration — nothing in this repository's ordinary runtime path can
reach it. It exists so a human operator can, later and separately
approved, list candidate metadata already sitting in the hosted Postgres
store — after a real ingestion (Phase 4K-1/4K-3) — without touching a
UI, without reviewing/publishing anything, and without writing a single
byte back to the database. Importing this module performs no
environment read, no argument parsing, no settings/repository
construction, and no data load — every real action happens only inside
main(), and main() itself is only ever invoked from the
`if __name__ == "__main__":` guard at the bottom of this file.

Run (later, once separately approved) as:
    .venv/bin/python -m scripts.hosted_postgres_candidate_audit \\
        --source {dart,edgar,edinet} --dsn-env-var NAME [--ticker TICKER]
(matching scripts/run_scan.py's and
scripts/hosted_postgres_candidate_ingest.py's own `-m` invocation
convention.)

DSN handling: `--dsn-env-var NAME` requires the operator to name — at
invocation time — an environment variable they have already exported in
that same shell for this one read only. This script reads only that
named variable, once, after every other input has already been
validated; it never reads EDGE_DB_BACKEND, EDGE_STATE_DB_URL,
get_settings(), `.env`, or a Streamlit secrets file, and it has no
hard-coded DSN-variable name of its own. The intended future operator
workflow is:
    export SOME_ONE_SHOT_READ_DSN_VAR=postgresql://...   # typed directly, not committed anywhere
    .venv/bin/python -m scripts.hosted_postgres_candidate_audit \\
        --source edgar --dsn-env-var SOME_ONE_SHOT_READ_DSN_VAR
    unset SOME_ONE_SHOT_READ_DSN_VAR                     # immediately afterward
No DSN value is ever printed, logged, or written to a file by this
script; a repository construction or read failure prints only a fixed,
generic message — never `str(exc)`, never any exception detail.

Read-only guarantee: this script constructs an explicit
`Settings(db_backend="postgres", state_db_url=<the one DSN read above>,
...)` (every other field pinned to a neutral value, mirroring
scripts/hosted_signals_preview.py's and
scripts/hosted_postgres_candidate_ingest.py's own discipline), builds
the matching Postgres `CandidateRepository` via the existing, unmodified
`backend_factory.get_candidate_repository()`, and calls only its
existing `load_candidates()` read method. It never calls
`update_candidate()`, `upsert_new_candidates()`, `get_candidate()`, or
`get_candidate_version()` on that repository; never issues direct SQL;
never constructs or calls a `SignalRepository`; never calls
`record_review_decision` or any publish/promotion logic; never imports a
source client/service/pipeline; and never touches Streamlit, Docker, a
workflow, a scheduler, or a browser. `--ticker`, when supplied, filters
purely in Python against the already-loaded candidates' real
`filing.stock_code` field, case-insensitively — no provider, resolver,
or scan is ever consulted.

Output is deliberately minimal and stable: a `CANDIDATES=<count>` line,
then one line per candidate (sorted by `filing.rcept_dt`, then `id`)
containing only `id`, `status`, `form` (filing.pblntf_ty), `filed`
(filing.rcept_dt), `ticker` (filing.stock_code), `confidence`, and
`rules` (matched_rules, comma-joined) — never an excerpt, filing title,
source URL, accession number, corp code/CIK, review note, state-history
detail, or any credential. Status/confidence are rendered via their own
literal string content (bypassing `Enum.__str__`'s qualified-name
override, without ever assuming a `.value` attribute exists — see
`_safe_field` below), so this renders correctly whether `status` is a
real `CandidateStatus` enum instance or a plain string."""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from src.config.settings import Settings
from src.data_access import backend_factory

_SOURCE_DISPLAY_NAMES = {
    "dart": "OpenDART / DART",
    "edgar": "SEC EDGAR",
    "edinet": "EDINET",
}


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hosted_postgres_candidate_audit",
        description=(
            "Strictly read-only hosted-Postgres candidate audit for exactly one "
            "source, with an optional ticker filter. --source and --dsn-env-var "
            "are required; there is no default source or DSN."
        ),
    )
    parser.add_argument(
        "--source", required=True, choices=sorted(_SOURCE_DISPLAY_NAMES),
        help="Exactly one of the repository's existing supported sources.",
    )
    parser.add_argument(
        "--dsn-env-var", required=True,
        help=(
            "Name (not the value) of an environment variable the operator has "
            "already exported in this shell, containing a hosted Postgres read "
            "DSN. Never a hard-coded name; never read at import time."
        ),
    )
    parser.add_argument(
        "--ticker", default=None,
        help=(
            "Optional: restrict output to candidates whose filing.stock_code "
            "matches this value case-insensitively. No provider/resolver/scan "
            "is used for this filter."
        ),
    )
    return parser


def _build_explicit_postgres_settings(dsn: str) -> Settings:
    """Mirrors scripts/hosted_signals_preview.py's and
    scripts/hosted_postgres_candidate_ingest.py's own discipline: every
    field besides db_backend/state_db_url/cache_dir is pinned to an
    explicit neutral value rather than left to its own
    ambient-environment default_factory, so this call reads no
    environment variable beyond the one DSN already captured in `dsn`.
    `cache_dir` is pinned to a fresh temp directory purely for
    defense-in-depth consistency with those sibling scripts — the
    Postgres candidate-repository read path this script actually uses
    never touches cache_dir at all."""
    return Settings(
        db_backend="postgres",
        state_db_url=dsn,
        data_mode="demo",
        cache_dir=Path(tempfile.mkdtemp(prefix="eeva-hosted-audit-")),
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


def _safe_field(value: object) -> str:
    """Never assumes a `.value` attribute exists — the exact mistake
    this phase's own tests guard against, since `CandidateSignal.confidence`
    is already a plain `str` with no `.value` at all, while `.status` is
    a real `CandidateStatus(str, Enum)` whose default `__str__`/`format()`
    (Python 3.11+ Enum behavior) renders the ugly qualified
    "CandidateStatus.PUBLISHED" form rather than the plain value.
    `str.__str__` bypasses that override and returns the object's own
    literal string content — correct for a real enum instance AND a
    no-op for an already-plain string (including a synthetic test
    double with no `.value` attribute whatsoever). Returns "" for None
    or any non-string value's str() form, left for the caller to
    normalize into "unknown"/"none"."""
    if value is None:
        return ""
    if isinstance(value, str):
        return str.__str__(value)
    return str(value)


def _format_candidate_line(candidate: object) -> str:
    filing = getattr(candidate, "filing", None)
    status = _safe_field(getattr(candidate, "status", None)) or "unknown"
    confidence = _safe_field(getattr(candidate, "confidence", None)) or "unknown"
    form = _safe_field(getattr(filing, "pblntf_ty", None)) or "unknown"
    filed = _safe_field(getattr(filing, "rcept_dt", None)) or "unknown"
    ticker = _safe_field(getattr(filing, "stock_code", None)) or "unknown"
    rules_list = getattr(candidate, "matched_rules", None) or []
    rules = ",".join(_safe_field(r) for r in rules_list) or "none"
    return (
        f"id={candidate.id} status={status} form={form} filed={filed} "
        f"ticker={ticker} confidence={confidence} rules={rules}"
    )


def _sort_key(candidate: object) -> tuple[str, str]:
    filing = getattr(candidate, "filing", None)
    return (_safe_field(getattr(filing, "rcept_dt", None)), _safe_field(getattr(candidate, "id", None)))


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)  # missing --source/--dsn-env-var or an unsupported source exits(2) here

    display_source = _SOURCE_DISPLAY_NAMES[args.source]

    dsn = os.environ.get(args.dsn_env_var)
    if not dsn:
        print(
            f"ERROR: the environment variable named by --dsn-env-var ({args.dsn_env_var}) "
            "is not set (or is empty). Aborting before any configuration or connection attempt.",
            file=sys.stderr,
        )
        return 2

    settings = _build_explicit_postgres_settings(dsn)

    try:
        candidate_repository = backend_factory.get_candidate_repository(settings, display_source)
        candidates = list(candidate_repository.load_candidates().values())
    except Exception:  # noqa: BLE001 — never leak a raw connection/config/read error
        print("AUDIT_STOP: hosted candidate repository could not be read.", file=sys.stderr)
        return 1

    if args.ticker:
        wanted = args.ticker.strip().upper()
        candidates = [
            c for c in candidates
            if _safe_field(getattr(getattr(c, "filing", None), "stock_code", None)).strip().upper() == wanted
        ]

    candidates.sort(key=_sort_key)

    print(f"CANDIDATES={len(candidates)}")
    for candidate in candidates:
        print(_format_candidate_line(candidate))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
