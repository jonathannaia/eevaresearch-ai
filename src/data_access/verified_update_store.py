"""Public store for VerifiedUpdate records (design §5.3, §9.2) — the only
file the Verified Updates page reads. Written by exactly one caller, the
backend publication service inside request_publication_decision, and
only on an AUTO_PUBLISHED decision (or a later human approval). The
agent process never holds a handle to this store.

Insert-if-absent by id, validated before write, atomic replace — a
malformed record is rejected rather than rendered, and a re-run of the
same session cannot double-publish (ids are deterministic upstream).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from src.models.verified_update import VerifiedUpdate, validate_verified_update

VERIFIED_UPDATES_FILENAME = "verified_updates.json"


def _path(cache_dir: Path) -> Path:
    return cache_dir / VERIFIED_UPDATES_FILENAME


def _load(cache_dir: Path) -> dict[str, dict]:
    path = _path(cache_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_atomic(cache_dir: Path, payload: dict[str, dict]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=VERIFIED_UPDATES_FILENAME + ".", dir=cache_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, _path(cache_dir))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load_verified_updates(cache_dir: Path) -> tuple[VerifiedUpdate, ...]:
    """Newest published_at first. A corrupt record is skipped, never fatal."""
    updates: list[VerifiedUpdate] = []
    for data in _load(cache_dir).values():
        try:
            updates.append(VerifiedUpdate.from_dict(data))
        except (KeyError, ValueError, TypeError):
            continue
    updates.sort(key=lambda u: (u.published_at, u.id), reverse=True)
    return tuple(updates)


def append_verified_updates(cache_dir: Path, updates: tuple[VerifiedUpdate, ...]) -> tuple[str, ...]:
    """Returns the ids actually inserted. An invalid record is skipped and
    an existing id is never overwritten; either way nothing else in the
    batch is affected."""
    store = _load(cache_dir)
    inserted: list[str] = []
    for update in updates:
        if update.id in store or validate_verified_update(update):
            continue
        store[update.id] = update.to_dict()
        inserted.append(update.id)
    if inserted:
        _write_atomic(cache_dir, store)
    return tuple(inserted)
