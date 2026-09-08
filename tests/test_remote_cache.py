"""Tests for the dormant R2 remote-cache sync infrastructure
(src/data_access/remote_cache/). No test here uses real credentials or
live network access — every ObjectStorageClient is a plain in-memory
FakeR2Client, and build_r2_client()/boto3 are never invoked. This mirrors
candidate_store.py's own test conventions: pure functions, tmp_path for
any local-file behavior, zero I/O beyond that."""
from __future__ import annotations

import json

import pytest

from src.config.settings import Settings
from src.data_access.remote_cache.interfaces import ObjectStorageClient
from src.data_access.remote_cache.manifest import Manifest
from src.data_access.remote_cache.r2_client import R2ConfigError, r2_settings_complete
from src.data_access.remote_cache.sync import (
    MANIFEST_OBJECT_KEY,
    SOURCE_FILES,
    fetch_remote_manifest,
    fetch_source_file,
    load_source_file_local_or_remote,
    object_key_for,
    remote_cache_available,
    sync_local_cache_to_remote,
)


class FakeR2Client(ObjectStorageClient):
    """In-memory stand-in for a real S3/R2 client — records every
    put_object call (key, in order) so tests can assert write ordering,
    and can be told to raise on a specific key to simulate a failed
    upload partway through a batch."""

    def __init__(self, raise_on_key: str | None = None):
        self.objects: dict[str, bytes] = {}
        self.put_calls: list[str] = []
        self._raise_on_key = raise_on_key

    def put_object(self, key: str, data: bytes) -> None:
        if key == self._raise_on_key:
            raise ConnectionError(f"simulated upload failure for {key}")
        self.objects[key] = data
        self.put_calls.append(key)

    def get_object(self, key: str) -> bytes | None:
        return self.objects.get(key)


def _r2_settings(**overrides) -> Settings:
    defaults = dict(
        remote_cache_enabled=True, r2_account_id="acct", r2_access_key_id="key",
        r2_secret_access_key="secret", r2_bucket="bucket", r2_endpoint="https://example.invalid",
    )
    defaults.update(overrides)
    return Settings(**defaults)


# --- Configuration gating ---


def test_remote_cache_available_false_when_disabled():
    settings = _r2_settings(remote_cache_enabled=False)
    assert remote_cache_available(settings) is False


def test_remote_cache_available_false_when_enabled_but_incomplete():
    settings = _r2_settings(r2_bucket=None)
    assert remote_cache_available(settings) is False
    assert r2_settings_complete(settings) is False


def test_remote_cache_available_true_when_enabled_and_complete():
    settings = _r2_settings()
    assert remote_cache_available(settings) is True


@pytest.mark.parametrize(
    "missing_field", ["r2_access_key_id", "r2_secret_access_key", "r2_bucket", "r2_endpoint"],
)
def test_r2_settings_complete_requires_every_actual_connection_field(missing_field):
    """The four fields boto3.client() actually consumes — endpoint,
    access key, secret key, bucket — are each individually required."""
    settings = _r2_settings(**{missing_field: None})
    assert r2_settings_complete(settings) is False


def test_r2_settings_complete_true_without_account_id():
    """Account ID is optional metadata, not a connection requirement —
    R2's endpoint URL already fully qualifies the account, and
    boto3.client() takes no separate account-ID parameter."""
    settings = _r2_settings(r2_account_id=None)
    assert r2_settings_complete(settings) is True
    assert remote_cache_available(settings) is True


def test_default_settings_have_remote_cache_disabled():
    """No env vars set → safe, inert default, matching every other
    optional credential field's own None-means-unconfigured convention."""
    settings = Settings()
    assert settings.remote_cache_enabled is False
    assert remote_cache_available(settings) is False


# --- Missing-credential fallback (reader path) ---


def test_load_source_file_falls_back_to_local_when_disabled(tmp_path):
    (tmp_path / "dart_candidates.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
    settings = Settings(remote_cache_enabled=False)

    result = load_source_file_local_or_remote(settings, tmp_path, "dart", "dart_candidates.json")
    assert result == {"a": 1}


def test_load_source_file_falls_back_to_local_when_incomplete_config(tmp_path):
    (tmp_path / "dart_candidates.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
    settings = _r2_settings(r2_secret_access_key=None)

    result = load_source_file_local_or_remote(settings, tmp_path, "dart", "dart_candidates.json")
    assert result == {"a": 1}


def test_load_source_file_falls_back_to_local_when_client_factory_raises(tmp_path):
    (tmp_path / "dart_candidates.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
    settings = _r2_settings()

    def _raising_factory(_settings):
        raise R2ConfigError("simulated: cannot build a real client")

    result = load_source_file_local_or_remote(settings, tmp_path, "dart", "dart_candidates.json", client_factory=_raising_factory)
    assert result == {"a": 1}


def test_load_source_file_returns_none_when_neither_remote_nor_local_exists(tmp_path):
    settings = Settings(remote_cache_enabled=False)
    result = load_source_file_local_or_remote(settings, tmp_path, "dart", "dart_candidates.json")
    assert result is None


def test_load_source_file_prefers_remote_when_available(tmp_path):
    (tmp_path / "dart_candidates.json").write_text(json.dumps({"local": True}), encoding="utf-8")
    settings = _r2_settings()
    client = FakeR2Client()
    sync_local_cache_to_remote(settings, tmp_path, client)
    # Overwrite local after syncing, to prove the remote copy (not local) is returned.
    (tmp_path / "dart_candidates.json").write_text(json.dumps({"local": "changed-after-sync"}), encoding="utf-8")

    result = load_source_file_local_or_remote(settings, tmp_path, "dart", "dart_candidates.json", client_factory=lambda s: client)
    assert result == {"local": True}


# --- Manifest-last ordering ---


def test_sync_uploads_source_files_before_manifest(tmp_path):
    (tmp_path / "dart_candidates.json").write_text(json.dumps({"x": 1}), encoding="utf-8")
    (tmp_path / "edgar_candidates.json").write_text(json.dumps({"y": 2}), encoding="utf-8")
    settings = _r2_settings()
    client = FakeR2Client()

    manifest = sync_local_cache_to_remote(settings, tmp_path, client)

    assert manifest is not None
    assert client.put_calls[-1] == MANIFEST_OBJECT_KEY
    assert MANIFEST_OBJECT_KEY not in client.put_calls[:-1]


def test_sync_returns_none_when_no_local_files_exist(tmp_path):
    settings = _r2_settings()
    client = FakeR2Client()
    manifest = sync_local_cache_to_remote(settings, tmp_path, client)
    assert manifest is None
    assert client.put_calls == []


def test_sync_skips_source_files_that_do_not_exist_locally(tmp_path):
    (tmp_path / "dart_candidates.json").write_text(json.dumps({"x": 1}), encoding="utf-8")
    settings = _r2_settings()
    client = FakeR2Client()

    manifest = sync_local_cache_to_remote(settings, tmp_path, client)

    assert manifest is not None
    assert len(manifest.entries) == 1
    assert manifest.entries[0].filename == "dart_candidates.json"


def test_sync_covers_exactly_the_six_candidate_and_filing_event_files():
    filenames = {filename for _source, filename in SOURCE_FILES}
    assert filenames == {
        "dart_candidates.json", "dart_filing_events.json",
        "edgar_candidates.json", "edgar_filing_events.json",
        "edinet_candidates.json", "edinet_filing_events.json",
    }


# --- Partial-upload safety ---


def test_partial_upload_failure_never_writes_manifest(tmp_path):
    (tmp_path / "dart_candidates.json").write_text(json.dumps({"x": 1}), encoding="utf-8")
    (tmp_path / "edgar_candidates.json").write_text(json.dumps({"y": 2}), encoding="utf-8")
    settings = _r2_settings()
    failing_key = object_key_for("edgar", "edgar_candidates.json")
    client = FakeR2Client(raise_on_key=failing_key)

    with pytest.raises(ConnectionError):
        sync_local_cache_to_remote(settings, tmp_path, client)

    assert MANIFEST_OBJECT_KEY not in client.put_calls
    assert MANIFEST_OBJECT_KEY not in client.objects


def test_reader_sees_previous_manifest_unchanged_after_failed_batch(tmp_path):
    (tmp_path / "dart_candidates.json").write_text(json.dumps({"version": "first"}), encoding="utf-8")
    settings = _r2_settings()
    client = FakeR2Client()
    first_manifest = sync_local_cache_to_remote(settings, tmp_path, client)
    assert first_manifest is not None

    # Second batch: dart succeeds, edgar fails — manifest must not update.
    (tmp_path / "dart_candidates.json").write_text(json.dumps({"version": "second"}), encoding="utf-8")
    (tmp_path / "edgar_candidates.json").write_text(json.dumps({"z": 1}), encoding="utf-8")
    failing_key = object_key_for("edgar", "edgar_candidates.json")
    client._raise_on_key = failing_key
    with pytest.raises(ConnectionError):
        sync_local_cache_to_remote(settings, tmp_path, client)

    reread_manifest = fetch_remote_manifest(client)
    assert reread_manifest is not None
    assert reread_manifest.generated_at == first_manifest.generated_at
    assert [e.filename for e in reread_manifest.entries] == ["dart_candidates.json"]


def test_fetch_remote_manifest_returns_none_when_never_written():
    client = FakeR2Client()
    assert fetch_remote_manifest(client) is None


# --- Object-key naming ---


def test_object_key_naming_is_deterministic_and_source_scoped():
    assert object_key_for("dart", "dart_candidates.json") == "radar-cache/dart/dart_candidates.json"
    assert object_key_for("edgar", "edgar_filing_events.json") == "radar-cache/edgar/edgar_filing_events.json"
    assert object_key_for("edinet", "edinet_candidates.json") == "radar-cache/edinet/edinet_candidates.json"


def test_manifest_object_key_is_fixed_and_stable():
    assert MANIFEST_OBJECT_KEY == "radar-cache/manifest.json"


# --- JSON round-trip behavior ---


def test_json_round_trip_preserves_shape_exactly(tmp_path):
    original = {
        "seen_receipt_numbers": ["20260812000001"],
        "filing_events": [{"rcept_no": "20260812000001", "corp_name": "SK Hynix"}],
        "candidate_signals": [],
    }
    (tmp_path / "dart_filing_events.json").write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
    settings = _r2_settings()
    client = FakeR2Client()

    manifest = sync_local_cache_to_remote(settings, tmp_path, client)
    assert manifest is not None
    result = fetch_source_file(client, manifest, "dart", "dart_filing_events.json")

    assert result == original


def test_json_round_trip_preserves_unicode_content(tmp_path):
    original = {"corp_name": "삼성전자", "note": "有価証券報告書"}
    (tmp_path / "edinet_candidates.json").write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
    settings = _r2_settings()
    client = FakeR2Client()

    manifest = sync_local_cache_to_remote(settings, tmp_path, client)
    result = fetch_source_file(client, manifest, "edinet", "edinet_candidates.json")

    assert result == original


def test_fetch_source_file_returns_none_for_unknown_entry():
    manifest = Manifest(schema_version=1, generated_at="2026-08-19T00:00:00+00:00", entries=())
    client = FakeR2Client()
    assert fetch_source_file(client, manifest, "dart", "dart_candidates.json") is None


def test_manifest_to_dict_and_from_dict_round_trip():
    raw = json.dumps({
        "schema_version": 1, "generated_at": "2026-08-19T00:00:00+00:00",
        "entries": [{
            "source": "dart", "filename": "dart_candidates.json", "object_key": "radar-cache/dart/dart_candidates.json",
            "size_bytes": 10, "sha256": "abc", "uploaded_at": "2026-08-19T00:00:00+00:00",
        }],
    })
    parsed = Manifest.from_dict(json.loads(raw))
    assert parsed.to_dict() == json.loads(raw)
