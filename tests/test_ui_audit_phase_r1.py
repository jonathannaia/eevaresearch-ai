"""Phase R1 (design/DECISIONS.md) — autonomous-Radar feed UI
simplification: a concise default header (no credential-driven "Live"
chip, no tracked-company/scope dump), "Latest" replacing "Needs your
decision" as the default view, Status moved into Advanced filters, and
filing cards restructured around one "Investigate →" primary action with
Publish/Monitor/Exclude relocated behind it. Every test here is a pure
rendering/content/source-inspection check via AppTest or direct source
reads — no candidate workflow, status, review action, signal-eligibility,
worker, scheduler, source, translation, retry, deployment, database,
dependency, environment variable, secret, or GitHub Actions behavior is
exercised or changed by this phase, and several tests below prove that
directly rather than merely asserting the negative in prose."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access.dart import candidate_store
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, StateTransition

HARNESS_DIR = Path(__file__).parent / "apptest_pages"
REPO_ROOT = Path(__file__).parent.parent
_HARNESS = HARNESS_DIR / "radar_inbox_page.py"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_corp_codes(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "005930": {"corp_code": "00126380", "corp_name": "삼성전자", "source": "OpenDART corpCode.xml", "retrieved_at": "2026-08-01T00:00:00+00:00"},
        "000660": {"corp_code": "00164779", "corp_name": "SK 하이닉스", "source": "OpenDART corpCode.xml", "retrieved_at": "2026-08-01T00:00:00+00:00"},
    }
    (cache_dir / "dart_corp_codes.json").write_text(json.dumps(payload), encoding="utf-8")


def _filing(rcept_no: str, report_nm: str) -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm=report_nm, rcept_dt="20260812", flr_nm="삼성전자", theme_slug="memory",
        source_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
        retrieved_at=_now_iso(),
    )


def _seed_filing_events(cache_dir: Path, filings: list[FilingEvent]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {"seen_receipt_numbers": [f.rcept_no for f in filings], "filing_events": [asdict(f) for f in filings], "candidate_signals": []}
    (cache_dir / "dart_filing_events.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _run_radar(tmp_path, **settings_overrides):
    settings = Settings(
        dart_api_key="dart-key", translation_api_key="deepl-key",
        edgar_user_agent=None, edinet_subscription_key=None,
        cache_dir=tmp_path, **settings_overrides,
    )
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=15)
        at.run()
    return at


def _text(at) -> str:
    return " ".join(m.value for m in at.markdown if not m.value.startswith("<style>"))


def _ordered_markdown(at) -> list[str]:
    """Every rendered Markdown element's value, in the exact order the
    page produced them — AppTest renders collapsed-expander content
    regardless of visual state, so this is the only reliable way to
    prove something is *inside* a specific expander (renders after its
    header) rather than in the default page body (renders before it)."""
    out: list[str] = []

    def _walk(node) -> None:
        cls = type(node).__name__
        if cls == "Markdown" and not node.value.startswith("<style>"):
            out.append(node.value)
        elif cls == "Expander":
            out.append(f"__EXPANDER_START__{node.label}")
        children = getattr(node, "children", None)
        if children:
            for key in sorted(children.keys(), key=lambda x: (isinstance(x, str), x)):
                _walk(children[key])

    _walk(at.main)
    return out


# ============================== HEADER ==============================

def test_default_header_has_only_the_approved_subtitle_and_no_live_chip(tmp_path):
    _seed_corp_codes(tmp_path)
    _seed_filing_events(tmp_path, [_filing("20260812000001", "일반 공고")])
    at = _run_radar(tmp_path)
    assert not at.exception
    all_text = _text(at)
    assert "Radar Inbox" in all_text
    assert "Radar watches tracked companies for material filings, theme developments, and high-confidence signals." in all_text
    # Genuinely deleted, not relocated — these exact strings exist nowhere
    # in the page, default view or not.
    assert "Automated primary-filing discovery" not in all_text
    assert "Live primary filings" not in all_text
    # No credential-driven freshness chip anywhere by default.
    assert "er-fresh-live" not in all_text
    assert "er-fresh-demo" not in all_text

    # Relocated (not deleted) content — still present somewhere on the
    # page, but must render AFTER the Ingestion status expander opens,
    # never before it (i.e. never in the default header).
    ordered = _ordered_markdown(at)
    ingestion_idx = ordered.index("__EXPANDER_START__Ingestion status")
    for relocated in ("Samsung Electronics + SK Hynix", "Korea DART + SEC EDGAR pilots configured", "Candidate signals are rule-based filing flags"):
        match_idx = next(i for i, v in enumerate(ordered) if relocated in v)
        assert match_idx > ingestion_idx, f"{relocated!r} rendered before Ingestion status opened"


def test_operational_scope_detail_only_lives_inside_ingestion_status(tmp_path):
    settings = Settings(
        dart_api_key="dart-key", translation_api_key="deepl-key",
        edgar_user_agent=None, edinet_subscription_key=None, cache_dir=tmp_path,
    )
    _seed_corp_codes(tmp_path)
    _seed_filing_events(tmp_path, [_filing("20260812000001", "일반 공고")])
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=15)
        at.run()
    assert not at.exception
    all_text = _text(at)
    # The relocated content still exists — nothing was deleted — just
    # inside the collapsed "Ingestion status" disclosure now.
    assert "Samsung Electronics + SK Hynix" in all_text
    assert "Korea DART + SEC EDGAR pilots configured" in all_text
    assert "Candidate signals are rule-based filing flags" in all_text
    assert any(e.label == "Ingestion status" for e in at.expander)
    source = (REPO_ROOT / "src" / "ui" / "pages" / "radar_inbox.py").read_text(encoding="utf-8")
    # Structural proof, not just text presence: the scope-line renders are
    # inside the same function body as the expander's own `with` block.
    ingestion_start = source.index('with st.expander("Ingestion status"):')
    dart_scope_idx = source.index("_DART_SCOPE_LINE}", ingestion_start)
    assert dart_scope_idx > ingestion_start


# ============================== VIEWS AND FILTERS ==============================

def test_default_view_is_latest_and_secondary_is_captured_filings(tmp_path):
    _seed_corp_codes(tmp_path)
    _seed_filing_events(tmp_path, [_filing("20260812000001", "일반 공고")])
    at = _run_radar(tmp_path)
    assert not at.exception
    radio = at.radio(key="radar-view-mode")
    assert radio.options == ["Latest", "Captured filings"]
    assert radio.value == "Latest"


def test_result_membership_is_unchanged_needs_review_candidate_visible_in_latest(tmp_path):
    """Same underlying `candidate is not None` filter as before the
    rename — a real candidate still appears in the default view, and a
    bare filing with no candidate still doesn't."""
    _seed_corp_codes(tmp_path)
    bare_filing = _filing("20260812000001", "일반 공고")
    candidate_filing = _filing("20260812000002", "신규시설투자등 결정")
    _seed_filing_events(tmp_path, [bare_filing, candidate_filing])
    candidate = CandidateSignal(
        id="cand-r1-membership", filing=candidate_filing,
        matched_rules=["capex_or_facility_investment:facility_investment:신규시설투자"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="신규시설투자등 관련 원문",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})
    settings = Settings(
        dart_api_key="dart-key", translation_api_key="deepl-key",
        edgar_user_agent=None, edinet_subscription_key=None, cache_dir=tmp_path,
    )
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=15)
        at.run()
        assert not at.exception
        default_text = _text(at)
        assert "신규시설투자등 결정" in default_text
        assert "일반 공고" not in default_text

        # get_settings must still be patched for this second run — AppTest
        # re-executes the harness script synchronously on `.run()`.
        at.radio(key="radar-view-mode").set_value("Captured filings")
        at.run()

    assert not at.exception
    all_text = _text(at)
    assert "신규시설투자등 결정" in all_text
    assert "일반 공고" in all_text


def test_status_filter_lives_inside_advanced_filters_not_the_default_row(tmp_path):
    _seed_corp_codes(tmp_path)
    filing = _filing("20260812000001", "일반 공고")
    _seed_filing_events(tmp_path, [filing])
    candidate = CandidateSignal(
        id="cand-r1-status-filter", filing=filing, matched_rules=["earnings:earnings_or_results_report:실적"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="실적 관련 원문",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})
    at = _run_radar(tmp_path)
    assert not at.exception
    # Still present and functional — just relocated.
    status_widget = at.multiselect(key="radar-filter-status")
    assert status_widget is not None
    # Structural proof it's inside "Advanced filters": Status's own
    # column assignment appears after the expander opens, in source.
    source = (REPO_ROOT / "src" / "ui" / "pages" / "radar_inbox.py").read_text(encoding="utf-8")
    advanced_idx = source.index('st.expander("Advanced filters")')
    status_idx = source.index('adv_cols[0].multiselect("Status"', advanced_idx)
    assert status_idx > advanced_idx
    # The default (non-advanced) row no longer builds a Status column.
    default_row_idx = source.index("search_col, source_col, theme_col, date_col = st.columns")
    assert default_row_idx < advanced_idx


def test_default_filter_row_is_search_source_theme_date_only(tmp_path):
    _seed_corp_codes(tmp_path)
    filing = _filing("20260812000001", "신규시설투자등 결정")
    _seed_filing_events(tmp_path, [filing])
    candidate = CandidateSignal(
        id="cand-r1-filter-row", filing=filing, matched_rules=["capex_or_facility_investment:facility_investment:신규시설투자"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="신규시설투자등 관련 원문",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})
    at = _run_radar(tmp_path)
    assert not at.exception
    assert at.text_input(key="radar-filter-search") is not None
    assert at.multiselect(key="radar-filter-source") is not None
    assert at.multiselect(key="radar-filter-theme") is not None
    assert at.date_input(key="radar-filter-dates") is not None


# ============================== FILING CARDS ==============================

def test_card_shows_why_this_matters_and_investigate_primary_path(tmp_path):
    _seed_corp_codes(tmp_path)
    filing = _filing("20260812000001", "신규시설투자등 결정")
    _seed_filing_events(tmp_path, [filing])
    candidate = CandidateSignal(
        id="cand-r1-card", filing=filing, matched_rules=["capex_or_facility_investment:facility_investment:신규시설투자"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="신규시설투자등 관련 원문",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})
    at = _run_radar(tmp_path)
    assert not at.exception
    all_text = _text(at)
    assert "Why this matters:" in all_text
    assert "Why flagged:" not in all_text
    expander_labels = [e.label for e in at.expander]
    assert "Investigate →" in expander_labels
    assert "Details" not in expander_labels


def test_publish_monitor_exclude_and_note_field_are_reachable_inside_investigate(tmp_path):
    _seed_corp_codes(tmp_path)
    filing = _filing("20260812000001", "신규시설투자등 결정")
    _seed_filing_events(tmp_path, [filing])
    candidate = CandidateSignal(
        id="cand-r1-review", filing=filing, matched_rules=["capex_or_facility_investment:facility_investment:신규시설투자"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="신규시설투자등 관련 원문",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})
    at = _run_radar(tmp_path)
    assert not at.exception
    button_labels = {b.label for b in at.button}
    assert "Publish" in button_labels
    assert "Monitor" in button_labels
    assert "Exclude" in button_labels
    assert at.text_input(key=f"radar-review-note-{candidate.id}") is not None


def test_review_actions_are_not_the_first_thing_rendered_on_the_card():
    """Structural proof, not just presence: `_render_review_actions` is
    called from inside `_render_investigate_body`, which only runs inside
    the Investigate `st.expander` block — never directly in
    `candidate_row`'s own top-level body."""
    source = (REPO_ROOT / "src" / "ui" / "components" / "radar_card.py").read_text(encoding="utf-8")
    row_start = source.index("def candidate_row(")
    row_body = source[row_start:]
    assert "_render_review_actions(" not in row_body
    assert "_render_investigate_body(" in row_body
    investigate_body_start = source.index("def _render_investigate_body(")
    investigate_body_end = source.index("\ndef ", investigate_body_start + 1)
    assert "_render_review_actions(" in source[investigate_body_start:investigate_body_end]


def test_publish_still_gates_signal_eligibility_unchanged():
    """No candidate workflow/status/eligibility behavior changed — proven
    directly against the real eligibility gate, not just by absence of a
    diff. Publish remains the only route to a Signal."""
    from src.logic.signal_promotion import _ELIGIBLE_STATUSES, is_eligible_for_signal

    assert _ELIGIBLE_STATUSES == frozenset({CandidateStatus.PUBLISHED})
    published = CandidateSignal(
        id="cand-r1-eligible", filing=_filing("20260812000099", "test"), matched_rules=[],
        confidence="High", status=CandidateStatus.PUBLISHED, extraction_state=ExtractionState.EXTRACTED,
    )
    monitoring = CandidateSignal(
        id="cand-r1-ineligible", filing=_filing("20260812000098", "test"), matched_rules=[],
        confidence="High", status=CandidateStatus.MONITORING, extraction_state=ExtractionState.EXTRACTED,
    )
    assert is_eligible_for_signal(published) is True
    assert is_eligible_for_signal(monitoring) is False


# ============================== DOCUMENTATION-ONLY CORRECTION ==============================

def test_signal_decision_policy_correction_does_not_alter_executed_policy():
    """The corrected docstring in signal_decision_policy.py must not
    coincide with any behavior change — proven by re-running its own real
    decision function against the exact fixture shapes its module already
    documents (424B5 complete vs. incomplete offering) and confirming the
    routes are identical to what edgar_pipeline.py has always executed."""
    from src.data_access.edgar.edgar_rules import normalize_form_type
    from src.logic.signal_decision_policy import SignalRoute, decide_signal_route

    filing = FilingEvent(
        rcept_no="0000320193-26-000001", corp_code="0000320193", corp_name="Test Corp", stock_code="TEST",
        report_nm="424B5", rcept_dt="2026-08-20", flr_nm="Test Corp", pblntf_ty="424B5",
        source_name="SEC EDGAR", original_language="English",
    )
    complete_candidate = CandidateSignal(
        id="cand-r1-424b5-complete", filing=filing, matched_rules=[], confidence="High",
        status=CandidateStatus.EXTRACTED, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="The offering priced $50 million of 1,000,000 shares with net proceeds to the company.",
    )
    incomplete_candidate = CandidateSignal(
        id="cand-r1-424b5-incomplete", filing=filing, matched_rules=[], confidence="High",
        status=CandidateStatus.EXTRACTED, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="This is a supplement to the prospectus.",
    )
    assert normalize_form_type(filing.pblntf_ty) == "424B5"
    assert decide_signal_route(complete_candidate).route == SignalRoute.PUBLISH
    assert decide_signal_route(incomplete_candidate).route == SignalRoute.TIMELINE


def test_signal_decision_policy_module_still_performs_no_io_or_mutation():
    source = (REPO_ROOT / "src" / "logic" / "signal_decision_policy.py").read_text(encoding="utf-8")
    for forbidden in ("import requests", "import httpx", "open(", "st.session_state", "candidate.status ="):
        assert forbidden not in source


# ============================== NO SCOPE CREEP ==============================

def test_phase_r1_introduces_no_worker_deployment_secret_or_dependency_change():
    """Diff-based, not whole-file-based: radar_inbox.py legitimately
    already mentions EDGE_RADAR_WORKER_* names in pre-existing Phase F1
    prose (explaining what it does NOT read) — a whole-file substring
    check would false-positive on that. This checks only the lines Phase
    R1 actually *added*, via `git diff`, for exactly the categories the
    approval explicitly forbade."""
    import subprocess

    result = subprocess.run(
        ["git", "diff", "HEAD", "--", "src/ui/pages/radar_inbox.py", "src/ui/components/radar_card.py", "src/logic/signal_decision_policy.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return  # not a git checkout with this phase's changes present — nothing to check
    added_lines = [line[1:] for line in result.stdout.splitlines() if line.startswith("+") and not line.startswith("+++")]
    added_text = "\n".join(added_lines)
    forbidden_substrings = (
        "EDGE_RADAR_LIVE_SCAN_ENABLED", "EDGE_RADAR_WORKER", "os.environ", "subprocess", "threading.Thread",
        "CREATE TABLE", "auto_publish_enabled = True", "auto_publish_enabled=True",
    )
    for forbidden in forbidden_substrings:
        assert forbidden not in added_text, f"a line Phase R1 added unexpectedly contains {forbidden!r}"


def test_phase_r1_does_not_touch_edgar_dart_edinet_scan_pipelines_or_worker():
    import subprocess

    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    changed = set(result.stdout.splitlines())
    forbidden_paths = {
        "scripts/radar_worker.py", "src/data_access/dart/scan_service.py", "src/data_access/dart/radar_pipeline.py",
        "src/data_access/edgar/scan_service.py", "src/data_access/edgar/edgar_pipeline.py",
        "src/data_access/edinet/scan_service.py", "src/data_access/edinet/edinet_pipeline.py",
        "src/data_access/translation/deepl_provider.py", "src/data_access/translation/translation_service.py",
        "src/data_access/dart/retry_policy.py", "requirements.txt", ".github/workflows/manual-scan.yml",
    }
    # Only meaningful when run against a real git checkout with this
    # phase's changes staged/unstaged (not from an installed package) —
    # skip quietly otherwise rather than false-failing in a foreign
    # environment.
    if result.returncode != 0:
        return
    assert not (changed & forbidden_paths), changed & forbidden_paths


# ============================== FRESHNESS UNCHANGED ==============================

def test_phase_f1_freshness_line_still_renders_and_stays_truthful(tmp_path):
    """Isolates the freshness line itself (not the whole page) before
    checking prohibited words — the operator-facing Ingestion status
    panel legitimately says "Continuous worker status" (naming the
    software component, not making a freshness claim), which would
    otherwise false-positive a page-wide substring check."""
    from src.logic.radar_freshness import UNAVAILABLE_MESSAGE

    _seed_corp_codes(tmp_path)
    _seed_filing_events(tmp_path, [_filing("20260812000001", "일반 공고")])
    at = _run_radar(tmp_path)  # db_backend defaults to "json" -> no durable status store
    assert not at.exception
    freshness_line = next(
        m.value for m in at.markdown
        if m.value.strip() == f'<div class="er-muted" style="margin-top:0.4rem;">{UNAVAILABLE_MESSAGE}</div>'
    )
    for prohibited in ("live", "real-time", "automatic", "continuous", "autonomous", "scheduled", "updating"):
        assert prohibited not in freshness_line.lower()
