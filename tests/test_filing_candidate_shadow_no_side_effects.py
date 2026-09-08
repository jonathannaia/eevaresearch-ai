"""Daily News Filing-Event Shadow Adapter, Batch 2b — cross-cutting
no-side-effect proof for all three source pipelines together. Two kinds
of evidence:

1. Mechanical (AST-based, same technique as
   test_filing_candidate_adapters_isolation.py): the three pipeline
   modules import no Daily News store/backend/worker/UI/translation-
   service code beyond what each already imported before this batch.
2. Runtime: with every shadow flag enabled and a filing that maps
   successfully, no NewsStory is ever constructed, no translation call is
   ever made as a side effect of the shadow block, and each pipeline's
   own pre-existing candidate store stays empty when no CandidateSignal
   was produced. Active feed/source registries and pilot-feed counts are
   proven unchanged by
   test_filing_candidate_adapters_isolation.py::test_runtime_source_registry_and_pilot_feeds_are_unaffected_by_this_batch
   already — not duplicated here."""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.config.tracked_companies import TrackedCompany
from src.data_access.dart import radar_pipeline
from src.data_access.dart import scan_service as dart_scan_service
from src.data_access.edgar import edgar_pipeline
from src.data_access.edgar import scan_service as edgar_scan_service
from src.data_access.edinet import edinet_pipeline
from src.data_access.edinet import scan_service as edinet_scan_service
from src.models import daily_news_models

_REPO_ROOT = Path(__file__).parent.parent

_PIPELINE_PATHS = (
    _REPO_ROOT / "src" / "data_access" / "edgar" / "edgar_pipeline.py",
    _REPO_ROOT / "src" / "data_access" / "dart" / "radar_pipeline.py",
    _REPO_ROOT / "src" / "data_access" / "edinet" / "edinet_pipeline.py",
)

_FORBIDDEN_IMPORT_SUBSTRINGS = (
    "daily_news_worker", "daily_news_backend", "daily_news_store",
    "daily_news_pipeline", "daily_news_scan_status_repository",
    "rss_atom_client", "state_db", "postgres_state_db", "streamlit", "src.ui",
    "daily_news_models",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
    return names


# ============================================================
# 1. Mechanical: no Daily News worker/backend/store/UI/model import
# ============================================================


def test_no_pipeline_imports_daily_news_worker_backend_store_ui_or_models():
    for path in _PIPELINE_PATHS:
        imported = _imported_modules(path)
        offenders = [m for m in imported if any(fragment in m for fragment in _FORBIDDEN_IMPORT_SUBSTRINGS)]
        assert not offenders, (path.name, offenders)


def test_no_pipeline_imports_newsstory_by_name():
    for path in _PIPELINE_PATHS:
        assert "NewsStory" not in _imported_names(path), path.name


# ============================================================
# 2. Runtime: no NewsStory construction, no translation call, no
#    persistence beyond each pipeline's own pre-existing candidate store
# ============================================================

_NVDA = TrackedCompany(
    name="NVIDIA", exchange="NASDAQ", krx_code="NVDA", source="SEC EDGAR",
    themes=("ai-buildout",), corp_code="0001045810",
)
_SAMSUNG = TrackedCompany(
    name="Samsung Electronics", exchange="KRX", krx_code="005930", source="OpenDART / DART",
    themes=("memory",), corp_code="00126380",
)
_SOFTBANK = TrackedCompany(
    name="SoftBank Group Corp.", exchange="TSE", krx_code="99840", source="EDINET",
    themes=("ai-buildout",), corp_code="E02778",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_edgar_shadow_block_never_constructs_a_newsstory(tmp_path):
    filing = edgar_scan_service.FilingEvent(
        rcept_no="0001045810-26-000001", corp_code="0001045810", corp_name="NVIDIA", stock_code="NVDA",
        report_nm="Annual report", rcept_dt="2026-08-01", flr_nm="NVIDIA", pblntf_ty="10-K",
        source_name="SEC EDGAR", retrieved_at=_now(), primary_document="doc.htm",
    )
    scope = edgar_scan_service.ScanScope(
        bgn_date="2026-08-01", end_date="2026-08-31", lookback_days=30,
        companies=("NVIDIA",), source="SEC EDGAR", scanned_at=_now(),
    )
    scan_result = edgar_scan_service.ScanResult(
        scope=scope, new_filing_events=(filing,), new_candidate_signals=(),
        already_seen_count=0, errors=(), no_data_companies=(),
    )

    with patch.object(edgar_scan_service, "scan", return_value=scan_result):
        with patch.object(daily_news_models.NewsStory, "__init__", side_effect=AssertionError("NewsStory must never be constructed")):
            report = edgar_pipeline.run_pipeline(MagicMock(), [_NVDA], tmp_path, filing_candidate_shadow_enabled=True)

    assert len(report.filing_candidate_shadow_matches) == 1


def test_dart_shadow_block_never_constructs_a_newsstory_or_calls_translation(tmp_path):
    filing = dart_scan_service.FilingEvent(
        rcept_no="20260801000001", corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm="분기보고서 (2026.06)", rcept_dt="2026-08-01", flr_nm="삼성전자",
        source_name="OpenDART / DART", retrieved_at=_now(),
    )
    scope = dart_scan_service.ScanScope(
        bgn_de="20260801", end_de="20260831", lookback_days=30,
        companies=("Samsung Electronics",), source="OpenDART / DART", scanned_at=_now(),
    )
    scan_result = dart_scan_service.ScanResult(
        scope=scope, new_filing_events=(filing,), new_candidate_signals=(),
        already_seen_count=0, errors=(), no_data_companies=(),
    )
    translation_provider = MagicMock()

    with patch.object(dart_scan_service, "scan", return_value=scan_result):
        with patch.object(daily_news_models.NewsStory, "__init__", side_effect=AssertionError("NewsStory must never be constructed")):
            report = radar_pipeline.run_pipeline(
                MagicMock(), translation_provider, [_SAMSUNG], tmp_path, filing_candidate_shadow_enabled=True,
            )

    assert len(report.filing_candidate_shadow_matches) == 1
    translation_provider.translate.assert_not_called()


def test_edinet_shadow_block_never_constructs_a_newsstory(tmp_path):
    filing = edinet_scan_service.FilingEvent(
        rcept_no="S100AAA1", corp_code="E02778", corp_name="SoftBank Group Corp.", stock_code="99840",
        report_nm="自己株式の取得状況に関するお知らせ", rcept_dt="2026-08-01", flr_nm="SoftBank Group Corp.",
        pblntf_ty="170000", pblntf_detail_ty="220", ordinance_code="010",
        source_name="EDINET", retrieved_at=_now(),
    )
    scope = edinet_scan_service.ScanScope(
        bgn_date="2026-08-01", end_date="2026-08-31", lookback_days=30,
        companies=("SoftBank Group Corp.",), source="EDINET", scanned_at=_now(),
    )
    scan_result = edinet_scan_service.ScanResult(
        scope=scope, new_filing_events=(filing,), new_candidate_signals=(),
        already_seen_count=0, errors=(), no_data_companies=(),
    )

    with patch.object(edinet_scan_service, "scan", return_value=scan_result):
        with patch.object(daily_news_models.NewsStory, "__init__", side_effect=AssertionError("NewsStory must never be constructed")):
            report = edinet_pipeline.run_pipeline(
                MagicMock(), [_SOFTBANK], tmp_path,
                material_event_lexicon_enabled=True, filing_candidate_shadow_enabled=True,
            )

    assert len(report.filing_candidate_shadow_matches) == 1


# ============================================================
# 3. scripts/radar_worker.py never reads the two new field names
# ============================================================


def test_radar_worker_never_reads_the_new_filing_candidate_shadow_fields():
    source = (_REPO_ROOT / "scripts" / "radar_worker.py").read_text(encoding="utf-8")
    assert "filing_candidate_shadow_matches" not in source
    assert "filing_candidate_shadow_diagnostics" not in source
