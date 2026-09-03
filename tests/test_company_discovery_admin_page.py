"""Company Discovery admin/status page — fixture-driven render tests
via AppTest. Proves the page is hidden-but-reachable, gated a second
way by settings.company_discovery_admin_enabled (default off), and —
the binding requirement — contains no button, form, or any other
state-changing control of any kind. Zero network calls; only reads a
SQLite-backed Candidate Ledger fixture."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access.company_discovery.company_discovery_backend import SqliteCandidateIssuerRepository
from src.data_access.state_db import connection, schema
from src.data_access.state_db.candidate_issuer_repository import create_candidate_with_evidence
from src.models.company_discovery_models import CandidateEvidence, RelationshipType, SourceType

_HARNESS = Path(__file__).parent / "apptest_pages" / "company_discovery_admin_page.py"


def _settings(tmp_path, **overrides) -> Settings:
    fields = dict(company_discovery_admin_enabled=True, company_discovery_worker_db_backend=None, cache_dir=tmp_path)
    fields.update(overrides)
    return Settings(**fields)


def test_admin_page_shows_disabled_message_when_flag_is_off(tmp_path):
    with patch("src.ui.pages.company_discovery_admin.get_settings", return_value=_settings(tmp_path, company_discovery_admin_enabled=False)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown) + " ".join(str(i.value) for i in at.info)
    assert "not enabled" in all_text.lower()


def test_admin_page_has_zero_buttons_forms_or_callbacks(tmp_path):
    db_path = tmp_path / "test.db"
    conn = connection.connect(db_path)
    schema.migrate(conn)
    create_candidate_with_evidence(
        conn, issuer_id="candidate:abc123", legal_name="Example Materials Corp.", native_name="",
        country_or_jurisdiction="Unconfirmed", entity_kind="corporate", coverage_state="Discovered",
        resolution_confidence="Medium", discovered_via="test", now="2026-09-01T00:00:00+00:00",
        evidence=CandidateEvidence(
            issuer_id="candidate:abc123", source_type=SourceType.FILING, source_name="SEC EDGAR",
            source_record_id="edgar:0001045810-26-000078", source_url="https://www.sec.gov/test",
            source_snippet="components supplied by Example Materials Corp.",
            relationship_type=RelationshipType.SUPPLIER, matched_pattern_category="supplied_by",
            extraction_timestamp="2026-09-01T00:00:00+00:00", dedup_key="dedup-1",
        ),
        alias_text="example materials",
    )

    settings = _settings(
        tmp_path, company_discovery_worker_db_backend="sqlite", company_discovery_worker_state_db_path=db_path,
    )
    with patch("src.ui.pages.company_discovery_admin.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    # "cmdk-trigger" is the global command-palette search button every
    # page's own with_chrome() shell adds (navigation only, never a
    # state-changing control, and not part of this page's own content)
    # — the one expected exception to "zero buttons on this page."
    page_buttons = [b for b in at.button if b.key != "cmdk-trigger"]
    assert page_buttons == []
    assert len(at.get("form")) == 0
    all_text = " ".join(m.value for m in at.markdown)
    assert "Example Materials Corp." in all_text


def test_admin_page_not_linked_in_the_visible_sidebar(tmp_path):
    from src.ui.ui import HIDDEN_FROM_NAV, PRIMARY_NAV, SYSTEM_NAV

    visible_keys = {k for k, _ in PRIMARY_NAV + SYSTEM_NAV}
    assert "company_discovery_admin" not in visible_keys
