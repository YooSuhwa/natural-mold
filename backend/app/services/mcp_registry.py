"""Curated catalog of well-known MCP servers.

The registry is a static JSON file (``app/data/mcp_server_registry.json``)
loaded once at first call. Each entry pre-fills the transport, URL or stdio
launch command, env_var template, and the matching credential definition key
so a user can pick "GitHub" / "Linear" / etc. and get a working
:class:`~app.models.mcp_server.McpServer` row without re-typing the wire
config.

The MCP server catalog pattern is borrowed from prior art — a curated
"connector library" that maps human-readable names to ready-to-run wire
configs (see ``NOTICES.md`` for attribution). The actual entry shape and
contents are Moldy-native.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_REGISTRY_PATH = (
    Path(__file__).parent.parent / "data" / "mcp_server_registry.json"
)
_cache: dict[str, dict[str, Any]] | None = None


def _load_registry() -> dict[str, dict[str, Any]]:
    """Return the JSON registry, parsing it lazily on first access."""

    global _cache
    if _cache is None:
        with _REGISTRY_PATH.open() as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(
                f"mcp_server_registry.json must be a JSON object, got {type(data)}"
            )
        _cache = data
    return _cache


def list_registry() -> list[dict[str, Any]]:
    """Return all registry entries in stable display order (alphabetical)."""

    items = list(_load_registry().values())
    items.sort(key=lambda e: (e.get("display_name") or e.get("key") or "").lower())
    return items


def get_registry_entry(key: str) -> dict[str, Any] | None:
    """Look up a single entry by its canonical ``key``."""

    return _load_registry().get(key)


def reset_cache() -> None:
    """Drop the in-memory cache. Used by tests that swap fixture files."""

    global _cache
    _cache = None


__all__ = [
    "get_registry_entry",
    "list_registry",
    "reset_cache",
]
