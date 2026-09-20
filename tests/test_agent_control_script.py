"""scripts/agent_control.py — the emergency stop, and the guarantee that
it can only ever stop.

The danger with an operational control is not that it fails to work; it
is that it grows a way to turn something on. These tests pin the shape
of the CLI itself, because that shape is the safety property: there is
no argument, flag or value that raises the agent's authority.

No database is reached except an in-memory SQLite one, and no credential
appears anywhere."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.state_db import agent_repository as sqlite_agent
from src.data_access.state_db import connection, schema
from src.logic import agent_mode

import scripts.agent_control as agent_control

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc).isoformat()


@pytest.fixture
def store():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    return backend_factory._AgentStoreRepository(conn=conn, module=sqlite_agent)


@pytest.fixture
def wired(monkeypatch, store):
    """The script builds its own store; point it at ours and keep it open
    across calls so a test can read back what was written."""
    monkeypatch.setattr(backend_factory, "get_agent_store_repository", lambda _settings: _NoClose(store))
    return store


class _NoClose:
    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def close(self):
        return None


# --- the CLI can only restrict ---------------------------------------------------

def test_off_is_the_only_mode_the_parser_accepts():
    assert agent_control.ALLOWED_MODES == ("off",)


@pytest.mark.parametrize("mode", ["shadow", "publish", "on", "live", "enabled", "OFF", "Off"])
def test_no_other_mode_can_be_passed_at_all(mode):
    """argparse refuses it before any code runs, which is the point: the
    surface itself has no way to express 'start the agent'."""
    with pytest.raises(SystemExit):
        agent_control.build_parser().parse_args(["--mode", mode, "--reason", "r"])


def test_the_seam_refuses_an_elevated_mode_even_if_the_parser_were_bypassed(wired):
    with pytest.raises(ValueError) as excinfo:
        agent_control.apply(Settings(), mode="publish", reason="r", actor="a", clear=False, now=NOW)
    assert "only off" in str(excinfo.value)


def test_a_reason_is_required():
    with pytest.raises(SystemExit):
        agent_control.build_parser().parse_args(["--mode", "off"])


@pytest.mark.parametrize("reason", ["", "   "])
def test_an_empty_reason_is_refused(wired, reason):
    with pytest.raises(ValueError):
        agent_control.apply(Settings(), mode="off", reason=reason, actor="a", clear=False, now=NOW)


# --- what it actually writes ------------------------------------------------------

def test_setting_the_override_records_mode_reason_and_actor(wired, store):
    message = agent_control.apply(Settings(), mode="off", reason="duplicate packets", actor="oncall",
                                  clear=False, now=NOW)
    control = store.get_control()
    assert control.mode_override == "off"
    assert control.reason == "duplicate packets" and control.updated_by == "oncall"
    assert "next tick" in message


def test_the_stored_override_actually_turns_a_shadow_run_off(wired, store):
    agent_control.apply(Settings(), mode="off", reason="paging", actor="oncall", clear=False, now=NOW)
    resolved = agent_mode.resolve_mode(Settings(research_agent_mode="shadow"), store.get_control())
    assert resolved.mode == "off" and "paging" in resolved.reason


def test_clearing_the_override_does_not_start_anything(wired, store):
    """Clearing removes a restriction; it never grants permission. The
    environment alone decides whether the agent runs."""
    agent_control.apply(Settings(), mode="off", reason="paging", actor="oncall", clear=False, now=NOW)
    message = agent_control.apply(Settings(), mode="off", reason="resolved", actor="oncall", clear=True, now=NOW)
    assert store.get_control().mode_override is None
    assert "environment" in message
    assert agent_mode.resolve_mode(Settings(research_agent_mode="off"), store.get_control()).mode == "off"


def test_an_override_is_idempotent(wired, store):
    for _ in range(3):
        agent_control.apply(Settings(), mode="off", reason="paging", actor="oncall", clear=False, now=NOW)
    rows = store.conn.execute("SELECT COUNT(*) AS n FROM agent_control").fetchone()
    assert rows["n"] == 1


# --- no secret may enter argv or output ---------------------------------------------

SECRET = "sk-live-0123456789abcdef"


def test_the_cli_has_no_flag_that_could_carry_a_credential():
    """Every argument is non-secret by construction, which is what makes
    this safe to run in a shared shell whose argv is world-readable."""
    options = {a.dest for a in agent_control.build_parser()._actions}
    assert options == {"help", "mode", "reason", "actor", "clear"}
    assert not any(word in name for name in options for word in ("token", "key", "password", "secret", "url", "dsn"))


def test_a_driver_failure_reports_only_the_exception_type(monkeypatch, capsys):
    """psycopg's exception text can carry the connection string it was
    built from, and this output gets pasted into tickets."""
    def explode(_settings):
        raise sqlite3.OperationalError(f"could not connect to postgresql://user:{SECRET}@host/db")

    monkeypatch.setattr(backend_factory, "get_agent_store_repository", explode)
    monkeypatch.setattr(agent_control, "get_settings", lambda: Settings())
    assert agent_control.main(["--mode", "off", "--reason", "paging"]) == 1
    out = capsys.readouterr().out
    assert SECRET not in out and "OperationalError" in out


def test_our_own_validation_message_is_shown_because_it_is_ours(monkeypatch, capsys, wired):
    monkeypatch.setattr(agent_control, "get_settings", lambda: Settings())
    assert agent_control.main(["--mode", "off", "--reason", "   "]) == 1
    assert "a reason is required" in capsys.readouterr().out


def test_the_happy_path_prints_no_connection_detail(monkeypatch, capsys, wired):
    monkeypatch.setattr(agent_control, "get_settings",
                        lambda: Settings(state_db_url=f"postgresql://u:{SECRET}@host/db"))
    assert agent_control.main(["--mode", "off", "--reason", "paging"]) == 0
    out = capsys.readouterr().out
    assert SECRET not in out and "host/db" not in out


def test_the_reason_is_stored_verbatim_and_is_the_operators_own_words(wired, store):
    agent_control.apply(Settings(), mode="off", reason="row 11 false negatives", actor="oncall",
                        clear=False, now=NOW)
    assert store.get_control().reason == "row 11 false negatives"
