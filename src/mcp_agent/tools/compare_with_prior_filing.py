"""compare_with_prior_filing (design §6) — a structured diff against the
issuer's already-persisted prior candidates, via the existing pure
prior_disclosure_comparison module. Never a narrative summary. Feeds
publication_policy row 8, which re-validates it. 1 call per session."""
from __future__ import annotations

from src.data_access import backend_factory
from src.logic.prior_disclosure_comparison import build_comparison_result
from src.mcp_agent.contracts import PriorFilingComparison, ToolErrorKind
from src.mcp_agent.tools._common import consume, filing_date, guarded, reject, rule_category
from src.mcp_agent.tools._context import ToolContext, scope_check
from src.models.models import CandidateSignal, CandidateStatus

NAME = "compare_with_prior_filing"


def run(ctx: ToolContext, issuer_id: str, current_document_id: str, matched_rules: list[str] | tuple[str, ...]) -> PriorFilingComparison:
    inputs = {"issuer_id": issuer_id, "current_document_id": current_document_id, "matched_rules": list(matched_rules or ())}
    if (error := consume(ctx, NAME, inputs)) is not None:
        return PriorFilingComparison(error=error)
    if (error := scope_check(ctx, issuer_id)) is not None:
        return PriorFilingComparison(error=reject(ctx, NAME, inputs, error.kind, error.detail))
    filing = ctx.filing_events_by_id.get((current_document_id or "").strip())
    if filing is None:
        return PriorFilingComparison(error=reject(ctx, NAME, inputs, ToolErrorKind.NOT_FOUND, "current_document_id was not returned by search_filing_metadata this session"))
    matched = tuple(r.strip() for r in (matched_rules or ()) if isinstance(r, str) and r.strip())

    candidates, error = guarded(ctx, NAME, inputs, lambda: backend_factory.get_candidate_repository(ctx.settings, filing.source_name).load_candidates())
    if error is not None:
        return PriorFilingComparison(error=error)
    priors = [c for c in candidates.values() if c.filing.corp_code == filing.corp_code and c.filing.rcept_no != filing.rcept_no]
    current = CandidateSignal(id=f"agent-{filing.rcept_no}", filing=filing, matched_rules=list(matched), confidence="Moderate", status=CandidateStatus.CANDIDATE_DETECTED)
    comparison, error = guarded(ctx, NAME, inputs, lambda: build_comparison_result(current, priors, ctx.now_iso()))
    if error is not None:
        return PriorFilingComparison(error=error)

    current_date = filing_date(filing)
    newer = [c for c in priors if current_date is not None and (d := filing_date(c.filing)) is not None and d > current_date]
    categories = {rule_category(r) for r in matched}
    superseding = [c for c in newer if categories & {rule_category(r) for r in c.matched_rules}]
    prior_ids = {c.filing.rcept_no for c in newer}
    if comparison.prior_document_id:
        prior_ids.add(comparison.prior_document_id)
    result = PriorFilingComparison(
        has_newer_filing=bool(newer), has_superseding_disclosure=bool(superseding), prior_document_ids=tuple(sorted(prior_ids)),
        added_categories=tuple(comparison.added_categories), removed_categories=tuple(comparison.removed_categories),
        comparison_status=comparison.comparison_status,
    )
    ctx.last_comparison = result
    ctx.audit("prior_filing_comparison_performed", inputs, f"status={result.comparison_status} newer={result.has_newer_filing} superseding={result.has_superseding_disclosure} priors={len(priors)}", tool_name=NAME)
    return result
