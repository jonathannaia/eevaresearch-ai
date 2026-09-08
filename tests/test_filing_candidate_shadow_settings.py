"""Daily News Filing-Event Shadow Adapter, Batch 2b — settings-flag
tests for the three new, disabled-by-default shadow flags. Mirrors
test_edgar_auto_publish_settings.py's own pattern exactly (same
_parse_beta_auth_enabled parser, same unset/blank/malformed/true
coverage), one class per flag so the three sources never share a test
body."""
from __future__ import annotations

import pytest

from src.config.settings import Settings

_FLAG_CASES = (
    ("EDGE_EDGAR_FILING_CANDIDATE_SHADOW_ENABLED", "edgar_filing_candidate_shadow_enabled"),
    ("EDGE_DART_FILING_CANDIDATE_SHADOW_ENABLED", "dart_filing_candidate_shadow_enabled"),
    ("EDGE_EDINET_FILING_CANDIDATE_SHADOW_ENABLED", "edinet_filing_candidate_shadow_enabled"),
)


@pytest.mark.parametrize("env_var,attr_name", _FLAG_CASES)
def test_flag_defaults_false_when_env_is_unset(env_var, attr_name, monkeypatch):
    monkeypatch.delenv(env_var, raising=False)

    assert getattr(Settings(), attr_name) is False


@pytest.mark.parametrize("env_var,attr_name", _FLAG_CASES)
def test_flag_defaults_false_when_env_is_blank(env_var, attr_name, monkeypatch):
    monkeypatch.setenv(env_var, "")

    assert getattr(Settings(), attr_name) is False


@pytest.mark.parametrize("env_var,attr_name", _FLAG_CASES)
def test_flag_defaults_false_when_env_is_whitespace_only(env_var, attr_name, monkeypatch):
    monkeypatch.setenv(env_var, "   ")

    assert getattr(Settings(), attr_name) is False


@pytest.mark.parametrize("env_var,attr_name", _FLAG_CASES)
def test_flag_is_false_for_explicit_false_value(env_var, attr_name, monkeypatch):
    monkeypatch.setenv(env_var, "false")

    assert getattr(Settings(), attr_name) is False


@pytest.mark.parametrize("env_var,attr_name", _FLAG_CASES)
def test_flag_fails_closed_for_malformed_or_unrecognized_value(env_var, attr_name, monkeypatch):
    for malformed_value in ("enabled", "yesplease", "TRU", "2", "null"):
        monkeypatch.setenv(env_var, malformed_value)
        assert getattr(Settings(), attr_name) is False, malformed_value


@pytest.mark.parametrize("env_var,attr_name", _FLAG_CASES)
def test_flag_requires_explicit_true_value(env_var, attr_name, monkeypatch):
    for true_value in ("1", "true", "True", "TRUE", "yes", "on"):
        monkeypatch.setenv(env_var, true_value)
        assert getattr(Settings(), attr_name) is True, true_value


def test_all_three_flags_default_false_together_on_a_single_settings_instance(monkeypatch):
    for env_var, _ in _FLAG_CASES:
        monkeypatch.delenv(env_var, raising=False)

    settings = Settings()
    assert settings.edgar_filing_candidate_shadow_enabled is False
    assert settings.dart_filing_candidate_shadow_enabled is False
    assert settings.edinet_filing_candidate_shadow_enabled is False


def test_flags_are_independent_of_each_other_and_of_the_edinet_material_event_lexicon_flag(monkeypatch):
    # Enabling one shadow flag must never flip another, and must never
    # flip EDINET's separate, pre-existing material_event_lexicon_enabled
    # flag — the two features are wired completely independently.
    monkeypatch.delenv("EDGE_DART_FILING_CANDIDATE_SHADOW_ENABLED", raising=False)
    monkeypatch.delenv("EDGE_EDINET_FILING_CANDIDATE_SHADOW_ENABLED", raising=False)
    monkeypatch.delenv("EDGE_EDINET_MATERIAL_EVENT_LEXICON_ENABLED", raising=False)
    monkeypatch.setenv("EDGE_EDGAR_FILING_CANDIDATE_SHADOW_ENABLED", "true")

    settings = Settings()
    assert settings.edgar_filing_candidate_shadow_enabled is True
    assert settings.dart_filing_candidate_shadow_enabled is False
    assert settings.edinet_filing_candidate_shadow_enabled is False
    assert settings.edinet_material_event_lexicon_enabled is False
