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


def test_review_actions_record_review_decision_is_not_wired_to_postgres():
    """Confirms the deliberate service-entry boundary this phase draws:
    review_actions.record_review_decision() only special-cases
    settings.db_backend == "sqlite" — a literal string check, not a full
    backend_factory dispatch — so an explicit "postgres" selection falls
    through to its JSON-path branch untouched. This is the documented,
    intended scope boundary (see design/DECISIONS.md's Phase 4B-1
    record), verified directly rather than assumed."""
    import inspect

    source = inspect.getsource(review_actions.record_review_decision)
    assert '"sqlite"' in source
    assert '"postgres"' not in source


def test_postgres_backend_never_creates_or_writes_the_json_cache_directory(pg_isolated_dsn, tmp_path):
    settings = Settings(
        db_backend="postgres", state_db_url=pg_isolated_dsn, cache_dir=tmp_path / "should-never-be-created",
    )
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    repo.upsert_new_candidates([_candidate("cand-1", _filing())])
    assert not settings.cache_dir.exists()


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
    Path(__file__).resolve().parent.parent / "src" / "data_access" / "postgres_state_db" / "__init__.py",
    Path(__file__).resolve().parent.parent / "src" / "data_access" / "postgres_state_db" / "connection.py",
    Path(__file__).resolve().parent.parent / "src" / "data_access" / "postgres_state_db" / "schema.py",
    Path(__file__).resolve().parent.parent / "src" / "data_access" / "postgres_state_db" / "filing_event_repository.py",
    Path(__file__).resolve().parent.parent / "src" / "data_access" / "postgres_state_db" / "candidate_repository.py",
    Path(__file__).resolve().parent.parent / "src" / "data_access" / "postgres_state_db" / "identifier_repository.py",
    Path(__file__).resolve().parent.parent / "src" / "data_access" / "postgres_state_db" / "signal_repository.py",
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
