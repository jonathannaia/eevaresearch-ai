"""Durable-State Phase 4M-4 — Render auth-secrets generation
(scripts/render_generate_secrets.py). Every test uses only synthetic,
clearly-fake placeholder values — never a real Google OAuth credential,
cookie secret, or DSN — and no test ever asserts a real secret value
appears anywhere; assertions check *shape* (valid TOML, correct keys,
correct escaping) and *absence* (never a value, never a raw exception)."""
from __future__ import annotations

import stat
import tomllib

import pytest

from scripts import render_generate_secrets as gen

_FAKE_ENV = {
    "EDGE_AUTH_REDIRECT_URI": "https://example-test.onrender.com/oauth2callback",
    "EDGE_AUTH_COOKIE_SECRET": "synthetic-test-cookie-secret",
    "EDGE_GOOGLE_CLIENT_ID": "synthetic-client-id.apps.googleusercontent.com",
    "EDGE_GOOGLE_CLIENT_SECRET": "synthetic-client-secret",
    "EDGE_GOOGLE_SERVER_METADATA_URL": "https://accounts.google.com/.well-known/openid-configuration",
}


def _template_text() -> str:
    return gen.TEMPLATE_PATH.read_text(encoding="utf-8")


# --- The template itself is always valid, safe-to-commit TOML ---

def test_template_file_is_valid_toml_with_placeholder_values():
    parsed = tomllib.loads(_template_text())
    assert parsed["auth"]["redirect_uri"] == "${EDGE_AUTH_REDIRECT_URI}"
    assert parsed["auth"]["cookie_secret"] == "${EDGE_AUTH_COOKIE_SECRET}"
    assert parsed["auth"]["google"]["client_id"] == "${EDGE_GOOGLE_CLIENT_ID}"
    assert parsed["auth"]["google"]["client_secret"] == "${EDGE_GOOGLE_CLIENT_SECRET}"
    assert parsed["auth"]["google"]["server_metadata_url"] == "${EDGE_GOOGLE_SERVER_METADATA_URL}"


def test_template_contains_no_real_looking_secret_literal():
    """The template is committed to git — it must never contain
    anything except placeholders and comments."""
    text = _template_text()
    for name in gen.REQUIRED_VARS:
        assert f"${{{name}}}" in text  # every placeholder is present
    # No line looks like an actual assigned (non-placeholder) value for
    # any of the five auth fields.
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if "=" in stripped:
            _, _, value = stripped.partition("=")
            assert "${" in value, f"non-placeholder value found in template: {stripped!r}"


# --- Pure substitution logic — no file I/O, fully synthetic ---

def test_render_secrets_toml_substitutes_all_placeholders_correctly():
    rendered = gen.render_secrets_toml(_template_text(), _FAKE_ENV)
    parsed = tomllib.loads(rendered)
    assert parsed["auth"]["redirect_uri"] == _FAKE_ENV["EDGE_AUTH_REDIRECT_URI"]
    assert parsed["auth"]["cookie_secret"] == _FAKE_ENV["EDGE_AUTH_COOKIE_SECRET"]
    assert parsed["auth"]["google"]["client_id"] == _FAKE_ENV["EDGE_GOOGLE_CLIENT_ID"]
    assert parsed["auth"]["google"]["client_secret"] == _FAKE_ENV["EDGE_GOOGLE_CLIENT_SECRET"]
    assert parsed["auth"]["google"]["server_metadata_url"] == _FAKE_ENV["EDGE_GOOGLE_SERVER_METADATA_URL"]


def test_render_secrets_toml_escapes_quotes_and_backslashes_safely():
    """A value containing a double-quote or backslash must not break out
    of the template's quoted TOML string or corrupt the file's syntax —
    proven by round-tripping through a real TOML parser."""
    env = dict(_FAKE_ENV)
    env["EDGE_GOOGLE_CLIENT_SECRET"] = 'weird"value\\with-both'
    rendered = gen.render_secrets_toml(_template_text(), env)
    parsed = tomllib.loads(rendered)  # would raise TOMLDecodeError if escaping were wrong
    assert parsed["auth"]["google"]["client_secret"] == 'weird"value\\with-both'


def test_render_secrets_toml_tolerates_a_hash_character_in_a_value():
    """A literal '#' inside a value must never be treated as a TOML
    comment marker, since it's always embedded inside an already-quoted
    string — a real risk class for any config value containing one
    (e.g. a generated cookie secret)."""
    env = dict(_FAKE_ENV)
    env["EDGE_AUTH_COOKIE_SECRET"] = "abc#def#ghi"
    rendered = gen.render_secrets_toml(_template_text(), env)
    parsed = tomllib.loads(rendered)
    assert parsed["auth"]["cookie_secret"] == "abc#def#ghi"


@pytest.mark.parametrize("missing_var", list(gen.REQUIRED_VARS))
def test_render_secrets_toml_raises_when_one_required_var_is_missing(missing_var):
    env = dict(_FAKE_ENV)
    del env[missing_var]
    with pytest.raises(gen.MissingEnvironmentVariableError) as exc_info:
        gen.render_secrets_toml(_template_text(), env)
    assert exc_info.value.missing_names == [missing_var]


def test_render_secrets_toml_raises_when_a_required_var_is_blank():
    env = dict(_FAKE_ENV)
    env["EDGE_GOOGLE_CLIENT_ID"] = "   "
    with pytest.raises(gen.MissingEnvironmentVariableError) as exc_info:
        gen.render_secrets_toml(_template_text(), env)
    assert exc_info.value.missing_names == ["EDGE_GOOGLE_CLIENT_ID"]


def test_render_secrets_toml_reports_every_missing_var_at_once():
    env = {}
    with pytest.raises(gen.MissingEnvironmentVariableError) as exc_info:
        gen.render_secrets_toml(_template_text(), env)
    assert set(exc_info.value.missing_names) == set(gen.REQUIRED_VARS)


def test_missing_environment_variable_error_message_names_vars_never_values():
    env = dict(_FAKE_ENV)
    del env["EDGE_GOOGLE_CLIENT_SECRET"]
    with pytest.raises(gen.MissingEnvironmentVariableError) as exc_info:
        gen.render_secrets_toml(_template_text(), env)
    message = str(exc_info.value)
    assert "EDGE_GOOGLE_CLIENT_SECRET" in message
    for value in _FAKE_ENV.values():
        assert value not in message


# --- main(): file writing, permissions, no secret leakage ---

def test_main_writes_a_valid_toml_file_with_owner_only_permissions(tmp_path, monkeypatch):
    template_path = tmp_path / "secrets.tmpl.toml"
    output_path = tmp_path / "secrets.toml"
    template_path.write_text(_template_text(), encoding="utf-8")
    monkeypatch.setattr(gen, "TEMPLATE_PATH", template_path)
    monkeypatch.setattr(gen, "OUTPUT_PATH", output_path)
    for name, value in _FAKE_ENV.items():
        monkeypatch.setenv(name, value)

    rc = gen.main()

    assert rc == 0
    assert output_path.exists()
    parsed = tomllib.loads(output_path.read_text(encoding="utf-8"))
    assert parsed["auth"]["google"]["client_id"] == _FAKE_ENV["EDGE_GOOGLE_CLIENT_ID"]
    mode = stat.S_IMODE(output_path.stat().st_mode)
    assert mode == 0o600


def test_main_fails_closed_and_writes_nothing_when_a_var_is_missing(tmp_path, monkeypatch, capsys):
    template_path = tmp_path / "secrets.tmpl.toml"
    output_path = tmp_path / "secrets.toml"
    template_path.write_text(_template_text(), encoding="utf-8")
    monkeypatch.setattr(gen, "TEMPLATE_PATH", template_path)
    monkeypatch.setattr(gen, "OUTPUT_PATH", output_path)
    for name, value in _FAKE_ENV.items():
        if name != "EDGE_GOOGLE_CLIENT_SECRET":
            monkeypatch.setenv(name, value)
    monkeypatch.delenv("EDGE_GOOGLE_CLIENT_SECRET", raising=False)

    rc = gen.main()

    assert rc == 1
    assert not output_path.exists()
    captured = capsys.readouterr()
    assert "EDGE_GOOGLE_CLIENT_SECRET" in captured.err
    for value in _FAKE_ENV.values():
        assert value not in captured.err
        assert value not in captured.out


def test_main_never_prints_any_configured_value_on_success(tmp_path, monkeypatch, capsys):
    template_path = tmp_path / "secrets.tmpl.toml"
    output_path = tmp_path / "secrets.toml"
    template_path.write_text(_template_text(), encoding="utf-8")
    monkeypatch.setattr(gen, "TEMPLATE_PATH", template_path)
    monkeypatch.setattr(gen, "OUTPUT_PATH", output_path)
    for name, value in _FAKE_ENV.items():
        monkeypatch.setenv(name, value)

    gen.main()

    captured = capsys.readouterr()
    for value in _FAKE_ENV.values():
        assert value not in captured.out
        assert value not in captured.err


def test_main_reports_a_clear_error_when_template_file_is_absent(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gen, "TEMPLATE_PATH", tmp_path / "does-not-exist.toml")
    monkeypatch.setattr(gen, "OUTPUT_PATH", tmp_path / "secrets.toml")

    rc = gen.main()

    assert rc == 1
    assert not (tmp_path / "secrets.toml").exists()
    assert "template not found" in capsys.readouterr().err


# --- Repository hygiene: the generated file must never be trackable, the template always must be ---

def test_gitignore_excludes_the_real_secrets_file_but_not_the_template():
    gitignore_text = (gen.PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    lines = [line.strip() for line in gitignore_text.splitlines()]
    assert ".streamlit/secrets.toml" in lines
    assert ".streamlit/secrets.tmpl.toml" not in lines


def test_render_start_script_references_the_generator_and_correct_streamlit_command():
    """Structural check on scripts/render_start.sh — the exact
    production entrypoint Render's Start Command must invoke — without
    actually executing it (no subprocess call in this test suite)."""
    script_text = (gen.PROJECT_ROOT / "scripts" / "render_start.sh").read_text(encoding="utf-8")
    assert "render_generate_secrets.py" in script_text
    assert "streamlit run app.py" in script_text
    assert "--server.address 0.0.0.0" in script_text
    assert "--server.port" in script_text
    assert "PORT" in script_text
