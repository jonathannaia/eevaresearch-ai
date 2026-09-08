"""Company Discovery Phase 2 — orchestration entry point. Reads only
already-persisted FilingEvent, CandidateSignal, and NewsStory records
(via the existing repository factories — `backend_factory`/
`daily_news_backend`), applies deterministic extraction + resolution +
scoring, and persists Candidate evidence. Never fetches anything from
the network, never calls a translation provider, never writes to
`TrackedCompany`/`SEED_ISSUERS`/`DISCOVERY_STUBS`/any Radar/Filings/
Daily News table. Mirrors `daily_news_pipeline.py`'s role as "the one
place a worker's tick logic lives," but for company discovery.

Rolling ingestion-overlap window (approved correctness requirement):
every tick reprocesses every source record whose own `retrieved_at` is
within `_INGESTION_OVERLAP_HOURS` of `now` — not "strictly after the
worker's last tick." This is deliberate: a strict watermark can
permanently skip a record whose `retrieved_at` doesn't monotonically
increase relative to prior ticks (a backfill/correction touching an
older row, a clock skew between processes, or any other timestamp
anomaly), since a "greater than last watermark" filter, once passed
over, never looks at that record again. A generously-sized rolling
window — deliberately larger than the tick interval, so a missed or
delayed tick never creates a permanent gap — reprocesses recently-
ingested records every tick regardless of exact ordering, and the
`UNIQUE(dedup_key)` constraint on `candidate_evidence` (see
entity_resolution.generate_evidence_dedup_key) makes that reprocessing
safe: an already-recorded piece of evidence is silently skipped, never
duplicated. Older history is the one-shot backfill script's job, not
this recurring window's.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.config.issuer_registry import DISCOVERY_STUBS
from src.config.settings import Settings
from src.config.tracked_companies import get_tracked_companies
from src.data_access import backend_factory
from src.data_access.company_discovery import extraction_rules
from src.data_access.company_discovery.company_discovery_backend import CandidateIssuerRepositoryProtocol
from src.data_access.company_discovery.entity_resolution import (
    ResolutionOutcome,
    canonical_daily_news_record_id,
    canonical_filing_record_id,
    generate_evidence_dedup_key,
    generate_issuer_id,
    normalize_entity_name,
    resolve_mention,
)
from src.data_access.company_discovery.scoring import ScoreInputs, compute_composite_score
from src.data_access.daily_news import daily_news_backend
from src.models.company_discovery_models import (
    CandidateEvidence,
    CandidateScoreSnapshot,
    CandidateStateTransition,
    RelationshipType,
    ResolutionConfidence,
    SourceType,
)
from src.models.issuer import CoverageState

# Deliberately larger than any reasonable tick interval (approved
# default EDGE_COMPANY_DISCOVERY_SCAN_INTERVAL_MINUTES=240, i.e. 4
# hours) so a missed or delayed tick can never create a permanent gap
# in coverage — see module docstring.
_INGESTION_OVERLAP_HOURS = 72

_FILING_SOURCES: tuple[str, ...] = ("OpenDART / DART", "SEC EDGAR", "EDINET")


@dataclass(frozen=True)
class SourceRecordText:
    """One already-persisted record's extraction-ready text, reduced to
    exactly what candidate_pipeline needs — never anything richer than
    what the existing FilingEvent/CandidateSignal/NewsStory records
    already contain."""

    source_type: SourceType
    source_name: str
    source_record_id: str
    source_url: str
    text: str
    retrieved_at: str | None
    published_at: str | None


@dataclass(frozen=True)
class CandidateDiscoveryTickReport:
    evidence_created: int
    candidates_created: int
    candidates_quarantined: int
    candidates_rejected: int
    candidates_archived: int


def _within_overlap_window(retrieved_at: str | None, now: datetime, overlap_hours: int) -> bool:
    if not retrieved_at:
        return False
    try:
        dt = datetime.fromisoformat(retrieved_at)
    except (ValueError, TypeError):
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt) <= timedelta(hours=overlap_hours)


def _load_filing_texts(
    worker_settings: Settings, now: datetime, *, overlap_hours: int, max_records: int | None = None,
) -> list[SourceRecordText]:
    """Reads existing, already-persisted FilingEvent/CandidateSignal
    records only — no fetch of any kind. `report_nm` (title) is always
    included; `excerpt_original` from the matching CandidateSignal (by
    rcept_no), when one exists, is appended for richer extraction text.
    `max_records`, when given, bounds the total returned (used only by
    the one-shot backfill script — the recurring worker's own rolling
    overlap window is already naturally bounded)."""
    records: list[SourceRecordText] = []
    for source in _FILING_SOURCES:
        try:
            filings = backend_factory.get_filing_event_repository(worker_settings, source).load_filing_events()
            candidates = backend_factory.get_candidate_repository(worker_settings, source).load_candidates()
        except Exception:  # noqa: BLE001 — one source's read failure must never stop the others
            continue
        excerpt_by_rcept_no = {c.filing.rcept_no: c.excerpt_original for c in candidates.values() if c.excerpt_original}
        for filing in filings:
            if not _within_overlap_window(filing.retrieved_at, now, overlap_hours):
                continue
            excerpt = excerpt_by_rcept_no.get(filing.rcept_no, "")
            text = f"{filing.report_nm}. {excerpt}".strip()
            records.append(SourceRecordText(
                source_type=SourceType.FILING, source_name=filing.source_name or source,
                source_record_id=canonical_filing_record_id(filing.source_name or source, filing.rcept_no),
                source_url=filing.source_url, text=text,
                retrieved_at=filing.retrieved_at, published_at=filing.rcept_dt or filing.filed_at,
            ))
            if max_records is not None and len(records) >= max_records:
                return records
    return records


def _load_daily_news_texts(
    worker_settings: Settings, now: datetime, *, overlap_hours: int, max_records: int | None = None,
) -> list[SourceRecordText]:
    try:
        stories = daily_news_backend.get_daily_news_repository(worker_settings).load_stories()
    except Exception:  # noqa: BLE001 — never let a Daily News read failure stop filing processing
        return []
    records: list[SourceRecordText] = []
    for story in stories.values():
        if not story.sources:
            continue
        source = story.sources[0]
        if not _within_overlap_window(source.retrieved_at, now, overlap_hours):
            continue
        text = f"{story.headline}. {story.eeva_summary or ''}".strip()
        records.append(SourceRecordText(
            source_type=SourceType.DAILY_NEWS, source_name=source.publisher or story.company_name,
            source_record_id=canonical_daily_news_record_id(story.id),
            source_url=source.url, text=text,
            retrieved_at=source.retrieved_at, published_at=source.published_at,
        ))
        if max_records is not None and len(records) >= max_records:
            return records
    return records


def _core_names() -> frozenset[str]:
    names: set[str] = set()
    for company in get_tracked_companies(active_only=False):
        names.add(normalize_entity_name(company.name))
        if company.native_name:
            names.add(normalize_entity_name(company.native_name))
    return frozenset(names)


def _core_issuer_id_for_name(normalized_name: str) -> str | None:
    """Best-effort reverse lookup so evidence can record
    related_core_issuer_id — reuses issuer_registry's own SEED_ISSUERS,
    read-only, never written."""
    from src.config.issuer_registry import SEED_ISSUERS

    for issuer in SEED_ISSUERS:
        if normalize_entity_name(issuer.legal_name) == normalized_name:
            return issuer.issuer_id
        if issuer.native_name and normalize_entity_name(issuer.native_name) == normalized_name:
            return issuer.issuer_id
    return None


def _stub_names() -> frozenset[str]:
    names: set[str] = set()
    for stub in DISCOVERY_STUBS:
        names.add(normalize_entity_name(stub.legal_name))
        for alias in stub.aliases:
            names.add(normalize_entity_name(alias))
    return frozenset(names)


def _rescore_candidate(
    repository: CandidateIssuerRepositoryProtocol, issuer_id: str, now_iso: str,
) -> None:
    record = repository.get_candidate(issuer_id)
    if record is None or record.issuer.coverage_state in (CoverageState.QUARANTINED, CoverageState.REJECTED):
        return  # never scored — see scoring.py's own docstring
    evidence_rows = repository.get_evidence_for_issuer(issuer_id)
    if not evidence_rows:
        return
    relationship_types = tuple(RelationshipType(row["relationship_type"]) for row in evidence_rows)
    source_types = tuple(SourceType(row["source_type"]) for row in evidence_rows)
    distinct_sources = len({row["source_name"] for row in evidence_rows})
    theme_or_layer_present = any(row["theme_slug"] or row["supply_chain_layer"] for row in evidence_rows)
    has_core_relationship = any(row["related_core_issuer_id"] for row in evidence_rows)
    most_recent = max((row["extraction_timestamp"] for row in evidence_rows), default=None)
    composite, breakdown = compute_composite_score(ScoreInputs(
        relationship_types=relationship_types, theme_or_layer_present=theme_or_layer_present,
        has_core_relationship=has_core_relationship, source_types=source_types,
        distinct_source_count=distinct_sources, most_recent_evidence_at=most_recent,
        resolution_confidence=record.resolution_confidence,
    ))
    repository.record_score(CandidateScoreSnapshot(
        issuer_id=issuer_id, computed_at=now_iso, composite_score=composite,
        evidence_count=len(evidence_rows), independent_source_count=distinct_sources, score_breakdown=breakdown,
    ))


def run_candidate_discovery_tick(
    worker_settings: Settings, repository: CandidateIssuerRepositoryProtocol,
    *, stale_days: int, now: datetime | None = None,
    overlap_hours: int = _INGESTION_OVERLAP_HOURS,
    max_filing_records: int | None = None, max_daily_news_records: int | None = None,
    run_decay_pass: bool = True,
) -> CandidateDiscoveryTickReport:
    """Runs exactly one tick: reads existing Filing/Daily News data
    within the rolling overlap window (or a wider one, plus record caps
    — the one-shot backfill script's own use, see scripts/backfill_
    company_discovery.py), extracts + resolves + persists evidence,
    rescoring every touched candidate, then runs the staleness/decay
    pass (skippable — the backfill script never runs it, since a fresh
    import should never immediately archive what it just found). Never
    loops, never sleeps — the only function tests should call directly."""
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat()

    core_names = _core_names()
    stub_names = _stub_names()
    known_aliases = dict(repository.get_aliases())

    records = (
        _load_filing_texts(worker_settings, now, overlap_hours=overlap_hours, max_records=max_filing_records)
        + _load_daily_news_texts(worker_settings, now, overlap_hours=overlap_hours, max_records=max_daily_news_records)
    )

    evidence_created = 0
    candidates_created = 0
    candidates_quarantined = 0
    candidates_rejected = 0
    touched_issuer_ids: set[str] = set()

    for record in records:
        matches = extraction_rules.extract_all_matches(record.text)
        for match in matches:
            result = resolve_mention(
                match.org_text, core_names=core_names, stub_names=stub_names, known_aliases=known_aliases,
            )

            if result.outcome in (ResolutionOutcome.MATCHED_CORE, ResolutionOutcome.MATCHED_STUB):
                continue  # never creates or touches a Candidate row

            related_core_issuer_id = None
            # A relationship match's own snippet often names a Core
            # company alongside the candidate mention — best-effort,
            # read-only lookup against the same normalized Core name set.
            for core_name in core_names:
                if core_name and core_name in record.text.lower():
                    related_core_issuer_id = _core_issuer_id_for_name(core_name)
                    break

            if result.outcome == ResolutionOutcome.NEW_REJECTED:
                issuer_id = generate_issuer_id(match.org_text, "Unconfirmed")
                if repository.get_candidate(issuer_id) is not None:
                    continue  # already recorded as rejected — never re-litigated
                dedup_key = generate_evidence_dedup_key(
                    issuer_id, record.source_record_id, match.relationship_type.value, match.matched_pattern_category,
                )
                if repository.evidence_exists(dedup_key):
                    continue
                evidence = CandidateEvidence(
                    issuer_id=issuer_id, source_type=record.source_type, source_name=record.source_name,
                    source_record_id=record.source_record_id, source_url=record.source_url,
                    source_snippet=match.snippet, relationship_type=match.relationship_type,
                    matched_pattern_category=match.matched_pattern_category,
                    extraction_timestamp=now_iso, dedup_key=dedup_key,
                    related_core_issuer_id=related_core_issuer_id, theme_slug=match.theme_slug,
                    supply_chain_layer=match.supply_chain_layer, source_published_at=record.published_at,
                )
                repository.create_rejected_or_quarantined_candidate(
                    issuer_id=issuer_id, legal_name=match.org_text, country_or_jurisdiction="Unconfirmed",
                    entity_kind=result.entity_kind.value, coverage_state=CoverageState.REJECTED.value,
                    discovered_via=f"Company Discovery worker — {result.reason}", now=now_iso,
                    evidence=evidence, alias_text=result.normalized_name,
                )
                evidence_created += 1
                candidates_rejected += 1
                continue

            if result.outcome == ResolutionOutcome.NEW_QUARANTINED:
                issuer_id = generate_issuer_id(match.org_text, "Unconfirmed")
                if repository.get_candidate(issuer_id) is not None:
                    continue
                dedup_key = generate_evidence_dedup_key(
                    issuer_id, record.source_record_id, match.relationship_type.value, match.matched_pattern_category,
                )
                if repository.evidence_exists(dedup_key):
                    continue
                evidence = CandidateEvidence(
                    issuer_id=issuer_id, source_type=record.source_type, source_name=record.source_name,
                    source_record_id=record.source_record_id, source_url=record.source_url,
                    source_snippet=match.snippet, relationship_type=match.relationship_type,
                    matched_pattern_category=match.matched_pattern_category,
                    extraction_timestamp=now_iso, dedup_key=dedup_key,
                    related_core_issuer_id=related_core_issuer_id, theme_slug=match.theme_slug,
                    supply_chain_layer=match.supply_chain_layer, source_published_at=record.published_at,
                )
                repository.create_rejected_or_quarantined_candidate(
                    issuer_id=issuer_id, legal_name=match.org_text, country_or_jurisdiction="Unconfirmed",
                    entity_kind=result.entity_kind.value, coverage_state=CoverageState.QUARANTINED.value,
                    discovered_via=f"Company Discovery worker — {result.reason}", now=now_iso,
                    evidence=evidence, alias_text=result.normalized_name,
                )
                evidence_created += 1
                candidates_quarantined += 1
                continue

            # MATCHED_EXISTING_CANDIDATE or NEW_CANDIDATE
            if result.outcome == ResolutionOutcome.MATCHED_EXISTING_CANDIDATE:
                issuer_id = result.matched_issuer_id
            else:
                issuer_id = generate_issuer_id(match.org_text, "Unconfirmed")

            dedup_key = generate_evidence_dedup_key(
                issuer_id, record.source_record_id, match.relationship_type.value, match.matched_pattern_category,
            )
            if repository.evidence_exists(dedup_key):
                continue

            evidence = CandidateEvidence(
                issuer_id=issuer_id, source_type=record.source_type, source_name=record.source_name,
                source_record_id=record.source_record_id, source_url=record.source_url,
                source_snippet=match.snippet, relationship_type=match.relationship_type,
                matched_pattern_category=match.matched_pattern_category,
                extraction_timestamp=now_iso, dedup_key=dedup_key,
                related_core_issuer_id=related_core_issuer_id, theme_slug=match.theme_slug,
                supply_chain_layer=match.supply_chain_layer, source_published_at=record.published_at,
            )

            existing = repository.get_candidate(issuer_id)
            if existing is not None:
                repository.append_evidence_to_existing_candidate(evidence, result.normalized_name, now_iso)
            else:
                repository.create_candidate_with_evidence(
                    issuer_id=issuer_id, legal_name=match.org_text, native_name="", country_or_jurisdiction="Unconfirmed",
                    entity_kind=result.entity_kind.value, coverage_state=CoverageState.DISCOVERED.value,
                    resolution_confidence=ResolutionConfidence.MEDIUM.value,
                    discovered_via="Company Discovery worker — deterministic pattern extraction", now=now_iso,
                    evidence=evidence, alias_text=result.normalized_name,
                )
                candidates_created += 1
            evidence_created += 1
            touched_issuer_ids.add(issuer_id)
            known_aliases[result.normalized_name] = issuer_id

    for issuer_id in touched_issuer_ids:
        _rescore_candidate(repository, issuer_id, now_iso)

    candidates_archived = _run_decay_pass(repository, stale_days=stale_days, now=now) if run_decay_pass else 0

    return CandidateDiscoveryTickReport(
        evidence_created=evidence_created, candidates_created=candidates_created,
        candidates_quarantined=candidates_quarantined, candidates_rejected=candidates_rejected,
        candidates_archived=candidates_archived,
    )


def _run_decay_pass(repository: CandidateIssuerRepositoryProtocol, *, stale_days: int, now: datetime) -> int:
    """Discovered -> Archived only, for a Candidate whose last_evidence_at
    is older than `stale_days`. Never touches Rejected/Quarantined
    (already terminal) or Archived (already decayed) rows."""
    archived = 0
    for record in repository.list_candidates(coverage_state=CoverageState.DISCOVERED.value):
        try:
            last_evidence = datetime.fromisoformat(record.last_evidence_at)
        except (ValueError, TypeError):
            continue
        if last_evidence.tzinfo is None:
            last_evidence = last_evidence.replace(tzinfo=timezone.utc)
        if (now - last_evidence) > timedelta(days=stale_days):
            repository.transition_state(CandidateStateTransition(
                issuer_id=record.issuer.issuer_id, from_state=CoverageState.DISCOVERED.value,
                to_state=CoverageState.ARCHIVED.value, at=now.isoformat(),
                detail=f"No new evidence in {stale_days}+ days.", triggered_by="worker:company_discovery",
            ))
            archived += 1
    return archived
