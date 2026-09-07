"""Bounded MCP Apps metadata and payload projections.

MCP metadata is server-controlled input.  This module keeps only the fields
the host needs to render an App and applies conservative size/key limits to
the result metadata forwarded to the sandbox.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any, Final
from urllib.parse import urlparse

MAX_RESOURCE_URI_LENGTH: Final = 1_000
MAX_METADATA_BYTES: Final = 128 * 1024
MAX_METADATA_DEPTH: Final = 6
MAX_COLLECTION_ITEMS: Final = 128
MAX_STRING_LENGTH: Final = 32 * 1024

_DENIED_KEY_PARTS: Final = (
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
    "api_key",
    "api-key",
    "apikey",
)
_CSP_KEYS: Final = (
    "connectDomains",
    "resourceDomains",
    "frameDomains",
    "baseUriDomains",
)
_PERMISSION_KEYS: Final = ("camera", "microphone", "geolocation", "clipboardWrite")


def normalize_tool_ui_meta(value: Any) -> dict[str, Any] | None:
    """Return the standard, bounded MCP Apps tool metadata subset."""

    if not isinstance(value, Mapping):
        return None
    ui = value.get("ui")
    nested = ui if isinstance(ui, Mapping) else {}
    resource_uri = nested.get("resourceUri") or value.get("ui/resourceUri")
    visibility = nested.get("visibility")
    normalized_visibility: list[str] | None = None
    if visibility is not None:
        if not isinstance(visibility, Sequence) or isinstance(visibility, str | bytes):
            return None
        if any(item not in {"model", "app"} for item in visibility):
            return None
        normalized_visibility = list(dict.fromkeys(visibility))
    if not is_ui_resource_uri(resource_uri) and normalized_visibility is None:
        return None

    normalized_ui: dict[str, Any] = {}
    if is_ui_resource_uri(resource_uri):
        normalized_ui["resourceUri"] = resource_uri
    if normalized_visibility is not None:
        normalized_ui["visibility"] = normalized_visibility
    return {"ui": normalized_ui}


def normalize_resource_ui_meta(value: Any) -> dict[str, Any] | None:
    """Return the standard MCP Apps resource security/rendering fields only."""

    if not isinstance(value, Mapping):
        return None
    ui = value.get("ui")
    if not isinstance(ui, Mapping):
        return None

    normalized: dict[str, Any] = {}
    csp = ui.get("csp")
    if isinstance(csp, Mapping):
        normalized_csp: dict[str, list[str]] = {}
        for key in _CSP_KEYS:
            domains = _valid_origins(csp.get(key))
            if domains:
                normalized_csp[key] = domains
        if normalized_csp:
            normalized["csp"] = normalized_csp

    permissions = ui.get("permissions")
    if isinstance(permissions, Mapping):
        allowed_permissions = {
            key: {} for key in _PERMISSION_KEYS if isinstance(permissions.get(key), Mapping)
        }
        if allowed_permissions:
            normalized["permissions"] = allowed_permissions

    domain = ui.get("domain")
    if isinstance(domain, str) and _is_origin(domain):
        normalized["domain"] = domain
    if isinstance(ui.get("prefersBorder"), bool):
        normalized["prefersBorder"] = ui["prefersBorder"]
    return {"ui": normalized} if normalized else None


def sanitize_result_meta(value: Any) -> dict[str, Any] | None:
    """Bound opaque result metadata while removing credential-shaped keys."""

    sanitized = _sanitize_json(value, depth=0)
    if not isinstance(sanitized, dict):
        return None
    encoded = json.dumps(sanitized, ensure_ascii=False, separators=(",", ":")).encode()
    return sanitized if len(encoded) <= MAX_METADATA_BYTES else None


def sanitize_result_payload(value: Any) -> Any:
    """Return a bounded JSON value, or ``None`` when its encoding is too large."""

    try:
        raw_size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
    except (TypeError, ValueError):
        return None
    if raw_size > MAX_METADATA_BYTES:
        return None
    sanitized = _sanitize_json(value, depth=0)
    encoded = json.dumps(sanitized, ensure_ascii=False, separators=(",", ":")).encode()
    return sanitized if len(encoded) <= MAX_METADATA_BYTES else None


def is_ui_resource_uri(value: Any) -> bool:
    if not isinstance(value, str) or not value or len(value) > MAX_RESOURCE_URI_LENGTH:
        return False
    parsed = urlparse(value)
    return parsed.scheme == "ui" and bool(parsed.netloc or parsed.path)


def tool_allows_visibility(meta: Mapping[str, Any] | None, visibility: str) -> bool:
    """Apply MCP Apps' default visibility of both model and app."""

    if meta is None:
        return False
    ui = meta.get("ui")
    if not isinstance(ui, Mapping):
        return False
    configured = ui.get("visibility")
    if configured is None:
        return visibility in {"model", "app"}
    return isinstance(configured, list) and visibility in configured


def _valid_origins(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return [item for item in value[:32] if isinstance(item, str) and _is_origin(item)]


def _is_origin(value: str) -> bool:
    if len(value) > 2_048:
        return False
    parsed = urlparse(value)
    return (
        parsed.scheme in {"http", "https", "ws", "wss"}
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def _sanitize_json(value: Any, *, depth: int) -> Any:
    if depth > MAX_METADATA_DEPTH:
        return None
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:MAX_STRING_LENGTH]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:MAX_COLLECTION_ITEMS]:
            key = str(raw_key)[:256]
            lowered = key.lower()
            if any(part in lowered for part in _DENIED_KEY_PARTS):
                continue
            result[key] = _sanitize_json(item, depth=depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [_sanitize_json(item, depth=depth + 1) for item in value[:MAX_COLLECTION_ITEMS]]
    return None


__all__ = [
    "MAX_METADATA_BYTES",
    "is_ui_resource_uri",
    "normalize_resource_ui_meta",
    "normalize_tool_ui_meta",
    "sanitize_result_meta",
    "sanitize_result_payload",
    "tool_allows_visibility",
]
