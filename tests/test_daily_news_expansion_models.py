"""Daily News Source-Expansion & Ingestion Design, Batch 1 — pure,
fixture-driven tests for the two new UNUSED foundation modules
(filing_event_models.py, policy_disclosure_models.py). Zero network
calls, zero database access, zero worker/pipeline/UI/translation import
anywhere in this file or the modules it tests — see the isolation tests
at the bottom, which mechanically prove that claim rather than merely
asserting it in prose.
"""
from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

from src.data_access.daily_news.filing_event_models import (
    FilingCandidateStatus,
    FilingCandidateValidationError,
    FilingDerivedNewsCandidate,
    FilingProvenance,
    FilingSourceSystem,
    assert_valid_filing_candidate,
    validate_filing_candidate,
    validate_filing_event_category,
)
from src.data_access.daily_news.policy_disclosure_models import (
    DisclosureClassification,
    DisclosureItemType,
    DisclosureLifecycleStatus,
    OfficialDisclosureProvenance,
    PolicyDisclosureCandidate,
    PolicyDisclosureValidationError,
    PublicationEligibility,
    assert_valid_policy_disclosure_candidate,
    validate_policy_disclosure_candidate,
)

_REPO_ROOT = Path(__file__).parent.parent


def _filing_provenance(**overrides) -> FilingProvenance:
    fields = dict(
        source_system=FilingSourceSystem.EDGAR, retrieved_at="2026-09-04T00:00:00+00:00",
        source_form_code="8-K item 1.01",
    )
    fields.update(overrides)
    return FilingProvenance(**fields)


def _filing_candidate(**overrides) -> FilingDerivedNewsCandidate:
    fields = dict(
        doc_id="0000320193-26-000001", source_system=FilingSourceSystem.EDGAR, company_name="NVIDIA",
        issuer_identifier="0001045810", filing_date="2026-09-04", title_native="Material Agreement",
        event_category="material_agreement", provenance=_filing_provenance(),
        dedupe_key="nvidia|2026-09-04|material agreement", status=FilingCandidateStatus.SHADOW,
    )
    fields.update(overrides)
    return FilingDerivedNewsCandidate(**fields)


def _disclosure_provenance(**overrides) -> OfficialDisclosureProvenance:
    fields = dict(
        issuing_body="U.S. Federal Register", retrieved_at="2026-09-04T00:00:00+00:00",
        document_identifier="2026-12345",
    )
    fields.update(overrides)
    return OfficialDisclosureProvenance(**fields)


def _policy_candidate(**overrides) -> PolicyDisclosureCandidate:
    fields = dict(
        item_type=DisclosureItemType.ENACTED_RULE, classification=DisclosureClassification.OFFICIAL_PUBLIC_DISCLOSURE,
        lifecycle_status=DisclosureLifecycleStatus.EFFECTIVE, status_detail="Effective 2026-10-01",
        title="Final rule on X", published_at="2026-09-04", provenance=_disclosure_provenance(),
        publication_eligibility=PublicationEligibility.AUTONOMOUS_ELIGIBLE,
    )
    fields.update(overrides)
    return PolicyDisclosureCandidate(**fields)


# ============================================================
# FilingDerivedNewsCandidate — enum vocabulary
# ============================================================


def test_filing_source_system_has_exactly_edgar_dart_edinet():
    assert {s.value for s in FilingSourceSystem} == {"EDGAR", "DART", "EDINET"}


def test_filing_candidate_status_has_exactly_the_four_expected_values():
    assert {s.value for s in FilingCandidateStatus} == {"shadow", "candidate", "published", "suppressed"}


# ============================================================
# FilingDerivedNewsCandidate — category validation (fail-closed)
# ============================================================


def test_edgar_real_categories_from_edgar_rules_are_accepted():
    assert validate_filing_event_category(FilingSourceSystem.EDGAR, "earnings_or_results")
    assert validate_filing_event_category(FilingSourceSystem.EDGAR, "material_agreement")
    assert validate_filing_event_category(FilingSourceSystem.EDGAR, "ownership_change")


def test_dart_real_categories_from_dart_rules_are_accepted():
    assert validate_filing_event_category(FilingSourceSystem.DART, "earnings")
    assert validate_filing_event_category(FilingSourceSystem.DART, "capex_or_facility_investment")


def test_edinet_only_share_buyback_and_extraordinary_report_are_accepted():
    assert validate_filing_event_category(FilingSourceSystem.EDINET, "share_buyback_status")
    assert validate_filing_event_category(FilingSourceSystem.EDINET, "extraordinary_report")


def test_edinet_annual_securities_report_is_explicitly_rejected():
    # This batch's own explicit correction: annual securities reports are
    # NOT Daily News eligible at all, unlike share_buyback_status/
    # extraordinary_report.
    assert not validate_filing_event_category(FilingSourceSystem.EDINET, "annual_securities_report")


def test_unrecognized_category_is_rejected_for_every_source_system():
    assert not validate_filing_event_category(FilingSourceSystem.EDGAR, "not_a_real_category")
    assert not validate_filing_event_category(FilingSourceSystem.DART, "not_a_real_category")
    assert not validate_filing_event_category(FilingSourceSystem.EDINET, "not_a_real_category")


def test_unrecognized_source_system_is_rejected():
    assert not validate_filing_event_category("SOME_OTHER_SYSTEM", "earnings_or_results")


# ============================================================
# FilingDerivedNewsCandidate — validate_filing_candidate()
# ============================================================


def test_a_well_formed_shadow_candidate_has_no_violations():
    assert validate_filing_candidate(_filing_candidate()) == ()


def test_unrecognized_event_category_is_a_violation():
    violations = validate_filing_candidate(_filing_candidate(event_category="not_a_real_category"))
    assert any("unrecognized event_category" in v for v in violations)


def test_empty_doc_id_company_name_or_title_is_rejected():
    assert any("doc_id must be non-empty" in v for v in validate_filing_candidate(_filing_candidate(doc_id="")))
    assert any("company_name must be non-empty" in v for v in validate_filing_candidate(_filing_candidate(company_name="")))
    assert any("title_native must be non-empty" in v for v in validate_filing_candidate(_filing_candidate(title_native="")))


def test_published_without_official_document_url_is_rejected():
    violations = validate_filing_candidate(_filing_candidate(status=FilingCandidateStatus.PUBLISHED))
    assert any("PUBLISHED candidate must have a non-empty https:// official_document_url" in v for v in violations)


def test_published_with_non_https_url_is_rejected():
    violations = validate_filing_candidate(_filing_candidate(
        status=FilingCandidateStatus.PUBLISHED, official_document_url="http://www.sec.gov/x",
    ))
    assert any("https://" in v for v in violations)


def test_published_with_a_real_https_url_has_no_violations():
    valid = _filing_candidate(status=FilingCandidateStatus.PUBLISHED, official_document_url="https://www.sec.gov/Archives/edgar/data/x")
    assert validate_filing_candidate(valid) == ()


def test_suppressed_without_a_reason_is_rejected():
    violations = validate_filing_candidate(_filing_candidate(status=FilingCandidateStatus.SUPPRESSED))
    assert any("suppression_reason must be non-empty" in v for v in violations)


def test_suppressed_with_a_reason_has_no_violations():
    valid = _filing_candidate(status=FilingCandidateStatus.SUPPRESSED, suppression_reason="Duplicate of an existing story")
    assert validate_filing_candidate(valid) == ()


def test_edinet_candidate_may_be_shadow_or_suppressed():
    edinet_shadow = _filing_candidate(
        source_system=FilingSourceSystem.EDINET, event_category="share_buyback_status",
        provenance=_filing_provenance(source_system=FilingSourceSystem.EDINET, source_form_code="010:170000:220"),
        status=FilingCandidateStatus.SHADOW,
    )
    assert validate_filing_candidate(edinet_shadow) == ()

    edinet_suppressed = dataclasses.replace(
        edinet_shadow, status=FilingCandidateStatus.SUPPRESSED, suppression_reason="Stale item",
    )
    assert validate_filing_candidate(edinet_suppressed) == ()


def test_edinet_candidate_cannot_be_candidate_or_published():
    base = _filing_candidate(
        source_system=FilingSourceSystem.EDINET, event_category="extraordinary_report",
        provenance=_filing_provenance(source_system=FilingSourceSystem.EDINET, source_form_code="010:053000:180"),
        status=FilingCandidateStatus.SHADOW,
    )
    for forbidden_status, extra in (
        (FilingCandidateStatus.CANDIDATE, {}),
        (FilingCandidateStatus.PUBLISHED, {"official_document_url": "https://disclosure2.edinet-fsa.go.jp/x"}),
    ):
        candidate = dataclasses.replace(base, status=forbidden_status, **extra)
        violations = validate_filing_candidate(candidate)
        assert any("EDINET filing candidates must remain shadow_only" in v for v in violations), forbidden_status


def test_edgar_candidate_may_be_published_unlike_edinet():
    # Confirms the EDINET restriction is source-system-specific, not a
    # blanket rule applied to every system.
    valid = _filing_candidate(status=FilingCandidateStatus.PUBLISHED, official_document_url="https://www.sec.gov/Archives/edgar/data/x")
    assert validate_filing_candidate(valid) == ()


def test_translation_language_required_when_title_translated_is_set():
    violations = validate_filing_candidate(_filing_candidate(title_translated="Translated title"))
    assert any("translation_language is required" in v for v in violations)


def test_translation_language_present_has_no_violation():
    valid = _filing_candidate(title_translated="Translated title", translation_language="English")
    assert validate_filing_candidate(valid) == ()


def test_title_native_is_never_overwritten_by_a_translation():
    candidate = _filing_candidate(title_translated="Translated title", translation_language="English")
    assert candidate.title_native == "Material Agreement"
    assert candidate.title_native != candidate.title_translated


def test_assert_valid_filing_candidate_raises_for_an_invalid_candidate():
    import pytest

    with pytest.raises(FilingCandidateValidationError):
        assert_valid_filing_candidate(_filing_candidate(event_category="not_a_real_category"))


def test_assert_valid_filing_candidate_does_not_raise_for_a_valid_candidate():
    assert_valid_filing_candidate(_filing_candidate())  # must not raise


def test_filing_candidate_and_provenance_are_frozen():
    candidate = _filing_candidate()
    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        candidate.title_native = "changed"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        candidate.provenance.source_form_code = "changed"  # type: ignore[misc]


# ============================================================
# PolicyDisclosureCandidate — enum vocabulary
# ============================================================


def test_disclosure_item_type_has_exactly_the_eight_approved_values():
    assert {t.value for t in DisclosureItemType} == {
        "enacted_rule", "proposed_rule", "agency_action", "official_press_release", "bill_status",
        "budget_appropriation", "procurement_award", "disclosed_trade_holding_lobbying",
    }


def test_disclosure_lifecycle_status_has_exactly_proposed_final_effective():
    assert {s.value for s in DisclosureLifecycleStatus} == {"proposed", "final", "effective"}


def test_disclosure_classification_has_exactly_one_member():
    assert [c.value for c in DisclosureClassification] == ["Official public disclosure"]


def test_publication_eligibility_has_exactly_the_three_expected_values():
    assert {e.value for e in PublicationEligibility} == {
        "autonomous_eligible", "shadow_only", "manual_review_required",
    }


# ============================================================
# PolicyDisclosureCandidate — no political-intent/recommendation/
# sentiment field (structural, not merely a docstring claim)
# ============================================================


def test_no_field_name_suggests_intent_recommendation_or_sentiment():
    forbidden_substrings = ("intent", "recommend", "sentiment", "bias", "stance")
    for model in (PolicyDisclosureCandidate, OfficialDisclosureProvenance):
        for f in dataclasses.fields(model):
            lowered = f.name.lower()
            assert not any(bad in lowered for bad in forbidden_substrings), (model.__name__, f.name)


# ============================================================
# PolicyDisclosureCandidate — validate_policy_disclosure_candidate()
# ============================================================


def test_a_well_formed_candidate_has_no_violations():
    assert validate_policy_disclosure_candidate(_policy_candidate()) == ()


def test_classification_must_equal_the_single_fixed_value():
    violations = validate_policy_disclosure_candidate(_policy_candidate(classification="Not the real classification"))
    assert any("classification must be" in v for v in violations)


def test_empty_status_detail_title_or_published_at_is_rejected():
    assert any("status_detail must be non-empty" in v for v in validate_policy_disclosure_candidate(_policy_candidate(status_detail="")))
    assert any("title must be non-empty" in v for v in validate_policy_disclosure_candidate(_policy_candidate(title="")))
    assert any("published_at must be non-empty" in v for v in validate_policy_disclosure_candidate(_policy_candidate(published_at="")))


def test_empty_provenance_fields_are_rejected():
    violations = validate_policy_disclosure_candidate(_policy_candidate(provenance=_disclosure_provenance(issuing_body="")))
    assert any("provenance.issuing_body must be non-empty" in v for v in violations)


def test_excluded_source_name_in_issuing_body_is_rejected():
    for excluded in ("SemiAnalysis", "Citrini Research", "Serenity"):
        violations = validate_policy_disclosure_candidate(_policy_candidate(provenance=_disclosure_provenance(issuing_body=excluded)))
        assert any("excluded source name matched" in v for v in violations), excluded


def test_official_document_url_when_set_must_be_https():
    violations = validate_policy_disclosure_candidate(_policy_candidate(official_document_url="http://example.gov/x"))
    assert any("https://" in v for v in violations)


def test_official_document_url_none_is_allowed():
    assert validate_policy_disclosure_candidate(_policy_candidate(official_document_url=None)) == ()


def test_disclosed_trade_holding_lobbying_cannot_be_autonomous_eligible():
    violations = validate_policy_disclosure_candidate(_policy_candidate(
        item_type=DisclosureItemType.DISCLOSED_TRADE_HOLDING_LOBBYING,
        publication_eligibility=PublicationEligibility.AUTONOMOUS_ELIGIBLE,
    ))
    assert any("disclosed_trade_holding_lobbying items must never be publication_eligibility=AUTONOMOUS_ELIGIBLE" in v for v in violations)


def test_disclosed_trade_holding_lobbying_may_be_shadow_only_or_manual_review():
    for eligibility in (PublicationEligibility.SHADOW_ONLY, PublicationEligibility.MANUAL_REVIEW_REQUIRED):
        candidate = _policy_candidate(
            item_type=DisclosureItemType.DISCLOSED_TRADE_HOLDING_LOBBYING, publication_eligibility=eligibility,
        )
        assert validate_policy_disclosure_candidate(candidate) == (), eligibility


def test_every_other_item_type_may_be_autonomous_eligible():
    for item_type in DisclosureItemType:
        if item_type == DisclosureItemType.DISCLOSED_TRADE_HOLDING_LOBBYING:
            continue
        candidate = _policy_candidate(item_type=item_type, publication_eligibility=PublicationEligibility.AUTONOMOUS_ELIGIBLE)
        assert validate_policy_disclosure_candidate(candidate) == (), item_type


def test_assert_valid_policy_disclosure_candidate_raises_for_an_invalid_candidate():
    import pytest

    with pytest.raises(PolicyDisclosureValidationError):
        assert_valid_policy_disclosure_candidate(_policy_candidate(classification="wrong"))


def test_assert_valid_policy_disclosure_candidate_does_not_raise_for_a_valid_candidate():
    assert_valid_policy_disclosure_candidate(_policy_candidate())  # must not raise


def test_policy_disclosure_candidate_and_provenance_are_frozen():
    candidate = _policy_candidate()
    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        candidate.title = "changed"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        candidate.provenance.issuing_body = "changed"  # type: ignore[misc]


# ============================================================
# Isolation — mechanical proof, not prose. Neither new module imports
# any worker, feed-fetch client, database repository, translation
# provider, or UI render module; and no real runtime ingestion/
# publication path imports either new module.
# ============================================================


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


_NEW_MODULE_PATHS = (
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "filing_event_models.py",
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "policy_disclosure_models.py",
)

_FORBIDDEN_IMPORT_SUBSTRINGS = (
    "daily_news_worker", "daily_news_backend", "daily_news_pipeline", "rss_atom_client",
    "daily_news_store", "daily_news_scan_status_repository",
    "state_db", "postgres_state_db",
    "translation_service", "deepl_provider",
    "streamlit", "src.ui",
    "src.models.models",  # Radar's own model — Daily News stays decoupled at the type level
)


def test_new_modules_import_no_worker_feed_client_database_translation_or_ui_code():
    for path in _NEW_MODULE_PATHS:
        offenders = [
            module for module in _imported_modules(path)
            if any(forbidden in module for forbidden in _FORBIDDEN_IMPORT_SUBSTRINGS)
        ]
        assert not offenders, (path.name, offenders)


_RUNTIME_INGESTION_PUBLICATION_PATHS = (
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "daily_news_pipeline.py",
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "daily_news_backend.py",
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "daily_news_store.py",
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "feed_registry.py",
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "rss_atom_client.py",
    _REPO_ROOT / "scripts" / "daily_news_worker.py",
    _REPO_ROOT / "src" / "ui" / "pages" / "daily_news.py",
    _REPO_ROOT / "src" / "ui" / "pages" / "daily_news_admin.py",
)


def test_no_real_runtime_ingestion_or_publication_path_references_the_new_models():
    for path in _RUNTIME_INGESTION_PUBLICATION_PATHS:
        offenders = [
            module for module in _imported_modules(path)
            if "filing_event_models" in module or "policy_disclosure_models" in module
        ]
        assert not offenders, (path.name, offenders)


def test_new_modules_perform_no_io_import_at_all():
    # Defense-in-depth beyond the specific forbidden-substring list above
    # — neither new module should import requests, sqlite3, psycopg, or
    # any network/database primitive directly, since both are pure,
    # dataclass-and-enum-only modules.
    forbidden_primitives = ("requests", "sqlite3", "psycopg", "urllib.request", "http.client")
    for path in _NEW_MODULE_PATHS:
        offenders = [m for m in _imported_modules(path) if m in forbidden_primitives]
        assert not offenders, (path.name, offenders)
