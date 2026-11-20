"""Guards on tests/configured_test_settings.py itself — the helper that
lets page tests render a configured Radar/Signals state without reading
the developer's local .env."""
from __future__ import annotations

from pathlib import Path

from tests.configured_test_settings import (
    READINESS_FIELDS,
    _PLACEHOLDER_PREFIX,
    configured_settings,
)


def test_every_readiness_field_is_explicitly_populated(tmp_path):
    """The whole point: nothing is inherited from the environment."""
    settings = configured_settings(tmp_path)

    for field in READINESS_FIELDS:
        value = getattr(settings, field)
        assert isinstance(value, str) and value.strip(), field


def test_no_value_is_or_resembles_a_real_credential(tmp_path):
    """A real key can never be introduced here unnoticed."""
    settings = configured_settings(tmp_path)

    for field in READINESS_FIELDS:
        assert getattr(settings, field).startswith(_PLACEHOLDER_PREFIX), field


def test_it_does_not_read_the_ambient_environment(monkeypatch, tmp_path):
    """Set every readiness variable to a distinctive ambient value and
    prove none of them reaches the returned Settings -- this is the
    isolation property the helper exists to provide."""
    for env_name in (
        "EDGE_DART_API_KEY", "EDGE_TRANSLATION_API_KEY",
        "EDGE_EDGAR_USER_AGENT", "EDGE_EDINET_SUBSCRIPTION_KEY",
    ):
        monkeypatch.setenv(env_name, "ambient-value-that-must-not-leak")

    settings = configured_settings(tmp_path)

    for field in READINESS_FIELDS:
        assert getattr(settings, field) != "ambient-value-that-must-not-leak", field


def test_cache_dir_is_whatever_the_caller_passed(tmp_path):
    """Callers always pass a tmp_path; the helper must never substitute
    the real data/cache."""
    settings = configured_settings(tmp_path)

    assert settings.cache_dir == tmp_path
    assert Path("data/cache").resolve() != Path(settings.cache_dir).resolve()


def test_overrides_win_so_a_single_source_can_still_be_unconfigured(tmp_path):
    """Preserves the ability to assert a one-source-missing case without
    losing isolation on the others."""
    settings = configured_settings(tmp_path, dart_api_key=None)

    assert settings.dart_api_key is None
    assert settings.translation_api_key.startswith(_PLACEHOLDER_PREFIX)
    assert settings.edgar_user_agent.startswith(_PLACEHOLDER_PREFIX)


def test_it_is_not_an_autouse_fixture(tmp_path):
    """It must stay an explicitly imported function. A plain Settings()
    built without it still inherits the environment -- which is exactly
    why tests asserting missing configuration keep building their own."""
    import tests.configured_test_settings as mod

    assert not hasattr(mod, "pytest_plugins")
    assert not any(hasattr(getattr(mod, n), "_pytestfixturefunction") for n in dir(mod) if not n.startswith("__"))
