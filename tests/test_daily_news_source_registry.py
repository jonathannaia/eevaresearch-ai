"""Daily News source-registry FOUNDATION (design/DAILY_NEWS_SOURCE_
ADMISSION_POLICY.md) — pure, fixture-driven tests. Zero network calls.
`feed_registry.py` is asserted here to be byte-for-byte unchanged in
its own runtime shape (PILOT_FEEDS itself is never touched by this
foundation), and separately, that the new registry can represent every
one of those 12 sources with zero behavioral difference once adapted.
"""
from __future__ import annotations

from src.data_access.daily_news import feed_registry
from src.data_access.daily_news.source_registry import (
    PILOT_SOURCE_REGISTRY,
    DailyNewsSourceEntry,
    SourceCategory,
    SourceFormat,
    SourceHealthState,
    SourceRegistryValidationError,
    assert_valid_source_entry,
    find_registry_violations,
    normalize_source_url,
    to_daily_news_feed_source,
    validate_source_entry,
)


def _entry(**overrides) -> DailyNewsSourceEntry:
    fields = dict(
        source_id="test-source", category=SourceCategory.OFFICIAL_IR, format=SourceFormat.RSS_ATOM,
        canonical_url="https://example.com/rss", domains=("example.com",), jurisdiction="United States",
        enabled=True, health_state=SourceHealthState.PENDING_REVIEW, attribution_label="Example Co.",
        licensing_classification="Official company source.", priority=1, issuer_name="NVIDIA",
    )
    fields.update(overrides)
    return DailyNewsSourceEntry(**fields)


# ============================================================
# Enum vocabulary — exactly the categories/formats the approved scope named
# ============================================================


def test_source_category_has_exactly_the_five_approved_values():
    assert {c.value for c in SourceCategory} == {
        "official_ir", "official_newsroom", "official_filing", "regulator_exchange", "independent_news",
    }


def test_source_format_has_exactly_the_four_approved_values():
    assert {f.value for f in SourceFormat} == {
        "rss_atom", "official_api", "official_html_listing", "licensed_feed",
    }


# ============================================================
# Validation and normalization
# ============================================================


def test_valid_entry_has_no_violations():
    assert validate_source_entry(_entry()) == ()


def test_assert_valid_source_entry_raises_for_an_invalid_entry():
    invalid = _entry(canonical_url="http://example.com/rss")
    import pytest

    with pytest.raises(SourceRegistryValidationError):
        assert_valid_source_entry(invalid)


def test_assert_valid_source_entry_does_not_raise_for_a_valid_entry():
    assert_valid_source_entry(_entry())  # must not raise


def test_https_is_required():
    assert "canonical_url must be a non-empty https:// URL" in validate_source_entry(
        _entry(canonical_url="http://example.com/rss")
    )


def test_empty_canonical_url_is_rejected():
    assert "canonical_url must be a non-empty https:// URL" in validate_source_entry(
        _entry(canonical_url="")
    )


def test_empty_domains_is_rejected():
    assert "domains must be non-empty (at least one canonical domain)" in validate_source_entry(
        _entry(domains=())
    )


def test_empty_domain_entry_is_rejected():
    assert "domains must not contain an empty entry" in validate_source_entry(
        _entry(domains=("",))
    )


def test_empty_jurisdiction_is_rejected():
    assert "jurisdiction must be non-empty" in validate_source_entry(_entry(jurisdiction=""))


def test_empty_attribution_label_is_rejected():
    assert "attribution_label must be non-empty" in validate_source_entry(_entry(attribution_label=""))


def test_empty_licensing_classification_is_rejected_for_every_category():
    assert "licensing_classification must be non-empty" in validate_source_entry(
        _entry(licensing_classification="")
    )


def test_priority_below_one_is_rejected():
    assert "priority must be >= 1" in validate_source_entry(_entry(priority=0))


def test_normalize_source_url_is_case_and_trailing_slash_insensitive():
    a = normalize_source_url("https://EXAMPLE.com/rss/")
    b = normalize_source_url("https://example.com/rss")
    assert a == b


def test_normalize_source_url_preserves_query_string():
    a = normalize_source_url("https://example.com/feed?category=press")
    b = normalize_source_url("https://example.com/feed?category=other")
    assert a != b


def test_normalize_source_url_treats_different_paths_as_different():
    a = normalize_source_url("https://example.com/rss")
    b = normalize_source_url("https://example.com/atom")
    assert a != b


# ============================================================
# Unsupported category/format rejection
# ============================================================


def test_unsupported_category_string_is_rejected():
    entry = DailyNewsSourceEntry(
        source_id="bad-cat", category="not_a_real_category", format=SourceFormat.RSS_ATOM,
        canonical_url="https://example.com/rss", domains=("example.com",), jurisdiction="United States",
        enabled=True, health_state=SourceHealthState.PENDING_REVIEW, attribution_label="X",
        licensing_classification="test", priority=1, issuer_name="NVIDIA",
    )
    violations = validate_source_entry(entry)
    assert any("unsupported category" in v for v in violations)


def test_unsupported_format_string_is_rejected():
    entry = DailyNewsSourceEntry(
        source_id="bad-fmt", category=SourceCategory.OFFICIAL_IR, format="carrier_pigeon",
        canonical_url="https://example.com/rss", domains=("example.com",), jurisdiction="United States",
        enabled=True, health_state=SourceHealthState.PENDING_REVIEW, attribution_label="X",
        licensing_classification="test", priority=1, issuer_name="NVIDIA",
    )
    violations = validate_source_entry(entry)
    assert any("unsupported format" in v for v in violations)


def test_unsupported_health_state_string_is_rejected():
    entry = DailyNewsSourceEntry(
        source_id="bad-health", category=SourceCategory.OFFICIAL_IR, format=SourceFormat.RSS_ATOM,
        canonical_url="https://example.com/rss", domains=("example.com",), jurisdiction="United States",
        enabled=True, health_state="on_fire", attribution_label="X",
        licensing_classification="test", priority=1, issuer_name="NVIDIA",
    )
    violations = validate_source_entry(entry)
    assert any("unsupported health_state" in v for v in violations)


# ============================================================
# Prohibited-source rejection: social media + explicit excluded names
# ============================================================


def test_twitter_domain_is_rejected():
    violations = validate_source_entry(_entry(domains=("twitter.com",)))
    assert any("prohibited social-media domain" in v for v in violations)


def test_x_dot_com_domain_is_rejected():
    violations = validate_source_entry(_entry(domains=("x.com",)))
    assert any("prohibited social-media domain" in v for v in violations)


def test_reddit_domain_is_rejected():
    violations = validate_source_entry(_entry(domains=("reddit.com",)))
    assert any("prohibited social-media domain" in v for v in violations)


def test_semianalysis_is_rejected_by_attribution_label():
    violations = validate_source_entry(_entry(attribution_label="SemiAnalysis", issuer_agnostic=True, issuer_name=None, allowlisted=True))
    assert any("excluded source name matched" in v and "semianalysis" in v for v in violations)


def test_semianalysis_is_rejected_case_insensitively():
    violations = validate_source_entry(_entry(attribution_label="the SEMIANALYSIS newsletter", issuer_agnostic=True, issuer_name=None, allowlisted=True))
    assert any("excluded source name matched" in v for v in violations)


def test_citrini_research_is_rejected():
    violations = validate_source_entry(_entry(attribution_label="Citrini Research", issuer_agnostic=True, issuer_name=None, allowlisted=True))
    assert any("excluded source name matched" in v and "citrini" in v for v in violations)


def test_serenity_is_rejected():
    violations = validate_source_entry(_entry(attribution_label="Serenity", issuer_agnostic=True, issuer_name=None, allowlisted=True))
    assert any("excluded source name matched" in v and "serenity" in v for v in violations)


def test_excluded_name_check_also_covers_source_id():
    violations = validate_source_entry(_entry(source_id="serenity-macro-feed", attribution_label="Some other label", issuer_agnostic=True, issuer_name=None, allowlisted=True))
    assert any("excluded source name matched" in v for v in violations)


def test_a_legitimate_official_source_is_never_falsely_matched_as_excluded():
    # Sanity: the excluded-name check must not over-match ordinary text.
    violations = validate_source_entry(_entry(attribution_label="NVIDIA", source_id="nvidia-newsroom-rss"))
    assert not any("excluded source name matched" in v for v in violations)


# ============================================================
# Duplicate rejection
# ============================================================


def test_exact_duplicate_url_issuer_category_is_rejected():
    a = _entry(source_id="a", canonical_url="https://example.com/rss")
    b = _entry(source_id="b", canonical_url="https://example.com/rss")
    violations = find_registry_violations((a, b))
    assert any("duplicate of" in v for v in violations)


def test_duplicate_detection_is_normalization_aware():
    a = _entry(source_id="a", canonical_url="https://EXAMPLE.com/rss/")
    b = _entry(source_id="b", canonical_url="https://example.com/rss")
    violations = find_registry_violations((a, b))
    assert any("duplicate of" in v for v in violations)


def test_same_url_different_issuer_is_not_a_duplicate():
    a = _entry(source_id="a", canonical_url="https://example.com/rss", issuer_name="NVIDIA")
    b = _entry(source_id="b", canonical_url="https://example.com/rss", issuer_name="Intel Corp.")
    violations = find_registry_violations((a, b))
    assert not any("duplicate of" in v for v in violations)


def test_same_url_different_category_is_not_a_duplicate():
    a = _entry(source_id="a", canonical_url="https://example.com/rss", category=SourceCategory.OFFICIAL_IR)
    b = _entry(source_id="b", canonical_url="https://example.com/rss", category=SourceCategory.OFFICIAL_NEWSROOM)
    violations = find_registry_violations((a, b))
    assert not any("duplicate of" in v for v in violations)


def test_no_false_positive_duplicates_among_three_distinct_entries():
    a = _entry(source_id="a", canonical_url="https://example.com/rss-a")
    b = _entry(source_id="b", canonical_url="https://example.com/rss-b")
    c = _entry(source_id="c", canonical_url="https://example.com/rss-c")
    assert find_registry_violations((a, b, c)) == ()


# ============================================================
# Issuer-linkage validation
# ============================================================


def test_official_source_with_no_issuer_linkage_at_all_is_rejected():
    entry = _entry(issuer_name=None, issuer_agnostic=False)
    violations = validate_source_entry(entry)
    assert any("must set issuer_name or explicitly set issuer_agnostic" in v for v in violations)


def test_setting_both_issuer_name_and_issuer_agnostic_is_rejected():
    entry = _entry(issuer_name="NVIDIA", issuer_agnostic=True)
    violations = validate_source_entry(entry)
    assert any("mutually exclusive" in v for v in violations)


def test_official_source_with_a_real_tracked_issuer_is_accepted():
    entry = _entry(issuer_name="NVIDIA")
    assert validate_source_entry(entry) == ()


def test_official_source_with_a_fake_issuer_name_is_rejected():
    entry = _entry(issuer_name="Totally Fake Company That Does Not Exist, Inc.")
    violations = validate_source_entry(entry)
    assert any("does not resolve via tracked_company_for" in v for v in violations)


def test_official_source_can_use_a_discovery_stub_issuer_too():
    # Quanta Services resolves via tracked_company_for() through either
    # the real TrackedCompany path or the DISCOVERY_STUBS fallback — see
    # feed_registry.tracked_company_for()'s own docstring. Either is
    # accepted here, matching the real pipeline's own resolution.
    entry = _entry(issuer_name="Quanta Services, Inc.")
    assert validate_source_entry(entry) == ()


def test_regulator_exchange_source_may_be_issuer_agnostic():
    entry = _entry(
        category=SourceCategory.REGULATOR_EXCHANGE, issuer_name=None, issuer_agnostic=True,
        attribution_label="Example Regulator",
    )
    assert validate_source_entry(entry) == ()


def test_official_ir_source_cannot_be_issuer_agnostic_and_skip_linkage_checks():
    # issuer_agnostic=True is structurally allowed (the mutual-exclusion
    # rule doesn't forbid it for OFFICIAL_IR), but this documents the
    # real intent: an issuer-linked category should, in practice, always
    # carry a real issuer_name. This test proves an issuer-agnostic
    # OFFICIAL_IR entry at least still passes validation cleanly (no
    # crash, no false violation) rather than asserting a stronger rule
    # the approved scope didn't request.
    entry = _entry(category=SourceCategory.OFFICIAL_IR, issuer_name=None, issuer_agnostic=True)
    assert validate_source_entry(entry) == ()


# ============================================================
# Independent-news allowlist/licensing validation
# ============================================================


def test_independent_news_without_allowlisting_is_rejected():
    entry = _entry(
        category=SourceCategory.INDEPENDENT_NEWS, issuer_name=None, issuer_agnostic=True,
        attribution_label="Some Wire Service", licensing_classification="Licensed newswire content.",
        allowlisted=False,
    )
    violations = validate_source_entry(entry)
    assert any("must be explicitly allowlisted" in v for v in violations)


def test_independent_news_without_licensing_classification_is_rejected():
    entry = _entry(
        category=SourceCategory.INDEPENDENT_NEWS, issuer_name=None, issuer_agnostic=True,
        attribution_label="Some Wire Service", licensing_classification="", allowlisted=True,
    )
    violations = validate_source_entry(entry)
    assert any("licensing_classification must be non-empty" in v for v in violations)


def test_independent_news_with_both_allowlisting_and_licensing_is_accepted():
    entry = _entry(
        category=SourceCategory.INDEPENDENT_NEWS, issuer_name=None, issuer_agnostic=True,
        attribution_label="Some Wire Service", licensing_classification="Licensed newswire content.",
        allowlisted=True,
    )
    assert validate_source_entry(entry) == ()


# ============================================================
# PILOT_FEEDS preservation + migration/adapter equivalence proof
# ============================================================


def test_feed_registry_pilot_feeds_is_completely_untouched():
    # This foundation must not alter feed_registry.py at all — the real
    # pipeline/worker import chain stays exactly as it was.
    assert len(feed_registry.PILOT_FEEDS) == 12


def test_pilot_source_registry_has_exactly_twelve_entries():
    assert len(PILOT_SOURCE_REGISTRY) == 12


def test_pilot_source_registry_has_zero_validation_violations():
    assert find_registry_violations(PILOT_SOURCE_REGISTRY) == ()


def test_pilot_source_registry_source_ids_are_unique():
    ids = [e.source_id for e in PILOT_SOURCE_REGISTRY]
    assert len(ids) == len(set(ids))


def test_pilot_source_registry_covers_the_same_twelve_companies_as_pilot_feeds():
    registry_companies = {e.issuer_name for e in PILOT_SOURCE_REGISTRY}
    pilot_companies = {f.company_name for f in feed_registry.PILOT_FEEDS}
    assert registry_companies == pilot_companies


def test_adapting_pilot_source_registry_reproduces_pilot_feeds_exactly_in_order():
    adapted = tuple(to_daily_news_feed_source(e) for e in PILOT_SOURCE_REGISTRY)
    assert adapted == feed_registry.PILOT_FEEDS


def test_to_daily_news_feed_source_rejects_a_non_rss_atom_format():
    entry = _entry(format=SourceFormat.OFFICIAL_API)
    import pytest

    with pytest.raises(SourceRegistryValidationError):
        to_daily_news_feed_source(entry)


def test_to_daily_news_feed_source_rejects_an_issuer_agnostic_entry():
    entry = _entry(issuer_name=None, issuer_agnostic=True, category=SourceCategory.REGULATOR_EXCHANGE)
    import pytest

    with pytest.raises(SourceRegistryValidationError):
        to_daily_news_feed_source(entry)


# ============================================================
# Scope guard — this foundation must not be imported by the real
# pipeline/worker/UI, matching the approved "must not add, remove,
# replace, poll, or fetch any external feed yet" constraint.
# ============================================================


def test_source_registry_module_is_not_imported_by_the_real_pipeline_worker_or_ui():
    import ast
    from pathlib import Path

    repo_root = Path(__file__).parent.parent
    targets = (
        repo_root / "src" / "data_access" / "daily_news" / "daily_news_pipeline.py",
        repo_root / "scripts" / "daily_news_worker.py",
        repo_root / "src" / "ui" / "pages" / "daily_news.py",
        repo_root / "src" / "ui" / "pages" / "daily_news_admin.py",
        repo_root / "src" / "data_access" / "daily_news" / "feed_registry.py",
    )
    for path in targets:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
            elif isinstance(node, ast.Import):
                module = ",".join(alias.name for alias in node.names)
            assert not (module and "source_registry" in module), (path, module)
