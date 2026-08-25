"""Durable-State Phase 4K-1 — bounded, explicit hosted-Postgres candidate
ingestion harness.

STANDALONE ENTRY POINT ONLY. This module is NOT imported by app.py,
scripts/run_scan.py, scripts/hosted_signals_preview.py, any normal UI
page, any normal scan entry point, any GitHub workflow, or any Streamlit
deployment configuration — nothing in this repository's ordinary
runtime path can reach it. Its whole purpose is to make a future,
separately-approved, bounded hosted-Postgres ingestion run explicit and
deliberate, never automatic. Importing this module performs no
environment read, no settings/repository construction, and no scan —
every real action happens only inside main(), and main() itself is only
ever invoked from the `if __name__ == "__main__":` guard at the bottom
of this file.

Run (later, once separately approved) as:
    .venv/bin/python -m scripts.hosted_postgres_candidate_ingest \\
        --source {dart,edgar,edinet} --max-candidates N --dsn-env-var NAME \\
        [--edgar-user-agent "<AppName> <contact@email>"]  # required if and only if --source edgar
(matching scripts/run_scan.py's own `-m` invocation convention.)

Every activation input is explicit and required — there is no default
source, no default candidate bound, and no default DSN or DSN-variable
name. Missing or invalid input fails fast (a non-zero exit), before any
Settings, repository, or pipeline call is made.

Durable-State Phase 4K-2: `--edgar-user-agent` supplies EDGAR's own
non-secret but required identifying string (see
src/data_access/edgar/client.py's own `EdgarConfigError` — SEC rejects
requests without one) directly into `Settings(edgar_user_agent=...)`.
Required only when `--source edgar`; rejected if blank/whitespace-only;
has no default; is never read from `EDGE_EDGAR_USER_AGENT`, `.env`,
`get_settings()`, or any ambient source — the operator must type it at
invocation time, the same explicit discipline as `--dsn-env-var`. It is
not a secret, but this script still never echoes its value in any
printed output, error message, or exception text. DART and EDINET are
unaffected: this flag is neither required nor read for either source,
and no new API-key flag or ambient-credential read was added for them.

DSN handling: `--dsn-env-var NAME` requires the operator to name — at
invocation time — an environment variable they have already exported in
that same shell for this one run only. This script reads only that
named variable, once, after every other input has already been
validated; it never reads EDGE_DB_BACKEND, EDGE_STATE_DB_URL,
get_settings(), `.env`, or a Streamlit secrets file, and it has no
hard-coded DSN-variable name of its own. The intended future operator
workflow is:
    export SOME_ONE_SHOT_DSN_VAR_NAME=postgresql://...   # typed directly, not committed anywhere
    .venv/bin/python -m scripts.hosted_postgres_candidate_ingest \\
        --source edgar --max-candidates 1 --dsn-env-var SOME_ONE_SHOT_DSN_VAR_NAME
    unset SOME_ONE_SHOT_DSN_VAR_NAME                     # immediately afterward
No DSN value is ever printed, logged, or written to a file by this
script; only `type(exc).__name__` is ever reported for a failure,
matching backend_factory._require_postgres_connection's own
sanitization discipline.

Ingestion mechanics: this script constructs an explicit
`Settings(db_backend="postgres", state_db_url=<the one DSN read above>,
...)` (every other field pinned to a neutral value — see
_build_explicit_postgres_settings below), builds the matching Postgres
`CandidateRepository` via the existing, unmodified
`backend_factory.get_candidate_repository()`, and injects it into the
selected source's own existing, unmodified `{dart,edgar,edinet}_service.
run_scan(settings, max_candidates=..., candidate_repository=...)` — the
same additive, optional injection seam scripts/run_scan.py's own
`main()` already uses for local/synthetic tests (Durable-State Phases
4A/4C-1). This is a real, existing pipeline/service call — it may
create or advance candidates exactly as any other scan would — but it
never calls `record_review_decision`, never touches signal-promotion
logic, never constructs or writes through a `SignalRepository`, and
never executes direct SQL. No pipeline in this codebase ever sets a
candidate to PUBLISHED itself (see design/DECISIONS.md and
src/logic/review_actions.py) — only a human reviewer action can — and
this script calls no such action; a defensive check after the run
additionally verifies no PUBLISHED candidate exists, treating one as an
anomaly rather than silently succeeding.

Never touches this repository's real local cache: `cache_dir` is pinned
to a freshly created, uniquely-named temporary directory for each run
(never `data/cache/`, never a caller-supplied path), since the
underlying scan pipelines read/write JSON-backed identifier and
filing-event caches under `settings.cache_dir` regardless of candidate
backend — mirroring the same discipline this session's earlier, one-off
hosted-validation scripts already established.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.dart import radar_service as dart_radar_service
from src.data_access.edgar import edgar_service
from src.data_access.edinet import edinet_service
from src.models.models import CandidateStatus

# (CLI token) -> (backend_factory "source" string, service module).
# The three CLI tokens mirror scripts/run_scan.py's own _SOURCES tuple.
_SOURCE_CONFIG = {
    "dart": ("OpenDART / DART", dart_radar_service),
    "edgar": ("SEC EDGAR", edgar_service),
    "edinet": ("EDINET", edinet_service),
}


class UnexpectedAutomatedPublishError(RuntimeError):
    """Raised if a PUBLISHED candidate is ever observed after an
    ingestion-only run. This harness never calls a review/publish
    action, and no pipeline in this codebase sets PUBLISHED itself
    (see src/logic/review_actions.py) — so this should be structurally
    impossible. Treated as a safety violation, not silently ignored."""


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hosted_postgres_candidate_ingest",
        description=(
            "Bounded, explicit hosted-Postgres candidate ingestion for exactly one "
            "source. Every input below is required — there is no default source, "
            "candidate bound, or DSN. See this module's own docstring for the full "
            "safety contract before ever supplying a real --dsn-env-var."
        ),
    )
    parser.add_argument(
        "--source", required=True, choices=sorted(_SOURCE_CONFIG),
        help="Exactly one of the repository's existing supported sources.",
    )
    parser.add_argument(
        "--max-candidates", required=True, type=int,
        help="Strictly positive maximum number of candidates to process this run.",
    )
    parser.add_argument(
        "--dsn-env-var", required=True,
        help=(
            "Name (not the value) of an environment variable the operator has "
            "already exported in this shell, for this one run only, containing the "
            "hosted Postgres DSN. Never a hard-coded name; never read at import time."
        ),
    )
    parser.add_argument(
        "--edgar-user-agent", default=None,
        help=(
            "Required if and only if --source edgar: EDGAR's own non-secret "
            "identifying string, e.g. \"AppName contact@email\" (see SEC's access "
            "policy). No default; never read from EDGE_EDGAR_USER_AGENT or any "
            "ambient source. Ignored for --source dart/edinet."
        ),
    )
    return parser


def _build_explicit_postgres_settings(dsn: str, edgar_user_agent: str | None = None) -> Settings:
    """Mirrors scripts/hosted_signals_preview.py's own
    _build_hosted_signal_repository discipline: every field besides
    db_backend/state_db_url/cache_dir/edgar_user_agent is pinned to an
    explicit neutral value rather than left to its own
    ambient-environment default_factory, so this call reads no
    environment variable beyond the one DSN already captured in `dsn`
    and, for EDGAR only, the explicit `--edgar-user-agent` value the
    caller already validated. DART's and EDINET's own credentials remain
    unconditionally None — out of scope for this phase, deliberately;
    each source will fail closed with its own existing
    missing-configuration error if one is required and not present."""
    return Settings(
        db_backend="postgres",
        state_db_url=dsn,
        data_mode="demo",
        cache_dir=Path(tempfile.mkdtemp(prefix="eeva-hosted-ingest-")),
        dart_api_key=None,
        translation_api_key=None,
        edgar_user_agent=edgar_user_agent,
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


def _assert_no_automated_publish(candidate_repository) -> None:
    for candidate in candidate_repository.load_candidates().values():
        if candidate.status == CandidateStatus.PUBLISHED:
            raise UnexpectedAutomatedPublishError(
                "A candidate was found with status PUBLISHED after an "
                "ingestion-only run. This harness never performs a review or "
                "publish action, so this outcome should be impossible — treat "
                "it as a serious anomaly and stop before any further use of "
                "this hosted store."
            )


def _format_safe_summary(source_key: str, report) -> str:
    """Only already-documented-safe ScanReport fields — counts and safe
    strings, matching scripts/run_scan.py's own summary discipline.
    Never a document/filing content, never a credential."""
    bgn = getattr(report, "bgn_de", None) or getattr(report, "bgn_date", "")
    end = getattr(report, "end_de", None) or getattr(report, "end_date", "")
    return (
        f"{source_key.upper()} hosted ingestion complete — scan_id={report.scan_id} "
        f"window={bgn}..{end} candidates_detected={report.candidates_detected} "
        f"candidates_processed={report.candidates_processed} "
        f"candidates_deferred={report.candidates_deferred}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)  # missing/invalid --source or --max-candidates type exits(2) here

    if args.max_candidates <= 0:
        print("ERROR: --max-candidates must be a strictly positive integer.", file=sys.stderr)
        return 2

    display_source, service_module = _SOURCE_CONFIG[args.source]

    edgar_user_agent: str | None = None
    if args.source == "edgar":
        # Required if and only if --source edgar. Never echoed — not a
        # secret, but this script reports only that it is missing/blank,
        # never the value itself, per this phase's own safety contract.
        if args.edgar_user_agent is None or not args.edgar_user_agent.strip():
            print(
                "ERROR: --edgar-user-agent is required and must be non-blank when "
                "--source edgar is selected. Aborting before any configuration or "
                "connection attempt.",
                file=sys.stderr,
            )
            return 2
        edgar_user_agent = args.edgar_user_agent

    dsn = os.environ.get(args.dsn_env_var)
    if not dsn:
        print(
            f"ERROR: the environment variable named by --dsn-env-var ({args.dsn_env_var}) "
            "is not set (or is empty). Aborting before any configuration or connection attempt.",
            file=sys.stderr,
        )
        return 2

    settings = _build_explicit_postgres_settings(dsn, edgar_user_agent)

    try:
        candidate_repository = backend_factory.get_candidate_repository(settings, display_source)
    except Exception as exc:  # noqa: BLE001 — never leak a raw connection/config error
        print(
            f"ERROR: could not construct the hosted Postgres candidate repository ({type(exc).__name__}).",
            file=sys.stderr,
        )
        return 1

    try:
        report = service_module.run_scan(
            settings, max_candidates=args.max_candidates, candidate_repository=candidate_repository,
        )
    except Exception as exc:  # noqa: BLE001 — never leak a raw pipeline/network error
        print(f"ERROR: ingestion run failed ({type(exc).__name__}).", file=sys.stderr)
        return 1

    try:
        _assert_no_automated_publish(candidate_repository)
    except UnexpectedAutomatedPublishError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(_format_safe_summary(args.source, report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
