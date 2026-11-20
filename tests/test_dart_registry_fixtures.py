"""Guards on tests/dart_registry_fixtures.py itself — the registry-derived
DART fixture that five test files now share."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.config.tracked_companies import get_tracked_companies_for_source
from src.data_access import backend_factory
from src.data_access.dart import radar_service
from src.config.settings import Settings
from tests.dart_registry_fixtures import (
    DART_SOURCE,
    seed_sqlite_identifiers,
    SYNTHETIC_CORP_CODE_PREFIX,
    SYNTHETIC_SOURCE,
    corp_code_payload,
    seed_corp_codes,
    synthetic_corp_code,
    tracked_dart_companies,
    tracked_dart_name_for,
)


def _sqlite_identifier_conn(tmp_path):
    """A real sqlite identifier repository on the caller's own tmp_path."""
    settings = Settings(
        dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path,
        db_backend="sqlite", state_db_path=tmp_path / "state.db",
    )
    return backend_factory.get_identifier_repository(settings, DART_SOURCE).conn


def _identifier_row_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM resolved_identifiers").fetchone()[0]


def test_membership_is_the_registry_not_a_hand_written_list():
    """The whole point: the fixture cannot fall behind the registry."""
    registry = {c.krx_code for c in get_tracked_companies_for_source(DART_SOURCE)}

    assert registry, "registry returned no DART companies"
    assert set(corp_code_payload()) == registry


def test_every_corp_code_is_obviously_synthetic():
    """No real OpenDART corp code can be introduced here unnoticed."""
    for krx_code, record in corp_code_payload().items():
        assert record["corp_code"].startswith(SYNTHETIC_CORP_CODE_PREFIX), krx_code
        assert record["source"] == SYNTHETIC_SOURCE, krx_code


def test_corp_codes_are_unique_and_deterministic():
    first, second = corp_code_payload(), corp_code_payload()
    codes = [r["corp_code"] for r in first.values()]

    assert len(set(codes)) == len(codes), "synthetic corp codes collide"
    assert first == second, "fixture is not deterministic across calls"


def test_omit_removes_exactly_the_named_company():
    omitted = "000660"
    full, partial = corp_code_payload(), corp_code_payload(omit=(omitted,))

    assert omitted in full
    assert omitted not in partial
    assert set(full) - set(partial) == {omitted}


def test_seed_writes_to_the_callers_cache_dir_only(tmp_path):
    seed_corp_codes(tmp_path)

    assert (tmp_path / "dart_corp_codes.json").exists()
    assert Path("data/cache").resolve() != tmp_path.resolve()


def test_the_real_loader_reads_what_the_fixture_wrote(tmp_path):
    """Non-vacuity: the fixture is consumed by production's own reader,
    so a shape change breaks this rather than passing silently."""
    seed_corp_codes(tmp_path)

    companies = radar_service.get_radar_companies(tmp_path)
    by_krx = {c.krx_code: c for c in companies}

    for company in tracked_dart_companies():
        assert by_krx[company.krx_code].corp_code == synthetic_corp_code(company.krx_code)


def test_a_fully_seeded_fixture_produces_the_ready_state(tmp_path):
    seed_corp_codes(tmp_path)
    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)

    readiness = radar_service.radar_readiness(settings)

    assert readiness.unresolved_companies == ()
    assert readiness.ready


def test_an_omitted_company_produces_the_not_ready_state(tmp_path):
    """The complement of the test above, so "ready" is not asserted in
    isolation: omitting one literal krx_code must surface that one
    literal company name and leave readiness not ready."""
    seed_corp_codes(tmp_path, omit=("000660",))
    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)

    readiness = radar_service.radar_readiness(settings)

    assert readiness.unresolved_companies == ("SK Hynix",)
    assert not readiness.ready


# --- invalid `omit` ---------------------------------------------------------
#
# A mistyped omission used to be silently ignored: the fixture came out
# fully seeded, so a test written to assert the not-ready path would have
# asserted the ready path instead. These pin the rejection, and pin that
# nothing is written or inserted when it fires.

_UNKNOWN_KRX_CODE = "000000"          # not a tracked DART company at all
_MISTYPED_KRX_CODE = "00660"          # five digits — a typo for "000660"


@pytest.mark.parametrize("bad", [_UNKNOWN_KRX_CODE, _MISTYPED_KRX_CODE])
def test_payload_rejects_an_invalid_omitted_code(bad):
    with pytest.raises(ValueError) as excinfo:
        corp_code_payload(omit=(bad,))

    assert bad in str(excinfo.value), "the error must name the invalid code"


@pytest.mark.parametrize("bad", [_UNKNOWN_KRX_CODE, _MISTYPED_KRX_CODE])
def test_json_seeding_rejects_an_invalid_omitted_code_and_writes_nothing(bad, tmp_path):
    target = tmp_path / "dart_corp_codes.json"

    with pytest.raises(ValueError) as excinfo:
        seed_corp_codes(tmp_path, omit=(bad,))

    assert bad in str(excinfo.value)
    assert not target.exists(), "no fixture file may be created on invalid input"


@pytest.mark.parametrize("bad", [_UNKNOWN_KRX_CODE, _MISTYPED_KRX_CODE])
def test_json_seeding_does_not_overwrite_an_existing_file_on_invalid_input(bad, tmp_path):
    target = tmp_path / "dart_corp_codes.json"
    target.write_text("SENTINEL", encoding="utf-8")

    with pytest.raises(ValueError):
        seed_corp_codes(tmp_path, omit=(bad,))

    assert target.read_text(encoding="utf-8") == "SENTINEL", "existing file was overwritten"


@pytest.mark.parametrize("bad", [_UNKNOWN_KRX_CODE, _MISTYPED_KRX_CODE])
def test_sqlite_seeding_rejects_an_invalid_omitted_code_and_inserts_nothing(bad, tmp_path):
    conn = _sqlite_identifier_conn(tmp_path)
    before = _identifier_row_count(conn)

    with pytest.raises(ValueError) as excinfo:
        seed_sqlite_identifiers(conn, omit=(bad,))

    assert bad in str(excinfo.value)
    assert _identifier_row_count(conn) == before == 0, "no row may be inserted on invalid input"


def test_a_one_shot_omit_iterable_is_not_silently_consumed(tmp_path):
    """`omit` is read twice (validate, then filter). A generator would be
    exhausted by the first pass and silently empty for the second,
    yielding a fully seeded fixture for a caller who asked to omit one —
    the exact failure mode the normalization exists to prevent."""
    written = seed_corp_codes(tmp_path, omit=(c for c in ["000660"]))

    assert "000660" not in written
    assert len(written) == len(tracked_dart_companies()) - 1


def test_valid_omissions_still_work_on_both_paths(tmp_path):
    json_written = seed_corp_codes(tmp_path, omit=("000660",))
    conn = _sqlite_identifier_conn(tmp_path)
    sqlite_written = seed_sqlite_identifiers(conn, omit=("000660",))

    assert "000660" not in json_written
    assert "000660" not in sqlite_written
    assert len(json_written) == len(sqlite_written) == len(tracked_dart_companies()) - 1
    assert _identifier_row_count(conn) == len(tracked_dart_companies()) - 1


def test_name_lookup_raises_for_an_untracked_krx_code():
    """tracked_dart_name_for() exists to *check* a test's literal
    expectation, so it must fail loudly rather than return something
    plausible for a code the registry no longer carries."""
    with pytest.raises(AssertionError):
        tracked_dart_name_for("000000")


def test_it_is_not_an_autouse_fixture():
    import tests.dart_registry_fixtures as mod

    assert not hasattr(mod, "pytest_plugins")
    assert not any(
        hasattr(getattr(mod, n), "_pytestfixturefunction") for n in dir(mod) if not n.startswith("__")
    )
