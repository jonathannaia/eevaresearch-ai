"""The autonomous EDGAR 424B5 publication cohort — gate, control,
provenance, public label and withdrawal.

Offline only: in-memory SQLite and synthetic candidates. No network, no
Postgres, no worker run, no scan, no credential. The Postgres control
table's parity is proved separately against a real server.

These tests exist because every one of them is a way the cohort could
publish something it should not, or fail to take something back."""
from __future__ import annotations

import pytest

from src.data_access.state_db import connection, schema
from src.data_access.state_db import edgar_publication_control_repository as control_repo
from src.logic.edgar_publication_gate import (
    REASON_ALLOWED,
    REASON_CONTROL_OFF,
    REASON_FORM_NOT_IN_COHORT,
    REASON_LANE_NOT_ELIGIBLE,
    REASON_ROUTE_NOT_PUBLISH,
    REASON_SETTING_DISABLED,
    may_publish_autonomously,
)
from src.logic.signal_decision_policy import SignalRoute
from src.logic.signal_promotion import (
    AUTOMATED_DISCLOSURE_LABEL,
    PUBLISHED_BY_AUTONOMOUS_EDGAR,
    is_autonomously_published,
    is_eligible_for_signal,
    candidate_to_signal,
)
from src.logic.signal_withdrawal import (
    OUTCOME_ALREADY_WITHDRAWN,
    OUTCOME_NOT_AUTONOMOUS,
    OUTCOME_NOT_PUBLISHED,
    OUTCOME_WITHDRAWN,
    withdraw_autonomous_signal,
)
from src.models.models import CandidateSignal, CandidateStatus, FilingEvent

_ALL_ON = dict(
    source_name="SEC EDGAR", form_type="424B5", route=SignalRoute.PUBLISH,
    setting_enabled=True, control_enabled=True,
)


def _filing(form="424B5", source="SEC EDGAR", accession="0001045810-26-000001") -> FilingEvent:
    return FilingEvent(
        rcept_no=accession, corp_code="0001045810", corp_name="Synthetic Issuer Inc.",
        stock_code="SYN", report_nm=form, rcept_dt="2026-01-15", flr_nm="Synthetic Issuer Inc.",
        source_name=source, original_language="English", theme_slug="ai-buildout",
        # pblntf_ty is the canonical SEC form code the gate and the
        # policy both read; report_nm is only a document description.
        pblntf_ty=form,
        source_url="https://www.sec.gov/Archives/edgar/data/1045810/example.htm",
    )


def _candidate(status=CandidateStatus.PUBLISHED, published_by=PUBLISHED_BY_AUTONOMOUS_EDGAR, **kw) -> CandidateSignal:
    return CandidateSignal(
        id="cand-424b5-1", filing=_filing(**kw), matched_rules=["424B5 offering"],
        confidence="High", status=status, published_by=published_by,
        excerpt_original="The Company is offering $250,000,000 of shares; net proceeds will be used for general purposes.",
    )


# --- the gate ---------------------------------------------------------------

def test_a_fully_qualifying_424b5_is_eligible_when_every_gate_is_configured():
    decision = may_publish_autonomously(**_ALL_ON)
    assert decision.allowed is True
    assert decision.reason == REASON_ALLOWED


@pytest.mark.parametrize("override, expected_reason", [
    ({"form_type": "8-K"}, REASON_FORM_NOT_IN_COHORT),
    ({"form_type": "10-K"}, REASON_FORM_NOT_IN_COHORT),
    ({"form_type": "424B3"}, REASON_FORM_NOT_IN_COHORT),
    ({"form_type": ""}, REASON_FORM_NOT_IN_COHORT),
    ({"form_type": None}, REASON_FORM_NOT_IN_COHORT),
    ({"source_name": "EDINET"}, REASON_LANE_NOT_ELIGIBLE),
    ({"source_name": "OpenDART / DART"}, REASON_LANE_NOT_ELIGIBLE),
    ({"source_name": None}, REASON_LANE_NOT_ELIGIBLE),
    ({"route": SignalRoute.REVIEW}, REASON_ROUTE_NOT_PUBLISH),
    ({"route": SignalRoute.TIMELINE}, REASON_ROUTE_NOT_PUBLISH),
    ({"route": None}, REASON_ROUTE_NOT_PUBLISH),
    ({"setting_enabled": False}, REASON_SETTING_DISABLED),
    ({"control_enabled": False}, REASON_CONTROL_OFF),
])
def test_every_single_missing_condition_denies_publication(override, expected_reason):
    decision = may_publish_autonomously(**{**_ALL_ON, **override})
    assert decision.allowed is False
    assert decision.reason == expected_reason


def test_an_excluded_form_cannot_publish_even_when_a_rule_routes_it_to_publish():
    """The whole reason the gate is cohort-scoped rather than
    route-scoped: a future rule returning PUBLISH for another form must
    not start publishing on the day it ships."""
    for form in ("8-K", "10-Q", "S-1", "424B3", "6-K"):
        decision = may_publish_autonomously(**{**_ALL_ON, "form_type": form})
        assert decision.allowed is False, form


def test_the_gate_denies_when_nothing_is_supplied_at_all():
    decision = may_publish_autonomously(
        source_name=None, form_type=None, route=None, setting_enabled=False, control_enabled=False,
    )
    assert decision.allowed is False


# --- the database control ---------------------------------------------------

@pytest.fixture
def conn():
    c = connection.connect_in_memory()
    schema.migrate(c)
    return c


def test_an_absent_control_row_reads_as_disabled(conn):
    assert control_repo.get_control(conn) is None
    assert control_repo.is_publication_enabled(conn) is False


def test_an_enabled_control_reads_as_enabled_and_round_trips(conn):
    control_repo.set_control(conn, enabled=True, reason="cohort start", updated_by="ops", now="t0")

    control = control_repo.get_control(conn)
    assert control.enabled is True
    assert control.reason == "cohort start"
    assert control_repo.is_publication_enabled(conn) is True


def test_turning_the_control_off_disables_publication(conn):
    control_repo.set_control(conn, enabled=True, reason="on", updated_by="ops", now="t0")
    control_repo.set_control(conn, enabled=False, reason="halt", updated_by="ops", now="t1")

    assert control_repo.is_publication_enabled(conn) is False
    assert control_repo.get_control(conn).reason == "halt"


def test_a_missing_control_table_fails_closed(conn):
    conn.execute("DROP TABLE edgar_publication_control")

    assert control_repo.is_publication_enabled(conn) is False


def test_an_unreadable_connection_fails_closed(conn):
    conn.close()

    assert control_repo.is_publication_enabled(conn) is False


def test_setting_the_control_is_idempotent_and_keeps_one_row(conn):
    for index in range(3):
        control_repo.set_control(conn, enabled=True, reason=f"r{index}", updated_by="ops", now=f"t{index}")

    assert conn.execute("SELECT COUNT(*) AS n FROM edgar_publication_control").fetchone()["n"] == 1


# --- provenance, promotion and the public label -----------------------------

def test_a_qualifying_autonomous_candidate_promotes_to_a_signal_with_full_evidence():
    signal = candidate_to_signal(_candidate())

    assert signal.issuer == "Synthetic Issuer Inc."          # issuer
    assert signal.source_url.startswith("https://www.sec.gov/")  # source document link
    assert signal.source_name == "SEC EDGAR"
    assert signal.published_by == PUBLISHED_BY_AUTONOMOUS_EDGAR   # publication provenance
    assert is_autonomously_published(signal) is True             # visible automation label
    assert signal.is_demo is False


def test_the_accession_identifier_survives_promotion_through_the_signal_id():
    """The document identifier has to remain recoverable from a public
    Signal, or a withdrawal request cannot be tied to a filing."""
    signal = candidate_to_signal(_candidate())

    assert signal.id == "signal-cand-424b5-1"


def test_a_human_published_signal_never_receives_the_automation_label():
    signal = candidate_to_signal(_candidate(published_by="human_reviewer"))

    assert signal.published_by == "human_reviewer"
    assert is_autonomously_published(signal) is False


def test_a_signal_with_no_provenance_never_receives_the_automation_label():
    signal = candidate_to_signal(_candidate(published_by=None))

    assert is_autonomously_published(signal) is False


def test_the_label_appears_on_every_public_rendering_path():
    """Signals page card, compact row and Dashboard priority row all
    render through this one helper, so none can drift."""
    from src.ui.components import cards

    autonomous = candidate_to_signal(_candidate())
    human = candidate_to_signal(_candidate(published_by="human_reviewer"))

    assert AUTOMATED_DISCLOSURE_LABEL in cards.automated_disclosure_html(autonomous)
    assert cards.automated_disclosure_html(human) == ""


def test_the_label_leaks_no_rule_form_identifier_or_internal_detail():
    from src.ui.components import cards

    html = cards.automated_disclosure_html(candidate_to_signal(_candidate()))

    for leak in ("424B5", "cand-424b5-1", "0001045810", "offering", "SEC EDGAR", "sec.gov", "High"):
        assert leak not in html


# --- withdrawal -------------------------------------------------------------

def test_withdrawal_removes_an_autonomous_signal_from_every_public_query_path():
    candidate = _candidate()
    assert is_eligible_for_signal(candidate) is True

    result = withdraw_autonomous_signal(candidate, reason="excerpt did not support it", mode="live", now="t1")

    assert result.outcome == OUTCOME_WITHDRAWN
    assert result.candidate.status is CandidateStatus.WITHDRAWN
    # Promotion is an allowlist, so leaving PUBLISHED is what removes it
    # from every public surface — no query has to exclude it by name.
    assert is_eligible_for_signal(result.candidate) is False


def test_withdrawal_retains_the_record_its_evidence_and_an_audit_trail():
    candidate = _candidate()
    result = withdraw_autonomous_signal(candidate, reason="bad match", mode="live", now="t1")

    assert result.candidate.id == "cand-424b5-1"                    # record retained
    assert result.candidate.excerpt_original                        # evidence retained
    assert result.candidate.filing.rcept_no == "0001045810-26-000001"
    transition = result.candidate.state_history[-1]
    assert transition.status is CandidateStatus.WITHDRAWN
    assert transition.at == "t1"                                    # timestamp
    assert "actor=operator" in transition.detail                    # actor
    assert "mode=live" in transition.detail                         # mode
    assert "reason=bad match" in transition.detail                  # reason


def test_repeated_withdrawal_is_safe_and_records_no_duplicate_audit_row():
    candidate = _candidate()
    first = withdraw_autonomous_signal(candidate, reason="bad match", mode="live", now="t1")
    before = len(first.candidate.state_history)

    for tick in ("t2", "t3", "t4"):
        repeat = withdraw_autonomous_signal(first.candidate, reason="again", mode="live", now=tick)
        assert repeat.outcome == OUTCOME_ALREADY_WITHDRAWN
        assert repeat.changed is False

    assert len(first.candidate.state_history) == before


def test_a_human_published_signal_cannot_be_withdrawn_by_this_mechanism():
    candidate = _candidate(published_by="human_reviewer")

    result = withdraw_autonomous_signal(candidate, reason="x", mode="live", now="t1")

    assert result.outcome == OUTCOME_NOT_AUTONOMOUS
    assert result.changed is False
    assert result.candidate.status is CandidateStatus.PUBLISHED   # untouched
    assert is_eligible_for_signal(result.candidate) is True       # still public


def test_an_unpublished_autonomous_candidate_cannot_be_withdrawn():
    result = withdraw_autonomous_signal(
        _candidate(status=CandidateStatus.NEEDS_REVIEW), reason="x", mode="live", now="t1",
    )

    assert result.outcome == OUTCOME_NOT_PUBLISHED
    assert result.changed is False


def test_withdrawal_does_not_affect_other_candidates():
    withdrawn = withdraw_autonomous_signal(_candidate(), reason="x", mode="live", now="t1").candidate
    other = _candidate()

    assert is_eligible_for_signal(withdrawn) is False
    assert is_eligible_for_signal(other) is True


# --- replay / retry idempotency --------------------------------------------

def test_a_replayed_accession_yields_at_most_one_public_signal():
    """A worker restart mid-tick, or a retried scan, must not publish
    the same offering twice. Candidate identity is the accession
    number, and Signal identity derives from the candidate id, so the
    whole chain collapses to one public item."""
    from src.data_access.edgar.scan_service import dedup_key

    accession = "0001045810-26-000001"
    keys = {dedup_key("0001045810", accession) for _ in range(5)}
    assert len(keys) == 1                      # the scan dedup key is stable

    signals = [candidate_to_signal(_candidate(accession=accession)) for _ in range(5)]
    assert len({s.id for s in signals}) == 1   # and so is the public Signal id


def test_two_different_accessions_remain_two_distinct_signals():
    from src.data_access.edgar.scan_service import dedup_key

    first = dedup_key("0001045810", "0001045810-26-000001")
    second = dedup_key("0001045810", "0001045810-26-000002")

    assert first != second


def test_the_candidate_repository_upsert_keeps_one_row_per_accession(conn):
    """Persistence-level replay guard, against a real SQLite store."""
    from src.data_access.state_db import candidate_repository

    candidate = _candidate()
    for _ in range(3):
        candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [candidate])

    rows = conn.execute("SELECT COUNT(*) AS n FROM candidates WHERE id = ?", (candidate.id,)).fetchone()
    assert rows["n"] == 1


# --- BLOCKER-1 regression: the control is re-read every tick ---------------
#
# The control used to be resolved once, in _build_worker_settings(),
# which runs before the worker's tick loop. `--off` therefore needed a
# process restart to take effect — the opposite of what a database-backed
# emergency stop is for. It is now resolved per call by
# edgar_service.run_scan(), and the worker calls that once per tick.

def test_the_worker_settings_builder_freezes_no_control_state(tmp_path):
    """_build_worker_settings runs once, before the tick loop, so
    anything it decides about publication would outlive every tick."""
    import dataclasses

    from scripts import radar_worker
    from src.config.settings import Settings

    ambient = dataclasses.replace(
        Settings(), edgar_auto_publish_enabled=True,
        radar_worker_db_backend="sqlite",
        radar_worker_state_db_path=str(tmp_path / "state.db"),
    )
    worker_settings = radar_worker._build_worker_settings(ambient)

    # It carries the setting and decides nothing about the control.
    assert worker_settings.edgar_auto_publish_enabled is True
    assert not hasattr(worker_settings, "publication_control_enabled")


def test_the_control_is_resolved_on_every_scan_not_once_per_process(monkeypatch, tmp_path):
    """Tick 1 enabled, tick 2 disabled: the second scan must see the
    change with no restart."""
    from src.config.settings import Settings
    from src.data_access.edgar import edgar_service

    states = [True, False]
    seen: list[bool] = []

    monkeypatch.setattr(edgar_service, "resolve_publication_control_enabled", lambda s: states.pop(0))
    monkeypatch.setattr(edgar_service, "_client", lambda s: object())
    monkeypatch.setattr(edgar_service, "get_edgar_companies", lambda cache_dir, settings=None: ())

    def _capture(*_args, **kwargs):
        seen.append(kwargs["publication_control_enabled"])
        return object()

    monkeypatch.setattr(edgar_service.edgar_pipeline, "run_pipeline", _capture)
    # The setting must be on, or the control is never consulted at all.
    settings = Settings(cache_dir=tmp_path, edgar_auto_publish_enabled=True)

    edgar_service.run_scan(settings)   # tick 1
    edgar_service.run_scan(settings)   # tick 2, after `--off`

    assert seen == [True, False]       # re-read, not cached


# --- BLOCKER-2 regression: no caller can bypass the control ----------------

def test_the_pipeline_defaults_the_control_to_false_at_every_boundary():
    """A caller that omits the control cannot publish, however it sets
    auto_publish_enabled."""
    import inspect

    from src.data_access.edgar import edgar_pipeline

    for fn in (edgar_pipeline.run_pipeline, edgar_pipeline.process_candidate,
               edgar_pipeline.process_single_candidate):
        default = inspect.signature(fn).parameters["publication_control_enabled"].default
        assert default is False, fn.__name__


def test_supplying_auto_publish_true_alone_never_publishes():
    """The exact bypass: setting on, control absent."""
    decision = may_publish_autonomously(**{**_ALL_ON, "control_enabled": False})

    assert decision.allowed is False
    assert decision.reason == REASON_CONTROL_OFF


@pytest.mark.parametrize("entry_point", ["run_scan", "process_candidate_now"])
def test_every_service_entry_point_resolves_the_real_control(monkeypatch, tmp_path, entry_point):
    """run_scan and process_candidate_now both reach a publishing-capable
    pipeline, so both must read the database control rather than trust a
    caller-supplied boolean."""
    from src.config.settings import Settings
    from src.data_access.edgar import edgar_service

    resolved: list[str] = []
    monkeypatch.setattr(
        edgar_service, "resolve_publication_control_enabled",
        lambda s: resolved.append(entry_point) or False,
    )
    monkeypatch.setattr(edgar_service, "_client", lambda s: object())
    monkeypatch.setattr(edgar_service, "get_edgar_companies", lambda cache_dir, settings=None: ())

    captured: dict = {}

    def _capture(*_args, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(edgar_service.edgar_pipeline, "run_pipeline", _capture)
    monkeypatch.setattr(edgar_service.edgar_pipeline, "process_single_candidate", _capture)

    settings = Settings(cache_dir=tmp_path, edgar_auto_publish_enabled=True)
    if entry_point == "run_scan":
        edgar_service.run_scan(settings)
    else:
        edgar_service.process_candidate_now(settings, "cand-1")

    assert resolved == [entry_point]                        # the control was read
    assert captured["publication_control_enabled"] is False  # and it said no


def test_an_unreadable_control_resolves_to_disabled_at_the_service_boundary(tmp_path):
    """The resolver is the single place a database problem could become
    permission. It cannot."""
    from src.config.settings import Settings
    from src.data_access.edgar import edgar_service

    # json backend: no control repository can be constructed at all.
    assert edgar_service.resolve_publication_control_enabled(Settings(cache_dir=tmp_path)) is False


def test_the_resolver_returns_true_only_for_a_genuinely_enabled_control(tmp_path, monkeypatch):
    from src.config.settings import Settings
    from src.data_access import backend_factory
    from src.data_access.edgar import edgar_service
    from src.data_access.state_db import connection, schema
    from src.data_access.state_db import edgar_publication_control_repository as repo

    db = connection.connect_in_memory()
    schema.migrate(db)

    closes: list[int] = []

    class _Repo:
        conn = db

        def is_publication_enabled(self):
            return repo.is_publication_enabled(db)

        def close(self):
            # Counted, not performed: the shared in-memory database has
            # to survive for the rest of this test.
            closes.append(1)

    monkeypatch.setattr(backend_factory, "get_edgar_publication_control_repository", lambda s: _Repo())
    settings = Settings(cache_dir=tmp_path)

    assert edgar_service.resolve_publication_control_enabled(settings) is False   # absent row
    repo.set_control(db, enabled=True, reason="on", updated_by="ops", now="t0")
    assert edgar_service.resolve_publication_control_enabled(settings) is True
    repo.set_control(db, enabled=False, reason="off", updated_by="ops", now="t1")
    assert edgar_service.resolve_publication_control_enabled(settings) is False
    assert len(closes) == 3   # one owned connection closed per resolve


# --- connection lifecycle ---------------------------------------------------
#
# The resolver opens its own connection through the factory, so it owns
# it. Without a finally-close the worker leaked one per tick, idle in
# transaction on Postgres — the same defect PR #85 fixed for source reads.

def _resolver_settings(tmp_path):
    from src.config.settings import Settings

    return Settings(cache_dir=tmp_path, edgar_auto_publish_enabled=True)


class _CountingRepo:
    def __init__(self, result=False, raises=False):
        self._result = result
        self._raises = raises
        self.closes = 0

    def is_publication_enabled(self):
        if self._raises:
            raise RuntimeError("fictional control read failure")
        return self._result

    def close(self):
        self.closes += 1


def test_the_owned_connection_is_closed_after_a_successful_read(monkeypatch, tmp_path):
    from src.data_access import backend_factory
    from src.data_access.edgar import edgar_service

    repo = _CountingRepo(result=True)
    monkeypatch.setattr(backend_factory, "get_edgar_publication_control_repository", lambda s: repo)

    assert edgar_service.resolve_publication_control_enabled(_resolver_settings(tmp_path)) is True
    assert repo.closes == 1


def test_the_owned_connection_is_closed_when_the_read_raises(monkeypatch, tmp_path):
    """The failure path is the one that matters: an error must not leak."""
    from src.data_access import backend_factory
    from src.data_access.edgar import edgar_service

    repo = _CountingRepo(raises=True)
    monkeypatch.setattr(backend_factory, "get_edgar_publication_control_repository", lambda s: repo)

    assert edgar_service.resolve_publication_control_enabled(_resolver_settings(tmp_path)) is False
    assert repo.closes == 1


def test_a_factory_failure_leaves_nothing_to_close_and_still_denies(monkeypatch, tmp_path):
    from src.data_access import backend_factory
    from src.data_access.edgar import edgar_service

    def _raise(_settings):
        raise RuntimeError("fictional factory failure")

    monkeypatch.setattr(backend_factory, "get_edgar_publication_control_repository", _raise)

    assert edgar_service.resolve_publication_control_enabled(_resolver_settings(tmp_path)) is False


def test_a_close_that_itself_fails_is_swallowed_and_preserves_the_answer(monkeypatch, tmp_path):
    """Cleanup must never change the outcome, and must never escape.

    A close() that raises used to propagate out of the resolver's
    finally, through run_scan, and into _run_provider_tick's handler —
    turning an otherwise successful EDGAR scan into a recorded scan
    failure. The answer had already been determined at that point, so
    the exception changed nothing except whether the scan looked
    healthy."""
    from src.data_access import backend_factory
    from src.data_access.edgar import edgar_service

    closes: list[int] = []

    class _UnclosableRepo:
        def is_publication_enabled(self):
            return True

        def close(self):
            closes.append(1)
            raise RuntimeError("fictional close failure")

    monkeypatch.setattr(backend_factory, "get_edgar_publication_control_repository", lambda s: _UnclosableRepo())

    # No exception escapes, and the determination survives intact.
    assert edgar_service.resolve_publication_control_enabled(_resolver_settings(tmp_path)) is True
    assert closes == [1]   # the close really was attempted, not skipped


def test_a_close_that_fails_preserves_a_disabled_answer_too(monkeypatch, tmp_path):
    """The same guarantee in the deny direction: a broken close must not
    accidentally read as permission."""
    from src.data_access import backend_factory
    from src.data_access.edgar import edgar_service

    class _UnclosableDisabledRepo:
        def is_publication_enabled(self):
            return False

        def close(self):
            raise RuntimeError("fictional close failure")

    monkeypatch.setattr(
        backend_factory, "get_edgar_publication_control_repository", lambda s: _UnclosableDisabledRepo(),
    )

    assert edgar_service.resolve_publication_control_enabled(_resolver_settings(tmp_path)) is False


def test_repeated_close_on_a_real_owned_connection_is_safe(tmp_path):
    from src.data_access.state_db import connection, schema
    from src.data_access.state_db import edgar_publication_control_repository as repo

    conn = connection.connect_in_memory()
    schema.migrate(conn)

    repo.close(conn)
    repo.close(conn)      # idempotent
    repo.close(conn)


# --- setting-off short circuit ---------------------------------------------

def test_the_control_is_not_read_at_all_when_the_feature_setting_is_off(monkeypatch, tmp_path):
    """A disabled feature must not pay a per-tick database cost, and a
    database problem must not reach a path that cannot publish."""
    from src.config.settings import Settings
    from src.data_access.edgar import edgar_service

    called: list[int] = []
    monkeypatch.setattr(
        edgar_service, "resolve_publication_control_enabled",
        lambda s: called.append(1) or True,
    )

    settings = Settings(cache_dir=tmp_path)   # setting off
    assert edgar_service._effective_publication_control(settings, None) is False
    assert called == []                       # never consulted


def test_a_supplied_true_control_cannot_override_a_disabled_feature_setting(tmp_path):
    from src.config.settings import Settings
    from src.data_access.edgar import edgar_service

    settings = Settings(cache_dir=tmp_path)   # setting off

    assert edgar_service._effective_publication_control(settings, True) is False


def test_a_supplied_control_is_honoured_only_when_the_setting_is_on(tmp_path):
    import dataclasses

    from src.config.settings import Settings
    from src.data_access.edgar import edgar_service

    on = dataclasses.replace(Settings(cache_dir=tmp_path), edgar_auto_publish_enabled=True)

    assert edgar_service._effective_publication_control(on, True) is True
    assert edgar_service._effective_publication_control(on, False) is False
