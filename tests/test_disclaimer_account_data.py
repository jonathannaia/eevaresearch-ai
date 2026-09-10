"""Admin Users v1 (design/DECISIONS.md) — the Disclaimer page's "Account
data" disclosure must name every field V15's user_accounts table
actually persists (email, display name, first/last sign-in timestamps,
sign-in count), and must not claim collection of anything v1 doesn't
persist (page views, IP address, device/fingerprint, payment, OAuth
tokens, cookies)."""
from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

_HARNESS = Path(__file__).parent / "apptest_pages" / "disclaimer_page.py"


def _account_data_text(at: AppTest) -> str:
    return " ".join(m.value for m in at.main.get("markdown") if not m.value.startswith("<style>"))


def test_account_data_section_discloses_sign_in_count():
    at = AppTest.from_file(str(_HARNESS), default_timeout=10)
    at.run()

    assert not at.exception
    text = _account_data_text(at)
    assert "email" in text.lower()
    assert "display name" in text.lower()
    assert "sign-in" in text.lower() and "timestamp" in text.lower()
    assert "sign-in count" in text.lower()


def test_account_data_section_does_not_claim_data_v1_does_not_collect():
    at = AppTest.from_file(str(_HARNESS), default_timeout=10)
    at.run()

    assert not at.exception
    text = _account_data_text(at).lower()
    for not_collected in ("page view", "ip address", "device", "fingerprint", "payment", "oauth token", "cookie"):
        assert not_collected not in text
