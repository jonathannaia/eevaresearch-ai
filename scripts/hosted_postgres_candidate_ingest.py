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
        [--issuer-ticker TICKER --issuer-cik CIK]         # required if and only if --source edgar
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

Durable-State Phase 4K-3: `--issuer-ticker`/`--issuer-cik` restrict an
EDGAR run to exactly one explicitly selected, already-tracked issuer —
required if and only if `--source edgar`. `--issuer-ticker` is matched,
offline and exactly, against the existing static
`get_tracked_companies_for_source("SEC EDGAR")` registry
(`src/config/tracked_companies.py`) — no network call, no bulk SEC
ticker file, no `cik_resolver` involvement at all. `--issuer-cik` is
validated and normalized **syntactically only**, reusing the existing,
unmodified `client.normalize_cik()` zero-padding convention (the same
one `cik_resolver.py`/`discovery_service.py` already use) — this script
never calls `cik_resolver.resolve_and_cache()`, never reads or writes a
CIK cache file, and never verifies the ticker/CIK pair against SEC
itself. **The operator is solely responsible for supplying a CIK that
actually belongs to the named ticker** — a syntactically valid but
mismatched pair will not raise an error here; it will simply scan
whatever issuer that CIK actually belongs to (or return no filings, if
none). A future live-operation approval must require the operator to
verify the ticker/CIK pair from an authoritative source (e.g. SEC's own
EDGAR company search) before ever supplying a real value. Once resolved,
the matched tracked-company record is copied (`dataclasses.replace`)
with the normalized CIK attached and passed as a **one-element list**
directly to the existing, unmodified `edgar_pipeline.run_pipeline()` —
never `edgar_service.run_scan()`, which this phase intentionally bypasses
for EDGAR specifically, since `run_scan()` always resolves the full
25-company tracked registry internally with no filtering parameter of
its own. DART and EDINET are completely unaffected: they remain on
their existing, unmodified `run_scan(...)` call, and these two new flags
are neither required nor read for either source.

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
_build_explicit_postgres_settings below) and builds the matching
Postgres `CandidateRepository` via the existing, unmodified
`backend_factory.get_candidate_repository()`. For DART/EDINET, it
injects that repository into the source's own existing, unmodified
`{dart,edinet}_service.run_scan(settings, max_candidates=...,
candidate_repository=...)` — the same additive, optional injection seam
scripts/run_scan.py's own `main()` already uses for local/synthetic
tests (Durable-State Phases 4A/4C-1). For EDGAR, Phase 4K-3 calls the
existing, unmodified `edgar_pipeline.run_pipeline()` directly instead,
with exactly one explicitly resolved issuer (see the
`--issuer-ticker`/`--issuer-cik` section below) — never
`edgar_service.run_scan()`, which always resolves the full tracked
registry internally. Either way, this is a real, existing pipeline/
service call — it may create or advance candidates exactly as any other
scan would — but it
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
from dataclasses import replace
from pathlib import Path

from src.config.settings import Settings
from src.config.tracked_companies import get_tracked_companies_for_source
from src.data_access import backend_factory
from src.data_access.dart import radar_service as dart_radar_service
from src.data_access.edgar import edgar_pipeline
from src.data_access.edgar.client import EdgarClient, normalize_cik
from src.data_access.edinet import edinet_service
from src.models.models import CandidateStatus

_EDGAR_SOURCE = "SEC EDGAR"

# (CLI token) -> backend_factory "source" string. The three CLI tokens
# mirror scripts/run_scan.py's own _SOURCES tuple.
_SOURCE_DISPLAY_NAMES = {
    "dart": "OpenDART / DART",
    "edgar": _EDGAR_SOURCE,
    "edinet": "EDINET",
}

# EDGAR is deliberately absent here (Phase 4K-3): it never goes through
# a source service module's run_scan() — see main()'s own branch below.
_NON_EDGAR_SERVICE_MODULES = {
    "dart": dart_radar_service,
    "edinet": edinet_service,
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
        "--source", required=True, choices=sorted(_SOURCE_DISPLAY_NAMES),
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
    parser.add_argument(
        "--issuer-ticker", default=None,
        help=(
            "Required if and only if --source edgar: the exact ticker of one "
            "already-tracked SEC EDGAR issuer (matched offline against the static "
            "tracked-company registry only). No default; ignored for --source dart/edinet."
        ),
    )
    parser.add_argument(
        "--issuer-cik", default=None,
        help=(
            "Required if and only if --source edgar: that issuer's SEC CIK, "
            "digits only (leading zeros optional). Validated and normalized "
            "syntactically/locally only — never checked against SEC. No default; "
            "ignored for --source dart/edinet."
        ),
    )
    return parser


def _validate_and_normalize_cik(raw: str) -> str | None:
    """Syntactic/local-only validation — never calls cik_resolver, never
    reads or writes a CIK cache, never makes a network request. Reuses
    the existing, unmodified client.normalize_cik() zero-padding
    convention (the same one cik_resolver.py/discovery_service.py
    already use) rather than inventing a new one. Returns None for
    anything non-numeric, blank, all-zero, or longer than the 10
    significant digits normalize_cik's own zero-padded form allows."""
    stripped = raw.strip()
    if not stripped or not stripped.isdigit():
        return None
    normalized = normalize_cik(stripped)
    if len(normalized) != 10 or int(normalized) == 0:
        return None
    return normalized


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

    display_source = _SOURCE_DISPLAY_NAMES[args.source]

    edgar_user_agent: str | None = None
    resolved_company = None
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

        if args.issuer_ticker is None or not args.issuer_ticker.strip():
            print(
                "ERROR: --issuer-ticker is required and must be non-blank when "
                "--source edgar is selected. Aborting before any configuration or "
                "connection attempt.",
                file=sys.stderr,
            )
            return 2
        if args.issuer_cik is None or not args.issuer_cik.strip():
            print(
                "ERROR: --issuer-cik is required and must be non-blank when "
                "--source edgar is selected. Aborting before any configuration or "
                "connection attempt.",
                file=sys.stderr,
            )
            return 2

        # Offline, static-registry lookup only — no network call, no
        # cik_resolver, no cache. Case-folded since tracked tickers are
        # conventionally stored uppercase; still an exact match against
        # existing data, never an external resolution.
        ticker = args.issuer_ticker.strip().upper()
        matches = [c for c in get_tracked_companies_for_source(_EDGAR_SOURCE) if c.krx_code == ticker]
        if len(matches) != 1:
            print(
                f"ERROR: --issuer-ticker does not match exactly one tracked {_EDGAR_SOURCE} "
                f"issuer ({len(matches)} match(es)). Aborting before any configuration or "
                "connection attempt.",
                file=sys.stderr,
            )
            return 2

        normalized_cik = _validate_and_normalize_cik(args.issuer_cik)
        if normalized_cik is None:
            print(
                "ERROR: --issuer-cik is not a valid SEC CIK (expected digits only). "
                "Aborting before any configuration or connection attempt.",
                file=sys.stderr,
            )
            return 2

        resolved_company = replace(matches[0], corp_code=normalized_cik)

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
        if args.source == "edgar":
            # Phase 4K-3: exactly one resolved issuer, via the existing
            # public edgar_pipeline.run_pipeline() directly — never
            # edgar_service.run_scan(), which always resolves the full
            # tracked-company registry internally with no way to filter it.
            client = EdgarClient(edgar_user_agent)
            report = edgar_pipeline.run_pipeline(
                client, [resolved_company], settings.cache_dir,
                max_candidates_to_process=args.max_candidates, candidate_repository=candidate_repository,
            )
        else:
            report = _NON_EDGAR_SERVICE_MODULES[args.source].run_scan(
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
