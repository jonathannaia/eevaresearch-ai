"""edgar_pipeline — the bounded, idempotent orchestration entry point
connecting scan_service, document_service, and edgar_rules's post-
extraction 8-K refinement. Fully mocked EdgarClient, zero network, no
User-Agent required."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.config.tracked_companies import TrackedCompany
from src.data_access.dart import candidate_store, retry_policy
from src.data_access.edgar import edgar_pipeline
from src.data_access.edgar.errors import EdgarApiError
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, StateTransition, TranslationState

_NVDA = TrackedCompany(
    name="NVIDIA", exchange="NASDAQ", krx_code="NVDA", source="SEC EDGAR",
    themes=("ai-buildout",), corp_code="0001045810",
)
_MU = TrackedCompany(
    name="Micron Technology", exchange="NASDAQ", krx_code="MU", source="SEC EDGAR",
    themes=("memory",), corp_code="0000723125",
)


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _recent(accession_numbers, forms=None, primary_docs=None, items=None):
    n = len(accession_numbers)
    recent = {
        "accessionNumber": accession_numbers,
        "filingDate": [_today()] * n,
        "form": forms or ["8-K"] * n,
        "primaryDocument": primary_docs or ["doc.htm"] * n,
        "primaryDocDescription": ["desc"] * n,
    }
    if items is not None:
        recent["items"] = items
    return recent


def _make_client(submissions_by_cik: dict, document_by_accession: dict | None = None):
    document_by_accession = document_by_accession or {}
    client = MagicMock()

    def _get_submissions(cik):
        return submissions_by_cik.get(cik, {"filings": {"recent": _recent([])}})

    def _fetch_document(cik, accession_no, filename):
        result = document_by_accession.get(accession_no)
        if isinstance(result, Exception):
            raise result
        return result if result is not None else b"<html><body><p>generic filing content</p></body></html>"

    client.get_submissions.side_effect = _get_submissions
    client.fetch_document.side_effect = _fetch_document
    return client


def test_8k_candidate_is_refined_after_extraction(tmp_path):
    client = _make_client(
        {"0001045810": {"filings": {"recent": _recent(["0001045810-26-000001"])}}},
        {"0001045810-26-000001": b"<html><body><p>Item 2.02 Results of Operations. Revenue rose.</p></body></html>"},
    )

    report = edgar_pipeline.run_pipeline(client, [_NVDA], tmp_path)

    assert report.candidates_processed == 1
    candidate = next(iter(candidate_store.load_candidates(tmp_path, edgar_pipeline.CANDIDATE_STORE_FILENAME).values()))
    assert candidate.status == CandidateStatus.NEEDS_REVIEW
    assert candidate.matched_rules == ["earnings_or_results:8-K item 2.02"]
    assert candidate.translation_state == TranslationState.NOT_REQUESTED


def test_8k_with_no_recognizable_items_keeps_coarse_classification(tmp_path):
    client = _make_client(
        {"0001045810": {"filings": {"recent": _recent(["0001045810-26-000001"])}}},
        {"0001045810-26-000001": b"<html><body><p>No item headers in this garbled excerpt at all.</p></body></html>"},
    )

    edgar_pipeline.run_pipeline(client, [_NVDA], tmp_path)

    candidate = next(iter(candidate_store.load_candidates(tmp_path, edgar_pipeline.CANDIDATE_STORE_FILENAME).values()))
    assert candidate.matched_rules == ["material_event_8k_pending_items:8-K"]
    assert candidate.status == CandidateStatus.NEEDS_REVIEW


def test_10q_candidate_never_goes_through_8k_refinement(tmp_path):
    client = _make_client(
        {"0001045810": {"filings": {"recent": _recent(["0001045810-26-000002"], forms=["10-Q"])}}},
        {"0001045810-26-000002": b"<html><body><p>Quarterly report content.</p></body></html>"},
    )

    edgar_pipeline.run_pipeline(client, [_NVDA], tmp_path)

    candidate = next(iter(candidate_store.load_candidates(tmp_path, edgar_pipeline.CANDIDATE_STORE_FILENAME).values()))
    assert candidate.matched_rules == ["earnings_or_results:10-Q"]


def test_translation_state_always_not_requested_never_translated(tmp_path):
    client = _make_client(
        {"0001045810": {"filings": {"recent": _recent(["0001045810-26-000001"])}}},
        {"0001045810-26-000001": b"<html><body><p>Item 1.01 Material Agreement.</p></body></html>"},
    )

    edgar_pipeline.run_pipeline(client, [_NVDA], tmp_path)

    candidate = next(iter(candidate_store.load_candidates(tmp_path, edgar_pipeline.CANDIDATE_STORE_FILENAME).values()))
    assert candidate.translation_state == TranslationState.NOT_REQUESTED
    assert candidate.title_translation is None
    assert candidate.excerpt_translation is None


def test_ownership_form_13d_reaches_needs_review_with_no_materiality_gate(tmp_path):
    client = _make_client(
        {"0001045810": {"filings": {"recent": _recent(["0001045810-26-000003"], forms=["SC 13D"])}}},
        {"0001045810-26-000003": b"<html><body><p>Schedule 13D ownership disclosure.</p></body></html>"},
    )

    edgar_pipeline.run_pipeline(client, [_NVDA], tmp_path)

    candidate = next(iter(candidate_store.load_candidates(tmp_path, edgar_pipeline.CANDIDATE_STORE_FILENAME).values()))
    # No ownership-materiality gate exists for EDGAR — always NEEDS_REVIEW
    # on successful extraction, never NOT_MATERIAL (that status/gate is
    # DART-specific — see ownership_materiality.py).
    assert candidate.status == CandidateStatus.NEEDS_REVIEW
    assert candidate.materiality_assessment == "Not assessed"


def test_retrieval_failure_reaches_retrieval_failed_status(tmp_path):
    from src.data_access.edgar.errors import EdgarApiError
    client = _make_client(
        {"0001045810": {"filings": {"recent": _recent(["0001045810-26-000004"])}}},
        {"0001045810-26-000004": EdgarApiError(404, "not found")},
    )

    edgar_pipeline.run_pipeline(client, [_NVDA], tmp_path)

    candidate = next(iter(candidate_store.load_candidates(tmp_path, edgar_pipeline.CANDIDATE_STORE_FILENAME).values()))
    assert candidate.status == CandidateStatus.RETRIEVAL_FAILED


def test_partial_failure_on_one_company_does_not_block_the_other(tmp_path):
    from src.data_access.edgar.errors import EdgarApiError
    failing_client = MagicMock()

    def _get_submissions(cik):
        if cik == "0001045810":
            raise EdgarApiError(500, "server error")
        return {"filings": {"recent": _recent(["0000723125-26-000001"])}}

    failing_client.get_submissions.side_effect = _get_submissions
    failing_client.fetch_document.return_value = b"<html><body><p>Item 2.02 Results.</p></body></html>"

    report = edgar_pipeline.run_pipeline(failing_client, [_NVDA, _MU], tmp_path)

    assert len(report.warnings) == 1
    candidates = candidate_store.load_candidates(tmp_path, edgar_pipeline.CANDIDATE_STORE_FILENAME)
    assert len(candidates) == 1
    assert next(iter(candidates.values())).filing.corp_name == "Micron Technology"


def test_budget_defers_excess_eligible_candidates(tmp_path):
    client = _make_client({
        "0001045810": {"filings": {"recent": _recent([f"0001045810-26-00000{i}" for i in range(3)])}},
    })

    report = edgar_pipeline.run_pipeline(client, [_NVDA], tmp_path, max_candidates_to_process=1)

    assert report.candidates_processed == 1
    assert report.candidates_deferred == 2


def test_process_single_candidate_processes_named_candidate(tmp_path):
    client = _make_client(
        {"0001045810": {"filings": {"recent": _recent(["0001045810-26-000001"])}}},
        {"0001045810-26-000001": b"<html><body><p>Item 2.02 Results.</p></body></html>"},
    )
    edgar_pipeline.run_pipeline(client, [_NVDA], tmp_path, max_candidates_to_process=0)
    candidate_id = next(iter(candidate_store.load_candidates(tmp_path, edgar_pipeline.CANDIDATE_STORE_FILENAME)))

    result = edgar_pipeline.process_single_candidate(client, candidate_id, tmp_path)

    assert result is not None
    assert result.status == CandidateStatus.NEEDS_REVIEW


def test_process_single_candidate_returns_none_for_unknown_id(tmp_path):
    client = _make_client({})
    assert edgar_pipeline.process_single_candidate(client, "edgar-cand-does-not-exist", tmp_path) is None


def test_clamp_max_candidates():
    assert edgar_pipeline.clamp_max_candidates(9999) == edgar_pipeline.MAX_CANDIDATES_PER_SCAN_CEILING
    assert edgar_pipeline.clamp_max_candidates(0) == 1


def test_pipeline_is_idempotent_across_repeated_runs(tmp_path):
    client = _make_client(
        {"0001045810": {"filings": {"recent": _recent(["0001045810-26-000001"])}}},
        {"0001045810-26-000001": b"<html><body><p>Item 2.02 Results.</p></body></html>"},
    )

    first = edgar_pipeline.run_pipeline(client, [_NVDA], tmp_path)
    second = edgar_pipeline.run_pipeline(client, [_NVDA], tmp_path)

    assert first.new_filing_events == 1
    assert second.new_filing_events == 0
    assert second.already_seen_count == 1
    assert len(candidate_store.load_candidates(tmp_path, edgar_pipeline.CANDIDATE_STORE_FILENAME)) == 1


def test_post_extraction_refinement_is_idempotent_when_scan_time_already_refined(tmp_path):
    # Scan-time already classifies this 8-K fully from the real `items`
    # metadata column (milestone 8 Gate 4 fix). Post-extraction
    # refinement must be a no-op consistency check here, not a second,
    # different classification — the document text repeats the same
    # items the metadata already gave us.
    client = _make_client(
        {"0001045810": {"filings": {"recent": _recent(["0001045810-26-000001"], items=["1.01,2.03,7.01"])}}},
        {"0001045810-26-000001": b"<html><body><p>Item 1.01 Entry into Agreement. Item 2.03 Financial Obligation. Item 7.01 Reg FD.</p></body></html>"},
    )

    report = edgar_pipeline.run_pipeline(client, [_NVDA], tmp_path)

    assert report.candidates_processed == 1
    candidate = next(iter(candidate_store.load_candidates(tmp_path, edgar_pipeline.CANDIDATE_STORE_FILENAME).values()))
    assert candidate.confidence == "High"
    assert candidate.matched_rules == [
        "material_agreement:8-K item 1.01",
        "financing_or_debt:8-K item 2.03",
        "regulation_fd_disclosure:8-K item 7.01",
    ]
    assert candidate.status == CandidateStatus.NEEDS_REVIEW


def test_post_extraction_refinement_still_helps_when_scan_time_metadata_was_missing(tmp_path):
    # Scan-time items metadata absent -> coarse fallback; document text
    # still lets post-extraction refine it, per the "idempotent
    # fallback" requirement (not "never refines again").
    client = _make_client(
        {"0001045810": {"filings": {"recent": _recent(["0001045810-26-000001"])}}},  # no items column
        {"0001045810-26-000001": b"<html><body><p>Item 2.02 Results of Operations.</p></body></html>"},
    )

    edgar_pipeline.run_pipeline(client, [_NVDA], tmp_path)

    candidate = next(iter(candidate_store.load_candidates(tmp_path, edgar_pipeline.CANDIDATE_STORE_FILENAME).values()))
    assert candidate.matched_rules == ["earnings_or_results:8-K item 2.02"]


def test_bounded_excerpt_reaching_only_one_expected_item_does_not_downgrade_scan_time_classification(tmp_path):
    # The real NVDA case (milestone 8, Gate 8 fix): scan-time metadata
    # knows all three items, but the bounded item-anchored excerpt only
    # reaches Item 1.01's text. The final classification must retain all
    # three categories and High confidence, not downgrade to Moderate.
    client = _make_client(
        {"0001045810": {"filings": {"recent": _recent(["0001045810-26-000001"], items=["1.01,2.03,7.01"])}}},
        # Document text mentions only "Item 1.01" -- Items 2.03/7.01 are
        # never reached by the excerpt (simulating a long real document).
        {"0001045810-26-000001": b"<html><body><p>Item 1.01 Entry into a Material Definitive Agreement. Substantive partnership details here.</p></body></html>"},
    )

    report = edgar_pipeline.run_pipeline(client, [_NVDA], tmp_path)

    assert report.candidates_processed == 1
    candidate = next(iter(candidate_store.load_candidates(tmp_path, edgar_pipeline.CANDIDATE_STORE_FILENAME).values()))
    assert candidate.confidence == "High"
    assert set(candidate.matched_rules) == {
        "material_agreement:8-K item 1.01",
        "financing_or_debt:8-K item 2.03",
        "regulation_fd_disclosure:8-K item 7.01",
    }
    assert candidate.status == CandidateStatus.NEEDS_REVIEW
    # Provenance recorded in state history, distinguishing scan-time
    # metadata from the bounded excerpt.
    last_detail = candidate.state_history[-1].detail
    assert "Scan-time SEC item metadata" in last_detail
    assert "retained as authoritative" in last_detail


def test_reprocessing_does_not_cause_repeated_category_growth_or_duplicate_records(tmp_path):
    client = _make_client(
        {"0001045810": {"filings": {"recent": _recent(["0001045810-26-000001"], items=["1.01,2.03,7.01"])}}},
        {"0001045810-26-000001": b"<html><body><p>Item 1.01 Entry into a Material Definitive Agreement. Details.</p></body></html>"},
    )

    edgar_pipeline.run_pipeline(client, [_NVDA], tmp_path, max_candidates_to_process=0)
    candidate_id = next(iter(candidate_store.load_candidates(tmp_path, edgar_pipeline.CANDIDATE_STORE_FILENAME)))

    first = edgar_pipeline.process_single_candidate(client, candidate_id, tmp_path)
    second = edgar_pipeline.process_single_candidate(client, candidate_id, tmp_path)

    assert set(first.matched_rules) == set(second.matched_rules) == {
        "material_agreement:8-K item 1.01",
        "financing_or_debt:8-K item 2.03",
        "regulation_fd_disclosure:8-K item 7.01",
    }
    assert first.confidence == second.confidence == "High"
    store = candidate_store.load_candidates(tmp_path, edgar_pipeline.CANDIDATE_STORE_FILENAME)
    assert len(store) == 1  # no duplicate CandidateSignal
    from src.data_access.edgar import scan_service
    assert len(scan_service.load_filing_events(tmp_path)) == 1  # no duplicate FilingEvent


def test_complete_424b5_stays_needs_review_when_auto_publish_is_disabled(tmp_path):
    client = _make_client(
        {
            "0001045810": {
                "filings": {
                    "recent": _recent(
                        ["0001045810-26-000050"],
                        forms=["424B5"],
                        primary_docs=["offering.htm"],
                    )
                }
            }
        },
        {
            "0001045810-26-000050": (
                b"<html><body><p>"
                b"We priced the public offering of 10,000,000 shares of common stock. "
                b"The offering is expected to result in gross proceeds of $125 million."
                b"</p></body></html>"
            )
        },
    )

    edgar_pipeline.run_pipeline(
        client,
        [_NVDA],
        tmp_path,
        auto_publish_enabled=False,
    )

    candidate = next(
        iter(candidate_store.load_candidates(
            tmp_path,
            edgar_pipeline.CANDIDATE_STORE_FILENAME,
        ).values())
    )

    assert candidate.status == CandidateStatus.NEEDS_REVIEW
    assert any(
        transition.detail.startswith("Shadow policy: PUBLISH")
        for transition in candidate.state_history
    )


def test_complete_424b5_is_published_when_auto_publish_is_enabled(tmp_path):
    from src.logic.signal_promotion import is_eligible_for_signal

    client = _make_client(
        {
            "0001045810": {
                "filings": {
                    "recent": _recent(
                        ["0001045810-26-000051"],
                        forms=["424B5"],
                        primary_docs=["offering.htm"],
                    )
                }
            }
        },
        {
            "0001045810-26-000051": (
                b"<html><body><p>"
                b"We priced the public offering of 10,000,000 shares of common stock. "
                b"The offering is expected to result in gross proceeds of $125 million."
                b"</p></body></html>"
            )
        },
    )

    edgar_pipeline.run_pipeline(
        client,
        [_NVDA],
        tmp_path,
        auto_publish_enabled=True,
    )

    candidate = next(
        iter(candidate_store.load_candidates(
            tmp_path,
            edgar_pipeline.CANDIDATE_STORE_FILENAME,
        ).values())
    )

    assert candidate.status == CandidateStatus.PUBLISHED
    assert is_eligible_for_signal(candidate) is True
    assert any(
        transition.detail.startswith("Shadow policy: PUBLISH")
        for transition in candidate.state_history
    )


# --- Automatic retry of stale RETRIEVAL_FAILED/PARSE_FAILED candidates ---
# Mirrors tests/test_radar_pipeline.py's DART suite exactly — see that
# file's own comment for the 180-minute (3 x 60 x 1) derivation.

_SCAN_INTERVAL = 60
_FIRST_BACKOFF_MINUTES = retry_policy.AUTOMATIC_RETRY_BACKOFF_MULTIPLIER * _SCAN_INTERVAL


def _failed_candidate(accession_no: str, minutes_ago: int, status: CandidateStatus = CandidateStatus.RETRIEVAL_FAILED, used: int = 1) -> CandidateSignal:
    now = datetime.now(timezone.utc)
    queued_ats = [now - timedelta(minutes=minutes_ago) - timedelta(seconds=i) for i in range(used)]
    history = [StateTransition(status=CandidateStatus.QUEUED_FOR_PROCESSING, at=at.isoformat()) for at in queued_ats]
    history.append(StateTransition(status=status, at=queued_ats[0].isoformat(), detail="EDGAR fetch failure"))
    filing = FilingEvent(
        rcept_no=accession_no, corp_code="0001045810", corp_name="NVIDIA", stock_code="NVDA",
        report_nm="Quarterly report", rcept_dt=datetime.now(timezone.utc).date().isoformat(), flr_nm="NVIDIA",
        pblntf_ty="10-Q", source_name="SEC EDGAR", primary_document="doc.htm",
    )
    return CandidateSignal(
        id=f"edgar-cand-{accession_no}", filing=filing, matched_rules=["earnings_or_results:10-Q"],
        confidence="Moderate", status=status, state_history=history,
    )


def test_stale_retrieval_failed_candidate_is_automatically_retried(tmp_path):
    candidate = _failed_candidate("0001045810-26-000099", minutes_ago=_FIRST_BACKOFF_MINUTES + 1)
    candidate_store.upsert_new_candidates(tmp_path, [candidate], edgar_pipeline.CANDIDATE_STORE_FILENAME)
    client = _make_client({"0001045810": {"filings": {"recent": _recent([])}}})

    edgar_pipeline.run_pipeline(client, [_NVDA], tmp_path, scan_interval_minutes=_SCAN_INTERVAL)

    client.fetch_document.assert_called_once()
    updated = candidate_store.load_candidates(tmp_path, edgar_pipeline.CANDIDATE_STORE_FILENAME)["edgar-cand-0001045810-26-000099"]
    assert updated.extraction_state == ExtractionState.EXTRACTED
    assert updated.status != CandidateStatus.RETRIEVAL_FAILED


def test_fresh_retrieval_failed_candidate_is_not_automatically_retried(tmp_path):
    candidate = _failed_candidate("0001045810-26-000099", minutes_ago=_FIRST_BACKOFF_MINUTES - 1)
    candidate_store.upsert_new_candidates(tmp_path, [candidate], edgar_pipeline.CANDIDATE_STORE_FILENAME)
    client = _make_client({"0001045810": {"filings": {"recent": _recent([])}}})

    edgar_pipeline.run_pipeline(client, [_NVDA], tmp_path, scan_interval_minutes=_SCAN_INTERVAL)

    client.fetch_document.assert_not_called()
    updated = candidate_store.load_candidates(tmp_path, edgar_pipeline.CANDIDATE_STORE_FILENAME)["edgar-cand-0001045810-26-000099"]
    assert len(updated.state_history) == len(candidate.state_history)


def test_candidate_at_max_retry_attempts_is_never_automatically_retried(tmp_path):
    candidate = _failed_candidate(
        "0001045810-26-000099", minutes_ago=_FIRST_BACKOFF_MINUTES * retry_policy.MAX_RETRY_ATTEMPTS + 1000,
        used=retry_policy.MAX_RETRY_ATTEMPTS,
    )
    candidate_store.upsert_new_candidates(tmp_path, [candidate], edgar_pipeline.CANDIDATE_STORE_FILENAME)
    client = _make_client({"0001045810": {"filings": {"recent": _recent([])}}})

    edgar_pipeline.run_pipeline(client, [_NVDA], tmp_path, scan_interval_minutes=_SCAN_INTERVAL)

    client.fetch_document.assert_not_called()


def test_automatic_retry_cap_leaves_excess_stale_failures_completely_untouched(tmp_path):
    candidates = [_failed_candidate(f"0001045810-26-00009{i}", minutes_ago=_FIRST_BACKOFF_MINUTES + 1) for i in (1, 2, 3)]
    candidate_store.upsert_new_candidates(tmp_path, candidates, edgar_pipeline.CANDIDATE_STORE_FILENAME)
    client = _make_client({"0001045810": {"filings": {"recent": _recent([])}}})

    edgar_pipeline.run_pipeline(client, [_NVDA], tmp_path, scan_interval_minutes=_SCAN_INTERVAL)

    assert client.fetch_document.call_count == retry_policy.AUTOMATIC_RETRY_MAX_PER_TICK_PER_SOURCE
    store = candidate_store.load_candidates(tmp_path, edgar_pipeline.CANDIDATE_STORE_FILENAME)
    untouched = [c for c in store.values() if len(c.state_history) == 2]
    assert len(untouched) == 1
    assert untouched[0].status == CandidateStatus.RETRIEVAL_FAILED


def test_automatic_retry_budget_is_independent_of_new_candidate_processing_budget(tmp_path):
    stale = _failed_candidate("0001045810-26-000099", minutes_ago=_FIRST_BACKOFF_MINUTES + 1)
    candidate_store.upsert_new_candidates(tmp_path, [stale], edgar_pipeline.CANDIDATE_STORE_FILENAME)
    client = _make_client(
        {"0001045810": {"filings": {"recent": _recent(["0001045810-26-000001"], forms=["10-Q"])}}},
        {"0001045810-26-000001": b"<html><body><p>Quarterly content.</p></body></html>"},
    )

    report = edgar_pipeline.run_pipeline(client, [_NVDA], tmp_path, scan_interval_minutes=_SCAN_INTERVAL, max_candidates_to_process=1)

    assert report.candidates_detected == 1
    assert report.candidates_processed == 1
    assert report.candidates_deferred == 0
    updated_stale = candidate_store.load_candidates(tmp_path, edgar_pipeline.CANDIDATE_STORE_FILENAME)["edgar-cand-0001045810-26-000099"]
    assert updated_stale.extraction_state == ExtractionState.EXTRACTED


def test_cached_successful_extraction_is_never_automatically_refetched_due_to_age(tmp_path):
    client = _make_client(
        {"0001045810": {"filings": {"recent": _recent(["0001045810-26-000001"], forms=["10-Q"])}}},
        {"0001045810-26-000001": b"<html><body><p>Quarterly content.</p></body></html>"},
    )
    edgar_pipeline.run_pipeline(client, [_NVDA], tmp_path)
    assert client.fetch_document.call_count == 1

    store = candidate_store.load_candidates(tmp_path, edgar_pipeline.CANDIDATE_STORE_FILENAME)
    candidate = next(iter(store.values()))
    ancient = datetime.now(timezone.utc) - timedelta(days=30)
    candidate.state_history = [StateTransition(status=t.status, at=ancient.isoformat(), detail=t.detail) for t in candidate.state_history]
    candidate_store.update_candidate(tmp_path, candidate, edgar_pipeline.CANDIDATE_STORE_FILENAME)

    edgar_pipeline.run_pipeline(client, [_NVDA], tmp_path, scan_interval_minutes=_SCAN_INTERVAL)

    client.fetch_document.assert_called_once()


def test_manual_process_single_candidate_now_actually_refetches_a_stale_failure(tmp_path):
    client = _make_client(
        {"0001045810": {"filings": {"recent": _recent(["0001045810-26-000001"], forms=["10-Q"])}}},
        {"0001045810-26-000001": EdgarApiError(500, "server error")},
    )
    edgar_pipeline.run_pipeline(client, [_NVDA], tmp_path)
    assert client.fetch_document.call_count == 1
    stored = candidate_store.load_candidates(tmp_path, edgar_pipeline.CANDIDATE_STORE_FILENAME)["edgar-cand-0001045810-26-000001"]
    assert stored.status == CandidateStatus.RETRIEVAL_FAILED

    client.fetch_document.side_effect = lambda cik, accession_no, filename: b"<html><body><p>Now healthy.</p></body></html>"
    result = edgar_pipeline.process_single_candidate(client, "edgar-cand-0001045810-26-000001", tmp_path)

    assert result.extraction_state == ExtractionState.EXTRACTED
    assert client.fetch_document.call_count == 2
