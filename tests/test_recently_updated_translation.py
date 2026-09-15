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
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access.translation.translation_service import TranslationAttempt
from src.models.models import CandidateSignal, CandidateStatus, FilingEvent, Translation

DASHBOARD_HARNESS = Path(__file__).parent / "apptest_pages" / "dashboard_page.py"

# Beta-blocker fix (design/DECISIONS.md): recently_updated.py now reads
# real CandidateSignals, never a raw FilingEvent — these fixtures must
# seed both the filing-events scan cache (still read by other dashboard
# modules, e.g. Regional Brief) and the separate, dedicated candidate
# store file _load_filing_rows() now actually reads from.
_CANDIDATE_FILENAME_BY_FILING_FILENAME = {
    "dart_filing_events.json": "dart_candidates.json",
    "edgar_filing_events.json": "edgar_candidates.json",
}


def _settings(tmp_path) -> Settings:
    return Settings(db_backend="json", cache_dir=tmp_path, translation_api_key="deepl-key")


def _seed_filing_event(cache_dir, filing: FilingEvent, filename: str) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {"seen_receipt_numbers": [filing.rcept_no], "filing_events": [asdict(filing)], "candidate_signals": []}
    (cache_dir / filename).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    candidate = CandidateSignal(
        id=f"cand-{filing.rcept_no}", filing=filing, matched_rules=["test_rule"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW,
    )
    candidate_filename = _CANDIDATE_FILENAME_BY_FILING_FILENAME[filename]
    candidate_payload = {candidate.id: asdict(candidate)}
    (cache_dir / candidate_filename).write_text(json.dumps(candidate_payload, ensure_ascii=False), encoding="utf-8")


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
    assert "Translation unavailable" in all_text
    assert "Try again" not in all_text  # no unbuilt/untested retry affordance
    assert "provider_error" not in all_text
    assert "DeepL returned HTTP 500." not in all_text
    assert not any(b.label in ("Original", "English") for b in at.button)
    assert not any(b.label == "Translate to English" for b in at.button)  # no retry affordance


def test_english_daily_news_row_never_shows_a_translate_action(tmp_path):
    """Dashboard/Signals quality fix (design/
    DASHBOARD_SIGNAL_QUALITY_FIX_DESIGN.md) note: Daily News issuer rows
    CAN now show a translate action, for a non-English row — see the new
    French fixture below. An ordinary English row must still never show
    one, exactly as before this fix."""
    from src.data_access.daily_news import daily_news_store
    from src.models.daily_news_models import NewsSourceReference, NewsStateTransition, NewsStory, NewsStoryStatus, SourceClass

    story = NewsStory(
        id="newsitem-apple-abc123", company_name="Apple Inc.", ticker="AAPL", theme_slug="ai-buildout",
        headline="Apple announces new product", eeva_summary="Summary text.", is_fallback_summary=False,
        translation_unavailable=False, original_title=None,
        sources=(
            NewsSourceReference(
                publisher="Apple Inc.", source_class=SourceClass.OFFICIAL_COMPANY, url="https://example.invalid/apple",
                title="Apple announces new product", published_at="2026-09-01T00:00:00+00:00",
                retrieved_at="2026-09-01T00:00:00+00:00", original_language="English",
            ),
        ),
        status=NewsStoryStatus.PUBLISHED,
        state_history=[NewsStateTransition(status=NewsStoryStatus.PUBLISHED, at="2026-09-01T00:00:00+00:00")],
    )
    daily_news_store.upsert_new_stories(tmp_path, [story])
    settings = _settings(tmp_path)

    at = _run_dashboard(settings)
    assert not at.exception
    all_text = _text(at)
    assert "Apple announces new product" in all_text
    assert not any(b.label == "Translate to English" for b in at.button)


def test_french_daily_news_row_shows_translate_action_and_translates_on_click(tmp_path):
    """Dashboard/Signals quality fix (design/
    DASHBOARD_SIGNAL_QUALITY_FIX_DESIGN.md): a Daily News issuer row
    correctly classified as French (not mislabeled "English") now shows
    the same on-demand translate control filing rows already have,
    reusing the exact same shared component."""
    from datetime import datetime, timezone

    from src.data_access.daily_news import daily_news_store
    from src.models.daily_news_models import NewsSourceReference, NewsStateTransition, NewsStory, NewsStoryStatus, SourceClass

    published_at = datetime.now(timezone.utc).isoformat()
    story = NewsStory(
        id="newsitem-meta-fr", company_name="Meta Platforms, Inc.", ticker="META", theme_slug="ai-buildout",
        headline="Meta lance Meta One", eeva_summary=None, is_fallback_summary=False,
        translation_unavailable=False, original_title=None,
        sources=(
            NewsSourceReference(
                publisher="Meta Platforms, Inc.", source_class=SourceClass.OFFICIAL_COMPANY,
                url="https://about.fb.com/fr/news/meta-one", title="Meta lance Meta One",
                published_at=published_at, retrieved_at=published_at, original_language="French",
            ),
        ),
        status=NewsStoryStatus.PUBLISHED,
        state_history=[NewsStateTransition(status=NewsStoryStatus.PUBLISHED, at=published_at)],
    )
    daily_news_store.upsert_new_stories(tmp_path, [story])
    settings = _settings(tmp_path)

    with patch(
        "src.ui.components.recently_updated.translation_service.translate_cached_with_outcome"
    ) as mock_translate:
        at = _run_dashboard(settings)

    assert not at.exception
    mock_translate.assert_not_called()  # never on page load
    all_text = _text(at)
    assert "Meta lance Meta One" in all_text  # native title shown by default
    translate_action = [b for b in at.button if b.label == "Translate to English"]
    assert len(translate_action) == 1

    translation = Translation(
        translated_text="Meta Launches Meta One", provider="DeepL",
        source_lang="FR", target_lang="EN", translated_at=published_at,
    )
    with patch(
        "src.ui.components.recently_updated.translation_service.translate_cached_with_outcome",
        return_value=TranslationAttempt(translation=translation),
    ):
        translate_action[0].click()
        _rerun(at, settings)

    all_text = _text(at)
    assert "Meta Launches Meta One" in all_text
    assert any(b.label == "Original" for b in at.button)


def test_translation_control_renders_inside_the_same_per_row_container_as_its_row_content(tmp_path, monkeypatch):
    """Detached-translation-control fix (design/DECISIONS.md) regression
    guard: a row's own markdown content and its translate action must be
    rendered while the SAME per-row container key is the innermost active
    container — proving they share one stable wrapper, not two unrelated
    top-level siblings. Exercises _render_row() directly against fake
    st.container/markdown/button/session_state doubles (no real
    ScriptRunContext needed) rather than introspecting AppTest's DOM,
    which exposes no public container-membership API."""
    from contextlib import contextmanager

    from src.ui.components import recently_updated

    _seed_filing_event(tmp_path, _dart_filing(), "dart_filing_events.json")
    settings = _settings(tmp_path)
    rows = recently_updated._load_filing_rows(settings, datetime.now(timezone.utc))
    assert len(rows) == 1
    row = rows[0]

    events: list[tuple[str, str, tuple]] = []
    container_stack: list[str | None] = []

    @contextmanager
    def fake_container(*args, key=None, **kwargs):
        container_stack.append(key)
        try:
            yield None
        finally:
            container_stack.pop()

    def fake_markdown(value, *args, **kwargs):
        events.append(("markdown", value, tuple(container_stack)))

    def fake_button(label, *args, **kwargs):
        events.append(("button", label, tuple(container_stack)))
        return False

    monkeypatch.setattr(recently_updated.st, "container", fake_container)
    monkeypatch.setattr(recently_updated.st, "markdown", fake_markdown)
    monkeypatch.setattr(recently_updated.st, "button", fake_button)
    monkeypatch.setattr(recently_updated.st, "session_state", {})

    # The per-row container key is a stable, content-derived identifier
    # (recently_updated._row_identity_key), not a list position — this
    # test uses the real function so it stays correct if that derivation
    # ever changes, rather than hardcoding today's exact key string.
    row_key = f"card-recently-updated-row-{recently_updated._row_identity_key(row)}"
    with fake_container(key=row_key):
        recently_updated._render_row(row, settings)

    row_events = [e for e in events if row_key in e[2]]
    title_markdown = [e for e in row_events if e[0] == "markdown" and row.title in e[1]]
    translate_button = [e for e in row_events if e[0] == "button" and e[1] == "Translate to English"]
    assert title_markdown, "row content markdown not found inside the per-row container"
    assert translate_button, "translate action not found inside the same per-row container"
    # Both share the same outermost per-row container as their innermost
    # active container's first entry — the button's own nested
    # cta-tertiary sub-container sits inside it, never outside/after it.
    assert title_markdown[0][2][0] == row_key
    assert translate_button[0][2][0] == row_key


def test_row_identity_key_is_derived_from_content_not_list_position(tmp_path):
    """The per-row container key must not be a positional index — it
    must stay attached to the same filing/story even if `shown`'s order
    or membership shifts between reruns (e.g. a new filing discovered
    concurrently). Proven directly against the pure key-derivation
    function: deterministic per row, distinct across different rows, and
    keyed by translation_document_id (never affected by where the row
    sits in any list)."""
    from src.ui.components import recently_updated

    _seed_filing_event(tmp_path, _dart_filing(rcept_no="20260901000001"), "dart_filing_events.json")
    settings = _settings(tmp_path)
    rows = recently_updated._load_filing_rows(settings, datetime.now(timezone.utc))
    assert len(rows) == 1
    row = rows[0]

    # Every filing row already carries a real translation_document_id
    # (set unconditionally in _load_filing_rows, regardless of language)
    # — that, not position, is what identifies this row's container.
    assert row.translation_document_id is not None
    assert recently_updated._row_identity_key(row) == row.translation_document_id

    # Deterministic: calling it again for the identical row yields the
    # identical key, independent of any surrounding list.
    assert recently_updated._row_identity_key(row) == recently_updated._row_identity_key(row)

    # A second, distinct filing gets a distinct key — no collision.
    other_settings_dir = tmp_path / "other"
    _seed_filing_event(other_settings_dir, _dart_filing(rcept_no="20260902000002"), "dart_filing_events.json")
    other_row = recently_updated._load_filing_rows(_settings(other_settings_dir), datetime.now(timezone.utc))[0]
    assert recently_updated._row_identity_key(other_row) != recently_updated._row_identity_key(row)


def test_row_identity_key_falls_back_to_source_url_when_no_translation_document_id(tmp_path):
    """Daily News rows never carry translation_document_id — the
    fallback must still be stable and content-derived (source_url),
    never a list position."""
    from src.ui.components import recently_updated

    from datetime import datetime, timezone

    row = recently_updated._Row(
        sort_key=datetime(2026, 9, 1, tzinfo=timezone.utc), company_name="Apple Inc.",
        title="Apple announces new product", source_label="Signals",
        display_date="Sep 1, 2026", source_url="https://example.invalid/apple",
    )
    assert row.translation_document_id is None
    assert recently_updated._row_identity_key(row) == "https://example.invalid/apple"


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
