"""Durable-State Phase 4M-0 — explicit, manually-run, idempotent
identifier-resolution bootstrap for EDGAR and DART, writing resolved
identifiers into an explicitly selected SQLite or Postgres identifier
repository.

STANDALONE ENTRY POINT ONLY. Not imported by app.py, scripts/run_scan.py,
scripts/radar_worker.py, scripts/hosted_postgres_candidate_ingest.py, or
any normal UI page/scan entry point. This is the one, explicit, human-run
step an operator runs before starting the continuous worker (and again,
occasionally, whenever a new tracked issuer is added) — the worker
itself (scripts/radar_worker.py) never calls this and never resolves an
identifier on its own; see that script's own docstring.

EDINET is deliberately excluded (no --source edinet choice exists): its
five tracked issuers already carry hardcoded, live-verified identifiers
in src/config/tracked_companies.py — there is no resolver to bootstrap.

Run (later, once separately approved) as:
    .venv/bin/python -m scripts.resolve_tracked_identifiers \\
        --source edgar --backend postgres --dsn-env-var NAME \\
        --edgar-user-agent "<AppName> <contact@email>"
    .venv/bin/python -m scripts.resolve_tracked_identifiers \\
        --source dart --backend sqlite --sqlite-path /path/to/state.db \\
        --dart-api-key "<key>"

Every activation input is explicit and required for the chosen
--source/--backend combination — there is no default backend, DSN,
sqlite path, user-agent, or API key, and no ambient EDGE_EDGAR_USER_AGENT/
EDGE_DART_API_KEY/EDGE_DB_BACKEND/EDGE_STATE_DB_URL is ever read. The DSN
is read only from the operator-named environment variable, once, after
every other input has already been validated — the same explicit
discipline as scripts/hosted_postgres_candidate_ingest.py's own
--dsn-env-var. No DSN, API key, or user-agent value is ever printed;
only `type(exc).__name__` is ever reported for a failure.

This script calls only the existing, unmodified cik_resolver.resolve_and_cache()/
corp_code_resolver.resolve_and_cache() (one real bulk-file fetch plus
per-ticker cross-checks, matching exactly what edgar_readiness()/
Radar Inbox's own existing manual resolution paths already do) — no new
resolution logic, and no broad/uncontrolled resolver call: the tracked
list resolved is always exactly today's existing tracked-company
universe for that source (src/config/tracked_companies.py), never a
broader or dynamically-discovered set. Resolved identifiers are written
into the selected repository via the existing, unmodified
upsert_resolved_identifier() — insert-or-replace by (source, ticker),
so re-running this script for the same issuers is a safe no-op/refresh,
never a duplicate or an error."""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from src.config.settings import Settings
from src.config.tracked_companies import get_tracked_companies_for_source
from src.data_access import backend_factory
from src.data_access.dart import corp_code_resolver
from src.data_access.dart.client import DartClient
from src.data_access.edgar import cik_resolver
from src.data_access.edgar.client import EdgarClient
from src.data_access.postgres_state_db.identifier_repository import (
    ResolvedIdentifierRecord as PostgresResolvedIdentifierRecord,
)
from src.data_access.postgres_state_db.identifier_repository import (
    upsert_resolved_identifier as postgres_upsert_resolved_identifier,
)
from src.data_access.state_db.identifier_repository import ResolvedIdentifierRecord as SqliteResolvedIdentifierRecord
from src.data_access.state_db.identifier_repository import upsert_resolved_identifier as sqlite_upsert_resolved_identifier

_SOURCE_DISPLAY_NAMES = {"edgar": "SEC EDGAR", "dart": "OpenDART / DART"}


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="resolve_tracked_identifiers",
        description=(
            "Explicit, manual, idempotent identifier-resolution bootstrap for "
            "EDGAR/DART only (EDINET needs no resolver). Every input is "
            "required for the chosen --source/--backend combination — no "
            "default backend, DSN, sqlite path, user-agent, or API key."
        ),
    )
    parser.add_argument("--source", required=True, choices=sorted(_SOURCE_DISPLAY_NAMES))
    parser.add_argument("--backend", required=True, choices=("sqlite", "postgres"))
    parser.add_argument(
        "--sqlite-path", default=None,
        help="Required if and only if --backend sqlite: path to the SQLite state database file.",
    )
    parser.add_argument(
        "--dsn-env-var", default=None,
        help=(
            "Required if and only if --backend postgres: name (not the value) of an "
            "environment variable the operator has already exported, containing the DSN."
        ),
    )
    parser.add_argument(
        "--edgar-user-agent", default=None,
        help="Required if and only if --source edgar: EDGAR's own non-secret identifying string.",
    )
    parser.add_argument(
        "--dart-api-key", default=None,
        help="Required if and only if --source dart: the OpenDART API key.",
    )
    return parser


def _build_explicit_settings(backend: str, state_db_path: Path | None, dsn: str | None) -> Settings:
    """Mirrors scripts/hosted_postgres_candidate_ingest.py's own
    discipline: every field besides db_backend/the one relevant
    connection field/cache_dir is pinned to an explicit neutral value."""
    return Settings(
        db_backend=backend,
        state_db_path=state_db_path,
        state_db_url=dsn,
        data_mode="demo",
        cache_dir=Path(tempfile.mkdtemp(prefix="eeva-resolver-bootstrap-")),
        dart_api_key=None,
        translation_api_key=None,
        edgar_user_agent=None,
        edinet_subscription_key=None,
        edgar_discovery_enabled=False,
        private_beta_auth_enabled=False,
        private_beta_allowed_emails=frozenset(),
        remote_cache_enabled=False,
        r2_account_id=None,
        r2_access_key_id=None,
        r2_secret_access_key=None,
        r2_bucket=None,
        r2_endpoint=None,
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    display_source = _SOURCE_DISPLAY_NAMES[args.source]

    if args.backend == "sqlite":
        if not args.sqlite_path:
            print("ERROR: --sqlite-path is required when --backend sqlite.", file=sys.stderr)
            return 2
        settings = _build_explicit_settings("sqlite", Path(args.sqlite_path), None)
    else:
        if not args.dsn_env_var:
            print("ERROR: --dsn-env-var is required when --backend postgres.", file=sys.stderr)
            return 2
        dsn = os.environ.get(args.dsn_env_var)
        if not dsn:
            print(
                f"ERROR: the environment variable named by --dsn-env-var ({args.dsn_env_var}) "
                "is not set (or is empty). Aborting before any configuration or connection attempt.",
                file=sys.stderr,
            )
            return 2
        settings = _build_explicit_settings("postgres", None, dsn)

    tickers = [c.krx_code for c in get_tracked_companies_for_source(display_source)]

    if args.source == "edgar":
        if not args.edgar_user_agent or not args.edgar_user_agent.strip():
            print(
                "ERROR: --edgar-user-agent is required and must be non-blank when "
                "--source edgar is selected.",
                file=sys.stderr,
            )
            return 2
        client = EdgarClient(args.edgar_user_agent)
        try:
            result = cik_resolver.resolve_and_cache(client, tickers, settings.cache_dir)
        except Exception as exc:  # noqa: BLE001 — never leak a raw network/parse error
            print(f"ERROR: identifier resolution failed ({type(exc).__name__}).", file=sys.stderr)
            return 1
        resolved_items = {
            ticker: (record.cik, record.company_name, record.source, record.retrieved_at)
            for ticker, record in result.resolved.items()
        }
        missing = result.missing_tickers
    else:
        if not args.dart_api_key or not args.dart_api_key.strip():
            print(
                "ERROR: --dart-api-key is required and must be non-blank when "
                "--source dart is selected.",
                file=sys.stderr,
            )
            return 2
        client = DartClient(args.dart_api_key)
        try:
            result = corp_code_resolver.resolve_and_cache(client, tickers, settings.cache_dir)
        except Exception as exc:  # noqa: BLE001 — never leak a raw network/parse error
            print(f"ERROR: identifier resolution failed ({type(exc).__name__}).", file=sys.stderr)
            return 1
        resolved_items = {
            ticker: (record.corp_code, record.corp_name, record.source, record.retrieved_at)
            for ticker, record in result.resolved.items()
        }
        missing = result.missing_krx_codes

    if result.error:
        print(f"ERROR: identifier resolution reported a failure ({result.error!r} — no raw detail).", file=sys.stderr)
        return 1

    try:
        repo = backend_factory.get_identifier_repository(settings, display_source)
        if args.backend == "sqlite":
            for ticker, (identifier, display_name, method, retrieved_at) in resolved_items.items():
                sqlite_upsert_resolved_identifier(
                    repo.conn, display_source, ticker,
                    SqliteResolvedIdentifierRecord(
                        identifier=identifier, display_name=display_name,
                        resolution_method=method, retrieved_at=retrieved_at,
                    ),
                )
        else:
            for ticker, (identifier, display_name, method, retrieved_at) in resolved_items.items():
                postgres_upsert_resolved_identifier(
                    repo.conn, display_source, ticker,
                    PostgresResolvedIdentifierRecord(
                        identifier=identifier, display_name=display_name,
                        resolution_method=method, retrieved_at=retrieved_at,
                    ),
                )
    except Exception as exc:  # noqa: BLE001 — never leak a raw connection/config error
        print(f"ERROR: could not persist resolved identifiers ({type(exc).__name__}).", file=sys.stderr)
        return 1

    print(
        f"{args.source.upper()} identifier resolution complete — "
        f"resolved={len(resolved_items)} missing={len(missing)} backend={args.backend}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
