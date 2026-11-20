"""Guards on tests/no_network.py itself.

Every test here proves the blocker *rejects* an attempt. None of them
makes a real connection: the stub raises before any syscall, and the
addresses used are never actually dialled.
"""
from __future__ import annotations

import socket
import subprocess
import sys

import pytest

from tests.no_network import NetworkCallBlocked, block_network

# Discard port on loopback. Never reached — the blocker raises first —
# but chosen so that a regression which *failed* to block could not
# reach anything meaningful either.
_UNREACHED = ("127.0.0.1", 9)


def test_socket_connect_is_blocked(monkeypatch):
    block_network(monkeypatch)

    with pytest.raises(NetworkCallBlocked):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(_UNREACHED)


def test_socket_connect_ex_is_blocked(monkeypatch):
    """`connect_ex` returns an errno instead of raising, so a client
    using it would otherwise slip past a `connect`-only patch."""
    block_network(monkeypatch)

    with pytest.raises(NetworkCallBlocked):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect_ex(_UNREACHED)


def test_create_connection_is_blocked(monkeypatch):
    block_network(monkeypatch)

    with pytest.raises(NetworkCallBlocked):
        socket.create_connection(_UNREACHED, timeout=0.01)


def test_a_real_http_client_cannot_slip_past_it(monkeypatch):
    """The point of blocking at the socket layer: the block holds for a
    client the helper knows nothing about, and `requests`' own retry
    handling cannot swallow it (it catches OSError, not AssertionError)."""
    requests = pytest.importorskip("requests")
    block_network(monkeypatch)

    with pytest.raises(NetworkCallBlocked):
        requests.get("http://127.0.0.1:9/", timeout=0.01)


def test_urllib_cannot_slip_past_it(monkeypatch):
    import urllib.request

    block_network(monkeypatch)

    with pytest.raises(NetworkCallBlocked):
        urllib.request.urlopen("http://127.0.0.1:9/", timeout=0.01)


def test_monkeypatch_restores_the_real_entry_points_afterwards():
    """No `block_network` call in this test: if teardown leaked, the
    real attributes would still be the stubs."""
    assert socket.socket.connect is not None
    assert getattr(socket.socket.connect, "__name__", "") != "_blocked"
    assert getattr(socket.create_connection, "__name__", "") == "create_connection"


def test_it_does_not_cover_subprocess_networking(monkeypatch):
    """Pins the documented limitation rather than leaving it as a claim:
    the patch lives in this interpreter only, so a child process still
    holds the real socket methods. Asserted by inspecting the child's
    own attributes — the child makes no connection."""
    block_network(monkeypatch)

    result = subprocess.run(
        [sys.executable, "-c", "import socket; print(socket.socket.connect.__name__)"],
        capture_output=True, text=True, check=True,
    )

    assert result.stdout.strip() == "connect"


def test_it_is_not_an_autouse_fixture():
    """It must stay an explicitly invoked helper, so it can never widen
    to tests that assert about live-call behaviour."""
    import tests.no_network as mod

    assert not hasattr(mod, "pytest_plugins")
    assert not any(
        hasattr(getattr(mod, n), "_pytestfixturefunction") for n in dir(mod) if not n.startswith("__")
    )
