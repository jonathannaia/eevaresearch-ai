"""Isolated, read-only EDGAR issuer-discovery preview harness (Phase B —
design/DECISIONS.md). Structurally separate from the tracked pipeline
(`scan_service.py`/`edgar_pipeline.py`): this module never imports
`document_service`, `document_extractor`, `candidate_store`,
`signal_promotion`, or `review_actions`, never resolves/retrieves a
filing document, never translates anything, and never writes to
`edgar_filing_events.json`, `edgar_candidates.json`,
`edgar_document_excerpts.json`, `tracked_companies.py`, or the Issuer
registry (`SEED_ISSUERS`/`DISCOVERY_STUBS`) — it only reads `SEED_ISSUERS`,
to build the active-coverage CIK exclusion set below.

**No real cross-issuer SEC endpoint is chosen or implemented here.**
`EdgarClient.get_submissions(cik)` — the tracked pipeline's only filing-
metadata method — requires an already-known CIK, so it structurally
cannot discover a company EevaResearch has never tracked. A genuine
discovery feed needs a different SEC mechanism entirely (SEC's full-text
search API or the EDGAR daily-index files are the two candidates
identified during this phase's design pass — neither has been read from
live documentation or verified against a real pull this session). Rather
than guess that shape, this module defines its own normalized row
contract (see `DiscoveryFeedBatch`) and depends on it through the
`EdgarDiscoveryFeedClient` Protocol below — a future, separately-approved
adapter implements that Protocol against whichever real endpoint a Gate-1
verification pass confirms; this module's own logic (exclusion, rule
matching, grouping, budgets, persistence) is fully correct and fully
testable today, independent of that still-open choice.

**Known limitation, flagged rather than silently worked around**: the
active-coverage CIK exclusion set below is built only from
`SEED_ISSUERS[i].identifiers["SEC EDGAR"]` — the Issuer Registry's own,
already-approved boundary for this module. As of Phase A, none of the 22
real SEC EDGAR seed issuers carry a populated `"SEC EDGAR"` identifier in
that static collection (each company's CIK is resolved lazily into
`data/cache/edgar_ciks.json` at runtime — see `tracked_companies.py`'s own
module docstring — never stored in the static tuple `SEED_ISSUERS` is
generated from). This function's exclusion logic is correct for whatever
`seed_issuers` it's actually given (proven by this module's own tests,
using synthetic Issuer records with populated identifiers) — but with
today's real registry data, the production exclusion set would be empty.
This is a real, load-bearing gap that must be closed before any live
discovery run is approved, independent of the endpoint-choice gate above.

Every proposal this module can ever produce is a `Proposed`,
never-verified, never-scan-eligible record — see `IssuerDiscoveryProposal`
and the two fixed status strings below. Nothing here can reach a
`CandidateSignal`, the Issuer registry, or Signal eligibility."""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from src.config.issuer_registry import SEED_ISSUERS
from src.data_access.edgar import discovery_rules, edgar_rules
from src.data_access.edgar.client import EdgarClient, normalize_cik
from src.models.issuer import Issuer

# --- Budgets (design/DECISIONS.md — Phase B) ---------------------------

MAX_EDGAR_DISCOVERY_METADATA_REQUESTS = 25
MAX_EDGAR_DISCOVERY_ROWS = 500
MAX_EDGAR_DISCOVERY_PROPOSALS = 10
DEFAULT_EDGAR_DISCOVERY_LOOKBACK_DAYS = 1

# --- Fixed, exact wording — every proposal carries these verbatim ------

VERIFICATION_STATUS = (
    "Unverified — no source identifier independently confirmed and no "
    "filing document retrieved; based on filing-list metadata only."
)
EXCLUDED_FROM_COVERAGE_REASON = (
    "Not active tracked coverage — this is a discovery proposal only, not "
    "scan-eligible, and requires human review before any registry change."
)
DISCOVERY_STATUS_PROPOSED = "Proposed"

_CACHE_FILENAME = "edgar_discovery_proposals.json"


# --- Proposal model ------------------------------------------------------

@dataclass(frozen=True)
class MatchedFiling:
    """One filing contributing evidence to a proposal. Every field is
    either a literal value copied from the feed row or a pure
    URL/formatting derivation (`source_url`) — nothing here is inferred."""

    accession_no: str
    form: str
    filing_date: str
    primary_document: str  # "" if the row didn't supply one
    source_url: str
    matched_rules: tuple[str, ...]  # full edgar_rules.RuleEvaluation.matched_rules, not truncated to one
    items_raw: str  # the row's own `items` value, verbatim — "" if absent


@dataclass(frozen=True)
class IssuerDiscoveryProposal:
    """A standalone preview record — deliberately NOT a CandidateSignal
    and NOT an Issuer, and never written into the Issuer registry. See
    module docstring."""

    proposal_id: str  # f"edgar-discovery:{cik}"
    cik: str  # normalized 10-digit form (see client.normalize_cik)
    issuer_display_name: str | None  # only from feed metadata — never fetched/inferred
    ticker: str | None  # only from feed metadata — never fetched/inferred
    matched_filings: tuple[MatchedFiling, ...]
    candidate_theme: str | None  # always None in Phase B — see module docstring
    candidate_layer: str | None  # always None in Phase B
    confidence: str  # "Moderate" or "High" — rule-match confidence only, see _combine_confidence
    verification_status: str
    excluded_from_coverage_reason: str
    discovery_status: str  # always DISCOVERY_STATUS_PROPOSED in this phase
    run_id: str  # the run that first created this proposal (unchanged by later merges)
    generated_at: str  # ISO 8601 — when first created (unchanged by later merges)
    dedup_key: str  # == cik in this phase


# --- Injectable feed seam — no real implementation in this phase -------

@dataclass(frozen=True)
class DiscoveryFeedBatch:
    """What an injected feed client hands back for one bounded pull.
    `rows` are normalized dicts, NOT tied to any specific real SEC
    endpoint's response shape — each row is expected to carry at minimum
    "cik", "accessionNumber", "filingDate", "form", and optionally
    "items", "companyName", "ticker", "primaryDocument". `requests_made`
    is the feed client's own declared count of HTTP requests it made to
    produce this batch — this module makes none itself and only enforces
    the budget against what the client reports."""

    rows: tuple[dict, ...]
    requests_made: int


class EdgarDiscoveryFeedClient(Protocol):
    """The one seam a future, separately-approved cross-issuer SEC feed
    adapter would implement — no real implementation exists in this
    phase (see module docstring). Tests satisfy this with a plain fake
    object; nothing here requires a mocking framework."""

    def fetch_recent_filing_rows(self, lookback_days: int, max_rows: int) -> DiscoveryFeedBatch: ...


@dataclass(frozen=True)
class DiscoveryRunResult:
    ran: bool
    reason: str  # empty when ran is True; explains why otherwise
    run_id: str | None
    new_proposals: tuple[IssuerDiscoveryProposal, ...]
    updated_proposal_ciks: tuple[str, ...]
    rows_examined: int
    requests_made: int
    already_seen_filing_count: int


# --- Cache I/O — same load-whole/merge/save-whole pattern as every other
# scan cache in this codebase (scan_service.py, discovery_service.py for
# EDINET) --------------------------------------------------------------

def _cache_path(cache_dir: Path) -> Path:
    return cache_dir / _CACHE_FILENAME


def _empty_cache() -> dict:
    return {"seen_filing_keys": [], "proposals": {}, "runs": []}


def _load_cache(cache_dir: Path) -> dict:
    path = _cache_path(cache_dir)
    if not path.exists():
        return _empty_cache()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_cache()
    if not isinstance(raw, dict):
        return _empty_cache()
    for key, default in _empty_cache().items():
        raw.setdefault(key, default)
    return raw


def _save_cache(cache_dir: Path, cache: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    _cache_path(cache_dir).write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _matched_filing_from_dict(data: dict) -> MatchedFiling:
    return MatchedFiling(
        accession_no=data["accession_no"], form=data["form"], filing_date=data["filing_date"],
        primary_document=data["primary_document"], source_url=data["source_url"],
        matched_rules=tuple(data["matched_rules"]), items_raw=data["items_raw"],
    )


def load_discovery_proposals(cache_dir: Path) -> tuple[IssuerDiscoveryProposal, ...]:
    """Read-only — never triggers a run, never calls a feed client. A
    malformed stored record is skipped rather than raised, matching this
    codebase's existing `load_candidates`/`load_discoveries` convention."""
    proposals: list[IssuerDiscoveryProposal] = []
    for data in _load_cache(cache_dir)["proposals"].values():
        try:
            filings = tuple(_matched_filing_from_dict(f) for f in data["matched_filings"])
            proposals.append(IssuerDiscoveryProposal(**{**data, "matched_filings": filings}))
        except (TypeError, KeyError):
            continue
    return tuple(proposals)


def _clean_optional(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _confidence_from_matched_rules(matched_rules: list[str]) -> str:
    """Same distinct-category-count rule `edgar_rules._confidence_for`
    uses for one filing, applied here across every matched_rules entry
    from every filing accumulated onto a proposal so far (existing plus
    this run's new ones) — two different single-category filings for the
    same new issuer should read as "High" exactly like one filing citing
    two items already does in the tracked pipeline; per-filing confidence
    alone would understate that combined evidence."""
    distinct_categories = len({rule.split(":", 1)[0] for rule in matched_rules})
    return "High" if distinct_categories >= 2 else "Moderate"


def _edgar_seed_cik_exclusion_set(seed_issuers: tuple[Issuer, ...]) -> frozenset[str]:
    """See module docstring's "Known limitation" note — correct for
    whatever `seed_issuers` is given, empty against today's real
    SEED_ISSUERS data."""
    return frozenset(
        issuer.identifiers["SEC EDGAR"] for issuer in seed_issuers if "SEC EDGAR" in issuer.identifiers
    )


def run_discovery(
    feed_client: EdgarDiscoveryFeedClient,
    cache_dir: Path,
    *,
    discovery_enabled: bool,
    seed_issuers: tuple[Issuer, ...] = SEED_ISSUERS,
    lookback_days: int = DEFAULT_EDGAR_DISCOVERY_LOOKBACK_DAYS,
) -> DiscoveryRunResult:
    """One bounded discovery pass. Disabled (`discovery_enabled=False`,
    the default posture — see `Settings.edgar_discovery_enabled`) fails
    closed before `feed_client` is ever called. A `requests_made` count
    over `MAX_EDGAR_DISCOVERY_METADATA_REQUESTS` also fails the whole run
    closed (nothing written) — enforced against what the client itself
    reports, since no real request-counting happens in this module.

    Rows are grouped by (normalized) CIK; a CIK already excluded (an
    active seed issuer) or already a stored proposal from a prior run may
    still gain new `matched_filings` this run, uncapped by
    `MAX_EDGAR_DISCOVERY_PROPOSALS` — that cap bounds only how many
    *new* CIKs (never seen before, across all runs) this one run may
    turn into a proposal; the rest are left unmarked-as-seen and are
    reconsidered on a future run rather than lost."""
    if not discovery_enabled:
        return DiscoveryRunResult(
            ran=False, reason="EDGE_EDGAR_DISCOVERY_ENABLED is not enabled.",
            run_id=None, new_proposals=(), updated_proposal_ciks=(),
            rows_examined=0, requests_made=0, already_seen_filing_count=0,
        )

    run_id = f"edgar-discovery-{uuid.uuid4().hex[:12]}"
    generated_at = datetime.now(timezone.utc).isoformat()

    batch = feed_client.fetch_recent_filing_rows(lookback_days, MAX_EDGAR_DISCOVERY_ROWS)

    if batch.requests_made > MAX_EDGAR_DISCOVERY_METADATA_REQUESTS:
        return DiscoveryRunResult(
            ran=False,
            reason=(
                f"Feed client reported {batch.requests_made} request(s), exceeding the configured "
                f"budget of {MAX_EDGAR_DISCOVERY_METADATA_REQUESTS} — run aborted, nothing written."
            ),
            run_id=run_id, new_proposals=(), updated_proposal_ciks=(),
            rows_examined=0, requests_made=batch.requests_made, already_seen_filing_count=0,
        )

    rows = batch.rows[:MAX_EDGAR_DISCOVERY_ROWS]
    excluded_ciks = _edgar_seed_cik_exclusion_set(seed_issuers)

    cache = _load_cache(cache_dir)
    seen_filing_keys: set[str] = set(cache["seen_filing_keys"])
    stored_proposals: dict[str, dict] = cache["proposals"]

    grouped: dict[str, dict[str, tuple[dict, edgar_rules.RuleEvaluation]]] = {}
    already_seen_filing_count = 0

    for row in rows:
        raw_cik = str(row.get("cik") or "").strip()
        accession_no = str(row.get("accessionNumber") or "").strip()
        if not raw_cik or not accession_no:
            continue
        cik = normalize_cik(raw_cik)
        if cik in excluded_ciks:
            continue
        evaluation = discovery_rules.evaluate_discovery_row(row.get("form", ""), row.get("items"))
        if evaluation is None:
            continue
        filing_key = f"{cik}:{accession_no}"
        if filing_key in seen_filing_keys:
            already_seen_filing_count += 1
            continue
        grouped.setdefault(cik, {})[accession_no] = (row, evaluation)

    new_ciks_in_order = [cik for cik in grouped if cik not in stored_proposals]
    allowed_new_ciks = set(new_ciks_in_order[:MAX_EDGAR_DISCOVERY_PROPOSALS])

    new_proposals: list[IssuerDiscoveryProposal] = []
    updated_ciks: list[str] = []

    for cik, matches_by_accession in grouped.items():
        is_new = cik not in stored_proposals
        if is_new and cik not in allowed_new_ciks:
            continue  # over this run's new-proposal cap — left unmarked-as-seen, reconsidered next run

        matches = list(matches_by_accession.items())
        appended_filings: list[MatchedFiling] = []
        for accession_no, (row, evaluation) in matches:
            seen_filing_keys.add(f"{cik}:{accession_no}")
            appended_filings.append(MatchedFiling(
                accession_no=accession_no,
                form=str(row.get("form") or ""),
                filing_date=str(row.get("filingDate") or ""),
                primary_document=str(row.get("primaryDocument") or ""),
                source_url=EdgarClient.filing_index_url(cik, accession_no),
                matched_rules=evaluation.matched_rules,
                items_raw=str(row.get("items") or ""),
            ))

        if is_new:
            first_row = matches[0][1][0]
            all_matched_rules = [rule for f in appended_filings for rule in f.matched_rules]
            proposal = IssuerDiscoveryProposal(
                proposal_id=f"edgar-discovery:{cik}",
                cik=cik,
                issuer_display_name=_clean_optional(first_row.get("companyName")),
                ticker=_clean_optional(first_row.get("ticker")),
                matched_filings=tuple(appended_filings),
                candidate_theme=None,
                candidate_layer=None,
                confidence=_confidence_from_matched_rules(all_matched_rules),
                verification_status=VERIFICATION_STATUS,
                excluded_from_coverage_reason=EXCLUDED_FROM_COVERAGE_REASON,
                discovery_status=DISCOVERY_STATUS_PROPOSED,
                run_id=run_id,
                generated_at=generated_at,
                dedup_key=cik,
            )
            stored_proposals[cik] = asdict(proposal)
            new_proposals.append(proposal)
        else:
            existing = stored_proposals[cik]
            existing_filings = [_matched_filing_from_dict(f) for f in existing["matched_filings"]]
            merged_filings = existing_filings + appended_filings
            all_matched_rules = [rule for f in merged_filings for rule in f.matched_rules]
            existing["matched_filings"] = [asdict(f) for f in merged_filings]
            existing["confidence"] = _confidence_from_matched_rules(all_matched_rules)
            updated_ciks.append(cik)

    cache["seen_filing_keys"] = sorted(seen_filing_keys)
    cache["proposals"] = stored_proposals
    cache["runs"] = cache["runs"] + [{
        "run_id": run_id, "generated_at": generated_at, "requests_made": batch.requests_made,
        "rows_examined": len(rows), "new_proposal_count": len(new_proposals),
        "updated_proposal_count": len(updated_ciks), "budget_exceeded_reason": None,
    }]
    _save_cache(cache_dir, cache)

    return DiscoveryRunResult(
        ran=True, reason="", run_id=run_id,
        new_proposals=tuple(new_proposals), updated_proposal_ciks=tuple(updated_ciks),
        rows_examined=len(rows), requests_made=batch.requests_made,
        already_seen_filing_count=already_seen_filing_count,
    )
