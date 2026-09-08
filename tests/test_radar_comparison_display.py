"""Radar evidence-packet foundation, Phase 3, Step 3B (design/DECISIONS.md)
— read-only Radar Inbox integration for the latest persisted comparison
record per rendered candidate: one bulk repository read per page render,
bounded to exactly the candidate ids on that page, and a small,
conditional, identity-checked "Comparison" row + fixed caveat in the
existing evidence panel. Every ComparisonRecord here is directly
constructed (never computed via build_comparison_result/
select_prior_candidate, never persisted/read from a real store). No
network calls, no scan, no live service call anywhere in this file."""
from __future__ import annotations

import ast
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.comparison_store import ComparisonRecord, build_comparison_record
from src.data_access.dart import candidate_store
from src.logic.prior_disclosure_comparison import ComparisonResult, ComparisonStatus
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, StateTransition
from src.ui.components.radar_status import comparison_status_label

_HARNESS = Path(__file__).parent / "apptest_pages" / "radar_inbox_page.py"
_COMPARISON_CAVEAT = "Deterministic rule-category comparison — not a filing-text, financial, or materiality determination."


@pytest.fixture(autouse=True)
def _clear_dashboard_snapshot_cache():
    from src.ui.pages.radar_inbox import _load_dashboard_snapshot

    _load_dashboard_snapshot.clear()
    yield
    _load_dashboard_snapshot.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_corp_codes(cache_dir) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {"005930": {"corp_code": "00126380", "corp_name": "삼성전자", "source": "OpenDART corpCode.xml", "retrieved_at": _now_iso()}}
    (cache_dir / "dart_corp_codes.json").write_text(json.dumps(payload), encoding="utf-8")


def _filing(rcept_no: str) -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm="유상증자 결정", rcept_dt="20260812", flr_nm="삼성전자", theme_slug="memory",
        source_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
        retrieved_at=_now_iso(),
    )


def _seed_filing_events(cache_dir, filings: list[FilingEvent]) -> None:
    from dataclasses import asdict

    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {"seen_receipt_numbers": [f.rcept_no for f in filings], "filing_events": [asdict(f) for f in filings], "candidate_signals": []}
    (cache_dir / "dart_filing_events.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _candidate(candidate_id: str, filing: FilingEvent, **overrides) -> CandidateSignal:
    defaults = dict(
        id=candidate_id, filing=filing, matched_rules=["financing:capital_increase:유상증자"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="본문 발췌.",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso(), detail="Extraction succeeded.")],
    )
    defaults.update(overrides)
    return CandidateSignal(**defaults)


def _comparison_record(current_candidate_id: str, **overrides) -> ComparisonRecord:
    result_fields = {
        "comparison_status": overrides.pop("comparison_status", ComparisonStatus.CHANGE_DETECTED.value),
        "comparison_basis": "matched_rules_set_diff:v1",
        "computed_at": overrides.pop("computed_at", _now_iso()),
        "prior_document_id": overrides.pop("prior_document_id", "R-prior"),
        "prior_filed_at": None,
        "added_categories": overrides.pop("added_categories", ("financing_or_debt",)),
        "removed_categories": overrides.pop("removed_categories", ()),
        "prior_excerpt": overrides.pop("prior_excerpt", "Prior excerpt."),
        "current_excerpt": overrides.pop("current_excerpt", "Current excerpt."),
        "limitations": overrides.pop("limitations", ("Comparable reporting period is not available in current metadata.",)),
    }
    result = ComparisonResult(**result_fields)
    return build_comparison_record(
        result, current_candidate_id=current_candidate_id, current_source_name="OpenDART / DART",
        current_corp_code="00126380", current_document_id="R-current",
    )


class _FakeComparisonRepository:
    def __init__(self, records_by_candidate_id: dict[str, ComparisonRecord], call_log: list[list[str]]):
        self._records_by_candidate_id = records_by_candidate_id
        self._call_log = call_log

    def latest_for_candidate_ids(self, candidate_ids):
        self._call_log.append(list(candidate_ids))
        return {cid: self._records_by_candidate_id[cid] for cid in candidate_ids if cid in self._records_by_candidate_id}


def _patched_factory(records_by_candidate_id: dict[str, ComparisonRecord], call_log: list):
    def _fake_get_comparison_repository(settings):
        return _FakeComparisonRepository(records_by_candidate_id, call_log)

    return patch("src.ui.pages.radar_inbox.backend_factory.get_comparison_repository", side_effect=_fake_get_comparison_repository)


# ============================================================
# Part A — page-level bulk read (proofs 1, 2, 3, 4, 5)
# ============================================================


def test_paginated_render_makes_exactly_one_bulk_comparison_call(tmp_path):
    from src.ui.pages.radar_inbox import PAGE_SIZE

    _seed_corp_codes(tmp_path)
    filings = [_filing(f"R-{i:02d}") for i in range(1, PAGE_SIZE + 6)]  # more than one page's worth
    _seed_filing_events(tmp_path, filings)
    candidates = {f"cand-{f.rcept_no}": _candidate(f"cand-{f.rcept_no}", f) for f in filings}
    candidate_store.save_candidates(tmp_path, candidates)

    call_log: list[list[str]] = []
    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings), _patched_factory({}, call_log):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    assert len(call_log) == 1  # exactly one bulk call for this render, never one per card


def test_bulk_request_contains_only_current_page_candidate_ids_not_other_pages(tmp_path):
    from src.ui.pages.radar_inbox import PAGE_SIZE

    _seed_corp_codes(tmp_path)
    filings = [_filing(f"R-{i:02d}") for i in range(1, PAGE_SIZE + 6)]
    _seed_filing_events(tmp_path, filings)
    candidates = {f"cand-{f.rcept_no}": _candidate(f"cand-{f.rcept_no}", f) for f in filings}
    candidate_store.save_candidates(tmp_path, candidates)

    all_ids = set(candidates.keys())
    call_log: list[list[str]] = []
    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings), _patched_factory({}, call_log):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    assert len(call_log) == 1
    page_1_ids = set(call_log[0])
    assert len(page_1_ids) == PAGE_SIZE  # only this page's worth
    assert page_1_ids < all_ids  # a strict subset — the remaining ids (page 2) are excluded


def test_no_comparison_repository_call_when_page_has_no_candidate_ids(tmp_path):
    # The unified feed renders filings that never became candidates too —
    # every item.candidate is None, so page_candidate_ids is empty.
    _seed_corp_codes(tmp_path)
    filings = [_filing("R-01"), _filing("R-02")]
    _seed_filing_events(tmp_path, filings)
    candidate_store.save_candidates(tmp_path, {})

    call_log: list[list[str]] = []
    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings), _patched_factory({}, call_log):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    assert call_log == []  # never called — no candidate ids existed on this page


def test_repository_obtained_only_through_get_comparison_repository_factory():
    repo_root = Path(__file__).parent.parent
    source = (repo_root / "src" / "ui" / "pages" / "radar_inbox.py").read_text(encoding="utf-8")
    assert "backend_factory.get_comparison_repository(" in source
    forbidden_direct_imports = (
        "from src.data_access.comparison_store import",
        "from src.data_access.state_db.comparison_repository",
        "from src.data_access.postgres_state_db.comparison_repository",
        "JsonComparisonRepository", "SqliteComparisonRepository", "PostgresComparisonRepository",
    )
    for forbidden in forbidden_direct_imports:
        assert forbidden not in source, f"radar_inbox.py must obtain the comparison repository only via the factory, found {forbidden!r}"


def test_repository_error_is_caught_and_radar_continues_rendering(tmp_path):
    _seed_corp_codes(tmp_path)
    filing = _filing("R-01")
    _seed_filing_events(tmp_path, [filing])
    candidate_store.save_candidates(tmp_path, {"cand-R-01": _candidate("cand-R-01", filing)})

    def _raising_get_comparison_repository(settings):
        raise RuntimeError("simulated repository construction failure — must never surface to the user")

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings), \
         patch("src.ui.pages.radar_inbox.backend_factory.get_comparison_repository", side_effect=_raising_get_comparison_repository):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception  # the page itself never crashes
    all_text = " ".join(m.value for m in at.markdown)
    assert "유상증자 결정" in all_text  # the card still rendered
    assert "simulated repository construction failure" not in all_text  # raw exception text never surfaced
    assert _COMPARISON_CAVEAT not in all_text  # no comparison row could have rendered — the repository never returned data


# ============================================================
# Part B — status-label helper (proofs 6, 7, 8, 9, 10)
# ============================================================


@pytest.mark.parametrize("status,expected_label", [
    ("NOT_AVAILABLE", "Comparison unavailable"),
    ("NOT_COMPARABLE", "Not comparable"),
    ("NO_MATERIAL_CHANGE", "Detection categories unchanged"),
    ("CHANGE_DETECTED", "Detection categories changed"),
])
def test_comparison_status_label_maps_each_known_status(status, expected_label):
    assert comparison_status_label(status) == expected_label


@pytest.mark.parametrize("malformed", ["SOMETHING_UNKNOWN", "", None, 42, ["a", "list"], {"a": "dict"}])
def test_comparison_status_label_never_raises_on_malformed_input(malformed):
    assert comparison_status_label(malformed) == "Comparison unavailable"


# ============================================================
# Part C — card display (proofs 6-15)
#
# Radar simplicity workstream: the Comparison row (and the Evidence
# status panel it lived inside) is removed from the public card
# entirely — none of the render-specific proofs below apply any more.
# The absence-only proofs are kept (they remain trivially, meaningfully
# true) and the render-positive proofs are replaced by
# test_no_comparison_row_renders_in_any_state below.
# ============================================================


def _render_single_candidate_page(tmp_path, candidate: CandidateSignal, records_by_candidate_id: dict):
    _seed_corp_codes(tmp_path)
    _seed_filing_events(tmp_path, [candidate.filing])
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})

    call_log: list[list[str]] = []
    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings), _patched_factory(records_by_candidate_id, call_log):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
    return at


@pytest.mark.parametrize("status", [
    ComparisonStatus.NOT_AVAILABLE.value, ComparisonStatus.NOT_COMPARABLE.value,
    ComparisonStatus.NO_MATERIAL_CHANGE.value, ComparisonStatus.CHANGE_DETECTED.value,
    "SOME_FUTURE_STATUS_NOT_YET_KNOWN",
])
def test_no_comparison_row_renders_in_any_state(tmp_path, status):
    candidate = _candidate("cand-R-01", _filing("R-01"))
    record = _comparison_record("cand-R-01", comparison_status=status)
    at = _render_single_candidate_page(tmp_path, candidate, {"cand-R-01": record})

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert _COMPARISON_CAVEAT not in all_text
    assert "Detection categories" not in all_text
    assert "Not comparable" not in all_text


def test_no_comparison_record_renders_no_row_and_no_caveat(tmp_path):
    candidate = _candidate("cand-R-01", _filing("R-01"))
    at = _render_single_candidate_page(tmp_path, candidate, {})  # no record for this candidate

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "Comparison unavailable" not in all_text
    assert "Not comparable" not in all_text
    assert "Detection categories" not in all_text
    assert _COMPARISON_CAVEAT not in all_text


def test_mismatched_candidate_id_renders_no_row_and_no_caveat(tmp_path):
    candidate = _candidate("cand-R-01", _filing("R-01"))
    # The record's own current_candidate_id refers to a DIFFERENT
    # candidate than the one being rendered — a hypothetical bulk-lookup
    # mis-grouping bug must never cause this to display.
    mismatched_record = _comparison_record("cand-SOME-OTHER-CANDIDATE")
    at = _render_single_candidate_page(tmp_path, candidate, {"cand-R-01": mismatched_record})

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "Detection categories changed" not in all_text
    assert _COMPARISON_CAVEAT not in all_text


def test_candidate_absent_renders_no_comparison_row(tmp_path):
    # The unified feed renders a bare FilingEvent with no CandidateSignal
    # at all — comparison never applies.
    _seed_corp_codes(tmp_path)
    filing = _filing("R-01")
    _seed_filing_events(tmp_path, [filing])
    candidate_store.save_candidates(tmp_path, {})

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    call_log: list[list[str]] = []
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings), _patched_factory({}, call_log):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert _COMPARISON_CAVEAT not in all_text


def test_unsafe_content_in_record_fields_never_appears_in_rendered_output(tmp_path):
    # Radar simplicity workstream: the Comparison row itself no longer
    # renders at all, so this is now a simpler, still-meaningful proof
    # that a comparison record's own fields never leak into the card
    # regardless.
    candidate = _candidate("cand-R-01", _filing("R-01"))
    unsafe_record = _comparison_record(
        "cand-R-01",
        added_categories=("<script>alert('added')</script>",),
        removed_categories=("<img src=x onerror=alert('removed')>",),
        limitations=("<b>unsafe limitation</b>",),
        prior_excerpt="<b>prior excerpt</b>",
        current_excerpt="<b>current excerpt</b>",
        prior_document_id="<b>R-prior</b>",
    )
    at = _render_single_candidate_page(tmp_path, candidate, {"cand-R-01": unsafe_record})

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "alert('added')" not in all_text
    assert "alert('removed')" not in all_text
    assert "unsafe limitation" not in all_text
    assert "prior excerpt" not in all_text
    assert "current excerpt" not in all_text
    assert "R-prior" not in all_text
    assert "Detection categories changed" not in all_text


# ============================================================
# Part D — existing behavior preserved (proof 17)
# ============================================================


def test_existing_filtering_ordering_pagination_source_links_and_translation_unchanged(tmp_path):
    # A light-touch confirmation that the surrounding page still behaves;
    # the full behavioral suite lives in tests/test_radar_inbox_page.py
    # and passes unmodified alongside this file. Radar simplicity
    # workstream: "English working translation"/"Original document" are
    # gone — the card's own new 5-field contract is what's confirmed here.
    from src.models.models import Translation

    filing = _filing("R-01")
    candidate = _candidate(
        "cand-R-01", filing,
        excerpt_translation=Translation(translated_text="Body excerpt.", provider="DeepL", source_lang="ko", target_lang="en", translated_at=_now_iso()),
    )
    at = _render_single_candidate_page(tmp_path, candidate, {"cand-R-01": _comparison_record("cand-R-01")})

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    # Filing-quality pass: Summary is grounded in the stored English
    # translation, shown directly (the toggle re-reveals it in full).
    assert "Body excerpt." in all_text
    assert any(b.label == "Show English translation" for b in at.button)
    assert any(b.label == "Open original filing ↗" for b in at.get("link_button"))


# ============================================================
# Part E — no comparison computation, no writes (proofs 18, 19)
# ============================================================


def test_no_comparison_algorithm_function_referenced_in_ui_files():
    repo_root = Path(__file__).parent.parent
    files = ["src/ui/pages/radar_inbox.py", "src/ui/components/radar_card.py", "src/ui/components/radar_status.py"]
    forbidden_names = {"build_comparison_result", "select_prior_candidate", "compare_matched_rules"}
    offenders = []
    for rel_path in files:
        source = (repo_root / rel_path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=rel_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in forbidden_names:
                offenders.append(f"{rel_path}: references {node.id!r}")
    assert not offenders, offenders


def test_no_comparison_write_path_referenced_in_ui_files():
    repo_root = Path(__file__).parent.parent
    files = ["src/ui/pages/radar_inbox.py", "src/ui/components/radar_card.py", "src/ui/components/radar_status.py"]
    forbidden_names = {"append_comparison_record", "insert_comparison_record"}
    offenders = []
    for rel_path in files:
        source = (repo_root / rel_path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=rel_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in forbidden_names:
                offenders.append(f"{rel_path}: references {node.id!r}")
    assert not offenders, offenders


def test_rendering_never_persists_a_comparison_record(tmp_path):
    candidate = _candidate("cand-R-01", _filing("R-01"))
    _render_single_candidate_page(tmp_path, candidate, {"cand-R-01": _comparison_record("cand-R-01")})
    assert not (tmp_path / "comparison_records.json").exists()  # never written by rendering


# ============================================================
# Part F — scope guards (proof 20)
# ============================================================


def test_scope_guard_only_approved_ui_files_and_tests_changed():
    """Runs against `git diff HEAD` — only meaningful in a real checkout
    with this step's changes present; spuriously fires while ANY other
    legitimate uncommitted change is present and resolves once
    committed — same documented convention as this repo's other
    phase-scoped scope guards."""
    repo_root = Path(__file__).parent.parent
    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    allowed = {
        "src/ui/pages/radar_inbox.py",
        "src/ui/components/radar_card.py",
        "src/ui/components/radar_status.py",
        "tests/test_radar_comparison_display.py",
    }
    assert changed <= allowed, changed - allowed


def test_no_daily_news_navigation_theme_pipeline_client_or_deployment_files_touched():
    repo_root = Path(__file__).parent.parent
    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    forbidden_prefixes = ("src/data_access/daily_news/",)
    forbidden_paths = {
        "src/ui/pages/daily_news.py", "src/ui/ui.py", "app.py",
        "scripts/radar_worker.py", "render.yaml", "design/RADAR_WORKER_DEPLOYMENT.md",
        "src/data_access/edgar/client.py", "src/data_access/edgar/scan_service.py", "src/data_access/edgar/edgar_pipeline.py",
        "src/data_access/edgar/document_extractor.py", "src/data_access/edgar/document_service.py",
        "src/data_access/dart/client.py", "src/data_access/dart/scan_service.py", "src/data_access/dart/radar_pipeline.py",
        "src/data_access/dart/document_extractor.py", "src/data_access/dart/document_service.py",
        "src/data_access/edinet/client.py", "src/data_access/edinet/scan_service.py", "src/data_access/edinet/edinet_pipeline.py",
        "src/data_access/edinet/document_extractor.py", "src/data_access/edinet/document_service.py",
        "src/data_access/translation/translation_service.py", "src/data_access/translation/deepl_provider.py",
        "src/models/models.py", "src/logic/prior_disclosure_comparison.py",
        "src/data_access/comparison_store.py", "src/data_access/backend_factory.py",
        "src/data_access/state_db/schema.py", "src/data_access/postgres_state_db/schema.py",
        "src/data_access/state_db/comparison_repository.py", "src/data_access/postgres_state_db/comparison_repository.py",
        "requirements.txt",
    }
    hit = {c for c in changed if c in forbidden_paths or any(c.startswith(p) for p in forbidden_prefixes)}
    assert not hit, hit
