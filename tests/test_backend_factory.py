"""backend_factory — the Durable-State Phase 2A composition seam.
Everything here uses tmp_path directories and explicit Settings
construction; no test relies on the ambient environment, the real local
cache directory, the real local environment file, the Streamlit secrets
file, or the pre-existing legacy database file. No network call anywhere
in this file."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.live.radar_signal_repository import RadarSignalRepository
from src.data_access.state_db.signal_repository import SqliteSignalRepository
from src.models.models import CandidateSignal, CandidateStatus, FilingEvent, StateTransition


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _filing(rcept_no: str = "acc-1", corp_code: str = "0000000001") -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code=corp_code, corp_name="Test Co", stock_code="TST",
        report_nm="8-K", rcept_dt="2026-08-21", flr_nm="Test Co", pblntf_ty="8-K",
        theme_slug="ai-buildout", source_url="https://example.com", retrieved_at=_now(),
        source_name="SEC EDGAR", original_language="English",
    )


def _candidate(filing: FilingEvent) -> CandidateSignal:
    return CandidateSignal(
        id=f"edgar-cand-{filing.rcept_no}", filing=filing, matched_rules=["material_agreement:8-K item 1.01"],
        confidence="Moderate", status=CandidateStatus.CANDIDATE_DETECTED,
        state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at=_now())],
    )


def _json_settings(tmp_path):
    return Settings(cache_dir=tmp_path / "cache")


def _sqlite_settings(tmp_path):
    return Settings(db_backend="sqlite", state_db_path=tmp_path / "state.db")


# --- 1/2. Unset/blank/unrecognized -> JSON, unchanged existing behavior ---

def test_unset_backend_selects_json_signal_repository(tmp_path, monkeypatch):
    monkeypatch.delenv("EDGE_DB_BACKEND", raising=False)
    settings = Settings(cache_dir=tmp_path / "cache")
    repo = backend_factory.get_signal_repository(settings)
    assert isinstance(repo, RadarSignalRepository)


def test_blank_backend_selects_json(tmp_path):
    settings = replace(_json_settings(tmp_path), db_backend="")
    assert isinstance(backend_factory.get_signal_repository(settings), RadarSignalRepository)


def test_unrecognized_backend_value_selects_json(tmp_path):
    settings = replace(_json_settings(tmp_path), db_backend="not-a-real-backend")
    assert isinstance(backend_factory.get_signal_repository(settings), RadarSignalRepository)


def test_json_candidate_repository_uses_the_correct_per_source_filename(tmp_path):
    settings = _json_settings(tmp_path)
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    assert repo.filename == "edgar_candidates.json"
    repo_dart = backend_factory.get_candidate_repository(settings, "OpenDART / DART")
    assert repo_dart.filename == "dart_candidates.json"


# --- 3/4. Explicit sqlite + explicit path -> SQLite, schema migrated ---

def test_explicit_sqlite_backend_selects_sqlite_signal_repository(tmp_path):
    settings = _sqlite_settings(tmp_path)
    repo = backend_factory.get_signal_repository(settings)
    assert isinstance(repo, SqliteSignalRepository)


def test_sqlite_selection_migrates_only_the_configured_temp_database(tmp_path):
    settings = _sqlite_settings(tmp_path)
    repo = backend_factory.get_signal_repository(settings)
    # The schema exists on the connection this repository actually holds.
    tables = {
        row["name"] for row in repo._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    assert {"schema_version", "candidates", "filing_events", "state_transitions", "resolved_identifiers"} <= tables
    assert (tmp_path / "state.db").exists()


def test_sqlite_backend_case_insensitive_and_trims_whitespace(tmp_path):
    settings = replace(_sqlite_settings(tmp_path), db_backend=" SQLite ")
    assert isinstance(backend_factory.get_signal_repository(settings), SqliteSignalRepository)


# --- 5. sqlite selected without a valid path -> explicit config error, never silent JSON ---

def test_sqlite_without_state_db_path_raises_configuration_error(tmp_path):
    settings = Settings(db_backend="sqlite", state_db_path=None, cache_dir=tmp_path / "cache")
    with pytest.raises(backend_factory.BackendConfigurationError):
        backend_factory.get_signal_repository(settings)


def test_sqlite_with_blank_state_db_path_raises_configuration_error(tmp_path):
    settings = replace(_sqlite_settings(tmp_path), state_db_path="   ")
    with pytest.raises(backend_factory.BackendConfigurationError):
        backend_factory.get_candidate_repository(settings, "SEC EDGAR")


def test_config_error_never_falls_back_to_json_silently(tmp_path):
    settings = Settings(db_backend="sqlite", state_db_path=None, cache_dir=tmp_path / "cache")
    try:
        backend_factory.get_candidate_repository(settings, "SEC EDGAR")
        assert False, "expected BackendConfigurationError"
    except backend_factory.BackendConfigurationError:
        pass
    # And no JSON file was written as a side effect of the failed attempt.
    assert not (tmp_path / "cache").exists()


# --- 6/7. EDGE_STATE_DB_URL ignored; retired names have no effect ---

def test_state_db_url_is_never_read_by_the_factory(tmp_path):
    settings = replace(_sqlite_settings(tmp_path), state_db_url="not-a-real-connection-string")
    # Must not raise, parse, or attempt any connection based on this field.
    repo = backend_factory.get_signal_repository(settings)
    assert isinstance(repo, SqliteSignalRepository)


def test_retired_env_names_cannot_influence_selection(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_DB_PATH", str(tmp_path / "legacy-ignored.db"))
    monkeypatch.setenv("EDGE_DB_URL", "postgres://ignored")
    monkeypatch.delenv("EDGE_DB_BACKEND", raising=False)
    settings = Settings(cache_dir=tmp_path / "cache")
    assert settings.db_backend == "json"
    assert isinstance(backend_factory.get_signal_repository(settings), RadarSignalRepository)
    assert not (tmp_path / "legacy-ignored.db").exists()


# --- 8/9/10. Cross-backend equivalence ---

@pytest.mark.parametrize("settings_factory", [_json_settings, _sqlite_settings])
def test_candidate_retrieval_and_idempotent_insertion_equivalent_across_backends(tmp_path, settings_factory):
    settings = settings_factory(tmp_path)
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    filing = _filing()
    candidate = _candidate(filing)

    repo.upsert_new_candidates([candidate])
    repo.upsert_new_candidates([candidate])  # repeat — must not duplicate
    store = repo.load_candidates()
    assert list(store.keys()) == [candidate.id]
    assert repo.get_candidate(candidate.id).status == CandidateStatus.CANDIDATE_DETECTED


@pytest.mark.parametrize("settings_factory", [_json_settings, _sqlite_settings])
def test_review_state_update_equivalent_across_backends(tmp_path, settings_factory):
    settings = settings_factory(tmp_path)
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    filing = _filing()
    candidate = _candidate(filing)
    repo.upsert_new_candidates([candidate])

    published = replace(candidate, status=CandidateStatus.PUBLISHED, reviewed_at=_now(), reviewed_note="Approved")
    outcome = repo.update_candidate(published)
    assert outcome.status == "updated"
    assert repo.get_candidate(candidate.id).status == CandidateStatus.PUBLISHED
    assert repo.get_candidate(candidate.id).reviewed_note == "Approved"


def test_filing_event_dedup_json_backend(tmp_path):
    # JSON's real architecture has no standalone "write one filing event"
    # path outside scan_service.scan() (a live-network-calling function,
    # out of scope this phase) — candidate_store.upsert_new_candidates()
    # writes edgar_candidates.json only, never edgar_filing_events.json.
    # This seeds the filing-events file the same way scan_service.py's
    # own tests do, to test the read/exists path on its own real format.
    from src.data_access.edgar import scan_service as edgar_scan_service

    settings = _json_settings(tmp_path)
    filing = _filing()
    edgar_scan_service._save_cache(
        settings.cache_dir,
        {"seen_keys": [edgar_scan_service.dedup_key(filing.corp_code, filing.rcept_no)],
         "filing_events": [filing.__dict__], "candidate_signals": []},
    )

    filing_repo = backend_factory.get_filing_event_repository(settings, "SEC EDGAR")
    assert filing_repo.exists(filing.corp_code, filing.rcept_no) is True
    assert filing_repo.exists("nonexistent", "nonexistent") is False
    assert len(filing_repo.load_filing_events()) == 1


def test_filing_event_dedup_sqlite_backend(tmp_path):
    # SQLite's architecture is genuinely different here (a real,
    # documented divergence, not a bug): candidates.py's FK to
    # filing_events means upsert_new_candidates() DOES create the parent
    # filing_events row as part of the same transaction. This is the
    # SQLite-side equivalent proof, exercised through its own real
    # candidate-insert path rather than a manual seed, since that path
    # legitimately populates filing_events for this backend.
    settings = _sqlite_settings(tmp_path)
    candidate_repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    filing_repo = backend_factory.get_filing_event_repository(settings, "SEC EDGAR")

    filing = _filing()
    candidate_repo.upsert_new_candidates([_candidate(filing)])

    assert filing_repo.exists(filing.corp_code, filing.rcept_no) is True
    assert filing_repo.exists("nonexistent", "nonexistent") is False
    assert len(filing_repo.load_filing_events()) == 1


@pytest.mark.parametrize("settings_factory", [_json_settings, _sqlite_settings])
def test_published_candidate_derives_the_same_signal_identity_across_backends(tmp_path, settings_factory):
    settings = settings_factory(tmp_path)
    candidate_repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    filing = _filing()
    candidate = _candidate(filing)
    candidate_repo.upsert_new_candidates([candidate])
    candidate_repo.update_candidate(replace(candidate, status=CandidateStatus.PUBLISHED))

    signal_repo = backend_factory.get_signal_repository(settings)
    signals = signal_repo.get_all_signals()
    assert [s.id for s in signals] == [f"signal-{candidate.id}"]


@pytest.mark.parametrize("settings_factory", [_json_settings, _sqlite_settings])
def test_non_published_candidate_produces_no_signal_across_backends(tmp_path, settings_factory):
    settings = settings_factory(tmp_path)
    candidate_repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    filing = _filing()
    candidate_repo.upsert_new_candidates([_candidate(filing)])  # stays CANDIDATE_DETECTED

    signal_repo = backend_factory.get_signal_repository(settings)
    assert signal_repo.get_all_signals() == []


# --- Identifier repository equivalence (test area 8's "identifier mapping retrieval") ---

def test_identifier_repository_json_reads_the_real_resolver_cache_shape(tmp_path):
    from src.data_access.edgar import cik_resolver

    cache_dir = tmp_path / "cache"
    client_result_record = cik_resolver.ResolvedCik(
        cik="0000002488", company_name="ADVANCED MICRO DEVICES INC",
        source="SEC company_tickers.json + submissions cross-check", retrieved_at=_now(),
    )
    cik_resolver._save_cache(cache_dir, {"AMD": client_result_record})

    settings = Settings(cache_dir=cache_dir)
    repo = backend_factory.get_identifier_repository(settings, "SEC EDGAR")
    record = repo.get_identifier("AMD")
    assert record is not None
    assert record.identifier == "0000002488"
    assert record.display_name == "ADVANCED MICRO DEVICES INC"


def test_identifier_repository_sqlite_round_trips(tmp_path):
    from src.data_access.state_db.identifier_repository import ResolvedIdentifierRecord, upsert_resolved_identifier
    from src.data_access.state_db.connection import connect
    from src.data_access.state_db.schema import migrate

    settings = _sqlite_settings(tmp_path)
    conn = connect(settings.state_db_path)
    migrate(conn)
    upsert_resolved_identifier(
        conn, "SEC EDGAR", "AMD",
        ResolvedIdentifierRecord(
            identifier="0000002488", display_name="ADVANCED MICRO DEVICES INC",
            resolution_method="SEC company_tickers.json + submissions cross-check", retrieved_at=_now(),
        ),
    )
    conn.close()

    repo = backend_factory.get_identifier_repository(settings, "SEC EDGAR")
    record = repo.get_identifier("AMD")
    assert record.identifier == "0000002488"


# --- 11/12. Backend isolation — no cross-talk ---

def test_json_backend_never_creates_a_sqlite_database_file(tmp_path):
    db_path = tmp_path / "should-never-exist.db"
    settings = Settings(cache_dir=tmp_path / "cache", db_backend="json", state_db_path=db_path)
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    repo.upsert_new_candidates([_candidate(_filing())])
    assert not db_path.exists()


def test_sqlite_backend_never_creates_or_writes_the_json_cache_directory(tmp_path):
    cache_dir = tmp_path / "should-never-exist-cache"
    settings = Settings(cache_dir=cache_dir, db_backend="sqlite", state_db_path=tmp_path / "state.db")
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    repo.upsert_new_candidates([_candidate(_filing())])
    assert not cache_dir.exists()


# --- 7. The real composition root (container.get_repositories), exercised
# with fully explicit synthetic settings — never real process defaults ---

def test_get_repositories_with_synthetic_json_settings_selects_radar_signal_repository(tmp_path):
    from src.data_access.container import get_repositories

    settings = _json_settings(tmp_path)
    candidate_repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    published = replace(_candidate(_filing()), status=CandidateStatus.PUBLISHED)
    candidate_repo.upsert_new_candidates([published])

    ctx = get_repositories(settings)  # settings passed explicitly — get_settings() never reached
    assert isinstance(ctx.signal_repository, RadarSignalRepository)
    assert [s.id for s in ctx.signal_repository.get_all_signals()] == [f"signal-{published.id}"]


def test_get_repositories_with_synthetic_sqlite_settings_selects_sqlite_signal_repository(tmp_path):
    from src.data_access.container import get_repositories

    settings = _sqlite_settings(tmp_path)
    candidate_repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    published = replace(_candidate(_filing()), status=CandidateStatus.PUBLISHED)
    candidate_repo.upsert_new_candidates([published])

    ctx = get_repositories(settings)
    assert isinstance(ctx.signal_repository, SqliteSignalRepository)
    assert [s.id for s in ctx.signal_repository.get_all_signals()] == [f"signal-{published.id}"]


# --- 8. Source-level guard: this phase's own files must never reference
# real local state or real Signal IDs ---

_THIS_DIR = Path(__file__).resolve().parent
_PHASE2A_FILES = (
    _THIS_DIR / "test_backend_factory.py",
    _THIS_DIR.parent / "src" / "data_access" / "backend_factory.py",
    _THIS_DIR.parent / "src" / "data_access" / "container.py",
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
    """For this test file only: the forbidden-string list itself must
    contain the literal strings it checks for — that's unavoidable for a
    self-scanning guard. Strips just that one tuple definition out before
    scanning, so the rest of the file (every actual test body) is still
    fully checked."""
    text = path.read_text(encoding="utf-8")
    if path.name != "test_backend_factory.py":
        return text
    start = text.index("_FORBIDDEN_REAL_STATE_REFERENCES = (")
    end = text.index(")\n", start) + len(")\n")
    return text[:start] + text[end:]


def test_phase2a_files_never_reference_real_local_state_or_real_signal_ids():
    offenders = []
    for path in _PHASE2A_FILES:
        source = _source_excluding_this_guards_own_string_list(path)
        for forbidden in _FORBIDDEN_REAL_STATE_REFERENCES:
            if forbidden in source:
                offenders.append((path.name, forbidden))
    assert offenders == []
