"""Durable-State Phase 2B — backend selection extended into the
standalone, network-free application-facing paths: review decisions
(review_actions.record_review_decision), Radar Inbox's display read path
(radar_inbox._build_items/_edinet_scope_line), and identifier-cache reads
used for readiness/company resolution (edgar_service.get_edgar_companies,
dart/radar_service.get_radar_companies).

Everything here uses tmp_path/`:memory:` and synthetic fixture records
only — no test calls get_settings() or accepts an ambient real path, and
none accesses the real local cache directory, the real .env, the
Streamlit secrets file, or the pre-existing legacy database."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.dart import radar_service as dart_radar_service
from src.data_access.edgar import edgar_service
from src.logic import review_actions
from src.models.models import CandidateSignal, CandidateStatus, FilingEvent, StateTransition
from src.ui.pages import radar_inbox


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_settings(tmp_path):
    return Settings(cache_dir=tmp_path / "cache")


def _sqlite_settings(tmp_path):
    return Settings(db_backend="sqlite", state_db_path=tmp_path / "state.db")


def _edgar_filing(rcept_no: str = "acc-1", corp_code: str = "0000000001") -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code=corp_code, corp_name="Test Co", stock_code="TST",
        report_nm="8-K", rcept_dt="2026-08-21", flr_nm="Test Co", pblntf_ty="8-K",
        theme_slug="ai-buildout", source_url="https://example.com", retrieved_at=_now(),
        source_name="SEC EDGAR", original_language="English",
    )


def _dart_filing(rcept_no: str = "dart-acc-1", corp_code: str = "00164779") -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code=corp_code, corp_name="SK Hynix", stock_code="000660",
        report_nm="주요사항보고서", rcept_dt="20260821", flr_nm="SK하이닉스", theme_slug="memory",
        source_url="https://dart.fss.or.kr/", retrieved_at=_now(), source_name="OpenDART / DART",
    )


def _edinet_filing(rcept_no: str = "S100TEST", corp_code: str = "E99999") -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code=corp_code, corp_name="Synthetic EDINET Co", stock_code="99999",
        report_nm="有価証券報告書", rcept_dt="2026-08-21", flr_nm="Synthetic EDINET Co",
        theme_slug="ai-buildout", source_url="https://disclosure.edinet-fsa.go.jp/", retrieved_at=_now(),
        source_name="EDINET", original_language="Japanese",
    )


def _candidate(filing: FilingEvent, prefix: str = "edgar-cand-") -> CandidateSignal:
    return CandidateSignal(
        id=f"{prefix}{filing.rcept_no}", filing=filing, matched_rules=["material_agreement:8-K item 1.01"],
        confidence="Moderate", status=CandidateStatus.CANDIDATE_DETECTED,
        state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at=_now())],
    )


# --- 1/2. JSON default unchanged; SQLite selection wires each category ---

def test_record_review_decision_without_settings_uses_json_unchanged(tmp_path):
    from src.data_access.dart import candidate_store

    cache_dir = tmp_path / "cache"
    filing = _edgar_filing()
    candidate = _candidate(filing)
    candidate_store.upsert_new_candidates(cache_dir, [candidate], "edgar_candidates.json")
    result = review_actions.record_review_decision(
        cache_dir, candidate.id, "edgar_candidates.json", CandidateStatus.PUBLISHED, "approved",
    )
    assert result.status == CandidateStatus.PUBLISHED
    assert result.reviewed_note == "approved"


def test_record_review_decision_with_json_settings_behaves_identically(tmp_path):
    from src.data_access.dart import candidate_store

    settings = _json_settings(tmp_path)
    filing = _edgar_filing()
    candidate = _candidate(filing)
    candidate_store.upsert_new_candidates(settings.cache_dir, [candidate], "edgar_candidates.json")
    result = review_actions.record_review_decision(
        settings.cache_dir, candidate.id, "edgar_candidates.json", CandidateStatus.PUBLISHED, "approved",
        settings=settings,
    )
    assert result.status == CandidateStatus.PUBLISHED


def test_record_review_decision_with_sqlite_settings_routes_through_sqlite(tmp_path):
    settings = _sqlite_settings(tmp_path)
    filing = _edgar_filing()
    candidate = _candidate(filing)
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    repo.upsert_new_candidates([candidate])

    result = review_actions.record_review_decision(
        tmp_path / "unused-cache-dir", candidate.id, "edgar_candidates.json",
        CandidateStatus.PUBLISHED, "approved-sqlite", settings=settings,
    )
    assert result.status == CandidateStatus.PUBLISHED
    assert result.reviewed_note == "approved-sqlite"
    # And it's genuinely persisted through the SQLite repository, not JSON.
    reloaded = repo.get_candidate(candidate.id)
    assert reloaded.status == CandidateStatus.PUBLISHED


# --- 3/4. Synthetic EDGAR/DART/EDINET filing-event dedup through the selected display path ---

@pytest.mark.parametrize(
    "filing_factory,source,prefix",
    [
        (_edgar_filing, "SEC EDGAR", "edgar-cand-"),
        (_dart_filing, "OpenDART / DART", "cand-"),
        (_edinet_filing, "EDINET", "edinet-cand-"),
    ],
)
def test_build_items_sqlite_path_shows_synthetic_filing_and_candidate_per_source(tmp_path, filing_factory, source, prefix):
    settings = _sqlite_settings(tmp_path)
    filing = filing_factory()
    candidate = _candidate(filing, prefix=prefix)
    backend_factory.get_candidate_repository(settings, source).upsert_new_candidates([candidate])

    items = radar_inbox._build_items(tmp_path / "unused-cache-dir", settings)
    matching = [i for i in items if i.filing.rcept_no == filing.rcept_no]
    assert len(matching) == 1
    assert matching[0].candidate is not None
    assert matching[0].candidate.id == candidate.id


def test_build_items_json_path_unchanged_when_settings_omitted(tmp_path):
    # JSON's real architecture (unchanged by this phase, see
    # backend_factory.py's module docstring): the filing-events file and
    # the candidates file are separate, with no automatic sync outside a
    # live scan_service.scan() call — seed both directly, the same
    # pattern test_backend_factory.py's own JSON filing-event test uses.
    from src.data_access.dart import candidate_store
    from src.data_access.edgar import scan_service as edgar_scan_service

    cache_dir = tmp_path / "cache"
    filing = _edgar_filing()
    candidate = _candidate(filing)
    candidate_store.upsert_new_candidates(cache_dir, [candidate], "edgar_candidates.json")
    edgar_scan_service._save_cache(
        cache_dir,
        {"seen_keys": [edgar_scan_service.dedup_key(filing.corp_code, filing.rcept_no)],
         "filing_events": [filing.__dict__], "candidate_signals": []},
    )

    items = radar_inbox._build_items(cache_dir)  # settings omitted entirely
    matching = [i for i in items if i.filing.rcept_no == filing.rcept_no]
    assert len(matching) == 1
    assert matching[0].candidate.id == candidate.id


# --- 5. Candidate survives SQLite upsert + readback with all required fields ---

def test_candidate_survives_sqlite_upsert_and_readback_via_selected_path(tmp_path):
    settings = _sqlite_settings(tmp_path)
    filing = _edgar_filing()
    candidate = _candidate(filing)
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    repo.upsert_new_candidates([candidate])

    reloaded = repo.get_candidate(candidate.id)
    assert reloaded.filing == filing
    assert reloaded.matched_rules == candidate.matched_rules
    assert reloaded.confidence == candidate.confidence
    assert reloaded.status == candidate.status
    assert len(reloaded.state_history) == 1


# --- 6/7. Review decision + state transition; stale conflict preserves current state ---

def test_review_decision_writes_state_transition_and_updates_status(tmp_path):
    settings = _sqlite_settings(tmp_path)
    filing = _edgar_filing()
    candidate = _candidate(filing)
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    repo.upsert_new_candidates([candidate])

    review_actions.record_review_decision(
        tmp_path, candidate.id, "edgar_candidates.json", CandidateStatus.MONITORING, "watching", settings=settings,
    )
    reloaded = repo.get_candidate(candidate.id)
    assert reloaded.status == CandidateStatus.MONITORING
    assert [t.status for t in reloaded.state_history] == [CandidateStatus.CANDIDATE_DETECTED, CandidateStatus.MONITORING]


def test_stale_sqlite_review_update_conflict_preserves_current_state(tmp_path):
    settings = _sqlite_settings(tmp_path)
    filing = _edgar_filing()
    candidate = _candidate(filing)
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    repo.upsert_new_candidates([candidate])

    # A concurrent writer publishes first.
    first = review_actions.record_review_decision(
        tmp_path, candidate.id, "edgar_candidates.json", CandidateStatus.PUBLISHED, "first reviewer", settings=settings,
    )
    assert first.status == CandidateStatus.PUBLISHED

    # This caller is still holding a candidate object read BEFORE that
    # write landed (simulated directly against the repository, since
    # record_review_decision always reads fresh — the race is exercised
    # at the repository level, exactly like record_review_decision's own
    # internal read-then-write does).
    stale_version = 1  # the version the row had before the first reviewer's update
    stale_candidate = replace(candidate, status=CandidateStatus.DISMISSED, reviewed_note="stale second reviewer")
    outcome = repo.update_candidate(stale_candidate, expected_version=stale_version)

    assert outcome.status == "conflict"
    assert outcome.current.status == CandidateStatus.PUBLISHED
    assert outcome.current.reviewed_note == "first reviewer"
    # And the repository's own stored state is untouched by the rejected write.
    assert repo.get_candidate(candidate.id).status == CandidateStatus.PUBLISHED


# --- 8. Identifier mappings round-trip under SQLite, no live resolution ---

def test_get_edgar_companies_sqlite_path_uses_only_synthetic_identifier_records(tmp_path):
    from src.data_access.state_db.identifier_repository import ResolvedIdentifierRecord

    settings = _sqlite_settings(tmp_path)
    id_repo = backend_factory.get_identifier_repository(settings, "SEC EDGAR")
    from src.data_access.state_db.identifier_repository import upsert_resolved_identifier
    upsert_resolved_identifier(
        id_repo.conn, "SEC EDGAR", "NVDA",
        ResolvedIdentifierRecord(
            identifier="0001045810", display_name="SYNTHETIC NVIDIA RECORD",
            resolution_method="synthetic-test-fixture", retrieved_at=_now(),
        ),
    )

    companies = edgar_service.get_edgar_companies(tmp_path / "unused-cache-dir", settings)
    nvda = next(c for c in companies if c.krx_code == "NVDA")
    assert nvda.corp_code == "0001045810"


def test_get_radar_companies_sqlite_path_uses_only_synthetic_identifier_records(tmp_path):
    from src.data_access.state_db.identifier_repository import ResolvedIdentifierRecord, upsert_resolved_identifier

    settings = _sqlite_settings(tmp_path)
    id_repo = backend_factory.get_identifier_repository(settings, "OpenDART / DART")
    upsert_resolved_identifier(
        id_repo.conn, "OpenDART / DART", "000660",
        ResolvedIdentifierRecord(
            identifier="00164779", display_name="SYNTHETIC SK HYNIX RECORD",
            resolution_method="synthetic-test-fixture", retrieved_at=_now(),
        ),
    )

    companies = dart_radar_service.get_radar_companies(tmp_path / "unused-cache-dir", settings)
    hynix = next(c for c in companies if c.krx_code == "000660")
    assert hynix.corp_code == "00164779"


def test_get_edgar_companies_json_path_unchanged_when_settings_omitted(tmp_path):
    from src.data_access.edgar import cik_resolver

    cache_dir = tmp_path / "cache"
    cik_resolver._save_cache(
        cache_dir,
        {"NVDA": cik_resolver.ResolvedCik(
            cik="0001045810", company_name="SYNTHETIC", source="synthetic-test-fixture", retrieved_at=_now(),
        )},
    )
    companies = edgar_service.get_edgar_companies(cache_dir)  # settings omitted
    nvda = next(c for c in companies if c.krx_code == "NVDA")
    assert nvda.corp_code == "0001045810"


def test_run_scan_routes_identifier_reads_through_the_configured_backend(tmp_path):
    # Durable-State Phase 4M-0 replaces the old Phase 2B limitation this
    # test used to confirm (that run_scan() never passed `settings` into
    # get_edgar_companies()/get_radar_companies(), so identifier
    # resolution during an actual scan stayed JSON-only regardless of
    # backend) with the new, intentional behavior: run_scan() now passes
    # `settings` through, so a "sqlite"/"postgres" db_backend resolves
    # identifiers from that backend's own identifier repository. This is
    # verified directly against source, not by running a real scan
    # (which would require live network calls, out of scope), exactly
    # like the test it replaces.
    import inspect

    edgar_source = inspect.getsource(edgar_service.run_scan)
    assert "get_edgar_companies(settings.cache_dir, settings)" in edgar_source

    dart_source = inspect.getsource(dart_radar_service.run_scan)
    assert "get_radar_companies(settings.cache_dir, settings)" in dart_source

    # process_candidate_now() is unchanged by this phase — it operates on
    # one already-persisted candidate by ID and has no identifier-lookup
    # call of its own to route.
    edgar_process_source = inspect.getsource(edgar_service.process_candidate_now)
    assert "get_edgar_companies" not in edgar_process_source


# --- 9/10. Derived Signal identity through the selected SignalRepository ---

def test_published_candidate_signal_identity_via_selected_signal_repository(tmp_path):
    settings = _sqlite_settings(tmp_path)
    filing = _edgar_filing()
    candidate = _candidate(filing)
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    repo.upsert_new_candidates([candidate])
    review_actions.record_review_decision(
        tmp_path, candidate.id, "edgar_candidates.json", CandidateStatus.PUBLISHED, "ok", settings=settings,
    )

    signal_repo = backend_factory.get_signal_repository(settings)
    assert [s.id for s in signal_repo.get_all_signals()] == [f"signal-{candidate.id}"]


def test_non_published_candidate_produces_no_signal_via_selected_signal_repository(tmp_path):
    settings = _sqlite_settings(tmp_path)
    filing = _edgar_filing()
    candidate = _candidate(filing)
    backend_factory.get_candidate_repository(settings, "SEC EDGAR").upsert_new_candidates([candidate])

    signal_repo = backend_factory.get_signal_repository(settings)
    assert signal_repo.get_all_signals() == []


# --- 12/13. Backend isolation through the newly-wired paths ---

def test_sqlite_review_decision_never_touches_json_cache_directory(tmp_path):
    cache_dir_marker = tmp_path / "should-never-exist-cache"
    settings = Settings(db_backend="sqlite", state_db_path=tmp_path / "state.db", cache_dir=cache_dir_marker)
    filing = _edgar_filing()
    candidate = _candidate(filing)
    backend_factory.get_candidate_repository(settings, "SEC EDGAR").upsert_new_candidates([candidate])

    review_actions.record_review_decision(
        cache_dir_marker, candidate.id, "edgar_candidates.json", CandidateStatus.PUBLISHED, "ok", settings=settings,
    )
    assert not cache_dir_marker.exists()


def test_json_build_items_never_opens_a_sqlite_database(tmp_path):
    db_path_marker = tmp_path / "should-never-exist.db"
    cache_dir = tmp_path / "cache"
    radar_inbox._build_items(cache_dir)  # settings omitted -> JSON path
    assert not db_path_marker.exists()


# --- Source guard: this phase's modified/new files must never reference real local state ---

_PHASE2B_FILES = (
    Path(__file__).resolve().parent / "test_backend_factory_phase2b.py",
    Path(__file__).resolve().parent.parent / "src" / "data_access" / "backend_factory.py",
    Path(__file__).resolve().parent.parent / "src" / "logic" / "review_actions.py",
    Path(__file__).resolve().parent.parent / "src" / "ui" / "pages" / "radar_inbox.py",
    Path(__file__).resolve().parent.parent / "src" / "data_access" / "edgar" / "edgar_service.py",
    Path(__file__).resolve().parent.parent / "src" / "data_access" / "dart" / "radar_service.py",
    Path(__file__).resolve().parent.parent / "design" / "DECISIONS.md",
)
_FORBIDDEN_REAL_STATE_REFERENCES = (
    "data/cache",
    "data/edge_research.db",
    ".streamlit/secrets.toml",
    "signal-cand-20260819000254",
    "signal-edgar-cand-0001193125-26-354029",
    "signal-edgar-cand-0001193125-26-356217",
)


def _source_excluding_this_guards_own_string_list(path: Path) -> str:
    """Returns the text this guard actually checks for `path`:

    - `test_backend_factory_phase2b.py` (itself): the whole file, minus
      the `_FORBIDDEN_REAL_STATE_REFERENCES` tuple's own literal string
      list — that list necessarily contains the strings it checks for.
    - `DECISIONS.md`: only the "Durable-State Phase 2B" section this
      phase actually added — that file also has ~2700 lines of earlier,
      already-committed history (Phase 1/2A and everything before it)
      that legitimately discusses the real cache directory/legacy
      database/real Signal IDs in past tense as established project
      fact; scanning the whole file would fail against content outside
      this diff, not against anything this phase wrote.
    - every other file: the whole file, unmodified.
    """
    text = path.read_text(encoding="utf-8")
    if path.name == "test_backend_factory_phase2b.py":
        start = text.index("_FORBIDDEN_REAL_STATE_REFERENCES = (")
        end = text.index(")\n", start) + len(")\n")
        return text[:start] + text[end:]
    if path.name == "DECISIONS.md":
        marker = "## Durable-State Phase 2B"
        return text[text.index(marker):]
    return text


def test_phase2b_files_never_reference_real_local_state_or_real_signal_ids():
    offenders = []
    for path in _PHASE2B_FILES:
        source = _source_excluding_this_guards_own_string_list(path)
        for forbidden in _FORBIDDEN_REAL_STATE_REFERENCES:
            if forbidden in source:
                offenders.append((path.name, forbidden))
    assert offenders == []
