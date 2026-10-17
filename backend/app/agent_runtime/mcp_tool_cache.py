"""Per-process MCP tool discovery cache keyed by credential identity (plan 11.3; F-12).

Cached tools keep their connection config (transport headers) and the
interceptors that inject ``auth_config`` arguments, so an entry is only valid
for the identity that resolved it. ``identity_cache_key`` therefore adds, to the
server key (URL + transport-header digest), the tenant, org, server id, resolved
credential id, credential mode, run account and a digest of the injected
arguments: one user's (or one binding's) cached tools never serve another.

Entries are LRU-bounded (``MAX_ENTRIES``), expired ones are swept on insert and
an evicted key's idle lock is dropped with it. Entries are tagged
``credential:<id>``, ``server:<id>`` and ``account:<id>``;
``invalidate_mcp_tool_cache`` drops the entries carrying every given tag. ORM
listeners (``mcp_cache_invalidation``) call it on credential change, rotation
or revocation and on per-user binding changes. The cache is per process: other
workers fall back to the key (new secret values give a new digest) and the TTL,
and every run re-resolves its credentials before it reaches the cache.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from langchain_core.tools import BaseTool

MAX_ENTRIES = 256
_CACHE: OrderedDict[str, tuple[float, list[BaseTool]]] = OrderedDict()
_TAGS: dict[str, frozenset[str]] = {}
_LOCKS: dict[str, asyncio.Lock] = {}
_CACHE_GUARD = asyncio.Lock()
_IDENTITY_FIELDS = (
    "tenant_id",
    "org_id",
    "mcp_server_id",
    "credential_id",
    "credential_mode",
    "credential_subject_user_id",
)


def _drop(key: str) -> None:
    _CACHE.pop(key, None)
    _TAGS.pop(key, None)
    lock = _LOCKS.get(key)
    if lock is not None and not lock.locked():
        _LOCKS.pop(key, None)


def _store(key: str, tools: list[BaseTool], tags: frozenset[str], ttl_seconds: float) -> None:
    """Insert ``key`` (most recent) after sweeping expired entries; LRU-bounded."""

    now = time.monotonic()
    for stale in [k for k, (at, _) in _CACHE.items() if now - at >= ttl_seconds and k != key]:
        _drop(stale)
    _CACHE[key] = (now, tools)
    _CACHE.move_to_end(key)
    _TAGS[key] = tags
    while len(_CACHE) > MAX_ENTRIES:
        _drop(next(iter(_CACHE)))


def _clone_tool(tool: BaseTool) -> BaseTool:
    try:
        return tool.model_copy(deep=True)
    except Exception:  # noqa: BLE001
        try:
            return tool.model_copy()
        except Exception:  # noqa: BLE001
            return tool


def _clone_tools(tools: list[BaseTool]) -> list[BaseTool]:
    return [_clone_tool(tool) for tool in tools]


def identity_cache_key(
    server_key: str, configs: Iterable[dict[str, Any]], injected_auth: dict[str, Any]
) -> tuple[str, frozenset[str]]:
    """Cache key and invalidation tags for one server's tools under one identity."""

    identities = sorted(
        {tuple(str(config.get(name) or "") for name in _IDENTITY_FIELDS) for config in configs}
    )
    material = json.dumps(
        {"identities": identities, "auth": injected_auth}, sort_keys=True, default=str
    )
    digest = hashlib.sha256(material.encode()).hexdigest()[:16]
    tags: set[str] = set()
    for identity in identities:
        values = dict(zip(_IDENTITY_FIELDS, identity, strict=True))
        if values["mcp_server_id"]:
            tags.add(f"server:{values['mcp_server_id']}")
        if values["credential_id"]:
            tags.add(f"credential:{values['credential_id']}")
        if values["credential_subject_user_id"]:
            tags.add(f"account:{values['credential_subject_user_id']}")
    return f"{server_key}|{digest}", frozenset(tags)


async def _lock_for_key(key: str) -> asyncio.Lock:
    async with _CACHE_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _LOCKS[key] = lock
        return lock


async def get_cached_mcp_tools(
    key: str,
    factory: Callable[[], Awaitable[list[BaseTool]]],
    *,
    ttl_seconds: float,
    tags: frozenset[str] = frozenset(),
) -> list[BaseTool]:
    """Return cached MCP tools for ``key``, refreshing with ``factory`` on miss."""

    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached is not None and now - cached[0] < ttl_seconds:
        _CACHE.move_to_end(key)
        return _clone_tools(cached[1])

    lock = await _lock_for_key(key)
    async with lock:
        now = time.monotonic()
        cached = _CACHE.get(key)
        if cached is not None and now - cached[0] < ttl_seconds:
            return _clone_tools(cached[1])
        tools = await factory()
        _store(key, _clone_tools(tools), tags, ttl_seconds)
        return _clone_tools(tools)


def invalidate_mcp_tool_cache(
    *,
    credential_id: str | None = None,
    server_id: str | None = None,
    account_id: str | None = None,
) -> int:
    """Drop entries tagged with every given id; returns how many were dropped."""

    wanted = {
        f"{kind}:{value}"
        for kind, value in (
            ("credential", credential_id),
            ("server", server_id),
            ("account", account_id),
        )
        if value
    }
    if not wanted:
        return 0
    stale = [key for key, tags in _TAGS.items() if wanted <= tags]
    for key in stale:
        _drop(key)
    return len(stale)


async def clear_mcp_tool_cache() -> None:
    """Clear all cached MCP tool discovery results."""

    async with _CACHE_GUARD:
        _CACHE.clear()
        _TAGS.clear()
        _LOCKS.clear()


__all__ = [
    "clear_mcp_tool_cache",
    "get_cached_mcp_tools",
    "identity_cache_key",
    "invalidate_mcp_tool_cache",
]
