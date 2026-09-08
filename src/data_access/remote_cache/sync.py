"""Manifest-last sync between local data/cache JSON files and the R2
remote cache — dormant infrastructure only. Nothing in this repo calls
these functions yet: RadarSignalRepository, radar_inbox.py, and every
existing pipeline/read path are completely untouched by this module. It
prepares two things for future wiring — a writer path (a future GitHub
Actions scanner would call sync_local_cache_to_remote after a scan) and
a reader path (load_source_file_local_or_remote) — neither is invoked
from the live app today.

Manifest-last write pattern: every changed source file uploads first;
the manifest — the one object a reader is meant to trust — is written
only after every source upload in the batch has succeeded. A reader that
fetches the manifest therefore either sees the previous complete batch
(nothing yet overwritten) or the new complete batch (everything
overwritten) — never a mix, since intermediate source-file uploads never
touch the manifest object key at all. If any source upload fails partway
through a batch, the manifest is deliberately never written for that
batch, and the previous manifest — still fully valid — remains what any
reader will see.

Reads through this module always fall back to the existing local
data/cache/*.json files on any remote failure (disabled, incomplete
config, no manifest yet, a client/network error) — never a crash, never
an empty page, matching the "preserve current local behavior" rule the
rest of this pipeline already follows (candidate_store.load_candidates's
own "never raises" discipline)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from src.config.settings import Settings
from src.data_access.remote_cache.interfaces import ObjectStorageClient
from src.data_access.remote_cache.manifest import SCHEMA_VERSION, Manifest, ManifestEntry
from src.data_access.remote_cache.r2_client import build_r2_client, r2_settings_complete

MANIFEST_OBJECT_KEY = "radar-cache/manifest.json"

# The exact "candidate and filing-event JSON files for DART, EDGAR, and
# EDINET" this prepares to sync — matches the real on-disk filenames
# already hardcoded independently in each source's own candidate_store.py
# / scan_service.py. Duplicated here as literal strings rather than
# imported: every one of those modules already hardcodes its own filename
# as a private default parameter, not a shared public constant, so this
# matches the codebase's existing convention (e.g.
# RadarSignalRepository's own _DART_CANDIDATES_FILENAME) rather than
# inventing a new shared-constants module.
SOURCE_FILES: tuple[tuple[str, str], ...] = (
    ("dart", "dart_candidates.json"),
    ("dart", "dart_filing_events.json"),
    ("edgar", "edgar_candidates.json"),
    ("edgar", "edgar_filing_events.json"),
    ("edinet", "edinet_candidates.json"),
    ("edinet", "edinet_filing_events.json"),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def object_key_for(source: str, filename: str) -> str:
    return f"radar-cache/{source}/{filename}"


def remote_cache_available(settings: Settings) -> bool:
    """True only when explicitly opted in AND every required R2 setting
    is present — the one gate every entry point below checks first.
    False means "use local data/cache," never a crash."""
    return settings.remote_cache_enabled and r2_settings_complete(settings)


def sync_local_cache_to_remote(settings: Settings, cache_dir: Path, client: ObjectStorageClient) -> Manifest | None:
    """Uploads every existing source file, then writes the manifest last.
    A source file that hasn't been created locally yet (e.g. a source
    that hasn't scanned) is skipped, never invented. Returns None if no
    source file exists at all yet. Deliberately takes an already-built
    ObjectStorageClient rather than Settings alone, so its own tests can
    inject a fake with zero config/gating concerns mixed in — callers
    should check remote_cache_available(settings) before calling this."""
    entries: list[ManifestEntry] = []
    for source, filename in SOURCE_FILES:
        local_path = cache_dir / filename
        if not local_path.exists():
            continue
        data = local_path.read_bytes()
        key = object_key_for(source, filename)
        client.put_object(key, data)
        entries.append(
            ManifestEntry(
                source=source, filename=filename, object_key=key, size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(), uploaded_at=_now_iso(),
            )
        )
    if not entries:
        return None
    manifest = Manifest(schema_version=SCHEMA_VERSION, generated_at=_now_iso(), entries=tuple(entries))
    client.put_object(MANIFEST_OBJECT_KEY, json.dumps(manifest.to_dict(), indent=2).encode("utf-8"))
    return manifest


def fetch_remote_manifest(client: ObjectStorageClient) -> Manifest | None:
    """None if no manifest has ever been written yet — never invented."""
    raw = client.get_object(MANIFEST_OBJECT_KEY)
    if raw is None:
        return None
    return Manifest.from_dict(json.loads(raw))


def fetch_source_file(client: ObjectStorageClient, manifest: Manifest, source: str, filename: str):
    """Looks up filename's object_key from the manifest (never guesses a
    key) and returns its parsed JSON — None if the manifest has no entry
    for this (source, filename) or the object is unexpectedly missing."""
    entry = next((e for e in manifest.entries if e.source == source and e.filename == filename), None)
    if entry is None:
        return None
    raw = client.get_object(entry.object_key)
    if raw is None:
        return None
    return json.loads(raw)


def load_source_file_local_or_remote(
    settings: Settings,
    cache_dir: Path,
    source: str,
    filename: str,
    client_factory: Callable[[Settings], ObjectStorageClient] = build_r2_client,
):
    """The one reader-side entry point this module prepares for future
    wiring — NOT called from any existing page/pipeline yet. Tries the
    remote cache only when explicitly enabled and fully configured;
    falls back to the existing local data/cache file, unchanged, on any
    failure (missing config, a client/network error, no manifest yet, no
    entry for this file). Returns None only if neither the remote nor the
    local copy has this file at all (e.g. a source that has never
    scanned) — never a crash, matching candidate_store.load_candidates's
    own "never raises" discipline for exactly this same reason."""
    if remote_cache_available(settings):
        try:
            client = client_factory(settings)
            manifest = fetch_remote_manifest(client)
            if manifest is not None:
                remote_data = fetch_source_file(client, manifest, source, filename)
                if remote_data is not None:
                    return remote_data
        except Exception:
            # Any remote failure (network, auth, malformed response, a
            # missing boto3 install) must fall back to local — this
            # reader must never crash the app just because R2 is
            # unreachable or not yet synced.
            pass
    local_path = cache_dir / filename
    if not local_path.exists():
        return None
    return json.loads(local_path.read_text(encoding="utf-8"))
