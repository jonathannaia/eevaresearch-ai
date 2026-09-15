"""Gated KR/Light Reading rollout — quality-review harness tests
(design/DECISIONS.md). classify_item() is pure (no I/O) and reuses the
exact same matching/materiality/admission functions the real pipeline
calls — these tests double as regression coverage for the KR/Light
Reading sources' worst observed noise shapes (consumer format, generic
stock roundup, word-collision alias, off-topic) plus the current,
documented behavior for a personnel-appointment headline (admitted via
title placement today — flagged as a candidate for a future,
live-data-informed admission-rule tweak, not changed by this harness)."""
from __future__ import annotations

import json
import subprocess
import sys

from scripts.daily_news_quality_review import classify_item


def test_high_value_item_with_quantified_capex_anchor():
    result = classify_item(
        "SK Hynix Announces $10 Billion Capex Expansion for New Memory Fab",
        "The investment will expand production capacity.",
    )
    assert result.label == "HIGH VALUE"
    assert result.reason == "company_subject:SK Hynix"


def test_high_value_item_with_quantified_earnings():
    result = classify_item(
        "SK Hynix Reports Third Quarter Financial Results",
        "The company reported quarterly financial results today.",
    )
    assert result.label == "HIGH VALUE"


def test_borderline_item_with_no_hard_material_anchor():
    """A genuine on-theme corporate-subject story with no quantified
    change/earnings/capex anchor lands as BORDERLINE (Watchlist), not
    HIGH VALUE — this is exactly the "relevant but not yet material"
    case the harness's own BORDERLINE bucket is for."""
    result = classify_item(
        "SK Hynix Faces New US Export Control Review on HBM Chips",
        "Regulators are examining HBM exports amid new restrictions.",
    )
    assert result.label == "BORDERLINE"
    assert result.reason == "company_subject:SK Hynix"


def test_consumer_deals_item_is_irrelevant():
    result = classify_item(
        "Best Samsung Electronics Galaxy Deals This Chuseok Holiday",
        "Save up to 40% on Galaxy phones this week.",
    )
    assert result.label == "IRRELEVANT"
    assert result.reason.startswith("consumer_editorial_format:")


def test_generic_stock_roundup_naming_no_company_is_irrelevant():
    result = classify_item(
        "Stocks to Watch: Biggest Movers in Asian Markets Today",
        "A broad roundup of today's biggest gainers and losers.",
    )
    assert result.label == "IRRELEVANT"
    assert result.reason == "no_qualifying_company_or_theme_match"


def test_word_collision_alias_item_is_irrelevant():
    result = classify_item(
        "Best Disco Playlists for Your K-Pop Dance Party",
        "Get the party started with these upbeat tracks.",
    )
    assert result.label == "IRRELEVANT"
    assert result.reason == "company_mention_not_subject_worthy"


def test_off_topic_item_naming_no_company_or_theme_is_irrelevant():
    result = classify_item(
        "Local Festival Draws Record Crowds to Busan Waterfront",
        "Visitors enjoyed food stalls and live music over the weekend.",
    )
    assert result.label == "IRRELEVANT"
    assert result.reason == "no_qualifying_company_or_theme_match"


def test_personnel_appointment_item_is_currently_admitted_as_borderline():
    """Documents CURRENT behavior, not a target: a personnel-appointment
    headline naming a tracked company in the title is admitted today
    purely via title placement (editorial_admission.py's identity check
    accepts title placement OR company-action language for a
    non-ambiguous company — no distinction for personnel-only actions).
    This is exactly the kind of noise pattern Part 1's operational
    review is meant to surface; this test locks in today's behavior so
    a future, live-data-informed admission-rule tweak shows up here as
    an intentional, reviewed change rather than a silent regression."""
    result = classify_item(
        "Samsung Electronics Appoints New Head of Memory Division",
        "The appointment takes effect next month.",
    )
    assert result.label == "BORDERLINE"
    assert result.reason == "company_subject:Samsung Electronics"


def test_publisher_is_echoed_back_unchanged():
    result = classify_item("Best Disco Playlists for Your K-Pop Dance Party", None, publisher="Business Korea")
    assert result.publisher == "Business Korea"


def test_cli_reads_input_file_and_prints_summary_counts(tmp_path):
    input_path = tmp_path / "sample.json"
    input_path.write_text(json.dumps([
        {"title": "SK Hynix Reports Third Quarter Financial Results", "publisher": "Business Korea"},
        {"title": "Best Samsung Electronics Galaxy Deals This Chuseok Holiday"},
    ]), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "scripts.daily_news_quality_review", "--input", str(input_path)],
        capture_output=True, text=True, check=True,
    )

    assert "HIGH VALUE: 1" in result.stdout
    assert "IRRELEVANT: 1" in result.stdout
    assert "No feeds fetched, no writes occurred" in result.stdout


def test_cli_reads_from_stdin():
    payload = json.dumps([{"title": "Local Festival Draws Record Crowds to Busan Waterfront"}])

    result = subprocess.run(
        [sys.executable, "-m", "scripts.daily_news_quality_review", "--stdin"],
        input=payload, capture_output=True, text=True, check=True,
    )

    assert "IRRELEVANT: 1" in result.stdout


def test_cli_rejects_non_list_json_input(tmp_path):
    input_path = tmp_path / "bad.json"
    input_path.write_text(json.dumps({"title": "not a list"}), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "scripts.daily_news_quality_review", "--input", str(input_path)],
        capture_output=True, text=True,
    )

    assert result.returncode != 0
    assert "must be a JSON array" in result.stderr
