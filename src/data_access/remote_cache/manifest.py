"""Manifest schema for the R2 remote cache sync. A manifest is the single
object a reader is meant to trust as "what does the remote cache
currently contain" — see sync.py's own module docstring for why it's
always the last thing written in a sync batch."""
from __future__ import annotations

from dataclasses import dataclass

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ManifestEntry:
    source: str  # "dart" / "edgar" / "edinet"
    filename: str  # e.g. "dart_candidates.json" — matches the real on-disk name verbatim
    object_key: str
    size_bytes: int
    sha256: str
    uploaded_at: str  # ISO 8601


@dataclass(frozen=True)
class Manifest:
    schema_version: int
    generated_at: str  # ISO 8601
    entries: tuple[ManifestEntry, ...] = ()

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "entries": [
                {
                    "source": e.source,
                    "filename": e.filename,
                    "object_key": e.object_key,
                    "size_bytes": e.size_bytes,
                    "sha256": e.sha256,
                    "uploaded_at": e.uploaded_at,
                }
                for e in self.entries
            ],
        }

    @staticmethod
    def from_dict(data: dict) -> Manifest:
        entries = tuple(
            ManifestEntry(
                source=e["source"], filename=e["filename"], object_key=e["object_key"],
                size_bytes=e["size_bytes"], sha256=e["sha256"], uploaded_at=e["uploaded_at"],
            )
            for e in data.get("entries", [])
        )
        return Manifest(schema_version=data["schema_version"], generated_at=data["generated_at"], entries=entries)
