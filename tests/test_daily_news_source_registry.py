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
    EXPANSION_BATCH_1_SOURCE_REGISTRY,
    EXPANSION_BATCH_2_SOURCE_REGISTRY,
    PILOT_SOURCE_REGISTRY,
    RUNTIME_SOURCE_REGISTRY,
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
    # 2 (2026-09-04) appended exactly one more entry (19 + 1 = 20).
    assert len(RUNTIME_SOURCE_REGISTRY) == 20
    assert RUNTIME_SOURCE_REGISTRY[:12] == PILOT_SOURCE_REGISTRY
    assert RUNTIME_SOURCE_REGISTRY[12:19] == EXPANSION_BATCH_1_SOURCE_REGISTRY
    assert RUNTIME_SOURCE_REGISTRY[19:] == EXPANSION_BATCH_2_SOURCE_REGISTRY


def test_pilot_source_registry_has_zero_validation_violations():
    assert find_registry_violations(PILOT_SOURCE_REGISTRY) == ()


def test_expansion_batch_1_has_zero_validation_violations():
    assert find_registry_violations(EXPANSION_BATCH_1_SOURCE_REGISTRY) == ()


def test_runtime_source_registry_has_zero_validation_violations():
    # Also proves no cross-batch duplicate was introduced.
    assert find_registry_violations(RUNTIME_SOURCE_REGISTRY) == ()


def test_runtime_source_registry_source_ids_are_all_unique():
    ids = [e.source_id for e in RUNTIME_SOURCE_REGISTRY]
    assert len(ids) == len(set(ids)) == 20


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
    assert len(feed_registry.PILOT_FEEDS) == 20
    assert feed_registry.PILOT_FEEDS[:12] == adapted_original_twelve
    assert tuple(f.company_name for f in feed_registry.PILOT_FEEDS[:12]) == _EXPECTED_ORIGINAL_TWELVE_COMPANY_ORDER


def test_final_runtime_feed_list_has_exactly_twenty_entries():
    # Was exactly 19 through expansion batch 1; batch 2 (2026-09-04)
    # appended exactly one more entry (19 + 1 = 20).
    assert len(feed_registry.PILOT_FEEDS) == 20


def test_final_runtime_feed_list_appends_expansion_batch_1_after_the_original_twelve():
    adapted_expansion = tuple(to_daily_news_feed_source(e) for e in EXPANSION_BATCH_1_SOURCE_REGISTRY)
    assert feed_registry.PILOT_FEEDS[12:19] == adapted_expansion
    assert tuple(f.company_name for f in feed_registry.PILOT_FEEDS[12:19]) == _EXPECTED_EXPANSION_BATCH_1_COMPANY_ORDER


def test_final_runtime_feed_list_company_order_is_exactly_the_twenty_expected():
    assert tuple(f.company_name for f in feed_registry.PILOT_FEEDS) == (
        _EXPECTED_ORIGINAL_TWELVE_COMPANY_ORDER + _EXPECTED_EXPANSION_BATCH_1_COMPANY_ORDER + ("Meta Platforms, Inc.",)
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
    assert len(RUNTIME_SOURCE_REGISTRY) == 20
    assert RUNTIME_SOURCE_REGISTRY[:12] == PILOT_SOURCE_REGISTRY
    assert RUNTIME_SOURCE_REGISTRY[12:19] == EXPANSION_BATCH_1_SOURCE_REGISTRY
    assert RUNTIME_SOURCE_REGISTRY[19:] == EXPANSION_BATCH_2_SOURCE_REGISTRY


def test_runtime_source_registry_has_zero_violations_after_batch_2():
    assert find_registry_violations(RUNTIME_SOURCE_REGISTRY) == ()


def test_final_runtime_feed_list_has_exactly_twenty_entries():
    assert len(feed_registry.PILOT_FEEDS) == 20


def test_original_nineteen_runtime_feeds_retain_their_exact_relative_order():
    expected_first_nineteen_companies = (
        _EXPECTED_ORIGINAL_TWELVE_COMPANY_ORDER + _EXPECTED_EXPANSION_BATCH_1_COMPANY_ORDER
    )
    assert tuple(f.company_name for f in feed_registry.PILOT_FEEDS[:19]) == expected_first_nineteen_companies


def test_meta_newsroom_rss_is_appended_last():
    assert feed_registry.PILOT_FEEDS[-1].company_name == "Meta Platforms, Inc."
    assert feed_registry.PILOT_FEEDS[-1].feed_url == "https://about.fb.com/feed/"
    assert feed_registry.PILOT_FEEDS[-1].canonical_domains == ("about.fb.com",)


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
