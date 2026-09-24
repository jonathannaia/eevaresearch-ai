"""One-shot, read-only validation of the official EDINET code list.

Run this by hand from a real terminal. It exists because the browser-
driven Render Web Shell proved unable to deliver a command reliably, and
this validation is the gate in front of any EDINET registry addition.

What it does, in order:

  1. reads EDGE_EDINET_SUBSCRIPTION_KEY from the environment and exits
     BEFORE constructing a client if it is absent;
  2. makes EXACTLY ONE request -- fetch_code_list() -- and nothing else;
  3. parses it through the deployed path, _extract_csv_text() then
     parse_code_list_csv();
  4. gates on parser integrity and exits if the gate fails;
  5. only then checks the tracked EDINET issuers and the Batch 1
     candidate securities codes against the official list.

What it deliberately does NOT do: call resolve_and_cache() (it writes a
cache), write any file, touch the registry, add or infer an issuer, or
retry. There is no try/except, no loop and no fallback anywhere near the
fetch, so a failure cannot turn into a second request -- the one-fetch
property is structural, not a promise. An error therefore propagates as
a traceback; EdinetClient sanitizes its own error strings, and the URL
constant carries no credential (the key is attached inside the client as
a request parameter and never appears here).

Output is bounded on purpose: a boolean for the key, counts, at most
three truncated warnings, and identifiers only. No CSV body, no request
URL, no unrelated filer data.

Exit codes: 0 validation completed, 1 parser-integrity gate failed,
2 credential absent (no fetch attempted).
"""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

# Same convention as scripts/hosted_signals_preview.py: a script may be
# invoked from any working directory, so the repository root is not
# necessarily importable. Add it only if it isn't already present.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config.tracked_companies import TRACKED_COMPANIES
from src.data_access.edinet import edinet_code_resolver as resolver
from src.data_access.edinet.client import EdinetClient

_KEY_VAR = "EDGE_EDINET_SUBSCRIPTION_KEY"
_WARNING_SAMPLE = 3
_WARNING_CHARS = 160
_COLLISION_SAMPLE = 4

# TSE securities codes only. No EDINET code is supplied here -- resolving
# them against the official list is the entire point.
_CANDIDATES = (
    ("Lasertec", "6920"),
    ("Fujikura", "5803"),
    ("Hamamatsu Photonics", "6965"),
    ("Tokyo Ohka Kogyo", "4186"),
    ("Kokusai Electric", "6525"),
    ("THK", "6481"),
)

fetch_count = 0

key = os.environ.get(_KEY_VAR)
print("key_present:", bool(key))
if not key:
    print("fetch_count:", fetch_count)
    print(f"ABORT: {_KEY_VAR} is not set in this environment. No request was made.")
    raise SystemExit(2)

fetch_count += 1
zip_bytes = EdinetClient(subscription_key=key).fetch_code_list(resolver.PROVISIONAL_CODE_LIST_URL)
print("fetch_count:", fetch_count)
print("zip_bytes:", len(zip_bytes))

text = resolver._extract_csv_text(zip_bytes)
lines = text.splitlines()
declared_count = resolver.parse_summary_row(lines[0]).declared_count

# Re-walk the SAME in-memory text -- never a second fetch -- to count
# every filer, which is the population the summary row declares.
data_rows_seen = sum(1 for row in csv.reader(lines[2:]) if row)

rows, warnings = resolver.parse_code_list_csv(text)

print("declared_count:", declared_count)
print("data_rows_seen:", data_rows_seen)
print("resolver_eligible_rows:", len(rows))
print("warning_count:", len(warnings))
for warning in warnings[:_WARNING_SAMPLE]:
    print("  warning:", str(warning)[:_WARNING_CHARS])

integrity_ok = bool(rows) and not warnings and declared_count == data_rows_seen
print("parser_integrity:", "PASS" if integrity_ok else "FAIL")
if not integrity_ok:
    print("fetch_count:", fetch_count)
    print("STOPPED: parser gate failed; no issuer validation performed.")
    raise SystemExit(1)

by_edinet = {row["edinet_code"]: row for row in rows}
by_securities: dict[str, list[dict]] = {}
for row in rows:
    by_securities.setdefault(row["securities_code"], []).append(row)

tracked = [c for c in TRACKED_COMPANIES if c.source == "EDINET"]
print()
print("tracked_edinet_issuers:", len(tracked))
issues = 0
for company in tracked:
    official = by_edinet.get(company.corp_code or "")
    if official is None:
        print(f"  {company.corp_code}  ABSENT from official list")
        issues += 1
    elif official["securities_code"] != company.krx_code:
        print(f"  {company.corp_code}  SEC_MISMATCH stored={company.krx_code} official={official['securities_code']}")
        issues += 1
print("tracked_summary: pass=%d issues=%d" % (len(tracked) - issues, issues))

print()
passed = held = 0
for label, supplied in _CANDIDATES:
    lookup = resolver._normalize_lookup_code(supplied)
    matches = by_securities.get(lookup, [])
    print(f"candidate: {label} ({supplied} -> {lookup}) matches={len(matches)}")
    if len(matches) != 1:
        reason = "zero clean matches in the official list" if not matches else f"{len(matches)} matches -- ambiguous"
        print(f"  HOLD  reason={reason}")
        held += 1
        continue
    official = matches[0]
    name_en = (official.get("filer_name_en") or "").strip()
    print(f"  PASS  edinet={official['edinet_code']}  securities={official['securities_code']}  name_en={name_en!r}")
    passed += 1
    # Holding-company / affiliate check, reported as identifiers only so
    # no unrelated filer names reach the output.
    token = name_en.split()[0] if name_en else ""
    if len(token) >= 4:
        collisions = [
            (r["edinet_code"], r["securities_code"])
            for r in rows
            if token.lower() in (r.get("filer_name_en") or "").lower()
            and r["edinet_code"] != official["edinet_code"]
        ]
        print(f"  name_collisions={len(collisions)} {collisions[:_COLLISION_SAMPLE]}")
print("candidates_summary: pass=%d hold=%d" % (passed, held))

print()
print("fetch_count:", fetch_count)
print("DONE")
