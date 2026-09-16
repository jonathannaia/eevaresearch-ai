"""Historical editorial Signals audit harness (design/DECISIONS.md,
"Signals editorial lane false-positive audit" follow-up). Every fixture
here is synthetic and directly constructed — no mock/demo data, no
stale issuer cache, no substitute for a real audit. These tests
validate the harness's own mechanics (input parsing, recomputation,
disposition classification, report output) against the real,
unmodified matching/materiality/admission functions — they do not, and
cannot, stand in for running the tool against a real historical
export."""
from __future__ import annotations

import csv
import json

import pytest

from scripts.daily_news_historical_signals_audit import (
    InputRecord,
    _parse_list_field,
    audit_record,
    audit_records,
    build_markdown_summary,
    load_records,
    write_csv_report,
    write_json_report,
    write_report,
)

_TOMS_HARDWARE = "toms-hardware-rss"
_DCD = "data-center-dynamics-rss"
_BUSINESS_KOREA = "businesskorea-industries-rss"


def _record(**overrides) -> InputRecord:
    defaults = dict(
        id="s1", headline="Some Headline", source_url="https://example.com/a",
        published_at="2026-09-01T00:00:00Z", publisher="Example Publisher", source_feed_id=_TOMS_HARDWARE,
        excerpt=None, matched_companies=(), matched_themes=(), materiality_tier="Watchlist", materiality_reasons=(),
    )
    defaults.update(overrides)
    return InputRecord(**defaults)


# ============================================================
# A: _parse_list_field — input-parsing edge cases
# ============================================================


def test_parse_list_field_accepts_a_real_list():
    assert _parse_list_field(["A", "B"]) == ("A", "B")


def test_parse_list_field_accepts_a_json_encoded_string():
    assert _parse_list_field('["A", "B"]') == ("A", "B")


def test_parse_list_field_accepts_a_semicolon_delimited_string():
    assert _parse_list_field("A; B; C") == ("A", "B", "C")


def test_parse_list_field_accepts_a_pipe_delimited_string():
    assert _parse_list_field("A|B|C") == ("A", "B", "C")


def test_parse_list_field_accepts_a_single_bare_value():
    assert _parse_list_field("A") == ("A",)


def test_parse_list_field_returns_empty_for_none_and_blank():
    assert _parse_list_field(None) == ()
    assert _parse_list_field("") == ()
    assert _parse_list_field("   ") == ()


# ============================================================
# B: load_records — JSON and CSV input
# ============================================================


def test_load_records_json(tmp_path):
    path = tmp_path / "export.json"
    path.write_text(json.dumps([
        {
            "id": "s1", "headline": "Real Headline", "source_url": "https://example.com/a",
            "published_at": "2026-09-01T00:00:00Z", "publisher": "Example Publisher",
            "source_feed_id": _TOMS_HARDWARE, "excerpt": "Some excerpt.",
            "matched_companies": ["NVIDIA"], "matched_themes": [], "materiality_tier": "Watchlist",
            "materiality_reasons": ["on_taxonomy_no_anchor:x"],
        },
    ]), encoding="utf-8")

    records = load_records(path)

    assert len(records) == 1
    assert records[0].id == "s1"
    assert records[0].matched_companies == ("NVIDIA",)
    assert records[0].materiality_reasons == ("on_taxonomy_no_anchor:x",)


def test_load_records_json_rejects_a_non_array_top_level_value(tmp_path):
    path = tmp_path / "export.json"
    path.write_text(json.dumps({"id": "s1"}), encoding="utf-8")

    with pytest.raises(SystemExit):
        load_records(path)


def test_load_records_csv(tmp_path):
    path = tmp_path / "export.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "id", "headline", "source_url", "published_at", "publisher", "source_feed_id",
            "excerpt", "matched_companies", "matched_themes", "materiality_tier", "materiality_reasons",
        ])
        writer.writeheader()
        writer.writerow({
            "id": "s1", "headline": "Real Headline", "source_url": "https://example.com/a",
            "published_at": "2026-09-01T00:00:00Z", "publisher": "Example Publisher",
            "source_feed_id": _TOMS_HARDWARE, "excerpt": "Some excerpt.",
            "matched_companies": "NVIDIA;AMD", "matched_themes": "", "materiality_tier": "Watchlist",
            "materiality_reasons": "on_taxonomy_no_anchor:x",
        })

    records = load_records(path)

    assert len(records) == 1
    assert records[0].matched_companies == ("NVIDIA", "AMD")
    assert records[0].materiality_reasons == ("on_taxonomy_no_anchor:x",)


def test_load_records_rejects_an_unsupported_extension(tmp_path):
    path = tmp_path / "export.txt"
    path.write_text("id,headline\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        load_records(path)


def test_record_from_row_raises_on_missing_required_field(tmp_path):
    path = tmp_path / "export.json"
    path.write_text(json.dumps([{"id": "s1", "headline": "X"}]), encoding="utf-8")

    with pytest.raises(ValueError, match="missing required field"):
        load_records(path)


# ============================================================
# C: audit_record — recomputation and disposition, against the real
# matching/materiality/admission functions. Every fixture below mirrors
# one of the five known validation examples from the false-positive
# audit, as it would appear in a real export row (stored tags/tier
# reflecting what the OLD, pre-fix pipeline would have persisted).
# ============================================================


def test_unofficial_port_historical_mention_is_suppressed():
    """Minecraft PS2/Wii port — should not be a Microsoft signal."""
    record = _record(
        headline="Modder Rewrites Minecraft Legacy Console Engine to Run on PS2 and Wii in Just 32MB of RAM",
        excerpt=(
            "A hobbyist developer has rebuilt Minecraft's legacy console engine from scratch to run on the "
            "PlayStation 2 and Wii. Microsoft acquired Mojang, the Swedish studio behind Minecraft, for $2.5 "
            "billion in 2014, and the original console edition was later discontinued on modern platforms."
        ),
        matched_companies=("Microsoft Corporation",), materiality_tier="Background",
    )
    result = audit_record(record)
    assert result.disposition == "SUPPRESS_FROM_USER_FEED"
    assert result.admitted is False
    assert "Microsoft Corporation" not in result.recomputed_identified_companies


def test_polarise_capacity_lease_remains_high_signal_and_is_kept():
    """Polarise 15MW Prague lease — should remain High Signal."""
    record = _record(
        headline="Polarise to Lease 15MW of Data Center Capacity in Prague",
        excerpt=(
            "Polarise has signed an agreement to lease 15MW of data center capacity in Prague, expanding "
            "its footprint in Central Europe as demand for AI infrastructure grows across the region."
        ),
        source_feed_id=_DCD, publisher="Data Center Dynamics",
        matched_themes=("ai-buildout",), materiality_tier="High Signal",
        materiality_reasons=("quantified_change:capacity",),
    )
    result = audit_record(record)
    assert result.disposition == "KEEP"
    assert result.recomputed_tier == "High Signal"
    assert result.admitted is True


def test_ls_cable_strike_retains_only_evidence_supported_entity_tags():
    """LS Cable strike — retain only with evidence-supported entity
    tags. Here SK Hynix is the genuine subject (in title, announces its
    own chip) and Samsung Electronics is an incidental rival mention —
    exactly the "some tags valid, some not" shape REMOVE_INVALID_
    COMPANY_TAGS exists for."""
    record = _record(
        headline="SK Hynix Announces New HBM4 Memory Chip",
        excerpt=(
            "SK Hynix announced its new HBM4 memory chip today. Rivals in the memory market include "
            "Samsung Electronics and Micron."
        ),
        source_feed_id=_BUSINESS_KOREA, publisher="Business Korea",
        matched_companies=("SK Hynix", "Samsung Electronics"), materiality_tier="Watchlist",
    )
    result = audit_record(record)
    assert result.disposition == "REMOVE_INVALID_COMPANY_TAGS"
    assert result.recomputed_identified_companies == ("SK Hynix",)
    assert result.invalid_company_tags == ("Samsung Electronics",)


def test_bill_gates_commentary_is_never_a_microsoft_or_broadcom_high_signal():
    """Bill Gates AI commentary — should not be a Microsoft/Broadcom
    High Signal."""
    record = _record(
        headline="Bill Gates Compares AI to Alien Intelligence, Warns Governments to Prepare",
        excerpt=(
            "Microsoft co-founder Bill Gates said artificial intelligence should be treated like contact "
            "with an alien intelligence and warns governments around the world need to prepare for the "
            "societal shift AI will bring. AI chipmakers such as Nvidia, AMD, and Broadcom have benefited "
            "from the surge in demand Gates described."
        ),
        matched_companies=("Microsoft Corporation", "Broadcom Inc.", "NVIDIA"), materiality_tier="High Signal",
    )
    result = audit_record(record)
    assert result.disposition == "SUPPRESS_FROM_USER_FEED"
    assert result.recomputed_tier != "High Signal"
    assert "Microsoft Corporation" not in result.recomputed_identified_companies
    assert "Broadcom Inc." not in result.recomputed_identified_companies


def test_gaming_pc_discount_is_suppressed():
    """Gaming-PC discount — should not be High Signal, and (Signals
    precision follow-up, design/SIGNALS_PRECISION_FOLLOWUP_RETAIL_
    BOILERPLATE_GUIDANCE_2026_09_16.md, retail/deal/scarcity rule)
    should no longer be admitted at all under current rules — "save
    25% ($560)" is itself a curated consumer-format deal pattern, and
    NVIDIA being a genuine title-placed subject no longer rescues a
    deal-framed story on its own (the rescue requires real, non-weak
    anchor evidence, which a pure retail write-up has none of)."""
    record = _record(
        headline="Save 25% ($560) on This Gaming PC Packed With AMD and Nvidia Hardware",
        excerpt=(
            "This gaming PC deal pairs an AMD Ryzen 7 processor with an Nvidia GeForce RTX 4070 graphics "
            "card, marking one of the biggest price cut deals we've seen on this configuration this year."
        ),
        matched_companies=("NVIDIA",), materiality_tier="High Signal",
        materiality_reasons=("quantified_change:price cut",),
    )
    result = audit_record(record)
    assert result.disposition == "SUPPRESS_FROM_USER_FEED"
    assert result.admitted is False


def test_unknown_source_feed_id_needs_human_review():
    record = _record(source_feed_id="not-a-real-registered-source-id")
    result = audit_record(record)
    assert result.disposition == "NEEDS_HUMAN_REVIEW"
    assert any(n.startswith("unknown_source_feed_id:") for n in result.notes)


def test_missing_headline_needs_human_review():
    record = _record(headline="   ")
    result = audit_record(record)
    assert result.disposition == "NEEDS_HUMAN_REVIEW"


def test_unparseable_stored_tier_needs_human_review():
    record = _record(materiality_tier="Not A Real Tier")
    result = audit_record(record)
    assert result.disposition == "NEEDS_HUMAN_REVIEW"
    assert any(n.startswith("unparseable_stored_tier:") for n in result.notes)


def test_retier_watchlist_is_distinguished_from_retier_background():
    """A stored High Signal record whose recomputed tier is Watchlist
    (not Background) must get the Watchlist-specific disposition, not
    the Background one."""
    record = _record(
        headline="SK Hynix Faces New US Export Control Review on HBM Chips",
        excerpt="Regulators are examining HBM exports amid new restrictions.",
        source_feed_id=_BUSINESS_KOREA, publisher="Business Korea",
        matched_companies=("SK Hynix",), materiality_tier="High Signal",
    )
    result = audit_record(record)
    assert result.recomputed_tier == "Watchlist"
    assert result.disposition == "RETIER_WATCHLIST"


# ============================================================
# D: audit_records — batch wrapper
# ============================================================


def test_audit_records_processes_every_record_independently():
    records = [_record(id="s1"), _record(id="s2", headline="   ")]
    results = audit_records(records)
    assert [r.id for r in results] == ["s1", "s2"]
    assert results[1].disposition == "NEEDS_HUMAN_REVIEW"


# ============================================================
# E: report output — JSON, CSV, Markdown
# ============================================================


def test_write_json_report_round_trips(tmp_path):
    results = audit_records([_record(id="s1")])
    path = tmp_path / "audit.json"
    write_json_report(results, path)

    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    assert loaded[0]["id"] == "s1"
    assert "disposition" in loaded[0]


def test_write_csv_report_round_trips(tmp_path):
    results = audit_records([_record(id="s1", matched_companies=("NVIDIA", "AMD"))])
    path = tmp_path / "audit.csv"
    write_csv_report(results, path)

    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["id"] == "s1"
    assert json.loads(rows[0]["stored_companies"]) == ["NVIDIA", "AMD"]


def test_write_csv_report_handles_zero_results(tmp_path):
    path = tmp_path / "audit.csv"
    write_csv_report([], path)
    assert path.read_text(encoding="utf-8") == ""


def test_write_report_dispatches_by_extension(tmp_path):
    results = audit_records([_record(id="s1")])
    json_path = tmp_path / "audit.json"
    csv_path = tmp_path / "audit.csv"
    write_report(results, json_path)
    write_report(results, csv_path)
    assert json_path.exists()
    assert csv_path.exists()


def test_write_report_rejects_an_unsupported_extension(tmp_path):
    with pytest.raises(SystemExit):
        write_report([], tmp_path / "audit.txt")


def test_markdown_summary_includes_tier_and_disposition_counts():
    results = audit_records([
        _record(id="s1", materiality_tier="High Signal"),
        _record(id="s2", materiality_tier="Watchlist", headline="   "),
    ])
    summary = build_markdown_summary(results)
    assert "Records audited: **2**" in summary
    assert "Current (stored) tier" in summary
    assert "Recomputed tier" in summary
    assert "Recommended disposition" in summary
    assert "NEEDS_HUMAN_REVIEW" in summary


def test_markdown_summary_lists_high_signal_records_that_no_longer_qualify():
    downgraded = _record(
        id="s1", headline="Save 25% ($560) on This Gaming PC Packed With AMD and Nvidia Hardware",
        excerpt=(
            "This gaming PC deal pairs an AMD Ryzen 7 processor with an Nvidia GeForce RTX 4070 graphics "
            "card, marking one of the biggest price cut deals we've seen on this configuration this year."
        ),
        matched_companies=("NVIDIA",), materiality_tier="High Signal",
    )
    still_valid = _record(
        id="s2", headline="Polarise to Lease 15MW of Data Center Capacity in Prague",
        excerpt=(
            "Polarise has signed an agreement to lease 15MW of data center capacity in Prague, expanding "
            "its footprint in Central Europe as demand for AI infrastructure grows across the region."
        ),
        source_feed_id=_DCD, publisher="Data Center Dynamics",
        matched_themes=("ai-buildout",), materiality_tier="High Signal",
    )
    summary = build_markdown_summary(audit_records([downgraded, still_valid]))
    assert "1 of 2 currently High Signal records" in summary
    assert "s1" in summary
    assert "| s2 |" not in summary.split("no longer qualify")[1].split("##")[0]


def test_markdown_summary_reports_none_when_no_high_signal_records_are_downgraded():
    still_valid = _record(
        id="s2", headline="Polarise to Lease 15MW of Data Center Capacity in Prague",
        excerpt=(
            "Polarise has signed an agreement to lease 15MW of data center capacity in Prague, expanding "
            "its footprint in Central Europe as demand for AI infrastructure grows across the region."
        ),
        source_feed_id=_DCD, publisher="Data Center Dynamics",
        matched_themes=("ai-buildout",), materiality_tier="High Signal",
    )
    summary = build_markdown_summary(audit_records([still_valid]))
    assert "None — every currently High Signal record" in summary


def test_markdown_summary_reports_source_high_signal_share():
    results = audit_records([
        _record(id="s1", publisher="Business Korea", materiality_tier="High Signal"),
        _record(id="s2", publisher="Business Korea", materiality_tier="Watchlist"),
    ])
    summary = build_markdown_summary(results)
    assert "Sources with a high proportion of stored High Signal records" in summary
    assert "| Business Korea | 1 | 2 | 50% |" in summary
