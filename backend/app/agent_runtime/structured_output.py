"""Structured output for one agent run (workflow plan W0, 3.2; plan v2 22.3).

A workflow node declares the JSON its agent must return. Only a small JSON Schema
subset is accepted (``check_output_schema``): it is what the table editor can build and
it keeps validation cost bounded. The schema becomes a Pydantic model so LangChain's
``ToolStrategy`` validates the model's answer and asks once more on a mismatch (raw
dict schemas are passed through unvalidated). The run's result is then read back into
one of four outcomes; a node never sees an answer the server did not validate.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from langchain.agents.structured_output import ToolStrategy
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from app.exceptions import ValidationError as AppValidationError

OutputStatus = Literal["ok", "schema_invalid", "refused", "truncated"]

MAX_DEPTH: Final = 3
MAX_PROPERTIES: Final = 50
MAX_ENUM: Final = 50
MAX_SCHEMA_BYTES: Final = 16_384
MAX_DESCRIPTION: Final = 500
# One extra model turn after a mismatch; the second mismatch fails the step.
RETRIES: Final = 1

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_SCALARS: Final = {"string": str, "number": float, "integer": int, "boolean": bool}
_KEYS: Final = {
    "object": {"type", "properties", "required", "description"},
    "array": {"type", "items", "description"},
    "scalar": {"type", "description", "enum"},
}
_TRUNCATED: Final = {"length", "max_tokens", "MAX_TOKENS"}


class OutputSchemaError(AppValidationError):
    def __init__(self, message: str) -> None:
        super().__init__("OUTPUT_SCHEMA_INVALID", message)


class StructuredOutputExhausted(RuntimeError):
    """The model missed the schema again after its one retry."""


@dataclass(frozen=True, slots=True)
class StructuredOutcome:
    status: OutputStatus
    value: dict[str, Any] | None = None


def check_output_schema(schema: Any) -> dict[str, Any]:
    """The schema if it is inside the accepted subset, else ``OutputSchemaError``."""
    if not isinstance(schema, Mapping):
        raise OutputSchemaError("output schema must be an object")
    try:
        size = len(json.dumps(schema, ensure_ascii=False).encode())
    except (TypeError, ValueError) as exc:
        raise OutputSchemaError("output schema must be JSON") from exc
    if size > MAX_SCHEMA_BYTES:
        raise OutputSchemaError(f"output schema is larger than {MAX_SCHEMA_BYTES} bytes")
    if schema.get("type") != "object":
        raise OutputSchemaError("output schema root must be type object")
    _check(schema, path="$", depth=1)
    return dict(schema)


def _check(node: Any, *, path: str, depth: int) -> None:
    if not isinstance(node, Mapping):
        raise OutputSchemaError(f"{path}: must be an object")
    kind = node.get("type")
    group = kind if kind in ("object", "array") else "scalar"
    if group == "scalar" and kind not in _SCALARS:
        raise OutputSchemaError(f"{path}: unsupported type {kind!r}")
    extra = set(node) - _KEYS[group]
    if extra:
        raise OutputSchemaError(f"{path}: unsupported keys {sorted(extra)}")
    description = node.get("description")
    if description is not None and (
        not isinstance(description, str) or len(description) > MAX_DESCRIPTION
    ):
        raise OutputSchemaError(f"{path}: description must be text up to {MAX_DESCRIPTION}")
    if kind == "object":
        if depth > MAX_DEPTH:
            raise OutputSchemaError(f"{path}: nested deeper than {MAX_DEPTH} objects")
        _check_object(node, path=path, depth=depth)
    elif kind == "array":
        if "items" not in node:
            raise OutputSchemaError(f"{path}: array needs items")
        _check(node["items"], path=f"{path}[]", depth=depth + 1)
    elif "enum" in node:
        _check_enum(node["enum"], kind=str(kind), path=path)


def _check_object(node: Mapping[str, Any], *, path: str, depth: int) -> None:
    properties = node.get("properties")
    if not isinstance(properties, Mapping) or not properties:
        raise OutputSchemaError(f"{path}: object needs properties")
    if len(properties) > MAX_PROPERTIES:
        raise OutputSchemaError(f"{path}: more than {MAX_PROPERTIES} properties")
    for name, child in properties.items():
        if not isinstance(name, str) or not _NAME.match(name):
            raise OutputSchemaError(f"{path}: property name {name!r} must be an identifier")
        _check(child, path=f"{path}.{name}", depth=depth + 1)
    required = node.get("required", [])
    if not isinstance(required, list) or not all(isinstance(r, str) for r in required):
        raise OutputSchemaError(f"{path}: required must be a list of names")
    unknown = set(required) - set(properties)
    if unknown or len(set(required)) != len(required):
        raise OutputSchemaError(f"{path}: required names must be unique properties")


def _check_enum(values: Any, *, kind: str, path: str) -> None:
    if kind != "string":
        raise OutputSchemaError(f"{path}: enum is only for strings")
    if (
        not isinstance(values, list)
        or not values
        or len(values) > MAX_ENUM
        or not all(isinstance(v, str) and v for v in values)
        or len(set(values)) != len(values)
    ):
        raise OutputSchemaError(f"{path}: enum must be 1-{MAX_ENUM} distinct strings")


def output_model(schema: Mapping[str, Any], name: str = "WorkflowOutput") -> type[BaseModel]:
    """A Pydantic model that accepts exactly what ``schema`` describes (no extra keys)."""
    checked = check_output_schema(schema)
    return _model(checked, name)


def _model(node: Mapping[str, Any], name: str) -> type[BaseModel]:
    required = set(node.get("required", []))
    fields: dict[str, Any] = {}
    for prop, child in node["properties"].items():
        annotation = _annotation(child, f"{name}_{prop}")
        info = Field(description=child.get("description"))
        if prop in required:
            fields[prop] = (annotation, info)
        else:
            fields[prop] = (annotation | None, Field(None, description=child.get("description")))
    return create_model(
        name,
        __config__=ConfigDict(extra="forbid", strict=False),
        __doc__=node.get("description") or "The answer in the required JSON shape.",
        **fields,
    )


def _annotation(node: Mapping[str, Any], name: str) -> Any:
    kind = node["type"]
    if kind == "object":
        return _model(node, name)
    if kind == "array":
        return list[_annotation(node["items"], f"{name}_item")]  # type: ignore[misc]
    if "enum" in node:
        return Literal[tuple(node["enum"])]
    return _SCALARS[kind]


def response_format_for(schema: Mapping[str, Any]) -> ToolStrategy[Any]:
    """A ``ToolStrategy`` for one run: validated, one retry, then a hard failure.

    Built per run, so the retry counter never leaks between runs.
    """
    model = output_model(schema)
    misses = 0

    def handle(exc: Exception) -> str:
        nonlocal misses
        misses += 1
        if misses > RETRIES:
            raise StructuredOutputExhausted(str(exc)) from exc
        return (
            "The answer did not match the required JSON schema. Call the response tool "
            f"again with arguments that match it exactly. Error: {exc}"
        )

    return ToolStrategy(model, handle_errors=handle)


def structured_outcome(result: Mapping[str, Any], schema: Mapping[str, Any]) -> StructuredOutcome:
    """Read a finished run's state into an outcome; the value is re-validated here."""
    raw = result.get("structured_response")
    if raw is not None:
        data = raw.model_dump(mode="json") if isinstance(raw, BaseModel) else raw
        try:
            value = output_model(schema).model_validate(data).model_dump(mode="json")
        except ValidationError:
            return StructuredOutcome("schema_invalid")
        return StructuredOutcome("ok", value)
    last = _last_ai_message(result.get("messages") or [])
    if last is not None:
        metadata = getattr(last, "response_metadata", None) or {}
        reason = metadata.get("finish_reason") or metadata.get("stop_reason")
        if reason in _TRUNCATED:
            return StructuredOutcome("truncated")
        if reason == "refusal" or (getattr(last, "additional_kwargs", None) or {}).get("refusal"):
            return StructuredOutcome("refused")
    return StructuredOutcome("schema_invalid")


def _last_ai_message(messages: list[Any]) -> Any | None:
    for message in reversed(messages):
        if getattr(message, "type", None) == "ai":
            return message
    return None


__all__ = [
    "MAX_DEPTH",
    "OutputSchemaError",
    "OutputStatus",
    "StructuredOutcome",
    "StructuredOutputExhausted",
    "check_output_schema",
    "output_model",
    "response_format_for",
    "structured_outcome",
]
