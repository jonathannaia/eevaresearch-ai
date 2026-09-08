"""DeepLProvider — fully mocked requests.Session, zero network, no real
API key. Response shape mirrors DeepL's own documented API reference."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from src.data_access.translation.deepl_provider import DeepLProvider
from src.data_access.translation.interfaces import (
    TranslationApiError,
    TranslationConfigError,
    TranslationRateLimitError,
    TranslationTimeoutError,
)


def _mock_response(status_code=200, json_payload=None):
    resp = MagicMock()
    resp.status_code = status_code
    if json_payload is not None:
        resp.json.return_value = json_payload
    else:
        resp.json.side_effect = ValueError("no json body")
    return resp


def test_raises_config_error_without_api_key():
    provider = DeepLProvider(api_key=None, session=MagicMock())
    with pytest.raises(TranslationConfigError):
        provider.translate("신규시설투자등", "KO", "EN")


def test_successful_translation_returns_text():
    session = MagicMock()
    session.post.return_value = _mock_response(200, {
        "translations": [{"detected_source_language": "KO", "text": "New facility investment, etc.", "billed_characters": 8}],
    })
    provider = DeepLProvider(api_key="fake-key", session=session)

    result = provider.translate("신규시설투자등", "KO", "EN")

    assert result == "New facility investment, etc."


def test_request_uses_authorization_header_with_deepl_auth_key_scheme():
    session = MagicMock()
    session.post.return_value = _mock_response(200, {"translations": [{"text": "ok"}]})
    provider = DeepLProvider(api_key="fake-key", session=session)

    provider.translate("text", "KO", "EN")

    _, kwargs = session.post.call_args
    assert kwargs["headers"]["Authorization"] == "DeepL-Auth-Key fake-key"


def test_free_tier_key_uses_free_host():
    session = MagicMock()
    session.post.return_value = _mock_response(200, {"translations": [{"text": "ok"}]})
    provider = DeepLProvider(api_key="fake-key:fx", session=session)

    provider.translate("text", "KO", "EN")

    args, _ = session.post.call_args
    assert args[0] == "https://api-free.deepl.com/v2/translate"


def test_pro_tier_key_uses_pro_host():
    session = MagicMock()
    session.post.return_value = _mock_response(200, {"translations": [{"text": "ok"}]})
    provider = DeepLProvider(api_key="fake-pro-key", session=session)

    provider.translate("text", "KO", "EN")

    args, _ = session.post.call_args
    assert args[0] == "https://api.deepl.com/v2/translate"


def test_429_status_raises_rate_limit_error():
    session = MagicMock()
    session.post.return_value = _mock_response(429)
    provider = DeepLProvider(api_key="fake-key", session=session)

    with pytest.raises(TranslationRateLimitError):
        provider.translate("text", "KO", "EN")


def test_non_200_status_raises_api_error():
    session = MagicMock()
    session.post.return_value = _mock_response(500)
    provider = DeepLProvider(api_key="fake-key", session=session)

    with pytest.raises(TranslationApiError):
        provider.translate("text", "KO", "EN")


def test_malformed_response_shape_raises_api_error():
    session = MagicMock()
    session.post.return_value = _mock_response(200, {"unexpected": "shape"})
    provider = DeepLProvider(api_key="fake-key", session=session)

    with pytest.raises(TranslationApiError):
        provider.translate("text", "KO", "EN")


def test_timeout_raises_typed_timeout_error():
    session = MagicMock()
    session.post.side_effect = requests.Timeout()
    provider = DeepLProvider(api_key="fake-key", session=session)

    with pytest.raises(TranslationTimeoutError):
        provider.translate("text", "KO", "EN")
