"""candidate_pipeline.run_candidate_discovery_tick — orchestration-level
proofs: evidence-required invariant, idempotency, the rolling ingestion-
overlap window capturing late-arriving/timestamp-anomalous records, and
the no-promotion invariant. In-memory-file SQLite only; zero network
calls anywhere in this file."""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.config.settings import Settings
from src.config.tracked_companies import get_tracked_companies
from src.data_access.company_discovery import candidate_pipeline
from src.data_access.company_discovery.company_discovery_backend import SqliteCandidateIssuerRepository
from src.data_access.state_db import connection, schema
from src.data_access.state_db import candidate_repository as candrepo
from src.data_access.state_db import daily_news_repository as sqlite_daily_news
from src.data_access.state_db import filing_event_repository
from src.data_access.state_db.candidate_issuer_repository import get_evidence_for_issuer, list_candidates
from src.models.daily_news_models import NewsSourceReference, NewsStory, NewsStoryStatus, SourceClass
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, StateTransition


@pytest.fixture()
def db(tmp_path):
    db_path = tmp_path / "test.db"
    conn = connection.connect(db_path)
    schema.migrate(conn)
    settings = Settings(db_backend="sqlite", state_db_path=db_path, cache_dir=tmp_path)
    repo = SqliteCandidateIssuerRepository(conn=conn)
    return conn, settings, repo


def _filing(rcept_no: str, retrieved_at: str, report_nm: str = "8-K filing") -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="0001045810", corp_name="NVIDIA", stock_code="NVDA",
        report_nm=report_nm, rcept_dt="2026-09-02", flr_nm="NVIDIA", pblntf_ty="8-K",
        source_url="https://www.sec.gov/test", retrieved_at=retrieved_at,
        source_name="SEC EDGAR", original_language="English",
    )


def _candidate_for(filing: FilingEvent, excerpt: str) -> CandidateSignal:
    now = datetime.now(timezone.utc).isoformat()
    return CandidateSignal(
        id=f"cand-{filing.rcept_no}", filing=filing, matched_rules=["x:y:z"], confidence="Moderate",
        status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original=excerpt, state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=now)],
    )


def _news_story(story_id: str, retrieved_at: str, headline: str) -> NewsStory:
    return NewsStory(
        id=story_id, company_name="NVIDIA", ticker="NVDA", theme_slug="ai-buildout",
        headline=headline, eeva_summary=None, is_fallback_summary=False, translation_unavailable=False,
        original_title=None,
        sources=(NewsSourceReference(
            publisher="NVIDIA", source_class=SourceClass.OFFICIAL_COMPANY, url="https://nvidianews.nvidia.com/x",
            title=headline, published_at=retrieved_at, retrieved_at=retrieved_at, original_language="English",
        ),),
        status=NewsStoryStatus.PUBLISHED, state_history=[],
    )


def test_evidence_required_invariant_every_created_candidate_has_evidence(db):
    conn, settings, repo = db
    now = datetime.now(timezone.utc)
    filing = _filing("0001045810-26-000001", now.isoformat())
    filing_event_repository.upsert_filing_event(conn, filing)
    candrepo.upsert_new_candidates(conn, "SEC EDGAR", [_candidate_for(
        filing, "NVIDIA announced components supplied by Example Materials Corp. for the new facility.",
    )])

    candidate_pipeline.run_candidate_discovery_tick(settings, repo, stale_days=180, now=now)

    candidates = list_candidates(conn)
    assert len(candidates) >= 1
    for record in candidates:
        assert len(get_evidence_for_issuer(conn, record.issuer.issuer_id)) >= 1


def test_idempotent_across_repeated_ticks_over_the_same_window(db):
    conn, settings, repo = db
    now = datetime.now(timezone.utc)
    filing = _filing("0001045810-26-000002", now.isoformat())
    filing_event_repository.upsert_filing_event(conn, filing)
    candrepo.upsert_new_candidates(conn, "SEC EDGAR", [_candidate_for(
        filing, "NVIDIA announced components supplied by Example Materials Corp. for the new facility.",
    )])

    r1 = candidate_pipeline.run_candidate_discovery_tick(settings, repo, stale_days=180, now=now)
    r2 = candidate_pipeline.run_candidate_discovery_tick(settings, repo, stale_days=180, now=now)
    r3 = candidate_pipeline.run_candidate_discovery_tick(settings, repo, stale_days=180, now=now)

    assert r1.evidence_created == 1
    assert r2.evidence_created == 0
    assert r3.evidence_created == 0
    assert len(list_candidates(conn)) == 1


def test_rolling_overlap_window_captures_a_late_arriving_record(db):
    """A record whose own retrieved_at is well older than the tick
    interval (simulating a worker restart gap, a backfill touching an
    older row, or any other timestamp anomaly) is still processed as
    long as it falls within the rolling overlap window — never
    permanently skipped the way a strict "after last watermark" filter
    would skip it once passed over."""
    conn, settings, repo = db
    now = datetime.now(timezone.utc)
    fresh_filing = _filing("0001045810-26-000003", now.isoformat())
    # 48 hours old — well past a typical short worker-restart gap, but
    # still inside the approved 72-hour overlap window.
    late_arriving_filing = _filing(
        "0001045810-26-000004", (now - timedelta(hours=48)).isoformat(), report_nm="8-K filing late",
    )
    filing_event_repository.upsert_filing_event(conn, fresh_filing)
    filing_event_repository.upsert_filing_event(conn, late_arriving_filing)
    candrepo.upsert_new_candidates(conn, "SEC EDGAR", [
        _candidate_for(fresh_filing, "NVIDIA announced components supplied by Example Fresh Corp. this week."),
        _candidate_for(late_arriving_filing, "NVIDIA announced a strategic partnership with Example Late Corp. in a delayed filing."),
    ])

    report = candidate_pipeline.run_candidate_discovery_tick(settings, repo, stale_days=180, now=now)

    assert report.candidates_created == 2
    legal_names = {r.issuer.legal_name for r in list_candidates(conn)}
    assert "Example Fresh Corp." in legal_names
    assert "Example Late Corp." in legal_names


def test_record_outside_the_overlap_window_is_not_processed(db):
    conn, settings, repo = db
    now = datetime.now(timezone.utc)
    too_old_filing = _filing("0001045810-26-000005", (now - timedelta(hours=100)).isoformat())
    filing_event_repository.upsert_filing_event(conn, too_old_filing)
    candrepo.upsert_new_candidates(conn, "SEC EDGAR", [_candidate_for(
        too_old_filing, "NVIDIA announced components supplied by Example TooOld Corp. last quarter.",
    )])

    report = candidate_pipeline.run_candidate_discovery_tick(settings, repo, stale_days=180, now=now)

    assert report.evidence_created == 0
    assert list_candidates(conn) == ()


def test_core_and_stub_mentions_never_create_a_candidate_row(db):
    conn, settings, repo = db
    now = datetime.now(timezone.utc)
    filing = _filing("0001045810-26-000006", now.isoformat())
    filing_event_repository.upsert_filing_event(conn, filing)
    # "NVIDIA" is a real Core company name — a mention of it alone must
    # never create a Candidate row.
    candrepo.upsert_new_candidates(conn, "SEC EDGAR", [_candidate_for(
        filing, "Example customer NVIDIA placed a large order this quarter.",
    )])

    candidate_pipeline.run_candidate_discovery_tick(settings, repo, stale_days=180, now=now)

    legal_names = {r.issuer.legal_name for r in list_candidates(conn)}
    assert "NVIDIA" not in legal_names


def test_rejected_mention_is_never_re_litigated_on_a_later_tick(db):
    conn, settings, repo = db
    now = datetime.now(timezone.utc)
    filing = _filing("0001045810-26-000007", now.isoformat())
    filing_event_repository.upsert_filing_event(conn, filing)
    candrepo.upsert_new_candidates(conn, "SEC EDGAR", [_candidate_for(
        filing, "NVIDIA disclosed components supplied by Example Capital Fund LLC this quarter.",
    )])

    r1 = candidate_pipeline.run_candidate_discovery_tick(settings, repo, stale_days=180, now=now)
    r2 = candidate_pipeline.run_candidate_discovery_tick(settings, repo, stale_days=180, now=now)

    assert r1.candidates_rejected == 1
    assert r2.candidates_rejected == 0  # already recorded — never re-litigated
    rejected = list_candidates(conn, coverage_state="Rejected")
    assert len(rejected) == 1
    assert rejected[0].composite_score == 0.0


def test_no_promotion_path_tracked_companies_registry_is_never_touched(db):
    """The live TrackedCompany registry is static code — this proves the
    pipeline run doesn't (and structurally cannot) change what it
    returns, before/after a tick that creates real candidates."""
    conn, settings, repo = db
    before = get_tracked_companies(active_only=False)
    now = datetime.now(timezone.utc)
    filing = _filing("0001045810-26-000008", now.isoformat())
    filing_event_repository.upsert_filing_event(conn, filing)
    candrepo.upsert_new_candidates(conn, "SEC EDGAR", [_candidate_for(
        filing, "NVIDIA announced components supplied by Example Materials Corp. for the new facility.",
    )])

    candidate_pipeline.run_candidate_discovery_tick(settings, repo, stale_days=180, now=now)

    after = get_tracked_companies(active_only=False)
    assert before == after


def test_daily_news_text_is_also_read_and_extracted(db):
    conn, settings, repo = db
    now = datetime.now(timezone.utc)
    story = _news_story(
        "newsitem-nvidia-abc", now.isoformat(),
        "NVIDIA and Example Photonics Inc. announce strategic partnership",
    )
    sqlite_daily_news.upsert_new_stories(conn, [story])

    report = candidate_pipeline.run_candidate_discovery_tick(settings, repo, stale_days=180, now=now)

    assert report.candidates_created == 1
    record = list_candidates(conn)[0]
    assert record.issuer.legal_name == "Example Photonics Inc."
    evidence = get_evidence_for_issuer(conn, record.issuer.issuer_id)
    assert evidence[0]["source_record_id"] == "daily_news:newsitem-nvidia-abc"


def test_staleness_decay_archives_a_candidate_with_no_recent_evidence(db):
    conn, settings, repo = db
    now = datetime.now(timezone.utc)
    filing = _filing("0001045810-26-000009", now.isoformat())
    filing_event_repository.upsert_filing_event(conn, filing)
    candrepo.upsert_new_candidates(conn, "SEC EDGAR", [_candidate_for(
        filing, "NVIDIA announced components supplied by Example Stale Corp. for the new facility.",
    )])
    candidate_pipeline.run_candidate_discovery_tick(settings, repo, stale_days=180, now=now)

    much_later = now + timedelta(days=200)
    report = candidate_pipeline.run_candidate_discovery_tick(settings, repo, stale_days=180, now=much_later)

    assert report.candidates_archived == 1
    archived = list_candidates(conn, coverage_state="Archived")
    assert len(archived) == 1
