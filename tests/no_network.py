"""An explicitly-invoked blocker for outbound socket connections.

Why this exists. The configured-state page tests seed local fixtures and
render through `AppTest`; none of them should reach the network. Several
of those files already stub the known scan/process service seams
(`_guard_against_live_calls` and friends), but that only covers the
entry points someone thought to list. This helper closes the layer
underneath: the three calls every in-process Python HTTP client
ultimately funnels through, so an outbound call added anywhere beneath a
render fails the test loudly instead of silently succeeding on a machine
that happens to have credentials.

Scope, stated precisely. This covers **in-process socket calls only**.
It patches attributes in this interpreter, so it does not affect a
subprocess — `tests/test_no_network.py` pins that limitation explicitly,
and the repo's git-based scope guards do shell out to `git`, which is
local and unaffected. It is not, and must not be described as, universal
network isolation: a UDP send, a pre-existing open connection, or a C
extension holding its own socket would all bypass it.

Deliberately NOT autouse across the suite. It is an ordinary function
that a test, or a single file's own fixture, calls explicitly — the same
convention as `tests/configured_test_settings.py` — so it can never
widen to tests that are asserting about live-call behaviour. Because it
installs through `monkeypatch`, calling it from a fixture (or as a
test's first statement) puts the block in place before any `AppTest` is
constructed, and `monkeypatch` tears it down when the test ends.
"""
from __future__ import annotations

import socket


class NetworkCallBlocked(AssertionError):
    """Raised when a test running under `block_network` attempts an
    outbound connection. An `AssertionError` subclass so it reads as a
    test failure, and so `urllib3`/`requests` retry logic — which
    catches `OSError`, not `AssertionError` — cannot swallow it."""


_MESSAGE = "Outbound network connection attempted in a test that must stay network-free."


def block_network(monkeypatch) -> None:
    """Patch the three in-process connection entry points to raise.

    Pass the test's own `monkeypatch` fixture. Covers in-process socket
    calls only — not subprocess networking.
    """

    def _blocked(*_args, **_kwargs):
        raise NetworkCallBlocked(_MESSAGE)

    monkeypatch.setattr(socket.socket, "connect", _blocked, raising=True)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked, raising=True)
    monkeypatch.setattr(socket, "create_connection", _blocked, raising=True)
