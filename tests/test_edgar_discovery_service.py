"""EDGAR discovery preview harness (Phase B) — fully mocked, zero network
calls anywhere in this file. Modeled directly on
tests/test_edinet_discovery_service.py's shape. A fake feed client (not a
MagicMock) is used throughout so `EdgarDiscoveryFeedClient`'s Protocol
contract is exercised the same way a real, future adapter would satisfy
it — no mocking framework is required by the seam itself."""
from __future__ import annotations

import json

from src.config.settings import Settings
from src.data_access.edgar import discovery_service as ds
from src.models.issuer import CoverageState, Issuer


class _FakeFeedClient:
    """Records every call it receives — tests assert against `.calls`
    directly, which is a stronger proof of "zero calls" than a mock
    assertion would be, since there is no mocking-framework machinery to
    misconfigure."""

    def __init__(self, rows: list[dict] | None = None, requests_made: int = 1):
        self._rows = tuple(rows or ())
        self._requests_made = requests_made
        self.calls: list[tuple[int, int]] = []

    def fetch_recent_filing_rows(self, lookback_days: int, max_rows: int) -> ds.DiscoveryFeedBatch:
        self.calls.append((lookback_days, max_rows))
        return ds.DiscoveryFeedBatch(rows=self._rows, requests_made=self._requests_made)


def _row(cik: str, accession: str, form: str = "8-K", items: str = "1.01", **overrides) -> dict:
    row = {"cik": cik, "accessionNumber": accession, "filingDate": "2026-08-20", "form": form, "items": items}
    row.update(overrides)
    return row


def _seed_issuer_with_edgar_cik(cik: str) -> Issuer:
    return Issuer(
        issuer_id=f"edgar:TEST-{cik}", legal_name="Existing Seed Co", country_or_jurisdiction="United States (listing exchange)",
        coverage_state=CoverageState.SEED, identifiers={"SEC EDGAR": cik},
    )


# --- 1. Disabled by default, zero client calls ---

def test_settings_edgar_discovery_enabled_defaults_to_false():
    assert Settings().edgar_discovery_enabled is False


def test_disabled_discovery_makes_zero_calls_to_the_feed_client(tmp_path):
    feed = _FakeFeedClient(rows=[_row("0000009999", "0000009999-26-000001")])
    result = ds.run_discovery(feed, tmp_path, discovery_enabled=False)
    assert result.ran is False
    assert "not enabled" in result.reason
    assert feed.calls == []
    assert not (tmp_path / "edgar_discovery_proposals.json").exists()


# --- 2. Enabled + mocked client processes metadata-only mock rows ---

def test_enabled_discovery_calls_the_feed_client_exactly_once(tmp_path):
    feed = _FakeFeedClient(rows=[_row("0000009999", "0000009999-26-000001")])
    result = ds.run_discovery(feed, tmp_path, discovery_enabled=True)
    assert result.ran is True
    assert len(feed.calls) == 1
    assert feed.calls[0] == (ds.DEFAULT_EDGAR_DISCOVERY_LOOKBACK_DAYS, ds.MAX_EDGAR_DISCOVERY_ROWS)


# --- 3. Existing active seed EDGAR CIKs are excluded ---

def test_row_for_an_excluded_seed_cik_never_becomes_a_proposal(tmp_path):
    seed = (_seed_issuer_with_edgar_cik("0000009999"),)
    feed = _FakeFeedClient(rows=[_row("0000009999", "0000009999-26-000001")])
    result = ds.run_discovery(feed, tmp_path, discovery_enabled=True, seed_issuers=seed)
    assert result.new_proposals == ()


def test_exclusion_matches_regardless_of_raw_cik_zero_padding(tmp_path):
    # Feed row supplies an un-padded CIK; the exclusion set holds the
    # normalized 10-digit form — normalize_cik must reconcile them.
    seed = (_seed_issuer_with_edgar_cik("0000009999"),)
    feed = _FakeFeedClient(rows=[_row("9999", "0000009999-26-000001")])
    result = ds.run_discovery(feed, tmp_path, discovery_enabled=True, seed_issuers=seed)
    assert result.new_proposals == ()


def test_default_seed_issuers_argument_is_the_real_registry(tmp_path):
    # Proves the production wiring point (default parameter) is the real
    # SEED_ISSUERS collection, not a test-only stand-in — see module
    # docstring's "known limitation" note for why this currently excludes
    # nothing in practice.
    from src.config.issuer_registry import SEED_ISSUERS

    feed = _FakeFeedClient(rows=[])
    result = ds.run_discovery(feed, tmp_path, discovery_enabled=True)
    assert result.ran is True
    import inspect

    assert inspect.signature(ds.run_discovery).parameters["seed_issuers"].default is SEED_ISSUERS


# --- 4/5/6. Valid vs. rejected rows ---

def test_unknown_cik_with_valid_item_creates_a_proposed_proposal(tmp_path):
    feed = _FakeFeedClient(rows=[_row("0000009999", "0000009999-26-000001", items="1.01")])
    result = ds.run_discovery(feed, tmp_path, discovery_enabled=True)
    assert len(result.new_proposals) == 1
    proposal = result.new_proposals[0]
    assert proposal.discovery_status == "Proposed"
    assert proposal.proposal_id == "edgar-discovery:0000009999"
    assert proposal.cik == "0000009999"


def test_missing_malformed_generic_and_unrecognized_items_create_no_proposal(tmp_path):
    rows = [
        _row("0000001111", "0000001111-26-000001", items=""),
        _row("0000002222", "0000002222-26-000001", items=None),
        _row("0000003333", "0000003333-26-000001", items="not-a-real-item"),
        _row("0000004444", "0000004444-26-000001", items="9.01"),  # real but unconfigured
    ]
    feed = _FakeFeedClient(rows=rows)
    result = ds.run_discovery(feed, tmp_path, discovery_enabled=True)
    assert result.new_proposals == ()


def test_non_8k_rows_never_create_proposals(tmp_path):
    rows = [_row("0000005555", "0000005555-26-000001", form=form, items="1.01") for form in ("10-Q", "10-K", "SC 13D")]
    feed = _FakeFeedClient(rows=rows)
    result = ds.run_discovery(feed, tmp_path, discovery_enabled=True)
    assert result.new_proposals == ()


# --- 7. Multiple filings for one new CIK -> one proposal, multiple matched_filings ---

def test_multiple_filings_for_one_new_cik_merge_into_one_proposal(tmp_path):
    rows = [
        _row("0000009999", "0000009999-26-000001", items="1.01"),
        _row("0000009999", "0000009999-26-000002", items="2.03"),
    ]
    feed = _FakeFeedClient(rows=rows)
    result = ds.run_discovery(feed, tmp_path, discovery_enabled=True)
    assert len(result.new_proposals) == 1
    proposal = result.new_proposals[0]
    assert len(proposal.matched_filings) == 2
    accessions = {f.accession_no for f in proposal.matched_filings}
    assert accessions == {"0000009999-26-000001", "0000009999-26-000002"}
    # Two distinct categories (1.01, 2.03) across the two filings combined
    # -> High, same distinct-category-count rule edgar_rules uses within
    # a single multi-item filing.
    assert proposal.confidence == "High"


def test_duplicate_accession_within_one_batch_does_not_duplicate_matched_filings(tmp_path):
    rows = [
        _row("0000009999", "0000009999-26-000001", items="1.01"),
        _row("0000009999", "0000009999-26-000001", items="1.01"),  # exact duplicate row
    ]
    feed = _FakeFeedClient(rows=rows)
    result = ds.run_discovery(feed, tmp_path, discovery_enabled=True)
    assert len(result.new_proposals[0].matched_filings) == 1


# --- 8. Proposal cap across multiple unknown CIKs ---

def test_new_proposal_cap_defers_excess_ciks_to_a_future_run(tmp_path):
    rows = [_row(f"{i:010d}", f"{i:010d}-26-000001", items="1.01") for i in range(1, ds.MAX_EDGAR_DISCOVERY_PROPOSALS + 5)]
    feed = _FakeFeedClient(rows=rows)
    result = ds.run_discovery(feed, tmp_path, discovery_enabled=True)
    assert len(result.new_proposals) == ds.MAX_EDGAR_DISCOVERY_PROPOSALS

    # The deferred CIKs' filing_keys were never marked seen — a later run
    # (with headroom) picks them up rather than losing them.
    feed2 = _FakeFeedClient(rows=rows)
    result2 = ds.run_discovery(feed2, tmp_path, discovery_enabled=True)
    assert len(result2.new_proposals) == 4  # the remaining 4 beyond the first 10


# --- 9. Row cap and request cap ---

def test_row_cap_limits_examined_rows(tmp_path):
    rows = [_row(f"{i:010d}", f"{i:010d}-26-000001", items="1.01") for i in range(ds.MAX_EDGAR_DISCOVERY_ROWS + 10)]
    feed = _FakeFeedClient(rows=rows)
    result = ds.run_discovery(feed, tmp_path, discovery_enabled=True)
    assert result.rows_examined == ds.MAX_EDGAR_DISCOVERY_ROWS


def test_request_budget_exceeded_fails_closed_and_writes_nothing(tmp_path):
    feed = _FakeFeedClient(rows=[_row("0000009999", "0000009999-26-000001")], requests_made=ds.MAX_EDGAR_DISCOVERY_METADATA_REQUESTS + 1)
    result = ds.run_discovery(feed, tmp_path, discovery_enabled=True)
    assert result.ran is False
    assert "exceeding" in result.reason
    assert result.new_proposals == ()
    assert not (tmp_path / "edgar_discovery_proposals.json").exists()


def test_request_budget_at_exactly_the_limit_is_allowed(tmp_path):
    feed = _FakeFeedClient(rows=[_row("0000009999", "0000009999-26-000001")], requests_made=ds.MAX_EDGAR_DISCOVERY_METADATA_REQUESTS)
    result = ds.run_discovery(feed, tmp_path, discovery_enabled=True)
    assert result.ran is True


# --- 10. Repeat-run idempotence ---

def test_identical_repeat_run_creates_no_duplicate_proposals_or_filings(tmp_path):
    rows = [_row("0000009999", "0000009999-26-000001", items="1.01")]
    feed1 = _FakeFeedClient(rows=rows)
    ds.run_discovery(feed1, tmp_path, discovery_enabled=True)

    before_bytes = (tmp_path / "edgar_discovery_proposals.json").read_bytes()

    feed2 = _FakeFeedClient(rows=rows)
    result2 = ds.run_discovery(feed2, tmp_path, discovery_enabled=True)
    after_bytes = (tmp_path / "edgar_discovery_proposals.json").read_bytes()

    assert result2.new_proposals == ()
    assert result2.updated_proposal_ciks == ()
    assert result2.already_seen_filing_count == 1
    # "runs" audit history grows, but proposals/seen_filing_keys don't —
    # compare the stable substructures rather than the whole byte-for-byte
    # file (a new run entry is expected to append).
    before = json.loads(before_bytes)
    after = json.loads(after_bytes)
    assert before["proposals"] == after["proposals"]
    assert before["seen_filing_keys"] == after["seen_filing_keys"]
    assert len(after["runs"]) == len(before["runs"]) + 1


# --- 11. Merging into existing preview content, no duplicate CIK records ---

def test_new_filing_for_an_already_known_cik_updates_not_duplicates_the_proposal(tmp_path):
    feed1 = _FakeFeedClient(rows=[_row("0000009999", "0000009999-26-000001", items="1.01")])
    result1 = ds.run_discovery(feed1, tmp_path, discovery_enabled=True)
    first_proposal = result1.new_proposals[0]

    feed2 = _FakeFeedClient(rows=[_row("0000009999", "0000009999-26-000002", items="2.03")])
    result2 = ds.run_discovery(feed2, tmp_path, discovery_enabled=True)

    assert result2.new_proposals == ()
    assert result2.updated_proposal_ciks == ("0000009999",)

    loaded = ds.load_discovery_proposals(tmp_path)
    assert len(loaded) == 1  # still one proposal record for this CIK, not two
    assert len(loaded[0].matched_filings) == 2
    assert loaded[0].confidence == "High"  # two distinct categories now present across filings
    # run_id/generated_at reflect first creation, unchanged by the merge
    assert loaded[0].run_id == first_proposal.run_id
    assert loaded[0].generated_at == first_proposal.generated_at


# --- 12. Field provenance and unassigned theme/layer ---

def test_proposal_preserves_literal_metadata_and_leaves_theme_layer_unassigned(tmp_path):
    feed = _FakeFeedClient(rows=[_row(
        "0000009999", "0000009999-26-000001", items="1.01",
        companyName="Discovered Co", ticker="DISC", primaryDocument="ex-1.htm",
    )])
    result = ds.run_discovery(feed, tmp_path, discovery_enabled=True)
    proposal = result.new_proposals[0]
    assert proposal.issuer_display_name == "Discovered Co"
    assert proposal.ticker == "DISC"
    assert proposal.candidate_theme is None
    assert proposal.candidate_layer is None
    filing = proposal.matched_filings[0]
    assert filing.primary_document == "ex-1.htm"
    assert filing.items_raw == "1.01"
    assert filing.filing_date == "2026-08-20"
    assert filing.source_url == "https://www.sec.gov/Archives/edgar/data/9999/000000999926000001/"


def test_missing_optional_metadata_fields_do_not_crash_and_yield_none(tmp_path):
    feed = _FakeFeedClient(rows=[_row("0000009999", "0000009999-26-000001", items="1.01")])
    result = ds.run_discovery(feed, tmp_path, discovery_enabled=True)
    proposal = result.new_proposals[0]
    assert proposal.issuer_display_name is None
    assert proposal.ticker is None
    assert proposal.matched_filings[0].primary_document == ""


def test_verification_status_and_excluded_reason_are_the_exact_fixed_wording(tmp_path):
    feed = _FakeFeedClient(rows=[_row("0000009999", "0000009999-26-000001", items="1.01")])
    result = ds.run_discovery(feed, tmp_path, discovery_enabled=True)
    proposal = result.new_proposals[0]
    assert proposal.verification_status == ds.VERIFICATION_STATUS
    assert proposal.excluded_from_coverage_reason == ds.EXCLUDED_FROM_COVERAGE_REASON
    assert "no filing document retrieved" in proposal.verification_status
    assert "not scan-eligible" in proposal.excluded_from_coverage_reason


# --- 13. Preview write isolation — production stores untouched ---

_PRODUCTION_STORE_FILENAMES = (
    "edgar_candidates.json", "edgar_filing_events.json", "edgar_document_excerpts.json",
    "dart_candidates.json", "dart_filing_events.json", "dart_document_excerpts.json",
    "translation_cache.json",
)


def test_discovery_run_never_touches_any_production_store_file(tmp_path):
    before = {}
    for name in _PRODUCTION_STORE_FILENAMES:
        path = tmp_path / name
        path.write_text(f'{{"marker": "{name}"}}', encoding="utf-8")
        before[name] = path.read_bytes()

    feed = _FakeFeedClient(rows=[_row("0000009999", "0000009999-26-000001", items="1.01")])
    ds.run_discovery(feed, tmp_path, discovery_enabled=True)

    for name in _PRODUCTION_STORE_FILENAMES:
        assert (tmp_path / name).read_bytes() == before[name], f"{name} was modified by a discovery run"


def test_discovery_writes_only_its_own_cache_file(tmp_path):
    feed = _FakeFeedClient(rows=[_row("0000009999", "0000009999-26-000001", items="1.01")])
    ds.run_discovery(feed, tmp_path, discovery_enabled=True)
    created = {p.name for p in tmp_path.iterdir()}
    assert created == {"edgar_discovery_proposals.json"}


# --- 14. Structural import/source guards ---
#
# AST-based, not raw substring search: this module's own docstring names
# several forbidden modules/symbols in prose, to explain what isolation it
# guarantees ("never imports document_service...") — a naive substring
# check would be tripped by that explanatory text itself. Parsing actual
# `import`/`from ... import` statements checks the real guarantee (no such
# module is ever imported) without being fooled by comments/docstrings.

def _imported_module_and_symbol_names(module) -> set[str]:
    import ast

    tree = ast.parse(open(module.__file__, encoding="utf-8").read())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            if module_name:
                names.add(module_name)
            for alias in node.names:
                names.add(alias.name)
    return names


_FORBIDDEN_MODULE_PREFIXES = (
    "src.data_access.edgar.document_service", "src.data_access.edgar.document_extractor",
    "src.data_access.dart", "src.data_access.edinet", "src.data_access.translation",
    "src.data_access.remote_cache", "src.logic.review_actions", "src.logic.signal_promotion", "requests",
)
_FORBIDDEN_SYMBOLS = (
    "document_service", "document_extractor", "fetch_document", "candidate_store",
    "signal_promotion", "record_review_decision", "CandidateSignal", "DISCOVERY_STUBS",
    "tracked_companies_from_issuer_registry",
)


def test_module_never_imports_forbidden_modules_or_symbols():
    import src.data_access.edgar.discovery_service as module

    imported = _imported_module_and_symbol_names(module)
    for prefix in _FORBIDDEN_MODULE_PREFIXES:
        assert not any(name == prefix or name.startswith(prefix + ".") for name in imported), (
            f"discovery_service.py must not import {prefix!r} or anything under it"
        )
    for symbol in _FORBIDDEN_SYMBOLS:
        assert symbol not in imported, f"discovery_service.py must not import {symbol!r}"


def test_discovery_rules_module_never_imports_forbidden_modules_or_symbols():
    import src.data_access.edgar.discovery_rules as module

    imported = _imported_module_and_symbol_names(module)
    for prefix in _FORBIDDEN_MODULE_PREFIXES:
        assert not any(name == prefix or name.startswith(prefix + ".") for name in imported)
    for symbol in _FORBIDDEN_SYMBOLS:
        assert symbol not in imported


# --- 15. Preview artifact not read by existing UI query paths ---

def test_no_existing_ui_module_references_the_discovery_preview_filename_or_service():
    import pathlib

    ui_dir = pathlib.Path(__file__).resolve().parent.parent / "src" / "ui"
    offending = []
    for path in ui_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "edgar_discovery_proposals" in text or "discovery_service" in text or "IssuerDiscoveryProposal" in text:
            offending.append(str(path))
    assert offending == []


# --- Merge-idempotence across a longer, multi-run sequence (belt-and-suspenders) ---

def test_three_runs_same_new_cik_never_produces_more_than_one_proposal_record(tmp_path):
    for accession_suffix in ("000001", "000002", "000003"):
        feed = _FakeFeedClient(rows=[_row("0000009999", f"0000009999-26-{accession_suffix}", items="1.01")])
        ds.run_discovery(feed, tmp_path, discovery_enabled=True)

    loaded = ds.load_discovery_proposals(tmp_path)
    assert len(loaded) == 1
    assert len(loaded[0].matched_filings) == 3
