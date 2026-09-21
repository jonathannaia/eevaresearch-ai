"""`--self-check`: prove a future shadow run would start, and change
nothing proving it.

The point of a dry run is that it cannot alter anything even if an
assumption behind it turns out to be wrong. So the central test here is
not "does it report correctly" but "does it write" — every table is
hashed before and after, and the repository-opening path (which calls
migrate() on connect) is replaced with a landmine that raises if the
self-check reaches for it.

No model session, credential, network call or live agent is involved."""
from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

import pytest

from src.config.settings import Settings
from src.data_access.state_db import schema as sqlite_schema
from src.logic import agent_scheduler

import scripts.research_agent_worker as worker

REPO_ROOT = Path(__file__).parent.parent
GOOD_TABLES = ("candidates", "filing_events", "schema_version", *agent_scheduler.REQUIRED_AGENT_TABLES)
GOOD_INDEXES = worker.REQUIRED_AGENT_INDEXES


def _settings(**overrides) -> Settings:
    base = dict(
        db_backend="postgres", state_db_url="postgresql://ignored",
        research_agent_service_token="t" * 32,
        edgar_user_agent="EevaResearch research@example.test",
        research_agent_mode="off",
    )
    base.update(overrides)
    return Settings(**base)


def _inspect(version=24, tables=GOOD_TABLES, indexes=GOOD_INDEXES):
    return lambda _settings: (version, tuple(tables), tuple(indexes))


def _run(settings=None, *, probe=lambda: "", inspect=None) -> int:
    return worker.run_self_check_only(
        settings or _settings(), probe=probe, inspect=inspect or _inspect(),
    )


# --- it passes when everything is in place ------------------------------------------

def test_a_fully_prepared_deployment_passes(capsys):
    assert _run() == 0
    out = capsys.readouterr().out
    assert "self-check PASSED" in out
    assert "nothing was written" in out


def test_it_reports_the_actual_configured_and_effective_mode(capsys):
    """The worker is legitimately off while this runs; the operator still
    needs to see what mode is configured."""
    _run(_settings(research_agent_mode="off"))
    out = capsys.readouterr().out
    assert "Configured: off" in out and "Effective: off" in out


def test_a_deliberate_off_mode_is_not_a_failure():
    assert _run(_settings(research_agent_mode="off")) == 0


def test_prerequisites_are_evaluated_against_shadow_not_against_off():
    """agent_scheduler.run_self_check() short-circuits on an off mode. If
    the check ran against the configured mode it would pass vacuously
    with a broken database, which is the whole trap this avoids."""
    assert _run(_settings(research_agent_mode="off"), inspect=_inspect(version=23)) == 2


# --- it writes nothing ----------------------------------------------------------------

@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    sqlite_schema.migrate(conn)
    conn.execute(
        "INSERT INTO filing_events (source_name, corp_code, rcept_no, corp_name, stock_code, report_nm, "
        "rcept_dt, pblntf_ty, flr_nm, retrieved_at) VALUES ('SEC EDGAR','0001045810','acc-1','NVIDIA','NVDA',"
        "'10-Q','20260901','A','NVIDIA','2026-09-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO candidates (id, source, filing_corp_code, filing_rcept_no, confidence, status, "
        "extraction_state, translation_state, excerpt_quality, excerpt_original, version, created_at, updated_at) "
        "VALUES ('cand-1','SEC EDGAR','0001045810','acc-1','High','Needs review','Extracted','Not requested',"
        "'Usable text','Revenue rose.',1,'2026-09-01T00:00:00+00:00','2026-09-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO state_transitions (candidate_id, status, at, detail) "
        "VALUES ('cand-1','Extracted','2026-09-01T00:00:00+00:00','radar')"
    )
    conn.commit()
    return conn


def _fingerprint(conn) -> dict[str, str]:
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()]
    out = {}
    for table in names:
        rows = sorted(repr(tuple(r)) for r in conn.execute(f'SELECT * FROM "{table}"').fetchall())
        out[table] = hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()
    return out


def test_a_passing_self_check_writes_nothing_at_all(db, monkeypatch):
    """Every table hashed before and after — agent tables and product
    tables alike."""
    def read_only_inspect(_settings):
        version = db.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()["v"]
        tables = tuple(r["name"] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall())
        return (max(24, version), tables, GOOD_INDEXES)

    monkeypatch.setattr(
        worker.backend_factory, "get_agent_store_repository",
        lambda *a, **k: pytest.fail("self-check opened a repository, which calls migrate() on connect"),
    )
    before = _fingerprint(db)
    assert _run(inspect=read_only_inspect) == 0
    assert _fingerprint(db) == before

    for table in agent_scheduler.REQUIRED_AGENT_TABLES:
        assert db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 0
    assert db.execute("SELECT COUNT(*) AS n FROM candidates").fetchone()["n"] == 1
    assert db.execute("SELECT COUNT(*) AS n FROM state_transitions").fetchone()["n"] == 1


def test_it_never_opens_the_repository_even_when_the_check_fails(monkeypatch):
    monkeypatch.setattr(
        worker.backend_factory, "get_agent_store_repository",
        lambda *a, **k: pytest.fail("self-check opened a repository"),
    )
    assert _run(inspect=_inspect(version=20)) == 2


def test_the_read_only_inspector_is_the_documented_path():
    """It must use the plain connection helper, not the repository
    factory — connection.connect() does not migrate."""
    source = (REPO_ROOT / "scripts" / "research_agent_worker.py").read_text(encoding="utf-8")
    body = source[source.index("def inspect_database_readonly("):source.index("def run_self_check_only(")]
    # Assert about the code, not the prose: the docstring deliberately
    # names migrate() to explain why it is avoided.
    code = body.split('"""')[2] if body.count('"""') >= 2 else body
    assert "postgres_state_db import connection" in code
    assert "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY" in code
    assert "get_agent_store_repository" not in code
    assert "migrate" not in code


# --- it fails closed ---------------------------------------------------------------------

def test_a_missing_runtime_cli_fails_closed(capsys):
    assert _run(probe=lambda: "'claude' is not on PATH") == 2
    assert "runtime probe" in capsys.readouterr().out


@pytest.mark.parametrize("version", [None, 20, 22, 23])
def test_a_schema_below_v24_fails_closed(version, capsys):
    assert _run(inspect=_inspect(version=version)) == 2
    assert "schema" in capsys.readouterr().out.lower()


def test_a_newer_schema_is_accepted():
    assert _run(inspect=_inspect(version=25)) == 0


@pytest.mark.parametrize("missing", agent_scheduler.REQUIRED_AGENT_TABLES)
def test_a_missing_agent_table_fails_closed(missing, capsys):
    tables = tuple(t for t in GOOD_TABLES if t != missing)
    assert _run(inspect=_inspect(tables=tables)) == 2
    assert missing in capsys.readouterr().out


@pytest.mark.parametrize("missing", worker.REQUIRED_AGENT_INDEXES)
def test_a_missing_agent_index_fails_closed(missing, capsys):
    indexes = tuple(i for i in GOOD_INDEXES if i != missing)
    assert _run(inspect=_inspect(indexes=indexes)) == 2
    out = capsys.readouterr().out
    assert "missing agent indexes" in out and missing in out


@pytest.mark.parametrize("backend", ["json", "sqlite", ""])
def test_a_non_postgres_backend_fails_closed(backend, capsys):
    assert _run(_settings(db_backend=backend)) == 2
    assert "postgres" in capsys.readouterr().out


def test_a_missing_service_token_fails_closed(capsys):
    assert _run(_settings(research_agent_service_token=None)) == 2
    assert "SERVICE_TOKEN" in capsys.readouterr().out


def test_a_missing_edgar_user_agent_fails_closed(capsys):
    assert _run(_settings(edgar_user_agent="")) == 2
    assert "EDGAR_USER_AGENT" in capsys.readouterr().out


@pytest.mark.parametrize("mode", ["banana", "shadowmode", "on", "1", "enabled"])
def test_an_unrecognized_mode_value_fails_closed(mode, capsys):
    """A typo resolves silently to off, so shadow mode would never start
    however well everything else is configured."""
    assert _run(_settings(research_agent_mode=mode)) == 2
    assert "is not one of" in capsys.readouterr().out


def test_an_unreachable_database_fails_closed_without_leaking_detail(capsys):
    def explode(_settings):
        raise RuntimeError("could not connect to postgresql://u:sup3rsecret@host/db")

    assert _run(inspect=explode) == 2
    out = capsys.readouterr().out
    assert "RuntimeError" in out and "sup3rsecret" not in out


def test_every_problem_is_listed_not_just_the_first(capsys):
    assert _run(_settings(db_backend="json", edgar_user_agent=""), probe=lambda: "no cli") == 2
    out = capsys.readouterr().out
    assert out.count("research_agent_worker:   - ") >= 4


# --- normal behaviour is untouched ------------------------------------------------------------

def test_without_the_flag_the_worker_behaves_exactly_as_before(monkeypatch, capsys):
    monkeypatch.delenv("EDGE_RESEARCH_AGENT_LIVE_ENABLED", raising=False)
    monkeypatch.setattr(worker, "run_one_tick", lambda *a, **k: pytest.fail("a tick ran"))
    monkeypatch.setattr(worker, "run_self_check_only", lambda *a, **k: pytest.fail("self-check ran"))
    assert worker.main([]) == 0
    assert "EDGE_RESEARCH_AGENT_LIVE_ENABLED is not enabled" in capsys.readouterr().out


def test_the_flag_runs_before_the_live_enabled_gate(monkeypatch):
    """Readiness has to be checkable while the worker is still disabled."""
    monkeypatch.delenv("EDGE_RESEARCH_AGENT_LIVE_ENABLED", raising=False)
    called = {}

    def record(settings):
        called["ran"] = True
        return 0

    monkeypatch.setattr(worker, "run_self_check_only", record)
    assert worker.main(["--self-check"]) == 0
    assert called == {"ran": True}


def test_self_check_never_installs_signal_handlers(monkeypatch):
    """It is a short-lived command, not a daemon."""
    monkeypatch.setattr(worker, "_install_signal_handlers", lambda: pytest.fail("handlers installed"))
    monkeypatch.setattr(worker, "run_self_check_only", lambda settings: 0)
    assert worker.main(["--self-check"]) == 0


# --- the command line offers nothing else --------------------------------------------------------

def test_the_parser_accepts_exactly_one_flag():
    options = {a.dest for a in worker.build_parser()._actions}
    assert options == {"help", "self_check"}


@pytest.mark.parametrize("flag", [
    "--run-once", "--shadow", "--mode", "--live", "--enable", "--publish", "--tick", "--resume", "--force",
])
def test_no_operational_flag_exists(flag):
    with pytest.raises(SystemExit):
        worker.build_parser().parse_args([flag])


def test_the_flag_is_documented_as_writing_nothing():
    help_text = worker.build_parser().format_help()
    assert "--self-check" in help_text
    assert "Writes nothing" in help_text


# --- the index list stays in step with the migration -----------------------------------------------

def test_the_required_index_list_matches_what_v24_actually_creates():
    """Re-derived from the migration rather than trusted: a new index in
    V24 without a matching entry here would otherwise go unchecked."""
    from src.data_access.postgres_state_db import schema as pg_schema

    created = []
    for statement in dict(pg_schema._MIGRATIONS)[24]:
        match = re.search(r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(\w+)", statement, re.I)
        if match:
            created.append(match.group(1))
    assert sorted(created) == sorted(worker.REQUIRED_AGENT_INDEXES)
    assert len(created) == 9
