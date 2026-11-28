"""scripts/backfill_filing_story_themes.py — filling matched_themes on
filing-derived stories that were projected before the bridge started
tagging them.

Two layers, deliberately:

  * the selection decision is pure and tested offline, because that is
    what decides whether a row is touched at all; and
  * the write, the revision row and the read-back are exercised against
    a REAL Postgres schema via tests/_postgres_test_support.py, because
    a fake connection would prove nothing about the SQL, the V32 column
    or the commit — and a previous repair in this repository reported
    success while its transaction silently wrote nothing. That layer
    skips (never fails) when the local disposable container is not
    running.
"""
from __future__ import annotations

import json

import pytest

from scripts import backfill_filing_story_themes as backfill
from src.config.issuer_registry import SEED_ISSUERS
from src.data_access.daily_news import filing_signal_bridge
from src.data_access.postgres_state_db import schema as postgres_schema
from tests._postgres_test_support import (  # noqa: F401 — pytest fixtures
    pg_isolated_connection,
    pg_isolated_dsn,
)

_NVIDIA = next(i for i in SEED_ISSUERS if i.legal_name == "NVIDIA")


# --- the selection decision, offline -------------------------------------------


def test_a_single_tracked_company_resolves_to_its_registry_themes():
    row = {"matched_companies_json": json.dumps([_NVIDIA.legal_name])}
    assert backfill._themes_for_row(row) == _NVIDIA.themes
    assert _NVIDIA.themes, "fixture check: NVIDIA should carry at least one theme"


def test_an_unknown_company_resolves_to_nothing_rather_than_a_guess():
    assert backfill._themes_for_row({"matched_companies_json": json.dumps(["Not Tracked Inc"])}) == ()


def test_a_row_with_no_company_or_several_is_left_alone():
    """A filing story carries exactly one issuer by construction
    (filing_signal_bridge sets matched_companies=(company,)). A row that
    does not was written by something else, and this script must not
    guess which company's themes to apply."""
    assert backfill._themes_for_row({"matched_companies_json": "[]"}) == ()
    assert backfill._themes_for_row({"matched_companies_json": json.dumps(["NVIDIA", "Micron Technology"])}) == ()
    assert backfill._themes_for_row({"matched_companies_json": None}) == ()


# --- the write, against a real schema ------------------------------------------


def _insert_story(conn, story_id, companies, themes, tier="High Signal"):
    conn.execute(
        """
        INSERT INTO editorial_stories (
            id, headline, publisher, source_url, published_at, retrieved_at, excerpt,
            matched_companies_json, matched_themes_json, source_feed_id,
            materiality_tier, materiality_reasons
        ) VALUES (%s, 'h', 'p', %s, '2026-11-01', '2026-11-01', 'e', %s, %s, 'f', %s, '[]')
        """,
        # A unique index covers source_url, so every fixture row needs
        # its own.
        (story_id, f"https://example.test/{story_id}", json.dumps(companies), json.dumps(themes), tier),
    )


@pytest.fixture
def seeded(pg_isolated_connection):  # noqa: F811
    conn = pg_isolated_connection
    postgres_schema.migrate(conn)
    prefix = filing_signal_bridge.STORY_ID_PREFIX
    _insert_story(conn, f"{prefix}:untagged", [_NVIDIA.legal_name], [])
    _insert_story(conn, f"{prefix}:already-tagged", [_NVIDIA.legal_name], ["space"])
    _insert_story(conn, f"{prefix}:unknown-company", ["Not Tracked Inc"], [])
    _insert_story(conn, "news:story", [_NVIDIA.legal_name], [])
    conn.commit()
    return conn


def test_v32_adds_the_old_themes_column_the_revision_row_needs(seeded):
    cols = seeded.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'editorial_story_revisions'"
    ).fetchall()
    assert "old_themes_json" in {c["column_name"] for c in cols}


def test_candidates_are_filing_rows_with_no_themes_only(seeded):
    ids = {r["id"] for r in backfill._candidates(seeded)}
    prefix = filing_signal_bridge.STORY_ID_PREFIX
    assert f"{prefix}:untagged" in ids
    assert f"{prefix}:unknown-company" in ids          # selected, then left untouched
    assert f"{prefix}:already-tagged" not in ids, "an existing theme list must never be overwritten"
    assert "news:story" not in ids, "news stories get their themes from the editorial matcher"


# --- the whole run: write, revision row, read-back -----------------------------


@pytest.fixture
def seeded_dsn(pg_isolated_dsn, monkeypatch):  # noqa: F811
    """The script builds its own connection from Settings, so point
    Settings at the isolated schema and seed it through that same DSN."""
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(pg_isolated_dsn, row_factory=dict_row) as conn:
        postgres_schema.migrate(conn)
        prefix = filing_signal_bridge.STORY_ID_PREFIX
        _insert_story(conn, f"{prefix}:untagged", [_NVIDIA.legal_name], [])
        _insert_story(conn, f"{prefix}:already-tagged", [_NVIDIA.legal_name], ["space"])
        _insert_story(conn, f"{prefix}:unknown-company", ["Not Tracked Inc"], [])
        _insert_story(conn, "news:story", [_NVIDIA.legal_name], [])
        conn.commit()

    class _S:
        state_db_url = pg_isolated_dsn

    monkeypatch.setattr(backfill, "get_settings", lambda: _S())
    return pg_isolated_dsn


def _themes(dsn, story_id):
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        row = conn.execute(
            "SELECT matched_themes_json FROM editorial_stories WHERE id = %s", (story_id,)
        ).fetchone()
    return json.loads(row["matched_themes_json"] or "[]")


def test_a_dry_run_writes_nothing(seeded_dsn):
    assert backfill.main([]) == 0
    prefix = filing_signal_bridge.STORY_ID_PREFIX
    assert _themes(seeded_dsn, f"{prefix}:untagged") == []


def test_apply_fills_the_resolvable_row_and_leaves_every_other_alone(seeded_dsn):
    assert backfill.main(["--apply"]) == 0
    prefix = filing_signal_bridge.STORY_ID_PREFIX
    assert _themes(seeded_dsn, f"{prefix}:untagged") == list(_NVIDIA.themes)
    assert _themes(seeded_dsn, f"{prefix}:already-tagged") == ["space"], "existing themes overwritten"
    assert _themes(seeded_dsn, f"{prefix}:unknown-company") == [], "a guess was written"
    assert _themes(seeded_dsn, "news:story") == [], "a news story was rewritten"


def test_each_change_records_what_it_changed_from(seeded_dsn):
    """A revision row that says only that something happened is the gap
    V31 was added to close; V32's old_themes_json closes it here."""
    import psycopg
    from psycopg.rows import dict_row

    backfill.main(["--apply"])
    with psycopg.connect(seeded_dsn, row_factory=dict_row) as conn:
        rows = conn.execute(
            "SELECT * FROM editorial_story_revisions WHERE reason = %s", (backfill.BACKFILL_REASON,)
        ).fetchall()
    assert len(rows) == 1, "exactly the one row that changed"
    row = rows[0]
    assert row["story_id"] == f"{filing_signal_bridge.STORY_ID_PREFIX}:untagged"
    assert json.loads(row["old_themes_json"]) == []
    # Tier and companies are written unchanged — this backfill moves
    # neither, and the row should say so rather than imply a decision.
    assert row["old_tier"] == row["new_tier"]
    assert row["old_companies_json"] == row["new_companies_json"]


def test_a_second_run_is_a_no_op(seeded_dsn):
    import psycopg
    from psycopg.rows import dict_row

    backfill.main(["--apply"])
    assert backfill.main(["--apply"]) == 0
    with psycopg.connect(seeded_dsn, row_factory=dict_row) as conn:
        n = conn.execute(
            "SELECT count(*) n FROM editorial_story_revisions WHERE reason = %s", (backfill.BACKFILL_REASON,)
        ).fetchone()["n"]
    assert n == 1, "a re-run wrote a second revision for an unchanged row"


def test_a_story_with_no_tier_is_skipped_rather_than_crashing_the_run(pg_isolated_dsn, monkeypatch):  # noqa: F811
    """editorial_story_revisions.new_tier is NOT NULL, so a story with
    no tier cannot be given a revision row — and this script will not
    update a story it cannot log. It must skip and say so, not fail
    halfway through with some rows written and some not."""
    import psycopg
    from psycopg.rows import dict_row

    prefix = filing_signal_bridge.STORY_ID_PREFIX
    with psycopg.connect(pg_isolated_dsn, row_factory=dict_row) as conn:
        postgres_schema.migrate(conn)
        _insert_story(conn, f"{prefix}:no-tier", [_NVIDIA.legal_name], [], tier=None)
        _insert_story(conn, f"{prefix}:fine", [_NVIDIA.legal_name], [])
        conn.commit()

    class _S:
        state_db_url = pg_isolated_dsn

    monkeypatch.setattr(backfill, "get_settings", lambda: _S())
    assert backfill.main(["--apply"]) == 0
    assert _themes(pg_isolated_dsn, f"{prefix}:fine") == list(_NVIDIA.themes)
    assert _themes(pg_isolated_dsn, f"{prefix}:no-tier") == [], "a row that cannot be logged was still updated"
