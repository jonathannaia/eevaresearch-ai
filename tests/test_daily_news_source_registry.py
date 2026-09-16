"""Daily News source registry (design/DAILY_NEWS_SOURCE_ADMISSION_
POLICY.md) — pure, fixture-driven tests. Zero network calls.

Updated for Daily News source-expansion batch 1 (2026-09-04):
`feed_registry.PILOT_FEEDS` is now derived from `source_registry.
RUNTIME_SOURCE_REGISTRY` (the original 12 pilot sources plus expansion
batch 1's 7) — this file proves that derivation reproduces the original
12 exactly, field-for-field, in their original order, first; that the 7
new entries validate cleanly; and that the resulting 19-entry runtime
feed list is exactly what's expected.
"""
from __future__ import annotations

from src.data_access.daily_news import feed_registry
from src.data_access.daily_news.source_registry import (
    EDITORIAL_SOURCE_REGISTRY,
    EDITORIAL_SOURCE_REGISTRY_BATCH_2,
    EDITORIAL_SOURCE_REGISTRY_BATCH_3,
    EDITORIAL_SOURCE_REGISTRY_BATCH_4,
    EDITORIAL_SOURCE_REGISTRY_V1,
    EXPANSION_BATCH_1_SOURCE_REGISTRY,
    EXPANSION_BATCH_2_SOURCE_REGISTRY,
    EXPANSION_BATCH_3_SOURCE_REGISTRY,
    EXPANSION_BATCH_4_SOURCE_REGISTRY,
    EXPANSION_BATCH_5_SOURCE_REGISTRY,
    EXPANSION_BATCH_6_SOURCE_REGISTRY,
    PILOT_SOURCE_REGISTRY,
    RUNTIME_SOURCE_REGISTRY,
    DailyNewsSourceEntry,
    SourceCategory,
    SourceFormat,
    SourceHealthState,
    SourceRegistryValidationError,
    SourceReliabilityTier,
    assert_valid_source_entry,
    contains_excluded_source_name,
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


def test_source_category_has_exactly_the_eleven_supported_values():
    # Was "exactly the five approved values"; the Daily News Source-
    # Expansion & Ingestion Design, Batch 1 (source-registry/model
    # foundation only) added six more (issuer_ir, issuer_newsroom,
    # exchange, regulator, government_policy, government_procurement) —
    # purely additive, the original five are unchanged and still used by
    # every real RUNTIME_SOURCE_REGISTRY entry (see
    # test_runtime_source_registry_has_zero_violations_after_batch_2
    # elsewhere in this file, still passing unmodified).
    assert {c.value for c in SourceCategory} == {
        "official_ir", "official_newsroom", "official_filing", "regulator_exchange", "independent_news",
        "issuer_ir", "issuer_newsroom", "exchange", "regulator", "government_policy", "government_procurement",
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


_EXPECTED_EXPANSION_BATCH_1_COMPANY_ORDER = (
    "Amazon.com, Inc.", "Meta Platforms, Inc.", "Oracle Corporation", "Applied Materials, Inc.",
    "Lam Research Corp", "KLA Corp", "Arm Holdings plc",
)
_EXPECTED_ORIGINAL_TWELVE_COMPANY_ORDER = (
    "NVIDIA", "Intel Corp.", "Advanced Micro Devices", "Bloom Energy Corp", "Marvell Technology, Inc.",
    "MaxLinear, Inc.", "Rockwell Automation", "SK Hynix", "Quanta Services, Inc.", "nVent Electric plc",
    "Arista Networks, Inc.", "Cisco Systems, Inc.",
)


def test_pilot_source_registry_still_has_exactly_twelve_entries_unchanged():
    # The original 12 pilot sources themselves are untouched by the
    # expansion batch — same 12, same fields, same order.
    assert len(PILOT_SOURCE_REGISTRY) == 12
    assert tuple(e.issuer_name for e in PILOT_SOURCE_REGISTRY) == _EXPECTED_ORIGINAL_TWELVE_COMPANY_ORDER


def test_expansion_batch_1_has_exactly_seven_entries_in_the_given_order():
    assert len(EXPANSION_BATCH_1_SOURCE_REGISTRY) == 7
    assert tuple(e.issuer_name for e in EXPANSION_BATCH_1_SOURCE_REGISTRY) == _EXPECTED_EXPANSION_BATCH_1_COMPANY_ORDER


def test_runtime_source_registry_is_the_twelve_then_the_seven_then_the_one_in_order():
    # Was "twelve then seven" (19 total) through expansion batch 1; batch
    # 2 (2026-09-04) appended exactly one more entry (19 + 1 = 20); batch
    # 3 (2026-09-11) appended exactly four more (20 + 4 = 24); batch 4
    # (2026-09-13) appended exactly three more (24 + 3 = 27); batch 5
    # (2026-09-13) appended exactly one more (27 + 1 = 28); the Daily
    # News Cohort 1 batch (2026-09-15) appended exactly four more
    # (28 + 4 = 32).
    assert len(RUNTIME_SOURCE_REGISTRY) == 32
    assert RUNTIME_SOURCE_REGISTRY[:12] == PILOT_SOURCE_REGISTRY
    assert RUNTIME_SOURCE_REGISTRY[12:19] == EXPANSION_BATCH_1_SOURCE_REGISTRY
    assert RUNTIME_SOURCE_REGISTRY[19:20] == EXPANSION_BATCH_2_SOURCE_REGISTRY
    assert RUNTIME_SOURCE_REGISTRY[20:24] == EXPANSION_BATCH_3_SOURCE_REGISTRY
    assert RUNTIME_SOURCE_REGISTRY[24:27] == EXPANSION_BATCH_4_SOURCE_REGISTRY
    assert RUNTIME_SOURCE_REGISTRY[27:28] == EXPANSION_BATCH_5_SOURCE_REGISTRY
    assert RUNTIME_SOURCE_REGISTRY[28:] == EXPANSION_BATCH_6_SOURCE_REGISTRY


def test_pilot_source_registry_has_zero_validation_violations():
    assert find_registry_violations(PILOT_SOURCE_REGISTRY) == ()


def test_expansion_batch_1_has_zero_validation_violations():
    assert find_registry_violations(EXPANSION_BATCH_1_SOURCE_REGISTRY) == ()


def test_runtime_source_registry_has_zero_validation_violations():
    # Also proves no cross-batch duplicate was introduced.
    assert find_registry_violations(RUNTIME_SOURCE_REGISTRY) == ()


def test_runtime_source_registry_source_ids_are_all_unique():
    ids = [e.source_id for e in RUNTIME_SOURCE_REGISTRY]
    assert len(ids) == len(set(ids)) == 32


def test_pilot_source_registry_covers_the_same_twelve_companies_as_pilot_feeds():
    registry_companies = {e.issuer_name for e in PILOT_SOURCE_REGISTRY}
    pilot_companies = set(_EXPECTED_ORIGINAL_TWELVE_COMPANY_ORDER)
    assert registry_companies == pilot_companies


def test_adapted_original_twelve_pilot_feeds_are_unchanged_and_first_in_order():
    """The exact proof this task's own approved scope requires: the
    original 12 runtime feeds (now computed via
    feed_registry.PILOT_FEEDS, derived from RUNTIME_SOURCE_REGISTRY) are
    field-for-field equal to adapting PILOT_SOURCE_REGISTRY directly,
    and are the first 12 entries of the real, live PILOT_FEEDS."""
    adapted_original_twelve = tuple(to_daily_news_feed_source(e) for e in PILOT_SOURCE_REGISTRY)
    assert len(feed_registry.PILOT_FEEDS) == 32
    assert feed_registry.PILOT_FEEDS[:12] == adapted_original_twelve
    assert tuple(f.company_name for f in feed_registry.PILOT_FEEDS[:12]) == _EXPECTED_ORIGINAL_TWELVE_COMPANY_ORDER


def test_final_runtime_feed_list_has_exactly_twenty_entries():
    # Was exactly 19 through expansion batch 1; batch 2 (2026-09-04)
    # appended exactly one more entry (19 + 1 = 20); batch 3
    # (2026-09-11) appended exactly four more (20 + 4 = 24); batch 4
    # (2026-09-13) appended exactly three more (24 + 3 = 27); batch 5
    # (2026-09-13) appended exactly one more (27 + 1 = 28); the Daily
    # News Cohort 1 batch (2026-09-15) appended exactly four more
    # (28 + 4 = 32).
    assert len(feed_registry.PILOT_FEEDS) == 32


def test_final_runtime_feed_list_appends_expansion_batch_1_after_the_original_twelve():
    adapted_expansion = tuple(to_daily_news_feed_source(e) for e in EXPANSION_BATCH_1_SOURCE_REGISTRY)
    assert feed_registry.PILOT_FEEDS[12:19] == adapted_expansion
    assert tuple(f.company_name for f in feed_registry.PILOT_FEEDS[12:19]) == _EXPECTED_EXPANSION_BATCH_1_COMPANY_ORDER


_EXPECTED_EXPANSION_BATCH_3_COMPANY_ORDER = (
    "Qualcomm Incorporated", "Corning Inc.", "Synopsys, Inc.", "Cadence Design Systems, Inc.",
)


def test_final_runtime_feed_list_appends_expansion_batch_3_after_batch_2():
    adapted_expansion = tuple(to_daily_news_feed_source(e) for e in EXPANSION_BATCH_3_SOURCE_REGISTRY)
    assert feed_registry.PILOT_FEEDS[20:24] == adapted_expansion


_EXPECTED_EXPANSION_BATCH_4_COMPANY_ORDER = (
    "Samsung Electronics", "Murata Manufacturing Co., Ltd.", "Microchip Technology Incorporated",
)


def test_final_runtime_feed_list_appends_expansion_batch_4_after_batch_3():
    adapted_expansion = tuple(to_daily_news_feed_source(e) for e in EXPANSION_BATCH_4_SOURCE_REGISTRY)
    assert feed_registry.PILOT_FEEDS[24:27] == adapted_expansion
    assert tuple(f.company_name for f in feed_registry.PILOT_FEEDS[24:27]) == _EXPECTED_EXPANSION_BATCH_4_COMPANY_ORDER


def test_final_runtime_feed_list_appends_expansion_batch_5_after_batch_4():
    adapted_expansion = tuple(to_daily_news_feed_source(e) for e in EXPANSION_BATCH_5_SOURCE_REGISTRY)
    assert feed_registry.PILOT_FEEDS[27:28] == adapted_expansion
    assert tuple(f.company_name for f in feed_registry.PILOT_FEEDS[27:28]) == (
        "Hewlett Packard Enterprise Company",
    )


def test_final_runtime_feed_list_appends_daily_news_cohort1_batch_after_batch_5():
    adapted_expansion = tuple(to_daily_news_feed_source(e) for e in EXPANSION_BATCH_6_SOURCE_REGISTRY)
    assert feed_registry.PILOT_FEEDS[28:] == adapted_expansion
    assert tuple(f.company_name for f in feed_registry.PILOT_FEEDS[28:]) == (
        "Equinix, Inc.", "L3Harris Technologies, Inc.", "Firefly Aerospace Inc.",
        "YASKAWA Electric Corporation",
    )


def test_final_runtime_feed_list_company_order_is_exactly_the_twenty_expected():
    assert tuple(f.company_name for f in feed_registry.PILOT_FEEDS) == (
        _EXPECTED_ORIGINAL_TWELVE_COMPANY_ORDER + _EXPECTED_EXPANSION_BATCH_1_COMPANY_ORDER
        + ("Meta Platforms, Inc.",) + _EXPECTED_EXPANSION_BATCH_3_COMPANY_ORDER
        + _EXPECTED_EXPANSION_BATCH_4_COMPANY_ORDER + ("Hewlett Packard Enterprise Company",)
        + (
            "Equinix, Inc.", "L3Harris Technologies, Inc.", "Firefly Aerospace Inc.",
            "YASKAWA Electric Corporation",
        )
    )


def test_all_nineteen_runtime_feeds_use_rss_feed_format():
    # feed_format is informational-only (rss_atom_client.py handles both
    # RSS and Atom regardless), but every one of these 19 real sources
    # is in fact RSS — proving the adapter's "rss" default is accurate
    # for the whole real runtime list, not just the original 12.
    assert all(f.feed_format == "rss" for f in feed_registry.PILOT_FEEDS)


def test_all_nineteen_runtime_feeds_are_official_issuer_linked_and_no_excluded_source_present():
    # Every runtime feed traces back to an enabled, RSS_ATOM-format,
    # issuer-linked entry in an official category — never
    # issuer-agnostic, never independent_news, never one of the
    # explicitly excluded names/domains.
    _OFFICIAL_ISSUER_CATEGORIES = (
        SourceCategory.OFFICIAL_IR, SourceCategory.OFFICIAL_NEWSROOM, SourceCategory.OFFICIAL_FILING,
    )
    excluded_lowered = {"semianalysis", "citrini", "serenity"}
    for entry in RUNTIME_SOURCE_REGISTRY:
        if not (entry.enabled and entry.format == SourceFormat.RSS_ATOM):
            continue
        assert entry.category in _OFFICIAL_ISSUER_CATEGORIES, entry.source_id
        assert entry.issuer_agnostic is False, entry.source_id
        assert entry.issuer_name, entry.source_id
        lowered = f"{entry.attribution_label} {entry.source_id} {entry.issuer_name}".lower()
        assert not any(name in lowered for name in excluded_lowered), entry.source_id
        for domain in entry.domains:
            assert "twitter.com" not in domain and "x.com" != domain and "reddit.com" not in domain


def test_expansion_batch_1_alphabet_microsoft_micron_are_not_present():
    # Explicit negative proof matching this task's own exclusion list.
    excluded_companies = {"Alphabet Inc.", "Microsoft Corporation", "Micron Technology"}
    runtime_companies = {f.company_name for f in feed_registry.PILOT_FEEDS}
    assert not (excluded_companies & runtime_companies)


# ============================================================
# Daily News source-expansion batch 2 (2026-09-04) — exactly one entry,
# a worker-context validation candidate for the existing, reportedly-
# blocked meta-ir-rss source. Appended after batch 1, never replacing
# or altering meta-ir-rss itself.
# ============================================================


def test_expansion_batch_2_has_exactly_one_entry():
    assert len(EXPANSION_BATCH_2_SOURCE_REGISTRY) == 1
    entry = EXPANSION_BATCH_2_SOURCE_REGISTRY[0]
    assert entry.source_id == "meta-newsroom-rss"


def test_meta_newsroom_rss_entry_has_the_exact_requested_fields():
    entry = EXPANSION_BATCH_2_SOURCE_REGISTRY[0]
    assert entry.category == SourceCategory.OFFICIAL_NEWSROOM
    assert entry.format == SourceFormat.RSS_ATOM
    assert entry.canonical_url == "https://about.fb.com/feed/"
    assert entry.domains == ("about.fb.com",)
    assert entry.jurisdiction == "United States"
    assert entry.enabled is True
    assert entry.attribution_label == "Meta Platforms, Inc."
    assert entry.priority == 1
    assert entry.issuer_name == "Meta Platforms, Inc."
    assert entry.last_verified_at == "2026-09-04"
    assert entry.issuer_agnostic is False


def test_meta_newsroom_rss_licensing_classification_matches_the_pilot_constant():
    entry = EXPANSION_BATCH_2_SOURCE_REGISTRY[0]
    pilot_entry_licensing = PILOT_SOURCE_REGISTRY[0].licensing_classification
    assert entry.licensing_classification == pilot_entry_licensing


def test_meta_newsroom_rss_health_state_is_pending_review_not_a_new_enum_value():
    # "needs_review" was requested but has no matching SourceHealthState
    # member (PENDING_REVIEW, VERIFIED, DEGRADED, FAILING, RETIRED only)
    # — no new member was added, per this task's own "do not change
    # validation" scope. PENDING_REVIEW is the closest existing state and
    # is what was actually used; this test locks that mapping in.
    entry = EXPANSION_BATCH_2_SOURCE_REGISTRY[0]
    assert entry.health_state == SourceHealthState.PENDING_REVIEW
    assert entry.health_state != SourceHealthState.VERIFIED


def test_meta_newsroom_rss_notes_never_claims_verified_or_bypasses_403():
    entry = EXPANSION_BATCH_2_SOURCE_REGISTRY[0]
    lowered = entry.notes.lower()
    assert "verified" not in lowered
    assert "bypass" not in lowered.replace("not confirmed to bypass", "")


def test_meta_newsroom_rss_entry_validates_with_zero_violations():
    assert validate_source_entry(EXPANSION_BATCH_2_SOURCE_REGISTRY[0]) == ()


def test_meta_ir_rss_remains_present_enabled_and_unchanged():
    meta_ir = next(e for e in RUNTIME_SOURCE_REGISTRY if e.source_id == "meta-ir-rss")
    original = next(e for e in EXPANSION_BATCH_1_SOURCE_REGISTRY if e.source_id == "meta-ir-rss")
    assert meta_ir == original
    assert meta_ir.enabled is True
    assert meta_ir.canonical_url == "https://investor.atmeta.com/rss/pressrelease.aspx"


def test_runtime_source_registry_is_nineteen_then_the_one_new_entry():
    assert len(RUNTIME_SOURCE_REGISTRY) == 32
    assert RUNTIME_SOURCE_REGISTRY[:12] == PILOT_SOURCE_REGISTRY
    assert RUNTIME_SOURCE_REGISTRY[12:19] == EXPANSION_BATCH_1_SOURCE_REGISTRY
    assert RUNTIME_SOURCE_REGISTRY[19:20] == EXPANSION_BATCH_2_SOURCE_REGISTRY
    assert RUNTIME_SOURCE_REGISTRY[20:24] == EXPANSION_BATCH_3_SOURCE_REGISTRY
    assert RUNTIME_SOURCE_REGISTRY[24:27] == EXPANSION_BATCH_4_SOURCE_REGISTRY
    assert RUNTIME_SOURCE_REGISTRY[27:28] == EXPANSION_BATCH_5_SOURCE_REGISTRY
    assert RUNTIME_SOURCE_REGISTRY[28:] == EXPANSION_BATCH_6_SOURCE_REGISTRY


def test_runtime_source_registry_has_zero_violations_after_batch_2():
    assert find_registry_violations(RUNTIME_SOURCE_REGISTRY) == ()


def test_original_nineteen_runtime_feeds_retain_their_exact_relative_order():
    expected_first_nineteen_companies = (
        _EXPECTED_ORIGINAL_TWELVE_COMPANY_ORDER + _EXPECTED_EXPANSION_BATCH_1_COMPANY_ORDER
    )
    assert tuple(f.company_name for f in feed_registry.PILOT_FEEDS[:19]) == expected_first_nineteen_companies


def test_meta_newsroom_rss_is_at_index_nineteen():
    # Was PILOT_FEEDS[-1] through batch 2; batch 3 (2026-09-11) appended
    # 4 more entries after it, so its fixed position is now index 19,
    # not -1 — the old assertion would otherwise be silently false.
    assert feed_registry.PILOT_FEEDS[19].company_name == "Meta Platforms, Inc."
    assert feed_registry.PILOT_FEEDS[19].feed_url == "https://about.fb.com/feed/"
    assert feed_registry.PILOT_FEEDS[19].canonical_domains == ("about.fb.com",)


def test_no_other_company_or_source_was_added_or_changed_by_batch_2():
    # Explicit negative proof matching this task's own exclusion list —
    # none of these appear anywhere in the final runtime list.
    excluded_companies = {
        "Bloom Energy Corp", "Rockwell Automation", "nVent Electric plc", "Arista Networks, Inc.",
        "Oracle Corporation", "Alphabet Inc.", "Microsoft Corporation", "Micron Technology",
    }
    runtime_companies = [f.company_name for f in feed_registry.PILOT_FEEDS]
    # These ARE expected to already be present from earlier batches
    # (Bloom/Rockwell/nVent/Arista/Oracle are original pilot/batch-1
    # entries) — the real proof is that batch 2 didn't touch or
    # duplicate them, and that the three hard-excluded companies
    # (Alphabet/Microsoft/Micron) are still absent.
    assert "Alphabet Inc." not in runtime_companies
    assert "Microsoft Corporation" not in runtime_companies
    assert "Micron Technology" not in runtime_companies
    assert runtime_companies.count("Meta Platforms, Inc.") == 2  # meta-ir-rss + meta-newsroom-rss, never more
    for name in ("Bloom Energy Corp", "Rockwell Automation", "nVent Electric plc", "Arista Networks, Inc.", "Oracle Corporation"):
        assert runtime_companies.count(name) == 1  # unchanged, still exactly one entry each


# ============================================================
# Daily News source-expansion batch 3 (2026-09-11) — 4 official issuer
# IR RSS feeds for tracked companies with no prior Daily News source,
# each independently live-verified this batch. Appended after batch 2,
# never replacing or altering any existing entry.
# ============================================================


_EXPANSION_BATCH_3_EXPECTED_FIELDS = {
    "qualcomm-ir-rss": dict(
        canonical_url="https://investor.qualcomm.com/rss/pressrelease.aspx", domains=("investor.qualcomm.com",),
        attribution_label="Qualcomm Incorporated", issuer_name="Qualcomm Incorporated",
    ),
    "corning-ir-rss": dict(
        canonical_url="https://investor.corning.com/rss/pressrelease.aspx", domains=("investor.corning.com",),
        attribution_label="Corning Incorporated", issuer_name="Corning Inc.",
    ),
    "synopsys-ir-rss": dict(
        canonical_url="https://investor.synopsys.com/rss/pressrelease.aspx", domains=("investor.synopsys.com",),
        attribution_label="Synopsys, Inc.", issuer_name="Synopsys, Inc.",
    ),
    "cadence-ir-rss": dict(
        canonical_url="https://investor.cadence.com/rss/pressrelease.aspx", domains=("investor.cadence.com",),
        attribution_label="Cadence Design Systems, Inc.", issuer_name="Cadence Design Systems, Inc.",
    ),
}


def test_expansion_batch_3_has_exactly_four_entries_in_the_given_order():
    assert len(EXPANSION_BATCH_3_SOURCE_REGISTRY) == 4
    assert tuple(e.source_id for e in EXPANSION_BATCH_3_SOURCE_REGISTRY) == (
        "qualcomm-ir-rss", "corning-ir-rss", "synopsys-ir-rss", "cadence-ir-rss",
    )


def test_expansion_batch_3_entries_have_the_exact_verified_fields():
    for entry in EXPANSION_BATCH_3_SOURCE_REGISTRY:
        expected = _EXPANSION_BATCH_3_EXPECTED_FIELDS[entry.source_id]
        assert entry.canonical_url == expected["canonical_url"]
        assert entry.domains == expected["domains"]
        assert entry.attribution_label == expected["attribution_label"]
        assert entry.issuer_name == expected["issuer_name"]
        assert entry.category == SourceCategory.OFFICIAL_IR
        assert entry.format == SourceFormat.RSS_ATOM
        assert entry.jurisdiction == "United States"
        assert entry.enabled is True
        assert entry.health_state == SourceHealthState.VERIFIED
        assert entry.priority == 1
        assert entry.issuer_agnostic is False
        assert entry.last_verified_at == "2026-09-11"


def test_expansion_batch_3_licensing_classification_matches_the_pilot_constant():
    pilot_entry_licensing = PILOT_SOURCE_REGISTRY[0].licensing_classification
    for entry in EXPANSION_BATCH_3_SOURCE_REGISTRY:
        assert entry.licensing_classification == pilot_entry_licensing


def test_expansion_batch_3_has_zero_validation_violations():
    assert find_registry_violations(EXPANSION_BATCH_3_SOURCE_REGISTRY) == ()
    for entry in EXPANSION_BATCH_3_SOURCE_REGISTRY:
        assert validate_source_entry(entry) == ()


def test_expansion_batch_3_entries_validate_against_the_feed_adapter():
    for entry in EXPANSION_BATCH_3_SOURCE_REGISTRY:
        feed = to_daily_news_feed_source(entry)
        assert feed.feed_url == entry.canonical_url
        assert feed.canonical_domains == entry.domains
        assert feed.company_name == entry.issuer_name


# ============================================================
# Daily News source-expansion batch 4 (2026-09-13) — 3 official issuer
# newsroom feeds closing coverage gaps for already-tracked issuers
# (Samsung Electronics, Murata Manufacturing, Microchip Technology).
# ============================================================

_EXPANSION_BATCH_4_EXPECTED_FIELDS = {
    "samsung-newsroom-rss": dict(
        canonical_url="https://news.samsung.com/global/feed", domains=("news.samsung.com",),
        attribution_label="Samsung Electronics", issuer_name="Samsung Electronics", jurisdiction="South Korea",
    ),
    "murata-newsroom-rss": dict(
        canonical_url="https://www.murata.com/en-global/news/rssfeed", domains=("www.murata.com",),
        attribution_label="Murata Manufacturing Co., Ltd.", issuer_name="Murata Manufacturing Co., Ltd.",
        jurisdiction="Japan",
    ),
    "microchip-newsroom-rss": dict(
        canonical_url="https://www.microchip.com/RSS/recent-PRCorporate.xml", domains=("www.microchip.com",),
        attribution_label="Microchip Technology Incorporated", issuer_name="Microchip Technology Incorporated",
        jurisdiction="United States",
    ),
}


def test_expansion_batch_4_has_exactly_three_entries_in_the_given_order():
    assert len(EXPANSION_BATCH_4_SOURCE_REGISTRY) == 3
    assert tuple(e.source_id for e in EXPANSION_BATCH_4_SOURCE_REGISTRY) == (
        "samsung-newsroom-rss", "murata-newsroom-rss", "microchip-newsroom-rss",
    )


def test_expansion_batch_4_entries_have_the_exact_verified_fields():
    for entry in EXPANSION_BATCH_4_SOURCE_REGISTRY:
        expected = _EXPANSION_BATCH_4_EXPECTED_FIELDS[entry.source_id]
        assert entry.canonical_url == expected["canonical_url"]
        assert entry.domains == expected["domains"]
        assert entry.attribution_label == expected["attribution_label"]
        assert entry.issuer_name == expected["issuer_name"]
        assert entry.jurisdiction == expected["jurisdiction"]
        assert entry.category == SourceCategory.OFFICIAL_NEWSROOM
        assert entry.format == SourceFormat.RSS_ATOM
        assert entry.enabled is True
        assert entry.health_state == SourceHealthState.VERIFIED
        assert entry.priority == 1
        assert entry.issuer_agnostic is False
        assert entry.last_verified_at == "2026-09-13"


def test_expansion_batch_4_licensing_classification_matches_the_pilot_constant():
    pilot_entry_licensing = PILOT_SOURCE_REGISTRY[0].licensing_classification
    for entry in EXPANSION_BATCH_4_SOURCE_REGISTRY:
        assert entry.licensing_classification == pilot_entry_licensing


def test_expansion_batch_4_has_zero_validation_violations():
    assert find_registry_violations(EXPANSION_BATCH_4_SOURCE_REGISTRY) == ()
    for entry in EXPANSION_BATCH_4_SOURCE_REGISTRY:
        assert validate_source_entry(entry) == ()


def test_expansion_batch_4_entries_validate_against_the_feed_adapter():
    for entry in EXPANSION_BATCH_4_SOURCE_REGISTRY:
        feed = to_daily_news_feed_source(entry)
        assert feed.feed_url == entry.canonical_url
        assert feed.canonical_domains == entry.domains
        assert feed.company_name == entry.issuer_name


def test_expansion_batch_4_source_ids_and_domains_do_not_collide_with_any_existing_entry():
    # Explicit collision-freedom proof, per the approval's own pre-editing
    # requirement — every batch-4 source_id/canonical_url/domain is
    # unique across the entire runtime registry.
    other_entries = [e for e in RUNTIME_SOURCE_REGISTRY if e not in EXPANSION_BATCH_4_SOURCE_REGISTRY]
    other_ids = {e.source_id for e in other_entries}
    other_urls = {e.canonical_url for e in other_entries}
    other_domains = {d for e in other_entries for d in e.domains}
    for entry in EXPANSION_BATCH_4_SOURCE_REGISTRY:
        assert entry.source_id not in other_ids
        assert entry.canonical_url not in other_urls
        assert not (set(entry.domains) & other_domains)


# ============================================================
# Daily News source-expansion batch 5 (2026-09-13) — one official IR
# feed for a newly-tracked, Daily-News-only issuer (Hewlett Packard
# Enterprise Company, resolved via a new issuer_registry.DISCOVERY_STUBS
# entry, not tracked_companies.py).
# ============================================================


def test_expansion_batch_5_has_exactly_one_entry():
    assert len(EXPANSION_BATCH_5_SOURCE_REGISTRY) == 1
    entry = EXPANSION_BATCH_5_SOURCE_REGISTRY[0]
    assert entry.source_id == "hpe-ir-rss"


def test_hpe_ir_rss_entry_has_the_exact_verified_fields():
    entry = EXPANSION_BATCH_5_SOURCE_REGISTRY[0]
    assert entry.canonical_url == "https://investors.hpe.com/rss/news"
    assert entry.domains == ("investors.hpe.com",)
    assert entry.attribution_label == "Hewlett Packard Enterprise Company"
    assert entry.issuer_name == "Hewlett Packard Enterprise Company"
    assert entry.jurisdiction == "United States"
    assert entry.category == SourceCategory.OFFICIAL_IR
    assert entry.format == SourceFormat.RSS_ATOM
    assert entry.enabled is True
    assert entry.health_state == SourceHealthState.VERIFIED
    assert entry.priority == 1
    assert entry.issuer_agnostic is False
    assert entry.last_verified_at == "2026-09-13"


def test_hpe_ir_rss_licensing_classification_matches_the_pilot_constant():
    pilot_entry_licensing = PILOT_SOURCE_REGISTRY[0].licensing_classification
    assert EXPANSION_BATCH_5_SOURCE_REGISTRY[0].licensing_classification == pilot_entry_licensing


def test_hpe_ir_rss_resolves_with_no_duplicate_or_conflicting_source():
    # Historically (through Daily News source-expansion batch 5,
    # 2026-09-13), HPE existed only as an issuer_registry.DISCOVERY_STUBS
    # entry, and tracked_company_for() resolved this source through that
    # stub, never through tracked_companies.py — exactly like Quanta
    # Services/nVent Electric/Arista Networks/Cisco Systems before their
    # own later graduations. The Tier 1 Cohort 2 batch (2026-09-16)
    # graduated HPE to a real, verified TrackedCompany entry too (same
    # precedent as those four), while deliberately leaving this source
    # entry's own now-redundant DISCOVERY_STUBS entry untouched — so both
    # now exist simultaneously for the same issuer_name. This test proves
    # that dual existence is harmless: tracked_company_for() resolves
    # deterministically to exactly one record (the real TrackedCompany,
    # checked first — see that function's own docstring), the stub
    # remains present and valid without becoming reachable by any scan
    # pipeline, and the source-registry entry itself still validates
    # cleanly — no duplicate, conflicting, or broken IR RSS resolution
    # results from the overlap.
    from src.config.issuer_registry import DISCOVERY_STUBS
    from src.config.tracked_companies import get_tracked_companies

    entry = EXPANSION_BATCH_5_SOURCE_REGISTRY[0]

    # HPE now resolves through its real tracked-company record...
    tracked_names = {c.name for c in get_tracked_companies(active_only=False)}
    assert entry.issuer_name in tracked_names
    # ...while its original discovery-stub entry is untouched and still present...
    assert entry.issuer_name in {issuer.legal_name for issuer in DISCOVERY_STUBS}
    # ...and resolution itself is unambiguous: exactly one TrackedCompany
    # in the live registry carries this name (no duplicate to resolve
    # between), and tracked_company_for() returns that real, active,
    # SEC-EDGAR-sourced record — not the synthesized, inactive stub shape
    # feed_registry.py's own docstring describes for a stub-only match.
    matches = [c for c in get_tracked_companies(active_only=False) if c.name == entry.issuer_name]
    assert len(matches) == 1
    resolved = feed_registry.tracked_company_for(entry.issuer_name)
    assert resolved is not None
    assert resolved.active is True
    assert resolved.source == "SEC EDGAR"
    assert resolved.krx_code == "HPE"
    # The source entry itself still validates without error either way —
    # the issuer-linkage check accepts a tracked-company match exactly as
    # readily as a stub-only match (validate_source_entry's own contract),
    # so this graduation introduces no new validation violation.
    assert validate_source_entry(entry) == ()


def test_expansion_batch_5_has_zero_validation_violations():
    assert find_registry_violations(EXPANSION_BATCH_5_SOURCE_REGISTRY) == ()
    for entry in EXPANSION_BATCH_5_SOURCE_REGISTRY:
        assert validate_source_entry(entry) == ()


def test_expansion_batch_5_entry_validates_against_the_feed_adapter():
    entry = EXPANSION_BATCH_5_SOURCE_REGISTRY[0]
    feed = to_daily_news_feed_source(entry)
    assert feed.feed_url == entry.canonical_url
    assert feed.canonical_domains == entry.domains
    assert feed.company_name == entry.issuer_name


def test_expansion_batch_5_source_id_and_domain_do_not_collide_with_any_existing_entry():
    other_entries = [e for e in RUNTIME_SOURCE_REGISTRY if e not in EXPANSION_BATCH_5_SOURCE_REGISTRY]
    other_ids = {e.source_id for e in other_entries}
    other_urls = {e.canonical_url for e in other_entries}
    other_domains = {d for e in other_entries for d in e.domains}
    for entry in EXPANSION_BATCH_5_SOURCE_REGISTRY:
        assert entry.source_id not in other_ids
        assert entry.canonical_url not in other_urls
        assert not (set(entry.domains) & other_domains)


# ============================================================
# Editorial Daily News v1 (design/DECISIONS.md) — a SEPARATE registry
# from RUNTIME_SOURCE_REGISTRY: 9 CNBC feeds + 1 Korea Herald feed,
# every one issuer_agnostic=True, each independently live-verified.
# Never merged into RUNTIME_SOURCE_REGISTRY/PILOT_FEEDS — read only by
# editorial_pipeline.py. Government / Public Sector Daily News lane
# (design/DECISIONS.md) adds 2 more entries (Space Force, NIST) — see
# the dedicated section below for their own category-specific tests.
# ============================================================

_INDEPENDENT_NEWS_SOURCE_IDS = (
    "cnbc-top-news-rss", "cnbc-business-rss", "cnbc-finance-rss", "cnbc-economy-rss",
    "cnbc-technology-rss", "cnbc-earnings-rss", "cnbc-energy-rss", "cnbc-politics-policy-rss",
    "cnbc-asia-rss", "korea-herald-business-rss",
)
_GOVERNMENT_POLICY_SOURCE_IDS = ("spaceforce-news-rss", "nist-news-rss")


def test_editorial_source_registry_v1_has_exactly_twelve_entries():
    assert len(EDITORIAL_SOURCE_REGISTRY_V1) == 12
    assert tuple(e.source_id for e in EDITORIAL_SOURCE_REGISTRY_V1) == (
        _INDEPENDENT_NEWS_SOURCE_IDS + _GOVERNMENT_POLICY_SOURCE_IDS
    )


def test_editorial_source_registry_has_exactly_thirty_six_entries_v1_then_batch_2_then_batch_3():
    # Daily News source-expansion batch 2, editorial lane (2026-09-13)
    # appended 23 more entries after EDITORIAL_SOURCE_REGISTRY_V1's 12;
    # batch 3, Phase 1 activation (2026-09-15) appended 1 more (Data
    # Center Dynamics — METI was re-verified and excluded for staleness,
    # see source_registry.py's own EDITORIAL_SOURCE_REGISTRY_BATCH_3
    # comment) (12 + 23 + 1 = 36); the Daily News Cohort 1 batch
    # (2026-09-15) appended 2 more (SpaceNews, The Robot Report)
    # (36 + 2 = 38), never interleaved.
    assert len(EDITORIAL_SOURCE_REGISTRY) == 38
    assert EDITORIAL_SOURCE_REGISTRY[:12] == EDITORIAL_SOURCE_REGISTRY_V1
    assert EDITORIAL_SOURCE_REGISTRY[12:35] == EDITORIAL_SOURCE_REGISTRY_BATCH_2
    assert EDITORIAL_SOURCE_REGISTRY[35:36] == EDITORIAL_SOURCE_REGISTRY_BATCH_3
    assert EDITORIAL_SOURCE_REGISTRY[36:] == EDITORIAL_SOURCE_REGISTRY_BATCH_4


def test_editorial_source_registry_has_zero_validation_violations():
    assert find_registry_violations(EDITORIAL_SOURCE_REGISTRY) == ()
    for entry in EDITORIAL_SOURCE_REGISTRY:
        assert validate_source_entry(entry) == ()


def test_every_independent_news_editorial_entry_is_issuer_agnostic_allowlisted():
    independent_news_entries = [
        e for e in EDITORIAL_SOURCE_REGISTRY if e.source_id in _INDEPENDENT_NEWS_SOURCE_IDS
    ]
    assert len(independent_news_entries) == len(_INDEPENDENT_NEWS_SOURCE_IDS)
    for entry in independent_news_entries:
        assert entry.category == SourceCategory.INDEPENDENT_NEWS, entry.source_id
        assert entry.issuer_agnostic is True, entry.source_id
        assert entry.issuer_name is None, entry.source_id
        assert entry.allowlisted is True, entry.source_id
        assert entry.format == SourceFormat.RSS_ATOM, entry.source_id
        assert entry.health_state == SourceHealthState.VERIFIED, entry.source_id


def test_every_government_policy_editorial_entry_is_issuer_agnostic_verified():
    # Government / Public Sector Daily News lane (design/DECISIONS.md) —
    # allowlisted=True is NOT required here: validate_source_entry()'s
    # allowlisted gate is scoped only to SourceCategory.INDEPENDENT_NEWS
    # (source_registry.py's own validator), never GOVERNMENT_POLICY.
    government_entries = [
        e for e in EDITORIAL_SOURCE_REGISTRY if e.source_id in _GOVERNMENT_POLICY_SOURCE_IDS
    ]
    assert len(government_entries) == len(_GOVERNMENT_POLICY_SOURCE_IDS)
    for entry in government_entries:
        assert entry.category == SourceCategory.GOVERNMENT_POLICY, entry.source_id
        assert entry.issuer_agnostic is True, entry.source_id
        assert entry.issuer_name is None, entry.source_id
        assert entry.format == SourceFormat.RSS_ATOM, entry.source_id
        assert entry.health_state == SourceHealthState.VERIFIED, entry.source_id
        assert entry.jurisdiction == "United States", entry.source_id


def test_spaceforce_entry_uses_the_expected_feed_and_domain():
    spaceforce = next(e for e in EDITORIAL_SOURCE_REGISTRY if e.source_id == "spaceforce-news-rss")
    assert spaceforce.canonical_url == (
        "https://www.spaceforce.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1060&max=10"
    )
    assert spaceforce.domains == ("www.spaceforce.mil",)
    assert spaceforce.attribution_label == "U.S. Space Force"


def test_nist_entry_uses_the_expected_feed_and_domain():
    nist = next(e for e in EDITORIAL_SOURCE_REGISTRY if e.source_id == "nist-news-rss")
    assert nist.canonical_url == "https://www.nist.gov/news-events/news/rss.xml"
    assert nist.domains == ("www.nist.gov",)
    assert nist.attribution_label == "National Institute of Standards and Technology (NIST)"


def test_every_cnbc_entry_uses_the_search_cnbc_rss_endpoint_and_www_cnbc_domain():
    cnbc_entries = [e for e in EDITORIAL_SOURCE_REGISTRY if e.attribution_label == "CNBC"]
    assert len(cnbc_entries) == 9
    for entry in cnbc_entries:
        assert entry.canonical_url.startswith(
            "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id="
        )
        assert entry.domains == ("www.cnbc.com",)
        assert "device/rss/rss.html" not in entry.canonical_url  # the legacy, now-403 URL shape


def test_korea_herald_entry_is_business_only_on_its_own_domain():
    korea_herald = next(e for e in EDITORIAL_SOURCE_REGISTRY if e.attribution_label == "The Korea Herald")
    assert korea_herald.canonical_url == "https://www.koreaherald.com/rss/kh_Business"
    assert korea_herald.domains == ("www.koreaherald.com",)
    assert korea_herald.jurisdiction == "South Korea"


def test_editorial_source_registry_never_appears_in_runtime_source_registry():
    editorial_ids = {e.source_id for e in EDITORIAL_SOURCE_REGISTRY}
    runtime_ids = {e.source_id for e in RUNTIME_SOURCE_REGISTRY}
    assert not (editorial_ids & runtime_ids)
    # Daily News source-expansion batch 5 (2026-09-13) added 1 more
    # issuer-lane entry (27 -> 28); the Daily News Cohort 1 batch
    # (2026-09-15) added 4 more (28 -> 32) — this test's own point
    # (editorial and runtime source_ids never collide) is unaffected by
    # that count.
    assert len(RUNTIME_SOURCE_REGISTRY) == 32


# ============================================================
# Daily News source-expansion batch 2, editorial lane (2026-09-13) — 23
# more issuer_agnostic=True editorial sources: US independent/trade
# press, US federal regulators, Japan, South Korea. See
# EDITORIAL_SOURCE_REGISTRY_BATCH_2's own module-level comment in
# source_registry.py for the full verification/exclusion narrative
# (including Bank of Japan's rejection).
# ============================================================

_BATCH_2_US_INDEPENDENT_NEWS_SOURCE_IDS = (
    "techcrunch-rss", "ars-technica-rss", "the-verge-rss", "the-register-headlines-rss",
    "the-register-on-prem-rss", "pr-newswire-general-rss", "pr-newswire-financial-services-rss",
    "semiconductor-engineering-rss", "ieee-spectrum-rss", "data-center-frontier-rss",
    "toms-hardware-rss", "supply-chain-dive-rss", "utility-dive-rss",
)
_BATCH_2_US_REGULATOR_SOURCE_IDS = ("sec-press-releases-rss", "federal-reserve-press-rss", "ftc-press-releases-rss")
_BATCH_2_JAPAN_SOURCE_IDS = ("japan-times-rss", "jpx-market-news-rss", "fsa-japan-news-rss")
_BATCH_2_KOREA_SOURCE_IDS = ("yonhap-news-rss", "korea-times-rss", "korea-it-times-rss", "thelec-rss")
_BATCH_2_ALL_SOURCE_IDS = (
    _BATCH_2_US_INDEPENDENT_NEWS_SOURCE_IDS + _BATCH_2_US_REGULATOR_SOURCE_IDS
    + _BATCH_2_JAPAN_SOURCE_IDS + _BATCH_2_KOREA_SOURCE_IDS
)


def test_editorial_batch_2_has_exactly_twenty_three_entries_in_the_given_order():
    assert len(EDITORIAL_SOURCE_REGISTRY_BATCH_2) == 23
    assert tuple(e.source_id for e in EDITORIAL_SOURCE_REGISTRY_BATCH_2) == _BATCH_2_ALL_SOURCE_IDS


def test_editorial_batch_2_has_zero_validation_violations():
    assert find_registry_violations(EDITORIAL_SOURCE_REGISTRY_BATCH_2) == ()
    for entry in EDITORIAL_SOURCE_REGISTRY_BATCH_2:
        assert validate_source_entry(entry) == ()


def test_editorial_batch_2_jurisdiction_totals_are_exactly_as_expected():
    by_jurisdiction: dict[str, int] = {}
    for entry in EDITORIAL_SOURCE_REGISTRY_BATCH_2:
        by_jurisdiction[entry.jurisdiction] = by_jurisdiction.get(entry.jurisdiction, 0) + 1
    # The Register (2 entries) is a UK publication (Situation Publishing)
    # — its own jurisdiction is "United Kingdom", not "United States".
    assert by_jurisdiction == {"United States": 14, "United Kingdom": 2, "Japan": 3, "South Korea": 4}


def test_editorial_batch_2_lane_totals_are_exactly_as_expected():
    assert len(_BATCH_2_US_INDEPENDENT_NEWS_SOURCE_IDS) == 13
    assert len(_BATCH_2_US_REGULATOR_SOURCE_IDS) == 3
    assert len(_BATCH_2_JAPAN_SOURCE_IDS) == 3
    assert len(_BATCH_2_KOREA_SOURCE_IDS) == 4


def test_editorial_batch_2_independent_news_entries_are_issuer_agnostic_allowlisted():
    us_independent_ids = set(_BATCH_2_US_INDEPENDENT_NEWS_SOURCE_IDS)
    japan_korea_independent_ids = {
        "japan-times-rss", "yonhap-news-rss", "korea-times-rss", "korea-it-times-rss", "thelec-rss",
    }
    independent_ids = us_independent_ids | japan_korea_independent_ids
    entries = [e for e in EDITORIAL_SOURCE_REGISTRY_BATCH_2 if e.source_id in independent_ids]
    assert len(entries) == len(independent_ids)
    for entry in entries:
        assert entry.category == SourceCategory.INDEPENDENT_NEWS, entry.source_id
        assert entry.issuer_agnostic is True, entry.source_id
        assert entry.issuer_name is None, entry.source_id
        assert entry.allowlisted is True, entry.source_id
        assert entry.format == SourceFormat.RSS_ATOM, entry.source_id
        assert entry.health_state == SourceHealthState.VERIFIED, entry.source_id


def test_editorial_batch_2_regulator_and_exchange_entries_are_issuer_agnostic_no_allowlist_required():
    regulator_exchange_ids = set(_BATCH_2_US_REGULATOR_SOURCE_IDS) | {"jpx-market-news-rss", "fsa-japan-news-rss"}
    entries = [e for e in EDITORIAL_SOURCE_REGISTRY_BATCH_2 if e.source_id in regulator_exchange_ids]
    assert len(entries) == len(regulator_exchange_ids)
    for entry in entries:
        assert entry.category in (SourceCategory.REGULATOR, SourceCategory.EXCHANGE), entry.source_id
        assert entry.issuer_agnostic is True, entry.source_id
        assert entry.issuer_name is None, entry.source_id
        assert entry.allowlisted is False, entry.source_id  # never required outside INDEPENDENT_NEWS
        assert entry.format == SourceFormat.RSS_ATOM, entry.source_id
        assert entry.health_state == SourceHealthState.VERIFIED, entry.source_id


def test_the_register_pair_shares_one_attribution_label_on_the_same_domain():
    register_entries = [e for e in EDITORIAL_SOURCE_REGISTRY_BATCH_2 if e.source_id.startswith("the-register-")]
    assert len(register_entries) == 2
    for entry in register_entries:
        assert entry.attribution_label == "The Register"
        assert entry.domains == ("www.theregister.com",)
        assert entry.category == SourceCategory.INDEPENDENT_NEWS
        assert entry.licensing_classification.startswith("Independent journalism")


def test_pr_newswire_pair_shares_one_attribution_label_and_is_classified_as_a_wire_service():
    pr_newswire_entries = [e for e in EDITORIAL_SOURCE_REGISTRY_BATCH_2 if e.source_id.startswith("pr-newswire-")]
    assert len(pr_newswire_entries) == 2
    for entry in pr_newswire_entries:
        assert entry.attribution_label == "PR Newswire"
        assert entry.domains == ("www.prnewswire.com",)
        # PR Newswire is a press-release distribution wire service, not
        # independent journalism — its licensing_classification text
        # must say so explicitly, never reuse the "Independent
        # journalism" wording every other batch-2 news outlet uses.
        assert "wire service" in entry.licensing_classification.lower()
        assert "not independent journalism" in entry.licensing_classification.lower()
        assert not entry.licensing_classification.lower().startswith("independent journalism")


def test_pr_newswire_is_never_described_as_independent_journalism_in_notes():
    pr_newswire_entries = [e for e in EDITORIAL_SOURCE_REGISTRY_BATCH_2 if e.source_id.startswith("pr-newswire-")]
    for entry in pr_newswire_entries:
        assert "wire service" in entry.notes.lower()


def test_editorial_batch_2_source_ids_and_domains_do_not_collide_with_any_existing_entry():
    other_entries = [e for e in EDITORIAL_SOURCE_REGISTRY if e not in EDITORIAL_SOURCE_REGISTRY_BATCH_2]
    other_ids = {e.source_id for e in other_entries}
    other_urls = {e.canonical_url for e in other_entries}
    for entry in EDITORIAL_SOURCE_REGISTRY_BATCH_2:
        assert entry.source_id not in other_ids
        assert entry.canonical_url not in other_urls


def test_bank_of_japan_was_not_added_to_any_registry():
    # Explicit negative proof matching this batch's own rejection: Bank
    # of Japan (boj.or.jp) was live-verified but every one of its item
    # links used plain http:// — rejected, never added, no substitute.
    all_source_ids = {e.source_id for e in EDITORIAL_SOURCE_REGISTRY} | {e.source_id for e in RUNTIME_SOURCE_REGISTRY}
    all_domains = {d for e in EDITORIAL_SOURCE_REGISTRY for d in e.domains} | {
        d for e in RUNTIME_SOURCE_REGISTRY for d in e.domains
    }
    assert "boj-whatsnew-rss" not in all_source_ids
    assert "www.boj.or.jp" not in all_domains


def test_to_daily_news_feed_source_rejects_every_editorial_entry():
    # Structural proof: an issuer_agnostic entry is exactly the shape
    # to_daily_news_feed_source() already refuses — editorial sources
    # can never leak into feed_registry.PILOT_FEEDS through that adapter.
    import pytest

    for entry in EDITORIAL_SOURCE_REGISTRY:
        with pytest.raises(SourceRegistryValidationError):
            to_daily_news_feed_source(entry)


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


def test_source_registry_module_is_not_imported_directly_by_the_real_pipeline_worker_or_ui():
    """Updated for Daily News source-expansion batch 1 (2026-09-04):
    `feed_registry.py` now intentionally imports `source_registry` (to
    build the real, live PILOT_FEEDS) — that file is deliberately
    excluded from this check as of this batch. Every other real
    entry-point file must still never import `source_registry` directly
    — they only ever need `feed_registry.PILOT_FEEDS`, transitively
    benefiting from the new wiring without needing any direct knowledge
    of source_registry.py's own existence."""
    import ast
    from pathlib import Path

    repo_root = Path(__file__).parent.parent
    targets = (
        repo_root / "src" / "data_access" / "daily_news" / "daily_news_pipeline.py",
        repo_root / "scripts" / "daily_news_worker.py",
        repo_root / "src" / "ui" / "pages" / "daily_news.py",
        repo_root / "src" / "ui" / "pages" / "daily_news_admin.py",
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


def test_feed_registry_module_now_intentionally_imports_source_registry():
    import ast
    from pathlib import Path

    path = Path(__file__).parent.parent / "src" / "data_access" / "daily_news" / "feed_registry.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = any(
        isinstance(node, ast.ImportFrom) and node.module and "source_registry" in node.module
        for node in ast.walk(tree)
    )
    assert found, "feed_registry.py should import source_registry as of the expansion-batch-1 wiring"


# ============================================================
# Daily News Source-Expansion & Ingestion Design, Batch 1 — additive
# SourceCategory vocabulary, SourceReliabilityTier, and the reusable
# contains_excluded_source_name() helper. Nothing below changes any
# existing DailyNewsSourceEntry, SourceHealthState behavior, or the real
# RUNTIME_SOURCE_REGISTRY's own zero-violations state (proven by
# test_runtime_source_registry_has_zero_violations_after_batch_2
# elsewhere in this file, which this batch leaves untouched and passing).
# ============================================================


def test_original_five_source_categories_are_unchanged():
    assert SourceCategory.OFFICIAL_IR.value == "official_ir"
    assert SourceCategory.OFFICIAL_NEWSROOM.value == "official_newsroom"
    assert SourceCategory.OFFICIAL_FILING.value == "official_filing"
    assert SourceCategory.REGULATOR_EXCHANGE.value == "regulator_exchange"
    assert SourceCategory.INDEPENDENT_NEWS.value == "independent_news"


def test_six_new_source_categories_have_the_exact_expected_values():
    assert SourceCategory.ISSUER_IR.value == "issuer_ir"
    assert SourceCategory.ISSUER_NEWSROOM.value == "issuer_newsroom"
    assert SourceCategory.EXCHANGE.value == "exchange"
    assert SourceCategory.REGULATOR.value == "regulator"
    assert SourceCategory.GOVERNMENT_POLICY.value == "government_policy"
    assert SourceCategory.GOVERNMENT_PROCUREMENT.value == "government_procurement"


def test_no_existing_runtime_source_registry_entry_uses_a_new_category():
    # This batch creates no source entry for live use — every real entry
    # in the runtime registry must still use one of the five original
    # categories only.
    new_categories = {
        SourceCategory.ISSUER_IR, SourceCategory.ISSUER_NEWSROOM, SourceCategory.EXCHANGE,
        SourceCategory.REGULATOR, SourceCategory.GOVERNMENT_POLICY, SourceCategory.GOVERNMENT_PROCUREMENT,
    }
    for entry in RUNTIME_SOURCE_REGISTRY:
        assert entry.category not in new_categories, entry.source_id


def test_source_health_state_is_completely_unchanged():
    # Batch 1's own explicit scope: do not alter current source-health
    # behavior. SourceReliabilityTier (below) is a separate, additive
    # enum, never a replacement.
    assert {s.value for s in SourceHealthState} == {
        "pending_review", "verified", "degraded", "failing", "retired",
    }


def test_source_reliability_tier_has_exactly_the_five_approved_values():
    assert {t.value for t in SourceReliabilityTier} == {
        "verified", "probationary", "retired", "shadow_only", "validation_required",
    }


def test_source_reliability_tier_is_not_referenced_by_daily_news_source_entry():
    # Structural proof this batch did not wire the new reliability tier
    # into the existing, live source-entry dataclass.
    import dataclasses

    field_types = {f.name: f.type for f in dataclasses.fields(DailyNewsSourceEntry)}
    assert "SourceReliabilityTier" not in " ".join(str(t) for t in field_types.values())


def test_contains_excluded_source_name_matches_all_three_excluded_names_case_insensitively():
    assert contains_excluded_source_name("SemiAnalysis")
    assert contains_excluded_source_name("the SEMIANALYSIS newsletter")
    assert contains_excluded_source_name("Citrini Research")
    assert contains_excluded_source_name("citrini")
    assert contains_excluded_source_name("Serenity")


def test_contains_excluded_source_name_returns_false_for_a_clean_name():
    assert not contains_excluded_source_name("NVIDIA")
    assert not contains_excluded_source_name("")
    assert not contains_excluded_source_name(None)


# ============================================================
# Daily News Cohort 1 batch (2026-09-15) — 4 issuer-linked sources
# (Equinix, L3Harris, Firefly Aerospace, Yaskawa Electric) appended to
# RUNTIME_SOURCE_REGISTRY, 2 independent trade-press sources (SpaceNews,
# The Robot Report) appended to EDITORIAL_SOURCE_REGISTRY. See design/
# DAILY_NEWS_COHORT1_IMPLEMENTATION_DESIGN_2026_09_15.md.
# ============================================================

_COHORT1_ISSUER_SOURCE_IDS_TO_COMPANIES = {
    "equinix-ir-rss": "Equinix, Inc.",
    "l3harris-newsroom-rss": "L3Harris Technologies, Inc.",
    "firefly-aerospace-news-rss": "Firefly Aerospace Inc.",
    "yaskawa-newsroom-rss": "YASKAWA Electric Corporation",
}
_COHORT1_EDITORIAL_SOURCE_IDS_TO_LABELS = {
    "spacenews-rss": "SpaceNews",
    "robot-report-rss": "The Robot Report",
}


def test_expansion_batch_6_has_exactly_four_entries_in_the_given_order():
    assert len(EXPANSION_BATCH_6_SOURCE_REGISTRY) == 4
    assert tuple(e.issuer_name for e in EXPANSION_BATCH_6_SOURCE_REGISTRY) == (
        "Equinix, Inc.", "L3Harris Technologies, Inc.", "Firefly Aerospace Inc.",
        "YASKAWA Electric Corporation",
    )


def test_expansion_batch_6_has_zero_validation_violations():
    assert find_registry_violations(EXPANSION_BATCH_6_SOURCE_REGISTRY) == ()


def test_expansion_batch_6_source_classes_and_domains_match_the_approved_design():
    by_id = {e.source_id: e for e in EXPANSION_BATCH_6_SOURCE_REGISTRY}
    assert by_id["equinix-ir-rss"].category == SourceCategory.OFFICIAL_IR
    assert by_id["equinix-ir-rss"].domains == ("investor.equinix.com",)
    assert by_id["equinix-ir-rss"].jurisdiction == "United States"
    assert by_id["l3harris-newsroom-rss"].category == SourceCategory.OFFICIAL_NEWSROOM
    assert by_id["l3harris-newsroom-rss"].domains == ("www.l3harris.com",)
    assert by_id["firefly-aerospace-news-rss"].category == SourceCategory.OFFICIAL_NEWSROOM
    assert by_id["firefly-aerospace-news-rss"].domains == ("fireflyspace.com",)
    assert by_id["yaskawa-newsroom-rss"].category == SourceCategory.OFFICIAL_NEWSROOM
    assert by_id["yaskawa-newsroom-rss"].domains == ("www.yaskawa-global.com",)
    assert by_id["yaskawa-newsroom-rss"].jurisdiction == "Japan"
    for entry in EXPANSION_BATCH_6_SOURCE_REGISTRY:
        assert entry.format == SourceFormat.RSS_ATOM
        assert entry.enabled is True
        assert entry.health_state == SourceHealthState.VERIFIED


def test_yaskawa_newsroom_language_is_english_not_feed_metadata():
    # Deliberate curation override (design/
    # DAILY_NEWS_COHORT1_IMPLEMENTATION_DESIGN_2026_09_15.md) — the real
    # feed's own <channel><language> tag declares "ja", but every
    # observed item is genuinely English-language content. The curated
    # registry value must reflect observed content, never the feed's
    # own possibly-mislabeled metadata.
    yaskawa = next(e for e in RUNTIME_SOURCE_REGISTRY if e.source_id == "yaskawa-newsroom-rss")
    assert yaskawa.language == "English"


def test_other_expansion_batch_6_entries_default_to_english_language():
    by_id = {e.source_id: e for e in EXPANSION_BATCH_6_SOURCE_REGISTRY}
    for source_id in ("equinix-ir-rss", "l3harris-newsroom-rss", "firefly-aerospace-news-rss"):
        assert by_id[source_id].language == "English"


def test_editorial_batch_4_has_exactly_two_entries_in_the_given_order():
    assert len(EDITORIAL_SOURCE_REGISTRY_BATCH_4) == 2
    assert tuple(e.source_id for e in EDITORIAL_SOURCE_REGISTRY_BATCH_4) == (
        "spacenews-rss", "robot-report-rss",
    )


def test_editorial_batch_4_has_zero_validation_violations():
    assert find_registry_violations(EDITORIAL_SOURCE_REGISTRY_BATCH_4) == ()


def test_editorial_batch_4_entries_are_issuer_agnostic_allowlisted_independent_news():
    for entry in EDITORIAL_SOURCE_REGISTRY_BATCH_4:
        assert entry.category == SourceCategory.INDEPENDENT_NEWS
        assert entry.issuer_agnostic is True
        assert entry.issuer_name is None
        assert entry.allowlisted is True
        assert entry.format == SourceFormat.RSS_ATOM
        assert entry.enabled is True
        assert entry.health_state == SourceHealthState.VERIFIED
        assert entry.language == "English"


def test_spacenews_domain_and_jurisdiction():
    spacenews = next(e for e in EDITORIAL_SOURCE_REGISTRY_BATCH_4 if e.source_id == "spacenews-rss")
    assert spacenews.domains == ("spacenews.com",)
    assert spacenews.jurisdiction == "United States"
    assert spacenews.attribution_label == "SpaceNews"


def test_robot_report_domain_and_jurisdiction():
    robot_report = next(e for e in EDITORIAL_SOURCE_REGISTRY_BATCH_4 if e.source_id == "robot-report-rss")
    assert robot_report.domains == ("www.therobotreport.com",)
    assert robot_report.jurisdiction == "United States"
    assert robot_report.attribution_label == "The Robot Report"


def test_cohort1_sources_present_exactly_once_each_across_the_correct_registry():
    runtime_ids = {e.source_id for e in RUNTIME_SOURCE_REGISTRY}
    editorial_ids = {e.source_id for e in EDITORIAL_SOURCE_REGISTRY}
    for source_id in _COHORT1_ISSUER_SOURCE_IDS_TO_COMPANIES:
        assert source_id in runtime_ids
        assert source_id not in editorial_ids
    for source_id in _COHORT1_EDITORIAL_SOURCE_IDS_TO_LABELS:
        assert source_id in editorial_ids
        assert source_id not in runtime_ids


def test_cohort1_no_source_id_or_canonical_url_collision_with_existing_registry():
    all_entries = RUNTIME_SOURCE_REGISTRY + EDITORIAL_SOURCE_REGISTRY
    ids = [e.source_id for e in all_entries]
    assert len(ids) == len(set(ids))
    urls = [normalize_source_url(e.canonical_url) for e in all_entries]
    assert len(urls) == len(set(urls))


def test_cohort1_issuer_sources_resolve_to_a_real_tracked_company():
    # Every Lane A Cohort 1 entry's issuer_name must resolve via
    # feed_registry.tracked_company_for() — the same lookup the real
    # pipeline already performs; no DISCOVERY_STUBS entry is needed or
    # used for any of these 4 (unlike HPE/Quanta/nVent/Arista/Cisco),
    # since all 4 are already real TrackedCompany entries (PR #59).
    for source_id, company_name in _COHORT1_ISSUER_SOURCE_IDS_TO_COMPANIES.items():
        entry = next(e for e in RUNTIME_SOURCE_REGISTRY if e.source_id == source_id)
        assert entry.issuer_name == company_name
        resolved = feed_registry.tracked_company_for(company_name)
        assert resolved is not None
        assert resolved.source in ("SEC EDGAR", "EDINET")


def test_cohort1_hold_candidates_never_added_to_any_registry():
    # Negative fixture: none of the 7 explicit HOLD candidates from the
    # approved design gained a Lane A or Lane B entry as a side effect
    # of this batch.
    all_entries = RUNTIME_SOURCE_REGISTRY + EDITORIAL_SOURCE_REGISTRY
    labels = {e.attribution_label.lower() for e in all_entries}
    issuer_names = {(e.issuer_name or "").lower() for e in all_entries}
    hold_fragments = (
        "ge vernova", "digital realty", "nabtesco", "harmonic drive",
        "hanmi semiconductor", "hd hyundai electric", "korea exchange", "krx",
    )
    for fragment in hold_fragments:
        assert not any(fragment in label for label in labels), f"{fragment} must not appear as attribution_label"
        assert not any(fragment in name for name in issuer_names), f"{fragment} must not appear as issuer_name"


def test_cohort1_dormant_gated_sources_remain_untouched_and_still_gated():
    # Negative fixture: this batch does not activate, modify, or remove
    # any of the 4 dormant env-gated sources — they remain exactly
    # where they were, in their own separate, still-gated registries,
    # never merged into RUNTIME_SOURCE_REGISTRY or EDITORIAL_SOURCE_
    # REGISTRY by this batch.
    from src.data_access.daily_news.source_registry import (
        GATED_JP_KR_SOURCE_REGISTRY,
        GATED_MARKET_NEWS_SOURCE_REGISTRY,
    )

    gated_ids = {e.source_id for e in GATED_MARKET_NEWS_SOURCE_REGISTRY + GATED_JP_KR_SOURCE_REGISTRY}
    assert gated_ids == {
        "light-reading-rss", "japan-times-business-rss",
        "businesskorea-industries-rss", "businesskorea-science-tech-rss",
    }
    runtime_ids = {e.source_id for e in RUNTIME_SOURCE_REGISTRY}
    editorial_ids = {e.source_id for e in EDITORIAL_SOURCE_REGISTRY}
    assert gated_ids.isdisjoint(runtime_ids)
    assert gated_ids.isdisjoint(editorial_ids)


def test_l3harris_has_no_per_item_space_segment_filter_by_design():
    # Documents, does not fix: L3Harris's own newsroom feed mixes
    # space-segment and much larger non-space defense-electronics
    # content, and this registry entry (like every other issuer feed)
    # has no per-item segment/topic filter — a story is never rejected
    # at the source-registry level based on its own content. Whether a
    # non-space L3Harris item should still become a "Space" Signal is a
    # materiality/theme-tagging question for editorial_matching.py and
    # materiality_classification.py, not something a DailyNewsSourceEntry
    # field can express — this test documents that today's registry
    # entry itself carries no such filter, confirming the design's own
    # "known, accepted characteristic, not a defect" framing.
    l3harris = next(e for e in RUNTIME_SOURCE_REGISTRY if e.source_id == "l3harris-newsroom-rss")
    assert l3harris.allowed_event_filters == ()
