from src.config.settings import Settings


def test_edgar_auto_publish_is_disabled_when_env_is_unset(monkeypatch):
    monkeypatch.delenv("EDGE_EDGAR_AUTO_PUBLISH_ENABLED", raising=False)

    assert Settings().edgar_auto_publish_enabled is False


def test_edgar_auto_publish_requires_explicit_true_value(monkeypatch):
    monkeypatch.setenv("EDGE_EDGAR_AUTO_PUBLISH_ENABLED", "true")

    assert Settings().edgar_auto_publish_enabled is True


def test_edgar_auto_publish_fails_closed_for_unrecognized_value(monkeypatch):
    monkeypatch.setenv("EDGE_EDGAR_AUTO_PUBLISH_ENABLED", "enabled")

    assert Settings().edgar_auto_publish_enabled is False
