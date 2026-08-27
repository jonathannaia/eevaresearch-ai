"""Durable-State Phase 4B — direct backend_factory.py Postgres selection,
JSON-default preservation, connection-error sanitization, and a
concurrent human-review-vs-scan conflict scenario, against the real
local disposable Postgres test container where a real connection is
genuinely required. Pure routing/fail-closed tests need no real
connection at all and run unconditionally.

Also carries this phase's real-local-state and non-loopback-host guard,
covering every new src/data_access/postgres_state_db/ file and every new
Postgres test file — the same discipline established in
test_backend_factory_phase2b.py / test_candidate_persistence_phase3a.py
/ test_edgar_service.py, extended here to also forbid any host literal
other than 127.0.0.1/localhost, since a network-database backend is a
new risk surface those earlier guards never needed to cover."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.postgres_state_db.candidate_repository import UpdateOutcome as PostgresUpdateOutcome
from src.data_access.postgres_state_db.signal_repository import PostgresSignalRepository
from src.logic import review_actions
from src.models.models import CandidateSignal, CandidateStatus, FilingEvent, StateTransition

from tests._postgres_test_support import pg_isolated_dsn  # noqa: F401 (fixture import)


def _postgres_settings(dsn: str) -> Settings:
    return Settings(db_backend="postgres", state_db_url=dsn)


def _filing(rcept_no: str = "0001045810-26-000001") -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="0000045810", corp_name="NVIDIA", stock_code="NVDA",
        report_nm="8-K", rcept_dt="20260101", flr_nm="NVIDIA", source_name="SEC EDGAR",
        original_language="English", theme_slug="ai-buildout",
    )


def _candidate(candidate_id: str, filing: FilingEvent, status: CandidateStatus = CandidateStatus.CANDIDATE_DETECTED) -> CandidateSignal:
    return CandidateSignal(
        id=candidate_id, filing=filing, matched_rules=["earnings"], confidence="High", status=status,
        state_history=[StateTransition(status=status, at="2026-01-01T00:00:00+00:00")],
    )


# ---------------------------------------------------------------------------
# Pure routing / fail-closed tests — no real connection needed at all.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend_value", [None, "", "unrecognized-value"])
def test_unset_blank_or_unrecognized_backend_never_selects_postgres(backend_value, tmp_path):
    kwargs = {"cache_dir": tmp_path}
    if backend_value is not None:
        kwargs["db_backend"] = backend_value
    settings = Settings(**kwargs)
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    assert not isinstance(repo, backend_factory.PostgresCandidateRepository)
    signal_repo = backend_factory.get_signal_repository(settings)
    assert not isinstance(signal_repo, PostgresSignalRepository)


def test_explicit_postgres_backend_with_no_state_db_url_raises_configuration_error():
    settings = Settings(db_backend="postgres", state_db_url=None)
    with pytest.raises(backend_factory.BackendConfigurationError):
        backend_factory.get_candidate_repository(settings, "SEC EDGAR")


def test_explicit_postgres_backend_with_blank_state_db_url_raises_configuration_error():
    settings = Settings(db_backend="postgres", state_db_url="   ")
    with pytest.raises(backend_factory.BackendConfigurationError):
        backend_factory.get_candidate_repository(settings, "SEC EDGAR")


def test_postgres_config_error_never_falls_back_to_json_silently():
    """A malformed postgres selection must raise, never quietly return a
    working JSON-backed repository instead — the same fail-closed
    guarantee already proven for sqlite in test_backend_factory.py."""
    settings = Settings(db_backend="postgres", state_db_url=None)
    with pytest.raises(backend_factory.BackendConfigurationError):
        backend_factory.get_signal_repository(settings)


def test_postgres_connection_failure_is_sanitized_no_dsn_host_or_credentials():
    """A real connection attempt to an unreachable target (a closed
    loopback port — never the actual disposable test container) must
    fail fast and be reported with only the exception's class name.
    Runs unconditionally: this deliberately-wrong target is unreachable
    whether or not the real disposable container happens to be running."""
    bad_dsn = "host=127.0.0.1 port=1 dbname=x user=y password=REDACTED_TEST_ONLY connect_timeout=1"
    settings = Settings(db_backend="postgres", state_db_url=bad_dsn)
    with pytest.raises(backend_factory.BackendConfigurationError) as exc_info:
        backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    message = str(exc_info.value)
    assert "127.0.0.1" not in message
    assert "port=1" not in message
    assert "REDACTED_TEST_ONLY" not in message
    assert "dbname=x" not in message


# ---------------------------------------------------------------------------
# Real-connection tests — require the local disposable Postgres container.
# ---------------------------------------------------------------------------


def test_explicit_postgres_backend_selects_postgres_repositories(pg_isolated_dsn):
    settings = _postgres_settings(pg_isolated_dsn)
    candidate_repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    filing_repo = backend_factory.get_filing_event_repository(settings, "SEC EDGAR")
    identifier_repo = backend_factory.get_identifier_repository(settings, "SEC EDGAR")
    signal_repo = backend_factory.get_signal_repository(settings)
    assert isinstance(candidate_repo, backend_factory.PostgresCandidateRepository)
    assert isinstance(filing_repo, backend_factory.PostgresFilingEventRepository)
    assert isinstance(identifier_repo, backend_factory.PostgresIdentifierRepository)
    assert isinstance(signal_repo, PostgresSignalRepository)


def test_explicit_postgres_backend_selects_postgres_scan_status_repository(pg_isolated_dsn):
    """Durable-State Phase 4M-0 — sibling of
    test_backend_factory_scan_status.py's own sqlite-side proof, for the
    real Postgres path. get_scan_status_repository() has no JSON branch
    at all (see backend_factory.py's own docstring); that no-JSON
    behavior is proven unconditionally in test_backend_factory_scan_status.py
    and not repeated here."""
    from src.data_access.postgres_state_db.scan_status_repository import ProviderScanStatus as PgProviderScanStatus

    settings = _postgres_settings(pg_isolated_dsn)
    repo = backend_factory.get_scan_status_repository(settings)
    assert isinstance(repo, backend_factory.PostgresScanStatusRepository)
    assert repo.get_scan_status("SEC EDGAR") is None

    status = PgProviderScanStatus(
        provider="SEC EDGAR", cursor_value="20260101", started_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:01:00+00:00", last_successful_at="2026-01-01T00:01:00+00:00",
        items_discovered=1, candidates_created=1, skipped_unresolved_count=0, failure_code=None,
        updated_at="2026-01-01T00:01:00+00:00",
    )
    repo.upsert_scan_status(status)
    assert repo.get_scan_status("SEC EDGAR") == status
    assert repo.get_all_scan_statuses() == {"SEC EDGAR": status}


def test_candidate_survives_postgres_upsert_and_readback_via_selected_path(pg_isolated_dsn):
    settings = _postgres_settings(pg_isolated_dsn)
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    repo.upsert_new_candidates([_candidate("cand-1", _filing())])
    loaded = repo.get_candidate("cand-1")
    assert loaded is not None
    assert loaded.status == CandidateStatus.CANDIDATE_DETECTED


def test_published_candidate_derives_the_same_signal_identity_via_postgres_backend(pg_isolated_dsn):
    settings = _postgres_settings(pg_isolated_dsn)
    candidate_repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    candidate_repo.upsert_new_candidates([_candidate("cand-1", _filing(), status=CandidateStatus.PUBLISHED)])
    signal_repo = backend_factory.get_signal_repository(settings)
    signals = signal_repo.get_all_signals()
    assert [s.id for s in signals] == ["signal-cand-1"]


def test_non_published_candidate_produces_no_signal_via_postgres_backend(pg_isolated_dsn):
    settings = _postgres_settings(pg_isolated_dsn)
    candidate_repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    candidate_repo.upsert_new_candidates([_candidate("cand-1", _filing(), status=CandidateStatus.NEEDS_REVIEW)])
    signal_repo = backend_factory.get_signal_repository(settings)
    assert signal_repo.get_all_signals() == []


def test_stale_postgres_review_update_conflict_preserves_current_state(pg_isolated_dsn):
    """Direct reproduction of test_backend_factory_phase2b.py's own
    test_stale_sqlite_review_update_conflict_preserves_current_state,
    for Postgres — a concurrent human-review-vs-scan race: reviewer A
    reads version=1, reviewer/scan B writes first (version becomes 2),
    then A's stale write must be rejected, never silently overwriting
    B's already-applied change."""
    settings = _postgres_settings(pg_isolated_dsn)
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    repo.upsert_new_candidates([_candidate("cand-1", _filing())])

    reader_a = repo.get_candidate("cand-1")
    version_a = repo.get_candidate_version("cand-1")

    # A second writer (e.g. the scan pipeline advancing the candidate)
    # commits first.
    reader_b = repo.get_candidate("cand-1")
    reader_b.status = CandidateStatus.NEEDS_REVIEW
    outcome_b = repo.update_candidate(reader_b, expected_version=version_a)
    assert outcome_b.status == "updated"

    # A's now-stale write must be rejected.
    reader_a.status = CandidateStatus.DISMISSED
    outcome_a = repo.update_candidate(reader_a, expected_version=version_a)
    assert outcome_a.status == "conflict"
    assert outcome_a.current.status == CandidateStatus.NEEDS_REVIEW


def test_review_actions_record_review_decision_is_wired_to_postgres():
    """Durable-State Phase 4J-1 replaces the old boundary this test used
    to confirm (that "postgres" was deliberately absent from
    record_review_decision()'s source) with the new, intentional one:
    both "sqlite" and "postgres" are now recognized backend values,
    routed through the same shared repository-backed branch — not two
    separate hand-rolled branches — via backend_factory.get_candidate_repository(),
    never direct SQL. Verified directly rather than assumed, exactly like
    the test it replaces."""
    import inspect

    source = inspect.getsource(review_actions.record_review_decision)
    assert '"sqlite"' in source
    assert '"postgres"' in source
    assert "get_candidate_repository" in source


def test_record_review_decision_with_postgres_settings_routes_through_postgres_not_json(pg_isolated_dsn, tmp_path):
    """Direct sibling of test_backend_factory_phase2b.py's own
    test_record_review_decision_with_sqlite_settings_routes_through_sqlite
    — proves the new Postgres route is actually taken, not a silent
    fall-through to the JSON path (which would still "work" against an
    empty tmp_path cache_dir/filename, but would not persist through the
    Postgres repository at all)."""
    settings = _postgres_settings(pg_isolated_dsn)
    filing = _filing()
    candidate = _candidate("cand-1", filing, status=CandidateStatus.CANDIDATE_DETECTED)
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    repo.upsert_new_candidates([candidate])

    result = review_actions.record_review_decision(
        tmp_path / "unused-cache-dir", candidate.id, "edgar_candidates.json",
        CandidateStatus.PUBLISHED, "approved-postgres", settings=settings,
    )
    assert result is not None
    assert result.status == CandidateStatus.PUBLISHED
    assert result.reviewed_note == "approved-postgres"
    # Genuinely persisted through the Postgres repository, not JSON — and
    # no JSON cache file was ever created for this call.
    reloaded = repo.get_candidate(candidate.id)
    assert reloaded.status == CandidateStatus.PUBLISHED
    assert not (tmp_path / "unused-cache-dir").exists()


@pytest.mark.parametrize("status", [CandidateStatus.PUBLISHED, CandidateStatus.MONITORING, CandidateStatus.DISMISSED])
def test_record_review_decision_postgres_sets_status_reviewed_fields_and_appends_transition(pg_isolated_dsn, tmp_path, status):
    settings = _postgres_settings(pg_isolated_dsn)
    filing = _filing()
    candidate = _candidate("cand-1", filing, status=CandidateStatus.CANDIDATE_DETECTED)
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    repo.upsert_new_candidates([candidate])

    result = review_actions.record_review_decision(
        tmp_path, candidate.id, "edgar_candidates.json", status, "reviewer note", settings=settings,
    )
    assert result.status == status
    assert result.reviewed_at is not None
    assert result.reviewed_note == "reviewer note"
    assert [t.status for t in result.state_history] == [CandidateStatus.CANDIDATE_DETECTED, status]
    assert result.state_history[-1].detail == "reviewer note"


def test_record_review_decision_postgres_published_appears_in_signal_repository(pg_isolated_dsn, tmp_path):
    settings = _postgres_settings(pg_isolated_dsn)
    filing = _filing()
    candidate = _candidate("cand-1", filing, status=CandidateStatus.CANDIDATE_DETECTED)
    backend_factory.get_candidate_repository(settings, "SEC EDGAR").upsert_new_candidates([candidate])

    review_actions.record_review_decision(
        tmp_path, candidate.id, "edgar_candidates.json", CandidateStatus.PUBLISHED, settings=settings,
    )

    signals = backend_factory.get_signal_repository(settings).get_all_signals()
    assert [s.id for s in signals] == ["signal-cand-1"]


@pytest.mark.parametrize("status", [CandidateStatus.MONITORING, CandidateStatus.DISMISSED])
def test_record_review_decision_postgres_non_published_absent_from_signal_repository(pg_isolated_dsn, tmp_path, status):
    settings = _postgres_settings(pg_isolated_dsn)
    filing = _filing()
    candidate = _candidate("cand-1", filing, status=CandidateStatus.CANDIDATE_DETECTED)
    backend_factory.get_candidate_repository(settings, "SEC EDGAR").upsert_new_candidates([candidate])

    review_actions.record_review_decision(
        tmp_path, candidate.id, "edgar_candidates.json", status, settings=settings,
    )

    signals = backend_factory.get_signal_repository(settings).get_all_signals()
    assert signals == []


def test_record_review_decision_postgres_invalid_status_raises_and_writes_nothing(pg_isolated_dsn, tmp_path):
    settings = _postgres_settings(pg_isolated_dsn)
    filing = _filing()
    candidate = _candidate("cand-1", filing, status=CandidateStatus.CANDIDATE_DETECTED)
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    repo.upsert_new_candidates([candidate])

    with pytest.raises(ValueError):
        review_actions.record_review_decision(
            tmp_path, candidate.id, "edgar_candidates.json", CandidateStatus.NEEDS_REVIEW, settings=settings,
        )

    unchanged = repo.get_candidate(candidate.id)
    assert unchanged.status == CandidateStatus.CANDIDATE_DETECTED
    assert len(unchanged.state_history) == 1


def test_record_review_decision_postgres_unknown_candidate_id_returns_none(pg_isolated_dsn, tmp_path):
    settings = _postgres_settings(pg_isolated_dsn)
    result = review_actions.record_review_decision(
        tmp_path, "cand-does-not-exist", "edgar_candidates.json", CandidateStatus.PUBLISHED, settings=settings,
    )
    assert result is None


def test_stale_postgres_review_decision_conflict_via_record_review_decision_preserves_current_state(pg_isolated_dsn, tmp_path):
    """Sibling of test_backend_factory_phase2b.py's own
    test_stale_sqlite_review_update_conflict_preserves_current_state, for
    Postgres, exercised through record_review_decision() itself (not just
    the raw repository, which test_stale_postgres_review_update_conflict_preserves_current_state
    above already covers) — a stale concurrent writer's update must never
    overwrite a decision record_review_decision already committed."""
    settings = _postgres_settings(pg_isolated_dsn)
    filing = _filing()
    candidate = _candidate("cand-1", filing, status=CandidateStatus.CANDIDATE_DETECTED)
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    repo.upsert_new_candidates([candidate])
    stale_version = repo.get_candidate_version(candidate.id)

    first = review_actions.record_review_decision(
        tmp_path, candidate.id, "edgar_candidates.json", CandidateStatus.PUBLISHED, "first reviewer", settings=settings,
    )
    assert first.status == CandidateStatus.PUBLISHED

    # A second, stale writer — still holding the pre-review version —
    # attempts a conflicting decision directly against the repository.
    stale_candidate = repo.get_candidate(candidate.id)
    stale_candidate.status = CandidateStatus.DISMISSED
    stale_candidate.reviewed_note = "stale second reviewer"
    outcome = repo.update_candidate(stale_candidate, expected_version=stale_version)

    assert outcome.status == "conflict"
    assert outcome.current.status == CandidateStatus.PUBLISHED
    assert outcome.current.reviewed_note == "first reviewer"
    assert repo.get_candidate(candidate.id).status == CandidateStatus.PUBLISHED


def test_postgres_backend_never_creates_or_writes_the_json_cache_directory(pg_isolated_dsn, tmp_path):
    settings = Settings(
        db_backend="postgres", state_db_url=pg_isolated_dsn, cache_dir=tmp_path / "should-never-be-created",
    )
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    repo.upsert_new_candidates([_candidate("cand-1", _filing())])
    assert not settings.cache_dir.exists()


# ---------------------------------------------------------------------------
# Durable-State Phase 4E-1 — hosted PUBLISHED-only Signal repository proof.
# Every assertion below reads exclusively through get_all_signals()/
# get_signals_for_theme(); fixture setup uses only the existing,
# already-tested candidate_repository.upsert_new_candidates() write path.
# No UI, container.py, or backend_factory.py behavior is touched or
# implied by any test here — this proves the repository-layer guarantee
# only, exactly as scoped in design/DECISIONS.md's Phase 4E-1 record.
# ---------------------------------------------------------------------------


def _signal_filing(rcept_no: str, theme_slug: str, corp_name: str, source_url: str) -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code=f"{rcept_no}-cik", corp_name=corp_name, stock_code="ZZTST",
        report_nm="8-K", rcept_dt="20260101", flr_nm=corp_name, source_name="SEC EDGAR",
        original_language="English", theme_slug=theme_slug, source_url=source_url,
    )


def test_only_published_candidate_produces_a_signal_across_every_other_status(pg_isolated_dsn):
    settings = _postgres_settings(pg_isolated_dsn)
    candidate_repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")

    non_published_statuses = [
        CandidateStatus.NEEDS_REVIEW,
        CandidateStatus.PROCESSING_DEFERRED,
        CandidateStatus.DISMISSED,
        CandidateStatus.PARSE_FAILED,
        CandidateStatus.RETRIEVAL_FAILED,
    ]
    non_published_candidates = [
        _candidate(
            f"cand-nonpub-{i}",
            _signal_filing(f"rcept-nonpub-{i}", "ai-buildout", f"NONPUB-CORP-{i}", f"https://example.invalid/nonpub-{i}"),
            status=status,
        )
        for i, status in enumerate(non_published_statuses)
    ]
    published_candidate = _candidate(
        "cand-pub-1",
        _signal_filing("rcept-pub-1", "ai-buildout", "PUBLISHED-CORP", "https://example.invalid/pub-1"),
        status=CandidateStatus.PUBLISHED,
    )

    candidate_repo.upsert_new_candidates(non_published_candidates + [published_candidate])

    signal_repo = backend_factory.get_signal_repository(settings)
    assert isinstance(signal_repo, PostgresSignalRepository)  # explicit hosted path only, per this phase's own requirement
    signals = signal_repo.get_all_signals()

    assert [s.id for s in signals] == ["signal-cand-pub-1"]
    result_text = " ".join(f"{s.issuer} {s.excerpt} {s.source_url} {s.title_native}" for s in signals)
    for i in range(len(non_published_statuses)):
        assert f"NONPUB-CORP-{i}" not in result_text
        assert f"nonpub-{i}" not in result_text


def test_published_signal_preserves_permitted_fields(pg_isolated_dsn):
    settings = _postgres_settings(pg_isolated_dsn)
    candidate_repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    filing = _signal_filing("rcept-field-1", "photonics", "FIELD-PRESERVE-CORP", "https://example.invalid/field-1")
    candidate = _candidate("cand-field-1", filing, status=CandidateStatus.PUBLISHED)
    candidate_repo.upsert_new_candidates([candidate])

    signal_repo = backend_factory.get_signal_repository(settings)
    signals = signal_repo.get_all_signals()
    assert len(signals) == 1
    signal = signals[0]

    assert signal.source_name == filing.source_name
    assert signal.issuer == filing.corp_name
    assert signal.source_url == filing.source_url
    assert signal.theme_slug == filing.theme_slug
    assert signal.title_native == filing.report_nm
    assert signal.original_language == filing.original_language
    # No document was extracted/translated in this synthetic fixture —
    # excerpt/title_translated correctly stay None, never fabricated.
    assert signal.excerpt == candidate.excerpt_original
    assert signal.title_translated is None
    # No real tracked company has stock_code "ZZTST" — exchange_symbol
    # correctly stays None rather than a guessed match.
    assert signal.exchange_symbol is None
    assert signal.direction is not None
    assert signal.strength is not None
    assert signal.horizon is not None
    assert signal.last_updated


def test_get_all_signals_empty_in_a_clean_database_with_no_published_candidates(pg_isolated_dsn):
    settings = _postgres_settings(pg_isolated_dsn)
    signal_repo = backend_factory.get_signal_repository(settings)
    assert signal_repo.get_all_signals() == []


def test_get_signals_for_theme_returns_only_published_signals_in_that_theme(pg_isolated_dsn):
    settings = _postgres_settings(pg_isolated_dsn)
    candidate_repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")

    theme_a_published = _candidate(
        "cand-theme-a-pub",
        _signal_filing("rcept-theme-a-pub", "ai-buildout", "THEME-A-PUB-CORP", "https://example.invalid/theme-a-pub"),
        status=CandidateStatus.PUBLISHED,
    )
    theme_b_published = _candidate(
        "cand-theme-b-pub",
        _signal_filing("rcept-theme-b-pub", "memory", "THEME-B-PUB-CORP", "https://example.invalid/theme-b-pub"),
        status=CandidateStatus.PUBLISHED,
    )
    theme_a_not_published = _candidate(
        "cand-theme-a-nonpub",
        _signal_filing("rcept-theme-a-nonpub", "ai-buildout", "THEME-A-NONPUB-CORP", "https://example.invalid/theme-a-nonpub"),
        status=CandidateStatus.NEEDS_REVIEW,
    )

    candidate_repo.upsert_new_candidates([theme_a_published, theme_b_published, theme_a_not_published])

    signal_repo = backend_factory.get_signal_repository(settings)
    theme_a_signals = signal_repo.get_signals_for_theme("ai-buildout")

    assert [s.id for s in theme_a_signals] == ["signal-cand-theme-a-pub"]
    result_text = " ".join(s.issuer for s in theme_a_signals)
    assert "THEME-B-PUB-CORP" not in result_text
    assert "THEME-A-NONPUB-CORP" not in result_text


def test_signal_repository_connection_failure_is_sanitized_no_dsn_host_or_credentials():
    """Same fail-closed/sanitization proof as
    test_postgres_connection_failure_is_sanitized_no_dsn_host_or_credentials
    above, for get_signal_repository() specifically — the entry point this
    phase's hosted-read design actually uses. Runs unconditionally: this
    deliberately-wrong target is unreachable whether or not the real
    disposable container happens to be running."""
    bad_dsn = "host=127.0.0.1 port=1 dbname=x user=y password=REDACTED_TEST_ONLY connect_timeout=1"
    settings = Settings(db_backend="postgres", state_db_url=bad_dsn)
    with pytest.raises(backend_factory.BackendConfigurationError) as exc_info:
        backend_factory.get_signal_repository(settings)
    message = str(exc_info.value)
    assert "127.0.0.1" not in message
    assert "port=1" not in message
    assert "REDACTED_TEST_ONLY" not in message
    assert "dbname=x" not in message


# ---------------------------------------------------------------------------
# Real-local-state and non-loopback-host guard — covers every new
# postgres_state_db package file and every new Postgres test file.
# ---------------------------------------------------------------------------

_NEW_POSTGRES_FILES = (
    Path(__file__).resolve(),
    Path(__file__).resolve().parent / "_postgres_test_support.py",
    Path(__file__).resolve().parent / "test_state_db_postgres_schema.py",
    Path(__file__).resolve().parent / "test_state_db_postgres_filing_event_repository.py",
    Path(__file__).resolve().parent / "test_state_db_postgres_candidate_repository.py",
    Path(__file__).resolve().parent / "test_state_db_postgres_identifier_repository.py",
    Path(__file__).resolve().parent / "test_state_db_postgres_signal_repository.py",
    Path(__file__).resolve().parent / "test_state_db_postgres_scan_status_repository.py",
    Path(__file__).resolve().parent.parent / "src" / "data_access" / "postgres_state_db" / "__init__.py",
    Path(__file__).resolve().parent.parent / "src" / "data_access" / "postgres_state_db" / "connection.py",
    Path(__file__).resolve().parent.parent / "src" / "data_access" / "postgres_state_db" / "schema.py",
    Path(__file__).resolve().parent.parent / "src" / "data_access" / "postgres_state_db" / "filing_event_repository.py",
    Path(__file__).resolve().parent.parent / "src" / "data_access" / "postgres_state_db" / "candidate_repository.py",
    Path(__file__).resolve().parent.parent / "src" / "data_access" / "postgres_state_db" / "identifier_repository.py",
    Path(__file__).resolve().parent.parent / "src" / "data_access" / "postgres_state_db" / "signal_repository.py",
    Path(__file__).resolve().parent.parent / "src" / "data_access" / "postgres_state_db" / "scan_status_repository.py",
)

_FORBIDDEN_REAL_STATE_REFERENCES = (
    ".env",
    ".env.example",
    ".streamlit/secrets.toml",
    "data/cache",
    "data/edge_research.db",
)

_ALLOWED_HOST_LITERALS = frozenset({"127.0.0.1", "localhost"})
_SUSPICIOUS_HOST_KEYWORDS = (
    "neon.tech", "supabase", "turso", "amazonaws", "azure", "render.com", "railway.app", ".internal",
)
_IPV4_PATTERN = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")


def _contains_literal(source: str, needle: str) -> bool:
    """Plain substring `in` false-positives on a forbidden reference
    matching inside an unrelated, longer identifier (e.g. Python's own
    `os.environ` attribute name) — requires the character immediately
    following the match, if any, not be a word character, so a real
    standalone mention still matches but an accidental substring inside
    a longer word does not."""
    return re.search(re.escape(needle) + r"(?![A-Za-z0-9_])", source) is not None


def _source_excluding_this_guards_own_constants(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if path.name == "test_backend_factory_postgres.py":
        start = text.index("_NEW_POSTGRES_FILES = (")
        end = text.index("\n\n\ndef test_phase4b_files_never_reference_real_local_state_or_non_loopback_hosts():")
        return text[:start] + text[end:]
    return text


def test_phase4b_files_never_reference_real_local_state_or_non_loopback_hosts():
    offenders = []
    for path in _NEW_POSTGRES_FILES:
        source = _source_excluding_this_guards_own_constants(path)
        for forbidden in _FORBIDDEN_REAL_STATE_REFERENCES:
            if _contains_literal(source, forbidden):
                offenders.append(f"{path.name}: contains forbidden reference {forbidden!r}")
        for ip in _IPV4_PATTERN.findall(source):
            if ip not in _ALLOWED_HOST_LITERALS:
                offenders.append(f"{path.name}: contains non-loopback IPv4 literal {ip!r}")
        lowered = source.lower()
        for keyword in _SUSPICIOUS_HOST_KEYWORDS:
            if keyword in lowered:
                offenders.append(f"{path.name}: contains suspicious hosted-provider keyword {keyword!r}")
    assert not offenders, offenders
