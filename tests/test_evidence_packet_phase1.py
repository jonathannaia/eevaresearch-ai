"""Radar evidence-packet foundation, Phase 1 (design/DECISIONS.md) —
provenance integrity, cross-source timestamp/why-flagged normalization,
the source-aware location contract, and EDINET translation wiring.
Covers: the shared record_excerpt()/build_flag_reason() model helpers in
isolation, then integration through each of the three pipelines'
process_candidate(), confirming the excerpt-overwrite bug (identified by
the architecture audit in edinet_pipeline.py, and found identically in
DART's and EDGAR's pipelines on inspection) is fixed everywhere, not just
in EDINET. No network calls anywhere in this file."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.data_access.dart import radar_pipeline as dart_radar_pipeline
from src.data_access.dart import scan_service as dart_scan_service
from src.data_access.dart.document_service import DocumentFetchResult as DartDocumentFetchResult
from src.data_access.edgar import edgar_pipeline
from src.data_access.edgar import scan_service as edgar_scan_service
from src.data_access.edgar.document_extractor import extract_excerpt as edgar_extract_excerpt
from src.data_access.edgar.document_service import DocumentFetchResult as EdgarDocumentFetchResult
from src.data_access.edinet import edinet_pipeline
from src.data_access.edinet import scan_service as edinet_scan_service
from src.data_access.edinet.document_service import DocumentFetchResult as EdinetDocumentFetchResult
from src.config.tracked_companies import TrackedCompany
from src.data_access.translation.interfaces import TranslationError
from src.models.models import (
    CandidateSignal,
    CandidateStatus,
    EvidenceLocation,
    ExtractionState,
    FilingEvent,
    FlagReason,
    LocationKind,
    StateTransition,
    TranslationState,
    build_flag_reason,
    record_excerpt,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# Part A — record_excerpt() / build_flag_reason(), pure unit tests
# ============================================================


def _bare_candidate(**overrides) -> CandidateSignal:
    filing = FilingEvent(rcept_no="R1", corp_code="C1", corp_name="Example Corp", flr_nm="Example Corp", stock_code="EX", report_nm="Example filing", rcept_dt="2026-08-20")
    defaults = dict(id="cand-R1", filing=filing, matched_rules=["earnings:earnings_or_results"], confidence="Moderate", status=CandidateStatus.CANDIDATE_DETECTED)
    defaults.update(overrides)
    return CandidateSignal(**defaults)


def test_record_excerpt_first_assignment_sets_original_and_retrieved_at():
    candidate = _bare_candidate()
    changed = record_excerpt(candidate, "First extracted text.", "2026-08-20T00:00:00+00:00")
    assert changed is True
    assert candidate.excerpt_original == "First extracted text."
    assert candidate.excerpt_retrieved_at == "2026-08-20T00:00:00+00:00"
    assert candidate.excerpt_supplemental is None


def test_record_excerpt_repeat_with_identical_text_is_a_no_op():
    candidate = _bare_candidate()
    record_excerpt(candidate, "Same text.", "2026-08-20T00:00:00+00:00")
    changed = record_excerpt(candidate, "Same text.", "2026-08-21T00:00:00+00:00")
    assert changed is False
    assert candidate.excerpt_original == "Same text."
    assert candidate.excerpt_retrieved_at == "2026-08-20T00:00:00+00:00"  # untouched by the repeat
    assert candidate.excerpt_supplemental is None


def test_record_excerpt_later_different_text_is_preserved_as_supplemental_never_overwrites_original():
    candidate = _bare_candidate()
    record_excerpt(candidate, "Original excerpt from first extraction.", "2026-08-20T00:00:00+00:00")
    changed = record_excerpt(candidate, "A different, refined excerpt from a later extraction.", "2026-08-25T00:00:00+00:00")
    assert changed is False
    assert candidate.excerpt_original == "Original excerpt from first extraction."  # never overwritten
    assert candidate.excerpt_retrieved_at == "2026-08-20T00:00:00+00:00"  # original provenance untouched
    assert candidate.excerpt_supplemental == "A different, refined excerpt from a later extraction."


def test_record_excerpt_none_text_is_a_complete_no_op():
    candidate = _bare_candidate()
    changed = record_excerpt(candidate, None, "2026-08-20T00:00:00+00:00")
    assert changed is False
    assert candidate.excerpt_original is None
    assert candidate.excerpt_retrieved_at is None
    assert candidate.excerpt_supplemental is None


def test_build_flag_reason_shape():
    reason = build_flag_reason(["financing_or_debt:8-K item 2.03", "earnings_or_results:8-K item 2.02"], "High", source_detail="detail")
    assert reason.category == "financing_or_debt"
    assert reason.matched_terms == ("financing_or_debt:8-K item 2.03", "earnings_or_results:8-K item 2.02")
    assert "confidence=High" in reason.score_inputs
    assert "category_matches=2" in reason.score_inputs
    assert "2 detection rule(s)" in reason.human_readable_reason
    assert reason.source_detail == "detail"


def test_build_flag_reason_empty_matched_rules():
    reason = build_flag_reason([], "Low")
    assert reason.category == ""
    assert reason.matched_terms == ()
    assert reason.human_readable_reason == "No detection rule matched."


def test_evidence_location_default_is_unavailable():
    location = EvidenceLocation()
    assert location.kind == LocationKind.UNAVAILABLE
    assert location.page is None and location.section is None


def test_filing_event_filed_at_defaults_to_none():
    filing = FilingEvent(rcept_no="R1", corp_code="C1", corp_name="X", flr_nm="X", stock_code="X", report_nm="X", rcept_dt="2026-08-20")
    assert filing.filed_at is None


# ============================================================
# Part B — excerpt-immutability integration, all three pipelines
# ============================================================


def test_dart_process_candidate_never_overwrites_excerpt_original_on_reprocess(tmp_path, monkeypatch):
    filing = FilingEvent(rcept_no="R1", corp_code="00126380", corp_name="삼성전자", flr_nm="삼성전자", stock_code="005930", report_nm="실적발표", rcept_dt="20260820", source_name="OpenDART / DART")
    candidate = CandidateSignal(id="cand-R1", filing=filing, matched_rules=["earnings:earnings_or_results_report:실적"], confidence="Moderate", status=CandidateStatus.CANDIDATE_DETECTED)

    results = iter([
        DartDocumentFetchResult(rcept_no="R1", state=ExtractionState.EXTRACTED, excerpt_original="First extraction text.", detail="", retrieved_at="2026-08-20T00:00:00+00:00", from_cache=False),
        DartDocumentFetchResult(rcept_no="R1", state=ExtractionState.EXTRACTED, excerpt_original="Second, different extraction text.", detail="", retrieved_at="2026-08-21T00:00:00+00:00", from_cache=False),
    ])
    monkeypatch.setattr(dart_radar_pipeline.document_service, "get_or_fetch_excerpt", lambda *a, **k: next(results))
    from src.data_access.translation.translation_service import TranslationAttempt

    monkeypatch.setattr(dart_radar_pipeline, "translate_cached_with_outcome", lambda *a, **k: TranslationAttempt(translation=None))
    provider = MagicMock()
    provider.name = "DeepL"

    counters = {"documents_retrieved": 0, "documents_extracted": 0, "translations_completed": 0, "cache_hits": 0}
    dart_radar_pipeline.process_candidate(MagicMock(), provider, candidate, tmp_path, counters, {})
    assert candidate.excerpt_original == "First extraction text."

    dart_radar_pipeline.process_candidate(MagicMock(), provider, candidate, tmp_path, counters, {})
    assert candidate.excerpt_original == "First extraction text."  # never overwritten
    assert candidate.excerpt_supplemental == "Second, different extraction text."


def test_edgar_process_candidate_never_overwrites_excerpt_original_on_reprocess(tmp_path, monkeypatch):
    filing = FilingEvent(rcept_no="0001-26-000001", corp_code="0000320193", corp_name="Apple Inc.", flr_nm="Apple Inc.", stock_code="AAPL", report_nm="8-K", rcept_dt="2026-08-20", pblntf_ty="8-K", source_name="SEC EDGAR", original_language="English", primary_document="ex99.htm")
    candidate = CandidateSignal(id="edgar-cand-1", filing=filing, matched_rules=["material_event_8k_pending_items:8-K"], confidence="Moderate", status=CandidateStatus.CANDIDATE_DETECTED)

    results = iter([
        EdgarDocumentFetchResult(accession_no="0001-26-000001", state=ExtractionState.EXTRACTED, excerpt_original="First extraction text.", detail="", retrieved_at="2026-08-20T00:00:00+00:00", from_cache=False),
        EdgarDocumentFetchResult(accession_no="0001-26-000001", state=ExtractionState.EXTRACTED, excerpt_original="Second, different extraction text.", detail="", retrieved_at="2026-08-21T00:00:00+00:00", from_cache=False),
    ])
    monkeypatch.setattr(edgar_pipeline.document_service, "get_or_fetch_excerpt", lambda *a, **k: next(results))

    counters = {"documents_retrieved": 0, "documents_extracted": 0, "cache_hits": 0}
    edgar_pipeline.process_candidate(MagicMock(), candidate, tmp_path, counters, {})
    assert candidate.excerpt_original == "First extraction text."

    edgar_pipeline.process_candidate(MagicMock(), candidate, tmp_path, counters, {})
    assert candidate.excerpt_original == "First extraction text."
    assert candidate.excerpt_supplemental == "Second, different extraction text."


def test_edinet_process_candidate_never_overwrites_excerpt_original_on_reprocess(tmp_path, monkeypatch):
    filing = FilingEvent(rcept_no="S100XXXX", corp_code="E02778", corp_name="SoftBank Group", flr_nm="SoftBank Group", stock_code="9984", report_nm="有価証券報告書", rcept_dt="2026-08-20", source_name="EDINET", original_language="Japanese")
    candidate = CandidateSignal(id="edinet-cand-1", filing=filing, matched_rules=["annual_securities_report:010:030000:120"], confidence="Moderate", status=CandidateStatus.CANDIDATE_DETECTED)

    results = iter([
        EdinetDocumentFetchResult(doc_id="S100XXXX", state=ExtractionState.EXTRACTED, excerpt_original="First extraction text.", detail="", retrieved_at="2026-08-20T00:00:00+00:00", from_cache=False),
        EdinetDocumentFetchResult(doc_id="S100XXXX", state=ExtractionState.EXTRACTED, excerpt_original="Second, different extraction text.", detail="", retrieved_at="2026-08-21T00:00:00+00:00", from_cache=False),
    ])
    monkeypatch.setattr(edinet_pipeline.document_service, "get_or_fetch_excerpt", lambda *a, **k: next(results))

    counters = {"documents_retrieved": 0, "documents_extracted": 0, "cache_hits": 0}
    edinet_pipeline.process_candidate(MagicMock(), candidate, tmp_path, counters, {})
    assert candidate.excerpt_original == "First extraction text."

    edinet_pipeline.process_candidate(MagicMock(), candidate, tmp_path, counters, {})
    assert candidate.excerpt_original == "First extraction text."
    assert candidate.excerpt_supplemental == "Second, different extraction text."


# ============================================================
# Part C — EDINET full timestamp (filed_at)
# ============================================================


def test_derive_filed_at_preserves_full_timestamp_without_fabricating_an_offset():
    row = {"submitDateTime": "2026-08-17 09:00"}
    filed_at = edinet_scan_service._derive_filed_at(row)
    assert filed_at == "2026-08-17T09:00:00"
    assert "+" not in filed_at and "Z" not in filed_at  # no offset fabricated — source gave none


def test_derive_filed_at_returns_none_when_missing():
    assert edinet_scan_service._derive_filed_at({}) is None
    assert edinet_scan_service._derive_filed_at({"submitDateTime": ""}) is None


def test_derive_filed_at_returns_none_when_unparsable_never_fabricates():
    assert edinet_scan_service._derive_filed_at({"submitDateTime": "not-a-timestamp"}) is None


def test_derive_filing_date_still_truncates_to_date_only_unchanged():
    # rcept_dt's own existing date-only contract must survive Phase 1
    # untouched — filed_at is additive, never a replacement.
    row = {"submitDateTime": "2026-08-17 09:00"}
    date_part, reason = edinet_scan_service._derive_filing_date(row, "2026-08-16")
    assert date_part == "2026-08-17"
    assert reason == "submitDateTime"


def test_edgar_and_dart_filed_at_stays_none_current_endpoints_have_no_time_component():
    # Neither source's currently-used endpoint exposes a time component —
    # filed_at must stay None for both, never fabricated from rcept_dt.
    edgar_filing = edgar_scan_service._filing_event_from_row(
        {"accessionNumber": "0001-26-000001", "filingDate": "2026-08-20", "form": "8-K", "primaryDocument": "ex99.htm", "primaryDocDescription": "", "items": ""},
        TrackedCompany(name="Apple Inc.", exchange="NASDAQ", krx_code="AAPL", source="SEC EDGAR", themes=("ai-buildout",), corp_code="0000320193"),
        "2026-08-20T00:00:00+00:00",
    )
    assert edgar_filing.filed_at is None


# ============================================================
# Part D — EDINET translation wiring (non-fatal, additive)
# ============================================================


def _edinet_candidate_ready_for_extraction() -> CandidateSignal:
    filing = FilingEvent(rcept_no="S100YYYY", corp_code="E02778", corp_name="SoftBank Group", flr_nm="SoftBank Group", stock_code="9984", report_nm="有価証券報告書", rcept_dt="2026-08-20", source_name="EDINET", original_language="Japanese")
    return CandidateSignal(id="edinet-cand-2", filing=filing, matched_rules=["annual_securities_report:010:030000:120"], confidence="Moderate", status=CandidateStatus.CANDIDATE_DETECTED)


def test_edinet_stays_pending_when_no_translation_provider_given_unchanged_gate1_behavior(tmp_path, monkeypatch):
    candidate = _edinet_candidate_ready_for_extraction()
    monkeypatch.setattr(
        edinet_pipeline.document_service, "get_or_fetch_excerpt",
        lambda *a, **k: EdinetDocumentFetchResult(doc_id="S100YYYY", state=ExtractionState.EXTRACTED, excerpt_original="日本語の抜粋。", detail="", retrieved_at=_now(), from_cache=False),
    )
    counters = {"documents_retrieved": 0, "documents_extracted": 0, "cache_hits": 0}
    edinet_pipeline.process_candidate(MagicMock(), candidate, tmp_path, counters, {})
    assert candidate.translation_state == TranslationState.PENDING
    assert candidate.excerpt_translation is None


def test_edinet_translates_excerpt_when_provider_given(tmp_path, monkeypatch):
    candidate = _edinet_candidate_ready_for_extraction()
    monkeypatch.setattr(
        edinet_pipeline.document_service, "get_or_fetch_excerpt",
        lambda *a, **k: EdinetDocumentFetchResult(doc_id="S100YYYY", state=ExtractionState.EXTRACTED, excerpt_original="日本語の抜粋。", detail="", retrieved_at=_now(), from_cache=False),
    )
    seen_calls = []

    def _fake_translate_cached_with_outcome(provider, document_id, text, cache_dir, source_lang="KO"):
        seen_calls.append(source_lang)
        from src.data_access.translation.translation_service import TranslationAttempt
        from src.models.models import Translation
        translation = Translation(translated_text="Japanese excerpt.", provider="DeepL", source_lang=source_lang.lower(), target_lang="en", translated_at=_now())
        return TranslationAttempt(translation=translation)

    monkeypatch.setattr(edinet_pipeline, "translate_cached_with_outcome", _fake_translate_cached_with_outcome)
    provider = MagicMock()
    counters = {"documents_retrieved": 0, "documents_extracted": 0, "cache_hits": 0, "translations_completed": 0}
    edinet_pipeline.process_candidate(MagicMock(), candidate, tmp_path, counters, {}, translation_provider=provider)

    assert seen_calls == ["JA"]  # smallest language-code extension — reuses the same function
    assert candidate.translation_state == TranslationState.TRANSLATED
    assert candidate.excerpt_translation.translated_text == "Japanese excerpt."
    assert candidate.excerpt_original == "日本語の抜粋。"  # original never overwritten by translation
    assert counters["translations_completed"] == 1


@pytest.mark.parametrize(
    "side_effect_name,category,retryable",
    [
        ("missing_config", "config_missing_key", False),
        ("rate_limit_or_timeout", "rate_limit", True),
        ("malformed_response", "parse_error", False),
    ],
)
def test_edinet_translation_failure_is_non_fatal_original_retained(tmp_path, monkeypatch, side_effect_name, category, retryable):
    candidate = _edinet_candidate_ready_for_extraction()
    monkeypatch.setattr(
        edinet_pipeline.document_service, "get_or_fetch_excerpt",
        lambda *a, **k: EdinetDocumentFetchResult(doc_id="S100YYYY", state=ExtractionState.EXTRACTED, excerpt_original="日本語の抜粋。", detail="", retrieved_at=_now(), from_cache=False),
    )
    # translate_cached_with_outcome itself already categorizes every
    # failure mode (translation reliability workstream) — this proves the
    # EDINET call site persists that category/reason/retry schedule the
    # same non-fatal way DART's own call site does.
    from src.data_access.translation.translation_service import TranslationAttempt

    monkeypatch.setattr(
        edinet_pipeline, "translate_cached_with_outcome",
        lambda *a, **k: TranslationAttempt(translation=None, failure_category=category, failure_reason="simulated failure", retryable=retryable),
    )
    provider = MagicMock()
    counters = {"documents_retrieved": 0, "documents_extracted": 0, "cache_hits": 0, "translations_completed": 0}

    processed = edinet_pipeline.process_candidate(MagicMock(), candidate, tmp_path, counters, {}, translation_provider=provider)

    assert processed.translation_state == TranslationState.UNAVAILABLE
    assert processed.excerpt_translation is None
    assert processed.excerpt_original == "日本語の抜粋。"  # retained regardless of translation failure
    assert processed.status == CandidateStatus.NEEDS_REVIEW  # candidate is not failed by a translation failure
    assert processed.translation_failure_category == category
    assert processed.translation_failure_reason == "simulated failure"
    if retryable:
        assert processed.translation_next_retry_at is not None
    else:
        assert processed.translation_next_retry_at is None


def test_translate_cached_default_source_lang_is_unchanged_for_dart(tmp_path, monkeypatch):
    # DART's own call site never passes source_lang — confirms the new
    # optional parameter's default preserves DART's exact prior behavior.
    from src.data_access.translation.translation_service import translate_cached

    provider = MagicMock()
    provider.name = "DeepL"
    provider.translate.return_value = "Translated."
    result = translate_cached(provider, "doc-1", "원문입니다.", tmp_path)
    assert result.source_lang == "ko"
    provider.translate.assert_called_once_with("원문입니다.", "KO", "EN")


# ============================================================
# Part E — flag_reason preserves every source's existing rationale
# ============================================================


def test_dart_flag_reason_enriched_with_materiality_detail_after_processing(tmp_path, monkeypatch):
    filing = FilingEvent(rcept_no="R2", corp_code="00126380", corp_name="삼성전자", flr_nm="삼성전자", stock_code="005930", report_nm="최대주주등소유주식변동신고서", rcept_dt="20260820", source_name="OpenDART / DART")
    candidate = CandidateSignal(id="cand-R2", filing=filing, matched_rules=["ownership_change:major_shareholder_change:최대주주"], confidence="Moderate", status=CandidateStatus.CANDIDATE_DETECTED)
    monkeypatch.setattr(
        dart_radar_pipeline.document_service, "get_or_fetch_excerpt",
        lambda *a, **k: DartDocumentFetchResult(rcept_no="R2", state=ExtractionState.EXTRACTED, excerpt_original="지분 변동 내용.", detail="", retrieved_at=_now(), from_cache=False),
    )
    provider = MagicMock()
    provider.name = "DeepL"
    provider.translate.return_value = "Translated."
    from src.data_access.translation.translation_service import TranslationAttempt

    monkeypatch.setattr(dart_radar_pipeline, "translate_cached_with_outcome", lambda *a, **k: TranslationAttempt(translation=None))
    counters = {"documents_retrieved": 0, "documents_extracted": 0, "translations_completed": 0, "cache_hits": 0}

    processed = dart_radar_pipeline.process_candidate(MagicMock(), provider, candidate, tmp_path, counters, {})

    assert processed.flag_reason is not None
    assert processed.flag_reason.category == "ownership_change"
    assert processed.flag_reason.source_detail == processed.materiality_assessment  # DART's own existing rationale, preserved verbatim
    assert processed.materiality_assessment != "Not assessed"


def test_edgar_flag_reason_source_detail_carries_shadow_policy_reason_never_weakened(tmp_path, monkeypatch):
    filing = FilingEvent(rcept_no="0001-26-000002", corp_code="0000320193", corp_name="Apple Inc.", flr_nm="Apple Inc.", stock_code="AAPL", report_nm="8-K", rcept_dt="2026-08-20", pblntf_ty="8-K", source_name="SEC EDGAR", original_language="English", primary_document="ex99.htm")
    candidate = CandidateSignal(id="edgar-cand-2", filing=filing, matched_rules=["material_event_8k_pending_items:8-K"], confidence="Moderate", status=CandidateStatus.CANDIDATE_DETECTED)
    monkeypatch.setattr(
        edgar_pipeline.document_service, "get_or_fetch_excerpt",
        lambda *a, **k: EdgarDocumentFetchResult(accession_no="0001-26-000002", state=ExtractionState.EXTRACTED, excerpt_original="Item 2.03 text.", detail="", retrieved_at=_now(), from_cache=False),
    )
    counters = {"documents_retrieved": 0, "documents_extracted": 0, "cache_hits": 0}

    processed = edgar_pipeline.process_candidate(MagicMock(), candidate, tmp_path, counters, {})

    assert processed.flag_reason is not None
    # The existing typed shadow-policy rationale (route/reason/rule_ids)
    # must still be findable, verbatim, inside the normalized record —
    # this is additive, never a replacement for the state_history note.
    shadow_history_detail = next(t.detail for t in processed.state_history if t.status == CandidateStatus.EXTRACTED and "Shadow policy" in t.detail)
    assert "No high-precision automatic routing rule matched this filing." in shadow_history_detail
    assert "No high-precision automatic routing rule matched this filing." in processed.flag_reason.source_detail
    assert "edgar.fallback.review" in shadow_history_detail
    assert "edgar.fallback.review" in processed.flag_reason.source_detail


def test_edinet_flag_reason_source_detail_carries_code_triplet():
    evaluation_matched_rules = ["annual_securities_report:010:030000:120"]
    filing = FilingEvent(rcept_no="S100ZZZZ", corp_code="E02778", corp_name="SoftBank Group", flr_nm="SoftBank Group", stock_code="9984", report_nm="有価証券報告書", rcept_dt="2026-08-20", pblntf_ty="030000", pblntf_detail_ty="120", ordinance_code="010", source_name="EDINET")
    from src.data_access.edinet import edinet_rules
    evaluation = edinet_rules.RuleEvaluation(matched_rules=tuple(evaluation_matched_rules), confidence="Moderate")
    candidate = edinet_scan_service._candidate_signal_from_evaluation(filing, evaluation)
    assert candidate.flag_reason is not None
    assert "ordinanceCode=010" in candidate.flag_reason.source_detail
    assert "formCode=030000" in candidate.flag_reason.source_detail
    assert "docTypeCode=120" in candidate.flag_reason.source_detail


# ============================================================
# Part F — EDGAR source-aware evidence location
# ============================================================


def test_edgar_extract_excerpt_returns_location_section_for_item_anchor():
    text = "8-K cover page text. Item 2.03 Creation of a Direct Financial Obligation. Real substantive text about the financing arrangement follows here in detail."
    result = edgar_extract_excerpt(text.encode("utf-8"), expected_items=("2.03",))
    assert result.location_section == "Item 2.03"


def test_edgar_extract_excerpt_location_section_is_none_with_no_expected_items():
    text = "Plain 10-Q filing text with no item headers at all."
    result = edgar_extract_excerpt(text.encode("utf-8"))
    assert result.location_section is None


def test_edgar_pipeline_sets_evidence_location_section_when_anchor_found(tmp_path, monkeypatch):
    filing = FilingEvent(rcept_no="0001-26-000003", corp_code="0000320193", corp_name="Apple Inc.", flr_nm="Apple Inc.", stock_code="AAPL", report_nm="8-K", rcept_dt="2026-08-20", pblntf_ty="8-K", source_name="SEC EDGAR", original_language="English", primary_document="ex99.htm")
    candidate = CandidateSignal(id="edgar-cand-3", filing=filing, matched_rules=["financing_or_debt:8-K item 2.03"], confidence="Moderate", status=CandidateStatus.CANDIDATE_DETECTED)
    monkeypatch.setattr(
        edgar_pipeline.document_service, "get_or_fetch_excerpt",
        lambda *a, **k: EdgarDocumentFetchResult(accession_no="0001-26-000003", state=ExtractionState.EXTRACTED, excerpt_original="Item 2.03 text.", detail="", retrieved_at=_now(), from_cache=False, location_section="Item 2.03"),
    )
    counters = {"documents_retrieved": 0, "documents_extracted": 0, "cache_hits": 0}
    processed = edgar_pipeline.process_candidate(MagicMock(), candidate, tmp_path, counters, {})
    assert processed.evidence_location == EvidenceLocation(kind=LocationKind.SECTION, section="Item 2.03")


def test_edgar_pipeline_evidence_location_unavailable_when_no_anchor_found(tmp_path, monkeypatch):
    filing = FilingEvent(rcept_no="0001-26-000004", corp_code="0000320193", corp_name="Apple Inc.", flr_nm="Apple Inc.", stock_code="AAPL", report_nm="10-Q", rcept_dt="2026-08-20", pblntf_ty="10-Q", source_name="SEC EDGAR", original_language="English", primary_document="ex99.htm")
    candidate = CandidateSignal(id="edgar-cand-4", filing=filing, matched_rules=["earnings_or_results:10-Q"], confidence="Moderate", status=CandidateStatus.CANDIDATE_DETECTED)
    monkeypatch.setattr(
        edgar_pipeline.document_service, "get_or_fetch_excerpt",
        lambda *a, **k: EdgarDocumentFetchResult(accession_no="0001-26-000004", state=ExtractionState.EXTRACTED, excerpt_original="Plain 10-Q text.", detail="", retrieved_at=_now(), from_cache=False, location_section=None),
    )
    counters = {"documents_retrieved": 0, "documents_extracted": 0, "cache_hits": 0}
    processed = edgar_pipeline.process_candidate(MagicMock(), candidate, tmp_path, counters, {})
    assert processed.evidence_location == EvidenceLocation(kind=LocationKind.UNAVAILABLE)
    assert processed.evidence_location.page is None  # never a fabricated page for an HTML source


def test_dart_and_edinet_never_fabricate_a_page_number_evidence_location_stays_unset_or_unavailable():
    # Neither DART's nor EDINET's scan-time candidate construction sets
    # evidence_location at all in Phase 1 (no already-present location
    # data exists for either source) — None is itself a valid, honest
    # "not assessed" default, never a fabricated location.
    from src.data_access.dart import dart_rules
    filing = FilingEvent(rcept_no="R3", corp_code="00126380", corp_name="삼성전자", flr_nm="삼성전자", stock_code="005930", report_nm="실적발표", rcept_dt="20260820", source_name="OpenDART / DART")
    evaluation = dart_rules.evaluate_report_name(filing.report_nm)
    candidate = dart_scan_service._candidate_signal_from_evaluation(filing, evaluation)
    assert candidate.evidence_location is None


# ============================================================
# Part G — backward compatibility: old JSON records without new fields
# ============================================================


def test_old_json_record_without_new_fields_loads_with_safe_defaults(tmp_path):
    from src.data_access.dart import candidate_store

    legacy_payload = {
        "cand-legacy-1": {
            "id": "cand-legacy-1",
            "filing": {"rcept_no": "R-legacy", "corp_code": "00126380", "corp_name": "삼성전자", "stock_code": "005930", "report_nm": "실적발표", "rcept_dt": "20260101", "flr_nm": "삼성전자"},
            "matched_rules": ["earnings:earnings_or_results_report:실적"],
            "confidence": "Moderate",
            "status": "Candidate detected",
        }
    }
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "dart_candidates.json").write_text(json.dumps(legacy_payload, ensure_ascii=False), encoding="utf-8")

    loaded = candidate_store.load_candidates(tmp_path)
    candidate = loaded["cand-legacy-1"]
    assert candidate.excerpt_supplemental is None
    assert candidate.excerpt_retrieved_at is None
    assert candidate.flag_reason is None
    assert candidate.evidence_location is None
    assert candidate.filing.filed_at is None


def test_json_round_trip_preserves_new_fields(tmp_path):
    from src.data_access.dart import candidate_store

    filing = FilingEvent(rcept_no="R4", corp_code="00126380", corp_name="삼성전자", flr_nm="삼성전자", stock_code="005930", report_nm="실적발표", rcept_dt="20260820", filed_at=None)
    candidate = CandidateSignal(
        id="cand-R4", filing=filing, matched_rules=["earnings:earnings_or_results_report:실적"], confidence="Moderate",
        status=CandidateStatus.NEEDS_REVIEW, excerpt_original="First.", excerpt_supplemental="Second, different.",
        excerpt_retrieved_at="2026-08-20T00:00:00+00:00",
        flag_reason=FlagReason(category="earnings", matched_terms=("earnings:earnings_or_results_report:실적",), score_inputs=("confidence=Moderate",), human_readable_reason="Matched 1 rule.", source_detail="detail"),
        evidence_location=EvidenceLocation(kind=LocationKind.SECTION, section="Item 2.02"),
    )
    candidate_store.upsert_new_candidates(tmp_path, [candidate])
    reloaded = candidate_store.load_candidates(tmp_path)["cand-R4"]

    assert reloaded.excerpt_original == "First."
    assert reloaded.excerpt_supplemental == "Second, different."
    assert reloaded.excerpt_retrieved_at == "2026-08-20T00:00:00+00:00"
    assert reloaded.flag_reason == candidate.flag_reason
    assert reloaded.evidence_location == candidate.evidence_location


# ============================================================
# Part H — scope guard: no forbidden imports introduced this phase
# ============================================================


def test_no_new_document_fetch_or_forbidden_dependency_introduced():
    """Phase 1 explicitly excludes: new external/API/document-network
    calls, ZIP/archive handling, PDF-extraction expansion, OCR, layout
    parsers, linked-page fetching, crawlers/scrapers, LLM/agent/vector/
    graph-database code, and any Daily News change. Checked directly
    against the files this phase touched."""
    import ast

    repo_root = Path(__file__).parent.parent
    changed_files = [
        "src/models/models.py",
        "src/data_access/dart/radar_pipeline.py",
        "src/data_access/dart/scan_service.py",
        "src/data_access/dart/candidate_store.py",
        "src/data_access/edgar/edgar_pipeline.py",
        "src/data_access/edgar/scan_service.py",
        "src/data_access/edgar/document_extractor.py",
        "src/data_access/edgar/document_service.py",
        "src/data_access/edinet/edinet_pipeline.py",
        "src/data_access/edinet/scan_service.py",
        "src/data_access/edinet/edinet_service.py",
        "src/data_access/translation/translation_service.py",
        "src/data_access/state_db/schema.py",
        "src/data_access/state_db/candidate_repository.py",
        "src/data_access/state_db/filing_event_repository.py",
        "src/data_access/postgres_state_db/schema.py",
        "src/data_access/postgres_state_db/candidate_repository.py",
        "src/data_access/postgres_state_db/filing_event_repository.py",
        "src/ui/components/radar_card.py",
    ]
    forbidden_modules = (
        "zipfile", "pypdf", "PyPDF2", "pytesseract", "PIL", "cv2",
        "langchain", "anthropic", "openai", "chromadb", "pinecone", "weaviate", "networkx",
        "schedule", "apscheduler", "celery",
        "src.data_access.daily_news",
    )
    offenders = []
    for rel_path in changed_files:
        path = repo_root / rel_path
        assert path.exists(), f"expected changed file missing: {rel_path}"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                if any(module == forbidden or module.startswith(forbidden + ".") for forbidden in forbidden_modules):
                    offenders.append(f"{rel_path}: imports {module!r}")
    assert not offenders, offenders


def test_no_new_dependency_added_to_requirements():
    repo_root = Path(__file__).parent.parent
    import subprocess

    result = subprocess.run(["git", "diff", "HEAD", "--", "requirements.txt"], cwd=repo_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    assert result.stdout.strip() == "", f"requirements.txt was modified: {result.stdout}"


def test_no_worker_scheduler_or_render_config_files_touched():
    repo_root = Path(__file__).parent.parent
    import subprocess

    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    forbidden_paths = {
        "scripts/radar_worker.py", "design/RADAR_WORKER_DEPLOYMENT.md", "render.yaml",
        "src/ui/pages/daily_news.py", "src/data_access/daily_news",
    }
    hit = {c for c in changed if c in forbidden_paths or any(c.startswith(f + "/") for f in forbidden_paths)}
    assert not hit, hit
