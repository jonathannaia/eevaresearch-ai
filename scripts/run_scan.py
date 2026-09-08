"""Headless CLI entry point for running Radar's existing scan pipeline
outside Streamlit — for CI use. Invoke as:

    .venv/bin/python -m scripts.run_scan

(from the repo root, matching the same `-m` convention `pytest.ini`
already establishes for tests via `pythonpath = .`.)

Deliberately duplicates no scan/business logic: each source's existing
{dart,edgar,edinet}_service.run_scan(settings) is called exactly as
radar_inbox.py's own "Scan {source} now" button already calls it — same
readiness/client/translation-provider construction, same
scan_service/pipeline code, same local candidate_store read/write, same
per-company graceful error handling (a missing API key or a transient
provider error is already caught inside scan_service.scan() and recorded
in ScanReport.errors_by_category/warnings — it does NOT raise there, by
existing design; see scan_service.scan()'s own docstring). This module
only adds: a small per-source try/except (for genuinely unexpected
failures — a bug, a corrupted cache file — that error hierarchy doesn't
already catch), a safe stdout summary, remote-cache sync (reusing
src/data_access/remote_cache/sync.py unchanged), and a CI-friendly exit
code.

Never prints a Settings object, a repr of one, or any raw credential
value — only ScanReport fields (already explicitly designed to be safe/
JSON-serializable — "counts and safe strings only, never raw provider
responses, document contents, or secrets", per each ScanReport's own
docstring) and typed exception messages (each source's error hierarchy
names only which env var is missing, never a value — see
src/data_access/{dart,edgar,edinet}/errors.py). Remote-cache exception
text is never printed at all (only the exception's class name) — a real
boto3/botocore error frequently embeds the request/endpoint URL in its
message.

Exit code: 0 whenever every source's run_scan() call returns normally —
even if the returned ScanReport itself contains per-company/provider
errors or warnings, since scan_service.scan() already treats those as a
completed run with partial issues, not a failure, by existing design —
and remote-cache sync was either skipped (disabled/incomplete config) or
succeeded. Nonzero (1) if any source's run_scan() call raised an
unexpected exception, OR remote caching was explicitly enabled and fully
configured but the sync attempt itself failed.

Durable-State Phase 4A: main() accepts an optional, default-None
`edgar_candidate_repository` parameter — additive, no new environment
variable. Omitted (the CLI's real invocation, `python -m scripts.run_scan`,
always omits it), every source's run_scan() call is made exactly as
before this phase, with zero extra keyword arguments, not merely
`candidate_repository=None`. This is a synthetic/local-test-only seam —
a caller must invoke main() directly with a real Python object, matching
the same "not reachable from any real service entry point in
production" discipline as each pipeline module's own Phase 3A seam and
each service module's own extension of it.

Durable-State Phase 4C-1 extends this identically to DART and EDINET:
main() also accepts `dart_candidate_repository`/`edinet_candidate_repository`,
each additive/optional/default-None. Each of the three parameters
reaches only its own source's run_scan() call — never another source's
— and every source's default call shape (no extra keyword at all) is
unchanged from before this phase.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

from src.config.settings import Settings, get_settings
from src.data_access.dart import radar_service
from src.data_access.dart.candidate_store import CandidatePersistence
from src.data_access.edgar import edgar_service
from src.data_access.edinet import edinet_service
from src.data_access.remote_cache.r2_client import build_r2_client
from src.data_access.remote_cache.sync import remote_cache_available, sync_local_cache_to_remote

_SOURCES = ("dart", "edgar", "edinet")

# Module objects, not bound function references: `.run_scan` is looked
# up dynamically on each call below, so tests that monkeypatch
# {radar,edgar,edinet}_service.run_scan take effect correctly. A dict of
# already-bound function references, captured once at import time, would
# not observe a later patch.object() on the module's own attribute.
_SERVICE_MODULES = {"dart": radar_service, "edgar": edgar_service, "edinet": edinet_service}


@dataclass(frozen=True)
class SourceResult:
    source: str
    ok: bool
    report: Any = None  # a *_pipeline.ScanReport on success
    error: str | None = None  # "ExceptionClassName: message" on failure — never a raw traceback or secret


def _run_one_source(
    source: str, settings: Settings, candidate_repository: CandidatePersistence | None = None,
) -> SourceResult:
    try:
        # Every source's run_scan() accepts candidate_repository as of
        # Durable-State Phase 4C-1 (EDGAR: Phase 4A) — but it's still
        # omitted entirely, rather than passed as an explicit
        # `candidate_repository=None`, when not supplied, so the default
        # call shape stays byte-for-byte what it was before any of these
        # phases. Per-source routing (which of the three main() keywords
        # reaches this call) happens in main() below, not here.
        kwargs = {}
        if candidate_repository is not None:
            kwargs["candidate_repository"] = candidate_repository
        report = _SERVICE_MODULES[source].run_scan(settings, **kwargs)
        return SourceResult(source=source, ok=True, report=report)
    except Exception as exc:  # noqa: BLE001 — an unexpected per-source failure must not crash the other sources'
        return SourceResult(source=source, ok=False, error=f"{type(exc).__name__}: {exc}")


def _report_window(report: Any) -> tuple[str, str]:
    # DART's ScanReport uses bgn_de/end_de (matching DART's own API
    # param names); EDGAR/EDINET use bgn_date/end_date — the three
    # ScanReport dataclasses are intentionally same-shaped but not
    # identical (see edgar_pipeline.ScanReport's own docstring).
    bgn = getattr(report, "bgn_de", None) or getattr(report, "bgn_date", "")
    end = getattr(report, "end_de", None) or getattr(report, "end_date", "")
    return bgn, end


def _format_source_summary(result: SourceResult) -> str:
    if not result.ok:
        return f"  {result.source.upper()}: FAILED — {result.error}"
    r = result.report
    bgn, end = _report_window(r)
    lines = [
        f"  {result.source.upper()}: ok — scan_id={r.scan_id} window={bgn}..{end}",
        f"    filings_discovered={r.filings_discovered} new_filing_events={r.new_filing_events} "
        f"candidates_detected={r.candidates_detected} candidates_processed={r.candidates_processed} "
        f"candidates_deferred={r.candidates_deferred}",
        f"    documents_retrieved={r.documents_retrieved} documents_extracted={r.documents_extracted} "
        f"cache_hits={r.cache_hits} already_seen={r.already_seen_count} no_data={r.no_data_count}",
    ]
    if r.errors_by_category:
        lines.append(f"    errors_by_category={dict(r.errors_by_category)}")
    for warning in r.warnings:  # ScanReport.warnings is explicitly documented safe to surface verbatim
        lines.append(f"    warning: {warning}")
    return "\n".join(lines)


def _sync_remote_cache(settings: Settings) -> tuple[str, bool]:
    """Reuses the existing dormant remote_cache module unchanged. Absent
    or incomplete R2 config is the normal, expected default — reported
    as "skipped," always ok=True (local cache stays authoritative, by
    design). Only an *explicitly enabled and fully configured* sync
    attempt that then fails is treated as a real problem — ok=False —
    since the operator asked for it to work.

    Deliberately never includes str(exc) in the returned message: a real
    boto3/botocore failure frequently embeds the request URL (bucket/
    endpoint) in its exception text, which the "never print an endpoint
    or object URL" rule forbids. Only the exception's class name is
    reported — enough to distinguish e.g. a connection failure from an
    auth failure without risking a URL or credential leak."""
    if not remote_cache_available(settings):
        return "remote cache: skipped (disabled or not fully configured — local cache is authoritative, as designed)", True
    try:
        client = build_r2_client(settings)
        manifest = sync_local_cache_to_remote(settings, settings.cache_dir, client)
        if manifest is None:
            return "remote cache: nothing to sync (no local cache files exist yet)", True
        return f"remote cache: synced {len(manifest.entries)} file(s), manifest generated_at={manifest.generated_at}", True
    except Exception as exc:  # noqa: BLE001 — must never crash the CLI; reported as a real failure below instead
        return f"remote cache: sync failed ({type(exc).__name__}) — local data unaffected", False


def main(
    edgar_candidate_repository: CandidatePersistence | None = None,
    dart_candidate_repository: CandidatePersistence | None = None,
    edinet_candidate_repository: CandidatePersistence | None = None,
) -> int:
    settings = get_settings()
    repository_by_source = {
        "dart": dart_candidate_repository,
        "edgar": edgar_candidate_repository,
        "edinet": edinet_candidate_repository,
    }
    results = [
        _run_one_source(source, settings, candidate_repository=repository_by_source[source])
        for source in _SOURCES
    ]
    remote_summary, remote_ok = _sync_remote_cache(settings)

    print("Radar headless scan summary")
    for result in results:
        print(_format_source_summary(result))
    print(f"  {remote_summary}")

    failures = [r for r in results if not r.ok]
    for f in failures:
        print(f"ERROR: {f.source} scan failed — {f.error}", file=sys.stderr)
    if not remote_ok:
        print(
            "ERROR: remote cache sync failed — see the summary above for the exception "
            "type; check R2 configuration/secrets. Local scan data was written successfully.",
            file=sys.stderr,
        )

    return 1 if (failures or not remote_ok) else 0


if __name__ == "__main__":
    raise SystemExit(main())
