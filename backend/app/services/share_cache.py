"""Snapshot cache for public share GETs.

Cache key is ``(token, active_branch_checkpoint_id)``. New turns advance the
active branch checkpoint, so the key changes and previous snapshots fall out
naturally — no explicit invalidation per write needed. Revoking a share
*does* call :func:`invalidate_token` so a stale snapshot can't outlive the
token even within the TTL window.

Single-process, in-memory; the PoC does not run multiple backend replicas.
GIL makes individual dict ops atomic, so the racy check-then-evict path is
acceptable (occasional duplicate eviction does not corrupt state).
"""

from __future__ import annotations

import time
from typing import Any

from app.config import settings

_Entry = tuple[float, Any]  # (expires_monotonic_s, value)


class _TTLCache:
    def __init__(self, ttl_s: float, max_size: int) -> None:
        self._ttl_s = max(1.0, float(ttl_s))
        self._max_size = max(1, int(max_size))
        self._store: dict[tuple[str, str | None], _Entry] = {}

    def get(self, key: tuple[str, str | None]) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires, value = entry
        if time.monotonic() >= expires:
            self._store.pop(key, None)
            return None
        return value

    def put(self, key: tuple[str, str | None], value: Any) -> None:
        if len(self._store) >= self._max_size:
            now = time.monotonic()
            # Drop expired first (cheap), then FIFO if still over.
            for k in [k for k, (e, _) in list(self._store.items()) if e < now]:
                self._store.pop(k, None)
            if len(self._store) >= self._max_size:
                try:
                    oldest = next(iter(self._store))
                    self._store.pop(oldest, None)
                except StopIteration:
                    pass
        self._store[key] = (time.monotonic() + self._ttl_s, value)

    def invalidate_prefix(self, token: str) -> None:
        """Drop every entry whose first key element is ``token``."""
        for k in [k for k in list(self._store.keys()) if k[0] == token]:
            self._store.pop(k, None)

    def clear(self) -> None:
        self._store.clear()


_cache = _TTLCache(
    ttl_s=settings.share_snapshot_cache_ttl_s,
    max_size=settings.share_snapshot_cache_max,
)


def get_snapshot(token: str, checkpoint_id: str | None) -> Any | None:
    return _cache.get((token, checkpoint_id))


def put_snapshot(token: str, checkpoint_id: str | None, value: Any) -> None:
    _cache.put((token, checkpoint_id), value)


def get_envelope(token: str, checkpoint_id: str | None) -> Any | None:
    """``MessagesEnvelope`` 모양의 캐시 entry. ``SharedConversationView``와
    같은 token이라도 shape이 달라 별도 namespace로 분리한다."""
    return _cache.get((_envelope_key(token), checkpoint_id))


def put_envelope(token: str, checkpoint_id: str | None, value: Any) -> None:
    _cache.put((_envelope_key(token), checkpoint_id), value)


def _envelope_key(token: str) -> str:
    return f"{token}:envelope"


def invalidate_token(token: str) -> None:
    """View 와 envelope 양쪽의 token-prefix entry 모두 폐기."""
    _cache.invalidate_prefix(token)
    _cache.invalidate_prefix(_envelope_key(token))


def clear_all() -> None:
    """Test helper — purge the cache between tests."""
    _cache.clear()
