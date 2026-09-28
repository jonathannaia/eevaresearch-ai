"""Phase 2F — fine-grained step timing inside data_load and inside the
Recently Updated stage.

Offline and deterministic: a fake monotonic clock drives every duration
and records are observed through a handler attached to the render-timing
logger. No Streamlit runtime, network, database, cache, or external call
is involved.

Why this exists. Two optimizations were chosen from LOCAL profiling and
both mispredicted production by a wide margin — PR #79 projected ~1,100ms
of data_load and delivered ~1,950ms, PR #80 projected ~500ms for
Recently Updated and delivered ~1,856ms. Neither phase has ever been
decomposed in production. This adds the timers; it changes no behavior
and chooses no optimization.

The two groups reconcile against different containers, which is the
whole reason step() is a separate dimension from stage():

    data_load.*        sits inside data_load_ms
    recently_updated.* sits inside the dashboard.recently_updated stage,
                       which is itself part of ui_build_ms

so each group publishes its own subtotal and remainder.
"""
from __future__ import annotations

import logging
import re
import threading

import pytest

from src.ui import render_timing

# The steps the instrumented code declares, kept here as an explicit
# contract so a substep that silently stops being timed fails a test
# rather than quietly vanishing from the record.
EXPECTED_DATA_LOAD_STEPS = (
    "data_load.signals_feed",
    "data_load.source_connection_acquire",
    "data_load.source_filing_repo",
    "data_load.source_filing_query",
    "data_load.source_candidate_repo",
    "data_load.source_candidate_query",
    "data_load.source_exclusions",
    "data_load.daily_news_raw",
    "data_load.daily_news_canonical",
    "data_load.editorial_stories",
)
EXPECTED_RECENTLY_UPDATED_STEPS = (
    "recently_updated.filing_sources",
    "recently_updated.news_sources",
    "recently_updated.editorial_sources",
    "recently_updated.dedup",
    "recently_updated.sort",
    "recently_updated.merge",
    "recently_updated.materialize",
    "recently_updated.render_rows",
)
RECENTLY_UPDATED_STAGE = "dashboard.recently_updated"


class _FakeClock:
    def __init__(self) -> None:
        self.now = 7_000.0

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


def _step_lines(records) -> list[str]:
    return [r.getMessage() for r in records.records if "step_timing" in r.getMessage()]


def _fields(message: str) -> dict[str, str]:
    return dict(re.findall(r'(\w+)="([^"]*)"', message) + re.findall(r"(\w+)=(-?[\d.]+)", message))


def _step_map(message: str) -> dict[str, float]:
    rendered = re.search(r'steps="([^"]*)"', message).group(1)
    if not rendered:
        return {}
    return {part.split("=")[0]: float(part.split("=")[1]) for part in rendered.split(",")}


def _drive(
    clock, *, data_load_steps=(), recently_updated_steps=(),
    setup_ms=10.0, data_load_other_ms=0.0, stage_other_ms=0.0, tail_ms=0.0, raise_at=None,
):
    """Replays a Dashboard-shaped render: a data_load block containing
    data_load.* steps, then a recently_updated stage containing
    recently_updated.* steps."""
    with render_timing.page_render("dashboard"):
        clock.advance_ms(setup_ms)
        render_timing.mark_setup_complete()
        with render_timing.data_load():
            for name, duration in data_load_steps:
                with render_timing.step(name):
                    clock.advance_ms(duration)
                    if raise_at == name:
                        raise ValueError("boom-with-sensitive-payload")
            clock.advance_ms(data_load_other_ms)
        with render_timing.stage(RECENTLY_UPDATED_STAGE):
            for name, duration in recently_updated_steps:
                with render_timing.step(name):
                    clock.advance_ms(duration)
                    if raise_at == name:
                        raise ValueError("boom-with-sensitive-payload")
            clock.advance_ms(stage_other_ms)
        clock.advance_ms(tail_ms)


_SAMPLE_DATA_LOAD = (("data_load.signals_feed", 800.0), ("data_load.source_filing_query", 300.0))
_SAMPLE_RU = (("recently_updated.sort", 120.0), ("recently_updated.render_rows", 60.0))


# --- one record per render ------------------------------------------------

def test_one_step_record_per_completed_render(clock, records):
    _drive(clock, data_load_steps=_SAMPLE_DATA_LOAD, recently_updated_steps=_SAMPLE_RU)

    assert len(_step_lines(records)) == 1


def test_a_render_declaring_no_steps_emits_no_step_record(clock, records):
    with render_timing.page_render("themes"):
        render_timing.mark_setup_complete()
        clock.advance_ms(40.0)

    assert _step_lines(records) == []


def test_the_record_carries_the_required_safe_fields(clock, records):
    _drive(clock, data_load_steps=_SAMPLE_DATA_LOAD, recently_updated_steps=_SAMPLE_RU)

    fields = _fields(_step_lines(records)[0])

    assert fields["event"] == "dashboard_step_timing"
    assert fields["route"] == "dashboard"
    assert fields["outcome"] == "completed"
    for key in (
        "render_ordinal", "total_step_ms",
        "data_load_ms", "data_load_step_ms", "data_load_unaccounted_ms",
        "recently_updated_ms", "recently_updated_step_ms", "recently_updated_unaccounted_ms",
    ):
        assert key in fields, key


def test_render_ordinal_matches_the_enclosing_page_record(clock, records):
    _drive(clock, data_load_steps=_SAMPLE_DATA_LOAD, recently_updated_steps=_SAMPLE_RU)

    page = next(r.getMessage() for r in records.records if "page_render_timing" in r.getMessage())
    step = _step_lines(records)[0]

    assert _fields(page)["render_ordinal"] == _fields(step)["render_ordinal"]


def test_the_record_is_one_line_with_no_per_item_events(clock, records):
    many = tuple((f"recently_updated.{name}", 5.0) for name in ("sort", "dedup", "merge"))
    _drive(clock, recently_updated_steps=many * 40)  # 120 step entries

    lines = _step_lines(records)
    assert len(lines) == 1
    assert "\n" not in lines[0]
    assert len(_step_map(lines[0])) == 3  # repeats sum into three keys, not 120 events


# --- reconciliation -------------------------------------------------------

@pytest.mark.parametrize("durations", [(1.0, 2.0), (0.0, 500.0), (250.5, 749.5)])
def test_step_sum_reconciles_with_total_step_ms(clock, records, durations):
    _drive(
        clock,
        data_load_steps=(("data_load.signals_feed", durations[0]),),
        recently_updated_steps=(("recently_updated.sort", durations[1]),),
    )

    line = _step_lines(records)[0]
    assert sum(_step_map(line).values()) == pytest.approx(float(_fields(line)["total_step_ms"]), abs=0.15)


def test_data_load_group_reconciles_against_data_load_ms(clock, records):
    _drive(clock, data_load_steps=_SAMPLE_DATA_LOAD, data_load_other_ms=175.0)

    fields = _fields(_step_lines(records)[0])

    assert float(fields["data_load_step_ms"]) == pytest.approx(1100.0, abs=0.15)
    assert float(fields["data_load_ms"]) == pytest.approx(1275.0, abs=0.15)
    assert float(fields["data_load_unaccounted_ms"]) == pytest.approx(175.0, abs=0.15)


def test_recently_updated_group_reconciles_against_its_stage(clock, records):
    _drive(clock, recently_updated_steps=_SAMPLE_RU, stage_other_ms=40.0)

    fields = _fields(_step_lines(records)[0])

    assert float(fields["recently_updated_step_ms"]) == pytest.approx(180.0, abs=0.15)
    assert float(fields["recently_updated_ms"]) == pytest.approx(220.0, abs=0.15)
    assert float(fields["recently_updated_unaccounted_ms"]) == pytest.approx(40.0, abs=0.15)


def test_a_group_with_no_steps_reports_its_container_as_fully_unaccounted(clock, records):
    """A large remainder is itself the finding: it says the cost is
    somewhere no timer has been placed yet."""
    _drive(clock, data_load_steps=_SAMPLE_DATA_LOAD, stage_other_ms=900.0)

    fields = _fields(_step_lines(records)[0])

    assert float(fields["recently_updated_step_ms"]) == pytest.approx(0.0, abs=0.15)
    assert float(fields["recently_updated_unaccounted_ms"]) == pytest.approx(900.0, abs=0.15)


def test_the_two_groups_are_accounted_separately(clock, records):
    _drive(clock, data_load_steps=_SAMPLE_DATA_LOAD, recently_updated_steps=_SAMPLE_RU)

    fields = _fields(_step_lines(records)[0])

    assert float(fields["data_load_step_ms"]) == pytest.approx(1100.0, abs=0.15)
    assert float(fields["recently_updated_step_ms"]) == pytest.approx(180.0, abs=0.15)
    assert float(fields["total_step_ms"]) == pytest.approx(1280.0, abs=0.2)


def test_repeated_use_of_one_step_name_sums_rather_than_overwrites(clock, records):
    _drive(clock, data_load_steps=(("data_load.source_filing_query", 30.0), ("data_load.source_filing_query", 45.0)))

    assert _step_map(_step_lines(records)[0])["data_load.source_filing_query"] == pytest.approx(75.0, abs=0.15)


def test_nested_steps_do_not_double_count(clock, records):
    with render_timing.page_render("dashboard"):
        render_timing.mark_setup_complete()
        with render_timing.data_load():
            with render_timing.step("data_load.source_filing_query"):
                clock.advance_ms(40.0)
                with render_timing.step("data_load.daily_news_raw"):
                    clock.advance_ms(60.0)

    steps = _step_map(_step_lines(records)[0])
    assert steps == {"data_load.source_filing_query": pytest.approx(100.0, abs=0.15)}
    assert "data_load.daily_news_raw" not in steps


def test_steps_and_stages_are_independent_dimensions(clock, records):
    """A step inside a stage must still be recorded as a step — the
    stage nesting rule must not swallow it."""
    _drive(clock, recently_updated_steps=_SAMPLE_RU)

    steps = _step_map(_step_lines(records)[0])
    assert set(steps) == {"recently_updated.sort", "recently_updated.render_rows"}


def test_a_step_inside_a_data_load_block_is_still_recorded(clock, records):
    _drive(clock, data_load_steps=(("data_load.signals_feed", 10.0),))

    assert "data_load.signals_feed" in _step_map(_step_lines(records)[0])


def test_a_step_skipped_by_the_execution_path_is_simply_absent(clock, records):
    _drive(clock, data_load_steps=(("data_load.signals_feed", 10.0),))

    steps = _step_map(_step_lines(records)[0])
    assert "data_load.source_filing_query" not in steps
    assert set(steps) == {"data_load.signals_feed"}


# --- the declared steps and this contract cannot drift apart --------------

def test_dashboard_source_declares_exactly_the_expected_data_load_steps():
    import inspect

    from src.ui.pages import dashboard

    source = (
        inspect.getsource(dashboard.render)
        + inspect.getsource(dashboard._load_source_reads)
        + inspect.getsource(dashboard._load_daily_news_snapshot)
    )
    declared = re.findall(r'render_timing\.step\("([^"]+)"\)', source)

    assert sorted(declared) == sorted(EXPECTED_DATA_LOAD_STEPS)
    assert all(name.startswith("data_load.") for name in declared)


def test_recently_updated_source_declares_exactly_the_expected_steps():
    import inspect

    from src.ui.components import recently_updated

    source = (
        inspect.getsource(recently_updated._select_row_sources)
        + inspect.getsource(recently_updated.render_recently_updated)
    )
    declared = re.findall(r'render_timing\.step\("([^"]+)"\)', source)

    assert sorted(declared) == sorted(EXPECTED_RECENTLY_UPDATED_STEPS)
    assert all(name.startswith("recently_updated.") for name in declared)


def test_every_declared_step_belongs_to_a_known_group():
    known = (render_timing.STEP_GROUP_DATA_LOAD, render_timing.STEP_GROUP_RECENTLY_UPDATED)
    for name in EXPECTED_DATA_LOAD_STEPS + EXPECTED_RECENTLY_UPDATED_STEPS:
        assert name.split(".")[0] in known, name


# --- failure behaviour ----------------------------------------------------

def test_a_failed_render_emits_exactly_one_safe_failed_step_record(clock, records):
    with pytest.raises(ValueError):
        _drive(clock, data_load_steps=_SAMPLE_DATA_LOAD, raise_at="data_load.source_filing_query")

    lines = _step_lines(records)
    assert len(lines) == 1
    fields = _fields(lines[0])
    assert fields["outcome"] == "failed"
    assert fields["failure_kind"] == "ValueError"
    assert "boom-with-sensitive-payload" not in lines[0]


def test_a_failed_render_reports_the_steps_it_completed_before_raising(clock, records):
    with pytest.raises(ValueError):
        _drive(clock, data_load_steps=_SAMPLE_DATA_LOAD, raise_at="data_load.source_filing_query")

    steps = _step_map(_step_lines(records)[0])
    assert steps["data_load.signals_feed"] == pytest.approx(800.0, abs=0.15)
    assert steps["data_load.source_filing_query"] == pytest.approx(300.0, abs=0.15)


def test_the_original_exception_propagates_unchanged(clock, records):
    with pytest.raises(ValueError, match="boom-with-sensitive-payload"):
        _drive(clock, recently_updated_steps=_SAMPLE_RU, raise_at="recently_updated.sort")


def test_step_never_suppresses_an_exception(clock, records):
    with render_timing.page_render("dashboard"):
        render_timing.mark_setup_complete()
        with pytest.raises(RuntimeError):
            with render_timing.step("data_load.signals_feed"):
                raise RuntimeError("inner")


# --- instrumentation cannot break the page --------------------------------

def test_a_step_emit_failure_cannot_break_the_render(clock, monkeypatch):
    monkeypatch.setattr(
        render_timing, "_emit_steps",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("emit exploded")),
    )

    _drive(clock, data_load_steps=_SAMPLE_DATA_LOAD)  # must not raise


def test_a_logger_failure_cannot_break_the_render(clock, monkeypatch):
    monkeypatch.setattr(
        render_timing._LOGGER, "info",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("logger exploded")),
    )

    _drive(clock, data_load_steps=_SAMPLE_DATA_LOAD, recently_updated_steps=_SAMPLE_RU)


def test_step_outside_a_render_is_a_silent_no_op(records):
    with render_timing.step("data_load.signals_feed"):
        pass

    assert _step_lines(records) == []


def test_step_outside_a_render_still_yields_its_block(records):
    executed = []
    with render_timing.step("data_load.signals_feed"):
        executed.append(True)

    assert executed == [True]


# --- privacy and no behavior change ---------------------------------------

def test_the_record_contains_no_sensitive_material(clock, records):
    _drive(clock, data_load_steps=_SAMPLE_DATA_LOAD, recently_updated_steps=_SAMPLE_RU)

    line = _step_lines(records)[0]
    needles = (
        "postgres" + "ql://", "pass" + "word", "Bearer ", "api" + "_key", "@",
        "Traceback", "cookie", "Authorization", "select " + "*", "http" + "://",
    )
    for needle in needles:
        assert needle.lower() not in line.lower(), needle


def test_the_step_record_carries_only_fixed_identifier_names(clock, records):
    _drive(clock, data_load_steps=_SAMPLE_DATA_LOAD, recently_updated_steps=_SAMPLE_RU)

    for name in _step_map(_step_lines(records)[0]):
        assert re.fullmatch(r"[a-z_]+\.[a-z_]+", name), name


def test_the_instrumented_modules_add_no_rerun_fragment_or_javascript():
    import inspect

    from src.ui.components import recently_updated
    from src.ui.pages import dashboard

    sources = (
        inspect.getsource(dashboard.render),
        inspect.getsource(dashboard._load_daily_news_snapshot),
        inspect.getsource(recently_updated._select_row_sources),
        inspect.getsource(recently_updated.render_recently_updated),
    )
    for source in sources:
        for forbidden in ("st.rerun(", "st.fragment(", "<script", "components.html", "st.components"):
            assert forbidden not in source, forbidden


def test_step_performs_no_io():
    """AST check on step() itself: it reads a clock and writes a dict."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(render_timing.step).lstrip())
    called = {
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert called <= {"monotonic", "get"}, called


# === Aggregate counters on the same record ================================
#
# step() answers "how long did this take"; count() answers "over how many
# items". Production timing already shows recently_updated.news_sources at
# ~2.0s with near-complete coverage, and a local reproduction shows the
# cost is read-time tiering of stories whose materiality_tier was never
# stored. What the durations cannot say is how many such stories exist.
# These counters answer exactly that and nothing else.

COUNTER_TOTAL = "recently_updated.news_stories_total"
COUNTER_UNTIERED = "recently_updated.news_stories_untiered"


def _counter_map(message: str) -> dict[str, int]:
    found = re.search(r'counters="([^"]*)"', message)
    if not found or not found.group(1):
        return {}
    return {p.split("=")[0]: int(p.split("=")[1]) for p in found.group(1).split(",")}


def test_counters_ride_the_existing_step_record(clock, records):
    with render_timing.page_render("dashboard"):
        render_timing.mark_setup_complete()
        with render_timing.step("recently_updated.news_sources"):
            clock.advance_ms(2000.0)
            render_timing.count(COUNTER_TOTAL, 412)
            render_timing.count(COUNTER_UNTIERED, 388)

    lines = _step_lines(records)
    assert len(lines) == 1  # still exactly one record, not a second one
    assert _counter_map(lines[0]) == {COUNTER_TOTAL: 412, COUNTER_UNTIERED: 388}


def test_a_render_with_counters_but_no_steps_still_emits_one_record(clock, records):
    with render_timing.page_render("dashboard"):
        render_timing.mark_setup_complete()
        render_timing.count(COUNTER_TOTAL, 3)

    lines = _step_lines(records)
    assert len(lines) == 1
    assert _counter_map(lines[0]) == {COUNTER_TOTAL: 3}
    assert _step_map(lines[0]) == {}


def test_a_render_with_no_counters_emits_no_counters_field(clock, records):
    _drive(clock, data_load_steps=_SAMPLE_DATA_LOAD)

    assert "counters=" not in _step_lines(records)[0]


def test_counters_are_rendered_as_plain_integers(clock, records):
    with render_timing.page_render("dashboard"):
        render_timing.mark_setup_complete()
        render_timing.count(COUNTER_TOTAL, 7)

    rendered = re.search(r'counters="([^"]*)"', _step_lines(records)[0]).group(1)
    assert rendered == f"{COUNTER_TOTAL}=7"
    assert "." not in rendered.split("=")[1]


def test_a_failed_render_still_reports_the_counters_it_accumulated(clock, records):
    with pytest.raises(ValueError):
        with render_timing.page_render("dashboard"):
            render_timing.mark_setup_complete()
            render_timing.count(COUNTER_TOTAL, 120)
            raise ValueError("boom-with-sensitive-payload")

    line = _step_lines(records)[0]
    assert _counter_map(line)[COUNTER_TOTAL] == 120
    assert _fields(line)["outcome"] == "failed"
    assert "boom-with-sensitive-payload" not in line


def test_counter_names_are_fixed_internal_identifiers(clock, records):
    with render_timing.page_render("dashboard"):
        render_timing.mark_setup_complete()
        render_timing.count(COUNTER_TOTAL, 1)
        render_timing.count(COUNTER_UNTIERED, 1)

    for name in _counter_map(_step_lines(records)[0]):
        assert re.fullmatch(r"[a-z_]+\.[a-z_]+", name), name


def test_count_performs_no_io():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(render_timing.count).lstrip())
    called = {
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert called <= {"get"}, called


# --- the five source-read siblings (Phase 2G) -----------------------------
#
# These replaced a single `data_load.source_reads` step. They are
# siblings rather than children because step() attributes a nested step
# entirely to its enclosing one and contributes no separate key, so a
# child of a surviving parent would have recorded nothing at all.

SOURCE_READ_STEPS = (
    "data_load.source_connection_acquire",
    "data_load.source_filing_repo",
    "data_load.source_filing_query",
    "data_load.source_candidate_repo",
    "data_load.source_candidate_query",
    "data_load.source_exclusions",
)

# Phase 2H: the acquisition happens ONCE for the whole call, before the
# loop; the other five are per-source. Shapes below mirror that exactly.
_ACQUIRE = (("data_load.source_connection_acquire", 180.0),)
# One loop iteration's worth, in the order _load_source_reads performs them.
_ONE_SOURCE = ((SOURCE_READ_STEPS[1], 3.0), (SOURCE_READ_STEPS[2], 40.0),
               (SOURCE_READ_STEPS[3], 3.0), (SOURCE_READ_STEPS[4], 60.0),
               (SOURCE_READ_STEPS[5], 2.0))
_THREE_SOURCES = _ACQUIRE + _ONE_SOURCE * 3


def test_the_old_parent_step_is_gone_and_the_siblings_replace_it():
    assert "data_load.source_reads" not in EXPECTED_DATA_LOAD_STEPS
    for name in SOURCE_READ_STEPS:
        assert name in EXPECTED_DATA_LOAD_STEPS


def test_each_sibling_aggregates_all_three_sources_into_one_key(clock, records):
    _drive(clock, data_load_steps=_THREE_SOURCES)

    steps = _step_map(_step_lines(records)[0])
    assert set(steps) == set(SOURCE_READ_STEPS)  # six keys, not sixteen
    # Acquired once for the whole call, not once per source.
    assert steps["data_load.source_connection_acquire"] == pytest.approx(180.0, abs=0.2)
    assert steps["data_load.source_filing_repo"] == pytest.approx(9.0, abs=0.2)
    assert steps["data_load.source_filing_query"] == pytest.approx(120.0, abs=0.2)
    assert steps["data_load.source_candidate_repo"] == pytest.approx(9.0, abs=0.2)
    assert steps["data_load.source_candidate_query"] == pytest.approx(180.0, abs=0.2)
    assert steps["data_load.source_exclusions"] == pytest.approx(6.0, abs=0.2)


def test_the_siblings_do_not_overlap_and_sum_to_their_container(clock, records):
    _drive(clock, data_load_steps=_THREE_SOURCES)

    fields = _fields(_step_lines(records)[0])
    steps = _step_map(_step_lines(records)[0])
    # 180 acquire + 3 * (3 + 40 + 3 + 60 + 2)
    assert sum(steps.values()) == pytest.approx(504.0, abs=0.3)
    assert float(fields["data_load_step_ms"]) == pytest.approx(504.0, abs=0.3)


def test_a_sibling_nested_in_another_contributes_no_separate_key(clock, records):
    """The guard that makes siblings mandatory: if these were ever
    re-nested, the inner one would vanish rather than double count."""
    with render_timing.page_render("dashboard"):
        render_timing.mark_setup_complete()
        with render_timing.data_load():
            with render_timing.step("data_load.source_filing_repo"):
                clock.advance_ms(30.0)
                with render_timing.step("data_load.source_filing_query"):
                    clock.advance_ms(70.0)

    steps = _step_map(_step_lines(records)[0])
    assert steps == {"data_load.source_filing_repo": pytest.approx(100.0, abs=0.15)}
    assert "data_load.source_filing_query" not in steps


def test_data_load_reconciliation_holds_with_the_siblings(clock, records):
    _drive(clock, data_load_steps=_THREE_SOURCES, data_load_other_ms=9.0)

    fields = _fields(_step_lines(records)[0])
    data_load_ms = float(fields["data_load_ms"])
    step_ms = float(fields["data_load_step_ms"])
    unaccounted_ms = float(fields["data_load_unaccounted_ms"])

    assert step_ms + unaccounted_ms == pytest.approx(data_load_ms, abs=0.3)
    # The loop's own overhead is what lands in the remainder — by design,
    # so no new field is needed to carry it.
    assert unaccounted_ms == pytest.approx(9.0, abs=0.3)


def test_the_untouched_data_load_steps_keep_their_values(clock, records):
    _drive(clock, data_load_steps=(
        ("data_load.signals_feed", 465.0),
        *_THREE_SOURCES,
        ("data_load.daily_news_raw", 215.0),
        ("data_load.daily_news_canonical", 22.0),
        ("data_load.editorial_stories", 208.0),
    ))

    steps = _step_map(_step_lines(records)[0])
    assert steps["data_load.signals_feed"] == pytest.approx(465.0, abs=0.15)
    assert steps["data_load.daily_news_raw"] == pytest.approx(215.0, abs=0.15)
    assert steps["data_load.daily_news_canonical"] == pytest.approx(22.0, abs=0.15)
    assert steps["data_load.editorial_stories"] == pytest.approx(208.0, abs=0.15)


def test_still_exactly_one_step_record_per_render_with_a_bounded_key_set(clock, records):
    # Ten sources' worth of iterations must not grow the record.
    _drive(clock, data_load_steps=_ACQUIRE + _ONE_SOURCE * 10)

    lines = _step_lines(records)
    assert len(lines) == 1
    assert len(_step_map(lines[0])) == 6


def test_a_failed_source_read_still_emits_partial_timing_with_class_name_only(clock, records):
    with pytest.raises(ValueError):
        _drive(clock, data_load_steps=_ACQUIRE + _ONE_SOURCE, raise_at="data_load.source_candidate_repo")

    lines = _step_lines(records)
    assert len(lines) == 1
    fields = _fields(lines[0])
    assert fields["outcome"] == "failed"
    assert fields["failure_kind"] == "ValueError"
    assert "boom-with-sensitive-payload" not in lines[0]

    steps = _step_map(lines[0])
    assert steps["data_load.source_connection_acquire"] == pytest.approx(180.0, abs=0.15)
    assert steps["data_load.source_filing_repo"] == pytest.approx(3.0, abs=0.15)
    assert steps["data_load.source_candidate_repo"] == pytest.approx(3.0, abs=0.15)
    assert "data_load.source_candidate_query" not in steps  # never reached


def test_emitted_step_identifiers_come_only_from_the_fixed_allowlist(clock, records):
    _drive(clock, data_load_steps=_THREE_SOURCES, recently_updated_steps=_SAMPLE_RU)

    allowed = set(EXPECTED_DATA_LOAD_STEPS) | set(EXPECTED_RECENTLY_UPDATED_STEPS)
    assert set(_step_map(_step_lines(records)[0])) <= allowed


def test_the_step_record_carries_no_source_or_record_identifying_text(clock, records):
    """The specific reason the five names do NOT carry a source:
    REGION_SOURCE's values are real source names, so a per-source key
    would put them in the log."""
    from src.logic.market_map import REGION_SOURCE

    _drive(clock, data_load_steps=_THREE_SOURCES)
    line = _step_lines(records)[0]

    for source in REGION_SOURCE.values():
        assert source not in line
    for fragment in ("cand-", "rcept", "corp_code", "postgres://", "postgresql://",
                     "dbname", "password", "@", "SEC", "DART", "EDINET"):
        assert fragment not in line
