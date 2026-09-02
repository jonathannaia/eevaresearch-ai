"""translate_cached — caching, failure -> None (never raises), and
provenance fields. Uses a fake TranslationProvider, no real DeepL call."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.data_access.translation.interfaces import (
    TranslationApiError,
    TranslationConfigError,
    TranslationRateLimitError,
    TranslationTimeoutError,
)
from src.data_access.translation.translation_service import (
    MAX_TRANSLATION_RETRY_ATTEMPTS,
    TRANSLATION_RETRY_BACKOFF_MINUTES,
    record_translation_attempt,
    retry_translation_for_candidate,
    translate_cached,
    translate_cached_with_outcome,
    translation_retry_eligible,
)
from src.models.models import CandidateSignal, CandidateStatus, FilingEvent, Translation, TranslationState


class _FakeProvider:
    name = "DeepL"

    def __init__(self, result: str | None = None, error: Exception | None = None):
        self._result = result
        self._error = error
        self.call_count = 0

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        self.call_count += 1
        if self._error:
            raise self._error
        return self._result


def test_successful_translation_returns_translation_with_provenance(tmp_path):
    provider = _FakeProvider(result="New facility investment, etc.")

    result = translate_cached(provider, "20260807000537", "신규시설투자등", tmp_path)

    assert isinstance(result, Translation)
    assert result.translated_text == "New facility investment, etc."
    assert result.provider == "DeepL"
    assert result.source_lang == "ko"
    assert result.target_lang == "en"
    assert result.translated_at


def test_second_call_with_same_document_and_text_hits_cache_not_provider(tmp_path):
    provider = _FakeProvider(result="translated")

    translate_cached(provider, "doc1", "신규시설투자등", tmp_path)
    translate_cached(provider, "doc1", "신규시설투자등", tmp_path)

    assert provider.call_count == 1


def test_same_text_different_document_id_are_cached_separately(tmp_path):
    provider = _FakeProvider(result="translated")

    translate_cached(provider, "doc1", "동일한텍스트", tmp_path)
    translate_cached(provider, "doc2", "동일한텍스트", tmp_path)

    assert provider.call_count == 2


def test_different_text_same_document_id_are_cached_separately(tmp_path):
    provider = _FakeProvider(result="translated")

    translate_cached(provider, "doc1", "텍스트A", tmp_path)
    translate_cached(provider, "doc1", "텍스트B", tmp_path)

    assert provider.call_count == 2


def test_provider_failure_returns_none_not_an_exception(tmp_path):
    provider = _FakeProvider(error=TranslationConfigError("no key"))

    result = translate_cached(provider, "doc1", "text", tmp_path)

    assert result is None


def test_empty_text_returns_none_without_calling_provider(tmp_path):
    provider = _FakeProvider(result="should not be called")

    result = translate_cached(provider, "doc1", "", tmp_path)

    assert result is None
    assert provider.call_count == 0


def test_failed_translation_is_not_cached_so_a_retry_can_succeed_later(tmp_path):
    failing_provider = _FakeProvider(error=TranslationConfigError("no key"))
    translate_cached(failing_provider, "doc1", "text", tmp_path)

    working_provider = _FakeProvider(result="now it works")
    result = translate_cached(working_provider, "doc1", "text", tmp_path)

    assert result is not None
    assert result.translated_text == "now it works"


# ============================================================
# Translation reliability workstream — translate_cached_with_outcome's
# own failure categorization, and the bounded-retry state machine built
# on top of it.
# ============================================================


def test_translate_cached_with_outcome_categorizes_rate_limit_as_retryable(tmp_path):
    provider = _FakeProvider(error=TranslationRateLimitError("429", "rate limited"))
    attempt = translate_cached_with_outcome(provider, "doc1", "text", tmp_path)
    assert attempt.translation is None
    assert attempt.failure_category == "rate_limit"
    assert attempt.retryable is True


def test_translate_cached_with_outcome_categorizes_timeout_as_retryable(tmp_path):
    provider = _FakeProvider(error=TranslationTimeoutError("timed out"))
    attempt = translate_cached_with_outcome(provider, "doc1", "text", tmp_path)
    assert attempt.failure_category == "timeout"
    assert attempt.retryable is True


def test_translate_cached_with_outcome_categorizes_network_error_as_retryable(tmp_path):
    provider = _FakeProvider(error=TranslationApiError("network", "connection reset"))
    attempt = translate_cached_with_outcome(provider, "doc1", "text", tmp_path)
    assert attempt.failure_category == "network"
    assert attempt.retryable is True


def test_translate_cached_with_outcome_categorizes_provider_5xx_as_retryable(tmp_path):
    provider = _FakeProvider(error=TranslationApiError("500", "provider down"))
    attempt = translate_cached_with_outcome(provider, "doc1", "text", tmp_path)
    assert attempt.failure_category == "provider_error"
    assert attempt.retryable is True


def test_translate_cached_with_outcome_categorizes_missing_key_as_terminal(tmp_path):
    provider = _FakeProvider(error=TranslationConfigError("no key"))
    attempt = translate_cached_with_outcome(provider, "doc1", "text", tmp_path)
    assert attempt.failure_category == "config_missing_key"
    assert attempt.retryable is False


def test_translate_cached_with_outcome_categorizes_malformed_response_as_terminal(tmp_path):
    provider = _FakeProvider(error=TranslationApiError("parse", "not JSON"))
    attempt = translate_cached_with_outcome(provider, "doc1", "text", tmp_path)
    assert attempt.failure_category == "parse_error"
    assert attempt.retryable is False


def test_translate_cached_stays_a_thin_wrapper_over_translate_cached_with_outcome(tmp_path):
    provider = _FakeProvider(result="translated")
    assert translate_cached(provider, "doc1", "text", tmp_path).translated_text == "translated"


def _candidate_with_excerpt() -> CandidateSignal:
    filing = FilingEvent(
        rcept_no="R1", corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm="실적발표", rcept_dt="20260820", flr_nm="삼성전자", source_name="OpenDART / DART",
    )
    return CandidateSignal(
        id="cand-r1", filing=filing, matched_rules=["earnings:earnings_or_results_report:실적"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, excerpt_original="본문 발췌.",
    )


def test_record_translation_attempt_success_clears_failure_state():
    from src.data_access.translation.translation_service import TranslationAttempt

    candidate = _candidate_with_excerpt()
    candidate.translation_failure_category = "rate_limit"
    candidate.translation_failure_reason = "stale"
    candidate.translation_next_retry_at = "2026-01-01T00:00:00+00:00"
    successful_translation = Translation(translated_text="ok", provider="DeepL", source_lang="ko", target_lang="en", translated_at="now")

    record_translation_attempt(candidate, TranslationAttempt(translation=successful_translation))

    assert candidate.translation_state == TranslationState.TRANSLATED
    assert candidate.translation_failure_category is None
    assert candidate.translation_failure_reason is None
    assert candidate.translation_next_retry_at is None


def test_record_translation_attempt_retryable_failure_schedules_first_backoff_entry():
    from src.data_access.translation.translation_service import TranslationAttempt

    candidate = _candidate_with_excerpt()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    record_translation_attempt(candidate, TranslationAttempt(translation=None, failure_category="rate_limit", failure_reason="limited", retryable=True), now=now)

    assert candidate.translation_state == TranslationState.UNAVAILABLE
    assert candidate.translation_failure_category == "rate_limit"
    assert candidate.translation_failure_reason == "limited"
    assert candidate.translation_failure_at == now.isoformat()
    expected = (now + timedelta(minutes=TRANSLATION_RETRY_BACKOFF_MINUTES[0])).isoformat()
    assert candidate.translation_next_retry_at == expected


def test_record_translation_attempt_non_retryable_failure_schedules_nothing():
    from src.data_access.translation.translation_service import TranslationAttempt

    candidate = _candidate_with_excerpt()
    record_translation_attempt(candidate, TranslationAttempt(translation=None, failure_category="config_missing_key", failure_reason="no key", retryable=False))

    assert candidate.translation_state == TranslationState.UNAVAILABLE
    assert candidate.translation_next_retry_at is None


def test_record_translation_attempt_stops_scheduling_once_retry_cap_reached():
    from src.data_access.translation.translation_service import TranslationAttempt

    candidate = _candidate_with_excerpt()
    candidate.translation_retry_count = MAX_TRANSLATION_RETRY_ATTEMPTS
    record_translation_attempt(candidate, TranslationAttempt(translation=None, failure_category="rate_limit", failure_reason="limited", retryable=True))
    assert candidate.translation_next_retry_at is None


def test_translation_retry_eligible_true_only_once_next_retry_at_has_passed():
    candidate = _candidate_with_excerpt()
    candidate.translation_state = TranslationState.UNAVAILABLE
    candidate.translation_failure_category = "rate_limit"
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    candidate.translation_next_retry_at = (now + timedelta(minutes=5)).isoformat()

    assert translation_retry_eligible(candidate, now=now) is False
    assert translation_retry_eligible(candidate, now=now + timedelta(minutes=5)) is True


def test_translation_retry_eligible_false_for_terminal_category():
    candidate = _candidate_with_excerpt()
    candidate.translation_state = TranslationState.UNAVAILABLE
    candidate.translation_failure_category = "config_missing_key"
    candidate.translation_next_retry_at = None
    assert translation_retry_eligible(candidate) is False


def test_translation_retry_eligible_false_once_cap_reached():
    candidate = _candidate_with_excerpt()
    candidate.translation_state = TranslationState.UNAVAILABLE
    candidate.translation_failure_category = "rate_limit"
    candidate.translation_retry_count = MAX_TRANSLATION_RETRY_ATTEMPTS
    candidate.translation_next_retry_at = "2020-01-01T00:00:00+00:00"
    assert translation_retry_eligible(candidate) is False


def test_translation_retry_eligible_false_when_translated_or_not_requested():
    candidate = _candidate_with_excerpt()
    candidate.translation_state = TranslationState.TRANSLATED
    assert translation_retry_eligible(candidate) is False
    candidate.translation_state = TranslationState.NOT_REQUESTED
    assert translation_retry_eligible(candidate) is False


def test_retry_translation_for_candidate_retries_the_excerpt_and_succeeds(tmp_path):
    candidate = _candidate_with_excerpt()
    candidate.translation_state = TranslationState.UNAVAILABLE
    candidate.translation_failure_category = "rate_limit"
    candidate.translation_retry_count = 0
    provider = _FakeProvider(result="Body excerpt, translated.")

    retried = retry_translation_for_candidate(provider, candidate, tmp_path)

    assert retried.translation_state == TranslationState.TRANSLATED
    assert retried.excerpt_translation.translated_text == "Body excerpt, translated."
    assert retried.translation_retry_count == 1
    assert retried.translation_next_retry_at is None
    assert retried.translation_failure_category is None


def test_retry_translation_for_candidate_uses_japanese_source_lang_for_edinet(tmp_path):
    filing = FilingEvent(
        rcept_no="S1", corp_code="E02778", corp_name="SoftBank Group", stock_code="9984",
        report_nm="有価証券報告書", rcept_dt="20260820", flr_nm="SoftBank Group",
        source_name="EDINET", original_language="Japanese",
    )
    candidate = CandidateSignal(
        id="edinet-1", filing=filing, matched_rules=["x:y"], confidence="Moderate",
        status=CandidateStatus.NEEDS_REVIEW, excerpt_original="日本語の抜粋。",
        translation_state=TranslationState.UNAVAILABLE, translation_failure_category="rate_limit",
    )
    seen_source_langs = []

    class _RecordingProvider(_FakeProvider):
        def translate(self, text, source_lang, target_lang):
            seen_source_langs.append(source_lang)
            return super().translate(text, source_lang, target_lang)

    retry_translation_for_candidate(_RecordingProvider(result="translated"), candidate, tmp_path)
    assert seen_source_langs == ["JA"]


def test_retry_translation_for_candidate_persists_a_second_failure_with_advanced_backoff(tmp_path):
    candidate = _candidate_with_excerpt()
    candidate.translation_state = TranslationState.UNAVAILABLE
    candidate.translation_failure_category = "rate_limit"
    candidate.translation_retry_count = 0
    provider = _FakeProvider(error=TranslationRateLimitError("429", "still limited"))

    retried = retry_translation_for_candidate(provider, candidate, tmp_path)

    assert retried.translation_state == TranslationState.UNAVAILABLE
    assert retried.translation_retry_count == 1
    assert retried.translation_failure_category == "rate_limit"
    assert retried.translation_next_retry_at is not None
