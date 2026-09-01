"""EevaResearch — Citrini-style Theme research workspace vertical slice
(design/DECISIONS.md). The end-to-end outcome proof: builds the real
"AI Infrastructure: Where Is the Binding Constraint?" theme with real,
specific content across every workspace dimension — company roles,
radar-derived evidence (ingested via the deterministic matcher + human
review + promotion), hypotheses with confidence and disconfirming
conditions, a curator decision, and a watch item — and asserts the
result is NOT an empty record. Every fixture is synthetic and locally
constructed (a representative, not-real TSMC 8-K); nothing here touches
a real network, worker, scan, LLM, or production database. The theme
never leaves visibility=internal in this test — publication is a
separate, not-yet-built reviewed-evidence workflow."""
from __future__ import annotations

from src.config.settings import Settings
from src.data_access import backend_factory
from src.logic.theme_evidence_promotion import build_evidence_from_accepted_match
from src.logic.research_case_theme_matching import evaluate_theme_match
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, StateTransition
from src.models.research_case import ResearchCase, ResearchCaseStatus
from src.models.theme_matching import ThemeMatchingScope, ThemeMatchReviewDecision, MatchReviewStatus
from src.models.theme_research import (
    CompanyRole,
    EvidenceDirection,
    HypothesisConfidence,
    ResearchTheme,
    ThemeCategory,
    ThemeCompanyMapEntry,
    ThemeNoteType,
    ThemeResearchNote,
    ThemeStatus,
    ThemeVisibility,
)

_THEME_TITLE = "AI Infrastructure: Where Is the Binding Constraint?"
_THEME_CREATED_AT = "2026-09-01T11:35:00-04:00"


def _build_theme(theme_id: str) -> ResearchTheme:
    return ResearchTheme(
        id=theme_id,
        category=ThemeCategory.BOTTLENECK,
        status=ThemeStatus.NEW,
        visibility=ThemeVisibility.INTERNAL,
        title=_THEME_TITLE,
        key_question=(
            "Where in the AI infrastructure supply chain is capacity, availability, or "
            "delivery timing becoming a binding constraint?"
        ),
        hypothesis=(
            "Potential constraints may emerge across compute, memory, packaging, "
            "interconnect, power, cooling, and related infrastructure inputs."
        ),
        working_thesis=(
            "Internal evidence-collection draft. This Theme is collecting official-source "
            "context for human review and does not represent a published research conclusion."
        ),
        why_it_matters="To be determined after reviewed evidence identifies a specific, testable supply-chain constraint.",
        what_could_change_the_view="To be determined from reviewed supporting, mixed, and contradictory evidence.",
        what_to_watch_next=(
            "Official company disclosures relevant to capacity, supply, demand, lead times, yields, "
            "utilization, capital expenditure, packaging, memory, power, cooling, and interconnect infrastructure."
        ),
        created_at=_THEME_CREATED_AT,
        updated_at=_THEME_CREATED_AT,
    )


def _build_scope(theme_id: str) -> ThemeMatchingScope:
    return ThemeMatchingScope(
        theme_id=theme_id,
        sector_tags=("ai-buildout", "memory"),
        sector_subtags=(
            "compute-accelerators", "dram", "hbm", "semiconductor-test",
            "power-cooling", "interconnect", "interconnect-switching", "optical-components",
        ),
        allowed_matched_rule_categories=("material_agreement", "financing_or_debt", "other_material_event"),
        required_keywords=(
            "capacity", "wafer", "fab", "foundry", "packaging", "hbm", "dram", "allocation",
            "lead time", "yield", "node", "supply agreement", "capacity expansion",
        ),
        excluded_keywords=(
            "share repurchase", "stock buyback", "dividend declaration",
            "annual meeting of stockholders", "proxy statement", "executive compensation",
        ),
    )


def _representative_tsmc_candidate() -> CandidateSignal:
    """A synthetic, representative filing shaped exactly like a real
    TSMC 8-K capacity-expansion disclosure would be once ingested by
    the EDGAR pipeline — not a real filing, but real-shaped content
    (not placeholder junk) for exercising the full pipeline end-to-end."""
    filing = FilingEvent(
        rcept_no="0000320193-26-000123", corp_code="0001046179", corp_name="Taiwan Semiconductor Manufacturing Company Limited",
        stock_code="TSM", report_nm="8-K", rcept_dt="2026-08-20", flr_nm="Taiwan Semiconductor Manufacturing Company Limited",
        source_name="SEC EDGAR", source_url="https://www.sec.gov/Archives/edgar/data/1046179/000104617926000123/tsm-8k.htm",
        retrieved_at="2026-08-20T13:05:00+00:00", original_language="English",
        theme_slug="ai-buildout", subtheme_slug="compute-accelerators",
    )
    return CandidateSignal(
        id="edgar-cand-tsm-2026-08-20", filing=filing,
        matched_rules=["material_agreement:1.01"], confidence="High",
        status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original=(
            "The Company entered into a supply agreement to expand advanced packaging capacity "
            "at its Chiayi facility, targeting increased wafer allocation and shorter lead time "
            "for CoWoS-class packaging in support of AI accelerator demand."
        ),
        state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="2026-08-20T13:10:00+00:00")],
    )


def _promote_and_persist(settings: Settings, candidate: CandidateSignal, theme_id: str, case_id: str) -> None:
    from src.data_access import research_store

    research_store.append_research_case(settings.cache_dir, ResearchCase(
        id=case_id, trigger_source_type="radar", trigger_source_id=candidate.id,
        trigger_source_name=candidate.filing.corp_name, trigger_summary=candidate.filing.report_nm,
        title=f"{candidate.filing.corp_name} — {candidate.filing.report_nm}",
        research_question="What are the evidence-backed dependencies connected to this filing?",
        status=ResearchCaseStatus.OPEN, created_at="2026-08-20T13:10:00+00:00", version=1,
    ))

    matching_repository = backend_factory.get_theme_matching_repository(settings)
    scope = matching_repository.get_scope(theme_id)
    match = evaluate_theme_match(candidate, case_id, scope, created_at="2026-08-20T13:10:00+00:00")
    assert match is not None, "the representative candidate must actually pass the scope's own gates"
    matching_repository.insert_match(match)

    decision = ThemeMatchReviewDecision(
        id="theme-match-review-tsm-1", match_id=match.id, decision=MatchReviewStatus.ACCEPTED,
        reviewer_note="Confirmed: TSMC advanced-packaging capacity agreement, directly relevant.",
        reviewed_at="2026-08-21T09:00:00+00:00",
    )
    matching_repository.insert_review_decision(decision)

    evidence = build_evidence_from_accepted_match(
        candidate, match, decision, EvidenceDirection.SUPPORTS,
        fact="TSMC disclosed a supply agreement expanding advanced (CoWoS-class) packaging capacity at Chiayi.",
        relevance="Directly supports the hypothesis that advanced packaging, not wafer fabrication, is the near-term binding constraint for AI accelerator supply.",
    )
    assert evidence is not None
    curator = backend_factory.get_theme_curator_repository(settings)
    inserted = curator.insert_evidence_item(evidence)
    assert inserted is True


def test_ai_infrastructure_theme_is_populated_not_empty(tmp_path):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    curator = backend_factory.get_theme_curator_repository(settings)

    # 1. The internal Theme container itself.
    theme = _build_theme(theme_id="theme-ai-infra-binding-constraint")
    assert curator.insert_theme(theme) is True

    # 2. Map the supply chain: real company roles, not placeholders.
    company_map_entries = [
        ThemeCompanyMapEntry(id="cm-1", theme_id=theme.id, company_name="Nvidia Corporation", role=CompanyRole.DEMAND_DRIVER, note="Primary source of AI accelerator demand pulling on packaging/HBM capacity."),
        ThemeCompanyMapEntry(id="cm-2", theme_id=theme.id, company_name="Taiwan Semiconductor Manufacturing Company", role=CompanyRole.CONSTRAINT_OWNER, note="Controls advanced packaging (CoWoS) capacity — the current suspected binding constraint."),
        ThemeCompanyMapEntry(id="cm-3", theme_id=theme.id, company_name="SK Hynix Inc.", role=CompanyRole.ENABLER, note="HBM supplier; capacity here could relieve or worsen the constraint."),
        ThemeCompanyMapEntry(id="cm-4", theme_id=theme.id, company_name="Micron Technology, Inc.", role=CompanyRole.ENABLER, note="Second HBM supplier, ramping capacity."),
        ThemeCompanyMapEntry(id="cm-5", theme_id=theme.id, company_name="Advanced Micro Devices, Inc.", role=CompanyRole.EXPOSED, note="Exposed to the same packaging/HBM constraints as Nvidia for its own accelerators."),
    ]
    for entry in company_map_entries:
        assert curator.insert_company_map_entry(entry) is True

    # 3. Attach the matching scope and ingest one real, representative
    # radar signal as reviewed, curator-classified evidence.
    scope = _build_scope(theme.id)
    matching_repository = backend_factory.get_theme_matching_repository(settings)
    assert matching_repository.insert_scope(scope) is True

    candidate = _representative_tsmc_candidate()
    candidate_repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    candidate_repo.upsert_new_candidates([candidate])
    _promote_and_persist(settings, candidate, theme.id, case_id="case-tsm-2026-08-20")

    # 4. Record a hypothesis (with confidence + disconfirming condition),
    # a curator decision, and a watch item — the running research log.
    hypothesis = ThemeResearchNote(
        id="note-hyp-1", theme_id=theme.id, note_type=ThemeNoteType.HYPOTHESIS,
        content="Advanced (CoWoS-class) packaging capacity, not wafer fabrication, is the nearest-term binding constraint on AI accelerator supply.",
        confidence=HypothesisConfidence.MEDIUM,
        disconfirming_condition="If TSMC or an alternate packaging provider discloses packaging capacity additions that outpace disclosed AI accelerator demand for two consecutive quarters, reject this hypothesis.",
        created_at="2026-08-21T09:05:00+00:00",
    )
    decision_note = ThemeResearchNote(
        id="note-dec-1", theme_id=theme.id, note_type=ThemeNoteType.DECISION,
        content="Escalate to weekly internal review given TSMC's own disclosed packaging expansion timeline.",
        confidence=None, disconfirming_condition=None, created_at="2026-08-21T09:10:00+00:00",
    )
    watch_item = ThemeResearchNote(
        id="note-watch-1", theme_id=theme.id, note_type=ThemeNoteType.WATCH_ITEM,
        content="Watch TSMC's next quarterly disclosure for updated CoWoS capacity and utilization commentary.",
        confidence=None, disconfirming_condition=None, created_at="2026-08-21T09:15:00+00:00",
    )
    for note in (hypothesis, decision_note, watch_item):
        assert curator.insert_research_note(note) is True

    # --- Outcome assertions: this is a populated workspace, not an empty record. ---

    stored_theme = curator.get_theme(theme.id)
    assert stored_theme.title == _THEME_TITLE
    assert stored_theme.visibility is ThemeVisibility.INTERNAL

    theme_repo = backend_factory.get_theme_repository(settings)
    company_map = theme_repo.company_map_for_theme(theme.id)
    assert len(company_map) == 5
    assert {e.role for e in company_map} == {CompanyRole.DEMAND_DRIVER, CompanyRole.CONSTRAINT_OWNER, CompanyRole.ENABLER, CompanyRole.EXPOSED}

    evidence = theme_repo.evidence_for_theme(theme.id)
    assert len(evidence) == 1
    assert evidence[0].direction is EvidenceDirection.SUPPORTS
    assert "TSMC" in evidence[0].fact or "packaging" in evidence[0].fact

    notes = curator.research_notes_for_theme(theme.id)
    assert len(notes) == 3
    note_types = {n.note_type for n in notes}
    assert note_types == {ThemeNoteType.HYPOTHESIS, ThemeNoteType.DECISION, ThemeNoteType.WATCH_ITEM}
    hypothesis_notes = [n for n in notes if n.note_type is ThemeNoteType.HYPOTHESIS]
    assert hypothesis_notes[0].confidence is HypothesisConfidence.MEDIUM
    assert "reject" in hypothesis_notes[0].disconfirming_condition.lower()

    # --- Non-public, internal-by-default invariant, throughout. ---
    assert theme_repo.list_published_themes() == ()
    assert theme_repo.get_published_theme(theme.id) is None


def test_vertical_slice_stays_internal_across_sqlite_backend(tmp_path):
    """Same scenario, SQLite backend — confirms the whole slice is not
    JSON-only plumbing."""
    settings = Settings(db_backend="sqlite", state_db_path=tmp_path / "state.db")
    curator = backend_factory.get_theme_curator_repository(settings)
    theme = _build_theme(theme_id="theme-ai-infra-sqlite")
    curator.insert_theme(theme)

    matching_repository = backend_factory.get_theme_matching_repository(settings)
    scope = _build_scope(theme.id)
    matching_repository.insert_scope(scope)

    candidate = _representative_tsmc_candidate()
    candidate_repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    candidate_repo.upsert_new_candidates([candidate])
    _promote_and_persist(settings, candidate, theme.id, case_id="case-tsm-sqlite")

    hypothesis = ThemeResearchNote(
        id="note-hyp-sqlite-1", theme_id=theme.id, note_type=ThemeNoteType.HYPOTHESIS,
        content="Advanced packaging capacity is the binding constraint.",
        confidence=HypothesisConfidence.MEDIUM, disconfirming_condition="Reject if packaging capacity outpaces demand.",
        created_at="2026-08-21T09:05:00+00:00",
    )
    assert curator.insert_research_note(hypothesis) is True

    theme_repo = backend_factory.get_theme_repository(settings)
    assert len(theme_repo.evidence_for_theme(theme.id)) == 1
    assert len(curator.research_notes_for_theme(theme.id)) == 1
    assert theme_repo.list_published_themes() == ()
