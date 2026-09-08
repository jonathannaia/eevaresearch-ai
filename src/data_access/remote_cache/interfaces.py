"""Storage-backend interface for the R2 remote cache sync — deliberately
narrow (put/get bytes by key, nothing else), so a test double can satisfy
it with a plain in-memory dict and never needs network access or real
credentials."""
from __future__ import annotations

from abc import ABC, abstractmethod


class ObjectStorageClient(ABC):
    @abstractmethod
    def put_object(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def get_object(self, key: str) -> bytes | None:
        """None if the key doesn't exist — never raises for a missing key."""
