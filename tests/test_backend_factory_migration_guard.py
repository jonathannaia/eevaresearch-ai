"""Performance Phase 1 — the Postgres schema-migration guard in
backend_factory.py runs postgres_schema.migrate() at most once per
process per non-secret database identity.

Fully offline: every test here monkeypatches
`postgres_state_db_connection.connect` and `postgres_schema.migrate`
with counting fakes, so no network, live Postgres, credential,
environment variable, or database access of any kind occurs. Every DSN
string below is synthetic and labelled fictional; the only "password"
values present are obvious test literals used to prove they never reach
an identity tuple or a log record.

The guard's promise is narrow and these tests hold it to exactly that:
it removes repeated MIGRATION CHECKS. It does not reduce the number of
connections opened, and test_same_identity_still_opens_one_connection_
per_repository asserts that explicitly so a future change cannot quietly
turn this into connection reuse without updating the contract.
"""
from __future__ import annotations

import logging
import threading

import psycopg
import pytest

from src.config.settings import Settings
from src.data_access import backend_factory

# Fictional DSNs — never a real host, database, role, or credential.
_DSN_A = "host=127.0.0.1 port=55999 dbname=fictional_a user=fictional_user password=FICTIONAL_TEST_ONLY"
_DSN_B = "host=127.0.0.1 port=55999 dbname=fictional_b user=fictional_user password=FICTIONAL_TEST_ONLY"
_DSN_A_SCHEMA_1 = _DSN_A + " options='-c search_path=fictional_schema_one'"
_DSN_A_SCHEMA_2 = _DSN_A + " options='-c search_path=fictional_schema_two'"


class _FakeConnection:
    """Stands in for a psycopg connection. The guard never calls anything
    on it — it only hands it to migrate() — so a bare object suffices."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def guard(monkeypatch):
    """Resets the process-local guard state around each test and installs
    counting fakes for connect()/migrate(). Yields a small recorder."""
    monkeypatch.setattr(backend_factory, "_MIGRATED_POSTGRES_IDENTITIES", set())
    monkeypatch.setattr(backend_factory, "_MIGRATION_ORDINALS", {})
    monkeypatch.setattr(backend_factory, "_MIGRATION_LOCK", threading.Lock())

    class _Recorder:
        def __init__(self) -> None:
            self.connects: list[str] = []
            self.migrates: list[_FakeConnection] = []
            self.migrate_error: BaseException | None = None
            self.on_migrate = None

    recorder = _Recorder()

    def _fake_connect(dsn):
        recorder.connects.append(dsn)
        return _FakeConnection(dsn)

    def _fake_migrate(conn):
        if recorder.on_migrate is not None:
            recorder.on_migrate()
        recorder.migrates.append(conn)
        if recorder.migrate_error is not None:
            error, recorder.migrate_error = recorder.migrate_error, None
            raise error
        return 1

    monkeypatch.setattr(backend_factory.postgres_state_db_connection, "connect", _fake_connect)
    monkeypatch.setattr(backend_factory.postgres_schema, "migrate", _fake_migrate)
    return recorder


def _settings(dsn: str) -> Settings:
    return Settings(db_backend="postgres", state_db_url=dsn)


# --- same identity ---------------------------------------------------------

def test_same_identity_migrates_once_across_different_repository_factories(guard):
    settings = _settings(_DSN_A)
    backend_factory.get_signal_repository(settings)
    backend_factory.get_candidate_repository(settings, "EDINET")
    backend_factory.get_filing_event_repository(settings, "EDINET")
    backend_factory.get_scan_status_repository(settings)
    backend_factory.get_theme_repository(settings)

    assert len(guard.migrates) == 1


def test_same_identity_still_opens_one_connection_per_repository(guard):
    """The guard removes repeated migration checks ONLY — it must never
    be mistaken for, or silently become, connection reuse."""
    settings = _settings(_DSN_A)
    for _ in range(5):
        backend_factory.get_signal_repository(settings)

    assert len(guard.connects) == 5
    assert len(guard.migrates) == 1


def test_equivalent_dsn_with_different_credentials_shares_one_identity(guard):
    """user/password are excluded from the identity, so the same database
    reached with different credentials is the same identity."""
    backend_factory.get_signal_repository(_settings(_DSN_A))
    other_credentials = "host=127.0.0.1 port=55999 dbname=fictional_a user=other_user password=OTHER_FICTIONAL"
    backend_factory.get_signal_repository(_settings(other_credentials))

    assert len(guard.migrates) == 1


# --- distinct identities ---------------------------------------------------

def test_distinct_dbname_migrates_independently(guard):
    backend_factory.get_signal_repository(_settings(_DSN_A))
    backend_factory.get_signal_repository(_settings(_DSN_B))

    assert len(guard.migrates) == 2


def test_distinct_search_path_options_migrate_independently(guard):
    """The isolated-schema shape the Postgres test harness produces: one
    database, one role, a uuid-named schema per test carried in libpq
    `options`. Each must migrate on its own."""
    backend_factory.get_signal_repository(_settings(_DSN_A_SCHEMA_1))
    backend_factory.get_signal_repository(_settings(_DSN_A_SCHEMA_2))
    backend_factory.get_signal_repository(_settings(_DSN_A_SCHEMA_1))

    assert len(guard.migrates) == 2


def test_dsn_with_options_is_a_different_identity_from_the_same_dsn_without(guard):
    backend_factory.get_signal_repository(_settings(_DSN_A))
    backend_factory.get_signal_repository(_settings(_DSN_A_SCHEMA_1))

    assert len(guard.migrates) == 2


# --- concurrency -----------------------------------------------------------

def test_eight_concurrent_first_uses_perform_exactly_one_migration(guard):
    started = threading.Barrier(8)
    guard.on_migrate = lambda: __import__("time").sleep(0.05)
    settings = _settings(_DSN_A)
    errors: list[BaseException] = []

    def _worker():
        try:
            started.wait(timeout=10)
            backend_factory.get_signal_repository(settings)
        except BaseException as exc:  # noqa: BLE001 — surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert errors == []
    assert len(guard.migrates) == 1
    assert len(guard.connects) == 8  # every caller still got its own connection


# --- failure is never recorded ---------------------------------------------

def test_first_migration_failure_is_not_recorded_and_a_later_call_retries(guard):
    settings = _settings(_DSN_A)
    guard.migrate_error = RuntimeError("fictional migration failure")

    with pytest.raises(RuntimeError, match="fictional migration failure"):
        backend_factory.get_signal_repository(settings)

    assert backend_factory._MIGRATED_POSTGRES_IDENTITIES == set()

    backend_factory.get_signal_repository(settings)
    assert len(guard.migrates) == 2
    assert len(backend_factory._MIGRATED_POSTGRES_IDENTITIES) == 1

    backend_factory.get_signal_repository(settings)
    assert len(guard.migrates) == 2  # now guarded


def test_migration_failure_propagates_the_original_exception_type(guard):
    guard.migrate_error = psycopg.OperationalError("fictional operational failure")

    with pytest.raises(psycopg.OperationalError):
        backend_factory.get_signal_repository(_settings(_DSN_A))


# --- fail-closed configuration behavior is unchanged -----------------------

@pytest.mark.parametrize("dsn", [None, "", "   "])
def test_missing_or_blank_dsn_still_fails_closed_before_any_connection(guard, dsn):
    with pytest.raises(backend_factory.BackendConfigurationError):
        backend_factory.get_signal_repository(Settings(db_backend="postgres", state_db_url=dsn))

    assert guard.connects == []
    assert guard.migrates == []


def test_unreachable_target_error_is_still_sanitized_and_never_migrates(monkeypatch, guard):
    def _refuse(dsn):
        raise psycopg.OperationalError("connection refused to fictional-host:1 as fictional_user")

    monkeypatch.setattr(backend_factory.postgres_state_db_connection, "connect", _refuse)

    with pytest.raises(backend_factory.BackendConfigurationError) as excinfo:
        backend_factory.get_signal_repository(_settings(_DSN_A))

    message = str(excinfo.value)
    for secret in ("fictional-host", "fictional_user", "FICTIONAL_TEST_ONLY", "127.0.0.1", "fictional_a"):
        assert secret not in message
    assert guard.migrates == []


# --- malformed DSN falls back to unconditional migration -------------------

def test_malformed_dsn_returns_no_identity_rather_than_raising():
    for malformed in ("not a dsn", "%%%", "=="):
        assert backend_factory._postgres_identity(malformed) is None


def test_malformed_dsn_migrates_unconditionally_and_is_never_guarded(guard):
    settings = _settings("not a dsn")
    backend_factory.get_signal_repository(settings)
    backend_factory.get_signal_repository(settings)
    backend_factory.get_signal_repository(settings)

    assert len(guard.migrates) == 3
    assert backend_factory._MIGRATED_POSTGRES_IDENTITIES == set()


# --- SQLite is untouched ---------------------------------------------------

def test_three_sqlite_repository_creations_still_migrate_three_times(monkeypatch, tmp_path):
    calls: list[object] = []
    real_migrate = backend_factory.state_db_schema.migrate

    def _counting_migrate(conn):
        calls.append(conn)
        return real_migrate(conn)

    monkeypatch.setattr(backend_factory.state_db_schema, "migrate", _counting_migrate)
    settings = Settings(db_backend="sqlite", state_db_path=str(tmp_path / "fictional.db"))

    backend_factory.get_signal_repository(settings)
    backend_factory.get_signal_repository(settings)
    backend_factory.get_signal_repository(settings)

    assert len(calls) == 3


# --- identity never carries credential material ----------------------------

def test_identity_excludes_user_and_password_including_in_its_repr():
    identity = backend_factory._postgres_identity(
        "postgresql://fictional_user:FICTIONAL_TEST_ONLY@fictional-host:5432/fictional_a"
    )
    rendered = repr(identity)
    for secret in ("fictional_user", "FICTIONAL_TEST_ONLY", "password", "user"):
        assert secret not in rendered


def test_identity_keys_are_exactly_the_approved_non_secret_set():
    assert backend_factory._NONSECRET_CONNINFO_KEYS == ("host", "hostaddr", "port", "dbname", "options")
    identity = backend_factory._postgres_identity(_DSN_A)
    assert [key for key, _value in identity] == list(backend_factory._NONSECRET_CONNINFO_KEYS)


def test_absent_parameters_are_preserved_as_none_entries_not_dropped():
    identity = backend_factory._postgres_identity("dbname=fictional_a")
    assert identity == (
        ("host", None), ("hostaddr", None), ("port", None), ("dbname", "fictional_a"), ("options", None),
    )


# --- logging carries nothing sensitive -------------------------------------

@pytest.fixture
def owned_logger_records(caplog):
    """Captures records from this module's own logger.

    `caplog` alone cannot see them: the guard's logger sets
    `propagate=False` by design, so records never reach the root logger
    caplog instruments. Attaching caplog's handler directly to our logger
    is the supported way to observe a non-propagating logger — and it
    keeps `propagate=False` under test rather than weakening it."""
    logger = logging.getLogger("eeva.backend_factory")
    backend_factory._ensure_logger_configured()
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(caplog.handler)
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)
        logger.setLevel(previous_level)


def test_emitted_logs_contain_no_dsn_host_dbname_user_password_or_identity(guard, owned_logger_records):
    backend_factory.get_signal_repository(_settings(_DSN_A_SCHEMA_1))

    text = "\n".join(record.getMessage() for record in owned_logger_records.records)
    assert "schema_migration outcome=attempted" in text
    assert "schema_migration outcome=completed" in text
    assert "db_ordinal=1" in text
    for forbidden in (
        _DSN_A_SCHEMA_1, "127.0.0.1", "55999", "fictional_a", "fictional_user",
        "FICTIONAL_TEST_ONLY", "search_path", "fictional_schema_one", "postgresql://", "hostaddr",
    ):
        assert forbidden not in text, forbidden


def test_first_call_is_logged_so_the_repeat_assertion_below_is_not_vacuous(guard, owned_logger_records):
    backend_factory.get_signal_repository(_settings(_DSN_A))
    assert [r for r in owned_logger_records.records if "schema_migration" in r.getMessage()]


def test_no_log_is_emitted_for_a_guarded_repeat_call(guard, owned_logger_records):
    settings = _settings(_DSN_A)
    backend_factory.get_signal_repository(settings)
    owned_logger_records.clear()

    backend_factory.get_signal_repository(settings)

    assert [r for r in owned_logger_records.records if "schema_migration" in r.getMessage()] == []


# --- logger setup is idempotent and never touches the root logger ----------

def test_repeated_logger_initialization_adds_no_duplicate_owned_handler():
    for _ in range(5):
        backend_factory._ensure_logger_configured()

    owned = [
        handler for handler in backend_factory._LOGGER.handlers
        if getattr(handler, backend_factory._LOGGER_HANDLER_MARKER, False)
    ]
    assert len(owned) == 1
    assert backend_factory._LOGGER.propagate is False


def test_a_foreign_handler_does_not_suppress_our_owned_handler():
    foreign = logging.NullHandler()
    backend_factory._LOGGER.addHandler(foreign)
    try:
        backend_factory._ensure_logger_configured()
        owned = [
            handler for handler in backend_factory._LOGGER.handlers
            if getattr(handler, backend_factory._LOGGER_HANDLER_MARKER, False)
        ]
        assert len(owned) == 1
    finally:
        backend_factory._LOGGER.removeHandler(foreign)


# Note: handler idempotency is verified above by repeated
# _ensure_logger_configured() calls and by the foreign-handler case.
# It is deliberately NOT verified via importlib.reload(backend_factory):
# reloading rebinds every class the module defines — including the
# repository classes and the BackendConfigurationError /
# AgentSchedulingRequiresDurableBackend exception types — so any test
# module that already imported those names holds stale identities, and
# `isinstance` / `except` silently stop matching across the rest of the
# suite. That is a global-interpreter-state mutation, not a property of
# the code under test.


def test_root_logger_is_never_configured_by_this_module():
    backend_factory._ensure_logger_configured()
    root = logging.getLogger()
    assert not any(
        getattr(handler, backend_factory._LOGGER_HANDLER_MARKER, False) for handler in root.handlers
    )
