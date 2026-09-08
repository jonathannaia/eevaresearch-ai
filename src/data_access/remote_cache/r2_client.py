"""Real Cloudflare R2 client construction — S3-compatible, via boto3.

Deliberately isolated in its own function with a lazy `import boto3`
inside it (not at module import time): this keeps every other module in
this package importable in an environment without boto3 installed, and
keeps a plain `import src.data_access.remote_cache...` free of any
network-library dependency unless a caller actually asks to build a real
client. Never called by any test in this repo — tests inject an
ObjectStorageClient fake directly instead (see interfaces.py). Building
a client here makes no network call itself; every method call on the
result does.

No credential value is ever read, echoed, logged, or printed anywhere in
this module — only checked for presence (r2_settings_complete)."""
from __future__ import annotations

from src.config.settings import Settings
from src.data_access.remote_cache.interfaces import ObjectStorageClient


class R2ConfigError(Exception):
    """Raised when remote_cache_enabled is true but one or more required
    EDGE_R2_* settings are missing. Callers must catch this and fall back
    to local disk — never crash the app over it."""


def r2_settings_complete(settings: Settings) -> bool:
    # r2_account_id is deliberately NOT required here: R2's S3-compatible
    # endpoint URL already fully qualifies the account, and boto3.client()
    # takes no separate account-ID parameter — requiring it would only
    # ever narrow "considered configured" without narrowing "capable of
    # functioning." It stays an optional Settings field for possible
    # future use (e.g. a label in logs/manifest metadata), never a gate.
    return bool(
        settings.r2_access_key_id
        and settings.r2_secret_access_key
        and settings.r2_bucket
        and settings.r2_endpoint
    )


class Boto3ObjectStorageClient(ObjectStorageClient):
    """Thin adapter over a boto3 S3 client pointed at R2's S3-compatible
    endpoint. Constructed only by build_r2_client() below — never
    directly in a test."""

    def __init__(self, boto3_client, bucket: str):
        self._client = boto3_client
        self._bucket = bucket

    def put_object(self, key: str, data: bytes) -> None:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data)

    def get_object(self, key: str) -> bytes | None:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except self._client.exceptions.NoSuchKey:
            return None
        return response["Body"].read()


def build_r2_client(settings: Settings) -> Boto3ObjectStorageClient:
    """Raises R2ConfigError if settings are incomplete — never guesses a
    missing value or silently proceeds with a partial config."""
    if not r2_settings_complete(settings):
        raise R2ConfigError("EDGE_R2_* settings are incomplete — remote cache cannot be used.")
    import boto3  # lazy: keeps this package importable without boto3 installed

    client = boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
    )
    return Boto3ObjectStorageClient(client, settings.r2_bucket)
