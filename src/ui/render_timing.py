"""Phase 2A — production-safe page-render timing instrumentation.

Why this exists. After PR #74 removed repeated Postgres schema-migration
checks, a live measurement showed cold Radar Inbox navigation dropping
from ~6.9s to ~0.8s while ordinary WARM navigation stayed at roughly one
second on every page (Dashboard 988/999/1000ms, Themes 999ms, Coverage
1011ms). That floor is now the dominant cost in normal use, and nothing
in the codebase measures it: the only prior instrumentation used
`print()`, which Python block-buffers on a non-TTY stdout, so those lines
reached the hosting platform's log stream only when the process exited.

This module records ONE structured line per completed page render, split
into three phases that reconcile to the total by construction:

    total_python_ms = setup_ms + data_load_ms + ui_build_ms

  setup_ms     theme resolution, CSS, the theme bridge, sidebar and
               topbar — everything the shared chrome does before the
               page's own body runs.
  data_load_ms the time a page spends inside explicitly-declared
               data_load() blocks. Summed, so a page may declare more
               than one.
  ui_build_ms  the remainder: the page body's own widget construction
               plus the footer. Derived, never measured separately, so
               the three always add up.

Instrumentation only. This module performs no I/O beyond writing a log
record: no network call, database query, cache read or write, background
job, telemetry vendor, analytics beacon, browser timing code, or
external-source access. It never calls st.rerun(), st.fragment(), or any
Streamlit API at all, and it changes no page result, control, route,
state, cache key, or TTL.

Privacy. A record carries only a route key that is a fixed internal
identifier from app.py's own page table, a process-local ordinal, three
durations, an optional cache status, and an outcome. It never carries a
secret, cookie, authorization header, query string, user-entered text,
DSN, database identifier, IP address, exception message, or traceback —
a failed render logs `outcome="failed"` and the exception's CLASS NAME
only, the same discipline backend_factory.py already applies to
connection errors.

Volume is bounded at one record per page render: there is no per-item,
per-row, or per-widget event, and the helpers below are no-ops when no
render is in progress, so importing this module can never start emitting
records on its own.

Threading. Streamlit runs each browser session's script in its own
thread, so the in-flight record lives in `threading.local()`. A helper
called outside a page_render() block finds no record and returns
silently rather than raising — instrumentation must never be able to
break a page.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

LOGGER_NAME = "eeva.render_timing"
_LOGGER_HANDLER_MARKER = "_eeva_render_timing_owned"
_LOGGER = logging.getLogger(LOGGER_NAME)

_ORDINAL_LOCK = threading.Lock()
_render_ordinal = 0

_STATE = threading.local()

OUTCOME_COMPLETED = "completed"
OUTCOME_FAILED = "failed"


def get_logger(name: str) -> logging.Logger:
    """A logger that actually reaches the platform log stream.

    Public because more than one module needs this: the project
    configures no logging globally, so a plain `logging.getLogger(...)`
    record propagates to a root logger with no handlers and is silently
    discarded. Any module emitting operational diagnostics must go
    through here (or an equivalent) or its records simply vanish in
    production — exactly the failure mode the old `print()` diagnostics
    had, for a different reason."""
    logger = logging.getLogger(name)
    _configure(logger, f"_eeva_owned_{name.replace('.', '_')}")
    return logger


def _ensure_logger_configured() -> None:
    """Idempotently attach exactly one handler this module owns.

    The project configures no logging globally — no `logging.basicConfig`
    and no handlers on the root logger — so an INFO record would
    otherwise be discarded. This configures only its own named logger and
    never touches the root logger, with `propagate=False` so nothing is
    duplicated into a root handler a host application may add later.

    Ownership is tracked by a private attribute marker rather than a bare
    `if not _LOGGER.handlers`, so a handler attached by someone else can
    neither suppress ours nor be mistaken for it, and repeated calls
    never stack duplicates.

    The handler writes to `sys.stderr`, which is line-buffered, so each
    record reaches the platform log stream as it is emitted. That is the
    whole reason this module exists rather than more `print()` calls:
    stdout is block-buffered off a TTY, which is why the previous timing
    diagnostics only ever appeared at process shutdown."""
    _configure(_LOGGER, _LOGGER_HANDLER_MARKER)


def _configure(logger: logging.Logger, marker: str) -> None:
    for handler in logger.handlers:
        if getattr(handler, marker, False):
            return
    handler = logging.StreamHandler(sys.stderr)
    setattr(handler, marker, True)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def _next_ordinal() -> int:
    global _render_ordinal
    with _ORDINAL_LOCK:
        _render_ordinal += 1
        return _render_ordinal


@dataclass
class _RenderRecord:
    route: str
    ordinal: int
    started_at: float
    setup_ms: float | None = None
    data_load_ms: float = 0.0
    cache_status: str | None = None
    _data_load_depth: int = field(default=0, repr=False)


def _current() -> _RenderRecord | None:
    return getattr(_STATE, "record", None)


def mark_setup_complete() -> None:
    """Closes the setup phase. Called once by the shared chrome wrapper
    immediately before the page body runs. A second call is ignored, so
    the boundary can never be moved by a nested render."""
    record = _current()
    if record is None or record.setup_ms is not None:
        return
    record.setup_ms = (time.monotonic() - record.started_at) * 1000


def set_cache_status(status: str | None) -> None:
    """Records an already-known cache outcome ("hit"/"miss"). Only ever
    called where a page knows this for free — never by performing a
    lookup, probe, or extra read to discover it."""
    record = _current()
    if record is not None and status:
        record.cache_status = status


@contextmanager
def data_load(cache_status: str | None = None):
    """Marks a block as data loading. Durations from sibling blocks are
    summed; a nested block is attributed to its outermost enclosing one
    so the same milliseconds are never counted twice. A no-op outside a
    page_render() block, and it never suppresses an exception."""
    record = _current()
    if record is None:
        yield
        return
    if cache_status:
        record.cache_status = cache_status
    if record._data_load_depth > 0:  # already inside a data_load block
        record._data_load_depth += 1
        try:
            yield
        finally:
            record._data_load_depth -= 1
        return
    record._data_load_depth = 1
    started_at = time.monotonic()
    try:
        yield
    finally:
        record.data_load_ms += (time.monotonic() - started_at) * 1000
        record._data_load_depth = 0


@contextmanager
def page_render(route: str):
    """Times one page render and emits exactly one structured record.

    A render that raises emits `outcome="failed"` carrying the
    exception's class name only — never its message, arguments, or
    traceback — and then re-raises unchanged, so this wrapper can never
    alter a page's error behavior. A nested call (a page rendering
    another page) is passed through untimed so the outer record stays the
    single record for the navigation."""
    if _current() is not None:
        yield
        return

    record = _RenderRecord(route=route, ordinal=_next_ordinal(), started_at=time.monotonic())
    _STATE.record = record
    outcome = OUTCOME_COMPLETED
    failure_kind: str | None = None
    try:
        yield
    except BaseException as exc:
        outcome = OUTCOME_FAILED
        failure_kind = type(exc).__name__
        raise
    finally:
        _STATE.record = None
        try:
            _emit(record, outcome, failure_kind)
        except Exception:  # noqa: BLE001 — instrumentation must never break a page
            pass


def _emit(record: _RenderRecord, outcome: str, failure_kind: str | None) -> None:
    total_ms = (time.monotonic() - record.started_at) * 1000
    setup_ms = record.setup_ms if record.setup_ms is not None else total_ms
    data_load_ms = record.data_load_ms
    # Derived, so the three phases always reconcile to the total.
    ui_build_ms = total_ms - setup_ms - data_load_ms
    _ensure_logger_configured()
    message = (
        f'event="page_render_timing" route="{record.route}" '
        f"render_ordinal={record.ordinal} "
        f"total_python_ms={total_ms:.1f} setup_ms={setup_ms:.1f} "
        f"data_load_ms={data_load_ms:.1f} ui_build_ms={ui_build_ms:.1f} "
        f'cache_status="{record.cache_status or "unknown"}" outcome="{outcome}"'
    )
    if failure_kind is not None:
        message += f' failure_kind="{failure_kind}"'
    _LOGGER.info(message)
