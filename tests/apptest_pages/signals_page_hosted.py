"""Durable-State Phase 4G-1 — hosted-collaborator AppTest harness.

Mirrors signals_page.py's shape, but calls signals.render(signal_repository=...)
with an explicit, in-process fake collaborator instead of relying on the
default container.get_repositories() path. No Settings, get_settings,
backend_factory, container.get_repositories, Postgres/SQLite class,
psycopg, environment variable, DSN, external client, Docker, or network
use anywhere in this file — every fake is a plain in-memory object built
from public model fields only.

Mode is selected via st.session_state["_hosted_fake_mode"], pre-seeded by
the calling test before at.run() (a standard AppTest pattern): "normal"
(default), "empty", or "failing".
"""
from __future__ import annotations

import streamlit as st

from src.data_access.interfaces import SignalRepository
from src.models.models import Direction, Horizon, Signal, Strength
from src.ui.pages import signals
from src.ui.ui import with_chrome

_LEAKY_EXCEPTION_MESSAGE = (
    "connection failed: SHOULD_NOT_LEAK_HOST=hosted-db.example.internal "
    "SHOULD_NOT_LEAK_DSN=postgresql://user:pw@hosted-db.example.internal/db "
    "SHOULD_NOT_LEAK_PASSWORD=hunter2"
)


class _FakeSignalRepository(SignalRepository):
    def __init__(self, signals: list[Signal] | None = None, exc: Exception | None = None) -> None:
        self._signals = signals or []
        self._exc = exc

    def get_all_signals(self) -> list[Signal]:
        if self._exc is not None:
            raise self._exc
        return list(self._signals)

    def get_signals_for_theme(self, theme_slug: str) -> list[Signal]:
        if self._exc is not None:
            raise self._exc
        return [s for s in self._signals if s.theme_slug == theme_slug]


def _published_fixture_signal() -> Signal:
    return Signal(
        id="hosted-fixture-signal-1",
        title="Hosted Fixture Signal — Memory Capacity Expansion",
        theme_slug="memory",
        subtheme_slug=None,
        direction=Direction.IMPROVING,
        strength=Strength.STRONG,
        horizon=Horizon.MULTI_QUARTER,
        evidence_count=1,
        interpretation="Hosted fixture interpretation text.",
        contrary_evidence="",
        validation_criteria="",
        invalidation_criteria="",
        related_tickers=["000660"],
        last_updated="2026-08-01T00:00:00+00:00",
        is_demo=False,
        issuer="Hosted Fixture Issuer Co.",
        source_name="OpenDART / DART",
        source_url="https://dart.fss.or.kr/hosted-fixture",
        excerpt="Hosted fixture excerpt text, original language.",
    )


_mode = st.session_state.get("_hosted_fake_mode", "normal")
if _mode == "empty":
    _repo: SignalRepository = _FakeSignalRepository(signals=[])
elif _mode == "failing":
    _repo = _FakeSignalRepository(exc=RuntimeError(_LEAKY_EXCEPTION_MESSAGE))
else:
    _repo = _FakeSignalRepository(signals=[_published_fixture_signal()])

with_chrome(lambda: signals.render(signal_repository=_repo), "signals")()
