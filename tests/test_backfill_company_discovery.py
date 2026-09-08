"""scripts/backfill_company_discovery.py — one-shot, explicitly-
invoked, bounded, idempotent, disabled-unless-backend-configured. No
real network call anywhere; SQLite fixtures only."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from scripts import backfill_company_discovery
from src.config.settings import Settings
from src.data_access.state_db import candidate_repository as candrepo
from src.data_access.state_db import connection, filing_event_repository, schema
from src.data_access.state_db.candidate_issuer_repository import list_candidates
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, StateTransition


def _settings(tmp_path, **overrides) -> Settings:
    fields = dict(
        company_discovery_worker_db_backend=None, company_discovery_worker_state_db_path=None,
        cache_dir=tmp_path,
    )
    fields.update(overrides)
    return Settings(**fields)


def test_main_is_a_no_op_when_backend_not_configured(tmp_path, capsys):
    with patch("scripts.backfill_company_discovery.get_settings", return_value=_settings(tmp_path)):
        result = backfill_company_discovery.main()
    assert result == 0
    assert "no durable candidate ledger" in capsys.readouterr().out.lower()


def test_backfill_is_disabled_unless_backend_is_sqlite_or_postgres(tmp_path, capsys):
    with patch(
        "scripts.backfill_company_discovery.get_settings",
        return_value=_settings(tmp_path, company_discovery_worker_db_backend="json"),
    ):
        result = backfill_company_discovery.main()
    assert result == 0
    assert "no durable candidate ledger" in capsys.readouterr().out.lower()


def test_backfill_processes_existing_history_and_is_idempotent(tmp_path, capsys):
    db_path = tmp_path / "test.db"
    conn = connection.connect(db_path)
    schema.migrate(conn)

    old = datetime.now(timezone.utc).replace(year=2020)  # far outside the recurring worker's 72h window
    filing = FilingEvent(
        rcept_no="0001045810-26-000001", corp_code="0001045810", corp_name="NVIDIA", stock_code="NVDA",
        report_nm="8-K filing", rcept_dt="2020-01-01", flr_nm="NVIDIA", pblntf_ty="8-K",
        source_url="https://www.sec.gov/test", retrieved_at=old.isoformat(),
        source_name="SEC EDGAR", original_language="English",
    )
    filing_event_repository.upsert_filing_event(conn, filing)
    candrepo.upsert_new_candidates(conn, "SEC EDGAR", [CandidateSignal(
        id="cand-1", filing=filing, matched_rules=["x:y:z"], confidence="Moderate",
        status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="NVIDIA announced components supplied by Example Materials Corp. for the new facility.",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=old.isoformat())],
    )])
    conn.close()

    settings = _settings(tmp_path, company_discovery_worker_db_backend="sqlite", company_discovery_worker_state_db_path=db_path)
    with patch("scripts.backfill_company_discovery.get_settings", return_value=settings):
        result1 = backfill_company_discovery.main()
        result2 = backfill_company_discovery.main()

    assert result1 == 0
    assert result2 == 0
    output = capsys.readouterr().out
    assert "evidence_created=1" in output  # first run
    assert "evidence_created=0" in output  # second run — idempotent

    conn2 = connection.connect(db_path)
    candidates = list_candidates(conn2)
    assert len(candidates) == 1
    assert candidates[0].issuer.legal_name == "Example Materials Corp."


def test_backfill_never_runs_the_staleness_decay_pass(tmp_path):
    """A fresh import must never immediately archive what it just
    found — run_decay_pass=False is threaded all the way through."""
    import inspect

    source = inspect.getsource(backfill_company_discovery.main)
    assert "run_decay_pass=False" in source
