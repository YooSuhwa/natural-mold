"""Frozen dependency decisions; ORM objects are constructed only at apply."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import assert_never
from uuid import UUID

from pydantic import JsonValue, TypeAdapter

from app.authz.org_context import OrgContext
from app.error_codes import marketplace_invalid_package
from app.marketplace.schemas import CreateAgentFromBlueprintIn

type JsonObject = dict[str, JsonValue]


def dependency_mapping(value: JsonValue, kind: str) -> JsonObject:
    match value:
        case dict():
            return value
        case str() | int() | float() | bool() | list() | None:
            raise marketplace_invalid_package(f"{kind} dependency is malformed")
        case unreachable:
            assert_never(unreachable)


def position(value: JsonValue) -> int:
    match value:
        case str() | int() | float() | bool():
            return int(value or 0)
        case dict() | list():
            return int(str(value))
        case None:
            return 0
        case unreachable:
            assert_never(unreachable)


def mapping(value: JsonValue) -> JsonObject:
    match value:
        case dict():
            return value
        case str() | int() | float() | bool() | list() | None:
            return {}
        case unreachable:
            assert_never(unreachable)


def sequence(value: JsonValue) -> list[JsonValue]:
    match value:
        case list():
            return value
        case str() | int() | float() | bool() | dict() | None:
            return []
        case unreachable:
            assert_never(unreachable)


def creation_args(value: JsonValue) -> tuple[JsonValue, ...]:
    """Keep native list(value or []) construction distinct from match normalization."""
    if not value:
        return ()
    match value:
        case list() | str() | dict():
            return tuple(value)
        case int() | float() | bool() | None:
            raise TypeError(value)
        case unreachable:
            assert_never(unreachable)


def creation_mapping(value: JsonValue) -> JsonObject:
    """Preserve dict(value or {}) including JSON-encoded pair sequences."""
    if not value:
        return {}
    match value:
        case dict():
            return dict(value)
        case list():
            pairs: list[tuple[str | int | float | bool | None, JsonValue]] = []
            for pair in value:
                match pair:
                    case [(str() | int() | float() | bool() | None) as key, item]:
                        pairs.append((key, item))
                    case str() as text if len(text) == 2:
                        pairs.append((text[0], text[1]))
                    case list() | dict() | str() | int() | float() | bool() | None:
                        raise ValueError(pair)
                    case unreachable:
                        assert_never(unreachable)
            return TypeAdapter(JsonObject).validate_json(json.dumps(dict(pairs)))
        case str() | int() | float() | bool() | None:
            raise TypeError(value)
        case unreachable:
            assert_never(unreachable)


def optional_text(value: JsonValue) -> str | None:
    return str(value) if value is not None else None


def credential_key(prefix: str, value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_]+", "_", value.strip().lower()).strip("_")
    return f"{prefix}_{cleaned or 'credential'}"


@dataclass(frozen=True, slots=True)
class GraphRequest:
    capabilities: JsonObject
    context: OrgContext
    body: CreateAgentFromBlueprintIn
    bindings: dict[str, UUID]


@dataclass(frozen=True, slots=True)
class ToolDraft:
    id: UUID
    user_id: UUID | None
    definition_key: str
    name: str
    description: str | None
    parameters: JsonObject
    credential_id: UUID | None
    enabled: bool


@dataclass(frozen=True, slots=True)
class ServerDraft:
    id: UUID
    name: str
    description: str | None
    transport: str
    url: str | None
    command: str | None
    args: tuple[JsonValue, ...]
    env_vars: JsonObject
    headers: JsonObject
    credential_id: UUID | None


@dataclass(frozen=True, slots=True)
class McpToolDraft:
    id: UUID
    server_id: UUID
    name: str
    description: str | None
    input_schema: JsonValue


@dataclass(frozen=True, slots=True)
class CredentialAssignment:
    server_id: UUID
    credential_id: UUID


@dataclass(frozen=True, slots=True)
class ToolPlan:
    ids: tuple[UUID, ...]
    creations: tuple[ToolDraft, ...]


@dataclass(frozen=True, slots=True)
class McpPlan:
    ids: tuple[UUID, ...]
    servers: tuple[ServerDraft, ...]
    tools: tuple[McpToolDraft, ...]
    assignments: tuple[CredentialAssignment, ...]


@dataclass(frozen=True, slots=True)
class GraphPlan:
    context: OrgContext
    tools: ToolPlan
    skill_ids: tuple[UUID, ...]
    mcp: McpPlan
    sub_agent_ids: tuple[UUID, ...]
