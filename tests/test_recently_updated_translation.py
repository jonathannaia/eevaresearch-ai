"""Dashboard Recently Updated — on-demand title translation (Dashboard/
Filings usability pass, design/DECISIONS.md). Fixture-driven proofs,
via the same real Dashboard AppTest harness test_recent_theme_activity.py
already uses, that:

  - the translation provider is never called during a plain page render;
  - a `Translate to English` action only appears for a non-English row;
  - clicking it calls translation_service.translate_cached_with_outcome
    exactly once and then shows an `Original`/`English` toggle;
  - a failed/unavailable attempt shows one concise status line and
    leaves the original title as the only thing shown.

translation_service.translate_cached_with_outcome is monkeypatched
directly (no real DeepL call, no network, no API key) — this file only
proves recently_updated.py's own call discipline and rendering, not
translation_service.py's own internal behavior (covered elsewhere)."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access.translation.translation_service import TranslationAttempt
from src.models.models import FilingEvent, Translation

DASHBOARD_HARNESS = Path(__file__).parent / "apptest_pages" / "dashboard_page.py"


def _settings(tmp_path) -> Settings:
    return Settings(db_backend="json", cache_dir=tmp_path, translation_api_key="deepl-key")


def _seed_filing_event(cache_dir, filing: FilingEvent, filename: str) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {"seen_receipt_numbers": [filing.rcept_no], "filing_events": [asdict(filing)], "candidate_signals": []}
    (cache_dir / filename).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _dart_filing(rcept_no: str = "20260901000001") -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm="신규시설투자등 결정", rcept_dt="20260901", flr_nm="삼성전자",
        source_name="OpenDART / DART", source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + rcept_no,
        retrieved_at="2026-09-01T00:00:00+00:00",
    )


def _edgar_filing(rcept_no: str = "0000320193-26-000100") -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="0000320193", corp_name="Apple Inc.", stock_code="AAPL",
        report_nm="Quarterly results announcement", rcept_dt="2026-09-01", flr_nm="Apple Inc.",
        source_name="SEC EDGAR", source_url="https://example.invalid/" + rcept_no,
        retrieved_at="2026-09-01T00:00:00+00:00", original_language="English",
    )


def _run_dashboard(settings: Settings) -> AppTest:
    with patch("src.ui.pages.dashboard.get_settings", return_value=settings):
        at = AppTest.from_file(str(DASHBOARD_HARNESS), default_timeout=15)
        at.run()
    return at


def _rerun(at: AppTest, settings: Settings) -> None:
    with patch("src.ui.pages.dashboard.get_settings", return_value=settings):
        at.run()


def _text(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown if not m.value.startswith("<style>"))


def test_no_translation_call_on_initial_page_load(tmp_path):
    _seed_filing_event(tmp_path, _dart_filing(), "dart_filing_events.json")
    settings = _settings(tmp_path)

    with patch(
        "src.ui.components.recently_updated.translation_service.translate_cached_with_outcome"
    ) as mock_translate:
        at = _run_dashboard(settings)

    assert not at.exception
    mock_translate.assert_not_called()
    all_text = _text(at)
    assert "신규시설투자등 결정" in all_text  # original title shown by default
    translate_action = [b for b in at.button if b.label == "Translate to English"]
    assert len(translate_action) == 1


def test_english_row_never_shows_a_translate_action(tmp_path):
    _seed_filing_event(tmp_path, _edgar_filing(), "edgar_filing_events.json")
    settings = _settings(tmp_path)

    at = _run_dashboard(settings)
    assert not at.exception
    all_text = _text(at)
    assert "Quarterly results announcement" in all_text
    assert not any(b.label == "Translate to English" for b in at.button)


def test_translate_action_calls_provider_exactly_once_and_shows_toggle_on_success(tmp_path):
    _seed_filing_event(tmp_path, _dart_filing(), "dart_filing_events.json")
    settings = _settings(tmp_path)

    translation = Translation(
        translated_text="New facility investment decision", provider="DeepL",
        source_lang="KO", target_lang="EN", translated_at="2026-09-01T00:00:00+00:00",
    )
    with patch(
        "src.ui.components.recently_updated.translation_service.translate_cached_with_outcome",
        return_value=TranslationAttempt(translation=translation),
    ) as mock_translate:
        at = _run_dashboard(settings)
        translate_action = [b for b in at.button if b.label == "Translate to English"]
        assert len(translate_action) == 1
        translate_action[0].click()
        _rerun(at, settings)

    mock_translate.assert_called_once()
    call_kwargs = mock_translate.call_args.kwargs
    assert call_kwargs["text"] == "신규시설투자등 결정"
    assert call_kwargs["source_lang"] == "KO"

    all_text = _text(at)
    assert "New facility investment decision" in all_text
    assert any(b.label == "Original" for b in at.button)  # toggle now offers switching back
    assert not any(b.label == "Translate to English" for b in at.button)

    # A second rerun (no further click) must not call the provider again —
    # the successful result is cached in session state, not re-fetched.
    _rerun(at, settings)
    mock_translate.assert_called_once()

    original_toggle = [b for b in at.button if b.label == "Original"]
    original_toggle[0].click()
    _rerun(at, settings)
    all_text = _text(at)
    assert "신규시설투자등 결정" in all_text
    assert "New facility investment decision" not in all_text
    assert any(b.label == "English" for b in at.button)
    mock_translate.assert_called_once()  # still exactly one real call, toggling is purely local


def test_translate_action_failure_shows_concise_status_and_keeps_original_title(tmp_path):
    _seed_filing_event(tmp_path, _dart_filing(), "dart_filing_events.json")
    settings = _settings(tmp_path)

    with patch(
        "src.ui.components.recently_updated.translation_service.translate_cached_with_outcome",
        return_value=TranslationAttempt(translation=None, failure_category="provider_error", failure_reason="DeepL returned HTTP 500.", retryable=True),
    ) as mock_translate:
        at = _run_dashboard(settings)
        translate_action = [b for b in at.button if b.label == "Translate to English"]
        translate_action[0].click()
        _rerun(at, settings)

    mock_translate.assert_called_once()
    all_text = _text(at)
    assert "신규시설투자등 결정" in all_text  # original title stays the only thing shown
    assert "Translation unavailable." in all_text
    assert "provider_error" not in all_text
    assert "DeepL returned HTTP 500." not in all_text
    assert not any(b.label in ("Original", "English") for b in at.button)
    assert not any(b.label == "Translate to English" for b in at.button)  # no retry affordance


def test_preserves_source_issuer_date_and_link_unchanged_after_translation(tmp_path):
    _seed_filing_event(tmp_path, _dart_filing(), "dart_filing_events.json")
    settings = _settings(tmp_path)

    translation = Translation(
        translated_text="New facility investment decision", provider="DeepL",
        source_lang="KO", target_lang="EN", translated_at="2026-09-01T00:00:00+00:00",
    )
    with patch(
        "src.ui.components.recently_updated.translation_service.translate_cached_with_outcome",
        return_value=TranslationAttempt(translation=translation),
    ):
        at = _run_dashboard(settings)
        translate_action = [b for b in at.button if b.label == "Translate to English"]
        translate_action[0].click()
        _rerun(at, settings)

    all_text = _text(at)
    assert "삼성전자" in all_text  # company/issuer unchanged
    assert "Korea DART" in all_text  # source label unchanged
    assert "dart.fss.or.kr" in all_text  # source link unchanged
