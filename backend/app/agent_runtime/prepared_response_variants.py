"""Canonical response-format variant IDs for prepared children (internal I12).

A variant ID covers the schema and every strategy field that changes the
compiled child (``tool_message_content``, ``handle_errors``, ``strict``).
Callable, async or unknown forms are rejected before any graph compilation.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Any, is_typeddict

from langchain.agents.structured_output import AutoStrategy, ProviderStrategy, ToolStrategy
from pydantic import BaseModel, TypeAdapter

DEFAULT_VARIANT = "default"


class PreparedChildCatalogError(ValueError):
    """The child closure cannot be prepared; raised before the marker."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def canonical_sha(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _unsupported() -> PreparedChildCatalogError:
    return PreparedChildCatalogError("response_variant_unsupported")


def _type_variant(value: type) -> str:
    if issubclass(value, BaseModel):
        schema = value.model_json_schema()
    elif dataclasses.is_dataclass(value) or is_typeddict(value):
        schema = TypeAdapter(value).json_schema()
    else:
        raise _unsupported()
    return f"type:{value.__module__}.{value.__qualname__}:{canonical_sha(schema)[:16]}"


def _exception_name(value: Any) -> str:
    if not isinstance(value, type) or not issubclass(value, Exception):
        raise _unsupported()
    return f"{value.__module__}.{value.__qualname__}"


def _handle_errors_identity(value: Any) -> Any:
    if isinstance(value, (bool, str)):
        return value
    if isinstance(value, tuple):
        return [_exception_name(item) for item in value]
    return _exception_name(value)


def _strategy_fields(value: ToolStrategy | ProviderStrategy | AutoStrategy) -> dict[str, Any]:
    if isinstance(value, ToolStrategy):
        return {
            "tool_message_content": value.tool_message_content,
            "handle_errors": _handle_errors_identity(value.handle_errors),
        }
    if isinstance(value, ProviderStrategy):
        return {"strict": value.schema_spec.strict}
    return {}


def response_variant_id(value: Any) -> str:
    """Return the stable variant ID of one response format (pure, no compile)."""

    if value is None:
        return DEFAULT_VARIANT
    if isinstance(value, dict):
        try:
            return f"dict:{canonical_sha(value)[:16]}"
        except (TypeError, ValueError):
            raise _unsupported() from None
    if isinstance(value, (ToolStrategy, ProviderStrategy, AutoStrategy)):
        fields = canonical_sha(_strategy_fields(value))[:16]
        inner = response_variant_id(value.schema)
        return f"schema:{type(value).__name__}:{inner}:{fields}"
    if isinstance(value, type):
        return _type_variant(value)
    raise _unsupported()
