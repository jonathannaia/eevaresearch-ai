"""DART issuer fixtures derived from the tracked-company registry.

Why this exists. `radar_readiness()` treats a tracked DART company with
no resolved corp code as unresolved, and `.ready` is False while any
company is unresolved — deliberately fail-closed. Five test files used
to hand-list the tracked DART companies and their corp codes, so every
time the registry gained a company those lists silently fell behind:
readiness stayed False, the Radar page rendered its "not configured"
state instead of any cards, and thirteen tests failed for a reason
unrelated to what they were asserting. The 2026 Tier 1 supply-chain
cohort adding two DART issuers is what put them in that state.

Deriving fixture membership from the registry removes that drift class:
a fixture built here cannot fall behind the registry, because it *is*
the registry.

Test data only. Every corp code produced here is synthetic — generated
deterministically from the company's own krx_code and marked by
`SYNTHETIC_CORP_CODE_PREFIX` and `SYNTHETIC_SOURCE`. These are NOT real
OpenDART corp codes. Nothing here reads a developer cache, a `.env`, or
the network, and no value here may be used to address the live DART API.

Deliberately NOT a conftest fixture and NOT autouse — an ordinary module
that each test file imports explicitly, the same convention as
tests/configured_test_settings.py and tests/no_network.py.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.config.tracked_companies import TrackedCompany, get_tracked_companies_for_source

DART_SOURCE = "OpenDART / DART"

#: Marks a corp code as fixture data. Real OpenDART corp codes are never
#: constructed here, so a value carrying this prefix can always be
#: recognised as synthetic — asserted by this module's own test.
SYNTHETIC_CORP_CODE_PREFIX = "99"
SYNTHETIC_SOURCE = "synthetic-test-fixture"

_RETRIEVED_AT = "2026-01-01T00:00:00+00:00"


def tracked_dart_companies() -> tuple[TrackedCompany, ...]:
    """Every tracked DART company, straight from the registry — the one
    set `radar_readiness()` itself iterates."""
    return tuple(get_tracked_companies_for_source(DART_SOURCE))


def synthetic_corp_code(krx_code: str) -> str:
    """A deterministic, unique, obviously-synthetic corp code for a
    krx_code. Deterministic so a fixture is reproducible run to run;
    unique because krx_code is unique in the registry."""
    return f"{SYNTHETIC_CORP_CODE_PREFIX}{krx_code}"


def _normalized_omit(omit) -> tuple[str, ...]:
    """Materialize `omit` once, then reject anything that is not a tracked
    DART krx_code.

    Materializing matters: `omit` is consumed twice here (once to find
    unknown codes, once to filter), so a generator or other one-shot
    iterable would be exhausted by the check and then silently empty
    during seeding — producing a fully seeded fixture for a caller who
    asked for an omission. Returning a tuple makes both passes see the
    same values.

    Rejecting matters for the same reason a wrong fixture is worse than
    a missing one: a mistyped code ("00660" for "000660") would
    otherwise match nothing, the fixture would come out fully seeded,
    and a test written to assert the *not-ready* path would silently
    assert the *ready* path instead.
    """
    codes = tuple(omit)
    known = {c.krx_code for c in tracked_dart_companies()}
    unknown = sorted(set(codes) - known)
    if unknown:
        raise ValueError(
            f"omit names krx_codes that are not tracked DART companies: {unknown}. "
            f"Tracked DART krx_codes are: {sorted(known)}."
        )
    return codes


def corp_code_payload(omit=()) -> dict[str, dict[str, str]]:
    """`dart_corp_codes.json`'s own on-disk shape for every tracked DART
    company, so the tests exercise the real loader rather than a stub.

    `omit` lists krx_codes to leave *out*, for a test that needs a
    genuinely unresolved company. Callers pass explicit literal
    krx_codes and assert explicit literal company names, so the
    omission and the expected outcome never come from the same
    derivation.
    """
    omitted = _normalized_omit(omit)
    return {
        c.krx_code: {
            "corp_code": synthetic_corp_code(c.krx_code),
            "corp_name": c.name,
            "source": SYNTHETIC_SOURCE,
            "retrieved_at": _RETRIEVED_AT,
        }
        for c in tracked_dart_companies()
        if c.krx_code not in omitted
    }


def seed_corp_codes(cache_dir: Path, omit=()) -> dict[str, dict[str, str]]:
    """Write the payload to the caller's own `cache_dir` (always a
    tmp_path — never the real data/cache). Returns what it wrote.

    The payload — and therefore `omit` validation — is built before any
    directory is created or any file written, so an invalid `omit`
    raises without creating or overwriting `dart_corp_codes.json`.
    """
    payload = corp_code_payload(omit=omit)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "dart_corp_codes.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def seed_sqlite_identifiers(conn, omit=()) -> tuple[str, ...]:
    """The same membership through the real sqlite identifier
    repository, for the test that exercises that path end to end rather
    than the JSON cache. Returns the krx_codes written.

    `omit` is validated up front, before the first upsert, so an invalid
    `omit` raises without inserting any row.
    """
    from src.data_access.state_db.identifier_repository import (
        ResolvedIdentifierRecord,
        upsert_resolved_identifier,
    )

    omitted = _normalized_omit(omit)
    written: list[str] = []
    for company in tracked_dart_companies():
        if company.krx_code in omitted:
            continue
        upsert_resolved_identifier(
            conn, DART_SOURCE, company.krx_code,
            ResolvedIdentifierRecord(
                identifier=synthetic_corp_code(company.krx_code), display_name=company.name,
                resolution_method=SYNTHETIC_SOURCE, retrieved_at=_RETRIEVED_AT,
            ),
        )
        written.append(company.krx_code)
    return tuple(written)


def tracked_dart_name_for(krx_code: str) -> str:
    """The registry's own name for a krx_code. Used only to *check* that
    a test's literal expectation still matches the registry — never to
    produce the expectation itself."""
    for company in tracked_dart_companies():
        if company.krx_code == krx_code:
            return company.name
    raise AssertionError(f"krx_code {krx_code!r} is no longer a tracked DART company")
