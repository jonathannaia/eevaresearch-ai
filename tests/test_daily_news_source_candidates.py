"""Daily News Source-Expansion & Ingestion Design, Batch 1.5 — pure,
fixture-driven tests for the new UNUSED source_candidates.py module.
Zero network calls, zero database access, zero worker/pipeline/UI/
translation import anywhere in this file or the module it tests — see
the isolation tests at the bottom, which mechanically prove that claim
rather than merely asserting it in prose.
"""
from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from src.data_access.daily_news.feed_registry import PILOT_FEEDS
from src.data_access.daily_news.source_candidates import (
    FILTER_POLICIES,
    VALIDATED_SOURCE_CANDIDATES,
    FilterPolicyValidationError,
    SourceCandidateRecord,
    SourceCandidateStatus,
    SourceCandidateValidationError,
    SourceFilterPolicy,
    assert_valid_filter_policy,
    assert_valid_source_candidate,
    validate_filter_policy,
    validate_source_candidate_record,
)
from src.data_access.daily_news.source_registry import (
    RUNTIME_SOURCE_REGISTRY,
    SourceCategory,
    SourceFormat,
    SourceHealthState,
    SourceReliabilityTier,
)

_REPO_ROOT = Path(__file__).parent.parent


def _candidate(**overrides) -> SourceCandidateRecord:
    fields = dict(
        source_id="test-candidate", display_name="Test Candidate", category=SourceCategory.GOVERNMENT_POLICY,
        reliability_tier=SourceReliabilityTier.SHADOW_ONLY, status=SourceCandidateStatus.VALIDATED_SHADOW_CANDIDATE,
        official_landing_page="https://example.gov", validated_at="2026-09-04",
        validation_source_record="Local, bounded, read-only browser validation pass, 2026-09-04.",
        machine_endpoint="https://example.gov/feed.rss", machine_format=SourceFormat.RSS_ATOM,
        allowed_item_domains=("example.gov",), language="English", filter_policy_id="test-policy",
    )
    fields.update(overrides)
    return SourceCandidateRecord(**fields)


def _policy(**overrides) -> SourceFilterPolicy:
    fields = dict(
        policy_id="test-policy", source_id="test-candidate", title_keywords=("test",),
    )
    fields.update(overrides)
    return SourceFilterPolicy(**fields)


# ============================================================
# Exactly the five approved records, no more, no fewer
# ============================================================


def test_validated_source_candidates_has_exactly_five_records():
    assert len(VALIDATED_SOURCE_CANDIDATES) == 5


def test_source_ids_are_exactly_the_five_approved_ids():
    assert {c.source_id for c in VALIDATED_SOURCE_CANDIDATES} == {
        "federal-register-api", "nist-chips-news", "jpx-news", "bis-news-updates", "ustr-press-releases",
    }


def test_source_ids_are_unique():
    ids = [c.source_id for c in VALIDATED_SOURCE_CANDIDATES]
    assert len(ids) == len(set(ids))


def test_every_record_validates_with_zero_violations():
    for record in VALIDATED_SOURCE_CANDIDATES:
        assert validate_source_candidate_record(record) == (), record.source_id


def test_every_filter_policy_validates_with_zero_violations():
    for policy in FILTER_POLICIES:
        assert validate_filter_policy(policy) == (), policy.policy_id


# ============================================================
# Per-record expected shape, from the approved validation pass
# ============================================================


def test_federal_register_record_matches_approved_scope():
    record = next(c for c in VALIDATED_SOURCE_CANDIDATES if c.source_id == "federal-register-api")
    assert record.category == SourceCategory.GOVERNMENT_POLICY
    assert record.status == SourceCandidateStatus.VALIDATED_SHADOW_CANDIDATE
    assert record.machine_endpoint == "https://www.federalregister.gov/api/v1/documents.json"
    assert record.machine_format == SourceFormat.OFFICIAL_API
    assert record.allowed_item_domains == ("federalregister.gov",)
    assert "govinfo.gov" not in record.allowed_item_domains
    assert record.health_state is None
    assert record.filter_policy_id == "federal-register-agency-keyword-filter"


def test_nist_chips_record_matches_approved_scope():
    record = next(c for c in VALIDATED_SOURCE_CANDIDATES if c.source_id == "nist-chips-news")
    assert record.category == SourceCategory.GOVERNMENT_POLICY
    assert record.status == SourceCandidateStatus.VALIDATED_SHADOW_CANDIDATE
    assert record.machine_endpoint == "https://www.nist.gov/news-events/news/rss.xml"
    assert record.machine_format == SourceFormat.RSS_ATOM
    assert "www.nist.gov" in record.allowed_item_domains
    assert record.health_state is None
    assert record.filter_policy_id == "nist-chips-topic-filter"


def test_jpx_news_record_matches_approved_scope():
    record = next(c for c in VALIDATED_SOURCE_CANDIDATES if c.source_id == "jpx-news")
    assert record.category == SourceCategory.EXCHANGE
    assert record.status == SourceCandidateStatus.VALIDATED_SHADOW_CANDIDATE
    assert record.machine_endpoint == "https://www.jpx.co.jp/english/rss/jpx-news.xml"
    assert record.machine_format == SourceFormat.RSS_ATOM
    assert "www.jpx.co.jp" in record.allowed_item_domains
    assert record.health_state is None
    assert record.filter_policy_id == "jpx-institutional-scope-filter"


def test_jpx_news_is_never_described_as_issuer_timely_disclosure():
    # TDnet is explicitly and deliberately mentioned in this record's own
    # notes, but only to draw the "this is NOT that" distinction — the
    # record must never claim jpx-news itself IS issuer timely
    # disclosure/TDnet coverage.
    record = next(c for c in VALIDATED_SOURCE_CANDIDATES if c.source_id == "jpx-news")
    combined_text = f"{record.filtering_requirement} {record.validation_notes}"
    assert "NOT TDnet" in combined_text
    assert "must never be described as per-issuer timely disclosure" in combined_text


def test_bis_news_updates_record_is_deferred_with_no_endpoint():
    record = next(c for c in VALIDATED_SOURCE_CANDIDATES if c.source_id == "bis-news-updates")
    assert record.category == SourceCategory.REGULATOR
    assert record.status == SourceCandidateStatus.DEFERRED
    assert record.machine_endpoint is None
    assert record.machine_format is None
    assert record.filter_policy_id is None
    assert "no scraper" in record.constraints.lower()


def test_ustr_press_releases_record_is_deferred_with_no_endpoint():
    record = next(c for c in VALIDATED_SOURCE_CANDIDATES if c.source_id == "ustr-press-releases")
    assert record.category == SourceCategory.GOVERNMENT_POLICY
    assert record.status == SourceCandidateStatus.DEFERRED
    assert record.machine_endpoint is None
    assert record.machine_format is None
    assert record.filter_policy_id is None
    assert "no scraper" in record.constraints.lower()


def test_no_record_has_a_health_state_set():
    # None of these five sources has ever been polled by the real
    # worker — a live SourceHealthState value would misrepresent that.
    for record in VALIDATED_SOURCE_CANDIDATES:
        assert record.health_state is None, record.source_id


# ============================================================
# Filter policies — exactly one per validated_shadow_candidate,
# proposed data matches the approved scope
# ============================================================


def test_filter_policies_has_exactly_three_records_one_per_validated_shadow_candidate():
    assert len(FILTER_POLICIES) == 3
    shadow_candidates = {
        c.source_id for c in VALIDATED_SOURCE_CANDIDATES if c.status == SourceCandidateStatus.VALIDATED_SHADOW_CANDIDATE
    }
    assert {p.source_id for p in FILTER_POLICIES} == shadow_candidates


def test_every_filter_policy_id_is_referenced_by_exactly_one_candidate():
    policy_ids = {p.policy_id for p in FILTER_POLICIES}
    referenced_ids = {c.filter_policy_id for c in VALIDATED_SOURCE_CANDIDATES if c.filter_policy_id}
    assert policy_ids == referenced_ids


def test_federal_register_filter_policy_data():
    policy = next(p for p in FILTER_POLICIES if p.policy_id == "federal-register-agency-keyword-filter")
    assert "Bureau of Industry and Security" in policy.agency_allowlist
    assert "semiconductor" in policy.title_keywords
    assert policy.fail_closed_requires_positive_match is True


def test_nist_filter_policy_data():
    policy = next(p for p in FILTER_POLICIES if p.policy_id == "nist-chips-topic-filter")
    assert "CHIPS" in policy.title_keywords
    assert "chips" in policy.url_keywords
    assert policy.fail_closed_requires_positive_match is True


def test_jpx_filter_policy_data_uses_only_institutional_terms():
    policy = next(p for p in FILTER_POLICIES if p.policy_id == "jpx-institutional-scope-filter")
    assert policy.title_keywords  # non-empty positive-match field required
    combined = " ".join(policy.title_keywords).lower() + " " + policy.notes.lower()
    assert "issuer" not in combined or "not" in combined
    assert policy.fail_closed_requires_positive_match is True


# ============================================================
# Fail-closed enforcement
# ============================================================


def test_filter_policy_with_no_positive_match_field_is_rejected():
    policy = _policy(title_keywords=(), url_keywords=(), agency_allowlist=(), topic_theme_tags=())
    violations = validate_filter_policy(policy)
    assert any("at least one non-empty positive-match field" in v for v in violations)


def test_filter_policy_with_fail_closed_disabled_is_rejected():
    policy = _policy(fail_closed_requires_positive_match=False)
    violations = validate_filter_policy(policy)
    assert any("fail_closed_requires_positive_match must always be True" in v for v in violations)


def test_filter_policy_with_unrecognized_evaluation_order_field_is_rejected():
    policy = _policy(evaluation_order=("not_a_real_field",))
    violations = validate_filter_policy(policy)
    assert any("unrecognized field name" in v for v in violations)


def test_filter_policy_with_recognized_evaluation_order_has_no_violation():
    policy = _policy(evaluation_order=("title_keywords", "url_keywords"))
    assert validate_filter_policy(policy) == ()


def test_assert_valid_filter_policy_raises_for_an_invalid_policy():
    with pytest.raises(FilterPolicyValidationError):
        assert_valid_filter_policy(_policy(fail_closed_requires_positive_match=False))


def test_assert_valid_filter_policy_does_not_raise_for_a_valid_policy():
    assert_valid_filter_policy(_policy())  # must not raise


# ============================================================
# SourceCandidateRecord validation
# ============================================================


def test_validated_shadow_candidate_without_https_endpoint_is_rejected():
    violations = validate_source_candidate_record(_candidate(machine_endpoint="http://example.gov/feed.rss"))
    assert any("https://" in v for v in violations)


def test_validated_shadow_candidate_without_machine_format_is_rejected():
    violations = validate_source_candidate_record(_candidate(machine_format=None))
    assert any("machine_format" in v for v in violations)


def test_validated_shadow_candidate_without_allowed_item_domains_is_rejected():
    violations = validate_source_candidate_record(_candidate(allowed_item_domains=()))
    assert any("allowed_item_domains" in v for v in violations)


def test_validated_shadow_candidate_without_filter_policy_id_is_rejected():
    violations = validate_source_candidate_record(_candidate(filter_policy_id=None))
    assert any("filter_policy_id" in v for v in violations)


def test_validated_shadow_candidate_without_language_is_rejected():
    violations = validate_source_candidate_record(_candidate(language=""))
    assert any("language" in v for v in violations)


def test_deferred_candidate_with_a_machine_endpoint_is_rejected():
    violations = validate_source_candidate_record(_candidate(
        status=SourceCandidateStatus.DEFERRED, machine_endpoint="https://example.gov/feed.rss",
        constraints="No scraper is implemented.",
    ))
    assert any("machine_endpoint=None" in v for v in violations)


def test_deferred_candidate_without_no_scraper_statement_is_rejected():
    violations = validate_source_candidate_record(_candidate(
        status=SourceCandidateStatus.DEFERRED, machine_endpoint=None, constraints="Needs more work.",
    ))
    assert any("no scraper" in v for v in violations)


def test_deferred_candidate_with_no_scraper_statement_has_no_violation():
    record = _candidate(
        status=SourceCandidateStatus.DEFERRED, machine_endpoint=None, machine_format=None,
        allowed_item_domains=(), filter_policy_id=None,
        constraints="No scraper is implemented, suggested, or implied by this record.",
    )
    assert validate_source_candidate_record(record) == ()


def test_missing_official_landing_page_is_rejected():
    violations = validate_source_candidate_record(_candidate(official_landing_page=""))
    assert any("official_landing_page" in v for v in violations)


def test_missing_validation_source_record_is_rejected():
    violations = validate_source_candidate_record(_candidate(validation_source_record=""))
    assert any("validation_source_record" in v for v in violations)


def test_assert_valid_source_candidate_raises_for_an_invalid_record():
    with pytest.raises(SourceCandidateValidationError):
        assert_valid_source_candidate(_candidate(official_landing_page=""))


def test_assert_valid_source_candidate_does_not_raise_for_a_valid_record():
    assert_valid_source_candidate(_candidate())  # must not raise


# ============================================================
# Excluded-name guard (SemiAnalysis / Citrini Research / Serenity)
# ============================================================


@pytest.mark.parametrize("excluded", ["SemiAnalysis", "Citrini Research", "Serenity"])
def test_excluded_name_in_display_name_is_rejected(excluded):
    violations = validate_source_candidate_record(_candidate(display_name=excluded))
    assert any("excluded source name matched" in v for v in violations)


@pytest.mark.parametrize("excluded", ["SemiAnalysis", "Citrini Research", "Serenity"])
def test_excluded_name_in_validation_notes_is_rejected(excluded):
    violations = validate_source_candidate_record(_candidate(validation_notes=f"See {excluded} for context."))
    assert any("excluded source name matched" in v for v in violations)


@pytest.mark.parametrize("excluded", ["SemiAnalysis", "Citrini Research", "Serenity"])
def test_excluded_name_in_filter_policy_notes_is_rejected(excluded):
    violations = validate_filter_policy(_policy(notes=f"Per {excluded}."))
    assert any("excluded source name matched" in v for v in violations)


def test_no_approved_record_or_policy_contains_an_excluded_name():
    for record in VALIDATED_SOURCE_CANDIDATES:
        for field_value in (record.source_id, record.display_name, record.validation_notes, record.constraints):
            assert "semianalysis" not in field_value.lower()
            assert "citrini" not in field_value.lower()
            assert "serenity" not in field_value.lower()
    for policy in FILTER_POLICIES:
        assert "semianalysis" not in policy.notes.lower()
        assert "citrini" not in policy.notes.lower()
        assert "serenity" not in policy.notes.lower()


# ============================================================
# Frozen/immutable
# ============================================================


def test_source_candidate_record_is_frozen():
    record = _candidate()
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.display_name = "changed"  # type: ignore[misc]


def test_source_filter_policy_is_frozen():
    policy = _policy()
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.policy_id = "changed"  # type: ignore[misc]


# ============================================================
# Absent from every real runtime registry/feed collection
# ============================================================


def test_no_candidate_source_id_or_endpoint_appears_in_runtime_source_registry():
    runtime_source_ids = {e.source_id for e in RUNTIME_SOURCE_REGISTRY}
    runtime_urls = {e.canonical_url for e in RUNTIME_SOURCE_REGISTRY}
    for candidate in VALIDATED_SOURCE_CANDIDATES:
        assert candidate.source_id not in runtime_source_ids, candidate.source_id
        if candidate.machine_endpoint:
            assert candidate.machine_endpoint not in runtime_urls, candidate.source_id


def test_no_candidate_endpoint_appears_in_pilot_feeds():
    pilot_urls = {f.feed_url for f in PILOT_FEEDS}
    for candidate in VALIDATED_SOURCE_CANDIDATES:
        if candidate.machine_endpoint:
            assert candidate.machine_endpoint not in pilot_urls, candidate.source_id


def test_no_candidate_company_name_collides_with_a_pilot_feed_company():
    # These candidates are issuer-agnostic (government/exchange sources,
    # not tied to any tracked company) — confirm no accidental overlap
    # with PILOT_FEEDS' own company_name-keyed entries.
    pilot_company_names = {f.company_name for f in PILOT_FEEDS}
    for candidate in VALIDATED_SOURCE_CANDIDATES:
        assert candidate.display_name not in pilot_company_names


def test_source_reliability_tier_used_here_is_still_unattached_to_any_real_registry_entry():
    from src.data_access.daily_news.source_registry import DailyNewsSourceEntry

    field_names = {f.name for f in dataclasses.fields(DailyNewsSourceEntry)}
    assert "reliability_tier" not in field_names


# ============================================================
# Isolation — mechanical proof. Neither new module imports any
# worker, feed-fetch client, database repository, translation provider,
# or UI render module; and no real runtime path imports either module.
# canonical_url.py itself is never imported (this batch never alters or
# depends on its behavior).
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


_NEW_MODULE_PATH = _REPO_ROOT / "src" / "data_access" / "daily_news" / "source_candidates.py"

_FORBIDDEN_IMPORT_SUBSTRINGS = (
    "daily_news_worker", "daily_news_backend", "daily_news_pipeline", "rss_atom_client",
    "daily_news_store", "daily_news_scan_status_repository", "feed_registry",
    "state_db", "postgres_state_db",
    "translation_service", "deepl_provider",
    "streamlit", "src.ui",
    "src.models.models",
    "canonical_url",
)


def test_new_module_imports_no_worker_feed_client_database_translation_ui_or_canonical_url_code():
    offenders = [
        module for module in _imported_modules(_NEW_MODULE_PATH)
        if any(forbidden in module for forbidden in _FORBIDDEN_IMPORT_SUBSTRINGS)
    ]
    assert not offenders, offenders


_RUNTIME_INGESTION_PUBLICATION_PATHS = (
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "daily_news_pipeline.py",
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "daily_news_backend.py",
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "daily_news_store.py",
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "feed_registry.py",
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "rss_atom_client.py",
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "canonical_url.py",
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "dedup.py",
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "source_registry.py",
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "filing_event_models.py",
    _REPO_ROOT / "src" / "data_access" / "daily_news" / "policy_disclosure_models.py",
    _REPO_ROOT / "scripts" / "daily_news_worker.py",
    _REPO_ROOT / "src" / "ui" / "pages" / "daily_news.py",
    _REPO_ROOT / "src" / "ui" / "pages" / "daily_news_admin.py",
)


def test_no_real_runtime_or_other_foundation_module_references_source_candidates():
    for path in _RUNTIME_INGESTION_PUBLICATION_PATHS:
        offenders = [module for module in _imported_modules(path) if "source_candidates" in module]
        assert not offenders, (path.name, offenders)


def test_new_module_performs_no_io_import_at_all():
    forbidden_primitives = ("requests", "sqlite3", "psycopg", "urllib.request", "http.client")
    offenders = [m for m in _imported_modules(_NEW_MODULE_PATH) if m in forbidden_primitives]
    assert not offenders, offenders


def test_source_registry_module_does_not_import_source_candidates():
    # One-directional dependency: source_candidates.py imports FROM
    # source_registry.py (to reuse SourceCategory/SourceFormat/
    # SourceHealthState/SourceReliabilityTier/contains_excluded_source_name)
    # — never the other way around.
    path = _REPO_ROOT / "src" / "data_access" / "daily_news" / "source_registry.py"
    offenders = [m for m in _imported_modules(path) if "source_candidates" in m]
    assert not offenders
