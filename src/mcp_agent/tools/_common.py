"""Helpers shared by the ten tools: budget/audit plumbing, the guarded
real-call wrapper (design §11 — a tool never raises into the model), the
per-source dispatch table (rules, lookback clamp, cache-first excerpt
fetch, native-id match), and evidence registration.

The dispatch table is the single place the tools touch the three
adapters, and it touches only their READ paths: the *_rules evaluators,
scan_service.clamp_lookback_days, and document_service.get_or_fetch_
excerpt — which serves from the on-disk excerpt cache first and performs
at most one bounded fetch on a miss, exactly as a human-triggered page
view would. Never the pipelines' process_candidate/run_pipeline.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from src.data_access.dart import dart_rules
from src.data_access.dart import document_service as dart_documents
from src.data_access.dart import scan_service as dart_scan
from src.data_access.dart.low_value_filing_rules import is_low_value_filing_title, matched_rules_are_low_value_only
from src.data_access.edgar import document_service as edgar_documents
from src.data_access.edgar import edgar_rules
from src.data_access.edgar import scan_service as edgar_scan
from src.data_access.edinet import document_service as edinet_documents
from src.data_access.edinet import edinet_rules
from src.data_access.edinet import scan_service as edinet_scan
from src.mcp_agent.contracts import (
    SOURCE_NAME_TO_TIER,
    EvidenceRecord,
    FreshnessStatus,
    Suppression,
    ToolError,
    ToolErrorKind,
    mint_evidence_id,
)
from src.mcp_agent.tools._context import ToolContext
from src.mcp_agent.tools._untrusted import scrub_untrusted_text
from src.models.models import EvidenceLocation, ExtractionState, FilingEvent


@dataclass(frozen=True)
class FetchedExcerpt:
    state: ExtractionState
    text: str
    detail: str
    retrieved_at: str
    location_section: str | None = None
    source_member: str | None = None


def _fetch_dart(ctx: ToolContext, filing: FilingEvent) -> FetchedExcerpt:
    r = dart_documents.get_or_fetch_excerpt(ctx.clients.get(filing.source_name), filing.rcept_no, ctx.settings.cache_dir)
    return FetchedExcerpt(r.state, r.excerpt_original or "", r.detail, r.retrieved_at)


def _fetch_edgar(ctx: ToolContext, filing: FilingEvent) -> FetchedExcerpt:
    r = edgar_documents.get_or_fetch_excerpt(
        ctx.clients.get(filing.source_name), filing.corp_code, filing.rcept_no, filing.primary_document, ctx.settings.cache_dir,
    )
    return FetchedExcerpt(r.state, r.excerpt_original or "", r.detail, r.retrieved_at, location_section=r.location_section)


def _fetch_edinet(ctx: ToolContext, filing: FilingEvent) -> FetchedExcerpt:
    r = edinet_documents.get_or_fetch_excerpt(ctx.clients.get(filing.source_name), filing.rcept_no, ctx.settings.cache_dir)
    return FetchedExcerpt(r.state, r.excerpt_original or "", r.detail, r.retrieved_at, source_member=r.evidence_source_member)


def _evaluate_dart(filing: FilingEvent):
    return dart_rules.evaluate_report_name(filing.report_nm)


def _evaluate_edgar(filing: FilingEvent):
    return edgar_rules.evaluate_form_type(filing.pblntf_ty)


def _evaluate_edinet(filing: FilingEvent):
    return edinet_rules.evaluate_document(filing.ordinance_code, filing.pblntf_ty, filing.pblntf_detail_ty)


def _cik_matches(filing: FilingEvent, native_id: str) -> bool:
    try:
        return int(filing.corp_code) == int(native_id)
    except (TypeError, ValueError):
        return False


def _exact_matches(filing: FilingEvent, native_id: str) -> bool:
    return filing.corp_code == native_id


@dataclass(frozen=True)
class SourceAdapter:
    id_pattern: re.Pattern[str]
    evaluate: Callable[[FilingEvent], Any]
    clamp_lookback: Callable[[int], int]
    fetch: Callable[[ToolContext, FilingEvent], FetchedExcerpt]
    native_id_matches: Callable[[FilingEvent, str], bool]


SOURCE_ADAPTERS: dict[str, SourceAdapter] = {
    "OpenDART / DART": SourceAdapter(re.compile(r"^\d{8}$"), _evaluate_dart, dart_scan.clamp_lookback_days, _fetch_dart, _exact_matches),
    "SEC EDGAR": SourceAdapter(re.compile(r"^\d{1,10}$"), _evaluate_edgar, edgar_scan.clamp_lookback_days, _fetch_edgar, _cik_matches),
    "EDINET": SourceAdapter(re.compile(r"^E\d{5}$"), _evaluate_edinet, edinet_scan.clamp_lookback_days, _fetch_edinet, _exact_matches),
}


# --- budget / audit plumbing ------------------------------------------------

def consume(ctx: ToolContext, name: str, inputs: Any, chars: int = 0) -> ToolError | None:
    error = ctx.budget.consume(name, chars)
    if error is not None:
        ctx.audit(f"{name}_rejected", inputs, f"{error.kind.value}: {error.detail}", tool_name=name)
    return error


def reject(ctx: ToolContext, name: str, inputs: Any, kind: ToolErrorKind, detail: str) -> ToolError:
    error = ToolError(kind, detail)
    ctx.audit(f"{name}_rejected", inputs, f"{kind.value}: {detail}", tool_name=name)
    return error


def guarded(ctx: ToolContext, name: str, inputs: Any, fn: Callable[[], Any]) -> tuple[Any, ToolError | None]:
    """Runs a real service call. Any exception becomes a typed
    RETRIEVAL_FAILED result and marks the session, so publication_policy
    row 2 routes to FAILED_RETRIEVAL — never a stack trace to the model."""
    try:
        return fn(), None
    except Exception as exc:  # noqa: BLE001 — deliberate fail-closed boundary
        ctx.retrieval_error = True
        return None, reject(ctx, name, inputs, ToolErrorKind.RETRIEVAL_FAILED, f"{type(exc).__name__}: {exc}")


# --- filing helpers -----------------------------------------------------------

def filing_date(filing: FilingEvent) -> date | None:
    raw = (filing.filed_at or filing.rcept_dt or "").strip()
    for candidate, fmt in ((raw[:10], "%Y-%m-%d"), (raw[:8], "%Y%m%d")):
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    return None


def rule_category(rule: str) -> str:
    return rule.split(":", 1)[0].strip()


def suppression_for(source_name: str, filing: FilingEvent, matched_rules: tuple[str, ...]) -> tuple[Suppression, str]:
    """design §2.5 — the same deterministic DART low-value gates
    radar_pipeline applies, called directly. Other sources have no
    low-value lexicon today, so they are never suppressed here."""
    if source_name != "OpenDART / DART":
        return Suppression.NONE, ""
    if is_low_value_filing_title(filing.report_nm):
        return Suppression.NOT_MATERIAL, "low_value_filing_title"
    if matched_rules and matched_rules_are_low_value_only(matched_rules):
        return Suppression.NOT_MATERIAL, "matched_rules_low_value_only"
    return Suppression.NONE, ""


def require_document(ctx: ToolContext, name: str, inputs: Any, document_id: str) -> tuple[FilingEvent | None, ToolError | None]:
    """Server-side foreign-key check (design §6): the id must have been
    returned by search_filing_metadata THIS session, and a suppressed
    filing never reaches excerpt release even if asked for directly."""
    filing = ctx.filing_events_by_id.get((document_id or "").strip())
    if filing is None:
        return None, reject(ctx, name, inputs, ToolErrorKind.NOT_FOUND, "document_id was not returned by search_filing_metadata this session")
    row = ctx.metadata_rows_by_id.get(document_id)
    if row is not None and row.suppression is not Suppression.NONE:
        return None, reject(ctx, name, inputs, ToolErrorKind.SUPPRESSED, f"filing suppressed: {row.suppression.value}:{row.suppression_detail}")
    return filing, None


def register_evidence(
    ctx: ToolContext, filing: FilingEvent, text: str, locator: str, location: EvidenceLocation | None, retrieved_at: str,
) -> tuple[EvidenceRecord, str, tuple[str, ...]]:
    clean, flags = scrub_untrusted_text(text)
    evidence_id = mint_evidence_id(ctx.scope.session_id, filing.rcept_no, locator, clean)
    record = EvidenceRecord(
        evidence_id=evidence_id, session_id=ctx.scope.session_id, issuer_id=ctx.scope.issuer_id,
        source_tier=SOURCE_NAME_TO_TIER[filing.source_name], source_name=filing.source_name, source_url=filing.source_url,
        source_document_id=filing.rcept_no, source_date=filing.rcept_dt, excerpt_or_locator=locator,
        excerpt_sha256=hashlib.sha256(clean.encode("utf-8")).hexdigest(), retrieved_at=retrieved_at, confidence="high",
        freshness_status=FreshnessStatus.CURRENT, evidence_location=location,
    )
    ctx.evidence[evidence_id] = record
    ctx.evidence_text[evidence_id] = clean
    return record, clean, flags
