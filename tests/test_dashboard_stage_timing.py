"""Phase 2C — per-stage Dashboard render timing.

Offline and deterministic: a fake monotonic clock drives every duration
and records are observed through a handler attached to the render-timing
logger. No Streamlit runtime, network, database, cache, or external call
is involved.

Why this exists: after PR #76 warm Dashboard renders still take
10.8-11.3s, of which ~7.0-7.4s is the ui_build_ms residual. Two prior
attempts to explain that residual by reading the code were wrong. This
decomposes it by measurement instead.
"""
from __future__ import annotations

import logging
import re
import threading

import pytest

from src.ui import render_timing

# The stages src/ui/pages/dashboard.py declares, in render order. Kept
# here as an explicit contract so a section that silently stops being
# timed fails a test rather than quietly vanishing from the record.
EXPECTED_DASHBOARD_STAGES = (
    "dashboard.header",
    "dashboard.theme_activity_rows",
    "dashboard.published_theme_stats",
    "dashboard.summary_tiles",
    "dashboard.latest_signals",
    "dashboard.recent_theme_activity",
    "dashboard.theme_health",
    "dashboard.regional_brief.total",
    "dashboard.recently_updated",
    "dashboard.priority_signals",
    "dashboard.policy_developments",
    "dashboard.footer",
)


class _FakeClock:
    def __init__(self) -> None:
        self.now = 5_000.0

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
def _reset(monkeypatch):
    monkeypatch.setattr(render_timing, "_render_ordinal", 0)
    monkeypatch.setattr(render_timing, "_STATE", threading.local())


@pytest.fixture
def records(caplog):
    logger = logging.getLogger(render_timing.LOGGER_NAME)
    render_timing._ensure_logger_configured()
    previous = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(caplog.handler)
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)
        logger.setLevel(previous)


def _stage_lines(records) -> list[str]:
    return [r.getMessage() for r in records.records if "stage_timing" in r.getMessage()]


def _fields(message: str) -> dict[str, str]:
    return dict(re.findall(r'(\w+)="([^"]*)"', message) + re.findall(r"(\w+)=([\d.]+)", message))


def _stage_map(message: str) -> dict[str, float]:
    rendered = re.search(r'stages="([^"]*)"', message).group(1)
    if not rendered:
        return {}
    return {part.split("=")[0]: float(part.split("=")[1]) for part in rendered.split(",")}


def _drive(clock, stages, *, setup_ms=10.0, data_load_ms=20.0, tail_ms=0.0, raise_at=None):
    """Replays a page render that declares the given stages."""
    with render_timing.page_render("dashboard"):
        clock.advance_ms(setup_ms)
        render_timing.mark_setup_complete()
        with render_timing.data_load():
            clock.advance_ms(data_load_ms)
        for name, duration in stages:
            with render_timing.stage(name):
                clock.advance_ms(duration)
                if raise_at == name:
                    raise ValueError("boom-with-sensitive-payload")
        clock.advance_ms(tail_ms)


# --- one record per completed render ---------------------------------------

def test_one_stage_record_per_completed_render(clock, records):
    _drive(clock, [("dashboard.header", 5.0), ("dashboard.footer", 3.0)])

    assert len(_stage_lines(records)) == 1


def test_the_record_carries_the_required_safe_fields(clock, records):
    _drive(clock, [("dashboard.header", 5.0)])

    fields = _fields(_stage_lines(records)[0])
    assert fields["event"] == "dashboard_stage_timing"
    assert fields["route"] == "dashboard"
    assert fields["outcome"] == "completed"
    assert fields["render_ordinal"] == "1"
    assert "total_staged_ms" in fields
    assert "unaccounted_ms" in fields


def test_render_ordinal_matches_the_enclosing_page_record(clock, records):
    for _ in range(3):
        _drive(clock, [("dashboard.header", 1.0)])

    page = [r.getMessage() for r in records.records if "page_render_timing" in r.getMessage()]
    stages = _stage_lines(records)
    assert [_fields(m)["render_ordinal"] for m in page] == ["1", "2", "3"]
    assert [_fields(m)["render_ordinal"] for m in stages] == ["1", "2", "3"]


def test_a_page_that_declares_no_stages_emits_no_stage_record(clock, records):
    with render_timing.page_render("coverage"):
        render_timing.mark_setup_complete()
        clock.advance_ms(10)

    assert _stage_lines(records) == []


# --- all declared stage keys present ---------------------------------------

def test_every_expected_dashboard_stage_key_is_present(clock, records):
    _drive(clock, [(name, 1.0 + index) for index, name in enumerate(EXPECTED_DASHBOARD_STAGES)])

    assert list(_stage_map(_stage_lines(records)[0])) == list(EXPECTED_DASHBOARD_STAGES)


def test_dashboard_source_declares_exactly_the_expected_stages():
    """The instrumented page and this contract cannot drift apart."""
    import inspect

    from src.ui.pages import dashboard

    declared = re.findall(r'render_timing\.stage\("([^"]+)"\)', inspect.getsource(dashboard.render))
    assert declared == list(EXPECTED_DASHBOARD_STAGES)


def test_a_stage_skipped_by_the_execution_path_is_simply_absent(clock, records):
    """Latest signals is conditional on a feed existing; an unexecuted
    section is honestly missing rather than reported as zero."""
    _drive(clock, [("dashboard.header", 2.0), ("dashboard.footer", 2.0)])

    keys = _stage_map(_stage_lines(records)[0])
    assert "dashboard.latest_signals" not in keys


# --- reconciliation --------------------------------------------------------

@pytest.mark.parametrize(
    "durations", [[5.0], [1.0, 2.0, 3.0], [100.0, 0.0], [7.5, 7.5, 7.5, 7.5]],
)
def test_stage_sum_reconciles_with_total_staged_ms(clock, records, durations):
    _drive(clock, [(f"dashboard.s{i}", d) for i, d in enumerate(durations)])

    message = _stage_lines(records)[0]
    assert abs(sum(_stage_map(message).values()) - float(_fields(message)["total_staged_ms"])) < 0.05
    assert abs(float(_fields(message)["total_staged_ms"]) - sum(durations)) < 0.05


def test_unaccounted_ms_is_ui_build_minus_the_staged_sum(clock, records):
    _drive(clock, [("dashboard.header", 30.0)], setup_ms=10.0, data_load_ms=20.0, tail_ms=12.0)

    page = _fields([r.getMessage() for r in records.records if "page_render_timing" in r.getMessage()][0])
    stage = _fields(_stage_lines(records)[0])
    expected = float(page["ui_build_ms"]) - float(stage["total_staged_ms"])
    assert abs(float(stage["unaccounted_ms"]) - expected) < 0.05
    assert abs(float(stage["unaccounted_ms"]) - 12.0) < 0.05  # the untimed tail


def test_repeated_use_of_one_stage_name_sums_rather_than_overwrites(clock, records):
    _drive(clock, [("dashboard.header", 4.0), ("dashboard.header", 6.0)])

    assert _stage_map(_stage_lines(records)[0])["dashboard.header"] == pytest.approx(10.0, abs=0.05)


def test_nested_stages_do_not_double_count(clock, records):
    with render_timing.page_render("dashboard"):
        render_timing.mark_setup_complete()
        with render_timing.stage("dashboard.outer"):
            clock.advance_ms(10)
            with render_timing.stage("dashboard.inner"):
                clock.advance_ms(20)
            clock.advance_ms(5)

    stages = _stage_map(_stage_lines(records)[0])
    assert stages == {"dashboard.outer": pytest.approx(35.0, abs=0.05)}
    assert "dashboard.inner" not in stages


def test_data_load_time_is_not_counted_as_a_stage(clock, records):
    _drive(clock, [("dashboard.header", 10.0)], data_load_ms=500.0)

    assert float(_fields(_stage_lines(records)[0])["total_staged_ms"]) == pytest.approx(10.0, abs=0.05)


# --- failure path ----------------------------------------------------------

def test_a_failed_render_emits_exactly_one_safe_failed_stage_record(clock, records):
    with pytest.raises(ValueError, match="boom-with-sensitive-payload"):
        _drive(
            clock,
            [("dashboard.header", 5.0), ("dashboard.summary_tiles", 7.0)],
            raise_at="dashboard.summary_tiles",
        )

    lines = _stage_lines(records)
    assert len(lines) == 1
    fields = _fields(lines[0])
    assert fields["outcome"] == "failed"
    assert fields["failure_kind"] == "ValueError"


def test_a_failed_render_reports_the_stages_it_completed_before_raising(clock, records):
    with pytest.raises(ValueError):
        _drive(clock, [("dashboard.header", 5.0), ("dashboard.summary_tiles", 7.0)],
               raise_at="dashboard.summary_tiles")

    stages = _stage_map(_stage_lines(records)[0])
    assert stages["dashboard.header"] == pytest.approx(5.0, abs=0.05)
    assert stages["dashboard.summary_tiles"] == pytest.approx(7.0, abs=0.05)


def test_the_original_exception_propagates_unchanged(clock, records):
    with pytest.raises(KeyError):
        with render_timing.page_render("dashboard"):
            render_timing.mark_setup_complete()
            with render_timing.stage("dashboard.header"):
                raise KeyError("k")


def test_no_sensitive_exception_text_is_logged(clock, records):
    with pytest.raises(ValueError):
        _drive(clock, [("dashboard.header", 1.0)], raise_at="dashboard.header")

    text = "\n".join(r.getMessage() for r in records.records)
    assert "boom-with-sensitive-payload" not in text
    assert "Traceback" not in text


# --- safety ----------------------------------------------------------------

def test_a_logger_failure_cannot_break_the_render(clock, monkeypatch):
    monkeypatch.setattr(
        render_timing, "_emit",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("logging backend down")),
    )
    _drive(clock, [("dashboard.header", 1.0)])  # must not raise


def test_a_stage_emit_failure_cannot_break_the_render(clock, monkeypatch):
    monkeypatch.setattr(
        render_timing, "_emit_stages",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stage emit down")),
    )
    _drive(clock, [("dashboard.header", 1.0)])  # must not raise


def test_stage_outside_a_render_is_a_silent_no_op(records):
    with render_timing.stage("dashboard.header"):
        pass

    assert _stage_lines(records) == []


def test_the_record_is_one_line_with_no_per_item_events(clock, records):
    """Volume is bounded: many stages still produce one record."""
    _drive(clock, [(f"dashboard.s{i}", 1.0) for i in range(40)])

    assert len(_stage_lines(records)) == 1


def test_the_record_contains_no_sensitive_material(clock, records):
    _drive(clock, [(name, 1.0) for name in EXPECTED_DASHBOARD_STAGES])

    text = "\n".join(_stage_lines(records))
    for forbidden in (
        "postgresql://", "http://", "https://", "password", "Cookie", "Authorization",
        "Bearer", "dbname", "host=", "SELECT", "@",
    ):
        assert forbidden not in text, forbidden


def test_the_instrumentation_introduces_no_network_db_or_streamlit_behavior():
    """AST check: the timing module imports nothing network- or
    database-shaped and calls neither rerun nor fragment."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(render_timing.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)

    for forbidden in ("requests", "urllib", "psycopg", "sqlite3", "socket", "streamlit", "st"):
        assert forbidden not in imported, forbidden
    for forbidden in ("rerun", "fragment", "cache_data", "cache_resource", "execute", "connect"):
        assert forbidden not in called, forbidden


def test_dashboard_instrumentation_adds_no_rerun_fragment_or_javascript():
    import inspect

    from src.ui.pages import dashboard

    source = inspect.getsource(dashboard.render)
    for forbidden in ("st.rerun(", "st.fragment(", "<script", "components.html", "st.components"):
        assert forbidden not in source, forbidden
