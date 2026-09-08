"""DeepL REST API translation provider. Verified against DeepL's own API
reference during development, not guessed: POST to /v2/translate,
`Authorization: DeepL-Auth-Key <key>` header, and free-tier keys (which
carry a documented ":fx" suffix) must use the api-free.deepl.com host
rather than the pro api.deepl.com host — this class detects that from
the key itself rather than requiring a separate "which tier" setting.
"""
from __future__ import annotations

import requests

from src.data_access.translation.interfaces import (
    TranslationApiError,
    TranslationConfigError,
    TranslationProvider,
    TranslationRateLimitError,
    TranslationTimeoutError,
)

_PRO_URL = "https://api.deepl.com/v2/translate"
_FREE_URL = "https://api-free.deepl.com/v2/translate"
_FREE_KEY_SUFFIX = ":fx"
_DEFAULT_TIMEOUT_SECONDS = 15


class DeepLProvider(TranslationProvider):
    name = "DeepL"

    def __init__(
        self, api_key: str | None, session: requests.Session | None = None, timeout: int = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._session = session or requests.Session()
        self._timeout = timeout

    def _base_url(self) -> str:
        if self._api_key and self._api_key.endswith(_FREE_KEY_SUFFIX):
            return _FREE_URL
        return _PRO_URL

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        if not self._api_key:
            raise TranslationConfigError("EDGE_TRANSLATION_API_KEY is not configured.")
        headers = {"Authorization": f"DeepL-Auth-Key {self._api_key}"}
        body = {"text": [text], "source_lang": source_lang.upper(), "target_lang": target_lang.upper()}
        try:
            response = self._session.post(self._base_url(), headers=headers, json=body, timeout=self._timeout)
        except requests.Timeout as exc:
            raise TranslationTimeoutError(f"DeepL request timed out after {self._timeout}s.") from exc
        except requests.RequestException as exc:
            raise TranslationApiError("network", str(exc)) from exc

        if response.status_code == 429:
            raise TranslationRateLimitError(str(response.status_code), "DeepL rate limit exceeded.")
        if response.status_code != 200:
            raise TranslationApiError(str(response.status_code), f"DeepL returned HTTP {response.status_code}.")
        try:
            payload = response.json()
            return payload["translations"][0]["text"]
        except (ValueError, KeyError, IndexError) as exc:
            raise TranslationApiError("parse", "DeepL response was not in the expected shape.") from exc
