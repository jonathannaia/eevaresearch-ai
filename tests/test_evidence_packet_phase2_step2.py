"""Radar evidence-packet foundation, Phase 2, Step 2 (design/DECISIONS.md)
— persisting truthful provenance for an excerpt extracted from a member
inside an official EDINET ZIP package via the new, optional
`CandidateSignal.evidence_source_member` field. Builds directly on Phase
1's `record_excerpt()` first-write-only contract (see
tests/test_evidence_packet_phase1.py) and Phase 2, Step 1's bounded ZIP
extraction (see tests/test_edinet_document_extractor.py). Covers the
model-level atomicity contract, pipeline threading (EDINET only — EDGAR/
DART stay None), and JSON backward compatibility/round-trip. SQLite/
Postgres round-trip and backward-compat live in
tests/test_state_db_candidate_repository.py and
tests/test_state_db_postgres_candidate_repository.py, mirroring where
Phase 1's own persistence tests live. No network calls anywhere in this
file."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from src.data_access.dart import radar_pipeline as dart_radar_pipeline
from src.data_access.dart.document_service import DocumentFetchResult as DartDocumentFetchResult
from src.data_access.edgar import edgar_pipeline
from src.data_access.edgar.document_service import DocumentFetchResult as EdgarDocumentFetchResult
from src.data_access.edinet import edinet_pipeline
from src.data_access.edinet.document_service import DocumentFetchResult as EdinetDocumentFetchResult
from src.models.models import (
    CandidateSignal,
    CandidateStatus,
    ExtractionState,
    FilingEvent,
    TranslationState,
    record_excerpt,
)


def _bare_candidate(**overrides) -> CandidateSignal:
    filing = FilingEvent(rcept_no="R1", corp_code="C1", corp_name="Example Corp", flr_nm="Example Corp", stock_code="EX", report_nm="Example filing", rcept_dt="2026-08-20")
    defaults = dict(id="cand-R1", filing=filing, matched_rules=["earnings:earnings_or_results"], confidence="Moderate", status=CandidateStatus.CANDIDATE_DETECTED)
    defaults.update(overrides)
    return CandidateSignal(**defaults)


# ============================================================
# Part A — record_excerpt() with evidence_source_member, pure unit tests
# ============================================================


def test_record_excerpt_first_write_with_member_sets_both_atomically():
    candidate = _bare_candidate()
    changed = record_excerpt(candidate, "ZIP-sourced text.", "2026-08-20T00:00:00+00:00", evidence_source_member="PublicDoc/0101.pdf")
    assert changed is True
    assert candidate.excerpt_original == "ZIP-sourced text."
    assert candidate.evidence_source_member == "PublicDoc/0101.pdf"


def test_record_excerpt_first_write_without_member_leaves_it_none():
    candidate = _bare_candidate()
    record_excerpt(candidate, "Bare PDF text.", "2026-08-20T00:00:00+00:00")
    assert candidate.evidence_source_member is None


def test_record_excerpt_later_extraction_with_different_member_never_overwrites_original_member():
    candidate = _bare_candidate()
    record_excerpt(candidate, "First extraction.", "2026-08-20T00:00:00+00:00", evidence_source_member="PublicDoc/0101.pdf")
    changed = record_excerpt(candidate, "A different, refined extraction.", "2026-08-25T00:00:00+00:00", evidence_source_member="PublicDoc/0202.pdf")
    assert changed is False
    assert candidate.excerpt_original == "First extraction."  # Phase 1 immutability, unaffected
    assert candidate.evidence_source_member == "PublicDoc/0101.pdf"  # never replaced by the later member
    assert candidate.excerpt_supplemental == "A different, refined extraction."


def test_record_excerpt_none_text_ignores_member_too():
    candidate = _bare_candidate()
    changed = record_excerpt(candidate, None, "2026-08-20T00:00:00+00:00", evidence_source_member="PublicDoc/0101.pdf")
    assert changed is False
    assert candidate.evidence_source_member is None


def test_evidence_source_member_defaults_to_none():
    assert _bare_candidate().evidence_source_member is None


# ============================================================
# Part B — EDINET pipeline integration: ZIP-member provenance threading
# ============================================================


def _edinet_filing(rcept_no: str) -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="E02778", corp_name="SoftBank Group", flr_nm="SoftBank Group",
        stock_code="9984", report_nm="有価証券報告書", rcept_dt="2026-08-20", source_name="EDINET",
        original_language="Japanese",
    )


def test_edinet_pipeline_records_evidence_source_member_from_zip_extraction(tmp_path, monkeypatch):
    filing = _edinet_filing("S100ZIP1")
    candidate = CandidateSignal(id="edinet-cand-zip-1", filing=filing, matched_rules=["annual_securities_report:010:030000:120"], confidence="Moderate", status=CandidateStatus.CANDIDATE_DETECTED)

    doc_result = EdinetDocumentFetchResult(
        doc_id="S100ZIP1", state=ExtractionState.EXTRACTED, excerpt_original="ZIP-sourced evidence.",
        detail="", retrieved_at="2026-08-20T00:00:00+00:00", from_cache=False,
        evidence_source_member="PublicDoc/0101.pdf",
    )
    monkeypatch.setattr(edinet_pipeline.document_service, "get_or_fetch_excerpt", lambda *a, **k: doc_result)

    counters = {"documents_retrieved": 0, "documents_extracted": 0, "cache_hits": 0}
    edinet_pipeline.process_candidate(MagicMock(), candidate, tmp_path, counters, {})

    assert candidate.excerpt_original == "ZIP-sourced evidence."
    assert candidate.evidence_source_member == "PublicDoc/0101.pdf"


def test_edinet_pipeline_bare_pdf_extraction_leaves_evidence_source_member_none(tmp_path, monkeypatch):
    filing = _edinet_filing("S100PDF1")
    candidate = CandidateSignal(id="edinet-cand-pdf-1", filing=filing, matched_rules=["annual_securities_report:010:030000:120"], confidence="Moderate", status=CandidateStatus.CANDIDATE_DETECTED)

    doc_result = EdinetDocumentFetchResult(
        doc_id="S100PDF1", state=ExtractionState.EXTRACTED, excerpt_original="Bare-PDF evidence.",
        detail="", retrieved_at="2026-08-20T00:00:00+00:00", from_cache=False,
        evidence_source_member=None,
    )
    monkeypatch.setattr(edinet_pipeline.document_service, "get_or_fetch_excerpt", lambda *a, **k: doc_result)

    counters = {"documents_retrieved": 0, "documents_extracted": 0, "cache_hits": 0}
    edinet_pipeline.process_candidate(MagicMock(), candidate, tmp_path, counters, {})

    assert candidate.excerpt_original == "Bare-PDF evidence."
    assert candidate.evidence_source_member is None


def test_edinet_unsupported_and_parse_failed_extractions_leave_evidence_source_member_none(tmp_path, monkeypatch):
    for state in (ExtractionState.UNSUPPORTED_FORMAT, ExtractionState.PARSE_FAILED, ExtractionState.RETRIEVAL_FAILED):
        filing = _edinet_filing(f"S100FAIL-{state.value}")
        candidate = CandidateSignal(id=f"edinet-cand-fail-{state.value}", filing=filing, matched_rules=["annual_securities_report:010:030000:120"], confidence="Moderate", status=CandidateStatus.CANDIDATE_DETECTED)
        doc_result = EdinetDocumentFetchResult(
            doc_id=filing.rcept_no, state=state, excerpt_original=None, detail="failed", retrieved_at="2026-08-20T00:00:00+00:00", from_cache=False,
        )
        monkeypatch.setattr(edinet_pipeline.document_service, "get_or_fetch_excerpt", lambda *a, **k: doc_result)
        counters = {"documents_retrieved": 0, "documents_extracted": 0, "cache_hits": 0}
        edinet_pipeline.process_candidate(MagicMock(), candidate, tmp_path, counters, {})
        assert candidate.evidence_source_member is None


def test_edinet_pipeline_never_overwrites_member_reference_on_reprocess(tmp_path, monkeypatch):
    filing = _edinet_filing("S100ZIP2")
    candidate = CandidateSignal(id="edinet-cand-zip-2", filing=filing, matched_rules=["annual_securities_report:010:030000:120"], confidence="Moderate", status=CandidateStatus.CANDIDATE_DETECTED)

    results = iter([
        EdinetDocumentFetchResult(doc_id="S100ZIP2", state=ExtractionState.EXTRACTED, excerpt_original="First extraction.", detail="", retrieved_at="2026-08-20T00:00:00+00:00", from_cache=False, evidence_source_member="PublicDoc/0101.pdf"),
        EdinetDocumentFetchResult(doc_id="S100ZIP2", state=ExtractionState.EXTRACTED, excerpt_original="Second, different extraction.", detail="", retrieved_at="2026-08-21T00:00:00+00:00", from_cache=False, evidence_source_member="PublicDoc/0202.pdf"),
    ])
    monkeypatch.setattr(edinet_pipeline.document_service, "get_or_fetch_excerpt", lambda *a, **k: next(results))

    counters = {"documents_retrieved": 0, "documents_extracted": 0, "cache_hits": 0}
    edinet_pipeline.process_candidate(MagicMock(), candidate, tmp_path, counters, {})
    assert candidate.evidence_source_member == "PublicDoc/0101.pdf"

    edinet_pipeline.process_candidate(MagicMock(), candidate, tmp_path, counters, {})
    assert candidate.excerpt_original == "First extraction."  # Phase 1 immutability, unaffected
    assert candidate.evidence_source_member == "PublicDoc/0101.pdf"  # never replaced by the later member
    assert candidate.excerpt_supplemental == "Second, different extraction."


def test_edinet_pipeline_member_provenance_does_not_disturb_other_evidence_fields(tmp_path, monkeypatch):
    """Confirms evidence_source_member threading is additive only — every
    other evidence-packet field (translation state, evidence location,
    filing id/source URL, review state) is untouched by this change."""
    filing = _edinet_filing("S100ZIP3")
    filing.source_url = "https://api.edinet-fsa.go.jp/api/v2/documents/S100ZIP3"
    candidate = CandidateSignal(
        id="edinet-cand-zip-3", filing=filing, matched_rules=["annual_securities_report:010:030000:120"],
        confidence="Moderate", status=CandidateStatus.CANDIDATE_DETECTED, reviewed_note="pre-existing note",
    )
    doc_result = EdinetDocumentFetchResult(
        doc_id="S100ZIP3", state=ExtractionState.EXTRACTED, excerpt_original="ZIP-sourced evidence.",
        detail="", retrieved_at="2026-08-20T00:00:00+00:00", from_cache=False,
        evidence_source_member="PublicDoc/0101.pdf",
    )
    monkeypatch.setattr(edinet_pipeline.document_service, "get_or_fetch_excerpt", lambda *a, **k: doc_result)

    counters = {"documents_retrieved": 0, "documents_extracted": 0, "cache_hits": 0}
    edinet_pipeline.process_candidate(MagicMock(), candidate, tmp_path, counters, {})

    assert candidate.evidence_source_member == "PublicDoc/0101.pdf"
    assert candidate.filing.rcept_no == "S100ZIP3"
    assert candidate.filing.source_url == "https://api.edinet-fsa.go.jp/api/v2/documents/S100ZIP3"
    assert candidate.reviewed_note == "pre-existing note"
    assert candidate.translation_state == TranslationState.PENDING  # unaffected, no provider given
    assert candidate.evidence_location is None  # EDINET never sets this (Phase 1 behavior, unchanged)


# ============================================================
# Part C — evidence_source_member is EDINET-ZIP-only; EDGAR/DART stay None
# ============================================================


def test_edgar_candidates_never_populate_evidence_source_member(tmp_path, monkeypatch):
    filing = FilingEvent(rcept_no="0001-26-000002", corp_code="0000320193", corp_name="Apple Inc.", flr_nm="Apple Inc.", stock_code="AAPL", report_nm="8-K", rcept_dt="2026-08-20", pblntf_ty="8-K", source_name="SEC EDGAR", original_language="English", primary_document="ex99.htm")
    candidate = CandidateSignal(id="edgar-cand-nomember", filing=filing, matched_rules=["material_event_8k_pending_items:8-K"], confidence="Moderate", status=CandidateStatus.CANDIDATE_DETECTED)

    doc_result = EdgarDocumentFetchResult(accession_no="0001-26-000002", state=ExtractionState.EXTRACTED, excerpt_original="EDGAR excerpt.", detail="", retrieved_at="2026-08-20T00:00:00+00:00", from_cache=False)
    monkeypatch.setattr(edgar_pipeline.document_service, "get_or_fetch_excerpt", lambda *a, **k: doc_result)

    counters = {"documents_retrieved": 0, "documents_extracted": 0, "cache_hits": 0}
    edgar_pipeline.process_candidate(MagicMock(), candidate, tmp_path, counters, {})
    assert candidate.excerpt_original == "EDGAR excerpt."
    assert candidate.evidence_source_member is None


def test_dart_candidates_never_populate_evidence_source_member(tmp_path, monkeypatch):
    filing = FilingEvent(rcept_no="R2", corp_code="00126380", corp_name="삼성전자", flr_nm="삼성전자", stock_code="005930", report_nm="실적발표", rcept_dt="20260820", source_name="OpenDART / DART")
    candidate = CandidateSignal(id="cand-dart-nomember", filing=filing, matched_rules=["earnings:earnings_or_results_report:실적"], confidence="Moderate", status=CandidateStatus.CANDIDATE_DETECTED)

    doc_result = DartDocumentFetchResult(rcept_no="R2", state=ExtractionState.EXTRACTED, excerpt_original="DART excerpt.", detail="", retrieved_at="2026-08-20T00:00:00+00:00", from_cache=False)
    monkeypatch.setattr(dart_radar_pipeline.document_service, "get_or_fetch_excerpt", lambda *a, **k: doc_result)
    from src.data_access.translation.translation_service import TranslationAttempt

    monkeypatch.setattr(dart_radar_pipeline, "translate_cached_with_outcome", lambda *a, **k: TranslationAttempt(translation=None))
    provider = MagicMock()
    provider.name = "DeepL"

    counters = {"documents_retrieved": 0, "documents_extracted": 0, "translations_completed": 0, "cache_hits": 0}
    dart_radar_pipeline.process_candidate(MagicMock(), provider, candidate, tmp_path, counters, {})
    assert candidate.excerpt_original == "DART excerpt."
    assert candidate.evidence_source_member is None


# ============================================================
# Part D — JSON persistence: backward compatibility and round-trip
# ============================================================


def test_old_json_record_without_evidence_source_member_loads_as_none(tmp_path):
    from src.data_access.dart import candidate_store

    legacy_payload = {
        "cand-legacy-p2s2": {
            "id": "cand-legacy-p2s2",
            "filing": {"rcept_no": "R-legacy2", "corp_code": "E02778", "corp_name": "SoftBank Group", "stock_code": "9984", "report_nm": "有価証券報告書", "rcept_dt": "20260101", "flr_nm": "SoftBank Group"},
            "matched_rules": ["annual_securities_report:010:030000:120"],
            "confidence": "Moderate",
            "status": "Candidate detected",
        }
    }
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "dart_candidates.json").write_text(json.dumps(legacy_payload, ensure_ascii=False), encoding="utf-8")

    loaded = candidate_store.load_candidates(tmp_path)
    assert loaded["cand-legacy-p2s2"].evidence_source_member is None


def test_json_round_trip_preserves_evidence_source_member(tmp_path):
    from src.data_access.dart import candidate_store

    filing = _edinet_filing("S100ZIP4")
    candidate = CandidateSignal(
        id="edinet-cand-p2s2", filing=filing, matched_rules=["annual_securities_report:010:030000:120"], confidence="Moderate",
        status=CandidateStatus.NEEDS_REVIEW, excerpt_original="ZIP-sourced evidence.",
        excerpt_retrieved_at="2026-08-20T00:00:00+00:00", evidence_source_member="PublicDoc/0101.pdf",
    )
    candidate_store.upsert_new_candidates(tmp_path, [candidate])
    reloaded = candidate_store.load_candidates(tmp_path)["edinet-cand-p2s2"]

    assert reloaded.evidence_source_member == "PublicDoc/0101.pdf"
    assert reloaded.excerpt_original == "ZIP-sourced evidence."


# ============================================================
# Part E — scope guard: no forbidden imports/paths introduced this step
# ============================================================


def test_no_forbidden_imports_introduced_in_phase2_step2_files():
    import ast

    repo_root = Path(__file__).parent.parent
    # document_extractor.py is deliberately excluded here: Step 2 added
    # no NEW import there (only a new dataclass field and a return-value
    # change) — its imports, including zipfile/pypdf (both pre-approved
    # in Step 1 / Gate 10.A), were already verified by Step 1's own scope
    # guard in tests/test_edinet_document_extractor.py.
    changed_files = [
        "src/models/models.py",
        "src/data_access/edinet/document_service.py",
        "src/data_access/edinet/edinet_pipeline.py",
        "src/data_access/dart/candidate_store.py",
        "src/data_access/state_db/schema.py",
        "src/data_access/state_db/candidate_repository.py",
        "src/data_access/postgres_state_db/schema.py",
        "src/data_access/postgres_state_db/candidate_repository.py",
    ]
    forbidden_modules = (
        "zipfile", "pypdf", "PyPDF2", "pytesseract", "PIL", "cv2",
        "langchain", "anthropic", "openai", "chromadb", "pinecone", "weaviate", "networkx",
        "schedule", "apscheduler", "celery",
        "requests", "httpx", "urllib",
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
    import subprocess

    repo_root = Path(__file__).parent.parent
    result = subprocess.run(["git", "diff", "HEAD", "--", "requirements.txt"], cwd=repo_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    assert result.stdout.strip() == "", f"requirements.txt was modified: {result.stdout}"


def test_no_ui_daily_news_edgar_dart_worker_or_deployment_files_touched():
    """Runs against `git diff HEAD` — only meaningful in a real checkout
    with this step's changes present, so it spuriously fires while ANY
    other legitimate uncommitted change happens to touch one of these
    paths, and resolves once committed — same documented convention as
    this repo's other phase-scoped scope guards
    (tests/test_ui_audit_phase_r1.py etc.)."""
    import subprocess

    repo_root = Path(__file__).parent.parent
    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    forbidden_prefixes = ("src/ui/", "src/data_access/daily_news/")
    forbidden_paths = {
        "scripts/radar_worker.py", "render.yaml", "design/RADAR_WORKER_DEPLOYMENT.md",
        "src/data_access/edgar/edgar_pipeline.py", "src/data_access/edgar/scan_service.py",
        "src/data_access/edgar/document_extractor.py", "src/data_access/edgar/document_service.py",
        "src/data_access/dart/radar_pipeline.py", "src/data_access/dart/scan_service.py",
        "src/data_access/dart/document_extractor.py", "src/data_access/dart/document_service.py",
        "src/data_access/dart/ownership_materiality.py", "src/data_access/dart/retry_policy.py",
        "src/data_access/translation/translation_service.py", "src/data_access/translation/deepl_provider.py",
    }
    hit = {c for c in changed if c in forbidden_paths or any(c.startswith(p) for p in forbidden_prefixes)}
    assert not hit, hit
