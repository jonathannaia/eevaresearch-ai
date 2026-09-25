"""Phase 2A — src/ui/render_timing.py, the page-render timing helper.

Fully offline and deterministic: a fake monotonic clock drives every
duration, and records are observed through a handler attached directly
to the module's own logger. No Streamlit runtime, network, database,
cache, filesystem, or external call is involved.
"""
from __future__ import annotations

import logging
import pathlib
import re
import threading

import pytest

from src.ui import render_timing


class _FakeClock:
    """Deterministic stand-in for time.monotonic(), in seconds."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance_ms(self, milliseconds: float) -> None:
        self.now += milliseconds / 1000.0


@pytest.fixture
def clock(monkeypatch):
    fake = _FakeClock()
    monkeypatch.setattr(render_timing.time, "monotonic", fake)
    return fake


@pytest.fixture(autouse=True)
def reset_module_state(monkeypatch):
    """Keeps the process-local ordinal and in-flight record from leaking
    between tests."""
    monkeypatch.setattr(render_timing, "_render_ordinal", 0)
    monkeypatch.setattr(render_timing, "_STATE", threading.local())
    yield


@pytest.fixture
def records(caplog):
    """Captures this module's records. `caplog` alone cannot see them:
    the logger sets propagate=False by design, so records never reach the
    root logger caplog instruments. Attaching caplog's handler directly
    observes the real logger while keeping propagate=False under test."""
    logger = logging.getLogger(render_timing.LOGGER_NAME)
    render_timing._ensure_logger_configured()
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(caplog.handler)
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)
        logger.setLevel(previous_level)


def _fields(message: str) -> dict[str, str]:
    return dict(re.findall(r'(\w+)="?([^"\s]+)"?', message))


def _only_record(records) -> dict[str, str]:
    lines = [r.getMessage() for r in records.records if "page_render_timing" in r.getMessage()]
    assert len(lines) == 1, lines
    return _fields(lines[0])


# --- one record per render, with every required field ----------------------

def test_a_completed_render_emits_exactly_one_record_with_all_required_fields(clock, records):
    with render_timing.page_render("dashboard"):
        clock.advance_ms(10)
        render_timing.mark_setup_complete()
        with render_timing.data_load():
            clock.advance_ms(40)
        clock.advance_ms(25)

    fields = _only_record(records)
    assert fields["event"] == "page_render_timing"
    assert fields["route"] == "dashboard"
    assert fields["render_ordinal"] == "1"
    assert fields["total_python_ms"] == "75.0"
    assert fields["setup_ms"] == "10.0"
    assert fields["data_load_ms"] == "40.0"
    assert fields["ui_build_ms"] == "25.0"
    assert fields["outcome"] == "completed"
    assert "cache_status" in fields


@pytest.mark.parametrize("route", ["dashboard", "daily_news", "radar_inbox", "themes", "coverage"])
def test_each_instrumented_route_is_recorded_under_its_own_identifier(clock, records, route):
    with render_timing.page_render(route):
        render_timing.mark_setup_complete()

    assert _only_record(records)["route"] == route


# --- segments reconcile ----------------------------------------------------

@pytest.mark.parametrize(
    "setup,loads,tail",
    [(10, [40], 25), (0, [], 0), (5, [1, 2, 3], 7), (100, [0], 0), (1, [250], 9)],
)
def test_segments_always_reconcile_to_the_total(clock, records, setup, loads, tail):
    with render_timing.page_render("dashboard"):
        clock.advance_ms(setup)
        render_timing.mark_setup_complete()
        for load in loads:
            with render_timing.data_load():
                clock.advance_ms(load)
        clock.advance_ms(tail)

    fields = _only_record(records)
    total = float(fields["total_python_ms"])
    parts = float(fields["setup_ms"]) + float(fields["data_load_ms"]) + float(fields["ui_build_ms"])
    assert abs(total - parts) < 0.05
    assert total == pytest.approx(setup + sum(loads) + tail, abs=0.05)


def test_multiple_data_load_blocks_are_summed(clock, records):
    with render_timing.page_render("coverage"):
        render_timing.mark_setup_complete()
        for _ in range(3):
            with render_timing.data_load():
                clock.advance_ms(20)

    assert _only_record(records)["data_load_ms"] == "60.0"


def test_nested_data_load_blocks_are_not_double_counted(clock, records):
    with render_timing.page_render("themes"):
        render_timing.mark_setup_complete()
        with render_timing.data_load():
            clock.advance_ms(10)
            with render_timing.data_load():
                clock.advance_ms(30)
            clock.advance_ms(10)

    assert _only_record(records)["data_load_ms"] == "50.0"


def test_without_mark_setup_complete_the_whole_render_counts_as_setup(clock, records):
    with render_timing.page_render("dashboard"):
        clock.advance_ms(30)

    fields = _only_record(records)
    assert fields["setup_ms"] == "30.0"
    assert fields["ui_build_ms"] == "0.0"


def test_mark_setup_complete_is_idempotent(clock, records):
    with render_timing.page_render("dashboard"):
        clock.advance_ms(10)
        render_timing.mark_setup_complete()
        clock.advance_ms(40)
        render_timing.mark_setup_complete()  # must not move the boundary

    assert _only_record(records)["setup_ms"] == "10.0"


# --- ordinal ---------------------------------------------------------------

def test_render_ordinal_increments_per_render(clock, records):
    for _ in range(3):
        with render_timing.page_render("dashboard"):
            render_timing.mark_setup_complete()

    ordinals = [
        _fields(r.getMessage())["render_ordinal"]
        for r in records.records if "page_render_timing" in r.getMessage()
    ]
    assert ordinals == ["1", "2", "3"]


def test_concurrent_renders_get_distinct_ordinals_and_one_record_each(clock, records):
    def _worker():
        with render_timing.page_render("dashboard"):
            render_timing.mark_setup_complete()

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    lines = [r.getMessage() for r in records.records if "page_render_timing" in r.getMessage()]
    ordinals = sorted(int(_fields(line)["render_ordinal"]) for line in lines)
    assert ordinals == [1, 2, 3, 4, 5, 6, 7, 8]


# --- cache status ----------------------------------------------------------

def test_cache_status_defaults_to_unknown(clock, records):
    with render_timing.page_render("dashboard"):
        render_timing.mark_setup_complete()

    assert _only_record(records)["cache_status"] == "unknown"


def test_cache_status_from_data_load_argument_is_recorded(clock, records):
    with render_timing.page_render("radar_inbox"):
        render_timing.mark_setup_complete()
        with render_timing.data_load(cache_status="hit"):
            clock.advance_ms(5)

    assert _only_record(records)["cache_status"] == "hit"


def test_a_later_set_cache_status_overrides_the_declared_default(clock, records):
    with render_timing.page_render("radar_inbox"):
        render_timing.mark_setup_complete()
        with render_timing.data_load(cache_status="hit"):
            render_timing.set_cache_status("miss")  # what the cached body does on a real miss

    assert _only_record(records)["cache_status"] == "miss"


# --- failure path ----------------------------------------------------------

def test_a_failed_render_emits_one_safe_failed_record_and_reraises(clock, records):
    with pytest.raises(ValueError, match="boom-with-secret-payload"):
        with render_timing.page_render("dashboard"):
            clock.advance_ms(12)
            render_timing.mark_setup_complete()
            raise ValueError("boom-with-secret-payload")

    fields = _only_record(records)
    assert fields["outcome"] == "failed"
    assert fields["failure_kind"] == "ValueError"
    assert fields["total_python_ms"] == "12.0"

    text = "\n".join(r.getMessage() for r in records.records)
    assert "boom-with-secret-payload" not in text
    assert "Traceback" not in text


def test_a_failed_render_still_reports_the_phases_it_completed(clock, records):
    with pytest.raises(RuntimeError):
        with render_timing.page_render("coverage"):
            clock.advance_ms(10)
            render_timing.mark_setup_complete()
            with render_timing.data_load():
                clock.advance_ms(20)
            raise RuntimeError("x")

    fields = _only_record(records)
    assert fields["setup_ms"] == "10.0"
    assert fields["data_load_ms"] == "20.0"


def test_data_load_never_suppresses_an_exception(clock, records):
    with pytest.raises(KeyError):
        with render_timing.page_render("dashboard"):
            render_timing.mark_setup_complete()
            with render_timing.data_load():
                raise KeyError("k")


def test_an_emit_failure_never_breaks_the_page(clock, monkeypatch):
    monkeypatch.setattr(
        render_timing, "_emit",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("logging backend down")),
    )
    with render_timing.page_render("dashboard"):  # must not raise
        render_timing.mark_setup_complete()


# --- helpers are safe no-ops outside a render ------------------------------

def test_helpers_outside_a_render_are_silent_no_ops(records):
    render_timing.mark_setup_complete()
    render_timing.set_cache_status("hit")
    with render_timing.data_load():
        pass

    assert [r for r in records.records if "page_render_timing" in r.getMessage()] == []


def test_a_nested_page_render_does_not_emit_a_second_record(clock, records):
    with render_timing.page_render("dashboard"):
        render_timing.mark_setup_complete()
        with render_timing.page_render("coverage"):
            clock.advance_ms(5)

    assert _only_record(records)["route"] == "dashboard"


# --- bounded volume and privacy -------------------------------------------

def test_exactly_one_record_per_render_regardless_of_work_done(clock, records):
    with render_timing.page_render("radar_inbox"):
        render_timing.mark_setup_complete()
        for _ in range(50):
            with render_timing.data_load():
                clock.advance_ms(1)

    assert len(_only_record(records)) > 0  # _only_record asserts exactly one


def test_the_record_contains_no_sensitive_material(clock, records):
    with render_timing.page_render("radar_inbox"):
        render_timing.mark_setup_complete()
        with render_timing.data_load(cache_status="miss"):
            clock.advance_ms(5)

    text = "\n".join(r.getMessage() for r in records.records)
    for forbidden in (
        "postgresql://", "password", "user=", "dbname", "host=", "Cookie", "Authorization",
        "Bearer", "session_id", "@", "?", "SELECT",
    ):
        assert forbidden not in text, forbidden


def test_the_module_performs_no_io_beyond_logging():
    """Parsed with `ast` rather than substring-matched, so the module's
    own docstring naming the APIs it avoids cannot trip this guard."""
    import ast

    tree = ast.parse(pathlib.Path(render_timing.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)

    assert imported == {"__future__", "logging", "sys", "threading", "time", "contextlib", "dataclasses"}
    for forbidden in ("requests", "urllib", "psycopg", "sqlite3", "socket", "streamlit", "st"):
        assert forbidden not in imported, forbidden
    for forbidden in ("rerun", "fragment", "cache_data", "cache_resource", "execute", "connect", "get", "post"):
        assert forbidden not in called, forbidden


# --- logger setup ----------------------------------------------------------

def test_repeated_logger_initialization_adds_no_duplicate_owned_handler():
    for _ in range(5):
        render_timing._ensure_logger_configured()

    owned = [
        h for h in logging.getLogger(render_timing.LOGGER_NAME).handlers
        if getattr(h, render_timing._LOGGER_HANDLER_MARKER, False)
    ]
    assert len(owned) == 1
    assert logging.getLogger(render_timing.LOGGER_NAME).propagate is False


def test_a_foreign_handler_does_not_suppress_our_owned_handler():
    logger = logging.getLogger(render_timing.LOGGER_NAME)
    foreign = logging.NullHandler()
    logger.addHandler(foreign)
    try:
        render_timing._ensure_logger_configured()
        owned = [h for h in logger.handlers if getattr(h, render_timing._LOGGER_HANDLER_MARKER, False)]
        assert len(owned) == 1
    finally:
        logger.removeHandler(foreign)


def test_root_logger_is_never_configured_by_this_module():
    render_timing._ensure_logger_configured()
    root = logging.getLogger()
    assert not any(
        getattr(h, render_timing._LOGGER_HANDLER_MARKER, False) for h in root.handlers
    )
