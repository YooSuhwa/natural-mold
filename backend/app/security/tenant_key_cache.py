"""Short-lived per-process cache of unwrapped company data keys (B-16).

Entries are keyed by (company, key id, sha256 of the wrapped key) and expire after
``TTL_SECONDS``. Callers always re-read ``tenants.data_keys`` before using an entry,
so the cache never keeps a destroyed key usable; it only saves the unwrap work.
"""

from __future__ import annotations

import hashlib
import time
from uuid import UUID

TTL_SECONDS = 30.0
_entries: dict[tuple[UUID, str, str], tuple[float, bytes]] = {}


def _key(tenant_id: UUID, key_id: str, wrapped: str) -> tuple[UUID, str, str]:
    return tenant_id, key_id, hashlib.sha256(wrapped.encode()).hexdigest()


def get(tenant_id: UUID, key_id: str, wrapped: str) -> bytes | None:
    entry = _entries.get(_key(tenant_id, key_id, wrapped))
    if entry is None or entry[0] <= time.monotonic():
        return None
    return entry[1]


def put(tenant_id: UUID, key_id: str, wrapped: str, raw: bytes) -> None:
    _entries[_key(tenant_id, key_id, wrapped)] = (time.monotonic() + TTL_SECONDS, raw)


def clear(tenant_id: UUID | None = None) -> None:
    for cache_key in [k for k in _entries if tenant_id is None or k[0] == tenant_id]:
        _entries.pop(cache_key, None)


def count(tenant_id: UUID) -> int:
    return sum(1 for k in _entries if k[0] == tenant_id)
