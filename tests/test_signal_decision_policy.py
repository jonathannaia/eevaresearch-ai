from src.logic.signal_decision_policy import SignalRoute, decide_signal_route
from src.models.models import (
    CandidateSignal,
    CandidateStatus,
    ExtractionState,
    FilingEvent,
)


def _candidate(
    *,
    form: str,
    excerpt: str,
    rules: list[str] | None = None,
) -> CandidateSignal:
    filing = FilingEvent(
        corp_code="0000000000",
        corp_name="Example Corp",
        stock_code="EXM",
        report_nm=form,
        rcept_no="0000000000-26-000001",
        rcept_dt="2026-08-26",
        flr_nm="Example Corp",
        pblntf_ty=form,
        pblntf_detail_ty="",
        source_name="SEC EDGAR",
        source_url="https://example.test/",
        original_language="English",
        theme_slug="space",
        subtheme_slug="launch",
        primary_document="example.htm",
        retrieved_at="2026-08-26T00:00:00+00:00",
        is_demo=False,
    )
    return CandidateSignal(
        id="edgar-cand-test",
        filing=filing,
        matched_rules=rules or [],
        confidence="Moderate",
        status=CandidateStatus.NEEDS_REVIEW,
        extraction_state=ExtractionState.EXTRACTED,
        excerpt_original=excerpt,
    )


def test_schedule_13g_routes_to_timeline():
    candidate = _candidate(form="SCHEDULE 13G", excerpt="Beneficial ownership statement.")
    decision = decide_signal_route(candidate)

    assert decision.route == SignalRoute.TIMELINE
    assert decision.rule_ids == ("edgar.13g.default_timeline",)


def test_item_8_01_supporting_documents_route_to_timeline():
    candidate = _candidate(
        form="8-K",
        rules=["other_material_event:8-K item 8.01"],
        excerpt=(
            "The previously announced transaction is incorporated by reference. "
            "The filing includes pro forma financial statements and the consent "
            "of independent registered public accounting firm."
        ),
    )
    decision = decide_signal_route(candidate)

    assert decision.route == SignalRoute.TIMELINE
    assert decision.rule_ids == ("edgar.8k.8_01.supporting_documents",)


def test_periodic_report_routes_to_timeline():
    candidate = _candidate(form="10-Q", excerpt="Quarterly report for the period ended June 30.")
    decision = decide_signal_route(candidate)

    assert decision.route == SignalRoute.TIMELINE
    assert decision.rule_ids == ("edgar.periodic_report.default_timeline",)


def test_complete_424b5_offering_proposes_publish():
    candidate = _candidate(
        form="424B5",
        excerpt=(
            "We priced the public offering of 10,000,000 shares of common stock. "
            "The offering is expected to result in gross proceeds of $125 million."
        ),
    )
    decision = decide_signal_route(candidate)

    assert decision.route == SignalRoute.PUBLISH
    assert decision.rule_ids == ("edgar.424b5.complete_offering_terms",)


def test_incomplete_424b5_routes_to_timeline():
    candidate = _candidate(
        form="424B5",
        excerpt="This prospectus supplement relates to an offering of common stock.",
    )
    decision = decide_signal_route(candidate)

    assert decision.route == SignalRoute.TIMELINE
    assert decision.rule_ids == ("edgar.424b5.incomplete_offering_terms",)


def test_unknown_extracted_filing_routes_to_review():
    candidate = _candidate(form="8-K", excerpt="Item 5.02 management announcement.")
    decision = decide_signal_route(candidate)

    assert decision.route == SignalRoute.REVIEW
    assert decision.rule_ids == ("edgar.fallback.review",)
