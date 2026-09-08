"""Company Discovery Phase 2 — manual, one-shot backfill of the
Candidate Ledger over the existing Filing/Daily News backlog. NOT an
autonomous worker and NOT invoked automatically by anything — an
operator runs this exactly once when first standing up the Candidate
Ledger against a deployment's existing history, mirroring
scripts/import_daily_news_json_to_db.py's own "manual, one-shot,
idempotent" pattern exactly.

Invoke as:
    .venv/bin/python -m scripts.backfill_company_discovery

Safe to run more than once: every extracted evidence row's dedup_key is
already deterministic (see entity_resolution.generate_evidence_dedup_key)
— see candidate_pipeline.py's own module docstring for the full
idempotency/overlap-window rationale, which this script also relies on
via a much larger effective window plus the same MAX_BACKFILL_*_ROWS
bounds below. Nothing is ever duplicated.

Reads the SAME settings the discovery worker itself would use
(EDGE_COMPANY_DISCOVERY_WORKER_DB_BACKEND/_STATE_DB_URL) — never the
ambient dashboard EDGE_DB_BACKEND/EDGE_STATE_DB_URL pair. Exits 0 and
does nothing if that backend isn't exactly "sqlite" or "postgres".

Bounded: MAX_BACKFILL_FILING_ROWS / MAX_BACKFILL_DAILY_NEWS_ROWS cap how
many source records one run processes (a request-budget-style defense,
mirroring src/data_access/edgar/discovery_service.py's own
MAX_EDGAR_DISCOVERY_ROWS precedent) — a real deployment's full backlog
may exceed this in one run; simply re-running the script continues
where the last run's dedup_key coverage left off, safely.

Never runs the staleness/decay pass (a fresh import should never
immediately archive what it just found) and never touches
TrackedCompany/SEED_ISSUERS/DISCOVERY_STUBS or any live-monitoring
configuration. Only prints safe, already-sanitized summary counts —
never a raw exception, evidence content, or credential."""
from __future__ import annotations

import dataclasses

from src.config.settings import Settings, get_settings
from src.data_access.company_discovery import candidate_pipeline
from src.data_access.company_discovery.company_discovery_backend import get_candidate_issuer_repository

# One calendar decade — effectively "all existing history" without
# needing a separate "no time filter" code path in candidate_pipeline.py.
_BACKFILL_OVERLAP_HOURS = 24 * 365 * 10
MAX_BACKFILL_FILING_ROWS = 2000
MAX_BACKFILL_DAILY_NEWS_ROWS = 2000


def _build_backfill_settings(ambient: Settings) -> Settings | None:
    backend = ambient.company_discovery_worker_db_backend
    if backend not in ("sqlite", "postgres"):
        return None
    return dataclasses.replace(
        ambient, db_backend=backend,
        state_db_url=ambient.company_discovery_worker_state_db_url,
        state_db_path=ambient.company_discovery_worker_state_db_path,
    )


def main() -> int:
    ambient = get_settings()
    backfill_settings = _build_backfill_settings(ambient)
    if backfill_settings is None:
        print(
            'EDGE_COMPANY_DISCOVERY_WORKER_DB_BACKEND is not "sqlite" or "postgres" (or is unset) — '
            "there is no durable Candidate Ledger target to backfill into. Exiting without changing anything."
        )
        return 0

    try:
        repository = get_candidate_issuer_repository(backfill_settings)
    except Exception as exc:  # noqa: BLE001 — never leak a raw connection/config error
        print(f"ERROR: could not construct the Company Discovery candidate repository ({type(exc).__name__}).")
        return 1

    report = candidate_pipeline.run_candidate_discovery_tick(
        backfill_settings, repository, stale_days=ambient.company_discovery_stale_days,
        overlap_hours=_BACKFILL_OVERLAP_HOURS,
        max_filing_records=MAX_BACKFILL_FILING_ROWS, max_daily_news_records=MAX_BACKFILL_DAILY_NEWS_ROWS,
        run_decay_pass=False,
    )
    print(
        f"Company Discovery backfill — evidence_created={report.evidence_created} "
        f"candidates_created={report.candidates_created} candidates_quarantined={report.candidates_quarantined} "
        f"candidates_rejected={report.candidates_rejected}"
    )
    print(
        "If the real backlog exceeds this run's MAX_BACKFILL_*_ROWS bounds, re-run this script — "
        "already-recorded evidence is safely skipped, never duplicated."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
