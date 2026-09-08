from datetime import datetime, timedelta, timezone

from src.models.models import (
    CandidateSignal,
    CandidateStatus,
    CapitalRotationMetric,
    Catalyst,
    ChatAnswer,
    ClaimType,
    Direction,
    EvidenceItem,
    ExcerptQuality,
    Exposure,
    ExtractionState,
    FilingEvent,
    Horizon,
    ResearchClaim,
    Signal,
    StateTransition,
    Strength,
    Subtheme,
    Theme,
    Ticker,
    TranslationState,
    Translation,
    WatchlistEntry,
)


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def test_theme_holds_subthemes():
    sub = Subtheme(slug="optical-transceivers", name="Optical Transceivers", description="d", theme_slug="photonics")
    theme = Theme(slug="photonics", name="Photonics", description="d", subthemes=[sub])
    assert theme.subthemes[0].theme_slug == theme.slug


def test_evidence_item_defaults_are_demo_safe():
    ev = EvidenceItem(
        id="ev-1", title="t", source_name="EevaResearch Demo Data", source_type="Demo Dataset",
        published_at=_iso(1), retrieved_at=_iso(1), excerpt="e", claim_type=ClaimType.FACT,
    )
    assert ev.source_url is None
    assert ev.is_demo is True
    assert ev.source_name == "EevaResearch Demo Data"


def test_evidence_item_freshness_buckets():
    fresh = EvidenceItem(id="1", title="t", source_name="s", source_type="s", published_at=_iso(1), retrieved_at=_iso(1), excerpt="e", claim_type=ClaimType.FACT)
    aging = EvidenceItem(id="2", title="t", source_name="s", source_type="s", published_at=_iso(5), retrieved_at=_iso(5), excerpt="e", claim_type=ClaimType.FACT)
    stale = EvidenceItem(id="3", title="t", source_name="s", source_type="s", published_at=_iso(30), retrieved_at=_iso(30), excerpt="e", claim_type=ClaimType.FACT)
    assert fresh.freshness_label == "Fresh"
    assert aging.freshness_label == "Aging"
    assert stale.freshness_label == "Stale"


def test_evidence_item_freshness_handles_unparseable_timestamp():
    bad = EvidenceItem(id="1", title="t", source_name="s", source_type="s", published_at="not-a-date", retrieved_at="not-a-date", excerpt="e", claim_type=ClaimType.FACT)
    assert bad.freshness_label == "Unknown"


def test_research_claim_carries_evidence_list():
    ev = EvidenceItem(id="1", title="t", source_name="s", source_type="s", published_at=_iso(1), retrieved_at=_iso(1), excerpt="e", claim_type=ClaimType.FACT)
    claim = ResearchClaim(text="claim", claim_type=ClaimType.INTERPRETATION, evidence=[ev])
    assert claim.evidence[0].id == "1"


def test_ticker_defaults_to_demo():
    t = Ticker(
        symbol="DEMO", company_name="Nova Aperture Systems (Demo Company — Not Real)",
        theme_slug="photonics", subtheme_slug="optical-transceivers", exposure=Exposure.PRIMARY,
        market_cap_bucket="Mid", liquidity_bucket="Medium", technical_strength="Neutral",
        risk_level="Medium", thesis="placeholder thesis",
    )
    assert t.is_demo is True


def test_catalyst_theme_and_ticker_optional_linkage():
    c = Catalyst(id="c1", title="t", date="2026-09-01", catalyst_type="Industry Event", description="d", theme_slug="space")
    assert c.ticker_symbol is None


def test_signal_enums_hold_expected_values():
    s = Signal(
        id="s1", title="t", theme_slug="memory", subtheme_slug=None, direction=Direction.EMERGING,
        strength=Strength.MODERATE, horizon=Horizon.MULTI_WEEK, evidence_count=2,
        interpretation="i", contrary_evidence="c", validation_criteria="v", invalidation_criteria="iv",
        related_tickers=["DEMO"], last_updated=_iso(1),
    )
    assert s.direction == Direction.EMERGING
    assert s.strength.value == "Moderate"


def test_capital_rotation_metric_is_demo_by_default():
    m = CapitalRotationMetric(theme_slug="ai-buildout", relative_performance_pct=1.2, breadth_pct=55.0, leaders=["DEMO"], laggards=[], as_of=_iso(0))
    assert m.is_demo is True


def test_chat_answer_defaults_claim_type_interpretation():
    a = ChatAnswer(
        question="q", what_happened="w", why_it_matters="w2", underappreciated="u", risks="r",
        what_to_watch="wtw", sources=[], confidence=Strength.MODERATE, freshness="Fresh",
    )
    assert a.claim_type == ClaimType.INTERPRETATION


def test_watchlist_entry_defaults_note_empty():
    e = WatchlistEntry(list_name="Core Themes", ticker_symbol="DEMO", added_at=_iso(0))
    assert e.note == ""


def test_filing_event_defaults_to_korean_and_not_demo():
    f = FilingEvent(
        rcept_no="20260115000123", corp_code="00126380", corp_name="삼성전자",
        stock_code="005930", report_nm="분기보고서", rcept_dt=_iso(0), flr_nm="삼성전자", pblntf_ty="A",
    )
    assert f.original_language == "Korean"
    assert f.is_demo is False
    assert f.source_name == "OpenDART / DART"


def test_candidate_signal_excerpt_defaults_to_none_not_empty_string():
    filing = FilingEvent(
        rcept_no="20260115000123", corp_code="00126380", corp_name="삼성전자",
        stock_code="005930", report_nm="분기보고서", rcept_dt=_iso(0), flr_nm="삼성전자", pblntf_ty="A",
    )
    candidate = CandidateSignal(
        id="c1", filing=filing, matched_rules=["capex_keyword"], confidence="Moderate",
        status=CandidateStatus.CANDIDATE_DETECTED,
    )
    # None (not "") is the signal to the UI that parsing hasn't succeeded
    # yet — distinct from a filing that genuinely has no excerpt text.
    assert candidate.excerpt_original is None
    assert candidate.title_translation is None
    assert candidate.excerpt_translation is None
    assert candidate.extraction_state == ExtractionState.NOT_FETCHED


def test_extraction_state_matches_the_six_approved_states():
    assert {s.value for s in ExtractionState} == {
        "Not fetched", "Document available; extraction pending", "Extracted",
        "Unsupported format", "Parse failed", "Retrieval failed",
    }


def test_candidate_status_matches_the_seventeen_approved_lifecycle_states():
    assert {s.value for s in CandidateStatus} == {
        "New filing event", "Candidate detected", "Queued for document processing",
        "Document retrieval in progress", "Extraction pending", "Extracted",
        "Translation pending", "Translated", "Needs review", "Processing deferred",
        "Parse failed", "Retrieval failed", "Translation unavailable",
        "Published", "Dismissed", "Not material", "Monitoring",
    }


def test_monitoring_status_round_trips_through_the_real_candidate_store(tmp_path):
    """Human-review gate (Stage 1): MONITORING must survive a real
    persisted write/read cycle through the exact same candidate_store.py
    code path production uses — zero candidate_store.py change was made
    or is needed for this to work, since CandidateStatus(str, Enum)
    serializes/deserializes generically for any valid member."""
    from src.data_access.dart import candidate_store

    filing = FilingEvent(
        rcept_no="20260115000999", corp_code="00126380", corp_name="삼성전자",
        stock_code="005930", report_nm="분기보고서", rcept_dt=_iso(0), flr_nm="삼성전자", pblntf_ty="A",
    )
    candidate = CandidateSignal(
        id="c-monitoring-1", filing=filing, matched_rules=["capex_keyword"], confidence="Moderate",
        status=CandidateStatus.MONITORING,
        state_history=[StateTransition(status=CandidateStatus.MONITORING, at=_iso(0), detail="Reviewed — watching for confirmation.")],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})

    reloaded = candidate_store.load_candidates(tmp_path)[candidate.id]
    assert reloaded.status == CandidateStatus.MONITORING
    assert reloaded.state_history[-1].status == CandidateStatus.MONITORING
    assert reloaded.state_history[-1].detail == "Reviewed — watching for confirmation."


def test_monitoring_status_renders_with_a_valid_bucket_and_label():
    """MONITORING must not fall through radar_status.py's status-bucket
    mapping with no entry (which would render an unstyled/blank badge) —
    same completeness discipline as every other real status."""
    from src.ui.components.radar_status import _STATUS_BUCKET, RadarItem, status_label

    filing = FilingEvent(
        rcept_no="20260115000999", corp_code="00126380", corp_name="삼성전자",
        stock_code="005930", report_nm="분기보고서", rcept_dt=_iso(0), flr_nm="삼성전자", pblntf_ty="A",
    )
    candidate = CandidateSignal(
        id="c-monitoring-2", filing=filing, matched_rules=["capex_keyword"], confidence="Moderate",
        status=CandidateStatus.MONITORING,
    )
    assert _STATUS_BUCKET.get(CandidateStatus.MONITORING) is not None
    assert status_label(RadarItem(filing=filing, candidate=candidate)) == "Monitoring"


def test_translation_state_matches_the_four_approved_states():
    assert {s.value for s in TranslationState} == {
        "Not requested", "Translation pending", "Translated", "Translation unavailable",
    }


def test_excerpt_quality_matches_the_five_approved_states():
    assert {s.value for s in ExcerptQuality} == {
        "Very short or empty", "Likely boilerplate", "Table-heavy", "Usable text", "Unknown",
    }


def test_candidate_signal_defaults_translation_state_and_excerpt_quality():
    filing = FilingEvent(
        rcept_no="20260115000456", corp_code="00126380", corp_name="삼성전자",
        stock_code="005930", report_nm="분기보고서", rcept_dt=_iso(0), flr_nm="삼성전자",
    )
    candidate = CandidateSignal(
        id="c2", filing=filing, matched_rules=[], confidence="Moderate",
        status=CandidateStatus.CANDIDATE_DETECTED,
    )
    assert candidate.translation_state == TranslationState.NOT_REQUESTED
    assert candidate.excerpt_quality == ExcerptQuality.UNKNOWN
    assert candidate.state_history == []
    assert candidate.materiality_assessment == "Not assessed"


def test_state_transition_records_status_timestamp_and_detail():
    transition = StateTransition(status=CandidateStatus.RETRIEVAL_FAILED, at=_iso(0), detail="DART timed out.")
    assert transition.status == CandidateStatus.RETRIEVAL_FAILED
    assert transition.detail == "DART timed out."


def test_translation_is_never_labeled_as_the_source():
    t = Translation(
        translated_text="Sample English translation.", provider="DeepL",
        source_lang="ko", target_lang="en", translated_at=_iso(0),
    )
    assert t.source_lang == "ko"
    assert t.target_lang == "en"
    assert t.provider == "DeepL"
