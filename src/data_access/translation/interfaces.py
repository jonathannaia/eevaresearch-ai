"""TranslationProvider abstraction. Every error subclass is meant to be
caught by translation_service.py above it — a translation failure must
never raise into the UI; the caller falls back to the Korean original
and a "Translation unavailable" state instead."""
from __future__ import annotations

from abc import ABC, abstractmethod


class TranslationError(Exception):
    """Base class for every error a TranslationProvider can raise."""


class TranslationConfigError(TranslationError):
    """No API key configured — a setup problem, not a service problem."""


class TranslationApiError(TranslationError):
    def __init__(self, status: str, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(f"Translation provider error {status}: {message}")


class TranslationRateLimitError(TranslationApiError):
    pass


class TranslationTimeoutError(TranslationError):
    """The HTTP request itself timed out (network layer, not a provider
    status code)."""


class TranslationProvider(ABC):
    name: str

    @abstractmethod
    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Returns the translated text, or raises a TranslationError
        subclass — never returns a fabricated or partial translation."""
